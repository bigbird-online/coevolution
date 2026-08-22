#!/usr/bin/env bash
# companion —— 装载脚本（幂等）。
#
# 做四件事：
#   1. 检查前置：uv 存在（缺则明确报错退非 0，不静默）。
#   2. `uv sync` 建本 skill 自己的 .venv（article_collector.py 的引擎跑在其上）。
#   3. 把 assets/*.template.yaml 拷成 user/profile.yaml、user/sources.yaml；
#      已存在则不覆盖（防重跑抹掉已填配置），只提示。
#   4. 建 user/pushed/ 运行时目录；跑完打印下一步。
#
# 反复跑安全：已填的配置不会被覆盖，.venv 已在则 uv sync 只做增量。
#
# 用法：  ./init.sh      （在本目录内运行）

set -euo pipefail

# 钉死到脚本自身所在目录，不依赖调用者的 cwd。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ASSETS="$ROOT/assets"
USER_DIR="$ROOT/user"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
skip() { printf '  [skip] %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

say "== companion 装载 =="
say ""

# ---- 1. 前置检查 ----------------------------------------------------------
say "[1/4] 检查前置工具"
if ! command -v uv >/dev/null 2>&1; then
  die "未找到 uv。请先安装 uv（https://docs.astral.sh/uv/），它会自动拉取 Python >=3.11。"
fi
ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

# pyproject.toml 要求 Python >=3.11；uv 会在 sync 时自行拉取满足要求的解释器，
# 无需系统预装 3.11。这里只做存在性提示，不拿系统 python3 版本当门槛。
if command -v python3 >/dev/null 2>&1; then
  ok "系统 python3 $(python3 --version 2>&1 | awk '{print $2}')（仅参考；.venv 由 uv 按 >=3.11 建）"
else
  skip "系统无 python3——不影响，uv sync 会自行拉取 >=3.11"
fi

# ---- 2. 建 .venv ----------------------------------------------------------
say ""
say "[2/4] uv sync 建 .venv（引擎依赖：feedparser/filelock/httpx/...）"
uv sync
VENV_PY="$ROOT/.venv/bin/python3"
[ -x "$VENV_PY" ] || die "uv sync 完成但未找到 $VENV_PY——环境异常，请检查 uv sync 输出。"
ok ".venv 就绪：$VENV_PY"

# ---- 3. 拷配置模板（不覆盖已填） -----------------------------------------
say ""
say "[3/4] 生成 user/ 配置（已存在则不覆盖）"
mkdir -p "$USER_DIR"

copy_template() {
  local src="$1" dst="$2" name="$3"
  [ -f "$src" ] || die "模板缺失：$src"
  if [ -f "$dst" ]; then
    skip "$name 已存在，保留你已填的内容（未覆盖）"
  else
    cp "$src" "$dst"
    ok "$name 已生成 —— 待填"
  fi
}

copy_template "$ASSETS/profile.template.yaml" "$USER_DIR/profile.yaml" "user/profile.yaml"
copy_template "$ASSETS/sources.template.yaml" "$USER_DIR/sources.yaml" "user/sources.yaml"

# ---- 4. 运行时目录 --------------------------------------------------------
say ""
say "[4/4] 建运行时目录"
mkdir -p "$USER_DIR/pushed"
ok "user/pushed/"

# ---- 收尾：下一步 ---------------------------------------------------------
say ""
say "== 装载完成。接下来你要亲自做的（工具不替你猜）：=="
say ""
say "  1. 填 user/profile.yaml —— 每一项都因人而异，空着＝还没跟本人对齐。最关键的："
say "       focus_domain      本轮聚焦领域"
say "       landing_criteria  认什么算「读有所获、产生了真实决定」"
say "       score_baseline    本人的评分基线（不能沿用别人的门槛）"
say "     （delivery: 段可留空 —— 不配＝只落盘到 user/pushed/，是完全合法的用法。）"
say ""
say "  2. 按你的 focus_domain 增删 user/sources.yaml（种子只保证「能跑」，不保证「对味」）。"
say ""
say "  3. 验证装成功（最小命令）："
say "       ./.venv/bin/python3 scripts/deliver.py status"
say ""
say "  4. 跑链路："
say "       ./.venv/bin/python3 scripts/article_collector.py collect --days 7"
say "       ./.venv/bin/python3 scripts/pool.py intake-status"
