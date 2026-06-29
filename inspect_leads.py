#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_leads.py —— 采集质量「体检」：一条命令量化判断“搜索到不到位”

读 data/leads.csv（采集原始）+ data/queue.csv（打分保留）+ config.json，汇总 4 个关键指标：
  ① 召回/供给   —— 抓回多少条、各平台分布
  ② 评论意图    —— 多少条把热评摘进了正文（高价值意图“多少钱/怎么寄”多在评论里）
  ③ 买家信号    —— 含意图词的比例、疑似同行广告的比例、队列里的意图命中率
  ④ 精度/留存   —— 打分后保留多少、优先级与分数分布
并列出 top 线索给你亲眼核对（是“买家在问” 还是 “同行打广告”）。

只读本地 CSV，不联网、不发布。
用法：python3 inspect_leads.py [--top 10]
"""

import argparse
from collections import Counter

from store import load_config, read_csv, path_of

# 同行/卖家广告的粗启发词（仅作提示，不参与打分）——命中越多越像“卖家在打广告”而非“买家在问”
SELLER_HINT = [
    "承接", "承运", "专线", "一手", "庄家", "招代理", "代理加盟", "价格表", "报价单",
    "欢迎咨询", "免费咨询", "诚信", "十年", "多年经验", "全国上门", "门到门服务",
    "加微", "vx", "v信", "微信同号", "热线", "联系电话", "一站式",
]


def contains_any(text, words):
    return [w for w in words if w and w in text]


def bar(n, total, width=22):
    if total <= 0:
        return ""
    filled = round(width * n / total)
    return "█" * filled + "·" * (width - filled)


def pct(n, d):
    return (n / d * 100) if d else 0.0


def text_of(row):
    return f"{row.get('title','')} {row.get('content_excerpt','')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10, help="预览前 N 条高分线索")
    args = ap.parse_args()

    cfg = load_config()
    # 意图词：新版 config 拆成 强/弱 两档；兼容旧版 intent_signals
    intent = (cfg.get("intent_strong", []) + cfg.get("intent_weak", [])) or cfg.get("intent_signals", [])

    leads = read_csv(path_of("leads"))
    queue = read_csv(path_of("queue"))

    if not leads and not queue:
        print("没有数据。先 ./run.sh crawl 采集真数据，或 ./run.sh demo 用样例跑通。")
        return

    # ① 召回/供给
    n_raw = len(leads)
    plat_raw = Counter((l.get("platform") or "未知") for l in leads)

    # ② 评论意图：content_excerpt 里有“｜评论”
    with_comments = sum(1 for l in leads if "｜评论" in (l.get("content_excerpt") or ""))

    # ③ 买家信号
    raw_intent = sum(1 for l in leads if contains_any(text_of(l), intent))
    raw_seller = sum(1 for l in leads if contains_any(text_of(l), SELLER_HINT))

    # ④ 精度/留存
    n_kept = len(queue)
    prio = Counter(q.get("priority", "") for q in queue)
    q_intent = sum(1 for q in queue if (q.get("intent_hits") or "").strip())
    scores = [int(float(q.get("score") or 0)) for q in queue]

    print("=" * 52)
    print("  采集质量体检（只读，不发送）")
    print("=" * 52)

    print(f"\n① 召回/供给：抓回 {n_raw} 条原始线索")
    for p, n in plat_raw.most_common():
        print(f"     {p:<12} {n:>4}  {bar(n, n_raw)}")
    if n_raw == 0:
        print("   ⚠ 抓回 0 条 → 搜索没返回 / 登录态没拿到 / 被风控限了。")

    print(f"\n② 评论意图是否进来：{with_comments}/{n_raw} 条含合并热评"
          f"（{pct(with_comments, n_raw):.0f}%）")
    if n_raw and with_comments == 0:
        print("   ⚠ 没合并到评论 → 高价值意图（多少钱/怎么寄常在评论里）会漏；"
              "确认 crawl 带了 --get_comment。")

    print("\n③ 买家信号：")
    print(f"   原始含意图词       {raw_intent}/{n_raw}（{pct(raw_intent, n_raw):.0f}%）")
    print(f"   疑似同行广告(启发)  {raw_seller}/{n_raw}（{pct(raw_seller, n_raw):.0f}%）")
    print(f"   队列意图命中       {q_intent}/{n_kept}（{pct(q_intent, n_kept):.0f}%）  ← 越高越值钱")

    print(f"\n④ 精度/留存：打分后保留 {n_kept} 条（留存 {pct(n_kept, n_raw):.0f}%）")
    for pr in ["high", "mid", "low"]:
        n = prio.get(pr, 0)
        if n:
            print(f"     {pr:<6} {n:>4}  {bar(n, n_kept)}")
    if scores:
        srt = sorted(scores)
        print(f"   分数区间 {srt[0]}–{srt[-1]}（中位 {srt[len(srt)//2]}）")

    top = sorted(queue, key=lambda q: int(float(q.get("score") or 0)), reverse=True)[:args.top]
    if top:
        print(f"\n── 前 {len(top)} 条（亲眼核对：买家在问？还是同行广告？）──")
        for i, q in enumerate(top, 1):
            title = (q.get("title") or "(无标题)").strip()[:36]
            print(f" {i:>2}. [{q.get('priority',''):>4}] {str(q.get('score','')):>3} "
                  f"{q.get('platform','')}  {title}")
            print(f"     意图: {q.get('intent_hits') or '-'}")
            print(f"     {q.get('url','')}")

    print("\n判断口径：")
    print("  · ② 接近 0 → 评论没抓到，等于白搜（意图多在评论里）")
    print("  · ③ 队列意图命中率低 / 同行广告占比高 → 搜到的是卖家不是买家，调 keywords.txt")
    print("  · ④ 留存率极低 → 关键词太宽或搜到的多不相关")
    print("  · 最后点开上面链接亲眼看：真有人在问『怎么寄/多少钱/清关』才算到位")


if __name__ == "__main__":
    main()
