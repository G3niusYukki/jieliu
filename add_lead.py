#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_lead.py —— 手动录入线索（不接采集器也能当天起步）

两种用法：
  1) 交互式：python3 add_lead.py
     按提示一条条填（粘贴链接最省事，其它能填则填）。
  2) 命令行：python3 add_lead.py --platform xiaohongshu --url <链接> \
              --title "标题" --excerpt "正文/热评摘录" [--author 作者]

提示：高价值意图（怎么寄/多少钱/清关麻烦吗）经常在评论区，
把热评也摘进 --excerpt，score.py 打分会准很多。
"""

import argparse
import hashlib
import re
from urllib.parse import urlparse

from store import LEADS_FIELDS, append_csv, now_str, path_of


def guess_platform(url):
    host = (urlparse(url).hostname or "").lower()
    if "xiaohongshu" in host or "xhslink" in host:
        return "xiaohongshu"
    if "douyin" in host or "iesdouyin" in host:
        return "douyin"
    return ""


def guess_content_id(url):
    """从链接里抽一个 id；抽不到就用链接的哈希兜底，保证去重有稳定主键。"""
    m = re.search(r"/(?:explore|discovery/item|video)/([0-9a-zA-Z_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{8,})", url)
    if m:
        return m.group(1)
    return "h" + hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def build_record(platform, url, title, excerpt, author, comments):
    return {
        "platform": platform or guess_platform(url),
        "content_id": guess_content_id(url),
        "url": url,
        "title": title,
        "author_id": author,
        "author_name": author,
        "content_excerpt": excerpt,
        "publish_time": "",
        "likes": "",
        "comments_count": comments,
        "crawl_time": now_str().replace(" ", "T"),
    }


def interactive():
    print("手动录入线索（直接回车跳过非必填项，输入 q 退出）。\n")
    n = 0
    while True:
        url = input("链接 url（必填）: ").strip()
        if url.lower() == "q" or not url:
            break
        title = input("标题: ").strip()
        excerpt = input("正文/热评摘录（含意图词更好）: ").strip()
        author = input("作者（可留空）: ").strip()
        comments = input("评论数（可留空）: ").strip()
        rec = build_record("", url, title, excerpt, author, comments)
        append_csv(path_of("leads"), rec, LEADS_FIELDS)
        n += 1
        print(f"  ✓ 已加入 [{rec['platform'] or '未知平台'}] {rec['content_id']}\n")
    print(f"共录入 {n} 条 -> {path_of('leads')}\n接着跑：python3 score.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--excerpt", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--comments", default="")
    args = ap.parse_args()

    if not args.url:
        interactive()
        return
    rec = build_record(args.platform, args.url, args.title,
                       args.excerpt, args.author, args.comments)
    append_csv(path_of("leads"), rec, LEADS_FIELDS)
    print(f"已加入 [{rec['platform'] or '未知平台'}] {rec['content_id']} -> {path_of('leads')}")


if __name__ == "__main__":
    main()
