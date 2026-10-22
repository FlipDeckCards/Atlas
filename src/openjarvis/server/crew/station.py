"""The Station: a live picture of the crew, drawn in code (no borrowed art; Helix's portrait is Michael's own image, crew/assets/helix.jpg).

Aibussol is the core in the middle with Helix inside it. Around it: a factory per product and per new business, a module per outside
platform (online when connected), joined by docking tubes. The picture shows only what /api/crew/state says: crew members walk
between their console and the floor while their task runs, one waiting on Michael walks the tube to Helix with a question,
a finished one shows a check. Nothing is animated that is not happening.
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
    <div id="stage"><canvas id="cv"></canvas></div>
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
// Aibussol is the core in the middle, with Helix inside it. Around it: a factory for each product (and each new business the
// crew starts), a module for each outside platform (online when connected), joined to the core by docking tubes.
// Crew members walk around inside the factory they are working in; one waiting on Michael walks the tube to Helix.
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let W = 0, H = 0, DPR = 1, stars = [], sky = null, core = { x: 0, y: 0, r: 60 }, bays = [], plats = [], decor = [], lastT = 0;
const bots = new Map();
const helixImg = new Image(); let helixReady = false;
helixImg.onload = () => { helixReady = true; }; helixImg.src = '/crew/helix.jpg';
const PRODUCTS = [
  { key: 'flipdeck', label: 'FLIPDECK', color: '#ff6d00' },
  { key: 'marnyx', label: 'MARNYX', color: '#b06cff' },
  { key: 'deadzone', label: 'DEADZONE', color: '#ff3344' },
];
const PLATFORMS = [
  { key: 'stripe', full: 'STRIPE', short: 'STRIPE', brand: '#8a7dff' },
  { key: 'etsy', full: 'ETSY', short: 'ETSY', brand: '#f1641e' },
  { key: 'google_play', full: 'GOOGLE PLAY', short: 'PLAY', brand: '#34d17a' },
  { key: 'app_store', full: 'APP STORE', short: 'APPLE', brand: '#2f9bff' },
];
const WORKING = ['active', 'planning', 'paused', 'stalled'];

function resize() {
  const r = cv.getBoundingClientRect(); DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = r.width; H = r.height; cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR); ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  stars = Array.from({ length: Math.round(W * H / 2200) }, () => ({ x: Math.random() * W, y: Math.random() * H, z: Math.random(), p: Math.random() * 6.28 }));
  sky = document.createElement('canvas'); sky.width = Math.max(1, Math.round(W)); sky.height = Math.max(1, Math.round(H));
  const s = sky.getContext('2d');
  s.fillStyle = '#04050a'; s.fillRect(0, 0, W, H);
  for (const [fx, fy, rad, col] of [[0.18, 0.25, 0.55, 'rgba(90,40,160,0.16)'], [0.85, 0.8, 0.6, 'rgba(0,120,150,0.13)'], [0.7, 0.15, 0.35, 'rgba(255,90,0,0.05)']]) {
    const g = s.createRadialGradient(W * fx, H * fy, 0, W * fx, H * fy, Math.max(W, H) * rad);
    g.addColorStop(0, col); g.addColorStop(1, 'rgba(0,0,0,0)'); s.fillStyle = g; s.fillRect(0, 0, W, H);
  }
  layout();
}
window.addEventListener('resize', resize);

function factoryKey(m) {
  const p = String(m.project || '').toLowerCase().replace(/[^a-z]/g, '');
  if (p === 'aibussol' || PRODUCTS.some(f => f.key === p)) return p;
  return 'new:' + String(m.title || m.goal || m.id).toLowerCase().slice(0, 40);
}

function fillBay(b) {
  const ms = S ? S.missions.filter(m => m.status !== 'cancelled') : [];
  b.missions = ms.filter(m => factoryKey(m) === b.key);
  const current = b.missions.filter(m => WORKING.includes(m.status));
  b.tasks = current.flatMap(m => m.tasks.filter(t => t.status !== 'cancelled'));
  b.planning = current.some(m => m.status === 'planning');
  b.title = current.length ? (current[0].title || current[0].goal) : '';
  b.state = b.tasks.some(t => t.status === 'waiting') ? 'NEEDS YOU' : b.tasks.some(t => t.status === 'running') ? 'WORKING'
    : b.planning ? 'PLANNING' : current.length ? 'QUEUED' : b.missions.length ? 'DONE' : 'IDLE';
  // the product itself: LIVE or DOWN from the server's check of its site, shown whenever no crew is working there
  const site = S && S.products ? S.products[b.key] : null;
  if (site && (b.state === 'IDLE' || b.state === 'DONE')) {
    b.state = site.live ? 'LIVE' : 'DOWN';
    b.powered = !!site.live;
    b.idleText = site.live ? 'SITE LIVE · CREW STANDING BY' : 'SITE NOT ANSWERING';
  }
  return b;
}

function factories() {
  const ms = S ? S.missions.filter(m => m.status !== 'cancelled') : [];
  const out = PRODUCTS.map(f => fillBay(Object.assign({}, f)));
  out.push(fillBay({ key: 'aibussol', label: 'AIBUSSOL', color: '#00e5ff', isCore: true }));
  const seen = [];
  ms.forEach(m => { const k = factoryKey(m); if (k.startsWith('new:') && !seen.includes(k)) seen.push(k); });
  seen.slice(0, 3).forEach(k => {
    const m = ms.find(x => factoryKey(x) === k);
    out.push(fillBay({ key: k, label: String(m.title || 'NEW BUSINESS').toUpperCase(), color: '#00ff99', isNew: true }));
  });
  return out;
}

function layout() {
  const pad = Math.max(8, Math.min(W, H) * 0.02);
  const cw = (W - pad * 4) / 3, ch = (H - pad * 4) / 3;
  const cell = (i, j) => ({ x: pad + i * (cw + pad), y: pad + j * (ch + pad), w: cw, h: ch });
  core = { x: W / 2, y: H / 2, r: Math.min(cw, ch) * 0.47 };
  const all = factories();
  const coreBay = all.find(b => b.isCore);
  const slots = { flipdeck: cell(0, 0), marnyx: cell(2, 0), deadzone: cell(0, 2) };
  const newSlots = [cell(2, 2), cell(0, 1), cell(2, 1)];
  bays = [];
  all.forEach(b => {
    if (b.isCore) return;
    const c = b.isNew ? newSlots.shift() : slots[b.key];
    if (!c) return;
    bays.push(Object.assign(b, c, { cx: c.x + c.w / 2, cy: c.y + c.h / 2 }));
  });
  core.bay = coreBay;
  // free new-business slots: the next one is an expansion bay, the others carry solar arrays
  decor = newSlots.map((c, i) => Object.assign({ kind: i === 0 ? 'expansion' : 'solar' }, c, { cx: c.x + c.w / 2, cy: c.y + c.h / 2 }));
  // platform modules: two above the core, two below it, each half a cell wide
  const pw = (cw - pad) / 2, ph = ch * 0.62;
  const top = cell(1, 0), bot = cell(1, 2);
  const spots = [[top.x, top.y + 2], [top.x + pw + pad, top.y + 2], [bot.x, bot.y + bot.h - ph - 2], [bot.x + pw + pad, bot.y + bot.h - ph - 2]];
  plats = PLATFORMS.map((p, i) => {
    const c = S && S.connections ? S.connections[p.key] : null;
    const [x, y] = spots[i];
    return Object.assign({}, p, { x, y, w: pw, h: ph, cx: x + pw / 2, cy: y + ph / 2, connected: !!(c && c.connected), built: !!(c && c.built) });
  });
}

// ── drawing helpers ──────────────────────────────────────────────────────────────────────────
function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function chamfer(x, y, w, h, c) { ctx.beginPath(); ctx.moveTo(x + c, y); ctx.lineTo(x + w - c, y); ctx.lineTo(x + w, y + c); ctx.lineTo(x + w, y + h - c); ctx.lineTo(x + w - c, y + h); ctx.lineTo(x + c, y + h); ctx.lineTo(x, y + h - c); ctx.lineTo(x, y + c); ctx.closePath(); }
function alpha(hex, a) { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')'; }
function inRect(p, x, y) { return x >= p.x && x <= p.x + p.w && y >= p.y && y <= p.y + p.h; }
function fitText(text, maxW) { let s = text; while (s.length > 3 && ctx.measureText(s + '…').width > maxW) s = s.slice(0, -1); return s === text ? s : s + '…'; }
function hash(str) { let h = 0; for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0; return Math.abs(h); }

function drawSky(t) {
  if (sky) ctx.drawImage(sky, 0, 0, W, H);
  for (const s of stars) {
    const tw = 0.35 + 0.65 * Math.abs(Math.sin(t * 0.0006 * (0.4 + s.z) + s.p));
    ctx.fillStyle = 'rgba(170,225,255,' + (0.12 + 0.5 * s.z) * tw + ')';
    const x = (s.x + t * 0.003 * s.z) % W; const sz = s.z > 0.9 ? 1.8 : 1;
    ctx.fillRect(x, s.y, sz, sz);
  }
}

// where a line from the core towards a module meets that module's edge
function edgePoints(m) {
  const dx = m.cx - core.x, dy = m.cy - core.y, d = Math.hypot(dx, dy) || 1, ux = dx / d, uy = dy / d;
  const tx = Math.abs(ux) > 1e-6 ? (m.w / 2) / Math.abs(ux) : 1e9, ty = Math.abs(uy) > 1e-6 ? (m.h / 2) / Math.abs(uy) : 1e9, k = Math.min(tx, ty);
  return { sx: core.x + ux * (core.r + 2), sy: core.y + uy * (core.r + 2), ex: m.cx - ux * k, ey: m.cy - uy * k, ux, uy };
}

// a hallway: a floor with walls on both sides, opening through a doorway into the room at one end and into Helix's core at the other
function drawHall(m, color, live, t, width) {
  const { ex, ey, ux, uy } = edgePoints(m);
  const ring = Math.max(6, core.r * 0.14);
  const sx = core.x + ux * (core.r - ring * 0.5), sy = core.y + uy * (core.r - ring * 0.5);      // starts inside the core's ring
  const ix = ex + ux * 12, iy = ey + uy * 12;                                                     // ends just inside the room
  const len = Math.hypot(ix - sx, iy - sy); if (len < 6) return;
  const nx = -uy, ny = ux, hw = width / 2, wall = Math.max(3, width * 0.18);
  const quad = (o1, o2) => { ctx.beginPath(); ctx.moveTo(sx + nx * o1, sy + ny * o1); ctx.lineTo(ix + nx * o1, iy + ny * o1); ctx.lineTo(ix + nx * o2, iy + ny * o2); ctx.lineTo(sx + nx * o2, sy + ny * o2); ctx.closePath(); };
  // shadow, walls, floor
  ctx.fillStyle = 'rgba(0,0,0,0.5)'; quad(hw + wall + 3, -hw - wall - 3); ctx.fill();
  ctx.fillStyle = '#1c3243'; quad(hw + wall, -hw - wall); ctx.fill();
  const fg = ctx.createLinearGradient(sx + nx * hw, sy + ny * hw, sx - nx * hw, sy - ny * hw);
  fg.addColorStop(0, '#0d1a25'); fg.addColorStop(0.5, '#08111a'); fg.addColorStop(1, '#0d1a25');
  ctx.fillStyle = fg; quad(hw, -hw); ctx.fill();
  // floor plates and the wall edges
  ctx.strokeStyle = alpha(color, 0.09); ctx.lineWidth = 1;
  for (let d = 6; d < len; d += 10) {
    const px = sx + (ix - sx) * d / len, py = sy + (iy - sy) * d / len;
    ctx.beginPath(); ctx.moveTo(px + nx * hw, py + ny * hw); ctx.lineTo(px - nx * hw, py - ny * hw); ctx.stroke();
  }
  ctx.strokeStyle = 'rgba(150,190,210,0.35)'; ctx.lineWidth = 1;
  for (const o of [hw, -hw, hw + wall, -hw - wall]) { ctx.beginPath(); ctx.moveTo(sx + nx * o, sy + ny * o); ctx.lineTo(ix + nx * o, iy + ny * o); ctx.stroke(); }
  // wall lights and the guide strip down the middle
  for (let d = 10; d < len - 6; d += 16) {
    const px = sx + (ix - sx) * d / len, py = sy + (iy - sy) * d / len;
    for (const side of [1, -1]) {
      ctx.fillStyle = alpha(color, live ? 0.5 + 0.4 * Math.sin(t * 0.004 + d) : 0.18);
      ctx.beginPath(); ctx.arc(px + nx * (hw + wall / 2) * side, py + ny * (hw + wall / 2) * side, 1.3, 0, 6.29); ctx.fill();
    }
  }
  ctx.strokeStyle = alpha(color, live ? 0.45 : 0.12); ctx.lineWidth = 1.2; ctx.setLineDash([5, 7]); ctx.lineDashOffset = live ? t * 0.03 : 0;
  ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ix, iy); ctx.stroke(); ctx.setLineDash([]);
  if (live) {
    for (let k = 0; k < 2; k++) {
      const p = (t * 0.00028 + k / 2) % 1, px = ix + (sx - ix) * p, py = iy + (sy - iy) * p;
      ctx.fillStyle = alpha(color, 0.9); ctx.shadowColor = color; ctx.shadowBlur = 10; ctx.beginPath(); ctx.arc(px, py, 2.4, 0, 6.29); ctx.fill(); ctx.shadowBlur = 0;
    }
  }
  // doorways: a frame where the hall meets the room's wall and where it meets the core
  for (const [dx, dy, lit] of [[ex, ey, live], [core.x + ux * core.r, core.y + uy * core.r, live]]) {
    ctx.save(); ctx.translate(dx, dy); ctx.rotate(Math.atan2(uy, ux));
    ctx.fillStyle = '#2d4a60'; ctx.fillRect(-4, -hw - wall - 2, 8, wall + 2); ctx.fillRect(-4, hw, 8, wall + 2);        // door posts
    ctx.fillStyle = 'rgba(150,190,210,0.5)'; ctx.fillRect(-4, -hw - wall - 2, 1, wall + 2); ctx.fillRect(-4, hw, 1, wall + 2);
    ctx.fillStyle = alpha(color, lit ? 0.9 : 0.4); ctx.fillRect(-1, -hw, 2, hw * 2);                                    // lit threshold
    ctx.restore();
  }
}

function drawHull(m, color, lit, sel) {
  const { x, y, w, h } = m, c = Math.min(14, w * 0.08);
  ctx.save();
  if (lit) { ctx.shadowColor = color; ctx.shadowBlur = 18; }
  chamfer(x, y, w, h, c);
  const g = ctx.createLinearGradient(x, y, x, y + h); g.addColorStop(0, '#13202d'); g.addColorStop(1, '#070c13');
  ctx.fillStyle = g; ctx.fill(); ctx.shadowBlur = 0;
  ctx.strokeStyle = 'rgba(140,180,200,0.28)'; ctx.lineWidth = 3; chamfer(x, y, w, h, c); ctx.stroke();
  ctx.strokeStyle = alpha(color, sel ? 1 : lit ? 0.75 : 0.32); ctx.lineWidth = sel ? 2 : 1.2; chamfer(x + 2, y + 2, w - 4, h - 4, c - 1); ctx.stroke();
  ctx.restore();
  // header plate
  const hh = 20;
  const hg = ctx.createLinearGradient(x, y, x + w, y); hg.addColorStop(0, alpha(color, lit ? 0.38 : 0.16)); hg.addColorStop(1, alpha(color, 0.03));
  ctx.fillStyle = hg; ctx.beginPath(); ctx.moveTo(x + c + 2, y + 3); ctx.lineTo(x + w - c - 2, y + 3); ctx.lineTo(x + w - 3, y + c); ctx.lineTo(x + w - 3, y + hh); ctx.lineTo(x + 3, y + hh); ctx.lineTo(x + 3, y + c); ctx.closePath(); ctx.fill();
  // rivets
  ctx.fillStyle = 'rgba(160,200,220,0.35)';
  for (const [rx, ry] of [[x + c + 4, y + h - 5], [x + w - c - 4, y + h - 5], [x + 5, y + h / 2], [x + w - 5, y + h / 2]]) { ctx.beginPath(); ctx.arc(rx, ry, 1.3, 0, 6.29); ctx.fill(); }
  return hh;
}

function drawBot(x, y, color, status, t, seed, facing, sc, moving) {
  sc = sc || 1;
  const bob = moving ? Math.abs(Math.sin(t * 0.016 + seed)) * 1.2 : status === 'running' ? Math.sin(t * 0.008 + seed) * 0.8 : status === 'waiting' ? Math.abs(Math.sin(t * 0.01 + seed)) * 1.4 : 0;
  const dim = status === 'queued' || status === 'cancelled';
  ctx.save(); ctx.translate(x, y - bob); ctx.scale(sc, sc); ctx.globalAlpha = dim ? 0.5 : 1;
  ctx.fillStyle = 'rgba(0,0,0,0.4)'; ctx.beginPath(); ctx.ellipse(0, 9 + bob, 5.5, 1.8, 0, 0, 6.29); ctx.fill();
  const step = moving ? Math.sin(t * 0.02 + seed) * 2 : 0;                      // legs
  ctx.fillStyle = '#8fa3ae'; ctx.fillRect(-3 + step * 0.5, 5, 2, 4); ctx.fillRect(1 - step * 0.5, 5, 2, 4);
  ctx.fillStyle = '#d3e0e7'; rr(-4.5, -2, 9, 8.5, 3); ctx.fill();                 // body
  ctx.fillStyle = color; ctx.fillRect(-4.5, 1.5, 9, 1.8);                          // role stripe
  ctx.fillStyle = '#eef6f9'; ctx.beginPath(); ctx.arc(0, -5.4, 4.4, 0, 6.29); ctx.fill(); // head
  ctx.fillStyle = '#0b1a24'; rr(-3.2 + facing, -6.8, 6.4, 3, 1.4); ctx.fill();     // visor
  ctx.fillStyle = alpha(color, 0.95); ctx.fillRect(-2.2 + facing, -6, 4.4, 1);
  ctx.restore();
  if (status === 'running' && !moving) {
    const s = (t * 0.004 + seed) % 1; ctx.fillStyle = alpha(color, 1 - s); ctx.fillRect(x + 6 * sc, y - (11 + s * 7) * sc, 1.6 * sc, 1.6 * sc);
  }
}

// ── crew members: each one walks between its console and the factory floor while it works ─────────
function interior(m, hh) { return { x: m.x + 12, y: m.y + hh + 10, w: m.w - 24, h: m.h - hh - 30 }; }
function botFor(tk, I, desk, t) {
  let b = bots.get(tk.id);
  if (!b) { b = { fx: desk.fx, fy: Math.min(0.95, desk.fy + 0.25), tx: desk.fx, ty: desk.fy, wait: 0, atDesk: false, seed: hash(tk.id) % 100 }; bots.set(tk.id, b); }
  b.seen = t;
  return b;
}
function stepBot(b, tk, desk, dt) {
  const spd = 0.00018 * dt;                                         // fraction of the floor per frame
  if (tk.status !== 'running') { b.tx = desk.fx + (tk.status === 'done' ? 0.06 : 0); b.ty = desk.fy + (tk.status === 'queued' ? 0.35 : 0.05); }
  const dx = b.tx - b.fx, dy = b.ty - b.fy, d = Math.hypot(dx, dy);
  if (d > 0.01) { b.fx += dx / d * Math.min(spd, d); b.fy += dy / d * Math.min(spd, d); b.moving = true; b.facing = dx < 0 ? -1 : 1; return; }
  b.moving = false;
  if (tk.status !== 'running') return;
  b.wait -= dt;
  if (b.wait > 0) return;
  if (b.atDesk) {                                                  // done typing: go check the floor or the conveyor
    b.tx = 0.08 + Math.random() * 0.84; b.ty = 0.45 + Math.random() * 0.45; b.atDesk = false; b.wait = 700 + Math.random() * 900;
  } else {                                                         // back to the console to work
    b.tx = desk.fx; b.ty = desk.fy; b.atDesk = true; b.wait = 2200 + Math.random() * 2600;
  }
}

// a room's name and status, drawn after the hallways so a doorway never hides them
function drawHeader(m) {
  const { x, y, w } = m;
  ctx.font = '9px Share Tech Mono, monospace'; const st = m.state, stW = ctx.measureText(st).width;
  ctx.font = '700 10px Orbitron, monospace'; const name = fitText((m.isNew ? '◆ ' : '') + m.label, w - stW - 30), nW = ctx.measureText(name).width;
  ctx.fillStyle = 'rgba(8,14,22,0.92)'; rr(x + 8, y + 4, nW + 8, 14, 2); ctx.fill(); rr(x + w - stW - 14, y + 4, stW + 8, 14, 2); ctx.fill();
  ctx.textAlign = 'right'; ctx.font = '9px Share Tech Mono, monospace';
  ctx.fillStyle = st === 'DOWN' ? '#ff3344' : st === 'NEEDS YOU' || st === 'OFFLINE' ? '#ff8a2a' : st === 'WORKING' || st === 'ONLINE' || st === 'LIVE' ? '#00ff99' : 'rgba(142,207,224,0.6)'; ctx.fillText(st, x + w - 10, y + 14);
  ctx.font = '700 10px Orbitron, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = m.color; ctx.fillText(name, x + 12, y + 14);
}

function drawFactory(m, t, dt) {
  const sel = m.platform ? selected === 'links' : selected === 'f:' + m.key, live = !!m.powered || m.tasks.some(k => k.status === 'running');
  const hh = drawHull(m, m.color, live || m.tasks.length > 0, sel);
  const { x, y, w, h } = m;
  const I = interior(m, hh);
  // floor tiles
  ctx.fillStyle = 'rgba(4,8,13,0.85)'; rr(I.x, I.y, I.w, I.h, 4); ctx.fill();
  ctx.strokeStyle = alpha(m.color, 0.06); ctx.lineWidth = 1;
  for (let gx = I.x + 12; gx < I.x + I.w; gx += 12) { ctx.beginPath(); ctx.moveTo(gx, I.y); ctx.lineTo(gx, I.y + I.h); ctx.stroke(); }
  for (let gy = I.y + 12; gy < I.y + I.h; gy += 12) { ctx.beginPath(); ctx.moveTo(I.x, gy); ctx.lineTo(I.x + I.w, gy); ctx.stroke(); }
  // consoles along the back wall
  const n = Math.max(3, Math.min(6, m.tasks.length));
  const desks = [];
  for (let i = 0; i < n; i++) {
    const cx = I.x + I.w * (i + 0.5) / n, cy = I.y + 4;
    const cwid = Math.min(26, I.w / n - 6);
    const tk = m.tasks[i];
    const on = tk && tk.status === 'running';
    ctx.fillStyle = '#18303f'; rr(cx - cwid / 2, cy, cwid, 11, 2); ctx.fill();
    ctx.fillStyle = on ? alpha(ROLE_COLORS[tk.role] || m.color, 0.55 + 0.35 * Math.sin(t * 0.012 + i)) : (!tk && m.powered) ? alpha(m.color, 0.35 + 0.25 * Math.sin(t * 0.005 + i * 1.7)) : tk ? 'rgba(60,110,130,0.5)' : 'rgba(40,70,90,0.35)';
    ctx.fillRect(cx - cwid / 2 + 2, cy + 2, cwid - 4, 6);
    ctx.fillStyle = on || m.powered ? '#00ff99' : 'rgba(255,90,40,0.5)'; ctx.fillRect(cx + cwid / 2 - 4, cy + 9, 2, 1.5);
    desks.push({ fx: (cx - I.x) / I.w, fy: Math.min(0.9, 26 / I.h) });
  }
  // conveyor and crates
  const cyb = I.y + I.h - 9;
  ctx.fillStyle = '#132634'; ctx.fillRect(I.x + 4, cyb, I.w - 8, 7);
  ctx.strokeStyle = alpha(m.color, live ? 0.5 : 0.14); ctx.lineWidth = 1;
  const off = live ? (t * 0.03) % 9 : 0;
  for (let sx = I.x + 4 + off; sx < I.x + I.w - 6; sx += 9) { ctx.beginPath(); ctx.moveTo(sx, cyb + 1); ctx.lineTo(sx + 3, cyb + 6); ctx.stroke(); }
  if (live) for (let k = 0; k < 3; k++) { const p = (t * 0.0001 + k / 3) % 1; ctx.fillStyle = alpha(m.color, 0.85); rr(I.x + 6 + (I.w - 20) * p, cyb + 1, 7, 5, 1); ctx.fill(); }
  // blinking ceiling lights
  for (let i = 0; i < 3; i++) { ctx.fillStyle = alpha(m.color, live ? 0.35 + 0.3 * Math.sin(t * 0.003 + i * 2) : 0.12); ctx.fillRect(x + w * (0.25 + i * 0.25) - 6, y + hh + 2, 12, 2); }
  if (m.title) { ctx.font = '9px Share Tech Mono, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = 'rgba(142,207,224,0.7)'; ctx.fillText(fitText(m.title, w - 24), x + 12, y + h - 7); }
  if (m.planning && !m.tasks.length) {
    ctx.textAlign = 'center'; ctx.fillStyle = alpha('#8ecfe0', 0.5 + 0.4 * Math.sin(t * 0.004)); ctx.font = '10px Share Tech Mono, monospace';
    ctx.fillText(m.isNew ? 'BUILDING NEW FACTORY…' : 'HELIX IS PLANNING…', m.cx, I.y + I.h / 2 + 4); return;
  }
  if (!m.tasks.length) {
    if (m.state === 'COMING SOON') { ctx.setLineDash([4, 5]); ctx.strokeStyle = 'rgba(142,207,224,0.18)'; rr(I.x + 4, I.y + 18, I.w - 8, I.h - 30, 3); ctx.stroke(); ctx.setLineDash([]); }
    ctx.textAlign = 'center'; ctx.fillStyle = m.powered ? alpha(m.color, 0.7) : 'rgba(142,207,224,0.3)'; ctx.font = '9px Share Tech Mono, monospace';
    ctx.fillText(fitText(m.idleText || 'STANDING BY', I.w - 8), m.cx, I.y + I.h / 2 + 6);
  }
  const sc = Math.max(0.95, Math.min(1.6, I.w / 150));
  m.tasks.forEach((tk, i) => {
    if (tk.status === 'waiting') return;
    const desk = desks[i % desks.length], b = botFor(tk, I, desk, t);
    stepBot(b, tk, desk, dt);
    const bx = I.x + b.fx * I.w, by = I.y + b.fy * I.h;
    drawBot(bx, by, ROLE_COLORS[tk.role] || m.color, tk.status, t, b.seed, b.facing || 1, sc, b.moving);
    const ky = by - 16 * sc;
    if (tk.status === 'done') { ctx.strokeStyle = '#00ff99'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(bx - 4, ky); ctx.lineTo(bx - 1, ky + 3); ctx.lineTo(bx + 4, ky - 3); ctx.stroke(); }
    if (tk.status === 'failed') { ctx.strokeStyle = '#ff3344'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(bx - 3, ky - 2); ctx.lineTo(bx + 3, ky + 4); ctx.moveTo(bx + 3, ky - 2); ctx.lineTo(bx - 3, ky + 4); ctx.stroke(); }
  });
}

// a crew member waiting on Michael walks out along the tube and stands at Helix's door with a question
function drawWaiting(m, t) {
  const waiting = m.tasks.filter(k => k.status === 'waiting');
  if (!waiting.length) return;
  const { sx, sy, ex, ey } = edgePoints(m);
  waiting.slice(0, 3).forEach((tk, i) => {
    const p = 0.82 - i * 0.16 + Math.sin(t * 0.0012 + i) * 0.02; const qx = ex + (sx - ex) * p, qyb = ey + (sy - ey) * p;
    drawBot(qx, qyb, ROLE_COLORS[tk.role] || m.color, 'waiting', t, i * 3.1, sx < ex ? -1 : 1, 1.15, false);
    const qy = qyb - 26;
    ctx.fillStyle = '#ff8a2a'; ctx.shadowColor = '#ff8a2a'; ctx.shadowBlur = 8; rr(qx - 6, qy, 12, 12, 3); ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#050508'; ctx.font = '700 10px Share Tech Mono, monospace'; ctx.textAlign = 'center'; ctx.fillText('?', qx, qy + 9);
  });
}

function drawPlatform(p, t, dt) {
  // a platform is a factory of its own: powered and working when connected, dark when not
  p.tasks = []; p.powered = p.connected; p.key = p.key || p.label;
  p.state = p.connected ? 'ONLINE' : p.built ? 'OFFLINE' : 'COMING SOON';
  p.idleText = p.connected ? 'CONNECTED' : p.built ? 'TAP TO CONNECT' : 'UNDER CONSTRUCTION';
  p.label = p.w >= 96 ? p.full : p.short;
  drawFactory(Object.assign(p, { color: p.connected ? p.brand : p.built ? '#ff8a2a' : '#3a5566', platform: true }), t, dt);
}

function drawDecor(d, t) {
  if (d.kind === 'expansion') {
    ctx.setLineDash([6, 6]); ctx.strokeStyle = alpha('#00ff99', 0.28 + 0.12 * Math.sin(t * 0.002)); ctx.lineWidth = 1.2;
    chamfer(d.x + 6, d.y + 6, d.w - 12, d.h - 12, 12); ctx.stroke(); ctx.setLineDash([]);
    // scaffolding
    ctx.strokeStyle = 'rgba(0,255,153,0.08)';
    for (let i = 1; i < 5; i++) { ctx.beginPath(); ctx.moveTo(d.x + 6 + (d.w - 12) * i / 5, d.y + 10); ctx.lineTo(d.x + 6 + (d.w - 12) * i / 5, d.y + d.h - 10); ctx.stroke(); }
    ctx.textAlign = 'center'; ctx.font = '700 9px Orbitron, monospace'; ctx.fillStyle = 'rgba(0,255,153,0.5)'; ctx.fillText(fitText('EXPANSION BAY', d.w - 20), d.cx, d.cy - 2);
    ctx.font = '9px Share Tech Mono, monospace'; ctx.fillStyle = 'rgba(142,207,224,0.4)'; ctx.fillText(fitText(d.w < 180 ? 'next business' : 'your next business builds here', d.w - 20), d.cx, d.cy + 13);
    return;
  }
  // solar array on a truss
  const ax = d.cx, ay = d.cy, aw = d.w * 0.8, ah = Math.min(d.h * 0.5, 60);
  ctx.strokeStyle = 'rgba(110,150,170,0.35)'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(ax - aw / 2, ay); ctx.lineTo(ax + aw / 2, ay); ctx.stroke();
  for (const side of [-1, 1]) {
    for (let i = 0; i < 3; i++) {
      const px = ax + side * (12 + i * (aw / 2 - 12) / 3), pw = (aw / 2 - 16) / 3;
      ctx.fillStyle = '#0d2747'; ctx.fillRect(side > 0 ? px : px - pw, ay - ah / 2, pw, ah);
      ctx.strokeStyle = 'rgba(80,150,220,0.35)'; ctx.lineWidth = 0.8;
      for (let k = 1; k < 4; k++) { ctx.beginPath(); ctx.moveTo(side > 0 ? px : px - pw, ay - ah / 2 + ah * k / 4); ctx.lineTo((side > 0 ? px : px - pw) + pw, ay - ah / 2 + ah * k / 4); ctx.stroke(); }
      ctx.fillStyle = 'rgba(160,210,255,' + (0.05 + 0.05 * Math.sin(t * 0.001 + i)) + ')'; ctx.fillRect(side > 0 ? px : px - pw, ay - ah / 2, pw, ah * 0.3);
    }
  }
  ctx.fillStyle = '#2b4458'; rr(ax - 7, ay - 7, 14, 14, 3); ctx.fill();
}

function drawCore(t) {
  const { x, y, r } = core, open = S ? S.questions.length : 0;
  const g = ctx.createRadialGradient(x, y, r * 0.5, x, y, r * 1.7); g.addColorStop(0, 'rgba(0,229,255,0.18)'); g.addColorStop(1, 'rgba(0,229,255,0)');
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 1.7, 0, 6.29); ctx.fill();
  // armored ring with docking notches
  const ring = Math.max(6, r * 0.14);
  ctx.strokeStyle = '#1b3042'; ctx.lineWidth = ring; ctx.beginPath(); ctx.arc(x, y, r - ring / 2, 0, 6.29); ctx.stroke();
  ctx.strokeStyle = 'rgba(150,190,210,0.35)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.29); ctx.stroke();
  ctx.beginPath(); ctx.arc(x, y, r - ring, 0, 6.29); ctx.stroke();
  for (let i = 0; i < 24; i++) {
    const a = i / 24 * Math.PI * 2 + t * 0.00008, c = Math.cos(a), s = Math.sin(a);
    ctx.strokeStyle = i % 3 === 0 ? 'rgba(0,229,255,0.65)' : 'rgba(120,160,180,0.3)'; ctx.lineWidth = i % 3 === 0 ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(x + c * (r - ring + 1), y + s * (r - ring + 1)); ctx.lineTo(x + c * (r - 1), y + s * (r - 1)); ctx.stroke();
  }
  // Helix
  const ir = r - ring - 2;
  ctx.save(); ctx.beginPath(); ctx.arc(x, y, ir, 0, 6.29); ctx.clip();
  ctx.fillStyle = '#050a12'; ctx.fillRect(x - ir, y - ir, ir * 2, ir * 2);
  if (helixReady) {
    const iw = helixImg.naturalWidth, ih = helixImg.naturalHeight, sc2 = (ir * 2.15) / Math.min(iw, ih);
    const dw = iw * sc2, dh = ih * sc2;
    ctx.globalAlpha = 0.95; ctx.drawImage(helixImg, x - dw / 2, y - dh * 0.42, dw, dh); ctx.globalAlpha = 1;
  } else {
    const hgt = ir * 1.3, n2 = 16, tw = t * 0.0016;
    for (let i = 0; i < n2; i++) {
      const f = i / (n2 - 1), yy = y - hgt / 2 + f * hgt, ph = f * Math.PI * 3 + tw;
      for (const [a, c] of [[Math.sin(ph), '#00e5ff'], [Math.sin(ph + Math.PI), '#ff6d00']]) { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(x + a * ir * 0.45, yy, 2.4, 0, 6.29); ctx.fill(); }
    }
  }
  const vg = ctx.createRadialGradient(x, y, ir * 0.55, x, y, ir); vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,20,35,0.75)');
  ctx.fillStyle = vg; ctx.fillRect(x - ir, y - ir, ir * 2, ir * 2);
  const scan = y - ir + ((t * 0.04) % (ir * 2));                            // scan line
  ctx.fillStyle = 'rgba(0,229,255,0.12)'; ctx.fillRect(x - ir, scan, ir * 2, 2);
  ctx.restore();
  ctx.save(); ctx.translate(x, y);
  ctx.rotate(t * 0.0003); ctx.strokeStyle = 'rgba(0,229,255,0.4)'; ctx.setLineDash([2, 10]); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(0, 0, r + 8, 0, 6.29); ctx.stroke();
  ctx.rotate(-t * 0.0007); ctx.strokeStyle = open ? 'rgba(255,138,42,' + (0.5 + 0.4 * Math.sin(t * 0.005)) + ')' : 'rgba(255,109,0,0.2)';
  ctx.setLineDash([16, 10]); ctx.lineWidth = open ? 2.5 : 1; ctx.beginPath(); ctx.arc(0, 0, r + 15, 0, 6.29); ctx.stroke(); ctx.setLineDash([]);
  ctx.restore();
  // Aibussol crew on the core's deck ring
  const cb = core.bay;
  if (cb) cb.tasks.filter(k => k.status !== 'waiting').forEach((tk, i) => {
    const a = -Math.PI / 2 + (t * 0.00012) * (tk.status === 'running' ? 1 : 0) + i * 0.9;
    drawBot(x + Math.cos(a) * (r - ring / 2), y + Math.sin(a) * (r - ring / 2) + 3, ROLE_COLORS[tk.role] || '#00e5ff', tk.status, t, i * 2.3, 1, 0.9, tk.status === 'running');
  });
  // crew members with no work stand by on Helix's deck, walking the ring until a mission sends them to a factory
  const free = (S && S.roster ? S.roster : []).filter(a => a.role !== 'lead' && !a.working && !a.waiting);
  free.forEach((a, i) => {
    const ang = Math.PI * 0.15 + i * (Math.PI * 1.7 / Math.max(free.length, 1)) + t * 0.00006 * (i % 2 ? 1 : -1);
    const bx = x + Math.cos(ang) * (r - ring / 2), by = y + Math.sin(ang) * (r - ring / 2) + 3;
    drawBot(bx, by, ROLE_COLORS[a.role] || '#00e5ff', 'idle', t, i * 4.1, Math.sin(ang) > 0 ? -1 : 1, Math.max(0.8, Math.min(1.1, r / 90)), true);
  });
}

// Helix's name plate and the open-items badge go on top of everything
function drawCoreLabels(t) {
  const { x, y, r } = core, open = S ? S.questions.length : 0;
  // Helix's name plate below the core
  ctx.textAlign = 'center';
  ctx.font = '700 ' + Math.max(9, r * 0.12) + 'px Orbitron, monospace';
  const hw2 = ctx.measureText('HELIX').width + 18;
  ctx.fillStyle = 'rgba(5,10,18,0.9)'; rr(x - hw2 / 2, y + r - 4, hw2, 15, 3); ctx.fill(); ctx.strokeStyle = 'rgba(255,109,0,0.6)'; rr(x - hw2 / 2, y + r - 4, hw2, 15, 3); ctx.stroke();
  ctx.fillStyle = '#ff8a2a'; ctx.fillText('HELIX', x, y + r + 7);
  if (open) {
    ctx.fillStyle = '#ff8a2a'; ctx.shadowColor = '#ff8a2a'; ctx.shadowBlur = 10; ctx.beginPath(); ctx.arc(x + r * 0.74, y - r * 0.74, 11, 0, 6.29); ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#050508'; ctx.font = '700 11px Share Tech Mono, monospace'; ctx.fillText(String(open), x + r * 0.74, y - r * 0.74 + 4);
  }
}

function frame(t) {
  requestAnimationFrame(frame);
  if (document.hidden || t - lastDraw < 33) return;
  const dt = Math.min(100, t - (lastT || t)); lastT = t; lastDraw = t;
  ctx.clearRect(0, 0, W, H);
  drawSky(t);
  decor.forEach(d => drawDecor(d, t));
  bays.forEach(b => drawFactory(b, t, dt));
  plats.forEach(p => drawPlatform(p, t, dt));
  drawCore(t);
  // hallways go on top of the room walls, so each one opens through a doorway into its room
  bays.forEach(b => drawHall(b, b.color, b.tasks.some(k => k.status === 'running'), t, Math.max(14, Math.min(24, core.r * 0.24))));
  plats.forEach(p => drawHall(p, p.color, !!p.connected, t, Math.max(11, Math.min(18, core.r * 0.18))));
  bays.forEach(b => drawWaiting(b, t));
  bays.forEach(drawHeader); plats.forEach(drawHeader);
  drawCoreLabels(t);
  for (const [id, b] of bots) if (t - b.seen > 5000) bots.delete(id);       // crew members whose tasks are gone
}

function hit(x, y) {
  if (Math.hypot(x - core.x, y - core.y) <= core.r + 16) return 'hub';
  if (plats.some(p => inRect(p, x, y))) return 'links';
  const b = bays.find(b => inRect(b, x, y));
  if (b) return 'f:' + b.key;
  return decor.some(d => d.kind === 'expansion' && inRect(d, x, y)) ? 'hub' : null;
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
