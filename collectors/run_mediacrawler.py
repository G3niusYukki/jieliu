#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_mediacrawler.py —— 一条命令：跑 MediaCrawler 采集 -> 自动灌进 data/leads.csv

做的事：
  1) 定位 MediaCrawler 安装（环境变量 MEDIACRAWLER_HOME / vendor/MediaCrawler / ../MediaCrawler）
  2) 用 keywords.txt 作为搜索词，按平台跑 search 模式（json 输出 + 抓评论）
  3) 找到最新的 contents / comments 输出，调 mediacrawler_adapter.py 归一化进 leads.csv

唯一需要你手动的一步：MediaCrawler 起来后用**你自己的手机扫码登录你自己的账号**
（LOGIN_TYPE=qrcode，非无头）。本脚本不碰你的账号密码、不自动登录、不发布。

用法：
  python3 collectors/run_mediacrawler.py [--platform xhs|dy|all] [--max 15] [--mode overwrite|append]
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "collectors" / "_mc_out"          # MediaCrawler 输出目录（已 gitignore）
ADAPTER = ROOT / "collectors" / "mediacrawler_adapter.py"

PLATFORM_DIR = {"xhs": "xhs", "dy": "dy"}           # 输出子目录就是平台代号


def find_mediacrawler():
    candidates = [
        os.environ.get("MEDIACRAWLER_HOME", ""),
        ROOT / "vendor" / "MediaCrawler",
        ROOT.parent / "MediaCrawler",
    ]
    for c in candidates:
        if c and (Path(c) / "main.py").exists():
            return Path(c)
    return None


def mc_python(mc_home):
    """优先用 MediaCrawler 自己的 venv，其次系统 python3。"""
    for p in [mc_home / ".venv" / "bin" / "python", mc_home / "venv" / "bin" / "python"]:
        if p.exists():
            return str(p)
    return sys.executable


def read_keywords():
    kw = []
    f = ROOT / "keywords.txt"
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            kw.append(line)
    return ",".join(kw)


def newest(platform, item_type, since=None):
    """找最新的 search_{contents|comments}_*.json（兼容 jsonl/csv）。

    since 给定时只接受本次运行之后（mtime>=since）产生的文件——避免采集失败/被中断时，
    把上一天的旧文件当成本次新数据回灌（旧帖会被盖上今天的 crawl_time 冲到队首）。
    """
    base = OUT_DIR / PLATFORM_DIR[platform]
    hits = []
    for ext in ("json", "jsonl", "csv"):
        hits += list((base / ext).glob(f"*_{item_type}_*.{ext}")) if (base / ext).exists() else []
    if since is not None:
        hits = [p for p in hits if p.stat().st_mtime >= since]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def count_records(path):
    """数一下产出文件里有多少条，用于判断『无结果』。读不出返回 -1（让 ingest 仍尝试）。"""
    try:
        suffix = path.suffix.lower()
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else len(data.get("data", []) or [data])
        if suffix == ".jsonl":
            return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
        with open(path, newline="", encoding="utf-8-sig") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return -1


def run_platform(mc_home, py, platform, keywords, max_notes, get_comment=True, max_comments=20):
    print(f"\n{'='*60}\n▶ 采集 {platform}  （扫码登录你自己的账号；抓公开数据）\n{'='*60}")
    cmd = [py, "main.py",
           "--platform", platform,
           "--lt", "qrcode",
           "--type", "search",
           "--keywords", keywords,
           "--get_comment", "true" if get_comment else "false",
           "--save_data_option", "json",
           "--save_data_path", str(OUT_DIR),
           "--crawler_max_notes_count", str(max_notes),
           "--headless", "false"]
    # 评论是请求量最大、最易触发限流的环节：可关(get_comment=false)做轻量采集，或限 Top-N 降风控
    if get_comment:
        cmd += ["--max_comments_count_singlenotes", str(max_comments)]
    # 继承 stdio：扫码登录是交互式的，必须让你看到二维码/提示
    r = subprocess.run(cmd, cwd=str(mc_home))
    if r.returncode != 0:
        print(f"⚠ MediaCrawler 退出码 {r.returncode}（可能是你中断了登录）。继续尝试读取已产出的数据。")
    return r.returncode


def ingest(platform, mode, since=None):
    """归一化本次产出进 leads.csv。返回归一化的记录数；0 表示没有本次新产出（此时绝不 overwrite，保留上一版）。"""
    contents = newest(platform, "contents", since=since)
    comments = newest(platform, "comments", since=since)
    if not contents:
        print(f"✗ 没找到 {platform} 本次的 contents 输出（被风控/中断/无结果）—— 保留上一版 leads.csv，不覆盖。")
        return 0
    n = count_records(contents)
    if n == 0:
        print(f"✗ {platform} 本次采集 0 条 —— 保留上一版 leads.csv，不覆盖。")
        return 0
    args = [sys.executable, str(ADAPTER), str(contents), "--platform", platform, "--mode", mode]
    if comments:
        args += ["--comments", str(comments), "--emit-commenter-leads"]
    print(f"→ 归一化 {platform}：{contents.name}" + (f" + {comments.name}" if comments else " （无评论文件）"))
    subprocess.run(args, check=True)
    return n if n > 0 else 1


def log_collect(platform, keywords, account_label, rc, records, verdict):
    """把每次采集结果追加进 data/collect_log.csv，供复盘与风控预警。"""
    log = ROOT / "data" / "collect_log.csv"
    log.parent.mkdir(parents=True, exist_ok=True)
    new = not log.exists()
    fields = ["time", "platform", "keywords_count", "account_label", "rc", "records", "verdict"]
    with open(log, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "platform": platform,
            "keywords_count": len([k for k in keywords.split(",") if k]),
            "account_label": account_label,
            "rc": rc, "records": records, "verdict": verdict,
        })


def verdict_of(rc, records):
    """三态判定：把『被风控』和『无结果』『正常』区分开，别再静默吞掉。"""
    if records > 0:
        return f"✓ 正常（{records} 条）"
    if rc != 0:
        return "⚠ 采集失败/疑似风控或登录中断 —— 该账号可能已被限，请检查后再用"
    return "△ 无结果 —— 关键词可能被降权，换词或稍后再试"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["xhs", "dy", "all"], default="all")
    ap.add_argument("--max", type=int, default=15, help="每平台最多抓多少条")
    ap.add_argument("--mode", choices=["overwrite", "append"], default="overwrite",
                    help="overwrite=当天重抓覆盖 leads.csv（默认）；append=追加")
    ap.add_argument("--account-label", default="",
                    help="本次采集所用账号的标签，仅记进 collect_log 便于审计『采集号/触达号』隔离")
    ap.add_argument("--get-comment", choices=["true", "false"], default="true",
                    help="是否抓评论(默认 true)。评论请求量最大、最易触发限流；轻量低风险采集可设 false")
    ap.add_argument("--max-comments", type=int, default=20,
                    help="每帖最多抓多少条一级评论(默认 20)；调小降请求量与封控风险")
    args = ap.parse_args()

    mc_home = find_mediacrawler()
    if not mc_home:
        print("✗ 没找到 MediaCrawler。请先跑 ./collectors/setup_mediacrawler.sh，"
              "或设环境变量 MEDIACRAWLER_HOME 指向它。详见 collectors/README.md")
        sys.exit(2)
    py = mc_python(mc_home)
    keywords = read_keywords()
    print(f"MediaCrawler: {mc_home}\nPython: {py}\n关键词: {keywords}")
    print("⚠ 采集请用【专门的小号】扫码，别用你发评论触达客户的账号——"
          "采集(尤其抓评论)请求量大、最易触发限流，与触达号混用会把触达能力一起废掉。")

    platforms = ["xhs", "dy"] if args.platform == "all" else [args.platform]
    mode = args.mode
    results = []
    for plat in platforms:
        t0 = time.time()
        rc = run_platform(mc_home, py, plat, keywords, args.max,
                          get_comment=(args.get_comment == "true"), max_comments=args.max_comments)
        n = ingest(plat, mode, since=t0)          # since=t0：只认本次新产出，杜绝旧数据回灌
        v = verdict_of(rc, n)
        log_collect(plat, keywords, args.account_label, rc, n, v)
        results.append((plat, v))
        if n > 0:
            mode = "append"                        # 有新数据才转 append，合并到同一 leads.csv

    print("\n===== 采集结果（已记入 data/collect_log.csv）=====")
    for plat, v in results:
        print(f"  {plat}: {v}")
    if any("正常" in v for _, v in results):
        print("\n✓ 现在跑： python3 score.py   然后 python3 inspect_leads.py / serve.py")
    else:
        print("\n本次无新线索入库，leads.csv 保持上一版未改。若反复『疑似风控』请换采集小号或降低频次。")


if __name__ == "__main__":
    main()
