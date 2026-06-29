#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mediacrawler_adapter.py —— 把 MediaCrawler 的采集结果归一化成本项目的 data/leads.csv

字段映射已对齐 MediaCrawler 真实输出（见 vendor/MediaCrawler/store/{xhs,douyin}/__init__.py）：

  小红书(xhs) 内容: note_id / note_url / title / desc / user_id / nickname / time / liked_count / comment_count
  抖音(dy)   内容: aweme_id / aweme_url / title / desc / user_id / nickname / create_time / liked_count / comment_count
  评论(两端):     {note_id|aweme_id} / content / like_count

关键增强：把每条内容下的**热评**摘进 content_excerpt —— 高价值意图（怎么寄/多少钱/清关麻烦吗）
经常只出现在评论里，喂给 score.py 才能打准分。

用法：
  python3 collectors/mediacrawler_adapter.py <contents文件> [--comments <comments文件>] \
          [--platform xhs|dy] [--mode append|overwrite] [--top-comments 8]

只做字段映射 + 本地文件读写，不发任何请求、不发布。
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEADS = ROOT / "data" / "leads.csv"

LEADS_FIELDS = ["platform", "content_id", "url", "title", "author_id",
                "author_name", "content_excerpt", "ip_location", "publish_time",
                "likes", "comments_count", "crawl_time",
                "lead_type", "parent_content_id", "comment_id", "target"]

# 落盘前脱敏：只抹「真实联系方式」(手机号/微信号/QQ/邮箱)，保留"电话/微信"等意图上下文词。
# 目的：PIPL/刑事红线——成规模存储敏感个人信息会把入罪门槛从一般信息5000条降到敏感信息50条。
_CONTACT_PATTERNS = [
    (re.compile(r"1[3-9]\d{9}"), "[手机]"),                                  # 大陆手机号
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[邮箱]"),
    (re.compile(r"(微信|薇信|威信|vx|v信|wx|wechat|qq|扣扣|加\s*[vV])"
                r"\s*[:：=是+\-]*\s*([A-Za-z0-9_]{5,20})", re.I), r"\1[已隐藏]"),  # 微信/QQ 号
    (re.compile(r"(?<!\d)\d{7,}(?!\d)"), "[号码]"),                          # QQ/座机等长数字串
]


def scrub(text):
    """抹掉文本里的真实联系方式（手机/微信/QQ/邮箱），保留其余语义。"""
    if not text:
        return text
    s = str(text)
    for pat, repl in _CONTACT_PATTERNS:
        s = pat.sub(repl, s)
    return s

# MediaCrawler 平台代号 -> 本项目平台名
PLATFORM_CODE = {"xhs": "xiaohongshu", "dy": "douyin",
                 "xiaohongshu": "xiaohongshu", "douyin": "douyin"}

# 本项目字段 <- MediaCrawler 候选源字段（命中第一个非空的）
CONTENT_MAP = {
    "content_id":      ["note_id", "aweme_id"],
    "url":             ["note_url", "aweme_url"],
    "title":           ["title", "desc"],
    "author_id":       ["user_id"],
    "author_name":     ["nickname"],
    "content_excerpt": ["desc", "title"],
    "ip_location":     ["ip_location"],
    "publish_time":    ["time", "create_time", "last_update_time"],
    "likes":           ["liked_count"],
    "comments_count":  ["comment_count"],
}
COMMENT_KEY = ["note_id", "aweme_id"]      # 评论挂到哪条内容
COMMENT_TEXT = ["content"]
COMMENT_LIKE = ["like_count"]
COMMENT_ID = ["comment_id"]
COMMENT_USER = ["user_id"]
COMMENT_NICK = ["nickname"]
COMMENT_IP = ["ip_location"]
COMMENT_TIME = ["create_time", "time"]


def load_intent_words():
    """读 config.json 的意图词（强+弱），用于把『在评论区问价』的人识别成可触达线索。"""
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return (cfg.get("intent_strong", []) + cfg.get("intent_weak", [])) or cfg.get("intent_signals", [])
    except Exception:
        return []


def emit_commenter_leads(comments, note_index, platform, now):
    """把命中意图词(在评论区问价/问怎么寄)的评论者，抽成可【直接回复该评论者】的独立线索。"""
    intent = load_intent_words()
    out = []
    for c in comments:
        note_id = pick(c, COMMENT_KEY)
        text = scrub(pick(c, COMMENT_TEXT))
        if not (note_id and text):
            continue
        if not any(w and w in text for w in intent):     # 只抽有购买意图的评论者
            continue
        note = note_index.get(note_id, {})
        note_title = note.get("title", "")
        nick = pick(c, COMMENT_NICK) or "网友"
        cmt_id = pick(c, COMMENT_ID) or f"{note_id}_{pick(c, COMMENT_USER)}"
        out.append({
            "platform": platform,
            "content_id": cmt_id,                          # 评论 id 作主键，与帖子线索独立去重
            "url": note.get("url", ""),                    # 打开这条帖子去定位/回复该评论
            "title": f"评论@{nick}：{text[:24]}",
            "author_id": pick(c, COMMENT_USER),
            "author_name": nick,
            # 折进原帖标题，让评论者线索继承物流品类上下文(否则纯意图无品类词会被打分丢弃)
            "content_excerpt": f"{text}｜原帖:{note_title}",
            "ip_location": pick(c, COMMENT_IP),
            "publish_time": normalize_time(pick(c, COMMENT_TIME)),
            "likes": pick(c, COMMENT_LIKE),
            "comments_count": "",
            "crawl_time": now,
            "lead_type": "commenter",
            "parent_content_id": note_id,
            "comment_id": cmt_id,
            "target": f"回复 @{nick}：{text}",
        })
    return out


def pick(row, candidates, default=""):
    for c in candidates:
        if c in row and str(row[c]).strip() and str(row[c]).strip().lower() != "none":
            return str(row[c]).strip()
    return default


def to_int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def normalize_time(v):
    """MediaCrawler 时间可能是秒级/毫秒级时间戳或字符串，尽量转 ISO。"""
    if not v:
        return ""
    s = str(v).strip()
    if s.replace(".", "").isdigit():
        ts = int(float(s))
        if ts > 10_000_000_000:        # 毫秒
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, OverflowError, OSError):
            return ""
    return s


def infer_platform(path, override):
    if override:
        return PLATFORM_CODE.get(override, override)
    p = str(path).replace("\\", "/")
    if "/xhs/" in p:
        return "xiaohongshu"
    if "/dy/" in p or "/douyin/" in p:
        return "douyin"
    return ""


def load_records(path):
    """读 MediaCrawler 的 json(数组) / jsonl / csv(utf-8-sig 带 BOM)。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("data", [data])
    if suffix == ".jsonl":
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out
    # csv —— MediaCrawler 用 utf-8-sig 写，必须用 utf-8-sig 读以去掉 BOM
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_comment_index(comments, top_n):
    """按 内容ID 聚合评论文本，取点赞最高的前 N 条。"""
    groups = defaultdict(list)
    for c in comments:
        cid = pick(c, COMMENT_KEY)
        text = pick(c, COMMENT_TEXT)
        if cid and text:
            groups[cid].append((to_int(pick(c, COMMENT_LIKE)), scrub(text)))
    index = {}
    for cid, items in groups.items():
        items.sort(key=lambda x: x[0], reverse=True)
        index[cid] = [t for _, t in items[:top_n]]
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="MediaCrawler 导出的 contents 文件（json/jsonl/csv）")
    ap.add_argument("--comments", default="", help="对应的 comments 文件（强烈建议带上）")
    ap.add_argument("--platform", default="", help="xhs / dy（源文件/路径推断不出时用）")
    ap.add_argument("--mode", choices=["append", "overwrite"], default="append")
    ap.add_argument("--top-comments", type=int, default=8)
    ap.add_argument("--emit-commenter-leads", action="store_true",
                    help="把评论区里『问价/问怎么寄』的评论者抽成可直接回复的独立线索(lead_type=commenter)")
    args = ap.parse_args()

    platform = infer_platform(args.source, args.platform)
    contents = load_records(args.source)
    comments_raw = load_records(args.comments) if args.comments else []
    cindex = build_comment_index(comments_raw, args.top_comments) if comments_raw else {}

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    out, merged_comments = [], 0
    note_index = {}
    for row in contents:
        rec = {k: pick(row, v) for k, v in CONTENT_MAP.items()}
        if not rec["content_id"]:
            continue
        rec["lead_type"] = "note"
        rec["platform"] = platform or PLATFORM_CODE.get(pick(row, ["platform", "source"]), "")
        rec["publish_time"] = normalize_time(rec["publish_time"])
        # 正文/标题落盘前脱敏（评论文本已在 build_comment_index 里脱敏）
        rec["title"] = scrub(rec["title"])
        rec["content_excerpt"] = scrub(rec["content_excerpt"])
        # 把热评摘进正文，让意图信号进入打分
        cmts = cindex.get(rec["content_id"], [])
        if cmts:
            rec["content_excerpt"] = (rec["content_excerpt"] + " ｜评论: " +
                                      " / ".join(cmts)).strip()
            merged_comments += len(cmts)
        rec["crawl_time"] = now
        note_index[rec["content_id"]] = {"url": rec.get("url", ""), "title": rec.get("title", "")}
        out.append({k: rec.get(k, "") for k in LEADS_FIELDS})

    note_n = len(out)
    commenter_n = 0
    if args.emit_commenter_leads and comments_raw:
        cleads = emit_commenter_leads(comments_raw, note_index, platform, now)
        out.extend({k: r.get(k, "") for k in LEADS_FIELDS} for r in cleads)
        commenter_n = len(cleads)

    LEADS.parent.mkdir(parents=True, exist_ok=True)
    write_header = args.mode == "overwrite" or not LEADS.exists()
    with open(LEADS, "w" if args.mode == "overwrite" else "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEADS_FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(out)

    print(f"归一化 {note_n} 条内容（平台={platform or '未知'}），合并热评 {merged_comments} 条"
          + (f"，抽出评论者线索 {commenter_n} 条" if args.emit_commenter_leads else "")
          + f" -> {LEADS}（mode={args.mode}）")
    print("接着回项目根目录跑：python3 score.py")


if __name__ == "__main__":
    main()
