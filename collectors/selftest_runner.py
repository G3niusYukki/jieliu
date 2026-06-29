#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest_runner.py —— 验证 run_mediacrawler.py 的「采集后」管道（不触网、不启动 MediaCrawler）。

把真实 schema 的 fixtures 放进【临时】OUT_DIR，验证：
  - 能定位 MediaCrawler 安装、用上它的 venv、正确读 keywords.txt
  - newest() 能找到最新 contents/comments
  - ingest() 把数据真正写进项目的 data/leads.csv，score.py 能生成队列

安全：用临时目录当 OUT_DIR、并备份/还原真实 data/*.csv —— 绝不碰你真实的采集缓存与线索。
跑法：python3 collectors/selftest_runner.py
"""

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "collectors"))
import run_mediacrawler as runner

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}")


def wj(p, o):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    now = datetime.now()
    ms = int((now - timedelta(days=1)).timestamp() * 1000)
    sec = int((now - timedelta(days=1)).timestamp())
    date = "2026-06-29"

    leads = ROOT / "data" / "leads.csv"
    queue = ROOT / "data" / "queue.csv"
    history = ROOT / "data" / "history.csv"
    db = ROOT / "data" / "jieliu.db"
    data_files = [leads, queue, history, db]
    # 备份真实数据，测试结束无条件还原（ingest 写真实 leads.csv，score 子进程写真实 db/queue）
    backups = {f: f.read_bytes() for f in data_files if f.exists()}

    tmp_out = tempfile.TemporaryDirectory()
    orig_out = runner.OUT_DIR
    runner.OUT_DIR = Path(tmp_out.name)        # 让 newest()/ingest() 只看临时目录，不碰真实 _mc_out
    out = runner.OUT_DIR

    try:
        wj(out / "xhs" / "json" / f"search_contents_{date}.json", [{
            "note_id": "n_x_furniture", "title": "家具海运回国", "desc": "一堆家具想运回国",
            "time": ms, "user_id": "ux", "nickname": "小鹿", "liked_count": "30",
            "comment_count": "12", "note_url": "https://www.xiaohongshu.com/explore/n_x_furniture",
            "source_keyword": "家具海运"}])
        wj(out / "xhs" / "json" / f"search_comments_{date}.json", [
            {"comment_id": "c1", "note_id": "n_x_furniture", "create_time": ms,
             "content": "大件托运多少钱 清关麻烦吗", "user_id": "ua", "nickname": "A", "like_count": "9"}])
        wj(out / "dy" / "json" / f"search_contents_{date}.json", [{
            "aweme_id": "a_d_piano", "title": "钢琴托运回国", "desc": "钢琴怎么运回国",
            "create_time": sec, "user_id": "ud", "nickname": "Tina", "liked_count": "200",
            "comment_count": "30", "aweme_url": "https://www.douyin.com/video/a_d_piano",
            "source_keyword": "钢琴托运"}])
        wj(out / "dy" / "json" / f"search_comments_{date}.json", [
            {"comment_id": "dc1", "aweme_id": "a_d_piano", "create_time": sec,
             "content": "求推荐靠谱渠道 怎么收费", "user_id": "uc", "nickname": "C", "like_count": "20"}])

        print("用例 C：runner 定位 + 配置")
        mc = runner.find_mediacrawler()
        check("找到 MediaCrawler 安装", mc is not None and (mc / "main.py").exists())
        check("用上 MediaCrawler 的 venv python", mc is not None and runner.mc_python(mc).endswith("/.venv/bin/python"))
        kw = runner.read_keywords()
        check("关键词读取（逗号拼接、无注释行）", "国际物流" in kw and "#" not in kw)

        print("\n用例 D：runner 找最新输出 -> 归一化 -> 打分")
        check("newest 找到 xhs contents", runner.newest("xhs", "contents") is not None)
        check("newest 找到 dy comments", runner.newest("dy", "comments") is not None)

        runner.ingest("xhs", "overwrite")
        runner.ingest("dy", "append")
        rows = list(csv.DictReader(open(leads, encoding="utf-8")))
        ids = {r["content_id"] for r in rows}
        check("leads.csv 写入两平台数据", {"n_x_furniture", "a_d_piano"} <= ids)
        n = next((r for r in rows if r["content_id"] == "n_x_furniture"), {})
        check("评论意图已并入正文", "多少钱" in n.get("content_excerpt", ""))

        subprocess.run([sys.executable, str(ROOT / "score.py")], check=True, cwd=str(ROOT))
        q = list(csv.DictReader(open(queue, encoding="utf-8")))
        check("score 生成非空队列", len(q) > 0)
        # 现在 ingest 默认抽评论者线索：2 条帖 + 2 条评论者，均 high
        check("帖+评论者都进队列且均 high", len(q) == 4 and all(r["priority"] == "high" for r in q))
        check("评论者线索已抽出(lead_type=commenter)", any(r.get("lead_type") == "commenter" for r in q))
        check("评论者线索带回复目标(target)", any(r.get("lead_type") == "commenter" and r.get("target") for r in q))
    finally:
        # 还原临时 OUT_DIR 与真实数据，保持你的采集缓存/线索原样
        runner.OUT_DIR = orig_out
        tmp_out.cleanup()
        for f in data_files:
            if f in backups:
                f.write_bytes(backups[f])
            else:
                f.unlink(missing_ok=True)

    print(f"\n{'='*42}\nrunner 管道自测：通过 {PASS} ｜ 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
