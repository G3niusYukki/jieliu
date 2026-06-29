#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serve.py —— 本地 Web 控制台（人工把关 + 转化回填，db 真相源）

两个视图：
  · 待处理：按优先级排好的 new 线索，一键打开链接+复制草稿，标 已发布/跳过/失败。
  · 跟进中：已发出的线索在这里回填——评论对外是否可见(影子限流)、对方回复/加私域/报价/成交额。
发送动作仍由你在打开的标签页里手动完成；本工具不自动发评论、不自动点发送。

跑法：python3 serve.py   （默认 http://127.0.0.1:8765，只监听本机）
"""

import json
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from store import (ROOT, load_config, mark_processed, init_db,
                   get_queue, get_followups, advance_stage, set_visibility, update_lead)

HOST, PORT = "127.0.0.1", 8765
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>自动化截流 · 控制台</title>
<style>
  :root{--hi:#e5484d;--mid:#f0a020;--low:#8a8f98;--bg:#0f1115;--card:#1a1d24;--fg:#e8eaed;--mut:#9aa0aa;--line:#2a2e37;--ok:#5bd66f;--cm:#6ea8fe}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,"PingFang SC",sans-serif}
  header{position:sticky;top:0;background:#12141a;border-bottom:1px solid var(--line);padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;z-index:5}
  h1{font-size:16px;margin:0} .stat{color:var(--mut);font-size:13px}
  .tabs{display:flex;gap:6px} .tab{cursor:pointer;padding:6px 12px;border-radius:7px;border:1px solid var(--line);background:#1a1d24;color:var(--mut)}
  .tab.on{background:#2b3650;color:#fff;border-color:#3a4150}
  .banner{color:var(--mut);font-size:12px;background:#1a1d24;border:1px solid var(--line);border-radius:6px;padding:4px 10px}
  button{font:inherit;border:1px solid var(--line);background:#222732;color:var(--fg);border-radius:7px;padding:6px 11px;cursor:pointer}
  button:hover{border-color:#3a4150} button.primary{background:#2b3650}
  main{max-width:980px;margin:0 auto;padding:18px 16px;display:flex;flex-direction:column;gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:14px 16px}
  .row1{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .badge{font-size:12px;font-weight:700;border-radius:5px;padding:2px 8px;color:#111}
  .badge.high{background:var(--hi);color:#fff} .badge.mid{background:var(--mid)} .badge.low{background:var(--low);color:#fff}
  .tag{font-size:12px;color:var(--mut);border:1px solid var(--line);border-radius:5px;padding:1px 7px}
  .tag.cm{color:#ffd479;border-color:#5a4a20} .tag.vis-yes{color:var(--ok)} .tag.vis-no{color:var(--hi)}
  .score{font-variant-numeric:tabular-nums;color:var(--mut);font-size:13px}
  .title{font-weight:600;margin:8px 0 2px} a.link{color:var(--cm);text-decoration:none;font-size:13px;word-break:break-all}
  .meta{color:var(--mut);font-size:12.5px;margin:4px 0}
  textarea{width:100%;min-height:60px;margin-top:8px;background:#10131a;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit;resize:vertical}
  input.amt,input.acc{background:#10131a;color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:5px 8px;font:inherit;width:110px}
  .acts{display:flex;gap:8px;margin-top:9px;flex-wrap:wrap;align-items:center}
  .grp{display:flex;gap:6px;align-items:center;flex-wrap:wrap;border:1px dashed var(--line);border-radius:8px;padding:6px 8px}
  .grp b{font-size:12px;color:var(--mut);font-weight:600}
  .empty{color:var(--mut);text-align:center;padding:50px 0} .hint{color:var(--mut);font-size:12px}
</style></head>
<body>
<header>
  <h1>自动化截流 · 控制台</h1>
  <div class="tabs">
    <span class="tab on" id="tab-q" onclick="show('q')">待处理</span>
    <span class="tab" id="tab-f" onclick="show('f')">跟进中</span>
  </div>
  <span class="stat" id="stat">加载中…</span>
  <span style="flex:1"></span>
  <button onclick="rescore()">重新打分</button>
  <button onclick="reload()">刷新</button>
  <span class="banner">发送由你手动完成 · 本工具不自动发评论</span>
</header>
<main id="list"></main>
<script>
let TAB='q';
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function show(t){TAB=t;document.getElementById('tab-q').classList.toggle('on',t==='q');
  document.getElementById('tab-f').classList.toggle('on',t==='f');reload();}
function reload(){TAB==='q'?loadQueue():loadFollowups();}
function card(b){return b.closest('.card');}

async function loadQueue(){
  const items = await (await fetch('/api/queue')).json();
  document.getElementById('stat').textContent =
    `待处理 ${items.length} 条 · 高 ${items.filter(x=>x.priority=='high').length} / 中 ${items.filter(x=>x.priority=='mid').length} / 低 ${items.filter(x=>x.priority=='low').length}`;
  const list=document.getElementById('list');
  if(!items.length){list.innerHTML='<div class="empty">队列为空。先跑 <code>python3 score.py</code> 或点「重新打分」。</div>';return;}
  list.innerHTML='';
  for(const it of items){
    const isCm = it.lead_type==='commenter';
    const el=document.createElement('div');el.className='card';el.dataset.id=it.id;
    el.innerHTML=`
      <div class="row1">
        <span class="badge ${it.priority}">${(it.priority||'').toUpperCase()}</span>
        <span class="score">分 ${it.score}</span>
        <span class="tag">${esc(it.platform)}</span>
        ${isCm?'<span class="tag cm">评论者·可直接回复</span>':''}
        ${it.ip_location?'<span class="tag">'+esc(it.ip_location)+'</span>':''}
      </div>
      <div class="title">${esc(isCm?it.target:it.title)||'(无标题)'}</div>
      <a class="link" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.url)}</a>
      <div class="meta">命中：${esc(it.matched_keywords)||'-'} ｜ 意图：${esc(it.intent_hits)||'-'}</div>
      <textarea>${esc(it.comment_text)}</textarea>
      <div class="hint">↑ 把 {hook} 换成针对${isCm?'该评论者':'该帖'}的具体一句，再酌情改两句，避免千篇一律。</div>
      <div class="acts">
        <button class="primary" onclick="openAndCopy(this)">打开链接 + 复制草稿</button>
        <button onclick="mark(this,'posted')">已发布</button>
        <button onclick="mark(this,'skipped')">跳过</button>
        <button onclick="mark(this,'failed')">失败</button>
      </div>`;
    list.appendChild(el);
  }
}

async function loadFollowups(){
  const items = await (await fetch('/api/followups')).json();
  const deal=items.filter(x=>x.stage==='deal').length;
  document.getElementById('stat').textContent =
    `跟进中 ${items.length} 条 · 成交 ${deal} · 未核验可见性 ${items.filter(x=>!x.visible).length}`;
  const list=document.getElementById('list');
  if(!items.length){list.innerHTML='<div class="empty">还没有已发出的线索。在「待处理」里标记「已发布」后会出现在这里。</div>';return;}
  const SL={posted:'已发出',replied:'对方回复',added:'加到私域',quoted:'已报价',deal:'成交'};
  list.innerHTML='';
  for(const it of items){
    const isCm=it.lead_type==='commenter';
    const vis = it.visible==='yes'?'<span class="tag vis-yes">对外可见</span>'
      : it.visible==='no'?'<span class="tag vis-no">被折叠/限流</span>'
      : '<span class="tag">可见性未核验</span>';
    const el=document.createElement('div');el.className='card';el.dataset.id=it.id;
    el.innerHTML=`
      <div class="row1">
        <span class="badge ${it.priority}">${(it.priority||'').toUpperCase()}</span>
        <span class="tag">${esc(it.platform)}</span>
        ${isCm?'<span class="tag cm">评论者</span>':''}
        <span class="tag">阶段：${SL[it.stage]||it.stage}</span>
        ${vis}
        ${it.deal_amount?'<span class="tag vis-yes">¥'+esc(''+it.deal_amount)+'</span>':''}
      </div>
      <div class="title">${esc(isCm?it.target:it.title)||'(无标题)'}</div>
      <a class="link" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.url)}</a>
      <div class="grp"><b>可见性核验</b>
        <button onclick="recheck(this)">无痕打开核验</button>
        <button onclick="vis(this,'yes')">对外可见</button>
        <button onclick="vis(this,'no')">被折叠/限流</button>
      </div>
      <div class="grp"><b>推进阶段</b>
        <button onclick="adv(this,'replied')">对方回复了</button>
        <button onclick="adv(this,'added')">加到私域</button>
        <button onclick="adv(this,'quoted')">已报价</button>
        <input class="amt" placeholder="成交额¥"> <button class="primary" onclick="deal(this)">成交</button>
      </div>
      <div class="acts"><span class="hint">用哪个号触达：</span><input class="acc" placeholder="账号标签" value="${esc(it.account||'')}" onchange="setacc(this)"></div>`;
    list.appendChild(el);
  }
}

async function post(url,body){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
async function openAndCopy(b){const c=card(b);try{await navigator.clipboard.writeText(c.querySelector('textarea').value);}catch(e){}
  window.open(c.querySelector('a.link').href,'_blank','noopener');}
async function mark(b,status){const c=card(b);const note=status==='failed'?(prompt('失败原因（可留空）：')||''):'';
  await post('/api/mark',{id:c.dataset.id,status,comment_text:c.querySelector('textarea').value,note});
  c.style.opacity=.35;c.querySelectorAll('button,textarea').forEach(x=>x.disabled=true);setTimeout(loadQueue,250);}
function recheck(b){window.open(card(b).querySelector('a.link').href,'_blank','noopener');
  alert('在弹出的标签页里【退出登录/用无痕窗口】看看你的评论还在不在，然后回来点「对外可见」或「被折叠/限流」。');}
async function vis(b,v){await post('/api/visibility',{id:card(b).dataset.id,visible:v});loadFollowups();}
async function adv(b,stage){await post('/api/advance',{id:card(b).dataset.id,stage});loadFollowups();}
async function deal(b){const c=card(b);const amt=c.querySelector('input.amt').value.trim();
  await post('/api/advance',{id:c.dataset.id,stage:'deal',deal_amount:amt?parseFloat(amt):null});loadFollowups();}
async function setacc(inp){await post('/api/advance',{id:card(inp).dataset.id,account:inp.value});}
async function rescore(){document.getElementById('stat').textContent='重新打分中…';
  await post('/api/rescore',{});reload();}
reload();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path.startswith("/api/queue"):
            return self._send(200, json.dumps(get_queue("new"), ensure_ascii=False))
        if self.path.startswith("/api/followups"):
            return self._send(200, json.dumps(get_followups(), ensure_ascii=False))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        # 轻量防跨站：只接受来自本机控制台的写操作
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return self._send(403, json.dumps({"error": "forbidden host"}))
        try:
            if self.path.startswith("/api/mark"):
                d = self._body()
                row = mark_processed(d["id"], d["status"], d.get("comment_text"), d.get("note", ""))
                return self._send(200, json.dumps({"ok": bool(row)}, ensure_ascii=False))
            if self.path.startswith("/api/visibility"):
                d = self._body()
                ok = set_visibility(d["id"], d["visible"])
                return self._send(200, json.dumps({"ok": bool(ok)}, ensure_ascii=False))
            if self.path.startswith("/api/advance"):
                d = self._body()
                amt = d.get("deal_amount")
                ok = advance_stage(d["id"], d.get("stage"), deal_amount=amt,
                                   account=d.get("account"), note=d.get("note"))
                return self._send(200, json.dumps({"ok": bool(ok)}, ensure_ascii=False))
            if self.path.startswith("/api/rescore"):
                out = subprocess.run([sys.executable, str(ROOT / "score.py")],
                                     capture_output=True, text=True, cwd=str(ROOT))
                return self._send(200, json.dumps({"ok": True, "stdout": out.stdout}, ensure_ascii=False))
        except Exception as e:
            return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    load_config()
    init_db()
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
