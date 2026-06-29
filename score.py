#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score.py —— 截流线索的「去重 + 打分 + 排序」引擎

输入：data/leads.csv      （采集端产出的原始线索，字段见 data/leads.sample.csv）
      data/history.csv    （已处理记录，用于去重；首次运行可不存在）
      config.json         （关键词分级、排除词、打分权重、冷却天数）
输出：data/queue.csv      （按分数排好序的「待处理」清单，给 publish_assist.py 用）

边界：本脚本只读公开数据、做筛选排序，不发布、不绕风控。
"""

import csv
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_config():
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def validate_config(cfg):
    """配置体检：缺键/结构不对时，用中文人话指出缺哪个，避免运营改崩 config 后跑出诡异结果。"""
    missing = []
    for key, subs in {
        "keyword_tiers": ["high", "mid", "low"],
        "scoring": ["tier_base", "priority_cut", "intent_cap"],
        "paths": ["leads", "queue", "history"],
    }.items():
        if not isinstance(cfg.get(key), dict):
            missing.append(key)
            continue
        missing += [f"{key}.{s}" for s in subs if s not in cfg[key]]
    missing += [k for k in ("exclude_words", "intent_strong", "intent_weak") if k not in cfg]
    if missing:
        raise SystemExit(
            "config.json 配置不完整，缺少：" + "、".join(missing) +
            "\n请对照 README / 默认 config.json 补全后重试。"
        )


def load_comment_templates():
    with open(ROOT / "comments.json", encoding="utf-8") as f:
        return json.load(f)["templates"]


def read_csv(path):
    p = ROOT / path
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def to_int(s, default=0):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default


def match_terms(text, terms):
    """返回 text 中命中的词列表。"""
    return [t for t in terms if t and t in text]


def pick_template(content_id, templates, priority):
    """按 content_id 稳定地选模板：同一帖始终同一条，不同帖尽量错开，避免千篇一律。"""
    pool = [t for t in templates if t.get("tier") in ("any", priority)] or templates
    h = int(hashlib.md5(content_id.encode("utf-8")).hexdigest(), 16)
    return pool[h % len(pool)]["text"]


def score_lead(lead, cfg, now):
    """对单条线索打分。返回 (score, priority, matched_keywords, intent_hits) 或 None（被排除/不相关）。"""
    text = f"{lead.get('title','')} {lead.get('content_excerpt','')}"
    author = lead.get("author_name", "")

    # 1) 硬排除：招聘/加盟/培训/骗局…
    if match_terms(text, cfg["exclude_words"]):
        return None

    # 1.1) 科普/B2B/国内大件等「像物流但不是回国买家」的主题 -> 丢弃
    if match_terms(text, cfg.get("negative_topic_words", [])):
        return None

    # 1.2) 作者本身是货代/集运/物流账号（卖家/同行）-> 丢弃，避免同行互相占坑
    if match_terms(author, cfg.get("seller_author_words", [])):
        return None

    # 2) 关键词分级（取命中的最高级）
    tiers = cfg["keyword_tiers"]
    hits = {
        "high": match_terms(text, tiers["high"]),
        "mid": match_terms(text, tiers["mid"]),
        "low": match_terms(text, tiers["low"]),
    }
    if hits["high"]:
        top_tier = "high"
    elif hits["mid"]:
        top_tier = "mid"
    elif hits["low"]:
        top_tier = "low"
    else:
        return None  # 没命中任何物流词 -> 不相关，丢弃

    matched = hits["high"] + hits["mid"] + hits["low"]

    sc = cfg["scoring"]
    score = sc["tier_base"][top_tier]

    # 3) 意图信号：强意图（多少钱/报价/求渠道）权重 > 弱意图（怎么发/多久能到）
    #    —— 真正的高价值购买信号，常在评论/正文里
    strong = match_terms(text, cfg.get("intent_strong", []))
    weak = match_terms(text, cfg.get("intent_weak", []))
    intent_hits = strong + weak
    intent_score = (len(strong) * sc.get("intent_strong_each", 30)
                    + len(weak) * sc.get("intent_weak_each", 15))
    score += min(intent_score, sc["intent_cap"])

    # 4) 时效加分（越新越好）
    pub = parse_dt(lead.get("publish_time", ""))
    if pub:
        age_days = (now - pub).days
        for d_str, bonus in sorted(sc["recency_bonus"].items(), key=lambda x: int(x[0])):
            if age_days <= int(d_str):
                score += bonus
                break

    # 5) 评论区活跃度（适度活跃最好，过饱和不加）
    cc = to_int(lead.get("comments_count"))
    if 1 <= cc <= sc["engagement_active_max_comments"]:
        score += sc["engagement_active_bonus"]

    # 5.5) 负向信号：已找到渠道/已成交/勿扰 -> 降权，别在死线索上耗（也降低骚扰投诉=封控）
    if match_terms(text, cfg.get("negative_signals", [])):
        score -= sc.get("negative_signal_penalty", 40)

    # 5.6) 海外属地：发帖人在海外 = 高度疑似「海运回国」真买家（最廉价的买家判别特征）
    if match_terms(lead.get("ip_location", ""), cfg.get("overseas_regions", [])):
        score += sc.get("overseas_bonus", 0)

    # 6) 优先级分桶
    cut = sc["priority_cut"]
    if score >= cut["high"]:
        priority = "high"
    elif score >= cut["mid"]:
        priority = "mid"
    else:
        priority = "low"

    return score, priority, matched, intent_hits


def build_dedup_index(history, cooldown_days, now):
    """从历史记录建去重索引：已处理的 content_id / url，以及每个作者最近一次处理时间。"""
    seen_ids, seen_urls = set(), set()
    author_last = {}
    for row in history:
        seen_ids.add(row.get("content_id", ""))
        seen_urls.add(row.get("url", ""))
        a = row.get("author_id", "")
        t = parse_dt(row.get("processed_at", "")) or parse_dt(row.get("created_at", ""))
        if a and t and (a not in author_last or t > author_last[a]):
            author_last[a] = t
    return seen_ids, seen_urls, author_last


def main():
    cfg = load_config()
    validate_config(cfg)
    templates = load_comment_templates()
    now = datetime.now()

    leads = read_csv(cfg["paths"]["leads"])
    history = read_csv(cfg["paths"]["history"])
    cooldown = timedelta(days=cfg["dedup"]["author_cooldown_days"])
    seen_ids, seen_urls, author_last = build_dedup_index(
        history, cfg["dedup"]["author_cooldown_days"], now
    )

    stats = {"total": len(leads), "excluded": 0, "dup": 0, "cooldown": 0, "kept": 0}
    batch_ids = set()
    rows = []

    for lead in leads:
        cid = lead.get("content_id", "").strip()
        url = lead.get("url", "").strip()
        author = lead.get("author_id", "").strip()

        # 去重：历史已处理 / 本批重复
        if cid in seen_ids or url in seen_urls or cid in batch_ids:
            stats["dup"] += 1
            continue
        # 作者冷却：同一个人 N 天内不重复触达
        last = author_last.get(author)
        if last and (now - last) < cooldown:
            stats["cooldown"] += 1
            continue

        scored = score_lead(lead, cfg, now)
        if scored is None:
            stats["excluded"] += 1
            continue
        score, priority, matched, intent_hits = scored
        batch_ids.add(cid)

        rows.append({
            "id": f"{lead.get('platform','')}-{cid}",
            "platform": lead.get("platform", ""),
            "content_id": cid,
            "url": url,
            "title": lead.get("title", ""),
            "author_id": author,
            "author_name": lead.get("author_name", ""),
            "matched_keywords": "|".join(matched),
            "intent_hits": "|".join(intent_hits),
            "priority": priority,
            "score": score,
            "status": "new",
            "comment_text": pick_template(cid, templates, priority),
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "processed_at": "",
            "note": "",
        })

    # 按分数降序 —— 这就是你每天该从上往下处理的顺序
    rows.sort(key=lambda r: r["score"], reverse=True)
    stats["kept"] = len(rows)

    fields = ["id", "platform", "content_id", "url", "title", "author_id",
              "author_name", "matched_keywords", "intent_hits", "priority",
              "score", "status", "comment_text", "created_at", "processed_at", "note"]
    out = ROOT / cfg["paths"]["queue"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"读取 {stats['total']} 条 -> 排除 {stats['excluded']} | "
          f"去重 {stats['dup']} | 作者冷却 {stats['cooldown']} | 保留 {stats['kept']}")
    print(f"已写入待处理队列：{cfg['paths']['queue']}")
    if rows:
        print("\n前几条（按优先级）：")
        for r in rows[:5]:
            print(f"  [{r['priority']:>4}] {r['score']:>3}  {r['platform']:<11} "
                  f"{r['title']}  «意图:{r['intent_hits'] or '-'}»")


if __name__ == "__main__":
    main()
