#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_assist.py —— 人工把关的「辅助发布」流程（合规辅助版）

对队列里每条 new：
  1) 打开帖子链接（浏览器）
  2) 把拟好的评论草稿复制到剪贴板（你粘贴 + 按需修改 + 自己点发送）
  3) 你标记 posted / skip / failed，写回 history.csv

刻意的设计边界：本脚本【不】自动填评论、【不】自动点发送、【不】做多账号/绕风控。
最后那一下由人来点——这既是最低成本的“反风控”，也让你能改成针对该帖的真实回复。
"""

import csv
import sys
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

QUEUE = ROOT / "data" / "queue.csv"
HISTORY = ROOT / "data" / "history.csv"

FIELDS = ["id", "platform", "content_id", "url", "title", "author_id",
          "author_name", "matched_keywords", "intent_hits", "priority",
          "score", "status", "comment_text", "created_at", "processed_at", "note"]


def copy_to_clipboard(text):
    """macOS：用 pbcopy 把草稿放进剪贴板。"""
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def read_rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def append_history(row):
    new = not HISTORY.exists()
    with open(HISTORY, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def main():
    rows = read_rows(QUEUE)
    todo = [r for r in rows if r.get("status") == "new"]
    if not todo:
        print("队列里没有 new 状态的线索。先跑 score.py 生成 data/queue.csv。")
        return
    todo.sort(key=lambda r: int(r.get("score", 0)), reverse=True)

    print(f"共 {len(todo)} 条待处理（按优先级从高到低）。")
    print("操作：[p]已发布  [s]跳过  [f]失败  [e]改评论  [o]重开链接  [q]退出\n")

    handled_ids = set()
    for i, r in enumerate(todo, 1):
        print("=" * 64)
        print(f"[{i}/{len(todo)}]  优先级 {r['priority']} | 分数 {r['score']} | {r['platform']}")
        print(f"标题：{r['title']}")
        print(f"命中：{r['matched_keywords']}   意图：{r['intent_hits'] or '-'}")
        print(f"链接：{r['url']}")
        print(f"草稿：{r['comment_text']}")
        print("  ↑ 记得把 {hook} 换成针对该帖的具体一句，并酌情再改两句。")

        webbrowser.open(r["url"])
        if copy_to_clipboard(r["comment_text"]):
            print("（草稿已复制到剪贴板，可直接粘贴后修改再发送）")
        else:
            print("（剪贴板复制失败，请手动复制上面的草稿）")

        while True:
            choice = input("> ").strip().lower()
            if choice in ("p", "s", "f"):
                r["status"] = {"p": "posted", "s": "skipped", "f": "failed"}[choice]
                r["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                note = input("备注（可留空回车）：").strip()
                if note:
                    r["note"] = note
                append_history(r)
                handled_ids.add(r["id"])
                break
            elif choice == "e":
                new_text = input("输入新的评论文本：").strip()
                if new_text:
                    r["comment_text"] = new_text
                    copy_to_clipboard(new_text)
                    print("（已更新并复制到剪贴板）")
            elif choice == "o":
                webbrowser.open(r["url"])
            elif choice == "q":
                print("已退出，进度已保存。")
                _flush(rows, handled_ids)
                return
            else:
                print("无效输入。p=已发布 s=跳过 f=失败 e=改评论 o=重开链接 q=退出")

    _flush(rows, handled_ids)
    print("\n全部处理完成。已处理的写入 history.csv，队列已更新。")


def _flush(all_rows, handled_ids):
    """已处理的从 queue 移除（已进 history），剩下的留在 queue.csv。"""
    remaining = [r for r in all_rows if r["id"] not in handled_ids]
    write_rows(QUEUE, remaining)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n中断退出。")
        sys.exit(0)
