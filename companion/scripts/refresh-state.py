#!/usr/bin/env python3
# Print the live dynamic snapshot to stdout (do not persist).
# STATE.md is for static context; this script is for "look at the current
# situation right now" — caller (Agent) decides whether to run it.
#
# Output: past-7d pushes / domain distribution vs weight / recent member-score
# calibration. Nothing is written back to disk.
#
# Usage: python3 scripts/refresh-state.py
#   (run from companion/ root; reads user/reading-log.jsonl)

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "user" / "reading-log.jsonl"
PROFILE = ROOT / "user" / "profile.yaml"

DOMAIN_MAP = {
    "ict": "ICT",
    "ai": "ICT",
    "engineering": "ICT",
    "economics": "经济",
    "business": "经济",
    "law": "法律",
    "legal": "法律",
    "legal_safety": "法律",
    "health": "医学",
    "medicine": "医学",
    "biology": "医学",
}

# 领域权重 = 每个用户自己的关注配比，属于个人配置，不是产品默认。
# 机制保留：算「实际分布 vs 权重应有比例」的偏离，供人判断，不做机械约束。
# 缺省一律等权（1:1:1:1）＝不替新用户预设任何倾向；用户在 user/profile.yaml
# 写 domain_weight 覆盖，例：
#   domain_weight:
#     ICT: 3
#     经济: 2
DEFAULT_WEIGHT = {"ICT": 1, "经济": 1, "法律": 1, "医学": 1}


def _profile_lines() -> list[str]:
    """读 user/profile.yaml 的有效行（去注释）；不存在/读不了则空。"""
    if not PROFILE.exists():
        return []
    try:
        raw = PROFILE.read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln.split("#", 1)[0].rstrip() for ln in raw.splitlines()]


def profile_block(name: str) -> dict[str, str]:
    """取 profile.yaml 里某个顶层块下的 `键: 值` 对（极简解析，只支持一层缩进）。

    本脚本刻意不引第三方 YAML 依赖——个人配置格式再怎么歪也不该让快照跑不出来，
    解析不出的行一律跳过。
    """
    out: dict[str, str] = {}
    in_block = False
    for line in _profile_lines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            in_block = line.strip().rstrip(":") == name
            continue
        if not in_block or ":" not in line:
            continue
        key, _, val = line.strip().partition(":")
        out[key.strip().strip("\"'")] = val.strip().strip("\"'")
    return out


def profile_scalar(name: str) -> str | None:
    """取 profile.yaml 里某个顶层标量 `名: 值`；没有则 None。"""
    for line in _profile_lines():
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, _, val = line.partition(":")
        if key.strip() == name:
            return val.strip().strip("\"'") or None
    return None


def load_local_tz() -> tzinfo:
    """展示用时区偏移。用户在 profile.yaml 写 timezone_offset_hours 覆盖；
    没配则跟随运行机器的本地时区（不替新用户预设某个国家）。"""
    val = profile_scalar("timezone_offset_hours")
    if val is not None:
        try:
            return timezone(timedelta(hours=float(val)))
        except ValueError:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def load_weight() -> dict[str, int]:
    """从 user/profile.yaml 读 domain_weight；没配则等权。"""
    weight: dict[str, int] = {}
    for key, val in profile_block("domain_weight").items():
        try:
            weight[key] = int(float(val))
        except ValueError:
            continue
    return weight or dict(DEFAULT_WEIGHT)


LOCAL_TZ = load_local_tz()
WEIGHT = load_weight()


def classify_domain(entry: dict) -> str:
    title = (entry.get("title") or "").lower()
    relevance = (entry.get("relevance") or "").lower()
    score_reason = (entry.get("score_reason") or "").lower()
    article_type = (entry.get("type") or "").lower()
    category = (entry.get("category") or "").lower()
    reason = (entry.get("reason") or "").lower()
    form = (entry.get("form") or "").lower()
    article_url = (entry.get("article_url") or "").lower()
    blob = f"{title} {relevance} {score_reason} {article_type} {category} {reason} {form} {article_url}"
    # category 字段直接命中优先（reading-log 已带显式 category=ict/health/law/economics）
    if category == "health" or "health/" in article_url:
        return "医学"
    if category == "law" or "law/" in article_url:
        return "法律"
    if category in ("economics", "economy") or "economics/" in article_url:
        return "经济"
    if category == "ict" or "ict/" in article_url:
        return "ICT"
    # Order matters: check medicine / law before ICT (some AI articles mention health).
    if any(k in blob for k in [
        "生理学", "生物医学", "drug", "medicine", "health", "vaccine", "ebola", "nutrition",
        "stem cell", "therapy", "autism", "干细胞", "自闭症", "clinical", "fda", "sbm",
        "lysenko", "rfk", "legionnaires", "filovirus", "delusion", "screening", "rct"
    ]):
        return "医学"
    if any(k in blob for k in [
        "legal_safety", "申诉", "环境安全", "domestic worker", "human rights", "press freedom",
        "humphrey", "executor", "constitutional", "supreme court", "scotus", "judicial",
        "汉弗莱", "宪政", "判例", "立法", "司法", "hkfp", "cpj", "hrw", "lawfare",
        "court", "tribunal", "正义", "regulation", "regulatory", "treaty", "sanction"
    ]):
        return "法律"
    if any(k in blob for k in [
        "economic", "经济", "business", "capital", "stratechery", "market", "投行", "vc 投资",
        "macro", "fed", "fomc", "inflation", "trade", "commodity", "gold", "bitcoin",
        "treasury", "联储", "通胀", "贸易", "汇率", "tariff"
    ]):
        return "经济"
    if any(k in blob for k in [
        "ai", "ml", "engineering", "vibe coding", "llm", "agent", "model", "coevolution", "ict",
        "anthropic", "openai", "hugging face", "transformers", "fetch", "project fetch",
        "skill", "claude", "gpt", "qwen", "deepseek", "ocr", "yolo", "robot", "agentic"
    ]):
        return "ICT"
    return "其它"


def iter_selected(days: int) -> list[dict]:
    if not LOG.exists():
        return []
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=days)
    out: list[dict] = []
    with LOG.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("action") not in ("selected", "selected_for_companion"):
                continue
            date_str = obj.get("date") or ""
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
            except ValueError:
                continue
            if d < cutoff:
                continue
            obj["_domain"] = classify_domain(obj)
            out.append(obj)
    out.sort(key=lambda x: x.get("date") or "")
    return out


def iter_member_scores(days: int) -> list[dict]:
    if not LOG.exists():
        return []
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=days)
    out: list[dict] = []
    with LOG.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if "member_score" not in obj:
                continue
            # 系统预判分：优先读中性字段名；后面几个是旧数据的历史字段名，
            # 保留只为读旧 reading-log 不崩。新写出的数据一律用 predicted / predicted_score。
            predicted = (
                obj.get("predicted")
                or obj.get("predicted_score")
                or obj.get("ken_predicted")
                or obj.get("ken_score_was")
                or obj.get("ken_score")
            )
            if predicted is None:
                continue
            date_str = obj.get("date") or ""
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
            except ValueError:
                continue
            if d < cutoff:
                continue
            out.append({
                "date": date_str,
                "title": obj.get("title") or obj.get("article_id") or "?",
                "predicted": float(predicted),
                "member": float(obj["member_score"]),
                "delta": float(obj["member_score"]) - float(predicted),
            })
    out.sort(key=lambda x: x["date"])
    return out


def render_distribution(selected: list[dict]) -> str:
    counts = Counter(s["_domain"] for s in selected)
    total = sum(counts.values())
    if total == 0:
        return "（过去 7 天无推送记录）"
    parts = []
    weight_sum = sum(WEIGHT.values())
    # 先列有权重的域（按用户配置的顺序），再列权重外冒出来的域（含"其它"）
    domains = list(WEIGHT) + [d for d in counts if d not in WEIGHT]
    for dom in domains:
        n = counts.get(dom, 0)
        if dom not in WEIGHT and n == 0:
            continue
        w = WEIGHT.get(dom, 0)
        expected_ratio = (w / weight_sum) if (w and weight_sum) else 0
        expected = round(expected_ratio * total, 1)
        delta = n - expected
        sign = "+" if delta > 0 else ""
        if w:
            parts.append(
                f"{dom} {n}（权重 {w}/{weight_sum}，按比例应 {expected}，偏 {sign}{delta:.1f}）"
            )
        else:
            parts.append(f"{dom} {n}（权重 0，外溢）")
    return "  - " + "\n  - ".join(parts)


def render_recent_pushes(selected: list[dict], member_scores: list[dict]) -> str:
    if not selected:
        return "  （过去 7 天无推送）"
    score_map = {s["title"]: s for s in member_scores}
    lines = []
    for s in selected[-7:]:
        date = s.get("date", "?")
        title = (s.get("title") or "?")[:60]
        dom = s["_domain"]
        # 同上：中性字段优先，旧字段名仅作读旧数据的兼容回退。
        # 回退链与 iter_member_scores 保持一致——原先这里只认一个旧名，
        # 导致带其它旧字段名的记录显示成 "?"。
        predicted = (
            s.get("predicted")
            or s.get("predicted_score")
            or s.get("ken_predicted")
            or s.get("ken_score_was")
            or s.get("ken_score")
            or "?"
        )
        member_entry = None
        # match by title prefix
        for k, v in score_map.items():
            if k and (k.startswith(title[:20]) or title.startswith((k or "")[:20])):
                member_entry = v
                break
        member_str = f"会员 {member_entry['member']}" if member_entry else "会员 —"
        lines.append(f"  - {date} [{dom}] {title} | 系统预判 {predicted} / {member_str}")
    return "\n".join(lines)


def render_calibration(member_scores: list[dict]) -> str:
    if not member_scores:
        return "  （过去 14 天无会员校准数据）"
    deltas = [m["delta"] for m in member_scores]
    avg = sum(deltas) / len(deltas)
    lines = [f"  - 近 {len(member_scores)} 次校准：会员实测 - 系统预判 = 平均 {avg:+.2f}（负=系统预判系统性偏高）"]
    for m in member_scores[-5:]:
        title = (m["title"] or "?")[:50]
        lines.append(f"  - {m['date']}: {title} | 系统预判 {m['predicted']} → 会员 {m['member']}（{m['delta']:+.1f}）")
    return "\n".join(lines)


def render_dynamic() -> str:
    now = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M %Z")
    selected_7d = iter_selected(7)
    scores_14d = iter_member_scores(14)
    weight_label = (
        f"{':'.join(WEIGHT)} = {':'.join(str(v) for v in WEIGHT.values())}"
        if WEIGHT else "（未配置）"
    )
    weight_note = (
        "缺省等权——在 user/profile.yaml 的 domain_weight 里改成自己的配比"
        if WEIGHT == DEFAULT_WEIGHT
        else "取自 user/profile.yaml 的 domain_weight"
    )

    return f"""# companion 当前动态快照（live，不落盘）
# 生成时间：{now}

## 过去 7 天推送（{len(selected_7d)} 篇）

{render_recent_pushes(selected_7d, scores_14d)}

## 当前领域分布 vs 权重（过去 7 天）

> 权重 {weight_label}（{weight_note}）
> 这是**判断维度**，不是机械约束。≥8.0 仍是硬门；偏离权重看到就好，不强行凑。

{render_distribution(selected_7d)}

## 近期会员评分校准（过去 14 天）

{render_calibration(scores_14d)}
"""


def main() -> int:
    print(render_dynamic())
    return 0


if __name__ == "__main__":
    sys.exit(main())
