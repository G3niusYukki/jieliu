#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py —— 共享数据层（sqlite 单一真相源 + 兼容 CSV）

为什么用 sqlite（Python 自带，无需 pip 安装）：
  线索在「发出评论」之后还要回填——是否对外可见(影子限流)、对方是否回复、是否加到私域、
  报价、成交金额——这些都晚于 posted 发生。旧的 CSV 方案 posted 即删行，没法回填。
  sqlite 让每条线索成为一行可持续更新的记录，支持转化漏斗与多账号归因。

数据形态：
  - data/leads.csv   采集层原始交接格式（adapter 产出，仍是 CSV）
  - data/jieliu.db   线索全生命周期的唯一真相源（leads 表）
  - data/queue.csv   由 db 导出的只读快照（给 inspect / 人工查看 / 向后兼容）

只读写本地文件，不联网、不发布。
"""

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 队列/快照字段（queue.csv 仍按这个，向后兼容 inspect 与历史脚本）
QUEUE_FIELDS = ["id", "lead_type", "platform", "content_id", "url", "title", "author_id",
                "author_name", "target", "matched_keywords", "intent_hits", "priority",
                "score", "status", "comment_text", "created_at", "processed_at", "note"]

# 采集层产出的原始线索字段
LEADS_FIELDS = ["platform", "content_id", "url", "title", "author_id",
                "author_name", "content_excerpt", "ip_location", "publish_time",
                "likes", "comments_count", "crawl_time",
                "lead_type", "parent_content_id", "comment_id", "target"]

VALID_STATUS = {"new", "opened", "posted", "skipped", "failed"}

# 转化漏斗阶段（有序）：发出 -> 对外可见 -> 对方回复 -> 加到私域 -> 报价 -> 成交
FUNNEL_STAGES = ["new", "posted", "replied", "added", "quoted", "deal", "dead"]

# db leads 表的全部列（id 为主键）
LEAD_COLUMNS = [
    "id", "lead_type", "platform", "content_id", "url", "title",
    "author_id", "author_name", "ip_location",
    "parent_content_id", "comment_id", "target",
    "matched_keywords", "intent_hits", "priority", "score",
    "comment_text", "status", "stage", "visible", "visible_checked_at",
    "account", "deal_amount", "note",
    "created_at", "posted_at", "replied_at", "added_at", "deal_at", "processed_at",
]
# 重打分时只刷新「打分/内容」类字段，绝不动「生命周期」字段（status/stage/visible/账号/成交/时间戳），
# 也不动 comment_text（避免覆盖运营已改写的草稿）
_RESCORE_FIELDS = ["lead_type", "platform", "content_id", "url", "title",
                   "author_id", "author_name", "ip_location",
                   "parent_content_id", "comment_id", "target",
                   "matched_keywords", "intent_hits", "priority", "score"]


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config():
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def path_of(key):
    """从 config.paths 取相对路径并解析成绝对路径。key ∈ {leads, queue, history, db}"""
    paths = load_config().get("paths", {})
    default = {"leads": "data/leads.csv", "queue": "data/queue.csv",
              "history": "data/history.csv", "db": "data/jieliu.db"}
    rel = paths.get(key, default.get(key))
    return ROOT / rel


# ---------------- CSV 基础读写（leads.csv 原始 / queue.csv 快照用） ----------------

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


# ---------------- sqlite 数据层 ----------------

def connect(db_path=None):
    p = Path(db_path) if db_path else path_of("db")
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn=None):
    own = conn is None
    conn = conn or connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY,
            lead_type TEXT DEFAULT 'note',
            platform TEXT, content_id TEXT, url TEXT, title TEXT,
            author_id TEXT, author_name TEXT, ip_location TEXT,
            parent_content_id TEXT, comment_id TEXT, target TEXT,
            matched_keywords TEXT, intent_hits TEXT,
            priority TEXT, score INTEGER,
            comment_text TEXT,
            status TEXT DEFAULT 'new',
            stage TEXT DEFAULT 'new',
            visible TEXT DEFAULT '',
            visible_checked_at TEXT,
            account TEXT,
            deal_amount REAL,
            note TEXT,
            created_at TEXT, posted_at TEXT, replied_at TEXT,
            added_at TEXT, deal_at TEXT, processed_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_author ON leads(author_id)")
    conn.commit()
    if own:
        conn.close()


def upsert_lead(row, conn=None):
    """插入一条新线索；若 id 已存在，只刷新打分/内容字段，保留其生命周期（status/stage/可见性/成交…）。"""
    own = conn is None
    conn = conn or connect()
    data = {k: row.get(k) for k in LEAD_COLUMNS}
    cols = ", ".join(LEAD_COLUMNS)
    ph = ", ".join("?" for _ in LEAD_COLUMNS)
    setters = ", ".join(f"{c}=excluded.{c}" for c in _RESCORE_FIELDS)
    conn.execute(
        f"INSERT INTO leads ({cols}) VALUES ({ph}) "
        f"ON CONFLICT(id) DO UPDATE SET {setters}",
        [data.get(c) for c in LEAD_COLUMNS],
    )
    conn.commit()
    if own:
        conn.close()


def dedup_index(cooldown_days, now=None, conn=None):
    """从「已触达过」(status 非 new) 的行建去重索引：已处理的 content_id / url，及每个作者最近处理时间。"""
    own = conn is None
    conn = conn or connect()
    seen_ids, seen_urls, author_last = set(), set(), {}
    for r in conn.execute("SELECT content_id, url, author_id, processed_at, created_at "
                          "FROM leads WHERE status != 'new'"):
        if r["content_id"]:
            seen_ids.add(r["content_id"])
        if r["url"]:
            seen_urls.add(r["url"])
        a = r["author_id"]
        t = _parse_dt(r["processed_at"]) or _parse_dt(r["created_at"])
        if a and t and (a not in author_last or t > author_last[a]):
            author_last[a] = t
    if own:
        conn.close()
    return seen_ids, seen_urls, author_last


def get_queue(status="new", conn=None):
    own = conn is None
    conn = conn or connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM leads WHERE status = ? ORDER BY score DESC", (status,))]
    if own:
        conn.close()
    return rows


def get_followups(conn=None):
    """跟进中的线索（已发出 status=posted），按漏斗阶段→分数排序，供回填可见性/回复/成交。"""
    own = conn is None
    conn = conn or connect()
    order = ("CASE stage WHEN 'posted' THEN 0 WHEN 'replied' THEN 1 "
             "WHEN 'added' THEN 2 WHEN 'quoted' THEN 3 WHEN 'deal' THEN 4 ELSE 5 END")
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM leads WHERE status='posted' ORDER BY {order}, score DESC")]
    if own:
        conn.close()
    return rows


def advance_stage(item_id, stage=None, deal_amount=None, account=None, note=None, conn=None):
    """推进漏斗阶段（replied/added/quoted/deal）并打时间戳/成交额；stage 为空时只更新账号/成交等。"""
    fields = {}
    ts = now_str()
    if stage:
        fields["stage"] = stage
        if stage == "replied":
            fields["replied_at"] = ts
        elif stage == "added":
            fields["added_at"] = ts
        elif stage == "deal":
            fields["deal_at"] = ts
    if deal_amount is not None:
        fields["deal_amount"] = deal_amount
    if account is not None:
        fields["account"] = account
    if note:
        fields["note"] = note
    return update_lead(item_id, conn=conn, **fields)


def set_visibility(item_id, visible, conn=None):
    """回填『评论对外是否可见』(yes/no)——把影子限流显性化。"""
    return update_lead(item_id, visible=visible, visible_checked_at=now_str(), conn=conn)


def get_lead(item_id, conn=None):
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT * FROM leads WHERE id = ?", (item_id,)).fetchone()
    if own:
        conn.close()
    return dict(r) if r else None


def update_lead(item_id, conn=None, **fields):
    """通用更新：写入给定字段（自动忽略未知列）。返回是否命中。"""
    own = conn is None
    conn = conn or connect()
    cols = [k for k in fields if k in LEAD_COLUMNS]
    if cols:
        sets = ", ".join(f"{c}=?" for c in cols)
        conn.execute(f"UPDATE leads SET {sets} WHERE id=?", [fields[c] for c in cols] + [item_id])
        conn.commit()
    ok = conn.execute("SELECT 1 FROM leads WHERE id=?", (item_id,)).fetchone() is not None
    if own:
        conn.close()
    return ok


def mark_processed(item_id, status, comment_text=None, note="", conn=None):
    """标记一条线索的处理结果（posted/skipped/failed）。不删除行——后续可继续回填可见性/回复/成交。"""
    if status not in VALID_STATUS:
        raise ValueError(f"非法状态: {status}")
    own = conn is None
    conn = conn or connect()
    fields = {"status": status, "processed_at": now_str()}
    if status == "posted":
        fields["stage"] = "posted"
        fields["posted_at"] = now_str()
    elif status in ("skipped", "failed"):
        fields["stage"] = "dead"
    if comment_text is not None:
        fields["comment_text"] = comment_text
    if note:
        fields["note"] = note
    ok = update_lead(item_id, conn=conn, **fields)
    result = get_lead(item_id, conn=conn) if ok else None
    if own:
        conn.close()
    return result


def export_queue_csv(queue_path=None, conn=None):
    """把 db 里 status=new 的线索导出成 queue.csv 快照（给 inspect / 人工查看 / 向后兼容）。"""
    rows = get_queue("new", conn=conn)
    write_csv(queue_path or path_of("queue"), rows, QUEUE_FIELDS)
    return len(rows)


def funnel_counts(conn=None):
    """转化漏斗计数：按 stage 与 visible 汇总，并算成交额。"""
    own = conn is None
    conn = conn or connect()
    by_stage = {s: 0 for s in FUNNEL_STAGES}
    for r in conn.execute("SELECT stage, COUNT(*) c FROM leads GROUP BY stage"):
        by_stage[r["stage"] or "new"] = r["c"]
    by_status = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM leads GROUP BY status")}
    by_visible = {(r["visible"] or "未核验"): r["c"] for r in conn.execute(
        "SELECT visible, COUNT(*) c FROM leads WHERE status='posted' OR stage!='new' GROUP BY visible")}
    total_deal = conn.execute("SELECT COALESCE(SUM(deal_amount),0) s FROM leads").fetchone()["s"]
    deals = conn.execute("SELECT COUNT(*) c FROM leads WHERE stage='deal'").fetchone()["c"]
    if own:
        conn.close()
    return {"by_stage": by_stage, "by_status": by_status, "by_visible": by_visible,
            "deals": deals, "deal_amount": total_deal}


def _parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt)
        except ValueError:
            continue
    return None
