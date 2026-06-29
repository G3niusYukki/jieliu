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
import hashlib
import json
import os
import re
import signal
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
WORKLIST_FILE = DATA / "worklist.json"             # 看板累积工作清单（含『已截流』状态）
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
    for p in [mc_home / ".venv" / "bin" / "python", mc_home / "venv" / "bin" / "python",
              mc_home / ".venv" / "Scripts" / "python.exe", mc_home / "venv" / "Scripts" / "python.exe"]:
        if p.exists():
            return str(p)
    return sys.executable


def crawler_installed():
    return find_mediacrawler() is not None


def run_setup(cn=False):
    """一次性安装采集器（纯 Python，跨平台，只需 git + python，无需 bash）。

    cn=True 或环境变量 JIELIU_CN=1 → 国内镜像模式（pip 清华源 + 跳过 playwright chromium）。
    可用环境变量覆盖：JIELIU_MC_REPO（clone 地址/镜像）、JIELIU_PIP_INDEX（pip 源）。
    步骤（幂等可重复跑）：clone → 建独立 venv → 装依赖 → 应用补丁 → macOS 去隔离 → 自检。
    """
    import shutil
    import urllib.parse
    cn = cn or bool(os.environ.get("JIELIU_CN", "").strip())
    mc_home = Path(os.environ.get("MEDIACRAWLER_HOME") or (ROOT / "vendor" / "MediaCrawler"))
    patch_dir = ROOT / "collectors" / "patches"
    repo = os.environ.get("JIELIU_MC_REPO", "").strip() or "https://github.com/NanmiCoder/MediaCrawler.git"
    pip_index = (os.environ.get("JIELIU_PIP_INDEX", "").strip()
                 or ("https://pypi.tuna.tsinghua.edu.cn/simple" if cn else ""))
    is_win = os.name == "nt"

    def run(cmd, quiet=False, **kw):
        if not quiet:
            print("   $ " + " ".join(str(c) for c in cmd))
        return subprocess.run(cmd, **kw)

    def pip_idx():
        if not pip_index:
            return []
        host = urllib.parse.urlparse(pip_index).hostname or ""
        return ["-i", pip_index] + (["--trusted-host", host] if host else [])

    git = shutil.which("git")
    if not git:
        print("✗ 没找到 git。请先安装 Git（Windows: https://git-scm.com/download/win），再重试。")
        return 1
    if cn:
        print("▶ 国内镜像模式：pip 用清华源；CDP 用系统 Chrome/Edge（跳过 playwright chromium 下载）。")
    print(f"▶ 采集器目录: {mc_home}")

    # 1) clone（已存在则跳过）
    if not (mc_home / "main.py").exists():
        print(f"▶ 克隆 MediaCrawler …（源：{repo}）")
        mc_home.parent.mkdir(parents=True, exist_ok=True)
        if run([git, "clone", "--depth", "1", repo, str(mc_home)]).returncode != 0:
            print("✗ 克隆失败。国内访问 GitHub 常受限，二选一：")
            print("   ① 用镜像/加速地址：JIELIU_MC_REPO=<镜像地址> python jieliu.py setup --cn")
            print("   ② 手动下载 MediaCrawler 解压到 vendor/MediaCrawler（含 main.py），再重跑（会自动跳过克隆）")
            return 1
    else:
        print("✓ 已存在 MediaCrawler，跳过克隆")

    # 2) venv（不存在才建）
    vpy = mc_home / ".venv" / ("Scripts" if is_win else "bin") / ("python.exe" if is_win else "python")
    if not vpy.exists():
        print("▶ 创建独立 venv …")
        run([sys.executable, "-m", "venv", str(mc_home / ".venv")])
    if not vpy.exists():
        print(f"✗ venv 创建失败：{vpy} 不存在（Linux 可能需先装 python3-venv）。")
        return 1

    # 3) 依赖
    print("▶ 安装依赖（可能要几分钟）…" + (f"  源：{pip_index}" if pip_index else ""))
    run([str(vpy), "-m", "pip", "install", "-q", "--upgrade", "pip"] + pip_idx())
    req = mc_home / "requirements.txt"
    if req.exists() and run([str(vpy), "-m", "pip", "install", "-q", "-r", str(req)] + pip_idx()).returncode != 0:
        print("✗ 依赖安装失败。" + ("" if pip_index else " 国内可加清华源：python jieliu.py setup --cn"))
        return 1
    if cn or os.environ.get("JIELIU_SKIP_PLAYWRIGHT", "").strip():
        print("▶ 跳过 playwright chromium 下载（CDP 用系统 Chrome/Edge；Windows 自带 Edge 即可）。")
    elif run([str(vpy), "-m", "playwright", "install", "chromium"]).returncode != 0:
        print("⚠ playwright chromium 未装（CDP 模式用系统 Chrome，不影响采集）")

    # 4) 应用补丁（git apply，幂等：已应用则跳过，版本漂移则告警不中断）
    if patch_dir.is_dir():
        print("▶ 应用补丁 …")
        for patch in sorted(patch_dir.glob("*.patch")):
            name = patch.name
            if run([git, "-C", str(mc_home), "apply", "--reverse", "--check", str(patch)],
                   quiet=True, capture_output=True).returncode == 0:
                print(f"  ✓ 已应用，跳过: {name}")
            elif run([git, "-C", str(mc_home), "apply", "--check", str(patch)],
                     quiet=True, capture_output=True).returncode == 0:
                run([git, "-C", str(mc_home), "apply", str(patch)], quiet=True)
                print(f"  ✓ 应用成功: {name}")
            else:
                print(f"  ⚠ 应用失败（MediaCrawler 版本可能已变，需人工核对）: {name}")

    # 5) macOS：去 .venv 隔离属性（否则 lxml 等原生库被系统策略拦）
    if sys.platform == "darwin":
        print("▶ macOS：清理 .venv 隔离属性 …")
        run(["xattr", "-r", "-d", "com.apple.quarantine", str(mc_home / ".venv")],
            quiet=True, stderr=subprocess.DEVNULL)

    # 6) 自检 import
    print("▶ 自检依赖 import …")
    if run([str(vpy), "-c",
            "import lxml.etree, playwright, httpx, parsel, execjs, xhshow; print('✓ 采集层依赖 OK')"]
           ).returncode != 0:
        print("⚠ 自检未通过（上面有依赖报错）；可先试用，采集报错再排查。")
        return 1
    print("\n✅ 采集器就绪。下一步：python jieliu.py serve（或直接 kanban）")
    return 0


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


# ----------------------------- 看板数据：累积工作清单 + 已截流状态 -----------------------------

def _row_key(row):
    raw = "|".join(str(row.get(k, "")) for k in ("platform", "type", "link", "who", "what"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_worklist():
    if WORKLIST_FILE.exists():
        try:
            return json.loads(WORKLIST_FILE.read_text(encoding="utf-8")).get("rows", [])
        except Exception:
            return []
    return []


def save_worklist(rows):
    DATA.mkdir(parents=True, exist_ok=True)
    WORKLIST_FILE.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")


def merge_latest_into_worklist():
    """把最近一次 截流链接.csv 的新行并入累积清单（按内容去重，保留『已截流』状态）。"""
    rows = load_worklist()
    have = {r.get("id") for r in rows}
    if OUT_FILE.exists():
        added = False
        with open(OUT_FILE, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = _row_key(r)
                if key in have:
                    continue
                have.add(key)
                added = True
                rows.append({"id": key, "done": False, "comment": "",
                             "score": _to_int(r.get("score")), "priority": r.get("priority", ""),
                             "platform": r.get("platform", ""), "type": r.get("type", ""),
                             "intent": r.get("intent", ""), "who": r.get("who", ""),
                             "link": r.get("link", ""), "what": r.get("what", "")})
        if added:
            save_worklist(rows)
    return rows


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
    interrupted = False
    for plat in platforms:
        since = None
        if do_crawl:
            t0 = time.time()
            try:
                run_platform(mc_home, py, plat, read_keywords(), max_notes, get_comment, max_comments)
            except KeyboardInterrupt:
                interrupted = True          # 中断了也要把已抓到的数据用上，绝不浪费
            since = t0
        contents = newest(plat, "contents", since=since)
        comments = newest(plat, "comments", since=since) if get_comment else None
        if contents:
            all_leads += normalize(contents, comments, plat, intent_words)
        elif do_crawl:
            print(f"  {plat}: ⚠ 没抓到本次数据（可能被风控/中断/无结果，或登录小号被限）")
        else:
            print(f"  {plat}: 没有可用的历史采集数据，先不带 --no-crawl 跑一次")
        if interrupted:
            print("\n⚠ 你中断了采集——下面直接用【已抓到的数据】出链接，不让你白跑。")
            break

    scored = []
    for lead in all_leads:
        r = score_lead(lead, cfg, now)
        if r is None:
            continue
        score, priority, intent_hits = r
        scored.append((score, priority, intent_hits, lead))
    return scored


# ----------------------------- AI：业务→关键词；模板+业务+帖子→评论草稿（纯 urllib 调火山方舟 ARK） -----------------------------

AICONFIG_FILE = DATA / "aiconfig.json"   # 业务/模板 + BYOK 的 ARK Key/模型（本地自用，不入库）

# 火山方舟 ARK 大陆端点（OpenAI 兼容 Chat Completions）
ARK_DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
ARK_DEFAULT_MODEL = "doubao-seed-1-6-250615"


def load_aiconfig():
    d = {}
    if AICONFIG_FILE.exists():
        try:
            d = json.loads(AICONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    return {"business": d.get("business", ""), "templates": d.get("templates", ""),
            "ark_key": d.get("ark_key", ""), "ark_model": d.get("ark_model", ""),
            "ark_base_url": d.get("ark_base_url", "")}


def save_aiconfig(updates):
    """只更新传入的字段，其余保留（updates 为 dict）。"""
    DATA.mkdir(parents=True, exist_ok=True)
    cur = {}
    if AICONFIG_FILE.exists():
        try:
            cur = json.loads(AICONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    cur.update(updates)
    AICONFIG_FILE.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")


def _ark_settings():
    """BYOK：Key/模型/端点 优先取环境变量，其次取看板里保存的，最后用默认。"""
    cfg = load_aiconfig()
    key = os.environ.get("ARK_API_KEY", "").strip() or (cfg.get("ark_key") or "").strip()
    model = (os.environ.get("ARK_MODEL", "").strip() or (cfg.get("ark_model") or "").strip()
             or ARK_DEFAULT_MODEL)
    base = (os.environ.get("ARK_BASE_URL", "").strip() or (cfg.get("ark_base_url") or "").strip()
            or ARK_DEFAULT_BASE)
    return key, model, base.rstrip("/")


def _llm(prompt, system, max_tokens=800):
    """调火山方舟 ARK 的 OpenAI 兼容 Chat Completions（纯标准库 urllib）。BYOK：见 _ark_settings。"""
    import urllib.request
    import urllib.error
    key, model, base = _ark_settings()
    if not key:
        raise RuntimeError("未配置 ARK API Key。用环境变量 ARK_API_KEY=... 或在看板「AI 设置」里填你自己的 Key（BYOK）。")
    payload = json.dumps({"model": model, "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"调用 ARK 失败 HTTP {e.code}：{e.read().decode('utf-8', 'ignore')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误：{e.reason}")
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"ARK 返回异常：{json.dumps(data, ensure_ascii=False)[:300]}")


def list_ark_models():
    """用当前 Key 拉取账号可用模型（OpenAI 兼容 GET /models）。"""
    import urllib.request
    import urllib.error
    key, _model, base = _ark_settings()
    if not key:
        raise RuntimeError("未配置 ARK API Key。请先在「AI 设置」填入 Key 并保存，再刷新模型。")
    req = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"获取模型失败 HTTP {e.code}：{e.read().decode('utf-8', 'ignore')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误：{e.reason}")
    ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    return sorted(set(ids))


_KW_SYSTEM = (
    "你是中文社媒获客的关键词策划。根据用户的【业务描述】，产出用于在小红书/抖音上搜索"
    "『潜在买家发帖或评论提问』的搜索词。要点：①站在消费者视角用大白话（例：海运回国、留学回国行李、"
    "美国寄回国、日本寄中国），不要行业黑话/卖家词（如货代、双清、头程、专线）；②覆盖不同说法、地区、"
    "场景；③每行一个，15-30 个；④只输出关键词本身，不要编号、不要解释、不要空行。")


def gen_keywords(business):
    if not (business or "").strip():
        raise RuntimeError("请先填写【业务】再生成关键词。")
    text = _llm(f"业务描述：\n{business.strip()}", _KW_SYSTEM, max_tokens=600)
    lines = [l.strip().lstrip("-*·0123456789.、)（） ").strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


_CMT_SYSTEM = (
    "你在帮商家做小红书/抖音『截流获客』，给目标帖子写一条评论草稿，供人工审核后手动发布。要求："
    "①紧扣该帖/该评论的具体内容，像真人随口搭话，不要像广告、不要套话；②可参考【历史评论模板】的语气，"
    "但每条都要不一样，避免雷同被判垃圾；③不要直接写微信/电话/QQ（会被限流），用『可以帮你看看』"
    "『有需要可以私我』这类自然引导；④20-60 字，中文；⑤只输出评论正文，不要解释、不要加引号。")


def gen_comment(lead, business, templates):
    parts = [f"【业务】{business.strip()}" if (business or "").strip() else "",
             f"【历史评论模板/风格】\n{templates.strip()}" if (templates or "").strip() else "",
             f"【目标平台】{lead.get('platform', '')}",
             f"【类型】{lead.get('type', '')}（帖子=在该帖下评论；评论者=回复评论区这个人）",
             f"【对方/帖主】{lead.get('who', '')}",
             f"【命中的买家意图】{lead.get('intent', '')}",
             f"【帖子/评论内容】{lead.get('what', '')}"]
    return _llm("\n".join(p for p in parts if p), _CMT_SYSTEM, max_tokens=400)


# ----------------------------- 看板（本地网页：业务/模板/关键词 · 看/筛/标记/跑采集/导出/生成评论） -----------------------------

DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>截流看板</title>
<style>
  :root{--hi:#e8462e;--mid:#d98a00;--low:#999}
  *{box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#222}
  header{background:#fff;border-bottom:1px solid #e3e5e8;padding:12px 18px;position:sticky;top:0;z-index:5}
  h1{font-size:16px;margin:0 0 8px}
  .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
  .bar>*{font-size:13px}
  select,input[type=text],input[type=number]{padding:5px 7px;border:1px solid #ccc;border-radius:6px;font-size:13px}
  button{padding:6px 12px;border:0;border-radius:6px;background:#1a73e8;color:#fff;cursor:pointer;font-size:13px}
  button.sec{background:#eef0f3;color:#333}
  button:disabled{opacity:.5;cursor:not-allowed}
  .spacer{flex:1}
  .stat{color:#666;font-size:12px}
  main{padding:14px 18px}
  table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;font-size:13px}
  th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #eef0f2;vertical-align:top}
  th{background:#fafbfc;font-weight:600;color:#555}
  tr.done{opacity:.4;text-decoration:line-through}
  td.p-high{color:var(--hi);font-weight:700}
  td.p-mid{color:var(--mid);font-weight:600}
  td.p-low{color:var(--low)}
  td.what{max-width:380px}
  a{color:#1a73e8}
  .modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);align-items:center;justify-content:center;z-index:10}
  .modal .box{background:#fff;border-radius:10px;padding:18px;width:min(640px,92vw)}
  textarea{width:100%;height:300px;font-family:ui-monospace,monospace;font-size:13px;padding:8px;border:1px solid #ccc;border-radius:6px}
  .runbox{display:none;background:#1e1e1e;color:#d8d8d8;font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;padding:10px;border-radius:8px;max-height:220px;overflow:auto;margin-top:10px}
  details.cfg{background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:8px 12px;margin-bottom:10px}
  details.cfg summary{cursor:pointer;font-weight:600;font-size:13px;color:#333;outline:none}
  .cfg-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0}
  .cfg label{display:block;font-size:12px;color:#666;margin:6px 0 3px}
  .cfg textarea{width:100%;height:88px;font-size:13px;padding:7px;border:1px solid #ccc;border-radius:6px;font-family:inherit}
  .cfg-actions{display:flex;gap:8px;margin:8px 0;flex-wrap:wrap}
  @media(max-width:760px){.cfg-grid{grid-template-columns:1fr}}
  td.cmtcol{white-space:nowrap}
  .drawer{position:fixed;top:0;right:0;height:100vh;width:min(440px,94vw);background:#fff;box-shadow:-4px 0 24px rgba(0,0,0,.18);transform:translateX(100%);transition:transform .2s;z-index:20;display:flex;flex-direction:column}
  .drawer.open{transform:translateX(0)}
  .drawer-head{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-bottom:1px solid #eee}
  .drawer-body{padding:14px;overflow:auto;display:flex;flex-direction:column;gap:8px}
  .d-meta{font-size:12px;color:#666}
  .d-content{background:#f6f7f9;border-radius:6px;padding:8px;font-size:13px;max-height:160px;overflow:auto;white-space:pre-wrap}
  .drawer textarea{width:100%;height:120px;font-size:14px;padding:8px;border:1px solid #ccc;border-radius:6px;font-family:inherit}
  hr{border:0;border-top:1px solid #eee;width:100%;margin:4px 0}
  #toast{position:fixed;left:50%;bottom:30px;transform:translateX(-50%) translateY(20px);background:#222;color:#fff;padding:9px 16px;border-radius:20px;font-size:13px;opacity:0;pointer-events:none;transition:.25s;z-index:30}
  #toast.show{opacity:.95;transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>
<header>
  <h1>截流看板 · 找链接 + AI 写评论草稿（只找不发，草稿请人工审核后手动发布）</h1>
  <details class="cfg" id="cfgpanel" open>
    <summary>① 业务 / 历史评论模板 / 关键词（点此展开收起）</summary>
    <div class="cfg-grid">
      <div>
        <label>业务（你是做什么的、卖给谁、主打哪些线路/国家）→ 用来生成搜索关键词</label>
        <textarea id="biz" placeholder="例：我做中美海运搬家，帮在美华人/留学生把家具、行李海运回国，主打门到门双清包税……"></textarea>
      </div>
      <div>
        <label>历史评论模板（你平时怎么留言，几条都行）→ 用来给每条链接生成风格一致的评论草稿</label>
        <textarea id="tpl" placeholder="例：这种我们之前帮人寄过，可以帮你看看～｜家具大件走海运最划算，有需要可以私我聊聊……"></textarea>
      </div>
    </div>
    <div class="cfg-actions">
      <button onclick="saveConfig()">💾 保存业务/模板</button>
      <button class="sec" onclick="genKeywords()">✨ 用业务生成关键词</button>
    </div>
    <label>搜索关键词（每行一个，# 开头为注释；可手动改，改完保存再采集生效）</label>
    <textarea id="kwtext" style="height:96px"></textarea>
    <div class="cfg-actions"><button class="sec" onclick="saveKw()">💾 保存关键词</button></div>
    <hr>
    <label>AI 设置 · 火山方舟 ARK（自带 Key / BYOK）<span id="ark-status" class="stat"></span></label>
    <div class="cfg-actions" style="align-items:center">
      <input type="password" id="ark-key" placeholder="ARK API Key（留空=不修改）" style="flex:2;min-width:180px;padding:5px 7px;border:1px solid #ccc;border-radius:6px;font-size:13px">
      <select id="ark-model-preset" onchange="pickModel(this.value)">
        <option value="">常用模型…（点「刷新可用模型」拉全量）</option>
        <option value="doubao-seed-1-6-250615">doubao-seed-1.6（推荐）</option>
        <option value="doubao-1-5-pro-32k-250115">doubao-1.5-pro-32k</option>
        <option value="doubao-1-5-lite-32k-250115">doubao-1.5-lite-32k（便宜）</option>
        <option value="deepseek-v3-250324">deepseek-v3</option>
      </select>
      <button class="sec" onclick="refreshModels()">↻ 刷新可用模型</button>
      <input type="text" id="ark-model" placeholder="模型名 或 接入点 ep-xxxx（可手填）" style="flex:2;min-width:200px;padding:5px 7px;border:1px solid #ccc;border-radius:6px;font-size:13px">
      <button onclick="saveArk()">💾 保存 AI 设置</button>
    </div>
  </details>
  <div class="bar">
    平台<select id="f-plat"><option value="all">全部</option><option value="xiaohongshu">小红书</option><option value="douyin">抖音</option></select>
    类型<select id="f-type"><option value="all">全部</option><option value="帖子">帖子</option><option value="评论者">评论者</option></select>
    优先级<select id="f-prio"><option value="all">全部</option><option value="high">high</option><option value="mid">mid</option><option value="low">low</option></select>
    <input type="text" id="f-q" placeholder="搜 找谁/意图/内容" oninput="render()">
    <label><input type="checkbox" id="f-hidedone" checked onchange="render()"> 隐藏已截流</label>
    <span class="spacer"></span>
    <span class="stat" id="stat"></span>
  </div>
  <div class="bar" style="margin-top:8px">
    <button id="runbtn" onclick="runCrawl()">▶ 跑一次采集</button>
    <button class="sec" id="stopbtn" onclick="stopCrawl()" disabled>■ 停止并出本次链接</button>
    <select id="r-plat"><option value="all">抖音+小红书</option><option value="xhs">仅小红书</option><option value="dy">仅抖音</option></select>
    每平台<input type="number" id="r-max" value="5" min="1" max="50" style="width:58px">帖
    每帖评论<input type="number" id="r-mc" value="20" min="0" max="100" style="width:58px">条
    <select id="r-gc"><option value="true">抓评论</option><option value="false">不抓评论(更快更稳)</option></select>
    <span class="spacer"></span>
    <button class="sec" onclick="exportCsv()">⬇ 导出当前列表</button>
    <button class="sec" onclick="loadLeads()">↻ 刷新</button>
  </div>
  <div class="runbox" id="runbox"><span id="runlog"></span></div>
</header>
<main>
  <table>
    <thead><tr><th>✓</th><th>优先</th><th>分</th><th>平台</th><th>类型</th><th>意图</th><th>找谁</th><th>内容</th><th>链接</th><th>评论</th></tr></thead>
    <tbody id="tb"></tbody>
  </table>
</main>
<div class="drawer" id="drawer">
  <div class="drawer-head">
    <strong id="d-title">线索详情</strong>
    <button class="sec" onclick="closeDrawer()">关闭 ✕</button>
  </div>
  <div class="drawer-body">
    <div class="d-meta" id="d-meta"></div>
    <div class="d-content" id="d-content"></div>
    <a id="d-link" href="#" target="_blank" rel="noopener">↗ 打开原帖/评论去手动发布</a>
    <hr>
    <div style="display:flex;gap:8px;align-items:center">
      <button id="d-genbtn" onclick="genComment()">✍ 生成评论草稿</button>
      <span class="stat">AI 草稿 · 请人工审核后手动发布</span>
    </div>
    <textarea id="d-comment" placeholder="点上面生成，或自己写评论草稿…"></textarea>
    <div class="cfg-actions">
      <button class="sec" onclick="copyComment()">📋 复制</button>
      <button class="sec" onclick="saveComment()">💾 保存草稿</button>
      <button id="d-donebtn" onclick="toggleDoneDrawer()">✓ 标记已截流</button>
    </div>
  </div>
</div>
<script>
let LEADS=[];
function val(id){return document.getElementById(id).value;}
function visible(){
  const plat=val('f-plat'),type=val('f-type'),prio=val('f-prio'),q=val('f-q').trim();
  const hideDone=document.getElementById('f-hidedone').checked;
  return LEADS.filter(l=>{
    if(plat!=='all'&&l.platform!==plat)return false;
    if(type!=='all'&&l.type!==type)return false;
    if(prio!=='all'&&l.priority!==prio)return false;
    if(hideDone&&l.done)return false;
    if(q&&!((''+l.who+l.what+l.intent).includes(q)))return false;
    return true;
  }).sort((a,b)=>(b.score||0)-(a.score||0));
}
function cell(tr,t,cls){const td=document.createElement('td');td.textContent=(t==null?'':String(t));if(cls)td.className=cls;tr.appendChild(td);}
function updateStat(){const rows=visible();document.getElementById('stat').textContent=`显示 ${rows.length} ／ 共 ${LEADS.length} 条 ｜ 已截流 ${LEADS.filter(l=>l.done).length}`;}
function render(){
  const rows=visible(),tb=document.getElementById('tb');tb.innerHTML='';
  for(const l of rows){
    const tr=document.createElement('tr');if(l.done)tr.className='done';
    const td0=document.createElement('td');
    const cb=document.createElement('input');cb.type='checkbox';cb.checked=!!l.done;
    cb.onchange=()=>mark(l,cb.checked,tr);td0.appendChild(cb);tr.appendChild(td0);
    cell(tr,l.priority,'p-'+l.priority);
    cell(tr,l.score);
    cell(tr,l.platform==='xiaohongshu'?'小红书':(l.platform==='douyin'?'抖音':l.platform));
    cell(tr,l.type);
    cell(tr,l.intent);
    cell(tr,l.who);
    cell(tr,l.what,'what');
    const tdl=document.createElement('td');
    if(l.link&&/^https?:\/\//.test(l.link)){const a=document.createElement('a');a.href=l.link;a.target='_blank';a.rel='noopener';a.textContent='打开';tdl.appendChild(a);}else tdl.textContent='-';
    tr.appendChild(tdl);
    const tdc=document.createElement('td');tdc.className='cmtcol';
    const wb=document.createElement('button');wb.className='sec';wb.textContent=l.comment?'改评论 ✍':'写评论';
    wb.onclick=()=>openDrawer(l);tdc.appendChild(wb);tr.appendChild(tdc);
    tb.appendChild(tr);
  }
  updateStat();
}
async function mark(l,done,tr){
  l.done=done;tr.className=done?'done':'';
  await fetch('/api/done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:l.id,done})});
  if(done&&document.getElementById('f-hidedone').checked)render();else updateStat();
}
async function loadLeads(){
  const r=await fetch('/api/leads');const d=await r.json();
  LEADS=d.leads||[];document.getElementById('runbtn').disabled=d.running;document.getElementById('stopbtn').disabled=!d.running;render();
  if(d.running)pollStatus();
}
async function runCrawl(){
  const body={platform:val('r-plat'),max:parseInt(val('r-max'))||5,max_comments:parseInt(val('r-mc'))||20,get_comment:val('r-gc')};
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.error){alert(d.error);return;}
  document.getElementById('runbox').style.display='block';
  document.getElementById('runbtn').disabled=true;document.getElementById('stopbtn').disabled=false;pollStatus();
}
async function stopCrawl(){
  document.getElementById('stopbtn').disabled=true;
  const d=await (await fetch('/api/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
  if(d.error){alert(d.error);document.getElementById('stopbtn').disabled=false;return;}
  toast(d.msg||'已发送停止信号，正在出本次链接…');
}
async function pollStatus(){
  const r=await fetch('/api/run/status');const d=await r.json();
  const box=document.getElementById('runbox');box.style.display='block';
  document.getElementById('runlog').textContent=d.log||'(启动中…)';box.scrollTop=box.scrollHeight;
  if(d.running){document.getElementById('runbtn').disabled=true;document.getElementById('stopbtn').disabled=false;setTimeout(pollStatus,1500);}
  else{document.getElementById('runbtn').disabled=false;document.getElementById('stopbtn').disabled=true;if(d.returncode!=null)loadLeads();}
}
async function loadConfig(){
  try{const c=await (await fetch('/api/config')).json();
    document.getElementById('biz').value=c.business||'';
    document.getElementById('tpl').value=c.templates||'';
    document.getElementById('ark-model').value=c.ark_model||'';
    const s=document.getElementById('ark-status');
    if(c.ark_key_env)s.textContent='· 已用环境变量 ARK_API_KEY';
    else if(c.ark_key_set)s.textContent='· 已保存 Key（…'+c.ark_key_hint+'）';
    else s.textContent='· 未设置 Key';
  }catch(e){}
  try{const k=await (await fetch('/api/keywords')).json();document.getElementById('kwtext').value=k.keywords||'';}catch(e){}
}
function pickModel(v){if(v){document.getElementById('ark-model').value=v;}}
async function refreshModels(){
  toast('正在用你的 Key 拉取可用模型…');
  let d;
  try{d=await (await fetch('/api/models')).json();}catch(e){alert('请求失败：'+e);return;}
  if(d.error){alert(d.error);return;}
  const sel=document.getElementById('ark-model-preset');
  sel.innerHTML='<option value="">已刷新 '+d.models.length+' 个可用模型，选一个填到右侧框…</option>';
  for(const m of d.models){const o=document.createElement('option');o.value=m;o.textContent=m;sel.appendChild(o);}
  toast('已拉取 '+d.models.length+' 个模型');
}
async function saveArk(){
  const ark_key=document.getElementById('ark-key').value.trim();
  const ark_model=document.getElementById('ark-model').value.trim();
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ark_key,ark_model})});
  document.getElementById('ark-key').value='';
  toast('已保存 AI 设置');loadConfig();
}
async function saveConfig(){
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({business:document.getElementById('biz').value,templates:document.getElementById('tpl').value})});
  toast('已保存业务/模板');
}
async function saveKw(){
  await fetch('/api/keywords',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keywords:document.getElementById('kwtext').value})});
  toast('已保存关键词');
}
async function genKeywords(){
  const business=document.getElementById('biz').value.trim();
  if(!business){alert('请先填写【业务】');return;}
  toast('正在用业务生成关键词…');
  const d=await (await fetch('/api/gen-keywords',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({business})})).json();
  if(d.error){alert(d.error);return;}
  document.getElementById('kwtext').value=d.keywords||'';
  toast('已生成，检查后点「保存关键词」再采集');
}
let CUR=null;
function openDrawer(l){
  CUR=l;
  document.getElementById('d-title').textContent=(l.type||'')+' · '+(l.who||'');
  const pn=l.platform==='xiaohongshu'?'小红书':(l.platform==='douyin'?'抖音':l.platform);
  document.getElementById('d-meta').textContent=`${pn} ｜ 优先级 ${l.priority} ｜ 分 ${l.score} ｜ 意图 ${l.intent}`;
  document.getElementById('d-content').textContent=l.what||'';
  const a=document.getElementById('d-link');
  if(l.link&&/^https?:\/\//.test(l.link)){a.href=l.link;a.style.display='inline';}else a.style.display='none';
  document.getElementById('d-comment').value=l.comment||'';
  document.getElementById('d-genbtn').textContent=l.comment?'✍ 重新生成':'✍ 生成评论草稿';
  document.getElementById('d-donebtn').textContent=l.done?'↺ 取消已截流':'✓ 标记已截流';
  document.getElementById('drawer').classList.add('open');
}
function closeDrawer(){document.getElementById('drawer').classList.remove('open');}
async function genComment(){
  if(!CUR)return;
  const btn=document.getElementById('d-genbtn');btn.disabled=true;const old=btn.textContent;btn.textContent='生成中…';
  try{
    const d=await (await fetch('/api/gen-comment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:CUR.id})})).json();
    if(d.error){alert(d.error);btn.textContent=old;}
    else{document.getElementById('d-comment').value=d.comment||'';CUR.comment=d.comment||'';btn.textContent='✍ 重新生成';render();}
  }catch(e){alert('生成失败：'+e);btn.textContent=old;}
  finally{btn.disabled=false;}
}
async function copyComment(){
  const t=document.getElementById('d-comment').value;
  try{await navigator.clipboard.writeText(t);toast('已复制，去原帖手动发布');}catch(e){alert('复制失败，请手动选中文本复制');}
}
async function saveComment(){
  if(!CUR)return;
  CUR.comment=document.getElementById('d-comment').value;
  await fetch('/api/comment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:CUR.id,comment:CUR.comment})});
  toast('草稿已保存');render();
}
async function toggleDoneDrawer(){
  if(!CUR)return;
  const nd=!CUR.done;CUR.done=nd;
  await fetch('/api/done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:CUR.id,done:nd})});
  document.getElementById('d-donebtn').textContent=nd?'↺ 取消已截流':'✓ 标记已截流';
  render();
}
function toast(msg){
  let t=document.getElementById('toast');
  if(!t){t=document.createElement('div');t.id='toast';document.body.appendChild(t);}
  t.textContent=msg;t.className='show';
  clearTimeout(window._tt);window._tt=setTimeout(()=>{t.className='';},2200);
}
function exportCsv(){
  const rows=visible(),cols=['score','priority','platform','type','intent','who','link','what'];
  let csv='﻿'+cols.join(',')+'\n';
  for(const l of rows)csv+=cols.map(c=>'"'+String(l[c]==null?'':l[c]).replace(/"/g,'""')+'"').join(',')+'\n';
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='截流链接_导出.csv';a.click();
}
['f-plat','f-type','f-prio'].forEach(id=>document.getElementById(id).onchange=render);
loadConfig();
loadLeads();
</script>
</body>
</html>
"""


def run_serve(host, port, open_browser=True, auto_setup=True):
    import http.server
    import threading
    import urllib.parse

    if auto_setup and not crawler_installed():
        print("⚠ 还没装采集器，正在自动安装（仅首次，可能要几分钟）…")
        run_setup()
        if crawler_installed():
            print("✓ 采集器已就绪。")
        else:
            print("⚠ 采集器未装好；看板照常可用（看/筛/标记/导出/AI），"
                  "但『跑一次采集』要等装好后才行（见 README『在 Windows 上运行』）。")

    state = {"running": False, "log": "", "returncode": None, "proc": None}
    lock = threading.Lock()

    def do_crawl(params):
        argv = [sys.executable, str(Path(__file__).resolve()), "run",
                "--platform", params["platform"], "--max", str(params["max"]),
                "--max-comments", str(params["max_comments"]),
                "--get-comment", params["get_comment"]]
        state["log"] = ("▶ 启动采集： " + " ".join(argv[3:]) +
                        "\n（首次会弹出浏览器扫码——务必用专门小号）\n\n")
        try:
            kw = {}
            if os.name == "nt":     # Windows：独立进程组，便于停止时定向发 CTRL_BREAK
                kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1, **kw)
            state["proc"] = proc
            for line in proc.stdout:
                state["log"] = (state["log"] + line)[-8000:]
            proc.wait()
            state["returncode"] = proc.returncode
            state["log"] = (state["log"] + f"\n✅ 采集结束（returncode={proc.returncode}）。刷新看板看新链接。")[-8000:]
        except Exception as e:
            state["returncode"] = -1
            state["log"] = (state["log"] + f"\n[采集异常] {e}")[-8000:]
        finally:
            with lock:
                state["running"] = False
                state["proc"] = None

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False))

        def _read_body(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", DASHBOARD_HTML)
            elif path == "/api/leads":
                rows = merge_latest_into_worklist()
                gen = (datetime.fromtimestamp(OUT_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                       if OUT_FILE.exists() else "")
                self._json({"leads": rows, "generated": gen, "running": state["running"]})
            elif path == "/api/run/status":
                self._json({"running": state["running"], "log": state["log"], "returncode": state["returncode"]})
            elif path == "/api/keywords":
                kwf = ROOT / "keywords.txt"
                self._json({"keywords": kwf.read_text(encoding="utf-8") if kwf.exists() else ""})
            elif path == "/api/config":
                c = load_aiconfig()
                envkey = os.environ.get("ARK_API_KEY", "").strip()
                key = envkey or (c.get("ark_key") or "")
                self._json({"business": c["business"], "templates": c["templates"],
                            "ark_model": c["ark_model"],
                            "ark_key_set": bool(key), "ark_key_env": bool(envkey),
                            "ark_key_hint": key[-4:] if len(key) >= 4 else ""})
            elif path == "/api/models":
                try:
                    self._json({"models": list_ark_models()})
                except Exception as e:
                    self._json({"error": str(e)})
            else:
                self._send(404, "text/plain; charset=utf-8", "not found")

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            data = self._read_body()
            if path == "/api/done":
                rid, done = str(data.get("id", "")), bool(data.get("done", False))
                rows = load_worklist()
                for r in rows:
                    if r.get("id") == rid:
                        r["done"] = done
                        break
                save_worklist(rows)
                self._json({"ok": True})
            elif path == "/api/run":
                with lock:
                    if state["running"]:
                        self._json({"error": "已有一个采集在跑，请等它结束。"})
                        return
                    plat = data.get("platform", "all")
                    plat = plat if plat in ("xhs", "dy", "all") else "all"
                    try:
                        mx = max(1, min(50, int(data.get("max", 5))))
                    except Exception:
                        mx = 5
                    try:
                        mc = max(0, min(100, int(data.get("max_comments", 20))))
                    except Exception:
                        mc = 20
                    gc = "true" if str(data.get("get_comment", "true")).lower() in ("true", "1", "yes", "on") else "false"
                    state.update(running=True, log="", returncode=None)
                threading.Thread(target=do_crawl, daemon=True,
                                 args=({"platform": plat, "max": mx,
                                        "max_comments": mc, "get_comment": gc},)).start()
                self._json({"ok": True})
            elif path == "/api/stop":
                p = state.get("proc")
                if state["running"] and p is not None and p.poll() is None:
                    try:
                        sig = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
                        p.send_signal(sig)             # 等于程序化 Ctrl+C → 触发「用已采集数据出链接」
                        self._json({"ok": True, "msg": "已发送停止信号，正在用已采集到的数据出链接…"})
                    except Exception as e:
                        self._json({"error": f"停止失败：{e}"})
                else:
                    self._json({"error": "当前没有正在运行的采集。"})
            elif path == "/api/keywords":
                (ROOT / "keywords.txt").write_text(str(data.get("keywords", "")), encoding="utf-8")
                self._json({"ok": True})
            elif path == "/api/config":
                upd = {}
                if "business" in data:
                    upd["business"] = str(data["business"])
                if "templates" in data:
                    upd["templates"] = str(data["templates"])
                if "ark_model" in data:
                    upd["ark_model"] = str(data["ark_model"])
                ak = str(data.get("ark_key", "")).strip()
                if ak:                      # 留空=不修改，避免前端用占位值覆盖真 Key
                    upd["ark_key"] = ak
                save_aiconfig(upd)
                self._json({"ok": True})
            elif path == "/api/gen-keywords":
                try:
                    self._json({"keywords": gen_keywords(str(data.get("business", "")))})
                except Exception as e:
                    self._json({"error": str(e)})
            elif path == "/api/gen-comment":
                rid = str(data.get("id", ""))
                rows = load_worklist()
                row = next((r for r in rows if r.get("id") == rid), None)
                if not row:
                    self._json({"error": "找不到这条线索（可能已刷新）。"})
                    return
                cfg = load_aiconfig()
                try:
                    cmt = gen_comment(row, cfg["business"], cfg["templates"])
                    row["comment"] = cmt
                    save_worklist(rows)
                    self._json({"comment": cmt})
                except Exception as e:
                    self._json({"error": str(e)})
            elif path == "/api/comment":
                rid = str(data.get("id", ""))
                rows = load_worklist()
                for r in rows:
                    if r.get("id") == rid:
                        r["comment"] = str(data.get("comment", ""))
                        break
                save_worklist(rows)
                self._json({"ok": True})
            else:
                self._send(404, "text/plain; charset=utf-8", "not found")

    httpd = http.server.ThreadingHTTPServer((host, port), Handler)
    shown = host if host != "0.0.0.0" else "你的局域网IP"
    print(f"\n{'='*60}\n看板已启动 → http://{shown}:{port}\n{'='*60}")
    if host == "0.0.0.0":
        print("⚠ 绑定 0.0.0.0：同一局域网都能访问，且【无登录验证】。仅在可信内网这么开，别暴露公网。")
    print("运营在网页上即可：看/筛/点链接、标记『已截流』、跑一次采集、导出当前列表、改关键词。")
    print("（Ctrl+C 停止看板）")
    if open_browser and host != "0.0.0.0":
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n看板已停止。")
    finally:
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description="找待人工截流的链接（只找不回）")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "setup", "serve"],
                    help="run=采集并输出链接(默认); setup=一次性装采集器; serve=开网页看板给运营用")
    ap.add_argument("--platform", choices=["xhs", "dy", "all"], default="all")
    ap.add_argument("--max", type=int, default=5, help="每平台最多抓多少帖")
    ap.add_argument("--max-comments", type=int, default=20, help="每帖最多抓多少条评论")
    ap.add_argument("--get-comment", choices=["true", "false"], default="true")
    ap.add_argument("--no-crawl", action="store_true", help="跳过采集，用上次抓到的数据重新筛")
    ap.add_argument("--account-label", default="", help="本次采集用的小号标签（仅提示）")
    ap.add_argument("--reset-seen", action="store_true", help="清空『已输出』记录后退出")
    ap.add_argument("--selftest", action="store_true", help="自检（不联网、不动你的数据）")
    ap.add_argument("--port", type=int, default=8787, help="serve: 看板端口（默认 8787）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="serve: 绑定地址；要给同局域网的运营访问就改成 0.0.0.0")
    ap.add_argument("--no-open", action="store_true", help="serve: 启动后不自动打开浏览器")
    ap.add_argument("--no-setup", action="store_true", help="serve: 首次不自动安装采集器")
    ap.add_argument("--cn", action="store_true",
                    help="setup: 国内镜像模式（pip 清华源 + 跳过 chromium 下载）")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()
    if args.reset_seen:
        SEEN_FILE.unlink(missing_ok=True)
        print("✓ 已清空『已输出』记录，下次会重新给出旧链接。")
        return
    if args.command == "setup":
        sys.exit(run_setup(cn=args.cn))
    if args.command == "serve":
        return run_serve(args.host, args.port, open_browser=not args.no_open,
                         auto_setup=not args.no_setup)

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
    if os.name == "nt" and hasattr(signal, "SIGBREAK"):
        try:    # Windows：让看板「停止采集」发来的 CTRL_BREAK 触发 KeyboardInterrupt → 走抢救出链接
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
        except (ValueError, OSError):
            pass
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断。已抓到的数据还在，跑 `python3 jieliu.py --no-crawl` 即可把它捞成链接。")
        sys.exit(0)
