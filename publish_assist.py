#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_assist.py —— 人工把关的「辅助发布」流程（合规辅助版，db 真相源）

对队列里每条 new：
  1) 打开帖子链接（浏览器）
  2) 把拟好的评论草稿复制到剪贴板（你粘贴 + 按需修改 + 自己点发送）
  3) 你标记 posted / skip / failed —— 写回 db（不删行，后续可继续回填可见性/回复/成交）

刻意的设计边界：本脚本【不】自动填评论、【不】自动点发送、【不】做多账号/绕风控。
最后那一下由人来点——这既是最低成本的“反风控”，也让你能改成针对该帖的真实回复。
"""

import sys
import subprocess
import webbrowser

import store


def copy_to_clipboard(text):
    """macOS：用 pbcopy 把草稿放进剪贴板。"""
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def main():
    store.init_db()
    todo = store.get_queue("new")
    if not todo:
        print("队列里没有 new 状态的线索。先跑 python3 score.py 生成队列。")
        return

    print(f"共 {len(todo)} 条待处理（按优先级从高到低）。")
    print("操作：[p]已发布  [s]跳过  [f]失败  [e]改评论  [o]重开链接  [q]退出\n")

    for i, r in enumerate(todo, 1):
        target = r.get("target") or r.get("title") or "(无标题)"
        print("=" * 64)
        kind = "评论者" if r.get("lead_type") == "commenter" else "帖子"
        print(f"[{i}/{len(todo)}]  优先级 {r['priority']} | 分数 {r['score']} | {r['platform']} | {kind}")
        print(f"目标：{target}")
        print(f"命中：{r.get('matched_keywords','')}   意图：{r.get('intent_hits') or '-'}"
              + (f"   属地：{r['ip_location']}" if r.get('ip_location') else ""))
        print(f"链接：{r['url']}")
        print(f"草稿：{r['comment_text']}")
        print("  ↑ 记得把 {hook} 换成针对该帖/该评论的具体一句，并酌情再改两句。")

        webbrowser.open(r["url"])
        if copy_to_clipboard(r["comment_text"]):
            print("（草稿已复制到剪贴板，可直接粘贴后修改再发送）")
        else:
            print("（剪贴板复制失败，请手动复制上面的草稿）")

        comment_text = r["comment_text"]
        while True:
            choice = input("> ").strip().lower()
            if choice in ("p", "s", "f"):
                status = {"p": "posted", "s": "skipped", "f": "failed"}[choice]
                note = input("失败原因/备注（可留空回车）：").strip() if choice == "f" else input("备注（可留空回车）：").strip()
                store.mark_processed(r["id"], status, comment_text=comment_text, note=note)
                break
            elif choice == "e":
                new_text = input("输入新的评论文本：").strip()
                if new_text:
                    comment_text = new_text
                    store.update_lead(r["id"], comment_text=new_text)
                    copy_to_clipboard(new_text)
                    print("（已更新并复制到剪贴板）")
            elif choice == "o":
                webbrowser.open(r["url"])
            elif choice == "q":
                print("已退出，进度已保存（db）。")
                return
            else:
                print("无效输入。p=已发布 s=跳过 f=失败 e=改评论 o=重开链接 q=退出")

    print("\n全部处理完成。状态已写入 db（python3 report.py 看转化漏斗）。")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n中断退出。")
        sys.exit(0)
