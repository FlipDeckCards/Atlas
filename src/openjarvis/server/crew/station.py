"""The Station: a live picture of the crew, drawn entirely in code (no image files, no borrowed art).

Helix's core sits in the middle; every mission is a room around it, joined by a corridor; every task is a crew member in its room.
The picture shows only what /api/crew/state says: a running task works at its station, a waiting task walks the corridor to Helix
with a question, a finished one shows a check. Nothing is animated that is not happening.
"""

STATION_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
<title>Aibussol Station</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet"/>
<style>
  :root { --cyan:#00e5ff; --cyan-dim:#005f6b; --orange:#ff6d00; --green:#00ff88; --red:#ff3344; --violet:#b06cff;
          --bg:#050508; --panel:#08080f; --border:#0d2535; --text:#8ecfe0; --text-dim:#3d6a7a; }
  * { box-sizing:border-box; margin:0; padding:0; }
  html, body { height:100%; }
  body { background:var(--bg); color:var(--text); font-family:'Share Tech Mono',monospace; display:grid; grid-template-rows:48px 1fr; overflow:hidden; }
  button { font-family:inherit; }
  #top { display:flex; align-items:center; gap:14px; padding:0 16px; background:var(--panel); border-bottom:1px solid var(--border); min-width:0; }
  #brand { font-family:'Orbitron',monospace; font-weight:900; letter-spacing:4px; color:var(--cyan); text-shadow:0 0 18px var(--cyan); font-size:15px; white-space:nowrap; }
  #brand span { color:var(--orange); }
  #brand small { font-size:9px; letter-spacing:3px; color:var(--text-dim); text-shadow:none; margin-left:8px; }
  .chip { font-size:10px; letter-spacing:1px; color:var(--text-dim); white-space:nowrap; }
  .chip b { color:var(--cyan); font-weight:400; }
  .spacer { flex:1; }
  .tbtn { background:transparent; border:1px solid var(--border); color:var(--text-dim); padding:5px 9px; font-size:9px; letter-spacing:2px; cursor:pointer; border-radius:2px; white-space:nowrap; }
  .tbtn:hover { border-color:var(--cyan); color:var(--cyan); }
  .tbtn.on { border-color:var(--cyan); color:var(--cyan); background:rgba(0,229,255,0.08); }
  .tbtn.warn.on { border-color:var(--orange); color:var(--orange); background:rgba(255,109,0,0.08); }
  #main { display:grid; grid-template-columns:300px 1fr 360px; min-height:0; }
  .col { background:var(--panel); overflow-y:auto; min-height:0; scrollbar-width:thin; scrollbar-color:var(--cyan-dim) transparent; }
  #left { border-right:1px solid var(--border); }
  #right { border-left:1px solid var(--border); }
  #center { display:flex; flex-direction:column; min-width:0; min-height:0; }
  #center #stage { flex:1 1 auto; }
  #center #chat { flex:0 0 auto; background:var(--panel); border-top:1px solid var(--border); border-bottom:none; }
  #stage { position:relative; min-width:0; min-height:0; overflow:hidden; }
  #cv { position:absolute; inset:0; width:100%; height:100%; display:block; cursor:default; }
  #hint { position:absolute; left:14px; bottom:12px; font-size:10px; color:var(--text-dim); letter-spacing:1px; pointer-events:none; }
  .sec { padding:14px 16px; border-bottom:1px solid var(--border); }
  .lbl { font-family:'Orbitron',monospace; font-size:9px; letter-spacing:3px; color:var(--text-dim); text-transform:uppercase; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .lbl .act { font-family:'Share Tech Mono',monospace; letter-spacing:1px; text-transform:none; }
  .h { color:var(--cyan); font-size:13px; margin-bottom:4px; overflow-wrap:anywhere; }
  .dim { color:var(--text-dim); font-size:11px; line-height:1.6; overflow-wrap:anywhere; }
  .row { display:flex; gap:8px; align-items:center; padding:6px 0; border-bottom:1px solid rgba(0,229,255,0.05); font-size:11px; }
  .row:last-child { border-bottom:none; }
  .dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
  .grow { flex:1; min-width:0; overflow-wrap:anywhere; }
  .tag { font-size:9px; letter-spacing:1px; padding:1px 5px; border:1px solid var(--border); border-radius:2px; color:var(--text-dim); white-space:nowrap; text-transform:uppercase; }
  .card { border:1px solid rgba(255,109,0,0.35); background:rgba(255,109,0,0.05); padding:10px; margin-bottom:10px; border-radius:2px; }
  .card.q { border-color:rgba(0,229,255,0.3); background:rgba(0,229,255,0.04); }
  .card .t { font-size:12px; line-height:1.55; color:#ffd2b0; margin-bottom:8px; overflow-wrap:anywhere; }
  .card.q .t { color:var(--text); }
  .card .k { font-size:9px; letter-spacing:2px; color:var(--orange); margin-bottom:6px; text-transform:uppercase; }
  .card.q .k { color:var(--cyan); }
  .btns { display:flex; gap:6px; flex-wrap:wrap; }
  .b { background:transparent; border:1px solid var(--cyan); color:var(--cyan); padding:6px 10px; font-size:10px; letter-spacing:1px; cursor:pointer; border-radius:2px; }
  .b:hover { background:rgba(0,229,255,0.1); }
  .b.ok { border-color:var(--green); color:var(--green); }
  .b.ok:hover { background:rgba(0,255,136,0.1); }
  .b.no { border-color:var(--red); color:var(--red); }
  .b.no:hover { background:rgba(255,51,68,0.08); }
  .b:disabled { opacity:0.35; cursor:default; }
  input[type=text], input[type=number], textarea { width:100%; background:rgba(0,229,255,0.03); border:1px solid var(--border); color:var(--text); padding:8px 10px; font-family:inherit; font-size:12px; outline:none; border-radius:2px; }
  input:focus, textarea:focus { border-color:var(--cyan); }
  textarea { resize:vertical; min-height:70px; line-height:1.5; }
  .ans { display:flex; gap:6px; margin-top:6px; }
  .bar { height:4px; background:rgba(0,229,255,0.08); border-radius:2px; overflow:hidden; margin:6px 0 2px; }
  .bar i { display:block; height:100%; background:var(--cyan); }
  .bar i.hot { background:var(--orange); }
  .ev { font-size:10px; line-height:1.55; padding:4px 0; color:var(--text-dim); border-bottom:1px solid rgba(0,229,255,0.04); overflow-wrap:anywhere; }
  .ev b { color:var(--text); font-weight:400; }
  .ev .c { color:var(--orange); }
  #chat-log { height:190px; overflow-y:auto; display:flex; flex-direction:column; gap:8px; padding-right:2px; scrollbar-width:thin; scrollbar-color:var(--cyan-dim) transparent; }
  .cm { max-width:92%; padding:8px 10px; font-size:12px; line-height:1.5; border-radius:2px; white-space:pre-wrap; overflow-wrap:anywhere; }
  .cm.u { align-self:flex-end; background:rgba(255,109,0,0.08); border:1px solid rgba(255,109,0,0.25); color:#ffa040; }
  .cm.a { align-self:flex-start; background:rgba(0,229,255,0.04); border:1px solid rgba(0,229,255,0.14); }
  .cm .tg { font-size:9px; letter-spacing:2px; opacity:0.55; margin-bottom:3px; }
  .agent { border:1px solid var(--border); border-radius:2px; padding:9px 10px; margin-bottom:8px; background:rgba(0,229,255,0.02); }
  .agent-top { display:flex; align-items:center; gap:7px; margin-bottom:5px; }
  .agent-name { font-family:'Orbitron',monospace; font-size:11px; letter-spacing:2px; font-weight:700; }
  .agent-can { font-size:11px; line-height:1.5; color:var(--text); }
  .agent-cannot { font-size:10px; line-height:1.5; color:var(--text-dim); margin-top:2px; }
  .agent-st { font-size:10px; letter-spacing:1px; color:var(--text-dim); margin-top:5px; text-transform:uppercase; }
  .msg { font-size:11px; min-height:14px; margin-top:6px; color:var(--green); overflow-wrap:anywhere; }
  .msg.err { color:var(--red); }
  a { color:var(--cyan); }
  #gate { position:fixed; inset:48px 0 0 0; display:none; align-items:center; justify-content:center; background:rgba(5,5,8,0.92); z-index:5; padding:20px; }
  #gate .box { max-width:460px; border:1px solid var(--cyan-dim); padding:28px; background:var(--panel); line-height:1.7; font-size:12px; }
  #gate .box .h { font-family:'Orbitron',monospace; letter-spacing:3px; font-size:12px; margin-bottom:12px; }
  @media (max-width: 820px) {
    body { grid-template-rows:auto 1fr; height:100dvh; }
    #top { flex-wrap:wrap; padding:8px 12px; gap:8px; }
    .hide-m, #hint { display:none; }
    #main { display:flex; flex-direction:column; overflow-y:auto; }
    #center { order:1; flex:0 0 auto; }
    #center #stage { height:50dvh; flex:0 0 auto; }
    #right { order:2; border-left:none; border-top:1px solid var(--border); overflow:visible; }
    #left { order:3; border-right:none; border-top:1px solid var(--border); overflow:visible; }
    #chat-log { height:210px; }
    #gate { inset:0; }
  }
</style>
</head>
<body>
<div id="top">
  <div id="brand">AIBUS<span>SOL</span><small>STATION</small></div>
  <span class="chip hide-m">RUNNING <b id="c-run">0</b></span>
  <span class="chip">TODAY <b id="c-spend">$0.00</b></span>
  <span class="chip hide-m" id="c-state"></span>
  <span class="spacer"></span>
  <button class="tbtn" id="t-night" title="Let the crew keep working through quiet hours">NIGHT SHIFT</button>
  <button class="tbtn warn" id="t-pause" title="Stop the crew from starting any new work">PAUSE ALL</button>
  <button class="tbtn" onclick="location.href='/'">← HELIX</button>
</div>
<div id="main">
  <div id="left" class="col"><div id="roster-box"></div></div>
  <div id="center">
    <div id="stage"><canvas id="cv"></canvas><div id="hint">Tap Helix for your inbox · a factory for its work · a dock for connections</div></div>
    <div id="chat" class="sec">
      <div class="lbl"><span>Helix</span><span class="act">same chat as the main screen</span></div>
      <div id="chat-log"></div>
      <div class="ans"><input id="chat-in" type="text" maxlength="8000" placeholder='Tell Helix… e.g. "build me…"' autocomplete="off"/><button class="b" id="chat-send">Send</button></div>
    </div>
  </div>
  <div id="right" class="col"><div id="links-box"></div><div id="side-body"></div></div>
</div>
<div id="gate"><div class="box"><div class="h" id="gate-h">STATION OFFLINE</div><div id="gate-t"></div></div></div>

<script>
const TOKEN_KEY = 'aibussol_token';
const token = () => localStorage.getItem(TOKEN_KEY) || '';
const PROJECT_COLORS = { flipdeck:'#ff6d00', marnyx:'#b06cff', deadzone:'#ff3344', aibussol:'#00e5ff', 'new':'#00ff88' };
const ROLE_COLORS = { lead:'#00e5ff', researcher:'#5ad1ff', builder:'#4dffc3', writer:'#ffc14d', designer:'#ff7ad9', operator:'#ff6d00' };
const STATUS_COLORS = { queued:'#2a5060', running:'#00e5ff', waiting:'#ff6d00', done:'#00ff88', failed:'#ff3344', cancelled:'#2a3a44' };
let S = null, selected = 'hub', lastDraw = 0, pollTimer = null;

function mk(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function money(x) { return '$' + Number(x || 0).toFixed(2); }
function ago(iso) { if (!iso) return ''; const s = (Date.now() - new Date(iso).getTime()) / 1000; if (s < 60) return 'just now'; if (s < 3600) return Math.floor(s/60) + 'm ago'; if (s < 86400) return Math.floor(s/3600) + 'h ago'; return Math.floor(s/86400) + 'd ago'; }
function projColor(p) { return PROJECT_COLORS[String(p || '').toLowerCase()] || '#00e5ff'; }

async function api(path, method, body) {
  const r = await fetch(path, { method: method || 'GET', headers: { 'Authorization': 'Bearer ' + token(), 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data: d };
}

function gate(title, text) {
  document.getElementById('gate').style.display = title ? 'flex' : 'none';
  if (!title) return;
  document.getElementById('gate-h').textContent = title;
  const t = document.getElementById('gate-t'); t.textContent = '';
  t.appendChild(document.createTextNode(text + ' '));
  const a = mk('a', null, 'Back to Helix'); a.href = '/'; t.appendChild(a);
}

async function refresh() {
  if (!token()) { gate('SIGN IN FIRST', 'Open Aibussol and sign in, then come back to the Station.'); return; }
  const r = await api('/api/crew/state').catch(() => null);
  if (!r) { setState('NO CONNECTION'); return; }
  if (r.status === 401) { gate('SIGN IN FIRST', 'Your session ended. Sign in again in Aibussol.'); return; }
  if (r.status === 403) { gate('OWNER ONLY', 'The crew is for the owner account.'); return; }
  if (!r.data.enabled) { gate('CREW IS OFF', r.data.detail || 'The crew is not enabled on the server.'); return; }
  gate(null);
  S = r.data;
  document.getElementById('c-run').textContent = S.running;
  document.getElementById('c-spend').textContent = money(S.spend.today_usd) + ' / ' + money(S.spend.daily_cap_usd);
  setState(S.blocked ? 'HOLD: ' + String(S.blocked).toUpperCase() : 'CREW READY');
  document.getElementById('t-night').classList.toggle('on', !!S.settings.night_shift);
  document.getElementById('t-pause').classList.toggle('on', !!S.settings.paused);
  document.getElementById('t-pause').textContent = S.settings.paused ? 'RESUME ALL' : 'PAUSE ALL';
  if (selected !== 'hub' && selected !== 'links' && !String(selected).startsWith('f:') && !S.missions.find(m => m.id === selected)) selected = 'hub';
  layout();
  renderSide();
}
function setState(t) { document.getElementById('c-state').textContent = t; }

document.getElementById('t-night').onclick = async () => { if (!S) return; await api('/api/crew/settings', 'POST', { night_shift: !S.settings.night_shift }); refresh(); };
document.getElementById('t-pause').onclick = async () => { if (!S) return; await api('/api/crew/settings', 'POST', { paused: !S.settings.paused }); refresh(); };

// ── side panel ───────────────────────────────────────────────────────────────────────────────
function renderSide(force) {
  const side = document.getElementById('side-body');
  const ae = document.activeElement;
  // never rebuild the panel under someone's fingers: wait until they leave the field they are typing in
  if (!force && ae && side.contains(ae) && /INPUT|TEXTAREA/.test(ae.tagName) && ae.value) { ae.onblur = () => renderSide(true); return; }
  const focused = document.activeElement && document.activeElement.id;
  side.textContent = '';
  document.getElementById('roster-box').replaceChildren(rosterSec());
  document.getElementById('links-box').replaceChildren(linksSec());
  if (selected === 'hub' || selected === 'links') side.append(inboxSec(), missionsSec(), ledgerSec(S.events, 'Station Ledger'));
  else if (String(selected).startsWith('f:')) side.append(backSec(), factorySec(selected.slice(2)));
  else {
    const m = S.missions.find(x => x.id === selected);
    side.append(backSec(), missionSec(m), inboxSec(m.id), outboxSec(m), ledgerSec(S.events.filter(e => e.mission_id === m.id), 'Mission Ledger'));
  }
  if (focused) { const el = document.getElementById(focused); if (el) el.focus(); }
}

function backSec() {
  const s = mk('div', 'sec'); const b = mk('button', 'b', '← Helix inbox'); b.onclick = () => { selected = 'hub'; renderSide(); }; s.append(b); return s;
}

function inboxSec(missionId) {
  const s = mk('div', 'sec');
  const qs = S.questions.filter(q => !missionId || q.mission_id === missionId);
  const l = mk('div', 'lbl'); l.append(mk('span', null, missionId ? 'Waiting on you' : 'Helix inbox'), mk('span', 'act', qs.length ? qs.length + ' open' : 'all clear'));
  s.append(l);
  if (!qs.length) { s.append(mk('div', 'dim', missionId ? 'Nothing from this mission needs you.' : 'Nothing needs you. Questions, approvals and budget requests from the crew land here and in Helix chat.')); return s; }
  qs.forEach(q => s.append(questionCard(q)));
  return s;
}

function questionCard(q) {
  const m = S.missions.find(x => x.id === q.mission_id);
  const isQ = q.kind === 'question';
  const c = mk('div', 'card' + (isQ ? ' q' : ''));
  c.append(mk('div', 'k', (q.kind === 'checkpoint' ? 'Approval needed' : q.kind === 'budget' ? 'Budget request' : 'Question') + (m ? ' · ' + (m.title || 'mission') : '')));
  c.append(mk('div', 't', q.text));
  const msg = mk('div', 'msg');
  const send = async (body, btns) => {
    btns.forEach(b => b.disabled = true);
    const r = await api('/api/crew/questions/' + encodeURIComponent(q.id) + '/answer', 'POST', body);
    msg.className = 'msg' + (r.ok ? '' : ' err'); msg.textContent = r.ok ? (r.data.message || 'Done.') : (r.data.detail || 'Failed.');
    setTimeout(refresh, 700);
  };
  const btns = mk('div', 'btns'); const all = [];
  if (isQ) {
    (q.options || []).forEach(o => { const b = mk('button', 'b', o); b.onclick = () => send({ answer: o }, all); all.push(b); btns.append(b); });
    const row = mk('div', 'ans'); const inp = mk('input'); inp.type = 'text'; inp.placeholder = 'Or type an answer…'; inp.maxLength = 2000;
    const go = mk('button', 'b', 'Send'); go.onclick = () => { if (inp.value.trim()) send({ answer: inp.value.trim() }, all.concat([go])); };
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') go.click(); });
    row.append(inp, go); c.append(btns, row);
  } else {
    const ok = mk('button', 'b ok', 'Approve'), no = mk('button', 'b no', 'Decline'); all.push(ok, no);
    ok.onclick = () => send({ approve: true }, all); no.onclick = () => send({ approve: false }, all);
    btns.append(ok, no); c.append(btns);
  }
  c.append(msg);
  return c;
}

function rosterSec() {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Crew roster'), mk('span', 'act', (S.roster || []).length + ' on the station')); s.append(l);
  (S.roster || []).forEach(a => {
    const c = mk('div', 'agent');
    const top = mk('div', 'agent-top'); const d = mk('span', 'dot'); d.style.background = ROLE_COLORS[a.role] || '#00e5ff';
    const nm = mk('span', 'agent-name', a.name); nm.style.color = ROLE_COLORS[a.role] || '#00e5ff';
    top.append(d, nm, mk('span', 'tag', a.role === 'lead' ? 'lead' : a.role), mk('span', 'grow'), mk('span', 'dim', a.model));
    c.append(top, mk('div', 'agent-can', a.can), mk('div', 'agent-cannot', 'Limits: ' + a.cannot));
    const st = a.working ? a.working + ' working' : a.waiting ? 'waiting on you' : 'standing by';
    const sl = mk('div', 'agent-st', st + (a.done ? ' · ' + a.done + ' done' : '')); if (a.working) sl.style.color = '#00ff88'; else if (a.waiting) sl.style.color = '#ff6d00';
    c.append(sl); s.append(c);
  });
  return s;
}

function factorySec(key) {
  const b = bays.find(x => x.key === key) || factories().find(x => x.key === key);
  const s = mk('div', 'sec');
  if (!b) { s.append(mk('div', 'dim', 'That factory is gone.')); return s; }
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Factory'), mk('span', 'tag', b.state)); s.append(l);
  const h = mk('div', 'h', b.label); h.style.color = b.color; s.append(h);
  if (!b.missions.length) { s.append(mk('div', 'dim', 'Idle. Tell Helix what to build for ' + (b.isNew ? 'this business' : b.label.charAt(0) + b.label.slice(1).toLowerCase()) + ' and the crew will work here.')); return s; }
  s.append(mk('div', 'dim', b.missions.length + ' mission(s). Tap one for its crew, files and ledger.'));
  b.missions.forEach(m => {
    const r = mk('div', 'row'); r.style.cursor = 'pointer'; r.onclick = () => { selected = m.id; renderSide(true); };
    const done = m.tasks.filter(t => t.status === 'done').length;
    r.append(mk('span', 'grow', m.title || m.goal), mk('span', 'tag', m.status), mk('span', 'dim', done + '/' + m.tasks.length));
    s.append(r);
  });
  return s;
}

function missionsSec() {
  const s = mk('div', 'sec');
  s.append(Object.assign(mk('div', 'lbl'), { textContent: 'Missions' }));
  if (!S.missions.length) { s.append(mk('div', 'dim', 'No missions yet.')); return s; }
  S.missions.forEach(m => {
    const r = mk('div', 'row'); r.style.cursor = 'pointer'; r.onclick = () => { selected = m.id; renderSide(); };
    const d = mk('span', 'dot'); d.style.background = projColor(m.project);
    const done = m.tasks.filter(t => t.status === 'done').length;
    r.append(d, mk('span', 'grow', m.title || m.goal), mk('span', 'tag', m.status), mk('span', 'dim', done + '/' + m.tasks.length));
    s.append(r);
  });
  return s;
}

function missionSec(m) {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Mission'), mk('span', 'tag', m.status)); s.append(l);
  s.append(mk('div', 'h', m.title || 'Untitled mission'), mk('div', 'dim', m.goal));
  const pct = m.budget_usd ? Math.min(100, m.spent_usd / m.budget_usd * 100) : 0;
  const bar = mk('div', 'bar'); const i = mk('i', pct > 80 ? 'hot' : ''); i.style.width = pct + '%'; bar.append(i);
  s.append(bar, mk('div', 'dim', money(m.spent_usd) + ' of ' + money(m.budget_usd) + (m.project ? ' · ' + m.project : '') + ' · started ' + ago(m.created_at)));
  const btns = mk('div', 'btns'); btns.style.marginTop = '10px';
  const act = (a, label, cls) => { const b = mk('button', 'b' + (cls ? ' ' + cls : ''), label); b.onclick = async () => { if (a === 'cancel' && !confirm('Cancel this mission? Its open work stops.')) return; await api('/api/crew/missions/' + m.id + '/' + a, 'POST'); refresh(); }; btns.append(b); };
  if (m.status === 'active' || m.status === 'planning') act('pause', 'Pause');
  if (m.status === 'paused' || m.status === 'stalled') act('resume', 'Resume');
  if (!['done', 'cancelled'].includes(m.status)) act('cancel', 'Cancel', 'no');
  s.append(btns);
  const tl = mk('div', 'lbl'); tl.style.marginTop = '14px'; tl.textContent = 'Crew'; s.append(tl);
  if (!m.tasks.length) s.append(mk('div', 'dim', m.status === 'planning' ? 'Helix is planning the tasks…' : 'No tasks.'));
  m.tasks.forEach(t => {
    const r = mk('div', 'row'); const d = mk('span', 'dot'); d.style.background = STATUS_COLORS[t.status] || '#2a5060';
    const g = mk('div', 'grow'); g.append(mk('div', null, (t.idx + 1) + '. ' + t.title));
    if (t.result && (t.status === 'done' || t.status === 'failed')) g.append(mk('div', 'dim', t.result.slice(0, 260)));
    r.append(d, g, mk('span', 'tag', t.role), mk('span', 'tag', t.status));
    s.append(r);
  });
  if (m.summary && m.status !== 'done') s.append(mk('div', 'dim', m.summary));
  return s;
}

function outboxSec(m) {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Outbox'), mk('span', 'act', m.outputs.length + ' file(s)')); s.append(l);
  if (!m.outputs.length) { s.append(mk('div', 'dim', 'Finished files land here.')); return s; }
  m.outputs.forEach(o => {
    const r = mk('div', 'row'); const b = mk('button', 'b', 'Download');
    b.onclick = async () => {
      const res = await fetch('/api/crew/outputs/' + o.id, { headers: { 'Authorization': 'Bearer ' + token() } });
      if (!res.ok) return; const blob = await res.blob(); const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = o.name; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    };
    r.append(mk('span', 'grow', o.name), mk('span', 'dim', (o.size / 1024).toFixed(1) + ' KB'), b); s.append(r);
  });
  return s;
}

function linksSec() {
  const s = mk('div', 'sec');
  s.append(Object.assign(mk('div', 'lbl'), { textContent: 'Connections' }));
  Object.entries(S.connections || {}).forEach(([key, c]) => {
    const r = mk('div', 'row'); const d = mk('span', 'dot'); d.style.background = c.connected ? '#00ff88' : c.built ? '#ff6d00' : '#2a3a44';
    const g = mk('div', 'grow'); g.append(mk('div', null, c.label + (c.connected ? ' · connected' : c.built ? ' · not connected' : ' · not built yet')));
    if (!c.connected && c.setup) g.append(mk('div', 'dim', c.setup));
    r.append(d, g);
    if (key === 'etsy' && c.can_connect) {
      const b = mk('button', 'b', 'Connect'); b.onclick = async () => { const x = await api('/api/crew/connect/etsy'); if (x.ok && x.data.url) location.href = x.data.url; else alert(x.data.detail || 'Etsy cannot be connected yet.'); };
      r.append(b);
    }
    if (key === 'etsy' && c.connected) {
      const b = mk('button', 'b no', 'Disconnect'); b.onclick = async () => { if (confirm('Forget the Etsy connection?')) { await api('/api/crew/connect/etsy/disconnect', 'POST'); refresh(); } };
      r.append(b);
    }
    s.append(r);
  });
  s.append(mk('div', 'dim', 'Official APIs only. Read and draft actions run on their own; anything public, paid or permanent waits for your Approve.'));
  return s;
}

function ledgerSec(events, title) {
  const s = mk('div', 'sec');
  s.append(Object.assign(mk('div', 'lbl'), { textContent: title }));
  if (!events.length) { s.append(mk('div', 'dim', 'Nothing yet.')); return s; }
  events.slice(0, 40).forEach(e => {
    const d = mk('div', 'ev'); d.append(mk('span', null, ago(e.created_at) + ' · '), mk('b', null, e.text));
    if (e.cost_usd > 0) d.append(mk('span', 'c', '  ' + money(e.cost_usd)));
    s.append(d);
  });
  return s;
}

// ── the station picture ──────────────────────────────────────────────────────────────────────
// A fixed station: Helix's core in the middle, one factory per product (always there, idle until it has work),
// a new factory for each new business the crew starts, and a dock for each outside platform (lit when connected).
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let W = 0, H = 0, DPR = 1, hub = { x: 0, y: 0, r: 60 }, stars = [], bays = [], pods = [];
const FACTORIES = [
  { key: 'flipdeck', label: 'FLIPDECK', color: '#ff6d00' },
  { key: 'marnyx', label: 'MARNYX', color: '#b06cff' },
  { key: 'deadzone', label: 'DEADZONE', color: '#ff3344' },
  { key: 'aibussol', label: 'AIBUSSOL', color: '#00e5ff' },
];
const DOCKS = [['stripe', 'STRIPE', -1, -1], ['etsy', 'ETSY', 1, -1], ['google_play', 'PLAY', -1, 1], ['app_store', 'APPLE', 1, 1]];   // [key, label, left/right, above/below Helix]
const WORKING = ['active', 'planning', 'paused', 'stalled'];

function resize() {
  const r = cv.getBoundingClientRect(); DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = r.width; H = r.height; cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR); ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  stars = Array.from({ length: Math.round(W * H / 2600) }, () => ({ x: Math.random() * W, y: Math.random() * H, z: Math.random(), p: Math.random() * 6.28 }));
  layout();
}
window.addEventListener('resize', resize);

function factoryKey(m) {
  const p = String(m.project || '').toLowerCase().replace(/[^a-z]/g, '');
  if (FACTORIES.some(f => f.key === p)) return p;
  return 'new:' + String(m.title || m.goal || m.id).toLowerCase().slice(0, 40);
}

function factories() {
  const ms = S ? S.missions.filter(m => m.status !== 'cancelled') : [];
  const fill = b => {
    b.missions = ms.filter(m => factoryKey(m) === b.key);
    const current = b.missions.filter(m => WORKING.includes(m.status));
    b.tasks = current.flatMap(m => m.tasks.filter(t => t.status !== 'cancelled'));
    b.planning = current.some(m => m.status === 'planning');
    b.title = current.length ? (current[0].title || current[0].goal) : '';
    b.state = b.tasks.some(t => t.status === 'waiting') ? 'NEEDS YOU' : b.tasks.some(t => t.status === 'running') ? 'WORKING'
      : b.planning ? 'PLANNING' : current.length ? 'QUEUED' : b.missions.length ? 'DONE' : 'IDLE';
    return b;
  };
  const fixed = FACTORIES.map(f => fill(Object.assign({}, f)));
  const seen = [];
  ms.forEach(m => { const k = factoryKey(m); if (k.startsWith('new:') && !seen.includes(k)) seen.push(k); });
  const fresh = seen.slice(0, 4).map(k => {
    const m = ms.find(x => factoryKey(x) === k);
    return fill({ key: k, label: String(m.title || 'NEW BUSINESS').toUpperCase(), color: '#00ff88', isNew: true });
  });
  return fixed.concat(fresh);
}

function layout() {
  const small = Math.min(W, H), g = Math.max(10, small * 0.03);
  hub = { x: W / 2, y: H / 2, r: Math.max(28, small * 0.095) };
  const narrow = W < 600;
  const fw = narrow ? Math.max(112, W * 0.31) : Math.max(130, Math.min(W * 0.34, 320)), fh = Math.max(96, Math.min(H * (narrow ? 0.29 : 0.32), 200));
  const corners = [[g, g], [W - fw - g, g], [g, H - fh - g], [W - fw - g, H - fh - g]];
  const list = factories();
  const midW = Math.max(110, Math.min(W - 2 * fw - 4 * g, 220)), midH = Math.max(70, Math.min(H - 2 * fh - 4 * g, 130));
  const mids = [[g, H / 2 - midH / 2, fw * 0.8, midH], [W - fw * 0.8 - g, H / 2 - midH / 2, fw * 0.8, midH],
                [W / 2 - midW / 2, g, midW, fh * 0.75], [W / 2 - midW / 2, H - fh * 0.75 - g, midW, fh * 0.75]];
  bays = list.map((b, i) => {
    const [x, y, w, h] = i < 4 ? [corners[i][0], corners[i][1], fw, fh] : mids[i - 4];
    return Object.assign(b, { x, y, w, h, cx: x + w / 2, cy: y + h / 2 });
  });
  const pw = narrow ? 46 : Math.max(62, Math.min(84, hub.r * 1.3)), ph = 18;
  pods = DOCKS.map(([key, label, side, row]) => {
    const cx = hub.x + side * (pw / 2 + 5), cy = row < 0 ? hub.y - hub.r - 34 : hub.y + hub.r + 50;
    const c = S && S.connections ? S.connections[key] : null;
    return { key, label, cx, cy, x: cx - pw / 2, y: cy - ph / 2, w: pw, h: ph, connected: !!(c && c.connected), built: !!(c && c.built) };
  });
}

function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function alpha(hex, a) { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')'; }
function inRect(p, x, y) { return x >= p.x && x <= p.x + p.w && y >= p.y && y <= p.y + p.h; }

function drawStars(t) {
  for (const s of stars) {
    const tw = 0.35 + 0.65 * Math.abs(Math.sin(t * 0.0006 * (0.4 + s.z) + s.p));
    ctx.fillStyle = 'rgba(160,220,255,' + (0.15 + 0.5 * s.z) * tw + ')';
    const x = (s.x + t * 0.004 * s.z) % W; ctx.fillRect(x, s.y, s.z > 0.85 ? 1.6 : 1, s.z > 0.85 ? 1.6 : 1);
  }
}

function corridorPoints(b) {
  const dx = b.cx - hub.x, dy = b.cy - hub.y, d = Math.hypot(dx, dy) || 1, ux = dx / d, uy = dy / d;
  const sx = hub.x + ux * (hub.r + 18), sy = hub.y + uy * (hub.r + 18);
  const tx = Math.abs(ux) > 1e-6 ? (b.w / 2) / Math.abs(ux) : 1e9, ty = Math.abs(uy) > 1e-6 ? (b.h / 2) / Math.abs(uy) : 1e9, k = Math.min(tx, ty);
  return { sx, sy, ex: b.cx - ux * k, ey: b.cy - uy * k };
}

function drawCorridor(b, t) {
  const { sx, sy, ex, ey } = corridorPoints(b);
  const live = b.tasks.some(x => x.status === 'running');
  ctx.strokeStyle = 'rgba(16,40,56,0.95)'; ctx.lineWidth = 14; ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke();
  ctx.strokeStyle = 'rgba(0,229,255,0.06)'; ctx.lineWidth = 14; ctx.setLineDash([2, 10]); ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke();
  ctx.strokeStyle = alpha(b.color, live ? 0.6 : 0.16); ctx.lineWidth = 1; ctx.setLineDash([4, 6]); ctx.lineDashOffset = live ? -t * 0.02 : 0;
  ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke(); ctx.setLineDash([]);
  if (live) {
    for (let k = 0; k < 3; k++) {
      const p = ((t * 0.00035 + k / 3) % 1); const x = ex + (sx - ex) * p, y = ey + (sy - ey) * p;
      ctx.fillStyle = alpha(b.color, 0.95); ctx.shadowColor = b.color; ctx.shadowBlur = 8; ctx.beginPath(); ctx.arc(x, y, 2.2, 0, 6.29); ctx.fill(); ctx.shadowBlur = 0;
    }
  }
}

function drawHub(t) {
  const { x, y, r } = hub; const open = S ? S.questions.length : 0;
  const g = ctx.createRadialGradient(x, y, r * 0.2, x, y, r * 1.7); g.addColorStop(0, 'rgba(0,229,255,0.16)'); g.addColorStop(1, 'rgba(0,229,255,0)');
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 1.7, 0, 6.29); ctx.fill();
  ctx.fillStyle = '#071019'; ctx.strokeStyle = 'rgba(0,229,255,0.5)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.29); ctx.fill(); ctx.stroke();
  ctx.save(); ctx.translate(x, y);
  ctx.rotate(t * 0.0003); ctx.strokeStyle = 'rgba(0,229,255,0.35)'; ctx.setLineDash([2, 9]); ctx.beginPath(); ctx.arc(0, 0, r + 8, 0, 6.29); ctx.stroke();
  ctx.rotate(-t * 0.0007); ctx.strokeStyle = open ? 'rgba(255,109,0,' + (0.45 + 0.4 * Math.sin(t * 0.005)) + ')' : 'rgba(255,109,0,0.18)';
  ctx.setLineDash([14, 10]); ctx.lineWidth = open ? 2 : 1; ctx.beginPath(); ctx.arc(0, 0, r + 15, 0, 6.29); ctx.stroke(); ctx.setLineDash([]);
  ctx.restore();
  const hgt = r * 1.25, n = 18, tw = t * 0.0016;
  for (let i = 0; i < n; i++) {
    const f = i / (n - 1), yy = y - hgt / 2 + f * hgt, ph = f * Math.PI * 3 + tw;
    const a1 = Math.sin(ph), a2 = Math.sin(ph + Math.PI), w = r * 0.42;
    const z1 = Math.cos(ph), z2 = Math.cos(ph + Math.PI);
    if (i % 2 === 0) { ctx.strokeStyle = 'rgba(142,207,224,0.22)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x + a1 * w, yy); ctx.lineTo(x + a2 * w, yy); ctx.stroke(); }
    for (const [a, z, c] of [[a1, z1, '#00e5ff'], [a2, z2, '#ff6d00']]) {
      ctx.fillStyle = alpha(c, 0.45 + 0.55 * (z + 1) / 2); ctx.shadowColor = c; ctx.shadowBlur = 6;
      ctx.beginPath(); ctx.arc(x + a * w, yy, 1.6 + 1.4 * (z + 1) / 2, 0, 6.29); ctx.fill();
    }
  }
  ctx.shadowBlur = 0;
  ctx.font = '700 ' + Math.max(9, r * 0.22) + 'px Orbitron, monospace'; ctx.textAlign = 'center'; ctx.fillStyle = '#00e5ff';
  ctx.fillText('HELIX', x, y + r + 30);
  if (open) {
    ctx.fillStyle = '#ff6d00'; ctx.beginPath(); ctx.arc(x + r * 0.78, y - r * 0.78, 11, 0, 6.29); ctx.fill();
    ctx.fillStyle = '#050508'; ctx.font = '700 11px Share Tech Mono, monospace'; ctx.fillText(String(open), x + r * 0.78, y - r * 0.78 + 4);
  }
}

function drawPod(p, t) {
  const col = p.connected ? '#00ff88' : p.built ? '#ff6d00' : '#2a4452';
  const up = p.cy < hub.y, ay = up ? p.y + p.h : p.y, hy = hub.y + (up ? -1 : 1) * Math.sqrt(Math.max(0, hub.r * hub.r - (p.cx - hub.x) * (p.cx - hub.x)));
  ctx.strokeStyle = alpha(col, p.connected ? 0.5 : 0.18); ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(p.cx, hy); ctx.lineTo(p.cx, ay); ctx.stroke();
  ctx.fillStyle = p.connected ? 'rgba(0,40,24,0.95)' : 'rgba(8,14,20,0.95)'; rr(p.x, p.y, p.w, p.h, 4); ctx.fill();
  ctx.strokeStyle = alpha(col, p.connected ? 0.9 : 0.35); ctx.lineWidth = selected === 'links' ? 1.6 : 1; rr(p.x, p.y, p.w, p.h, 4); ctx.stroke();
  ctx.fillStyle = p.connected ? alpha('#00ff88', 0.6 + 0.4 * Math.sin(t * 0.004 + p.cx)) : alpha(col, 0.5);
  ctx.beginPath(); ctx.arc(p.x + 7, p.cy, 2.5, 0, 6.29); ctx.fill();
  ctx.font = '8px Share Tech Mono, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = p.connected ? '#bfffe0' : alpha('#8ecfe0', p.built ? 0.6 : 0.35);
  ctx.fillText(p.label, p.x + 13, p.cy + 3);
}

function drawBot(x, y, color, status, t, seed, facing, sc) {
  sc = sc || 1;
  const bob = status === 'running' ? Math.sin(t * 0.008 + seed) * 1.2 : status === 'waiting' ? Math.abs(Math.sin(t * 0.01 + seed)) * 1.5 : 0;
  const dim = status === 'queued' || status === 'cancelled';
  ctx.save(); ctx.translate(x, y - bob); ctx.scale(sc, sc); ctx.globalAlpha = dim ? 0.45 : 1;
  ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.beginPath(); ctx.ellipse(0, 8 + bob, 5, 1.6, 0, 0, 6.29); ctx.fill();
  ctx.fillStyle = '#c8d6de'; rr(-4, -2, 8, 9, 3); ctx.fill();
  ctx.fillStyle = color; ctx.fillRect(-4, 2, 8, 1.5);
  ctx.fillStyle = '#e8f1f5'; ctx.beginPath(); ctx.arc(0, -5, 4.2, 0, 6.29); ctx.fill();
  ctx.fillStyle = '#0b1a24'; rr(-3 + facing, -6.4, 6, 2.8, 1.4); ctx.fill();
  ctx.fillStyle = alpha(color, 0.9); ctx.fillRect(-2 + facing, -5.6, 4, 1);
  ctx.restore();
  if (status === 'running') {
    const s = (t * 0.004 + seed) % 1; ctx.fillStyle = alpha(color, 1 - s); ctx.fillRect(x + 6 * sc, y - (10 + s * 6) * sc, 1.5 * sc, 1.5 * sc);
  }
}

function fitText(text, maxW) {
  let s = text; while (s.length > 3 && ctx.measureText(s + '…').width > maxW) s = s.slice(0, -1);
  return s === text ? s : s + '…';
}

function drawBay(b, t) {
  const { x, y, w, h, color } = b; const sel = selected === 'f:' + b.key;
  const idle = !b.tasks.length && !b.planning, live = b.tasks.some(k => k.status === 'running');
  const sc = Math.max(1, Math.min(1.8, w / 150));
  // hull
  ctx.fillStyle = 'rgba(7,11,18,0.96)'; rr(x, y, w, h, 7); ctx.fill();
  if (live) { ctx.shadowColor = color; ctx.shadowBlur = 14; }
  ctx.strokeStyle = alpha(color, sel ? 0.95 : idle ? 0.28 : 0.7); ctx.lineWidth = sel ? 2 : 1.2; rr(x, y, w, h, 7); ctx.stroke(); ctx.shadowBlur = 0;
  // header
  ctx.fillStyle = alpha(color, idle ? 0.07 : 0.16); ctx.fillRect(x + 1, y + 1, w - 2, 18);
  ctx.font = '9px Share Tech Mono, monospace'; const st = b.state; const stW = ctx.measureText(st).width;
  ctx.textAlign = 'right'; ctx.fillStyle = st === 'NEEDS YOU' ? '#ff6d00' : st === 'WORKING' ? '#00ff88' : 'rgba(142,207,224,0.55)'; ctx.fillText(st, x + w - 7, y + 13);
  ctx.font = '700 9px Orbitron, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = alpha(color, idle ? 0.6 : 1);
  ctx.fillText(fitText((b.isNew ? '◆ ' : '') + b.label, w - stW - 24), x + 8, y + 13);
  // floor plating and the conveyor line
  ctx.strokeStyle = alpha(color, 0.05); ctx.lineWidth = 1;
  for (let gx = x + 14; gx < x + w - 4; gx += 14) { ctx.beginPath(); ctx.moveTo(gx, y + 20); ctx.lineTo(gx, y + h - 16); ctx.stroke(); }
  const by0 = y + h - 13;
  ctx.fillStyle = 'rgba(20,34,46,0.95)'; ctx.fillRect(x + 6, by0, w - 12, 7);
  ctx.strokeStyle = alpha(color, live ? 0.55 : 0.12); ctx.lineWidth = 1;
  const off = live ? (t * 0.03) % 10 : 0;
  for (let sx = x + 6 + off; sx < x + w - 6; sx += 10) { ctx.beginPath(); ctx.moveTo(sx, by0 + 1); ctx.lineTo(sx + 4, by0 + 6); ctx.stroke(); }
  if (live) {
    for (let k = 0; k < 3; k++) { const p = (t * 0.00012 + k / 3) % 1; ctx.fillStyle = alpha(color, 0.85); ctx.fillRect(x + 8 + (w - 22) * p, by0 + 1.5, 6, 4); }
  }
  // current mission name
  if (b.title) {
    ctx.font = '9px Share Tech Mono, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = 'rgba(142,207,224,0.75)';
    ctx.fillText(fitText(b.title, w - 16), x + 8, y + 31);
  }
  if (b.planning && !b.tasks.length) {
    ctx.setLineDash([5, 5]); ctx.strokeStyle = alpha(color, 0.5 + 0.3 * Math.sin(t * 0.004)); rr(x + 10, y + 38, w - 20, h - 58, 4); ctx.stroke(); ctx.setLineDash([]);
    ctx.textAlign = 'center'; ctx.fillStyle = alpha('#8ecfe0', 0.5 + 0.4 * Math.sin(t * 0.004)); ctx.font = '10px Share Tech Mono, monospace';
    ctx.fillText(b.isNew ? 'BUILDING NEW FACTORY…' : 'HELIX IS PLANNING…', b.cx, y + h / 2 + 10);
    return;
  }
  // work stations: at least 3 machines; a crew member at each one that has a task
  const inside = b.tasks.filter(k => k.status !== 'waiting');
  const slots = Math.max(3, Math.min(8, b.tasks.length));
  const cols = Math.min(slots, w > 220 ? 4 : 3), rows = Math.ceil(slots / cols);
  const top = y + (b.title ? 38 : 26), cw = (w - 16) / cols, ch = (by0 - 4 - top) / rows;
  for (let i = 0; i < slots; i++) {
    const c = i % cols, r = Math.floor(i / cols), mx = x + 8 + cw * c + cw / 2, my = top + ch * r + ch * 0.55;
    const tk = inside[i];
    ctx.fillStyle = 'rgba(16,40,56,0.95)'; ctx.fillRect(mx - 10 * sc, my - 2 * sc, 20 * sc, 5 * sc);
    const scr = tk && tk.status === 'running' ? alpha(ROLE_COLORS[tk.role] || color, 0.55 + 0.3 * Math.sin(t * 0.01 + i)) : tk ? 'rgba(42,80,96,0.6)' : 'rgba(30,52,66,0.35)';
    ctx.fillStyle = scr; ctx.fillRect(mx - 7 * sc, my - 10 * sc, 14 * sc, 7 * sc);
    if (!tk) continue;
    drawBot(mx + (tk.status === 'done' ? 11 * sc : 0), my + 7 * sc, ROLE_COLORS[tk.role] || color, tk.status, t, i * 1.7, tk.status === 'running' ? 0 : 1, sc);
    const ky = my - 15 * sc;
    if (tk.status === 'done') { ctx.strokeStyle = '#00ff88'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(mx - 4, ky); ctx.lineTo(mx - 1, ky + 3); ctx.lineTo(mx + 4, ky - 3); ctx.stroke(); }
    if (tk.status === 'failed') { ctx.strokeStyle = '#ff3344'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(mx - 3, ky - 2); ctx.lineTo(mx + 3, ky + 4); ctx.moveTo(mx + 3, ky - 2); ctx.lineTo(mx - 3, ky + 4); ctx.stroke(); }
  }
  // crew members waiting on Michael walk the corridor to Helix and hold up a question
  const waiting = b.tasks.filter(k => k.status === 'waiting');
  if (waiting.length) {
    const { sx, sy, ex, ey } = corridorPoints(b);
    waiting.slice(0, 3).forEach((tk, i) => {
      const p = 0.72 - i * 0.14 + Math.sin(t * 0.0012 + i) * 0.015; const qx = ex + (sx - ex) * p, qyb = ey + (sy - ey) * p;
      drawBot(qx, qyb, ROLE_COLORS[tk.role] || color, 'waiting', t, i * 3.1, sx < ex ? -1 : 1, Math.min(sc, 1.4));
      const qy = qyb - 18 * Math.min(sc, 1.4) - 6;
      ctx.fillStyle = '#ff6d00'; rr(qx - 5, qy, 10, 10, 3); ctx.fill();
      ctx.fillStyle = '#050508'; ctx.font = '700 9px Share Tech Mono, monospace'; ctx.textAlign = 'center'; ctx.fillText('?', qx, qy + 8);
    });
  }
}

function frame(t) {
  requestAnimationFrame(frame);
  if (document.hidden || t - lastDraw < 33) return; lastDraw = t;
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#050508'; ctx.fillRect(0, 0, W, H);
  drawStars(t);
  bays.forEach(b => drawCorridor(b, t));
  pods.forEach(p => drawPod(p, t));
  drawHub(t);
  bays.forEach(b => drawBay(b, t));
}

function hit(x, y) {
  if (Math.hypot(x - hub.x, y - hub.y) <= hub.r + 16) return 'hub';
  if (pods.some(p => inRect(p, x, y))) return 'links';
  const b = bays.find(b => inRect(b, x, y));
  return b ? 'f:' + b.key : null;
}
cv.addEventListener('click', e => {
  const r = cv.getBoundingClientRect(), h = hit(e.clientX - r.left, e.clientY - r.top);
  if (h) { selected = h; renderSide(true); }
});
cv.addEventListener('mousemove', e => { const r = cv.getBoundingClientRect(); cv.style.cursor = hit(e.clientX - r.left, e.clientY - r.top) ? 'pointer' : 'default'; });

// ── one chat with Helix: the same conversation as the main screen ────────────────────────────────
let chatSig = '', chatBusy = false, chatTick = 0;
function chatMsg(role, text) {
  const d = mk('div', 'cm ' + (role === 'user' ? 'u' : 'a'));
  d.append(mk('div', 'tg', role === 'user' ? 'YOU' : (String(text).startsWith('CREW ·') ? 'CREW' : 'HELIX')), mk('div', null, text));
  return d;
}
async function loadChat(force) {
  if (chatBusy && !force) return;
  const r = await api('/api/session/history').catch(() => null);
  if (!r || !r.ok) return;
  const msgs = (r.data.messages || []).slice(-30);
  const sig = msgs.length + '|' + (msgs.length ? String(msgs[msgs.length - 1].content).slice(-80) : '');
  if (sig === chatSig && !force) return;
  chatSig = sig;
  const log = document.getElementById('chat-log');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  log.textContent = '';
  if (!msgs.length) log.append(mk('div', 'dim', 'Talk to Helix here. Say "build me…" to start a mission.'));
  msgs.forEach(m => log.append(chatMsg(m.role, m.content)));
  if (atBottom || force) log.scrollTop = log.scrollHeight;
}
async function sendChat() {
  const inp = document.getElementById('chat-in'), text = inp.value.trim();
  if (!text || chatBusy) return;
  chatBusy = true; inp.value = '';
  const log = document.getElementById('chat-log');
  log.append(chatMsg('user', text)); const wait = chatMsg('assistant', '…'); log.append(wait); log.scrollTop = log.scrollHeight;
  const r = await api('/api/chat', 'POST', { message: text, client_date: new Date().toLocaleString('en-US', { timeZoneName: 'short' }) }).catch(() => null);
  wait.lastChild.textContent = r ? (r.data.reply || r.data.detail || 'No answer.') : 'No connection.';
  log.scrollTop = log.scrollHeight;
  chatBusy = false;
  if (r && (r.data.ui_actions || []).some(a => a.type === 'crew_refresh')) setTimeout(refresh, 1200);
  setTimeout(() => loadChat(true), 500);
}
document.getElementById('chat-send').onclick = sendChat;
document.getElementById('chat-in').addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });

function poll() {
  clearTimeout(pollTimer);
  if (!document.hidden) { refresh(); if (chatTick++ % 3 === 0) loadChat(); }
  pollTimer = setTimeout(poll, 4000);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
if (new URLSearchParams(location.search).get('connected') === 'etsy') { selected = 'links'; history.replaceState(null, '', '/station'); }
resize(); poll(); loadChat(true); requestAnimationFrame(frame);
</script>
</body>
</html>
"""
