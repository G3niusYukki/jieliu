#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mediacrawler_adapter.py —— 把 MediaCrawler 的采集结果归一化成本项目的 data/leads.csv

为什么用 MediaCrawler 做采集层：
  - 它同时支持抖音 + 小红书（还有快手/B站/微博等），基于 Playwright 保留登录态、
    用 JS 取签名，不用自己逆向加密；社区维护、是这块最成熟的开源采集器。
  - 我们只用它「读公开数据」（笔记/视频/评论/点赞数），不用它发布。

用法：
  1) 单独去跑 MediaCrawler（见 collectors/README.md），把结果导出成 CSV/JSON。
  2) python3 collectors/mediacrawler_adapter.py <它的导出文件> [--platform xiaohongshu|douyin]
     -> 追加/写入到 data/leads.csv，然后回到项目根目录跑 score.py。

注意：MediaCrawler 不同版本/不同平台导出的字段名不一样，下面 FIELD_MAP 按需改一下即可。
本适配器只做字段映射，不发任何请求、不发布。
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEADS = ROOT / "data" / "leads.csv"

LEADS_FIELDS = ["platform", "content_id", "url", "title", "author_id",
                "author_name", "content_excerpt", "publish_time",
                "likes", "comments_count", "crawl_time"]

# 把 MediaCrawler 的字段名映射到我们的字段。左边是我们的，右边是「候选源字段名」（按版本不同会变，命中第一个存在的）。
FIELD_MAP = {
    "content_id":      ["note_id", "aweme_id", "id", "video_id"],
    "url":             ["note_url", "video_url", "url", "share_url"],
    "title":           ["title", "desc", "content", "note_title"],
    "author_id":       ["user_id", "author_id", "uid", "sec_uid"],
    "author_name":     ["nickname", "user_name", "author", "nick_name"],
    "content_excerpt": ["desc", "content", "note_desc", "comment_text"],
    "publish_time":    ["time", "publish_time", "create_time", "last_update_time"],
    "likes":           ["liked_count", "digg_count", "like_count", "likes"],
    "comments_count":  ["comment_count", "comments_count", "comment_num"],
}


def pick(row, candidates, default=""):
    for c in candidates:
        if c in row and str(row[c]).strip():
            return str(row[c]).strip()
    return default


def normalize_time(v):
    """MediaCrawler 时间可能是秒级/毫秒级时间戳或字符串，尽量转成 ISO。"""
    if not v:
        return ""
    s = str(v).strip()
    if s.isdigit():
        ts = int(s)
        if ts > 10_000_000_000:  # 毫秒
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, OverflowError, OSError):
            return ""
    return s


def load_source(path):
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("data", [])
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="MediaCrawler 导出的 CSV 或 JSON 文件")
    ap.add_argument("--platform", default="", help="xiaohongshu / douyin（源文件没有该字段时用）")
    ap.add_argument("--mode", choices=["append", "overwrite"], default="append")
    args = ap.parse_args()

    src = load_source(args.source)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    out = []
    for row in src:
        rec = {k: pick(row, v) for k, v in FIELD_MAP.items()}
        rec["platform"] = pick(row, ["platform", "source"], args.platform)
        rec["publish_time"] = normalize_time(rec["publish_time"])
        rec["crawl_time"] = now
        if rec["content_id"]:
            out.append({k: rec.get(k, "") for k in LEADS_FIELDS})

    LEADS.parent.mkdir(parents=True, exist_ok=True)
    write_header = args.mode == "overwrite" or not LEADS.exists()
    mode = "w" if args.mode == "overwrite" else "a"
    with open(LEADS, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEADS_FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(out)

    print(f"归一化 {len(out)} 条 -> {LEADS}（mode={args.mode}）")
    print("接着回项目根目录跑：python3 score.py")


if __name__ == "__main__":
    main()
