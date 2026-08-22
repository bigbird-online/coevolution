#!/usr/bin/env python3
"""选文层——把采集打分后的文章，挑出够格的一篇导成 selected.json。

## 为什么要这一步（补的是哪节断链）

采集器（article_collector.py）采文时算了一个 0-100 的分（作者权威 0-40 +
新鲜度 0-30 + 观点潜力 0-30），写进每篇文章 md 的 frontmatter `score_100`。
但日推池 pool.py 的 `enqueue` 认的是 selected.json、且门槛是 **0-10 制的 8.0**。

这两头之间原本没有任何脚本衔接——采集写 md、pool 读 json，分制还不一样
（一个 0-100、一个 0-10）。本脚本就是这节缺失的桥：
**扫已采集文章 → 读 score_100 → 换算到 0-10 制 → 导出最高分一篇为 selected.json**。

## 分制换算（口径写死，别猜）

    score_10 = score_100 / 10

所以 pool 的 8.0 硬门 = 采集原始分 **80 分**（满分 100 里的 80）。
这是唯一的换算约定；改门槛改这里，不在别处散落。

## 用法

    select.py                     # 扫全池，挑最高分一篇 → 打印 selected.json 到 stdout
    select.py --out sel.json      # 同时写到文件
    select.py --min 8.0           # 只在有文 ≥ 该 0-10 分时才导出（默认 0，即总导最高的）
    select.py --top 3             # 导出前 N 篇（JSON 数组）；默认 1（单篇 object）

导出的 selected.json 直接喂给：pool.py enqueue --file selected.json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

SKILL_ROOT = Path(__file__).resolve().parent.parent
ARTICLE_POOL_DIR = SKILL_ROOT / "user" / "article-pool"

# 分制换算：采集是 0-100 制，pool 的 8.0 门是 0-10 制。除以 10，别在别处再换。
SCORE_100_TO_10 = 10.0


def _parse_frontmatter(md_text):
    """抠出 md 顶部的 yaml frontmatter（--- 包起来那段）。解析失败返回 None。"""
    if not md_text.startswith("---"):
        return None
    end = md_text.find("\n---", 3)
    if end == -1:
        return None
    block = md_text[3:end]
    try:
        return yaml.safe_load(block) or {}
    except Exception:  # noqa: BLE001 — 单篇坏 frontmatter 不该让整轮选文崩
        return None


def _iter_articles():
    """遍历 article-pool 下所有文章 md，产出 (path, frontmatter_dict)。

    跳过 translated/ 子目录（那是译文产物，非采集原文），跳过无 score_100 的。
    """
    if not ARTICLE_POOL_DIR.exists():
        return
    for md_path in sorted(ARTICLE_POOL_DIR.rglob("*.md")):
        if "translated" in md_path.parts:
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        fm = _parse_frontmatter(text)
        if not fm or fm.get("score_100") is None:
            continue
        yield md_path, fm


def _to_selected(md_path, fm):
    """把一篇文章的 frontmatter 转成 pool.enqueue 认的 selected.json 结构。

    字段对齐 pool.cmd_enqueue：它读 score/title/url/source/domain/scoring_path。
    score 用 0-10 制（enqueue 的 8.0 门就是这个制）。
    """
    try:
        score_10 = round(float(fm["score_100"]) / SCORE_100_TO_10, 2)
    except (TypeError, ValueError):
        return None
    return {
        "score": score_10,
        "score_100": fm.get("score_100"),  # 留原始分，便于回溯
        "title": fm.get("title", ""),
        "url": fm.get("article_url", fm.get("source_url", "")),
        "source": fm.get("source", ""),
        "domain": fm.get("category", ""),
        "scoring_path": "collect.score_100/10",  # 说明这个分怎么来的
        "article_file": str(md_path.relative_to(SKILL_ROOT)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="从采集池选出够格文章导成 selected.json")
    parser.add_argument("--out", default=None, help="写到文件（默认只打印 stdout）")
    parser.add_argument("--min", type=float, default=0.0,
                        help="最低 0-10 分门槛，低于此全不导（默认 0=总导最高的）")
    parser.add_argument("--top", type=int, default=1,
                        help="导出前 N 篇（>1 输出 JSON 数组）；默认 1=单篇 object")
    args = parser.parse_args(argv)

    selected = []
    for md_path, fm in _iter_articles():
        s = _to_selected(md_path, fm)
        if s is not None:
            selected.append(s)

    selected.sort(key=lambda x: x["score"], reverse=True)
    selected = [s for s in selected if s["score"] >= args.min]

    if not selected:
        print("[select.py] 没有够格文章可选"
              + (f"（无 ≥{args.min} 的）" if args.min > 0 else "（池空或均无 score_100）"),
              file=sys.stderr)
        return 1

    picked = selected[: max(1, args.top)]
    out_obj = picked if args.top > 1 else picked[0]
    payload = json.dumps(out_obj, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"[select.py] 已写 {args.out}：{len(picked)} 篇，最高分 {picked[0]['score']}",
              file=sys.stderr)
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
