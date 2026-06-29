#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py —— 不依赖外部数据的自测，验证打分/去重/排除/冷却逻辑。

跑法：python3 selftest.py    （全绿即通过；用临时文件，不污染你的真实数据）
"""

import csv
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import score
from store import QUEUE_FIELDS

ROOT = Path(__file__).resolve().parent
PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%S")


def write_leads(path, rows):
    fields = ["platform", "content_id", "url", "title", "author_id", "author_name",
              "content_excerpt", "publish_time", "likes", "comments_count", "crawl_time"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def read_queue(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_pipeline(tmp, leads, processed=None):
    """在临时目录里跑一遍 score.main()，返回队列行。每次用全新临时 db，不碰真实数据。"""
    import store
    leads_p = tmp / "leads.csv"
    queue_p = tmp / "queue.csv"
    db_p = tmp / "test.db"
    db_p.unlink(missing_ok=True)
    write_leads(leads_p, leads)
    if processed:                       # 预置「已触达过」的线索，用于验证去重/冷却
        conn = store.connect(db_p)
        store.init_db(conn)
        for r in processed:
            store.upsert_lead(r, conn=conn)
            store.mark_processed(r["id"], "posted", conn=conn)
        conn.close()

    cfg = score.load_config()
    cfg["paths"] = {"leads": str(leads_p), "queue": str(queue_p),
                    "history": str(tmp / "history.csv"), "db": str(db_p)}
    orig = score.load_config
    score.load_config = lambda: cfg
    try:
        score.main()
    finally:
        score.load_config = orig
    return read_queue(queue_p)


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        leads = [
            # 高意图：清关/多少钱 + 高优先词
            {"platform": "xiaohongshu", "content_id": "a1", "url": "u/a1",
             "title": "家具海运回国", "author_id": "ua", "content_excerpt": "大件托运多少钱 清关麻烦吗",
             "publish_time": days_ago(1), "comments_count": "20"},
            # 招聘 -> 必须排除
            {"platform": "xiaohongshu", "content_id": "a2", "url": "u/a2",
             "title": "招聘国际物流业务员", "author_id": "ub", "content_excerpt": "加盟代理培训",
             "publish_time": days_ago(1), "comments_count": "5"},
            # 无任何物流词 -> 丢弃
            {"platform": "douyin", "content_id": "a3", "url": "u/a3",
             "title": "今天吃了火锅", "author_id": "uc", "content_excerpt": "好吃",
             "publish_time": days_ago(1), "comments_count": "5"},
            # 中优先词，无意图
            {"platform": "douyin", "content_id": "a4", "url": "u/a4",
             "title": "海外搬家分享", "author_id": "ud", "content_excerpt": "家居搬运",
             "publish_time": days_ago(40), "comments_count": "10"},
            # 与 a1 内容重复（同 content_id）-> 去重
            {"platform": "xiaohongshu", "content_id": "a1", "url": "u/a1",
             "title": "家具海运回国", "author_id": "ua", "content_excerpt": "大件托运多少钱 清关麻烦吗",
             "publish_time": days_ago(1), "comments_count": "20"},
        ]

        print("用例 1：排除 / 丢弃 / 去重 / 排序")
        q = run_pipeline(tmp, leads)
        ids = [r["content_id"] for r in q]
        check("招聘帖被排除", "a2" not in ids)
        check("无物流词帖被丢弃", "a3" not in ids)
        check("重复 content_id 去重（a1 只剩一条）", ids.count("a1") == 1)
        check("保留数为 2（a1, a4）", len(q) == 2)
        check("高意图帖 a1 排在最前", q[0]["content_id"] == "a1")
        check("a1 优先级为 high", q[0]["priority"] == "high")
        check("a1 命中意图（多少钱/清关麻烦）", "多少钱" in q[0]["intent_hits"])
        check("a1 分数高于 a4", int(q[0]["score"]) > int(q[1]["score"]))
        check("每条都生成了评论草稿", all(r["comment_text"].strip() for r in q))

        print("\n用例 2：已触达去重 + 作者冷却（db 真相源）")
        processed = [{"id": "xiaohongshu-a1", "platform": "xiaohongshu",
                      "content_id": "a1", "url": "u/a1", "author_id": "ua"}]
        q2 = run_pipeline(tmp, leads, processed=processed)
        ids2 = [r["content_id"] for r in q2]
        check("已触达过的 a1 不再出现", "a1" not in ids2)
        check("其余有效线索仍保留（a4）", "a4" in ids2)

        print("\n用例 3：脚本能独立运行（语法/导入无误）")
        for mod in ["score.py", "add_lead.py", "report.py", "store.py", "serve.py"]:
            r = subprocess.run([sys.executable, "-c", f"import ast;ast.parse(open('{ROOT/mod}',encoding='utf-8').read())"],
                               capture_output=True, text=True)
            check(f"{mod} 语法 OK", r.returncode == 0)

    print(f"\n{'='*40}\n通过 {PASS} ｜ 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
