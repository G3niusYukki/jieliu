#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report.py —— 复盘统计（闭环的最后一环：记录 -> 洞察）

读 data/history.csv，汇总：处理总量、各状态占比、各平台/优先级分布、最近活跃。
帮你判断「到底有没有带来线索」，再决定要不要继续投入。
"""

from collections import Counter
from datetime import datetime, timedelta

from store import read_csv, path_of


def parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime((s or "").strip(), fmt)
        except ValueError:
            continue
    return None


def bar(n, total, width=24):
    if total <= 0:
        return ""
    filled = round(width * n / total)
    return "█" * filled + "·" * (width - filled)


def main():
    rows = read_csv(path_of("history"))
    if not rows:
        print("还没有历史记录（data/history.csv 为空）。先用 publish_assist.py 或 Web 控制台处理几条。")
        return

    total = len(rows)
    by_status = Counter(r.get("status", "") for r in rows)
    by_platform = Counter(r.get("platform", "") for r in rows)
    by_priority = Counter(r.get("priority", "") for r in rows)

    now = datetime.now()
    last7 = sum(1 for r in rows
                if (d := parse_dt(r.get("processed_at"))) and (now - d) <= timedelta(days=7))

    posted = by_status.get("posted", 0)
    print("=" * 48)
    print(f"  累计处理 {total} 条 ｜ 近 7 天 {last7} 条")
    print("=" * 48)

    print("\n按状态：")
    for st in ["posted", "skipped", "failed", "opened", "new"]:
        n = by_status.get(st, 0)
        if n:
            print(f"  {st:<8} {n:>4}  {bar(n, total)}  {n/total*100:4.0f}%")

    print("\n按平台：")
    for p, n in by_platform.most_common():
        print(f"  {p or '未知':<12} {n:>4}  {bar(n, total)}")

    print("\n按优先级：")
    for pr in ["high", "mid", "low"]:
        n = by_priority.get(pr, 0)
        if n:
            print(f"  {pr:<6} {n:>4}  {bar(n, total)}")

    rate = posted / total * 100 if total else 0
    print(f"\n发布率（posted/总）：{rate:.0f}%  ——  高优先级里 posted "
          f"{sum(1 for r in rows if r.get('priority')=='high' and r.get('status')=='posted')} 条")
    print("\n提示：长期看高优先级的发布率和后续回复，才知道这套排序值不值得继续。")


if __name__ == "__main__":
    main()
