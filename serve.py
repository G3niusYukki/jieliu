#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serve.py —— 本地 Web 控制台（人工把关版）

把 data/queue.csv 用网页表格展示：按优先级排好，每条可一键打开链接、复制草稿、
标记 已发布/跳过/失败。发送动作仍由你在打开的标签页里手动完成 —— 本工具不自动发评论。

跑法：
  python3 serve.py            # 默认 http://127.0.0.1:8765 ，会自动开浏览器
只监听本机 127.0.0.1，纯本地，无外部依赖。
"""

import json
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from store import ROOT, load_config, read_csv, mark_processed, path_of

HOST, PORT = "127.0.0.1", 8765

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>自动化截流 · 控制台</title>
<style>
  :root{--hi:#e5484d;--mid:#f0a020;--low:#8a8f98;--bg:#0f1115;--card:#1a1d24;--fg:#e8eaed;--mut:#9aa0aa;--line:#2a2e37}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,"PingFang SC",sans-serif}
  header{position:sticky;top:0;background:#12141a;border-bottom:1px solid var(--line);padding:14px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  h1{font-size:17px;margin:0} .stat{color:var(--mut);font-size:13px}
  .banner{color:var(--mut);font-size:12px;background:#1a1d24;border:1px solid var(--line);border-radius:6px;padding:4px 10px}
  button{font:inherit;border:1px solid var(--line);background:#222732;color:var(--fg);border-radius:7px;padding:7px 12px;cursor:pointer}
  button:hover{border-color:#3a4150} button.primary{background:#2b3650}
  main{max-width:960px;margin:0 auto;padding:18px 16px;display:flex;flex-direction:column;gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:14px 16px}
  .row1{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .badge{font-size:12px;font-weight:700;border-radius:5px;padding:2px 8px;color:#111}
  .badge.high{background:var(--hi);color:#fff} .badge.mid{background:var(--mid)} .badge.low{background:var(--low);color:#fff}
  .score{font-variant-numeric:tabular-nums;color:var(--mut);font-size:13px}
  .plat{font-size:12px;color:var(--mut);border:1px solid var(--line);border-radius:5px;padding:1px 7px}
  .title{font-weight:600;margin:8px 0 2px} a.link{color:#6ea8fe;text-decoration:none;font-size:13px;word-break:break-all}
  .meta{color:var(--mut);font-size:12.5px;margin:4px 0}
  textarea{width:100%;min-height:62px;margin-top:8px;background:#10131a;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit;resize:vertical}
  .acts{display:flex;gap:8px;margin-top:9px;flex-wrap:wrap}
  .ok{color:#5bd66f} .empty{color:var(--mut);text-align:center;padding:50px 0}
  .hint{color:var(--mut);font-size:12px}
</style></head>
<body>
<header>
  <h1>自动化截流 · 控制台</h1>
  <span class="stat" id="stat">加载中…</span>
  <span style="flex:1"></span>
  <button onclick="rescore()">重新打分</button>
  <button onclick="load()">刷新</button>
  <span class="banner">发送由你在打开的标签页手动完成 · 本工具不自动发评论</span>
</header>
<main id="list"></main>
<script>
async function load(){
  const r = await fetch('/api/queue'); const items = await r.json();
  document.getElementById('stat').textContent =
    `待处理 ${items.length} 条 · 高 ${items.filter(x=>x.priority=='high').length} / 中 ${items.filter(x=>x.priority=='mid').length} / 低 ${items.filter(x=>x.priority=='low').length}`;
  const list = document.getElementById('list');
  if(!items.length){ list.innerHTML = '<div class="empty">队列为空。先跑 <code>python3 score.py</code> 或点「重新打分」。</div>'; return; }
  list.innerHTML = '';
  for(const it of items){
    const el = document.createElement('div'); el.className='card'; el.dataset.id=it.id;
    el.innerHTML = `
      <div class="row1">
        <span class="badge ${it.priority}">${it.priority.toUpperCase()}</span>
        <span class="score">分 ${it.score}</span>
        <span class="plat">${it.platform}</span>
      </div>
      <div class="title">${esc(it.title)||'(无标题)'}</div>
      <a class="link" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.url)}</a>
      <div class="meta">命中：${esc(it.matched_keywords)||'-'} ｜ 意图：${esc(it.intent_hits)||'-'}</div>
      <textarea>${esc(it.comment_text)}</textarea>
      <div class="hint">↑ 把 {hook} 换成针对该帖的具体一句，并酌情再改两句，避免千篇一律。</div>
      <div class="acts">
        <button class="primary" onclick="openAndCopy(this)">打开链接 + 复制草稿</button>
        <button onclick="mark(this,'posted')">已发布</button>
        <button onclick="mark(this,'skipped')">跳过</button>
        <button onclick="mark(this,'failed')">失败</button>
      </div>`;
    list.appendChild(el);
  }
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function card(b){return b.closest('.card');}
async function openAndCopy(b){
  const c=card(b); const url=c.querySelector('a.link').href; const txt=c.querySelector('textarea').value;
  try{ await navigator.clipboard.writeText(txt); }catch(e){}
  window.open(url,'_blank','noopener');
}
async function mark(b,status){
  const c=card(b); const id=c.dataset.id; const comment_text=c.querySelector('textarea').value;
  const note = status==='failed' ? (prompt('失败原因（可留空）：')||'') : '';
  await fetch('/api/mark',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id,status,comment_text,note})});
  c.style.opacity=.35; c.querySelectorAll('button,textarea').forEach(x=>x.disabled=true);
  setTimeout(load, 250);
}
async function rescore(){
  document.getElementById('stat').textContent='重新打分中…';
  const r=await fetch('/api/rescore',{method:'POST'}); const t=await r.text();
  await load();
}
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # 静默默认日志
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path.startswith("/api/queue"):
            rows = read_csv(path_of("queue"))
            rows = [r for r in rows if r.get("status") == "new"]
            rows.sort(key=lambda r: int(r.get("score") or 0), reverse=True)
            return self._send(200, json.dumps(rows, ensure_ascii=False))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        if self.path.startswith("/api/mark"):
            try:
                d = json.loads(raw or b"{}")
                row = mark_processed(d["id"], d["status"],
                                     d.get("comment_text"), d.get("note", ""))
                return self._send(200, json.dumps({"ok": bool(row)}, ensure_ascii=False))
            except Exception as e:
                return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
        if self.path.startswith("/api/rescore"):
            try:
                out = subprocess.run([sys.executable, str(ROOT / "score.py")],
                                     capture_output=True, text=True, cwd=str(ROOT))
                return self._send(200, json.dumps({"ok": True, "stdout": out.stdout}, ensure_ascii=False))
            except Exception as e:
                return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    load_config()  # 配置不存在就早点报错
    url = f"http://{HOST}:{PORT}/"
    print(f"控制台已启动：{url}  （Ctrl+C 退出）")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。")
