#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report.py —— 复盘（闭环最后一环：记录 -> 洞察），从 db 出真实转化漏斗

不再只看「发布率」，而是把线索的全生命周期摊开：
  发现(new) -> 发出(posted) -> 对外可见(visible) -> 对方回复(replied)
            -> 加到私域(added) -> 报价(quoted) -> 成交(deal / 金额)
帮你判断「到底有没有带来成交」，并反向校准关键词/模板/账号。
"""

import store

STAGE_LABEL = {
    "new": "待处理(new)", "posted": "已发出(posted)", "replied": "对方回复(replied)",
    "added": "加到私域(added)", "quoted": "已报价(quoted)", "deal": "成交(deal)",
    "dead": "跳过/失败(dead)",
}


def bar(n, total, width=24):
    if total <= 0:
        return ""
    filled = round(width * n / total)
    return "█" * filled + "·" * (width - filled)


def pct(n, d):
    return (n / d * 100) if d else 0.0


def main():
    store.init_db()
    conn = store.connect()
    total = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    if not total:
        print("db 里还没有线索。先 ./run.sh score 生成队列，再用 web/publish 处理几条。")
        conn.close()
        return

    f = store.funnel_counts(conn=conn)
    by_stage, by_visible = f["by_stage"], f["by_visible"]
    # 触达=已发出及以后（不含 new/dead）
    posted = sum(by_stage.get(s, 0) for s in ["posted", "replied", "added", "quoted", "deal"])
    visible_yes = by_visible.get("yes", 0)
    by_platform = {r["platform"]: r["c"] for r in conn.execute(
        "SELECT platform, COUNT(*) c FROM leads GROUP BY platform")}
    conn.close()

    print("=" * 52)
    print(f"  线索总数 {total} 条")
    print("=" * 52)

    print("\n转化漏斗：")
    for s in store.FUNNEL_STAGES:
        n = by_stage.get(s, 0)
        if n or s in ("new", "posted", "deal"):
            print(f"  {STAGE_LABEL.get(s, s):<16} {n:>4}  {bar(n, total)}")

    print("\n可见性（已发出线索里）：")
    if posted:
        for k in ("yes", "no", "未核验"):
            n = by_visible.get(k, 0)
            label = {"yes": "对外可见", "no": "被折叠/限流", "未核验": "未核验"}[k]
            print(f"  {label:<10} {n:>4}  {bar(n, posted)}")
        if visible_yes == 0 and by_visible.get("未核验", 0):
            print("  ⚠ 大量『未核验』——影子限流看不见，建议补『可见性核验』(下一步功能)")
    else:
        print("  （还没有已发出的线索）")

    print("\n按平台：")
    for p, n in sorted(by_platform.items(), key=lambda x: -x[1]):
        print(f"  {p or '未知':<12} {n:>4}  {bar(n, total)}")

    print("\n关键转化率：")
    print(f"  发出率   posted/total      = {pct(posted, total):4.0f}%  ({posted}/{total})")
    print(f"  可见率   visible/posted    = {pct(visible_yes, posted):4.0f}%  ({visible_yes}/{posted})")
    print(f"  回复率   replied/posted    = {pct(by_stage.get('replied',0), posted):4.0f}%")
    print(f"  成交数   deal              = {f['deals']} 单   成交额 ¥{f['deal_amount']:.0f}")
    print("\n提示：发出≠被看到。可见率长期偏低 = 账号被影子限流，该换号/降频，而不是继续发。")


if __name__ == "__main__":
    main()
