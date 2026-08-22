#!/usr/bin/env bash
# article-to-epub.sh — 单篇译文 md → EPUB
#
# 用法：./article-to-epub.sh <译文.md> [输出.epub]
# 默认输出：同目录 companion-<今日>.epub（投递环节需要固定可预测的文件名）
#
# 为什么整篇喂 pandoc、不按段截取：
#   曾用「按标记截取正文段」的做法（如 sed 只截某个固定小标题之后的内容），
#   文章 md 里没有那个标记 → 截出空内容 → EPUB 正文为空。
#   文章轴是「整篇译文成书」，去 YAML frontmatter 后整体喂 pandoc 即可。
#
# 依赖：pandoc >= 3.0，需在 PATH 中（macOS 与 Linux 均可跑，实测通过）。
set -euo pipefail

INPUT="${1:?用法: $0 <译文.md> [输出.epub]}"
[ -f "$INPUT" ] || { echo "错误：文件不存在 — $INPUT" >&2; exit 1; }

INPUT_DIR="$(cd "$(dirname "$INPUT")" && pwd)"
DATE="$(date +%Y-%m-%d)"
OUTPUT="${2:-${INPUT_DIR}/companion-${DATE}.epub}"

command -v pandoc >/dev/null 2>&1 || { echo "错误：缺 pandoc —— 请确保 pandoc 已安装且在 PATH 中" >&2; exit 1; }

# 从 frontmatter 取 title/author，作为 EPUB 元数据
title=$(grep -m1 '^title:' "$INPUT" | sed 's/title: *//;s/"//g' || true)
author=$(grep -m1 '^author:' "$INPUT" | sed 's/author: *//;s/"//g' || true)

# 去掉首个 YAML frontmatter（前两个 --- 之间），正文喂 pandoc
BODY="$(mktemp /tmp/companion-body.XXXXXX.md)"
trap 'rm -f "$BODY"' EXIT
awk 'BEGIN{b=0} /^---$/{b++; if(b<=2) next} b>=2{print}' "$INPUT" > "$BODY"

# 防空：正文字数门槛（避免再次推空书）
bytes=$(wc -c < "$BODY")
if [ "$bytes" -lt 200 ]; then
  echo "错误：正文仅 ${bytes} 字节，疑似空内容，拒绝生成空 EPUB" >&2
  exit 2
fi

pandoc "$BODY" -o "$OUTPUT" \
  --metadata title="${title:-Companion 伴读}" \
  --metadata author="${author:-Companion}" \
  --metadata lang=zh-CN 2>/dev/null

# 验证 EPUB 正文非空
ch_bytes=$(unzip -p "$OUTPUT" 'EPUB/text/ch*.xhtml' 2>/dev/null | wc -c || echo 0)
if [ "$ch_bytes" -lt 200 ]; then
  echo "错误：生成的 EPUB 正文仅 ${ch_bytes} 字节，生成失败" >&2
  exit 3
fi

echo "✓ EPUB 生成：${OUTPUT}（正文 ${ch_bytes} 字节）"
