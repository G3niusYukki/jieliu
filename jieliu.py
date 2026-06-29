#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jieliu.py —— 一个脚本搞定：采集 → 打分筛选 → 输出「需人工手动截流的链接」。

设计原则（按需求）：
  1) 只运行这一个脚本即可。
  2) 输出 = 待人工截流的链接清单（链接 + 一行上下文），不是分析报告。
  3) 只负责【找】，不负责回复——回复是人工的事。
  4) 输出过的链接永久排除，每次跑只给你全新的（--reset-seen 可清空重来）。

用法：
  python3 jieliu.py setup                  # 一次性：装好采集器（clone + 依赖 + 补丁 + 自检）
  python3 jieliu.py                         # 采集(抖音+小红书) → 输出新链接到 data/截流链接.csv
  python3 jieliu.py --platform xhs --max 5  # 只采小红书，每平台最多 5 帖
  python3 jieliu.py --get-comment false     # 不抓评论（更快更稳，但少了评论区买家）
  python3 jieliu.py --no-crawl              # 跳过采集，用上次抓到的数据重新筛
  python3 jieliu.py --reset-seen            # 清空"已输出"记录（之后会重新给出旧链接）
  python3 jieliu.py --selftest              # 自检（不联网、不动你的数据）

采集那一步需要你用【专门的小号】扫码登录（别用你发评论的账号）。本脚本不碰账号密码、不自动登录、不发布。
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT_DIR = ROOT / "collectors" / "_mc_out"          # MediaCrawler 输出目录
SEEN_FILE = DATA / "seen.json"                     # 已输出过的链接（永久排除）
OUT_FILE = DATA / "截流链接.csv"                    # 最终产物：待人工截流的链接清单
PLATFORM_DIR = {"xhs": "xhs", "dy": "dy"}
PLATFORM_CODE = {"xhs": "xiaohongshu", "dy": "douyin",
                 "xiaohongshu": "xiaohongshu", "douyin": "douyin"}


# ----------------------------- 配置 -----------------------------

def load_config():
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------- 脱敏（PIPL：抹真实联系方式，留意图词） -----------------------------

_CONTACT_PATTERNS = [
    (re.compile(r"1[3-9]\d{9}"), "[手机]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[邮箱]"),
    (re.compile(r"(微信|薇信|威信|vx|v信|wx|wechat|qq|扣扣|加\s*[vV])"
                r"\s*[:：=是+\-]*\s*([A-Za-z0-9_]{5,20})", re.I), r"\1[已隐藏]"),
    (re.compile(r"(?<!\d)\d{7,}(?!\d)"), "[号码]"),
]


def scrub(text):
    if not text:
        return text
    s = str(text)
    for pat, repl in _CONTACT_PATTERNS:
        s = pat.sub(repl, s)
    return s


# ----------------------------- 采集（跑 MediaCrawler） -----------------------------

def find_mediacrawler():
    import os
    for c in [os.environ.get("MEDIACRAWLER_HOME", ""),
              ROOT / "vendor" / "MediaCrawler", ROOT.parent / "MediaCrawler"]:
        if c and (Path(c) / "main.py").exists():
            return Path(c)
    return None


def mc_python(mc_home):
    for p in [mc_home / ".venv" / "bin" / "python", mc_home / "venv" / "bin" / "python"]:
        if p.exists():
            return str(p)
    return sys.executable


def read_keywords():
    kw = []
    for line in (ROOT / "keywords.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            kw.append(line)
    return ",".join(kw)


def run_platform(mc_home, py, platform, keywords, max_notes, get_comment, max_comments):
    print(f"\n{'='*60}\n▶ 采集 {platform}（扫码登录请用专门小号；只抓公开数据）\n{'='*60}")
    cmd = [py, "main.py", "--platform", platform, "--lt", "qrcode", "--type", "search",
           "--keywords", keywords, "--get_comment", "true" if get_comment else "false",
           "--save_data_option", "json", "--save_data_path", str(OUT_DIR),
           "--crawler_max_notes_count", str(max_notes), "--headless", "false"]
    if get_comment:
        cmd += ["--max_comments_count_singlenotes", str(max_comments)]
    return subprocess.run(cmd, cwd=str(mc_home)).returncode


def newest(platform, item_type, since=None):
    base = OUT_DIR / PLATFORM_DIR[platform]
    hits = []
    for ext in ("json", "jsonl", "csv"):
        hits += list((base / ext).glob(f"*_{item_type}_*.{ext}")) if (base / ext).exists() else []
    if since is not None:
        hits = [p for p in hits if p.stat().st_mtime >= since]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


# ----------------------------- 归一化（含评论合并 + 评论者抽取 + 脱敏） -----------------------------

def _load_records(path):
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("data", [data])
    if suffix == ".jsonl":
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _pick(row, cands, default=""):
    for c in cands:
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "none":
            return v
    return default


def _norm_time(v):
    if not v:
        return ""
    s = str(v).strip()
    if s.replace(".", "").isdigit():
        ts = int(float(s))
        if ts > 10_000_000_000:
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, OverflowError, OSError):
            return ""
    return s


def _to_int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def normalize(contents_path, comments_path, platform, intent_words):
    """把 MediaCrawler 输出归一成线索列表（帖子线索 + 评论区买家线索）。"""
    contents = _load_records(contents_path)
    comments = _load_records(comments_path) if comments_path else []
    plat = PLATFORM_CODE.get(platform, platform)

    # 评论按内容聚合（取赞最高的前 8 条并进正文，让意图信号参与打分）
    cgroups = defaultdict(list)
    for c in comments:
        cid = _pick(c, ["note_id", "aweme_id"])
        text = scrub(_pick(c, ["content"]))
        if cid and text:
            cgroups[cid].append((_to_int(_pick(c, ["like_count"])), text))
    top_comments = {cid: [t for _, t in sorted(v, reverse=True)[:8]] for cid, v in cgroups.items()}

    leads, note_index = [], {}
    for row in contents:
        content_id = _pick(row, ["note_id", "aweme_id"])
        if not content_id:
            continue
        url = _pick(row, ["note_url", "aweme_url"])
        title = scrub(_pick(row, ["title", "desc"]))
        excerpt = scrub(_pick(row, ["desc", "title"]))
        merged = top_comments.get(content_id, [])
        if merged:
            excerpt = (excerpt + " ｜评论: " + " / ".join(merged)).strip()
        note_index[content_id] = {"url": url, "title": title}
        leads.append({
            "id": f"{plat}-{content_id}", "lead_type": "note", "platform": plat,
            "url": url, "title": title, "content_excerpt": excerpt,
            "author_name": _pick(row, ["nickname"]),
            "ip_location": _pick(row, ["ip_location"]),
            "comments_count": _pick(row, ["comment_count"]),
            "publish_time": _norm_time(_pick(row, ["time", "create_time", "last_update_time"])),
            "who": _pick(row, ["nickname"]), "what": title,
        })

    # 评论区里「问价/问怎么寄」的人 → 抽成可直接回复 TA 的独立线索
    for c in comments:
        note_id = _pick(c, ["note_id", "aweme_id"])
        text = scrub(_pick(c, ["content"]))
        if not (note_id and text) or not any(w and w in text for w in intent_words):
            continue
        note = note_index.get(note_id, {})
        nick = _pick(c, ["nickname"]) or "网友"
        cmt_id = _pick(c, ["comment_id"]) or f"{note_id}_{_pick(c, ['user_id'])}"
        leads.append({
            "id": f"{plat}-{cmt_id}", "lead_type": "commenter", "platform": plat,
            "url": note.get("url", ""), "title": f"评论@{nick}：{text[:24]}",
            "content_excerpt": f"{text}｜原帖:{note.get('title','')}",
            "author_name": nick, "ip_location": _pick(c, ["ip_location"]),
            "comments_count": "",
            "publish_time": _norm_time(_pick(c, ["create_time", "time"])),
            "who": nick, "what": text,
        })
    return leads


# ----------------------------- 打分 / 筛选 -----------------------------

def _hits(text, terms):
    return [t for t in terms if t and t in text]


def score_lead(lead, cfg, now):
    """返回 (score, priority, intent_hits) 或 None（被排除/不相关）。"""
    text = f"{lead.get('title','')} {lead.get('content_excerpt','')}"
    author = lead.get("author_name", "")

    if _hits(text, cfg.get("exclude_words", [])):
        return None                                   # 招聘/加盟/培训…
    if _hits(text, cfg.get("negative_topic_words", [])):
        return None                                   # 科普/B2B/国内大件…
    if _hits(author, cfg.get("seller_author_words", [])):
        return None                                   # 作者是货代/集运卖家

    tiers = cfg["keyword_tiers"]
    hi, mid, low = _hits(text, tiers["high"]), _hits(text, tiers["mid"]), _hits(text, tiers["low"])
    top = "high" if hi else "mid" if mid else "low" if low else None
    if not top:
        return None                                   # 没命中任何物流词

    sc = cfg["scoring"]
    score = sc["tier_base"][top]
    strong = _hits(text, cfg.get("intent_strong", []))
    weak = _hits(text, cfg.get("intent_weak", []))
    intent_hits = strong + weak
    intent_score = len(strong) * sc.get("intent_strong_each", 30) + len(weak) * sc.get("intent_weak_each", 15)
    if lead.get("lead_type") == "commenter":
        intent_score *= sc.get("comment_intent_multiplier", 1.5)
    score += min(intent_score, sc["intent_cap"])

    pub = _parse_dt(lead.get("publish_time", ""))
    if pub:
        age = (now - pub).days
        for d, bonus in sorted(sc["recency_bonus"].items(), key=lambda x: int(x[0])):
            if age <= int(d):
                score += bonus
                break

    cc = _to_int(lead.get("comments_count"))
    if 1 <= cc <= sc["engagement_active_max_comments"]:
        score += sc["engagement_active_bonus"]
    if _hits(text, cfg.get("negative_signals", [])):
        score -= sc.get("negative_signal_penalty", 40)
    if _hits(lead.get("ip_location", ""), cfg.get("overseas_regions", [])):
        score += sc.get("overseas_bonus", 0)

    cut = sc["priority_cut"]
    priority = "high" if score >= cut["high"] else "mid" if score >= cut["mid"] else "low"
    return int(round(score)), priority, intent_hits


def _parse_dt(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime((s or "").strip(), fmt)
        except ValueError:
            continue
    return None


# ----------------------------- 已输出去重（永久排除） -----------------------------

def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")).get("seen", []))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    DATA.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps({"seen": sorted(seen)}, ensure_ascii=False), encoding="utf-8")


# ----------------------------- 输出 -----------------------------

def write_output(rows):
    DATA.mkdir(parents=True, exist_ok=True)
    fields = ["score", "priority", "platform", "type", "intent", "who", "link", "what"]
    with open(OUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ----------------------------- 主流程 -----------------------------

def collect_and_score(platforms, do_crawl, max_notes, get_comment, max_comments, account_label):
    cfg = load_config()
    intent_words = (cfg.get("intent_strong", []) + cfg.get("intent_weak", [])) or cfg.get("intent_signals", [])
    now = datetime.now()

    mc_home = py = None
    if do_crawl:
        mc_home = find_mediacrawler()
        if not mc_home:
            print("✗ 没找到采集器。先跑：python3 jieliu.py setup")
            sys.exit(2)
        py = mc_python(mc_home)
        print(f"关键词: {read_keywords()}")
        print("⚠ 扫码请用【专门的小号】，别用你发评论触达客户的账号。")

    all_leads = []
    for plat in platforms:
        since = None
        if do_crawl:
            t0 = time.time()
            rc = run_platform(mc_home, py, plat, read_keywords(), max_notes, get_comment, max_comments)
            since = t0
        contents = newest(plat, "contents", since=since)
        comments = newest(plat, "comments", since=since) if get_comment else None
        if not contents:
            if do_crawl:
                print(f"  {plat}: ⚠ 没抓到本次数据（可能被风控/中断/无结果，或登录小号被限）")
            else:
                print(f"  {plat}: 没有可用的历史采集数据，先不带 --no-crawl 跑一次")
            continue
        all_leads += normalize(contents, comments, plat, intent_words)

    scored = []
    for lead in all_leads:
        r = score_lead(lead, cfg, now)
        if r is None:
            continue
        score, priority, intent_hits = r
        scored.append((score, priority, intent_hits, lead))
    return scored


def main():
    ap = argparse.ArgumentParser(description="找待人工截流的链接（只找不回）")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "setup"],
                    help="run=采集并输出链接(默认); setup=一次性装采集器")
    ap.add_argument("--platform", choices=["xhs", "dy", "all"], default="all")
    ap.add_argument("--max", type=int, default=5, help="每平台最多抓多少帖")
    ap.add_argument("--max-comments", type=int, default=20, help="每帖最多抓多少条评论")
    ap.add_argument("--get-comment", choices=["true", "false"], default="true")
    ap.add_argument("--no-crawl", action="store_true", help="跳过采集，用上次抓到的数据重新筛")
    ap.add_argument("--account-label", default="", help="本次采集用的小号标签（仅提示）")
    ap.add_argument("--reset-seen", action="store_true", help="清空『已输出』记录后退出")
    ap.add_argument("--selftest", action="store_true", help="自检（不联网、不动你的数据）")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()
    if args.reset_seen:
        SEEN_FILE.unlink(missing_ok=True)
        print("✓ 已清空『已输出』记录，下次会重新给出旧链接。")
        return
    if args.command == "setup":
        sh = ROOT / "collectors" / "setup_mediacrawler.sh"
        sys.exit(subprocess.run(["bash", str(sh)]).returncode)

    platforms = ["xhs", "dy"] if args.platform == "all" else [args.platform]
    scored = collect_and_score(platforms, not args.no_crawl, args.max,
                               args.get_comment == "true", args.max_comments, args.account_label)

    seen = load_seen()
    fresh = [s for s in scored if s[3]["id"] not in seen]
    fresh.sort(key=lambda s: s[0], reverse=True)

    rows = []
    for score, priority, intent_hits, lead in fresh:
        is_cm = lead["lead_type"] == "commenter"
        rows.append({
            "score": score, "priority": priority, "platform": lead["platform"],
            "type": "评论者" if is_cm else "帖子",
            "intent": "|".join(intent_hits) or "-",
            "who": lead.get("who", ""),
            "link": lead["url"],
            "what": (f'回复@{lead["who"]}：' if is_cm else "") + (lead.get("what", "") or ""),
        })

    write_output(rows)
    save_seen(seen | {s[3]["id"] for s in fresh})

    total_scored = len(scored)
    print(f"\n{'='*60}")
    print(f"本次新增 {len(rows)} 条待截流链接"
          f"（共筛出 {total_scored} 条，排除已输出 {total_scored - len(rows)} 条）")
    print(f"已写入：{OUT_FILE}")
    if rows:
        print("\n按优先级（去 data/截流链接.csv 看全部）：")
        for r in rows[:20]:
            tag = "评论" if r["type"] == "评论者" else "帖子"
            print(f"  [{r['priority']:>4}] {r['score']:>3} {r['platform']:<11} {tag} 意图:{r['intent']}")
            print(f"        {r['what'][:42]}")
            print(f"        {r['link']}")
    else:
        print("（没有新链接——要么这批都跑过了，要么没搜到对口买家。可调 keywords.txt 或 --reset-seen）")


# ----------------------------- 自检 -----------------------------

def run_selftest():
    import tempfile
    P, F = [0], [0]

    def ck(name, cond):
        (P if cond else F)[0] += 1
        print(("  ✓ " if cond else "  ✗ ") + name)

    cfg = load_config()
    intent = (cfg.get("intent_strong", []) + cfg.get("intent_weak", []))
    now = datetime.now()
    nowms = int(now.timestamp() * 1000)

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        contents = [
            {"note_id": "n1", "title": "家具海运回国", "desc": "一堆家具想运回国",
             "time": nowms, "user_id": "u1", "nickname": "小鹿", "ip_location": "美国",
             "comment_count": "12", "note_url": "https://x/n1"},
            {"note_id": "n2", "title": "招聘国际物流业务员", "desc": "加盟代理培训",
             "time": nowms, "user_id": "u2", "nickname": "HR", "note_url": "https://x/n2"},
            {"note_id": "n3", "title": "每天一个货代知识｜灰清", "desc": "科普",
             "time": nowms, "user_id": "u3", "nickname": "货代老王", "note_url": "https://x/n3"},
        ]
        comments = [
            {"comment_id": "c1", "note_id": "n1", "content": "大件托运多少钱啊 加微信13912345678",
             "user_id": "b1", "nickname": "买家A", "ip_location": "澳大利亚", "like_count": "9",
             "create_time": nowms},
        ]
        cp = d / "c.json"; mp = d / "m.json"
        cp.write_text(json.dumps(contents, ensure_ascii=False))
        mp.write_text(json.dumps(comments, ensure_ascii=False))

        leads = normalize(cp, mp, "xhs", intent)
        by = {l["id"]: l for l in leads}
        ck("帖子线索归一化", "xiaohongshu-n1" in by)
        ck("评论里的联系方式被脱敏", "13912345678" not in by["xiaohongshu-c1"]["content_excerpt"])
        ck("评论意图合并进帖子正文", "多少钱" in by["xiaohongshu-n1"]["content_excerpt"])
        ck("评论者被抽成独立线索", "xiaohongshu-c1" in by and by["xiaohongshu-c1"]["lead_type"] == "commenter")

        scored = {l["id"]: score_lead(l, cfg, now) for l in leads}
        ck("招聘帖被排除", scored["xiaohongshu-n2"] is None)
        ck("货代科普帖被排除", scored["xiaohongshu-n3"] is None)
        ck("家具海运回国帖保留且 high", scored["xiaohongshu-n1"] and scored["xiaohongshu-n1"][1] == "high")
        ck("评论者线索保留(继承原帖品类) 且 high", scored["xiaohongshu-c1"] and scored["xiaohongshu-c1"][1] == "high")
        # 海外属地加分
        l_os = dict(by["xiaohongshu-n1"]); l_cn = dict(l_os); l_cn["ip_location"] = "广东"
        ck("海外属地比国内分高", score_lead(l_os, cfg, now)[0] > score_lead(l_cn, cfg, now)[0])

    print(f"\n{'='*42}\n自检：通过 {P[0]} ｜ 失败 {F[0]}")
    sys.exit(1 if F[0] else 0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断退出。")
        sys.exit(0)
