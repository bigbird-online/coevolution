#!/usr/bin/env bash
# 装载：准备 user/。同级有 checkup-planning/ 的，把 user/ 链到它的 user/，两个 skill 共用一份档案；
# 没有的，自建 user/ 并拷入 assets/ 里的模板。已存在的文件不覆盖，可重复运行。
set -euo pipefail
cd "$(dirname "$0")"

planning="../checkup-planning"

if [ -L user ]; then
  echo "[跳过] user/ 已链到 $(readlink user)"
elif [ -d user ]; then
  echo "[跳过] user/ 已存在"
elif [ -d "$planning" ]; then
  if [ ! -d "$planning/user" ]; then
    bash "$planning/init.sh" >/dev/null
    echo "[新建] $planning/user/（跑了 checkup-planning 的 init.sh）"
  fi
  ln -s "$planning/user" user
  echo "[链接] user/ -> $planning/user/，与 checkup-planning 共用档案"
else
  mkdir -p user
  echo "[新建] user/"
fi

mkdir -p user/checkups user/wearables user/interpretation
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
echo "下一步：把历次体检报告的 PDF 或照片放进 user/checkups/，手表、家测血压的导出放进 user/wearables/；"
echo "然后对 Agent 说「帮我读体检报告」。"
