#!/usr/bin/env python3
"""列出年度检查对照里已过期和快到期的检查。

读 Markdown 表格里表头含「到期」的列。到期值写 YYYY、YYYY-MM 或 YYYY-MM-DD，
分别表示最晚在这一年、这个月、这一天之内做；写「待报告」的单列为等报告；空着或写「—」的行不提醒。

用法：
  python3 scripts/due.py                       # 读 user/checkup-tracker.md
  python3 scripts/due.py <对照表.md> --today 2026-11-10 --days 60
  python3 scripts/due.py --self-test
只用 Python 标准库。
"""
import argparse
import calendar
import datetime as dt
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_TRACKER = ROOT / "user" / "checkup-tracker.md"
SKIP_VALUES = {"", "—", "-", "–", "无"}
PENDING_VALUES = {"待报告"}
NAME_HINTS = ("检查", "信号", "项目")
NEXT_HINTS = ("下一步",)


def deadline(value):
    """把到期值换成最晚日期；看不懂返回 None。"""
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value)
    if not m:
        return None
    year = int(m.group(1))
    if m.group(2) is None:
        return dt.date(year, 12, 31)
    month = int(m.group(2))
    if not 1 <= month <= 12:
        return None
    if m.group(3) is None:
        return dt.date(year, month, calendar.monthrange(year, month)[1])
    try:
        return dt.date(year, month, int(m.group(3)))
    except ValueError:
        return None


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator(line):
    return bool(re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", line.strip()))


def pick(header, hints):
    for i, h in enumerate(header):
        if any(k in h for k in hints):
            return i
    return None


def scan(text):
    """返回 [(章节, 名称, 下一步, 到期原值, 最晚日期或 None)]。"""
    rows, header, section = [], None, ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            section, header = s.lstrip("#").strip(), None
            continue
        if not s.startswith("|"):
            header = None
            continue
        if is_separator(s):
            continue
        row = cells(s)
        if header is None:
            if any(c == "到期" for c in row):
                header = row
            continue
        due_i = header.index("到期")
        if due_i >= len(row):
            continue
        value = row[due_i]
        if value in SKIP_VALUES:
            continue
        name_i = pick(header, NAME_HINTS)
        next_i = pick(header, NEXT_HINTS)
        name = row[name_i] if name_i is not None and name_i < len(row) else " / ".join(row[:2])
        nxt = row[next_i] if next_i is not None and next_i < len(row) else ""
        rows.append((section, name, nxt, value, deadline(value)))
    return rows


def report(rows, today, days):
    overdue, soon, bad, pending = [], [], [], []
    for section, name, nxt, value, end in rows:
        if value in PENDING_VALUES:
            pending.append((name, nxt))
        elif end is None:
            bad.append((section, name, value))
        elif end < today:
            overdue.append((end, name, nxt, value))
        elif (end - today).days <= days:
            soon.append((end, name, nxt, value))
    out = []
    if overdue:
        out.append("已过期：")
        out += [f"  - {n}（到期 {v}）{'：' + x if x else ''}" for _, n, x, v in sorted(overdue)]
    if soon:
        out.append(f"{days} 天内到期：")
        out += [f"  - {n}（到期 {v}）{'：' + x if x else ''}" for _, n, x, v in sorted(soon)]
    if pending:
        out.append("等报告（到手后照原件抄录，再填到期）：")
        out += [f"  - {n}{'：' + x if x else ''}" for n, x in pending]
    if bad:
        out.append("看不懂的到期值（请改成 YYYY、YYYY-MM 或 YYYY-MM-DD）：")
        out += [f"  - [{s}] {n}：{v}" for s, n, v in bad]
    if not out:
        out.append(f"{days} 天内没有到期的检查。" if rows else "还没有写到期的检查。")
    return "\n".join(out)


def self_test():
    sample = """# 年度检查对照
| 序 | 骑士 | 该有的检查 | 现状 | 下一步 | 到期 |
|---|---|---|---|---|---|
| 1 | 代谢 | HbA1c | 5.5% | 每年 | 2027 |
| 3 | 癌症 | 肠镜 | 没做过 | 另约 | 2026 |
| 2 | 心脑血管 | Lp(a) | 已测 | 一生一次 | — |
| 3 | 癌症 | 胃镜 | 没做过 | 另约 | 2026-03 |
| 5 | 体能 | 眼底 | 没做过 | 问 | 明年 |
## 在追信号
| 信号 | 首见 | 最近 | 下一步 | 到期 |
|---|---|---|---|---|
| 肺结节 | 2021 | 2024 | 复查 CT | 2026-11-30 |
| APOE | 2026 | 已做 | 抄录 | 待报告 |
"""
    rows = scan(sample)
    assert len(rows) == 6, rows
    assert deadline("2026") == dt.date(2026, 12, 31)
    assert deadline("2026-02") == dt.date(2026, 2, 28)
    assert deadline("2026-13") is None and deadline("2026-02-30") is None
    text = report(rows, dt.date(2026, 11, 10), 60)
    assert "已过期：\n  - 胃镜" in text, text
    assert "肺结节" in text and "肠镜" in text, text
    assert "HbA1c" not in text and "Lp(a)" not in text, text
    assert "眼底：明年" in text, text
    assert "等报告（到手后照原件抄录，再填到期）：\n  - APOE：抄录" in text, text
    assert "APOE：待报告" not in text, text
    assert report([], dt.date(2026, 1, 1), 60) == "还没有写到期的检查。"
    later = "| 检查 | 到期 |\n|---|---|\n| HbA1c | 2030 |\n"
    assert report(scan(later), dt.date(2026, 1, 1), 60) == "60 天内没有到期的检查。"
    print("self-test ok")


def main(argv=None):
    p = argparse.ArgumentParser(description="列出年度检查对照里已过期和快到期的检查")
    p.add_argument("tracker", nargs="?", default=str(DEFAULT_TRACKER), help="对照表路径，默认 user/checkup-tracker.md")
    p.add_argument("--today", help="按哪天算，YYYY-MM-DD，默认今天")
    p.add_argument("--days", type=int, default=60, help="提前多少天提醒，默认 60")
    p.add_argument("--self-test", action="store_true", help="跑内置自检")
    a = p.parse_args(argv)
    if a.self_test:
        self_test()
        return 0
    path = pathlib.Path(a.tracker)
    if not path.is_file():
        print(f"找不到 {path}。先在本 skill 目录跑 ./init.sh，或把对照表路径作为参数传进来。", file=sys.stderr)
        return 2
    try:
        today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    except ValueError:
        print(f"--today 要写成 YYYY-MM-DD，收到的是 {a.today}。", file=sys.stderr)
        return 2
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"{path} 不是 UTF-8 编码，请另存为 UTF-8 再试。", file=sys.stderr)
        return 2
    print(report(scan(text), today, a.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
