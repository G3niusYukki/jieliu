#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest_mc.py —— 验证「MediaCrawler 真实输出 -> 归一化 -> 打分」整条采集链路。

用 MediaCrawler 真实字段（note_id/aweme_id/note_url/desc/time/create_time/liked_count…）
造 fixtures，跑 mediacrawler_adapter，断言：
  - 字段映射正确（content_id / url / platform / 时间归一化）
  - **热评里的意图词被合并进正文**（核心增强）
  - 合并后 score.py 能把"意图只在评论里"的帖子排成高优先级

全程用临时文件，不动你的真实 data/*.csv。跑法：python3 collectors/selftest_mc.py
"""

import csv
import sys
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "collectors"))

import mediacrawler_adapter as mc
import score

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}")


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    now = datetime.now()
    ms_1d = int((now - timedelta(days=1)).timestamp() * 1000)   # xhs: 毫秒
    sec_1d = int((now - timedelta(days=1)).timestamp())          # dy: 秒

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        # ---- 真实 MediaCrawler schema 的 fixtures ----
        # xhs 内容：正文里【没有】意图词，意图只在评论里
        xhs_contents = [{
            "note_id": "n_intent_in_comment", "type": "normal",
            "title": "搬家回国求助", "desc": "东西好多不知道怎么办",
            "time": ms_1d, "last_update_time": ms_1d,
            "user_id": "u_x1", "nickname": "小鹿",
            "liked_count": "30", "comment_count": "12",
            "note_url": "https://www.xiaohongshu.com/explore/n_intent_in_comment?xsec_token=ABC",
            "source_keyword": "搬家回国",
        }, {
            "note_id": "n_recruit", "type": "normal",
            "title": "招聘国际物流业务员", "desc": "加盟代理培训包教",
            "time": ms_1d, "user_id": "u_x2", "nickname": "HR",
            "liked_count": "1", "comment_count": "0",
            "note_url": "https://www.xiaohongshu.com/explore/n_recruit",
            "source_keyword": "国际物流",
        }]
        xhs_comments = [
            {"comment_id": "c1", "create_time": ms_1d, "note_id": "n_intent_in_comment",
             "content": "请问大件托运大概多少钱啊", "user_id": "ua", "nickname": "A", "like_count": "9"},
            {"comment_id": "c2", "create_time": ms_1d, "note_id": "n_intent_in_comment",
             "content": "清关麻烦吗 求推荐靠谱渠道", "user_id": "ub", "nickname": "B", "like_count": "5"},
        ]
        # dy 内容：真实字段 aweme_id/aweme_url/create_time(秒)
        dy_contents = [{
            "aweme_id": "a_furniture", "aweme_type": "video",
            "title": "家具海运回国vlog", "desc": "中国到澳洲海运的家具到了",
            "create_time": sec_1d, "user_id": "u_d1", "nickname": "Tina",
            "liked_count": "540", "comment_count": "76",
            "aweme_url": "https://www.douyin.com/video/a_furniture",
            "source_keyword": "家具海运",
        }]
        dy_comments = [
            {"comment_id": "dc1", "create_time": sec_1d, "aweme_id": "a_furniture",
             "content": "门到门怎么收费", "user_id": "uc", "nickname": "C", "like_count": "20"},
        ]

        xc = tmp / "xhs" / "json" / "search_contents_2026-06-29.json"
        xcm = tmp / "xhs" / "json" / "search_comments_2026-06-29.json"
        dc = tmp / "dy" / "json" / "search_contents_2026-06-29.json"
        dcm = tmp / "dy" / "json" / "search_comments_2026-06-29.json"
        write_json(xc, xhs_contents); write_json(xcm, xhs_comments)
        write_json(dc, dy_contents); write_json(dcm, dy_comments)

        # ---- 跑 adapter（输出重定向到临时 leads.csv）----
        leads = tmp / "leads.csv"
        mc.LEADS = leads
        def run_adapter(src, comments, platform, mode):
            sys.argv = ["mediacrawler_adapter.py", str(src),
                        "--comments", str(comments), "--platform", platform, "--mode", mode]
            mc.main()
        run_adapter(xc, xcm, "xhs", "overwrite")
        run_adapter(dc, dcm, "dy", "append")

        rows = list(csv.DictReader(open(leads, encoding="utf-8")))
        by_id = {r["content_id"]: r for r in rows}

        print("用例 A：字段映射 + 评论合并")
        check("内容总数 3（xhs2 + dy1）", len(rows) == 3)
        n1 = by_id.get("n_intent_in_comment", {})
        check("xhs content_id 取自 note_id", "n_intent_in_comment" in by_id)
        check("xhs platform 归一为 xiaohongshu", n1.get("platform") == "xiaohongshu")
        check("xhs url 取自 note_url", n1.get("url", "").startswith("https://www.xiaohongshu.com/explore/"))
        check("xhs time(毫秒) 归一成 ISO", n1.get("publish_time", "").startswith("20"))
        check("★ 热评『多少钱』被合并进正文", "多少钱" in n1.get("content_excerpt", ""))
        check("★ 热评『清关麻烦』被合并进正文", "清关麻烦" in n1.get("content_excerpt", ""))
        a1 = by_id.get("a_furniture", {})
        check("dy content_id 取自 aweme_id", "a_furniture" in by_id)
        check("dy platform 归一为 douyin", a1.get("platform") == "douyin")
        check("dy url 取自 aweme_url", a1.get("url") == "https://www.douyin.com/video/a_furniture")
        check("dy 热评『门到门怎么收费』被合并", "门到门" in a1.get("content_excerpt", ""))

        # ---- 跑 score：意图只在评论里的帖子应被排成高优先级 ----
        print("\n用例 B：合并后打分 / 排序 / 排除")
        cfg = score.load_config()
        cfg["paths"] = {"leads": str(leads), "queue": str(tmp / "queue.csv"),
                        "history": str(tmp / "history.csv")}
        orig = score.load_config
        score.load_config = lambda: cfg
        try:
            score.main()
        finally:
            score.load_config = orig
        q = list(csv.DictReader(open(tmp / "queue.csv", encoding="utf-8")))
        qids = [r["content_id"] for r in q]
        qtop = q[0] if q else {}
        check("招聘帖被排除", "n_recruit" not in qids)
        check("评论意图帖进入队列", "n_intent_in_comment" in qids)
        n1q = next((r for r in q if r["content_id"] == "n_intent_in_comment"), {})
        check("★ 评论里的意图被算进分（intent_hits 非空）", bool(n1q.get("intent_hits")))
        check("★ 该帖优先级为 high", n1q.get("priority") == "high")

    print(f"\n{'='*42}\nMC 采集链路自测：通过 {PASS} ｜ 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
