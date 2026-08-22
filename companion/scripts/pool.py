#!/usr/bin/env python3
# 日推池（常平仓）—— 生产（打分入库）与消费（放粮推送）解耦的库存池。
#
#
# 核心：把"打分"和"推送"解耦成两个独立日动作——每天①尽力往池蓄水（≥8.0 入库）
# ②从池取最高分放粮。丰年存粮、荒年开仓，填平"好文非每天均匀到来"的供给波动。
#
# 池文件：user/daily-push-pool.jsonl，每行一条池条（schema 见下方 cmd_enqueue 的 entry）。
#
# 子命令：
#   enqueue --file <selected.json>   一篇 ≥8.0 文入池
#   pick                             取池中当前最高分且未过期一篇 → stdout（只读不改状态）
#   mark-pushed --pool-id <id>       该条 status=pushed + pushed_date
#   expire                           扫全池，时效文过期未推 → status=expired
#   intake-status                    输出池健康统计（含自算 consecutive_zero_intake_days）
#
# 每日调用时序钉死：expire → pick →（推成功）→ mark-pushed。expire 必在 pick 前。
#
# 并发写：假设单调度串行执行，暂不加锁（已知限制，低危；并行调用需自行加锁）。

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "user" / "daily-push-pool.jsonl"
READING_LOG = ROOT / "user" / "reading-log.jsonl"

TIME_SENSITIVE_TTL_DAYS = 7  # 时效文保鲜 7 天（起步默认值，可按自己的节奏调）

# 时效文判据（time_sensitive）：时事/政策/市场 = true；认知/框架/健康原理 = false。
# 用 selected.json 的 domain/category/type/scoring_path 等字段推断（明写 time_sensitive 优先）。
TIME_SENSITIVE_DOMAINS = {"law", "legal", "legal_safety", "politics", "policy",
                          "market", "economy", "economics", "geopolitics", "business"}
TIMELESS_TYPE_HINTS = ("insight", "framework", "cognition", "principle", "health_principle")


def today_str() -> str:
    """系统当天（date），不硬编日期——用运行机器的本地时区。"""
    return date.today().isoformat()


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def read_pool() -> list[dict]:
    """读全池；池文件不存在则空。跳过空行/坏行（不吞噬——坏行吐到 stderr）。"""
    if not POOL.exists():
        return []
    items: list[dict] = []
    for i, line in enumerate(POOL.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"[pool.py] WARN: skipping malformed line {i} in {POOL}: {e}",
                  file=sys.stderr)
    return items


def write_pool(items: list[dict]) -> None:
    """原子写回全池（先写 tmp 再 rename，避免半写）。"""
    POOL.parent.mkdir(parents=True, exist_ok=True)
    tmp = POOL.with_suffix(POOL.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    tmp.replace(POOL)


def infer_time_sensitive(sel: dict) -> bool:
    """判 time_sensitive：显式字段优先，否则据 domain/type 推断。"""
    if isinstance(sel.get("time_sensitive"), bool):
        return sel["time_sensitive"]
    domain = (sel.get("domain") or sel.get("category") or "").lower()
    if domain in TIME_SENSITIVE_DOMAINS:
        return True
    art_type = (sel.get("type") or sel.get("scoring_path") or "").lower()
    if any(h in art_type for h in TIMELESS_TYPE_HINTS):
        return False
    # 缺省保守判 true=时效（独立复核抓出的漏判修法）：走到这里的是"域不在时效白名单、
    # 类型也不像 timeless"的模糊文——认知/框架/健康原理类已在上面 return False 拦截。
    # 判 true 最坏=长存文 7 天没被 pick 就丢（少存一篇）；判 false 最坏=时效新闻永不过期、
    # 几周后被当新闻推=负价值事故。取代价小的一侧=保守过期。根治靠 selected 显式带
    # time_sensitive（见函数首行优先分支 + SKILL 入池步骤要求）。
    return True


def next_pool_id(items: list[dict], enq_date: str) -> str:
    """池内自增 id：pool-YYYYMMDD-NN（当日序号，跨全池计数避免撞车）。"""
    prefix = f"pool-{enq_date.replace('-', '')}"
    n = sum(1 for it in items if str(it.get("pool_id", "")).startswith(prefix)) + 1
    return f"{prefix}-{n:02d}"


# ---------------------------------------------------------------- enqueue

def cmd_enqueue(args) -> int:
    sel_path = Path(args.file)
    if not sel_path.is_absolute():
        sel_path = (Path.cwd() / sel_path).resolve()
    if not sel_path.exists():
        print(f"[pool.py] ERROR: --file not found: {sel_path}", file=sys.stderr)
        return 2
    sel = json.loads(sel_path.read_text(encoding="utf-8"))

    score = sel.get("score", sel.get("final_score"))
    if score is None:
        print("[pool.py] ERROR: selected.json missing score/final_score", file=sys.stderr)
        return 2
    if float(score) < 8.0:
        # 8.0 硬门不动——入池仍须真 ≥8.0，池不是存 7.5 的借口。
        print(f"[pool.py] REJECT: score {score} < 8.0, not enqueued (8.0 hard gate)",
              file=sys.stderr)
        return 3

    enq = args.date or today_str()
    items = read_pool()
    time_sensitive = infer_time_sensitive(sel)
    expiry = None
    if time_sensitive:
        d = parse_date(enq)
        expiry = (d + timedelta(days=TIME_SENSITIVE_TTL_DAYS)).isoformat() if d else None

    entry = {
        "pool_id": next_pool_id(items, enq),
        "title": sel.get("title", ""),
        "url": sel.get("url", sel.get("article_url", "")),
        "source": sel.get("source", ""),
        "domain": sel.get("domain", sel.get("category", "")),
        "score": float(score),
        "scoring_path": sel.get("scoring_path", sel.get("expansion_path", "")),
        # 原文落盘路径——放粮时要靠它取正文，没有它就只能推一条光秃秃的链接。
        "article_file": sel.get("article_file", ""),
        "reach_gap_exemption": sel.get("reach_gap_exemption"),
        "enqueued_date": enq,
        "expiry_date": expiry,
        "time_sensitive": time_sensitive,
        "status": "in_pool",
        "pushed_date": None,
    }
    items.append(entry)
    write_pool(items)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- pick

def cmd_pick(args) -> int:
    """取池中当前最高分且未过期一篇 → stdout。只从 status=in_pool 取，只读不改状态。

    放粮优先级：先 time_sensitive:true 且快到期，再分值，同分先快到期。
    """
    ref = args.date or today_str()
    ref_d = parse_date(ref)
    items = read_pool()

    def not_expired(it: dict) -> bool:
        # time_sensitive 且已过 expiry_date 的不取（即便 status 还没被 expire 标记，
        # 也不 pick——防 expire 未跑时误放过期时事文；但正常时序 expire 在 pick 前）。
        if not it.get("time_sensitive"):
            return True
        exp = parse_date(it.get("expiry_date"))
        if exp is None or ref_d is None:
            return True
        return ref_d <= exp

    candidates = [it for it in items
                  if it.get("status") == "in_pool" and not_expired(it)]
    if not candidates:
        # 池空（或全过期/已推）——不输出，退出码 1 供调用方判 both-fail。
        return 1

    def days_to_expiry(it: dict) -> int:
        exp = parse_date(it.get("expiry_date"))
        if exp is None or ref_d is None:
            return 10**6  # 长存文视作无限远，排到时效文之后
        return (exp - ref_d).days

    # 排序键（升序取第一个）：
    #   1. 分值降序（先取最高分）—— 主键
    #   2. 快到期优先：time_sensitive 且快到期的在同分内排前（防浪费）
    #   3. 同分同"是否时效"下，剩余天数少的先（先快到期）
    def sort_key(it: dict):
        score = float(it.get("score", 0))
        ts = 1 if it.get("time_sensitive") else 0
        dte = days_to_expiry(it)
        # score 降序 → -score；时效文优先 → -ts（1 排前）；快到期优先 → dte 升序
        return (-score, -ts, dte)

    best = sorted(candidates, key=sort_key)[0]
    print(json.dumps(best, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- mark-pushed

def cmd_mark_pushed(args) -> int:
    pool_id = args.pool_id
    ref = args.date or today_str()
    items = read_pool()
    found = False
    for it in items:
        if it.get("pool_id") == pool_id:
            if it.get("status") == "pushed":
                # 崩溃恢复：同 pool_id 已 pushed，不二次标记（防池内重推）。
                print(f"[pool.py] NOTE: {pool_id} already pushed on "
                      f"{it.get('pushed_date')}, no-op", file=sys.stderr)
                print(json.dumps(it, ensure_ascii=False))
                return 0
            it["status"] = "pushed"
            it["pushed_date"] = ref
            found = True
            write_pool(items)
            print(json.dumps(it, ensure_ascii=False))
            break
    if not found:
        print(f"[pool.py] ERROR: pool_id not found: {pool_id}", file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------- expire

def cmd_expire(args) -> int:
    """扫全池，time_sensitive:true 且过 expiry_date 未推 → status=expired。每日跑一次。

    必在 pick 之前跑：否则过期时事文可能被 pick 取走推出去。
    """
    ref = args.date or today_str()
    ref_d = parse_date(ref)
    items = read_pool()
    expired_ids: list[str] = []
    for it in items:
        if it.get("status") != "in_pool":
            continue
        if not it.get("time_sensitive"):
            continue
        exp = parse_date(it.get("expiry_date"))
        if exp is None or ref_d is None:
            continue
        if ref_d > exp:
            it["status"] = "expired"
            expired_ids.append(it.get("pool_id", "?"))
    if expired_ids:
        write_pool(items)
    print(json.dumps({"expired": expired_ids, "count": len(expired_ids)},
                     ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- intake-status

def _pushed_from_pool_today(ref: str) -> int:
    """当日经池推送的条数：池内 status=pushed 且 pushed_date=今天。"""
    items = read_pool()
    return sum(1 for it in items
              if it.get("status") == "pushed" and it.get("pushed_date") == ref)


def _consecutive_zero_intake_days(items: list[dict], ref: str) -> int:
    """由 pool.py 自算（单一真源，防两处各算一份）：扫各条 enqueued_date，从今天往回数
    连续多少天 intake=0。今天有入库则 0；今天 0、昨天 0、前天有 → 2；以此类推。

    周 audit 只消费这个字段、不自己跨天算——避免两读歧义。
    """
    ref_d = parse_date(ref)
    if ref_d is None:
        return 0
    enq_dates = {it.get("enqueued_date") for it in items if it.get("enqueued_date")}
    # 池从无任何入库历史（刚建/空池）→ 返 0，不是"断供 N 天"。
    # 验证跑抓出的漏：旧逻辑对空池一路回溯到安全上界返 367，
    # 会让周 audit 把"池诞生第一天还没蓄水"误报成"产能断供一年"的最高级告警。
    # "连续 0 入库"只对"曾经有过入库、之后断供"才有意义。
    if not enq_dates:
        return 0
    # 回溯只到池最老条目日为止——再往前是"池存在之前"的空白，不算断供。
    oldest = min(enq_dates)
    n = 0
    d = ref_d
    while d.isoformat() not in enq_dates:
        if d.isoformat() < oldest:   # 越过最老条目仍没匹配（不该发生，防御）→ 停
            break
        n += 1
        d = d - timedelta(days=1)
    return n


def _oldest_in_pool_age_days(items: list[dict], ref: str) -> int | None:
    """当前 in_pool 库存里最老一条的入库天龄；池无在库返回 None。"""
    ref_d = parse_date(ref)
    ages = []
    for it in items:
        if it.get("status") != "in_pool":
            continue
        enq = parse_date(it.get("enqueued_date"))
        if enq and ref_d:
            ages.append((ref_d - enq).days)
    return max(ages) if ages else None


def cmd_intake_status(args) -> int:
    ref = args.date or today_str()
    items = read_pool()
    intake_today = sum(1 for it in items if it.get("enqueued_date") == ref)
    pool_size = sum(1 for it in items if it.get("status") == "in_pool")
    status = {
        "intake_today": intake_today,
        "pool_size": pool_size,
        "pushed_from_pool_today": _pushed_from_pool_today(ref),
        "oldest_in_pool_age_days": _oldest_in_pool_age_days(items, ref),
        "consecutive_zero_intake_days": _consecutive_zero_intake_days(items, ref),
    }
    print(json.dumps(status, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="日推池（常平仓）：入库/放粮/过期/统计。见 SKILL.md §日推池。")
    # --date 供验收快进时间用；不传则用系统当天（date），生产不传。
    parser.add_argument("--date", default=None,
                        help="覆盖参考日期 YYYY-MM-DD（验收/快进用；生产不传，用系统当天）")
    parser.add_argument("--pool-file", default=None,
                        help="覆盖池文件路径（验收隔离用；生产不传，用 user/daily-push-pool.jsonl）")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enq = sub.add_parser("enqueue", help="一篇 ≥8.0 文入池")
    p_enq.add_argument("--file", required=True, help="selected.json 路径")
    p_enq.set_defaults(func=cmd_enqueue)

    sub.add_parser("pick", help="取池中最高分未过期一篇 → stdout").set_defaults(func=cmd_pick)
    sub.add_parser("expire", help="扫全池标记过期时效文").set_defaults(func=cmd_expire)
    sub.add_parser("intake-status", help="输出池健康统计").set_defaults(func=cmd_intake_status)

    p_mp = sub.add_parser("mark-pushed", help="标记某条已推")
    p_mp.add_argument("--pool-id", required=True)
    p_mp.set_defaults(func=cmd_mark_pushed)

    args = parser.parse_args(argv)

    # --pool-file 覆盖（验收隔离，绝不污染真实池）。
    global POOL
    if args.pool_file:
        POOL = Path(args.pool_file).resolve()

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
