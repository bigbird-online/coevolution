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

# 与 checkup-planning 共用档案时，它的模板缺解读要用的几栏，补在档案末尾（只补一次）。
if [ -e user/profile.md ] && ! grep -q '^- 身高' user/profile.md; then
  cat >> user/profile.md <<'ADD'

## 解读要用的补充（checkup-interpretation）

- 身高：（算腰围身高比用）
- 城乡、南方或北方：（算心血管病十年风险用）
- 血压、血糖、血脂：（是否诊断过，是否在用药）
- 内镜：（肠镜、胃镜做过没有、哪年、结论）
- 家族史：高血压、早发心脑血管病、糖尿病、癌症（哪一种）、痴呆

### 症状

> 最近有没有：运动时胸闷胸痛；单侧无力或麻木、说话不清；新出现的记忆变差、找词困难、在熟悉的地方迷路。没有就写「无」。

- 
ADD
  echo "[补充] user/profile.md 末尾补了解读要用的几栏"
fi

echo
echo "下一步：把历次体检报告的 PDF 或照片放进 user/checkups/，手表、家测血压的导出放进 user/wearables/；"
echo "然后对 Agent 说「帮我读体检报告」。"
