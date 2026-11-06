#!/usr/bin/env bash
# 装载：建 user/，把 assets/ 里的模板拷进去。已存在的文件不覆盖，可重复运行。
set -euo pipefail
cd "$(dirname "$0")"

mkdir -p user/checkups
for t in assets/*.template.md; do
  name="$(basename "$t" .template.md).md"
  if [ -e "user/$name" ]; then
    echo "[跳过] user/$name 已存在"
  else
    cp "$t" "user/$name"
    echo "[新建] user/$name"
  fi
done

echo
if command -v python3 >/dev/null 2>&1; then
  echo "到期提醒："
  python3 scripts/due.py
else
  echo "没找到 python3：到期提醒脚本用不了，Agent 仍可直接读 user/checkup-tracker.md。"
fi

echo
echo "下一步：打开 user/profile.md，填性别、年龄段和个人史；然后对 Agent 说「我要体检，帮我挑项目」。"
