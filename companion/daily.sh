#!/usr/bin/env bash
# daily.sh —— 每日伴读一次完整跑批。给 cron / 定时器调用。
#
# 它把 README 里那条链按钉死的时序跑一遍：
#   collect（采）→ select（选）→ enqueue（够格的入池）→ expire（清过期）→ pick（取今日一篇）→ 送达
#
# 设计取舍：
# - **采不到 / 今天没有够格的文章，不算失败**。池子空就是空——「丰年存粮、荒年开仓」的前提
#   就是允许有荒年。这种情况下脚本以 0 退出，只在日志里说明，不用报警吵人。
# - **真失败才非 0 退**：venv 缺失、脚本崩溃这类装载/代码问题。
# - 全程日志追加到 user/daily.log，cron 环境下出了事能回溯。

set -uo pipefail

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python3"
LOG="$ROOT/user/daily.log"
SELECTED="$ROOT/user/selected.json"

mkdir -p "$ROOT/user"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

if [ ! -x "$PY" ]; then
  log "FATAL: 找不到 $PY —— 先跑 ./init.sh 建 .venv"
  exit 1
fi

log "=== 每日跑批开始 ==="

# 1) 采集（联网）。采不到不阻断——可能今天源就没更新。
log "[1/5] 采集"
"$PY" scripts/article_collector.py collect --days 3 >>"$LOG" 2>&1 \
  || log "  采集非零退出（网络/源问题），继续用已有存货"

# 2) 选文：把够格的最高分一篇导成 selected.json。
#    没有够格的（全都 <8.0）→ select 返回非 0，这是正常的"荒年"，不是故障。
log "[2/5] 选文"
if "$PY" scripts/select.py --min 8.0 --out "$SELECTED" >>"$LOG" 2>&1; then
  # 3) 入池
  log "[3/5] 入池"
  "$PY" scripts/pool.py enqueue --file "$SELECTED" >>"$LOG" 2>&1 \
    || log "  入池未成（多为该文已在池中或未过 8.0 门），继续"
else
  log "  今日无 ≥8.0 新文可入池（正常，池中存货仍可放粮）"
fi

# 4) 清过期：时效文过了保鲜期就不该再推。放粮前必跑。
log "[4/5] 清过期"
"$PY" scripts/pool.py expire >>"$LOG" 2>&1 || true

# 5) 放粮：取池中最高分未过期一篇。
log "[5/5] 取今日一篇"
PICKED="$("$PY" scripts/pool.py pick 2>>"$LOG")"

if [ -z "$PICKED" ]; then
  log "  池空——今天不推。这不是故障：没有够格的文章就不推，比硬凑一篇强。"
  log "=== 完成（今日无推送）==="
  exit 0
fi

log "  取到：$(echo "$PICKED" | head -c 200)"

# 送达 = deliver.py 的两层（落盘必成 + 投递可选）。
# 这里把 pick 出来的条目交给 deliver：落盘到 user/pushed/，配了 delivery 就再投递一次。
"$PY" - "$PICKED" >>"$LOG" 2>&1 <<'PYEOF'
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "scripts"))
sys.path.insert(0, "scripts")
import deliver

item = json.loads(sys.argv[1])
title = item.get("title", "今日一篇")
# 正文取原文件（select 记了 article_file）；取不到就退回一条带链接的短讯。
body = ""
af = item.get("article_file")
if af:
    p = pathlib.Path(af)
    if p.exists():
        body = p.read_text(encoding="utf-8")
if not body:
    body = f"{title}\n\n{item.get('url','')}\n\n（正文文件未找到，仅推链接）"

res = deliver.send(
    title, body,
    slug=(item.get("pool_id") or "daily"),
    meta={"score": item.get("score"), "url": item.get("url"), "source": item.get("source")},
    extra={"score": item.get("score"), "url": item.get("url")},
)
print(f"已送达：{res['file']}")
PYEOF

RC=$?
if [ $RC -ne 0 ]; then
  log "  送达环节非零退出（rc=$RC）——落盘失败才是真失败，去 $LOG 看栈"
  exit $RC
fi

# 标记已推，避免明天重复推同一篇。
POOL_ID="$(echo "$PICKED" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("pool_id",""))' 2>/dev/null)"
if [ -n "$POOL_ID" ]; then
  "$PY" scripts/pool.py mark-pushed --pool-id "$POOL_ID" >>"$LOG" 2>&1 \
    && log "  已标记 $POOL_ID 为已推"
fi

log "=== 完成（今日已推送）==="
exit 0
