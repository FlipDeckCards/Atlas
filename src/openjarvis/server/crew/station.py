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
  #main { display:grid; grid-template-columns:1fr 380px; min-height:0; }
  #stage { position:relative; min-width:0; min-height:0; overflow:hidden; }
  #cv { position:absolute; inset:0; width:100%; height:100%; display:block; cursor:default; }
  #hint { position:absolute; left:14px; bottom:12px; font-size:10px; color:var(--text-dim); letter-spacing:1px; pointer-events:none; }
  #side { background:var(--panel); border-left:1px solid var(--border); overflow-y:auto; min-height:0; scrollbar-width:thin; scrollbar-color:var(--cyan-dim) transparent; }
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
    #main { grid-template-columns:1fr; grid-template-rows:44dvh 1fr; }
    #side { border-left:none; border-top:1px solid var(--border); }
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
  <div id="stage"><canvas id="cv"></canvas><div id="hint">Tap Helix for your inbox · tap a room for its mission</div></div>
  <div id="side"></div>
</div>
<div id="gate"><div class="box"><div class="h" id="gate-h">STATION OFFLINE</div><div id="gate-t"></div></div></div>

<script>
const TOKEN_KEY = 'aibussol_token';
const token = () => localStorage.getItem(TOKEN_KEY) || '';
const PROJECT_COLORS = { flipdeck:'#ff6d00', marnyx:'#b06cff', deadzone:'#ff3344', aibussol:'#00e5ff', 'new':'#00ff88' };
const ROLE_COLORS = { researcher:'#5ad1ff', builder:'#00e5ff', writer:'#ffc14d', designer:'#ff7ad9', operator:'#ff6d00' };
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
  if (selected !== 'hub' && selected !== 'links' && !S.missions.find(m => m.id === selected)) selected = 'hub';
  layout();
  renderSide();
}
function setState(t) { document.getElementById('c-state').textContent = t; }

document.getElementById('t-night').onclick = async () => { if (!S) return; await api('/api/crew/settings', 'POST', { night_shift: !S.settings.night_shift }); refresh(); };
document.getElementById('t-pause').onclick = async () => { if (!S) return; await api('/api/crew/settings', 'POST', { paused: !S.settings.paused }); refresh(); };

// ── side panel ───────────────────────────────────────────────────────────────────────────────
function renderSide(force) {
  const side = document.getElementById('side');
  const ae = document.activeElement;
  // never rebuild the panel under someone's fingers: wait until they leave the field they are typing in
  if (!force && ae && side.contains(ae) && /INPUT|TEXTAREA/.test(ae.tagName) && ae.value) { ae.onblur = () => renderSide(true); return; }
  const keepGoal = document.getElementById('goal'); const goalVal = keepGoal ? keepGoal.value : '';
  const keepBudget = document.getElementById('budget'); const budgetVal = keepBudget ? keepBudget.value : '';
  const focused = document.activeElement && document.activeElement.id;
  side.textContent = '';
  if (selected === 'hub') side.append(inboxSec(), launchSec(goalVal, budgetVal), missionsSec(), linksSec(), ledgerSec(S.events, 'Station Ledger'));
  else if (selected === 'links') side.append(backSec(), linksSec());
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

function launchSec(goalVal, budgetVal) {
  const s = mk('div', 'sec');
  s.append(Object.assign(mk('div', 'lbl'), { textContent: 'New mission' }));
  const ta = mk('textarea'); ta.id = 'goal'; ta.maxLength = 4000; ta.value = goalVal || '';
  ta.placeholder = 'e.g. Build me a digital planner product for card collectors and get it ready to sell on Etsy';
  const row = mk('div', 'ans');
  const bi = mk('input'); bi.type = 'number'; bi.id = 'budget'; bi.min = '0.05'; bi.step = '0.25'; bi.max = String(S.spend.max_mission_budget_usd);
  bi.value = budgetVal || String(S.spend.default_mission_budget_usd); bi.style.width = '90px'; bi.title = 'Mission budget (USD)';
  const go = mk('button', 'b', 'Launch'); const msg = mk('div', 'msg');
  go.onclick = async () => {
    const goal = ta.value.trim(); if (goal.length < 8) { msg.className = 'msg err'; msg.textContent = 'Describe the goal in a sentence or two.'; return; }
    go.disabled = true;
    const r = await api('/api/crew/missions', 'POST', { goal, budget_usd: parseFloat(bi.value) || undefined });
    go.disabled = false;
    if (r.ok) { ta.value = ''; msg.className = 'msg'; msg.textContent = 'Launched. Helix is planning it.'; selected = r.data.mission.id; setTimeout(refresh, 400); }
    else { msg.className = 'msg err'; msg.textContent = r.data.detail || 'Could not launch.'; }
  };
  row.append(mk('span', 'dim', 'Budget $'), bi, go);
  s.append(ta, row, msg, mk('div', 'dim', 'Or just tell Helix "build me…" in chat. The crew stops and asks before spending more or doing anything public.'));
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
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let W = 0, H = 0, DPR = 1, rooms = [], hub = { x: 0, y: 0, r: 60 }, stars = [];
function resize() {
  const r = cv.getBoundingClientRect(); DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = r.width; H = r.height; cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR); ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  stars = Array.from({ length: Math.round(W * H / 2600) }, () => ({ x: Math.random() * W, y: Math.random() * H, z: Math.random(), p: Math.random() * 6.28 }));
  layout();
}
window.addEventListener('resize', resize);

function layout() {
  const ms = S ? S.missions.filter(m => m.status !== 'cancelled').slice(0, 8) : [];
  const small = Math.min(W, H);
  hub = { x: W / 2, y: H / 2, r: Math.max(34, small * 0.11) };
  const n = ms.length, R = small * (n > 5 ? 0.38 : 0.34);
  const rw = Math.max(118, Math.min(250, small * (W > 900 ? 0.4 : 0.34))), rh = rw * 0.62;
  rooms = ms.map((m, i) => {
    const a = -Math.PI / 2 + (i / Math.max(n, 1)) * Math.PI * 2 + (n === 2 ? Math.PI / 2 : 0);
    let cx = W / 2 + Math.cos(a) * R * (W > H ? 1.35 : 0.95), cy = H / 2 + Math.sin(a) * R * (W > H ? 0.95 : 1.25);
    cx = Math.min(Math.max(cx, rw / 2 + 10), W - rw / 2 - 10); cy = Math.min(Math.max(cy, rh / 2 + 22), H - rh / 2 - 26);
    return { m, x: cx - rw / 2, y: cy - rh / 2, w: rw, h: rh, cx, cy };
  });
}

function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function alpha(hex, a) { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')'; }

function drawStars(t) {
  for (const s of stars) {
    const tw = 0.35 + 0.65 * Math.abs(Math.sin(t * 0.0006 * (0.4 + s.z) + s.p));
    ctx.fillStyle = 'rgba(160,220,255,' + (0.15 + 0.5 * s.z) * tw + ')';
    const x = (s.x + t * 0.004 * s.z) % W; ctx.fillRect(x, s.y, s.z > 0.85 ? 1.6 : 1, s.z > 0.85 ? 1.6 : 1);
  }
  ctx.strokeStyle = 'rgba(0,229,255,0.025)'; ctx.lineWidth = 1;
  for (let x = (W / 2) % 44; x < W; x += 44) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
  for (let y = (H / 2) % 44; y < H; y += 44) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
}

function corridorPoints(room) {
  const dx = room.cx - hub.x, dy = room.cy - hub.y, d = Math.hypot(dx, dy) || 1, ux = dx / d, uy = dy / d;
  const sx = hub.x + ux * (hub.r + 10), sy = hub.y + uy * (hub.r + 10);
  // where the corridor meets the room's edge
  const tx = Math.abs(ux) > 1e-6 ? (room.w / 2) / Math.abs(ux) : 1e9, ty = Math.abs(uy) > 1e-6 ? (room.h / 2) / Math.abs(uy) : 1e9, k = Math.min(tx, ty);
  return { sx, sy, ex: room.cx - ux * k, ey: room.cy - uy * k };
}

function drawCorridor(room, t) {
  const { sx, sy, ex, ey } = corridorPoints(room); const col = projColor(room.m.project);
  const live = room.m.tasks.some(x => x.status === 'running');
  ctx.strokeStyle = 'rgba(13,37,53,0.9)'; ctx.lineWidth = 12; ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke();
  ctx.strokeStyle = alpha(col, live ? 0.45 : 0.18); ctx.lineWidth = 1; ctx.setLineDash([4, 6]); ctx.lineDashOffset = live ? -t * 0.02 : 0;
  ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke(); ctx.setLineDash([]);
  if (live) {
    for (let k = 0; k < 3; k++) {
      const p = ((t * 0.00035 + k / 3) % 1); const x = ex + (sx - ex) * p, y = ey + (sy - ey) * p;
      ctx.fillStyle = alpha(col, 0.9); ctx.shadowColor = col; ctx.shadowBlur = 8; ctx.beginPath(); ctx.arc(x, y, 2, 0, 6.29); ctx.fill(); ctx.shadowBlur = 0;
    }
  }
}

function drawHub(t) {
  const { x, y, r } = hub; const open = S ? S.questions.length : 0;
  const g = ctx.createRadialGradient(x, y, r * 0.2, x, y, r * 1.6); g.addColorStop(0, 'rgba(0,229,255,0.16)'); g.addColorStop(1, 'rgba(0,229,255,0)');
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 1.6, 0, 6.29); ctx.fill();
  ctx.fillStyle = '#071019'; ctx.strokeStyle = 'rgba(0,229,255,0.5)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.29); ctx.fill(); ctx.stroke();
  // rotating outer rings
  ctx.save(); ctx.translate(x, y);
  ctx.rotate(t * 0.0003); ctx.strokeStyle = 'rgba(0,229,255,0.35)'; ctx.setLineDash([2, 9]); ctx.beginPath(); ctx.arc(0, 0, r + 8, 0, 6.29); ctx.stroke();
  ctx.rotate(-t * 0.0007); ctx.strokeStyle = open ? 'rgba(255,109,0,' + (0.45 + 0.4 * Math.sin(t * 0.005)) + ')' : 'rgba(255,109,0,0.18)';
  ctx.setLineDash([14, 10]); ctx.lineWidth = open ? 2 : 1; ctx.beginPath(); ctx.arc(0, 0, r + 15, 0, 6.29); ctx.stroke(); ctx.setLineDash([]);
  ctx.restore();
  // the helix: two strands turning around a vertical axis, joined by rungs
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
  ctx.font = '700 ' + Math.max(9, r * 0.2) + 'px Orbitron, monospace'; ctx.textAlign = 'center'; ctx.fillStyle = '#00e5ff';
  ctx.fillText('HELIX', x, y + r + 32);
  if (open) {
    ctx.fillStyle = '#ff6d00'; ctx.beginPath(); ctx.arc(x + r * 0.78, y - r * 0.78, 11, 0, 6.29); ctx.fill();
    ctx.fillStyle = '#050508'; ctx.font = '700 11px Share Tech Mono, monospace'; ctx.fillText(String(open), x + r * 0.78, y - r * 0.78 + 4);
  }
}

function drawBot(x, y, color, status, t, seed, facing, sc) {
  sc = sc || 1;
  const bob = status === 'running' ? Math.sin(t * 0.008 + seed) * 1.2 : status === 'waiting' ? Math.abs(Math.sin(t * 0.01 + seed)) * 1.5 : 0;
  const dim = status === 'queued' || status === 'cancelled';
  ctx.save(); ctx.translate(x, y - bob); ctx.scale(sc, sc); ctx.globalAlpha = dim ? 0.45 : 1;
  ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.beginPath(); ctx.ellipse(0, 8 + bob, 5, 1.6, 0, 0, 6.29); ctx.fill();
  ctx.fillStyle = '#c8d6de'; rr(-4, -2, 8, 9, 3); ctx.fill();                     // body
  ctx.fillStyle = color; ctx.fillRect(-4, 2, 8, 1.5);                              // role stripe
  ctx.fillStyle = '#e8f1f5'; ctx.beginPath(); ctx.arc(0, -5, 4.2, 0, 6.29); ctx.fill(); // head
  ctx.fillStyle = '#0b1a24'; rr(-3 + facing, -6.4, 6, 2.8, 1.4); ctx.fill();       // visor
  ctx.fillStyle = alpha(color, 0.9); ctx.fillRect(-2 + facing, -5.6, 4, 1);
  ctx.restore();
  if (status === 'running') {
    const s = (t * 0.004 + seed) % 1; ctx.fillStyle = alpha(color, 1 - s); ctx.fillRect(x + 6 * sc, y - (10 + s * 6) * sc, 1.5 * sc, 1.5 * sc);
  }
}

function drawRoom(room, t) {
  const { m, x, y, w, h } = room; const col = projColor(m.project); const sel = selected === m.id;
  ctx.fillStyle = 'rgba(8,12,20,0.94)'; rr(x, y, w, h, 6); ctx.fill();
  ctx.strokeStyle = alpha(col, sel ? 0.9 : 0.45); ctx.lineWidth = sel ? 2 : 1; rr(x, y, w, h, 6); ctx.stroke();
  // floor plates
  ctx.strokeStyle = alpha(col, 0.07); ctx.lineWidth = 1;
  for (let gx = x + 12; gx < x + w; gx += 12) { ctx.beginPath(); ctx.moveTo(gx, y + 20); ctx.lineTo(gx, y + h - 4); ctx.stroke(); }
  ctx.fillStyle = alpha(col, 0.12); ctx.fillRect(x + 1, y + 1, w - 2, 17);
  ctx.font = '9px Share Tech Mono, monospace'; const stW = ctx.measureText(m.status.toUpperCase()).width;
  ctx.font = '700 9px Orbitron, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = col;
  let title = (m.title || m.goal || 'mission').toUpperCase(); while (title.length > 4 && ctx.measureText(title + '…').width > w - stW - 22) title = title.slice(0, -1);
  ctx.fillText(title + ((m.title || m.goal || '').length > title.length ? '…' : ''), x + 7, y + 12);
  ctx.textAlign = 'right'; ctx.font = '9px Share Tech Mono, monospace'; ctx.fillStyle = 'rgba(142,207,224,0.7)';
  ctx.fillText(m.status.toUpperCase(), x + w - 6, y + 12);
  // budget strip
  const pct = m.budget_usd ? Math.min(1, m.spent_usd / m.budget_usd) : 0;
  ctx.fillStyle = 'rgba(0,229,255,0.08)'; ctx.fillRect(x + 6, y + h - 6, w - 12, 2);
  ctx.fillStyle = pct > 0.8 ? '#ff6d00' : col; ctx.fillRect(x + 6, y + h - 6, (w - 12) * pct, 2);
  if (m.status === 'planning') {
    ctx.textAlign = 'center'; ctx.fillStyle = 'rgba(142,207,224,' + (0.4 + 0.4 * Math.sin(t * 0.004)) + ')'; ctx.font = '10px Share Tech Mono, monospace';
    ctx.fillText('HELIX IS PLANNING…', room.cx, room.cy + 6); return;
  }
  const sc = Math.max(1, Math.min(1.7, w / 140));
  const inside = m.tasks.filter(tk => tk.status !== 'waiting');
  const cols = Math.max(1, Math.min(4, Math.ceil(Math.sqrt(inside.length * 1.6)))), rows = Math.ceil(inside.length / cols) || 1;
  const cw = (w - 16) / cols, ch = (h - 34) / rows;
  inside.forEach((tk, i) => {
    const c = i % cols, r = Math.floor(i / cols), bx = x + 8 + cw * c + cw / 2, by = y + 24 + ch * r + ch / 2;
    ctx.fillStyle = 'rgba(13,37,53,0.9)'; ctx.fillRect(bx - 9 * sc, by - 2 * sc, 18 * sc, 5 * sc);                  // desk
    const scr = tk.status === 'running' ? alpha(ROLE_COLORS[tk.role] || col, 0.5 + 0.3 * Math.sin(t * 0.01 + i)) : 'rgba(42,80,96,0.5)';
    ctx.fillStyle = scr; ctx.fillRect(bx - 6 * sc, by - 9 * sc, 12 * sc, 6 * sc);                                   // screen
    drawBot(bx + (tk.status === 'done' ? 10 * sc : 0), by + 6 * sc, ROLE_COLORS[tk.role] || col, tk.status, t, i * 1.7, tk.status === 'running' ? 0 : 1, sc);
    const my = by - 14 * sc;
    if (tk.status === 'done') { ctx.strokeStyle = '#00ff88'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(bx - 4, my); ctx.lineTo(bx - 1, my + 3); ctx.lineTo(bx + 4, my - 3); ctx.stroke(); }
    if (tk.status === 'failed') { ctx.strokeStyle = '#ff3344'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(bx - 3, my - 2); ctx.lineTo(bx + 3, my + 4); ctx.moveTo(bx + 3, my - 2); ctx.lineTo(bx - 3, my + 4); ctx.stroke(); }
  });
  // crew members waiting on Michael walk the corridor to Helix and hold up a question
  const waiting = m.tasks.filter(tk => tk.status === 'waiting');
  if (waiting.length) {
    const { sx, sy, ex, ey } = corridorPoints(room);
    waiting.forEach((tk, i) => {
      const p = 0.78 - i * 0.12 + Math.sin(t * 0.0012 + i) * 0.015; const bx = ex + (sx - ex) * p, by = ey + (sy - ey) * p;
      drawBot(bx, by, ROLE_COLORS[tk.role] || col, 'waiting', t, i * 3.1, sx < ex ? -1 : 1, sc);
      const qy = by - 18 * sc - 6;
      ctx.fillStyle = '#ff6d00'; rr(bx - 5, qy, 10, 10, 3); ctx.fill();
      ctx.fillStyle = '#050508'; ctx.font = '700 9px Share Tech Mono, monospace'; ctx.textAlign = 'center'; ctx.fillText('?', bx, qy + 8);
    });
  }
}

function drawEmpty() {
  ctx.textAlign = 'center'; ctx.fillStyle = 'rgba(142,207,224,0.55)'; ctx.font = '11px Share Tech Mono, monospace';
  ctx.fillText('NO MISSIONS YET', W / 2, hub.y + hub.r + 56);
  ctx.fillStyle = 'rgba(61,106,122,0.9)'; ctx.fillText('Tell Helix "build me…" or launch one from the panel', W / 2, hub.y + hub.r + 74);
}

function frame(t) {
  requestAnimationFrame(frame);
  if (document.hidden || t - lastDraw < 33) return; lastDraw = t;
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#050508'; ctx.fillRect(0, 0, W, H);
  drawStars(t);
  rooms.forEach(r => drawCorridor(r, t));
  drawHub(t);
  rooms.forEach(r => drawRoom(r, t));
  if (S && !rooms.length) drawEmpty();
}

cv.addEventListener('click', e => {
  const b = cv.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top;
  if (Math.hypot(x - hub.x, y - hub.y) <= hub.r + 16) { selected = 'hub'; renderSide(); return; }
  const r = rooms.find(r => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
  if (r) { selected = r.m.id; renderSide(); }
});
cv.addEventListener('mousemove', e => {
  const b = cv.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top;
  const over = Math.hypot(x - hub.x, y - hub.y) <= hub.r + 16 || rooms.some(r => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
  cv.style.cursor = over ? 'pointer' : 'default';
});

function poll() { clearTimeout(pollTimer); if (!document.hidden) refresh(); pollTimer = setTimeout(poll, 4000); }
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
if (new URLSearchParams(location.search).get('connected') === 'etsy') { selected = 'links'; history.replaceState(null, '', '/station'); }
resize(); poll(); requestAnimationFrame(frame);
</script>
</body>
</html>
"""
