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
import os
import subprocess
import sys
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


def newest(platform, item_type):
    """找最新的 search_{contents|comments}_*.json（兼容 jsonl/csv）。"""
    base = OUT_DIR / PLATFORM_DIR[platform]
    hits = []
    for ext in ("json", "jsonl", "csv"):
        hits += list((base / ext).glob(f"*_{item_type}_*.{ext}")) if (base / ext).exists() else []
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def run_platform(mc_home, py, platform, keywords, max_notes):
    print(f"\n{'='*60}\n▶ 采集 {platform}  （扫码登录你自己的账号；抓公开数据）\n{'='*60}")
    cmd = [py, "main.py",
           "--platform", platform,
           "--lt", "qrcode",
           "--type", "search",
           "--keywords", keywords,
           "--get_comment", "true",
           "--save_data_option", "json",
           "--save_data_path", str(OUT_DIR),
           "--crawler_max_notes_count", str(max_notes),
           "--headless", "false"]
    # 继承 stdio：扫码登录是交互式的，必须让你看到二维码/提示
    r = subprocess.run(cmd, cwd=str(mc_home))
    if r.returncode != 0:
        print(f"⚠ MediaCrawler 退出码 {r.returncode}（可能是你中断了登录）。继续尝试读取已产出的数据。")
    return r.returncode


def ingest(platform, mode):
    contents = newest(platform, "contents")
    comments = newest(platform, "comments")
    if not contents:
        print(f"✗ 没找到 {platform} 的 contents 输出，跳过归一化。")
        return False
    args = [sys.executable, str(ADAPTER), str(contents), "--platform", platform, "--mode", mode]
    if comments:
        args += ["--comments", str(comments)]
    print(f"→ 归一化 {platform}：{contents.name}" + (f" + {comments.name}" if comments else " （无评论文件）"))
    subprocess.run(args, check=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["xhs", "dy", "all"], default="all")
    ap.add_argument("--max", type=int, default=15, help="每平台最多抓多少条")
    ap.add_argument("--mode", choices=["overwrite", "append"], default="overwrite",
                    help="overwrite=当天重抓覆盖 leads.csv（默认）；append=追加")
    args = ap.parse_args()

    mc_home = find_mediacrawler()
    if not mc_home:
        print("✗ 没找到 MediaCrawler。请 clone 到 vendor/MediaCrawler，"
              "或设环境变量 MEDIACRAWLER_HOME 指向它。详见 collectors/README.md")
        sys.exit(2)
    py = mc_python(mc_home)
    keywords = read_keywords()
    print(f"MediaCrawler: {mc_home}\nPython: {py}\n关键词: {keywords}")

    platforms = ["xhs", "dy"] if args.platform == "all" else [args.platform]
    mode = args.mode
    for i, plat in enumerate(platforms):
        run_platform(mc_home, py, plat, keywords, args.max)
        ingest(plat, mode)
        mode = "append"          # 第二个平台起追加，合到同一个 leads.csv

    print(f"\n✓ 完成。现在跑： python3 score.py  然后 python3 serve.py")


if __name__ == "__main__":
    main()
