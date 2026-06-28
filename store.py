#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py —— 共享的数据层（字段定义 / 配置加载 / CSV 读写 / 状态流转）

被 add_lead.py、serve.py、report.py 复用，保证它们和 score.py / publish_assist.py
对同一套字段和文件路径。只读写本地 CSV，不联网、不发布。
"""

import csv
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 队列 / 历史的统一字段
QUEUE_FIELDS = ["id", "platform", "content_id", "url", "title", "author_id",
                "author_name", "matched_keywords", "intent_hits", "priority",
                "score", "status", "comment_text", "created_at", "processed_at", "note"]

# 采集层产出的原始线索字段
LEADS_FIELDS = ["platform", "content_id", "url", "title", "author_id",
                "author_name", "content_excerpt", "publish_time",
                "likes", "comments_count", "crawl_time"]

VALID_STATUS = {"new", "opened", "posted", "skipped", "failed"}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config():
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def path_of(key):
    """从 config.paths 取相对路径并解析成绝对路径。key ∈ {leads, queue, history}"""
    return ROOT / load_config()["paths"][key]


def read_csv(path):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def append_csv(path, row, fields):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def mark_processed(item_id, status, comment_text=None, note=""):
    """把队列里某条标成已处理：写入 history.csv，并从 queue.csv 移除。

    返回被处理的行（dict）；找不到返回 None。给 CLI 和 Web 控制台共用，行为一致。
    """
    if status not in VALID_STATUS:
        raise ValueError(f"非法状态: {status}")
    queue_path = path_of("queue")
    history_path = path_of("history")
    rows = read_csv(queue_path)
    target, remaining = None, []
    for r in rows:
        if r.get("id") == item_id and target is None:
            target = r
        else:
            remaining.append(r)
    if target is None:
        return None
    target["status"] = status
    if comment_text is not None:
        target["comment_text"] = comment_text
    target["note"] = note
    target["processed_at"] = now_str()
    append_csv(history_path, target, QUEUE_FIELDS)
    write_csv(queue_path, remaining, QUEUE_FIELDS)
    return target
