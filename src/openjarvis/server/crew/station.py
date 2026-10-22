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
  #main { display:grid; grid-template-columns:210px 1fr 340px; min-height:0; }
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
  .avatar { width:30px; height:30px; flex-shrink:0; border-radius:50%; background-color:rgba(0,229,255,0.06); border:1px solid var(--border); background-repeat:no-repeat; }
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
  #chat-log { height:118px; overflow-y:auto; display:flex; flex-direction:column; gap:8px; padding-right:2px; scrollbar-width:thin; scrollbar-color:var(--cyan-dim) transparent; }
  .cm { max-width:92%; padding:8px 10px; font-size:12px; line-height:1.5; border-radius:2px; white-space:pre-wrap; overflow-wrap:anywhere; }
  .cm.u { align-self:flex-end; background:rgba(255,109,0,0.08); border:1px solid rgba(255,109,0,0.25); color:#ffa040; }
  .cm.a { align-self:flex-start; background:rgba(0,229,255,0.04); border:1px solid rgba(0,229,255,0.14); }
  .cm .tg { font-size:9px; letter-spacing:2px; opacity:0.55; margin-bottom:3px; }
  .agent { border:1px solid var(--border); border-radius:2px; padding:6px 8px; margin-bottom:6px; cursor:default; background:rgba(0,229,255,0.02); }
  .agent-top { display:flex; align-items:center; gap:7px; margin-bottom:5px; }
  .agent-name { font-family:'Orbitron',monospace; font-size:11px; letter-spacing:2px; font-weight:700; }
  .agent-role { font-size:9px; letter-spacing:1px; color:var(--text-dim); text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .agent-can { font-size:11px; line-height:1.5; color:var(--text); }
  .agent-cannot { font-size:10px; line-height:1.5; color:var(--text-dim); margin-top:2px; }
  .agent-st { font-size:9px; letter-spacing:1px; color:var(--text-dim); margin-top:2px; padding-left:37px; text-transform:uppercase; }
  .pnl { border:1px solid var(--border); border-radius:2px; padding:8px 10px; margin:8px 0; background:rgba(0,229,255,0.02); }
  .pnl-t { font-size:9px; letter-spacing:2px; color:var(--text-dim); margin-bottom:6px; text-transform:uppercase; }
  .pnl-g { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px 10px; }
  .pnl-l { font-size:9px; letter-spacing:1px; color:var(--text-dim); text-transform:uppercase; }
  .pnl-v { font-size:14px; color:var(--text); margin-top:2px; }
  .pnl-table { margin:8px 0; font-size:11px; }
  .pnl-row { display:grid; grid-template-columns:1.6fr 1fr 1fr 1fr 1fr; gap:6px; padding:5px 0; border-bottom:1px solid rgba(0,229,255,0.05); align-items:center; }
  .pnl-row span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .pnl-row.hd { font-size:9px; letter-spacing:1px; color:var(--text-dim); text-transform:uppercase; }
  .pnl-note { font-size:10px; color:var(--text-dim); margin-top:5px; line-height:1.5; opacity:0.8; }
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
    #center #stage { height:min(100vw, 68dvh); flex:0 0 auto; }
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
  <span class="chip" title="Money in today (Stripe, all products) and money out today (crew + Helix AI + hosting)">TODAY IN <b id="c-in" style="color:#00ff99">$0.00</b> · OUT <b id="c-out" style="color:#ff8a2a">$0.00</b> · NET <b id="c-net">$0.00</b></span>
  <span class="chip hide-m" title="The crew's AI spend today against its daily safety cap (CREW_DAILY_CAP_USD)">CREW AI <b id="c-spend">$0.00</b></span>
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
const PROJECT_COLORS = { flipdeck:'#ff6d00', marnyx:'#b06cff', deadzone:'#ff3344', aibussol:'#00e5ff', 'new':'#ff4fb8' };
const ROLE_COLORS = { lead:'#00e5ff', researcher:'#5ad1ff', builder:'#4dffc3', writer:'#ffc14d', designer:'#ff7ad9', operator:'#ff6d00', reviewer:'#c8ff4d' };
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
  document.getElementById('c-spend').textContent = money(S.spend.today_usd) + ' of ' + money(S.spend.daily_cap_usd) + ' cap';
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
  if (selected === 'hub' || selected === 'links') side.append(overviewSec(), inboxSec(), missionsSec(), ledgerSec(S.events, 'Station Ledger'));
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
    const top = mk('div', 'agent-top'); const d = mk('span', 'avatar'); d.style.borderColor = alpha(ROLE_COLORS[a.role] || '#00e5ff', 0.5);
    if (a.role === 'lead') { d.style.backgroundImage = 'url(/crew/helix.jpg)'; d.style.backgroundSize = 'cover'; d.style.backgroundPosition = 'center 20%'; }
    else { const row = BOT_ROWS.indexOf(a.role); d.style.backgroundImage = 'url(/crew/bots.png?v=' + BOT_VER + ')'; const A = 38; d.style.backgroundSize = (17 * A) + 'px ' + (A * BOT_ROWS.length) + 'px'; d.style.backgroundPosition = '-4px ' + (-A * Math.max(0, row) - 1) + 'px'; }
    const nm = mk('span', 'agent-name', a.name); nm.style.color = ROLE_COLORS[a.role] || '#00e5ff';
    // compact card: face, name, role and status; what it can and cannot do shows on hover
    const who = mk('div', 'grow'); who.style.minWidth = '0'; who.append(nm, mk('div', 'agent-role', (a.role === 'lead' ? 'lead' : a.role) + ' · ' + a.model));
    top.append(d, who); top.style.marginBottom = '2px';
    c.title = a.name + ': ' + a.can + '\nLimits: ' + a.cannot;
    c.append(top);
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
  const site = (S && S.products && S.products[key]) || (FIN && FIN.products && FIN.products[key]) || null;
  if (site) s.append(mk('div', 'dim', (site.live ? 'Site live' : 'Site NOT answering') + (site.url ? ' · ' + site.url : '') + (site.checked_at ? ' · checked ' + ago(site.checked_at) : '')));
  const r = FIN && FIN.factories ? FIN.factories[key] : null;
  if (r) { s.append(pnlBlock(r, 'P&L')); s.append(mk('div', 'dim', costLine(r))); }
  else s.append(mk('div', 'dim', FIN ? 'No money in or out for this factory yet.' : 'Loading the numbers…'));
  if (key === 'aibussol' && FIN && FIN.aibussol_stats) { const a = FIN.aibussol_stats; s.append(mk('div', 'dim', a.users_total + ' users · ' + a.active_24h + ' active today · ' + a.subscribers_active + ' subscribers')); }
  // who is working here right now
  const crewHere = b.tasks.filter(t => ['running', 'waiting', 'queued'].includes(t.status));
  const cl = mk('div', 'lbl'); cl.style.marginTop = '12px'; cl.textContent = 'Crew in this factory'; s.append(cl);
  if (!crewHere.length) s.append(mk('div', 'dim', 'Nobody is working here right now.'));
  crewHere.forEach(t => {
    const who = (S.roster || []).find(a => a.role === t.role);
    const row = mk('div', 'row'); const d = mk('span', 'dot'); d.style.background = ROLE_COLORS[t.role] || '#00e5ff';
    row.append(d, mk('span', 'grow', (who ? who.name : t.role) + ' · ' + t.title), mk('span', 'tag', t.status)); s.append(row);
  });
  const ml = mk('div', 'lbl'); ml.style.marginTop = '12px'; ml.textContent = 'Missions'; s.append(ml);
  if (!b.missions.length) { s.append(mk('div', 'dim', 'None yet. Tell Helix what to build for ' + (b.isNew ? 'this business' : b.label.charAt(0) + b.label.slice(1).toLowerCase()) + ' and the crew will work here.')); return s; }
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
// Hub and spokes: Helix's core in the middle and eight identical modules spaced evenly around her, each turned to face the core
// and joined by a straight docking tube into the middle of its inner wall. Products, outside platforms and the next business all
// use the same module. Crew members walk around inside the module they are working in; free ones walk Helix's deck; one waiting
// on Michael walks the tube to Helix with a question.
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let W = 0, H = 0, DPR = 1, SC = 1, stars = [], sky = null, core = { x: 0, y: 0, r: 60 }, mods = [], bays = [], lastT = 0;
const bots = new Map();
const helixImg = new Image(); let helixReady = false;
helixImg.onload = () => { helixReady = true; }; helixImg.src = '/crew/helix.jpg';
// the crew: Michael's robot model, rendered per role (front, facing right, facing left) into one sheet: crew/assets/bots.png
const BOT_VER = '1c13d86a';   // changes whenever bots.png changes, so browsers never mix an old sheet with new code
const BOT_ROWS = ['lead', 'researcher', 'builder', 'writer', 'designer', 'operator', 'reviewer'], BOT_CELL = 64;
// sheet columns: 0 standing (front), then 4 walk frames each for front, back, right, left
const BOT_DIRS = { front: 0, back: 1, right: 2, left: 3 };
function dirOf(dx, dy, prev) { if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) return prev || 'front'; return Math.abs(dx) >= Math.abs(dy) * 0.8 ? (dx > 0 ? 'right' : 'left') : (dy > 0 ? 'front' : 'back'); }
const botSheet = new Image(); let botReady = false;
botSheet.onload = () => { botReady = true; }; botSheet.src = '/crew/bots.png?v=' + BOT_VER;
const PRODUCTS = [
  { key: 'flipdeck', label: 'FLIPDECK', color: '#ff6d00' },
  { key: 'marnyx', label: 'MARNYX', color: '#b06cff' },
  { key: 'deadzone', label: 'DEADZONE', color: '#ff3344' },
];
const PLATFORMS = [
  { key: 'stripe', label: 'STRIPE', color: '#4f6bff' },
  { key: 'etsy', label: 'ETSY', color: '#ffd23f' },
  { key: 'google_play', label: 'GOOGLE PLAY', color: '#34d17a' },
  { key: 'app_store', label: 'APP STORE', color: '#3fc8ff' },
];
const RING = ['flipdeck', 'stripe', 'marnyx', 'etsy', 'deadzone', 'google_play', 'new', 'app_store'];   // clockwise from the top
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
    b.idleText = site.live ? 'LIVE · STANDING BY' : 'SITE NOT ANSWERING';
  }
  return b;
}


function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

function chamfer(x, y, w, h, c) { ctx.beginPath(); ctx.moveTo(x + c, y); ctx.lineTo(x + w - c, y); ctx.lineTo(x + w, y + c); ctx.lineTo(x + w, y + h - c); ctx.lineTo(x + w - c, y + h); ctx.lineTo(x + c, y + h); ctx.lineTo(x, y + h - c); ctx.lineTo(x, y + c); ctx.closePath(); }

function alpha(hex, a) { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')'; }

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

function drawBot(x, y, color, status, t, seed, facing, sc, moving) {
  sc = sc || 1;
  if (typeof facing !== 'string') facing = facing < 0 ? 'left' : 'right';
  const bob = moving ? Math.abs(Math.sin(t * 0.016 + seed)) * 0.8 : status === 'running' ? Math.sin(t * 0.008 + seed) * 0.8 : status === 'waiting' ? Math.abs(Math.sin(t * 0.01 + seed)) * 1.4 : 0;
  const dim = status === 'queued' || status === 'cancelled';
  ctx.save(); ctx.translate(x, y - bob); ctx.scale(sc, sc); ctx.globalAlpha = dim ? 0.5 : 1;
  ctx.fillStyle = 'rgba(0,0,0,0.4)'; ctx.beginPath(); ctx.ellipse(0, 9 + bob, 5.5, 1.8, 0, 0, 6.29); ctx.fill();
  const row = BOT_ROWS.indexOf(Object.keys(ROLE_COLORS).find(k => ROLE_COLORS[k] === color) || '');
  if (botReady && row >= 0) {
    const H = 36, d = BOT_DIRS[facing] != null ? BOT_DIRS[facing] : 0;
    const col = moving ? 1 + d * 4 + Math.floor(t * 0.008 + seed * 7) % 4 : 0;     // a real walk cycle; standing faces you
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(botSheet, col * BOT_CELL, row * BOT_CELL, BOT_CELL, BOT_CELL, -H / 2, 10.5 - H * 0.873, H, H);
    ctx.restore();
    if (status === 'running' && !moving) {
      const s = (t * 0.004 + seed) % 1; ctx.fillStyle = alpha(color, 1 - s); ctx.fillRect(x + 7 * sc, y - (15 + s * 7) * sc, 1.6 * sc, 1.6 * sc);
    }
    return;
  }
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

function platformModule(p) {
  const c = S && S.connections ? S.connections[p.key] : null, on = !!(c && c.connected), built = !!(c && c.built);
  return { key: p.key, label: p.label, brand: p.color, color: p.color, platform: true, tasks: [], missions: [],
           powered: on, state: on ? 'ONLINE' : built ? 'OFFLINE' : 'COMING SOON', idleText: on ? 'CONNECTED' : built ? 'TAP TO CONNECT' : 'UNDER CONSTRUCTION' };
}
function newKeys() {
  const ms = S ? S.missions.filter(m => m.status !== 'cancelled') : [], seen = [];
  ms.forEach(m => { const k = factoryKey(m); if (k.startsWith('new:') && !seen.includes(k)) seen.push(k); });
  return seen;
}
function newBay(k) {
  const m = S.missions.find(x => factoryKey(x) === k);
  return fillBay({ key: k, label: String(m.title || 'NEW BUSINESS').toUpperCase(), color: '#ff4fb8', isNew: true });
}
function coreBay() { return fillBay({ key: 'aibussol', label: 'AIBUSSOL', color: '#00e5ff', isCore: true }); }
function factories() {                                                   // every factory, for the side panel
  return PRODUCTS.map(f => fillBay(Object.assign({}, f))).concat([coreBay()], newKeys().map(newBay));
}

function layout() {
  SC = Math.max(0.34, Math.min(1.25, Math.min(W / 1060, H / 800)));
  core = { x: W / 2, y: H / 2, r: 94 * SC };
  const mw = 184 * SC, mh = 116 * SC, gap = 46 * SC;
  const D = Math.max(core.r + 52 * SC + mh / 2, (mw + gap) / (2 * Math.sin(Math.PI / 8)));
  const prod = {}; PRODUCTS.forEach(f => { prod[f.key] = fillBay(Object.assign({}, f)); });
  const plat = {}; PLATFORMS.forEach(p => { plat[p.key] = platformModule(p); });
  const nk = newKeys();
  const fresh = nk.length ? newBay(nk[0]) : { key: 'expansion', label: 'EXPANSION BAY', color: '#ff4fb8', expansion: true, tasks: [], missions: [], state: 'OPEN', idleText: 'NEXT BUSINESS' };
  mods = RING.map((k, i) => {
    const m = k === 'new' ? fresh : (prod[k] || plat[k]);
    const a = -Math.PI / 2 + i * Math.PI / 4;
    return Object.assign(m, { a, ux: Math.cos(a), uy: Math.sin(a), cx: core.x + Math.cos(a) * D, cy: core.y + Math.sin(a) * D, w: mw, h: mh, rot: a + Math.PI / 2, D });
  });
  bays = mods.filter(m => !m.platform && !m.expansion);
  core.bay = coreBay();
}

// a module's own frame: x across its inner wall, y from its outer end (-) to the wall facing Helix (+)
function toWorld(m, lx, ly) { const c = Math.cos(m.rot), s = Math.sin(m.rot); return [m.cx + lx * c - ly * s, m.cy + lx * s + ly * c]; }
function toLocal(m, x, y) { const dx = x - m.cx, dy = y - m.cy, c = Math.cos(m.rot), s = Math.sin(m.rot); return [dx * c + dy * s, -dx * s + dy * c]; }
function inModule(m, x, y) { const [lx, ly] = toLocal(m, x, y); return Math.abs(lx) <= m.w / 2 && Math.abs(ly) <= m.h / 2; }
function hallW() { return 20 * SC; }
function modLive(m) { return !!m.powered || m.tasks.some(k => k.status === 'running'); }

// a docking tube: straight from Helix's core into the middle of the module's inner wall
function drawSpoke(m, t) {
  const site = S && S.products ? S.products[m.key] : null;               // a live product site keeps its tube flowing too
  const live = m.tasks.some(k => k.status === 'running') || (m.platform && m.powered) || !!(site && site.live);
  const col = m.color, hw = hallW() / 2, wall = 4 * SC;
  const r1 = core.r + 3, r2 = m.D - m.h / 2 + 2, ux = m.ux, uy = m.uy, nx = -uy, ny = ux;
  const P = (r, o) => [core.x + ux * r + nx * o, core.y + uy * r + ny * o];
  const quad = (o1, o2) => { const a = P(r1, o1), b = P(r2, o1), c = P(r2, o2), d = P(r1, o2); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.lineTo(c[0], c[1]); ctx.lineTo(d[0], d[1]); ctx.closePath(); };
  ctx.fillStyle = 'rgba(0,0,0,0.55)'; quad(hw + wall + 3, -hw - wall - 3); ctx.fill();
  const g = ctx.createLinearGradient(...P(r1, hw + wall), ...P(r1, -hw - wall));
  g.addColorStop(0, '#2a4559'); g.addColorStop(0.2, '#162a3a'); g.addColorStop(0.5, '#0a141e'); g.addColorStop(0.8, '#162a3a'); g.addColorStop(1, '#2a4559');
  ctx.fillStyle = g; quad(hw + wall, -hw - wall); ctx.fill();
  ctx.strokeStyle = 'rgba(160,200,220,0.4)'; ctx.lineWidth = 1;
  for (const o of [hw + wall, -hw - wall]) { const a = P(r1, o), b = P(r2, o); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
  for (let r = r1 + 6 * SC; r < r2 - 2; r += 8 * SC) {                      // ribs
    const a = P(r, hw + wall), b = P(r, -hw - wall);
    ctx.strokeStyle = 'rgba(0,0,0,0.35)'; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
  }
  for (const o of [hw, -hw]) {                                              // lit rails
    const a = P(r1, o), b = P(r2, o); ctx.strokeStyle = alpha(col, live ? 0.8 : 0.25); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
  }
  if (live) {
    // two lanes: the factory's colour flows in to Helix, bright green flows out from Helix to the factory
    const lane = hw * 0.45, dot = 2.2 * SC + 0.6;
    for (let k = 0; k < 2; k++) {
      const p = (t * 0.0003 + k / 2) % 1;
      const [ix, iy] = P(r2 + (r1 - r2) * p, lane);                 // factory -> Helix
      ctx.fillStyle = alpha(col, 0.95); ctx.shadowColor = col; ctx.shadowBlur = 10; ctx.beginPath(); ctx.arc(ix, iy, dot, 0, 6.29); ctx.fill();
      const q = (t * 0.0003 + k / 2 + 0.25) % 1;
      const [ox, oy] = P(r1 + (r2 - r1) * q, -lane);                // Helix -> factory
      ctx.fillStyle = '#00ff66'; ctx.shadowColor = '#00ff66'; ctx.beginPath(); ctx.arc(ox, oy, dot, 0, 6.29); ctx.fill(); ctx.shadowBlur = 0;
    }
  }
  // docking node next to the core
  const [nxp, nyp] = P(r1 + 12 * SC, 0);
  ctx.save(); ctx.translate(nxp, nyp); ctx.rotate(m.rot);
  const nsz = hallW() + 10 * SC;
  ctx.fillStyle = '#1f374a'; rr(-nsz / 2, -7 * SC, nsz, 14 * SC, 3 * SC); ctx.fill();
  ctx.strokeStyle = 'rgba(160,200,220,0.45)'; ctx.lineWidth = 1; rr(-nsz / 2, -7 * SC, nsz, 14 * SC, 3 * SC); ctx.stroke();
  ctx.fillStyle = alpha(col, live ? 0.95 : 0.35); ctx.fillRect(-nsz / 2 + 3 * SC, -1, nsz - 6 * SC, 2);
  ctx.restore();
}

function drawModule(m, t, dt) {
  const sel = m.platform ? selected === 'links' : selected === 'f:' + m.key;
  const live = modLive(m), lit = live || m.tasks.length > 0, col = m.color;
  const w = m.w, h = m.h, x0 = -w / 2, y0 = -h / 2, c = 12 * SC;
  ctx.save(); ctx.translate(m.cx, m.cy); ctx.rotate(m.rot);
  if (m.expansion) {
    ctx.setLineDash([6 * SC, 6 * SC]); ctx.strokeStyle = alpha(col, 0.3 + 0.12 * Math.sin(t * 0.002)); ctx.lineWidth = 1.2;
    chamfer(x0, y0, w, h, c); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = alpha(col, 0.08);
    for (let i = 1; i < 6; i++) { ctx.beginPath(); ctx.moveTo(x0 + w * i / 6, y0 + 6); ctx.lineTo(x0 + w * i / 6, y0 + h - 6); ctx.stroke(); }
    ctx.restore(); return;
  }
  if (lit) { ctx.shadowColor = col; ctx.shadowBlur = 16 * SC + 4; }
  chamfer(x0, y0, w, h, c);
  const g = ctx.createLinearGradient(0, y0, 0, y0 + h); g.addColorStop(0, '#162536'); g.addColorStop(1, '#070c13');
  ctx.fillStyle = g; ctx.fill(); ctx.shadowBlur = 0;
  ctx.strokeStyle = 'rgba(150,190,210,0.35)'; ctx.lineWidth = 3 * SC + 0.5; chamfer(x0, y0, w, h, c); ctx.stroke();
  ctx.strokeStyle = alpha(col, sel ? 1 : lit ? 0.85 : 0.6); ctx.lineWidth = sel ? 2 : 1.4; chamfer(x0 + 2.5 * SC, y0 + 2.5 * SC, w - 5 * SC, h - 5 * SC, c - SC); ctx.stroke();
  // outer-end band with running lights
  const band = 10 * SC;
  ctx.fillStyle = alpha(col, lit ? 0.32 : 0.2); ctx.fillRect(x0 + c, y0 + 3 * SC, w - 2 * c, band);
  for (let i = 0; i < 5; i++) { ctx.fillStyle = alpha(col, lit ? 0.4 + 0.5 * ((Math.floor(t / 300) + i) % 5 === 0 ? 1 : 0) : 0.15); ctx.fillRect(x0 + c + 6 * SC + i * 8 * SC, y0 + 3 * SC + band / 2 - 1, 4 * SC, 2); }
  // floor
  const I = { x: x0 + 9 * SC, y: y0 + band + 7 * SC, w: w - 18 * SC, h: h - band - 16 * SC };
  ctx.fillStyle = 'rgba(4,8,13,0.9)'; rr(I.x, I.y, I.w, I.h, 3 * SC); ctx.fill();
  ctx.strokeStyle = alpha(col, 0.07); ctx.lineWidth = 1;
  const tile = 11 * SC;
  for (let gx = I.x + tile; gx < I.x + I.w; gx += tile) { ctx.beginPath(); ctx.moveTo(gx, I.y); ctx.lineTo(gx, I.y + I.h); ctx.stroke(); }
  for (let gy = I.y + tile; gy < I.y + I.h; gy += tile) { ctx.beginPath(); ctx.moveTo(I.x, gy); ctx.lineTo(I.x + I.w, gy); ctx.stroke(); }
  // consoles along the outer wall
  const n = Math.max(3, Math.min(6, m.tasks.length || 4)), desks = [];
  for (let i = 0; i < n; i++) {
    const cx = I.x + I.w * (i + 0.5) / n, cy = I.y + 3 * SC, cw2 = Math.min(26 * SC, I.w / n - 6 * SC), tk = m.tasks[i], on = tk && tk.status === 'running';
    ctx.fillStyle = '#1b3445'; rr(cx - cw2 / 2, cy, cw2, 11 * SC, 2); ctx.fill();
    ctx.fillStyle = on ? alpha(ROLE_COLORS[tk.role] || col, 0.6 + 0.35 * Math.sin(t * 0.012 + i)) : (!tk && m.powered) ? alpha(col, 0.35 + 0.25 * Math.sin(t * 0.005 + i * 1.7)) : 'rgba(40,70,90,0.4)';
    ctx.fillRect(cx - cw2 / 2 + 2 * SC, cy + 2 * SC, cw2 - 4 * SC, 6 * SC);
    desks.push({ fx: (cx - I.x) / I.w, fy: Math.min(0.5, 22 * SC / I.h) });
  }
  // machines on the side walls
  for (const side of [-1, 1]) {
    const mx = side < 0 ? I.x + 7 * SC : I.x + I.w - 7 * SC, my = I.y + I.h * 0.62;
    ctx.fillStyle = '#16303f'; ctx.beginPath(); ctx.arc(mx, my, 6 * SC, 0, 6.29); ctx.fill();
    ctx.strokeStyle = alpha(col, live ? 0.6 : 0.2); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(mx, my, 6 * SC, t * 0.002 * side, t * 0.002 * side + 4); ctx.stroke();
  }
  if (m.state === 'COMING SOON') { ctx.setLineDash([4, 5]); ctx.strokeStyle = 'rgba(142,207,224,0.2)'; rr(I.x + 16 * SC, I.y + 18 * SC, I.w - 32 * SC, I.h - 26 * SC, 3); ctx.stroke(); ctx.setLineDash([]); }
  // the doorway in the wall facing Helix, where the tube docks
  // an open gap in the wall: the tube's floor runs straight into the room, with a door post on each side
  const hw = hallW() / 2, wallW = 4 * SC, dTop = y0 + h - 9 * SC, dBot = y0 + h + 3 * SC + 1;
  const dg = ctx.createLinearGradient(-hw - wallW, 0, hw + wallW, 0);
  dg.addColorStop(0, '#162a3a'); dg.addColorStop(0.2, '#0a141e'); dg.addColorStop(0.8, '#0a141e'); dg.addColorStop(1, '#162a3a');
  ctx.fillStyle = dg; ctx.fillRect(-hw - wallW, dTop, (hw + wallW) * 2, dBot - dTop);
  ctx.strokeStyle = alpha(col, live ? 0.8 : 0.25); ctx.lineWidth = 1.2;
  for (const o of [hw, -hw]) { ctx.beginPath(); ctx.moveTo(o, dTop); ctx.lineTo(o, dBot); ctx.stroke(); }       // the tube's lit rails carry on inside
  ctx.fillStyle = '#2d4a60'; ctx.fillRect(-hw - wallW - 4 * SC, dTop, 4 * SC, dBot - dTop); ctx.fillRect(hw + wallW, dTop, 4 * SC, dBot - dTop);
  ctx.restore();
  m.I = I; m.desks = desks;
  // crew members, drawn upright wherever they are on the floor
  const sc = Math.max(0.7, Math.min(1.4, SC * 1.15));
  m.tasks.forEach((tk, i) => {
    if (tk.status === 'waiting') return;
    const desk = desks[i % desks.length], b = botFor(tk, I, desk, t);
    // a crew member starting work walks from Helix's deck down this factory's tube and in through the doorway
    if (b.commute == null) b.commute = (tk.status === 'running' || tk.status === 'queued') ? 0 : 1;
    if (b.commute < 1) {
      const len = Math.max(40, m.D - m.h / 2 - core.r);
      b.commute = Math.min(1, b.commute + (dt || 16) * 0.05 / len);
      const r = core.r + 2 + (len + 10 * SC) * b.commute;
      drawBot(core.x + m.ux * r, core.y + m.uy * r, ROLE_COLORS[tk.role] || col, tk.status, t, b.seed, dirOf(m.ux, m.uy, 'front'), sc * 0.85, true);
      if (b.commute >= 1) { b.fx = 0.5; b.fy = 0.96; }                         // through the door: carry on inside
      return;
    }
    stepBot(b, tk, desk, dt);
    const [bx, by] = toWorld(m, I.x + b.fx * I.w, I.y + b.fy * I.h);
    if (b.lastX != null) b.wf = dirOf(bx - b.lastX, by - b.lastY, b.wf);                  // face where it walks on screen (modules are turned)
    b.lastX = bx; b.lastY = by;
    drawBot(bx, by, ROLE_COLORS[tk.role] || col, tk.status, t, b.seed, b.moving ? (b.wf || 'front') : 'front', sc, b.moving);
    const ky = by - 16 * sc;
    if (tk.status === 'done') { ctx.strokeStyle = '#00ff99'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(bx - 4, ky); ctx.lineTo(bx - 1, ky + 3); ctx.lineTo(bx + 4, ky - 3); ctx.stroke(); }
    if (tk.status === 'failed') { ctx.strokeStyle = '#ff3344'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(bx - 3, ky - 2); ctx.lineTo(bx + 3, ky + 4); ctx.moveTo(bx + 3, ky - 2); ctx.lineTo(bx - 3, ky + 4); ctx.stroke(); }
  });
}

// a crew member waiting on Michael stands in the tube near Helix's door, holding up a question
function drawWaiting(m, t) {
  const waiting = m.tasks.filter(k => k.status === 'waiting');
  waiting.slice(0, 3).forEach((tk, i) => {
    const r = core.r + 30 * SC + i * 18 * SC + Math.sin(t * 0.0015 + i) * 2;
    const qx = core.x + m.ux * r, qyb = core.y + m.uy * r;
    drawBot(qx, qyb, ROLE_COLORS[tk.role] || m.color, 'waiting', t, i * 3.1, 'front', Math.max(0.8, SC), false);
    const qy = qyb - 26 * Math.max(0.8, SC);
    ctx.fillStyle = '#ff8a2a'; ctx.shadowColor = '#ff8a2a'; ctx.shadowBlur = 8; rr(qx - 6, qy, 12, 12, 3); ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#050508'; ctx.font = '700 10px Share Tech Mono, monospace'; ctx.textAlign = 'center'; ctx.fillText('?', qx, qy + 9);
  });
}

// a name plate just outside each module's outer end, always upright, plus the module's status line in its middle
function drawPlate(m, t) {
  const fs = Math.max(8, 11 * SC), fs2 = Math.max(7, 9 * SC);
  ctx.font = '700 ' + fs + 'px Orbitron, monospace'; const name = fitText((m.isNew ? '◆ ' : '') + m.label, 190 * SC + 20), nw = ctx.measureText(name).width;
  ctx.font = fs2 + 'px Share Tech Mono, monospace'; const st = m.state || '', sw = ctx.measureText(st).width;
  const pw = Math.max(nw, sw) + 18 * SC + 6, ph = fs + fs2 + 12 * SC;
  const ext = Math.abs(m.ux) * pw / 2 + Math.abs(m.uy) * ph / 2;
  let ox = m.cx + m.ux * (m.h / 2 + 8 * SC + ext), oy = m.cy + m.uy * (m.h / 2 + 8 * SC + ext);
  if (ox - pw / 2 < 2 || ox + pw / 2 > W - 2) { ox = m.cx; oy = m.cy - m.w / 2 - 6 - ph / 2; }      // no room at the side (phones): sit above the module
  const x = ox - pw / 2, y = oy - ph / 2;
  ctx.fillStyle = 'rgba(6,11,18,0.94)'; rr(x, y, pw, ph, 3); ctx.fill();
  ctx.strokeStyle = alpha(m.color, 0.55); ctx.lineWidth = 1; rr(x, y, pw, ph, 3); ctx.stroke();
  ctx.fillStyle = m.color; ctx.fillRect(x, y + 3, 2, ph - 6);
  ctx.textAlign = 'center';
  ctx.font = '700 ' + fs + 'px Orbitron, monospace'; ctx.fillStyle = m.color; ctx.fillText(name, ox, y + 5 * SC + fs);
  ctx.font = fs2 + 'px Share Tech Mono, monospace';
  ctx.fillStyle = st === 'DOWN' ? '#ff3344' : st === 'NEEDS YOU' || st === 'OFFLINE' ? '#ff8a2a' : st === 'WORKING' || st === 'ONLINE' || st === 'LIVE' ? '#00ff99' : 'rgba(142,207,224,0.65)';
  ctx.fillText(st, ox, y + 7 * SC + fs + fs2);
}

function drawCore(t) {
  const { x, y, r } = core, open = S ? S.questions.length : 0;
  const g = ctx.createRadialGradient(x, y, r * 0.5, x, y, r * 1.8); g.addColorStop(0, 'rgba(0,229,255,0.18)'); g.addColorStop(1, 'rgba(0,229,255,0)');
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 1.8, 0, 6.29); ctx.fill();
  const ring = hallW() + 8 * SC;                         // the deck is as wide as the tubes (floor plus walls)
  // deck ring (where the crew walks) with an armored rim
  ctx.fillStyle = '#132636'; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.29); ctx.fill();
  ctx.strokeStyle = 'rgba(150,190,210,0.5)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.29); ctx.stroke();
  ctx.strokeStyle = 'rgba(0,229,255,0.35)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, r - ring, 0, 6.29); ctx.stroke();
  for (let i = 0; i < 32; i++) {
    const a = i / 32 * Math.PI * 2, ca = Math.cos(a), sa = Math.sin(a);
    ctx.strokeStyle = 'rgba(0,0,0,0.3)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x + ca * (r - ring), y + sa * (r - ring)); ctx.lineTo(x + ca * r, y + sa * r); ctx.stroke();
  }
  // Helix
  const ir = r - ring - 1;
  ctx.save(); ctx.beginPath(); ctx.arc(x, y, ir, 0, 6.29); ctx.clip();
  ctx.fillStyle = '#050a12'; ctx.fillRect(x - ir, y - ir, ir * 2, ir * 2);
  if (helixReady) {
    const iw = helixImg.naturalWidth, ih = helixImg.naturalHeight, s2 = (ir * 2.15) / Math.min(iw, ih);
    ctx.globalAlpha = 0.96; ctx.drawImage(helixImg, x - iw * s2 / 2, y - ih * s2 * 0.42, iw * s2, ih * s2); ctx.globalAlpha = 1;
  }
  const vg = ctx.createRadialGradient(x, y, ir * 0.55, x, y, ir); vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,20,35,0.75)');
  ctx.fillStyle = vg; ctx.fillRect(x - ir, y - ir, ir * 2, ir * 2);
  ctx.fillStyle = 'rgba(0,229,255,0.12)'; ctx.fillRect(x - ir, y - ir + ((t * 0.04) % (ir * 2)), ir * 2, 2);
  ctx.restore();
  ctx.save(); ctx.translate(x, y);
  ctx.rotate(t * 0.0003); ctx.strokeStyle = 'rgba(0,229,255,0.4)'; ctx.setLineDash([2, 10]); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(0, 0, r + 6 * SC, 0, 6.29); ctx.stroke();
  ctx.rotate(-t * 0.0007); ctx.strokeStyle = open ? 'rgba(255,138,42,' + (0.5 + 0.4 * Math.sin(t * 0.005)) + ')' : 'rgba(255,109,0,0.18)';
  ctx.setLineDash([16, 10]); ctx.lineWidth = open ? 2.5 : 1; ctx.beginPath(); ctx.arc(0, 0, r - ring - 4, 0, 6.29); ctx.stroke(); ctx.setLineDash([]);
  ctx.restore();
  // crew on the deck: Aibussol's own work, then everyone with nothing to do, walking the ring
  const track = r - ring / 2, sc = Math.max(0.6, Math.min(0.95, SC * 0.85));
  const onDeck = [];
  if (core.bay) core.bay.tasks.filter(k => k.status !== 'waiting').forEach(tk => onDeck.push({ role: tk.role, status: tk.status }));
  (S && S.roster ? S.roster : []).filter(a => a.role !== 'lead' && !a.working && !a.waiting).forEach(a => onDeck.push({ role: a.role, status: 'idle' }));
  onDeck.forEach((a, i) => {
    // everyone keeps walking the ring; small enough to stay on the track
    const dir = i % 2 ? 1 : -1, ang = -Math.PI / 2 + i * (Math.PI * 2 / Math.max(onDeck.length, 1)) + t * 0.00007 * dir;
    const face = dirOf(-Math.sin(ang) * dir, Math.cos(ang) * dir, 'front');
    drawBot(x + Math.cos(ang) * track, y + Math.sin(ang) * track + 4 * sc, ROLE_COLORS[a.role] || '#00e5ff', a.status, t, i * 4.1, face, sc, true);
  });
  // the open-items badge (no name plate: the portrait is Helix)
  ctx.textAlign = 'center';
  if (open) {
    ctx.fillStyle = '#ff8a2a'; ctx.shadowColor = '#ff8a2a'; ctx.shadowBlur = 10; ctx.beginPath(); ctx.arc(x + r * 0.62, y - r * 0.62, 11, 0, 6.29); ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#050508'; ctx.font = '700 11px Share Tech Mono, monospace'; ctx.fillText(String(open), x + r * 0.62, y - r * 0.62 + 4);
  }
}

function frame(t) {
  requestAnimationFrame(frame);
  if (document.hidden || t - lastDraw < 33) return;
  const dt = Math.min(100, t - (lastT || t)); lastT = t; lastDraw = t;
  ctx.clearRect(0, 0, W, H);
  drawSky(t);
  mods.forEach(m => drawSpoke(m, t));
  mods.forEach(m => drawModule(m, t, dt));
  drawCore(t);
  mods.forEach(m => drawWaiting(m, t));
  mods.forEach(m => drawPlate(m, t));
  for (const [id, b] of bots) if (t - b.seen > 5000) bots.delete(id);
}

function hit(x, y) {
  if (Math.hypot(x - core.x, y - core.y) <= core.r + 8) return 'hub';
  const m = mods.find(m => inModule(m, x, y));
  if (!m) return null;
  return m.platform ? 'links' : m.expansion ? 'hub' : 'f:' + m.key;
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

// ── money: P&L per factory and in total (refreshed every minute; the server caches Stripe for 5) ──
let FIN = null, finTick = 0;
async function loadFinance() {
  const r = await api('/api/crew/finance').catch(() => null);
  if (!r || !r.ok) return;
  FIN = r.data;
  const t = FIN.total;
  document.getElementById('c-in').textContent = money(t.revenue_today);
  document.getElementById('c-out').textContent = money(t.cost_today);
  const net = document.getElementById('c-net'); net.textContent = (t.net_today < 0 ? '-' : '') + money(Math.abs(t.net_today)); net.style.color = t.net_today < 0 ? '#ff3344' : '#00ff99';
  if (S && (selected === 'hub' || String(selected).startsWith('f:'))) renderSide();
}
function pnlBlock(r, title) {
  const box = mk('div', 'pnl');
  if (title) box.append(mk('div', 'pnl-t', title));
  const grid = mk('div', 'pnl-g');
  const cell = (label, v, color) => { const c = mk('div', 'pnl-c'); c.append(mk('div', 'pnl-l', label)); const b = mk('div', 'pnl-v', v); if (color) b.style.color = color; c.append(b); grid.append(c); };
  const sign = v => (v < 0 ? '-' : '') + money(Math.abs(v));
  cell('In today', money(r.revenue_today), '#00ff99'); cell('Out today', money(r.cost_today), '#ff8a2a'); cell('Net today', sign(r.net_today), r.net_today < 0 ? '#ff3344' : '#00ff99');
  cell('In 30d', money(r.revenue_30d)); cell('Out 30d', money(r.cost_30d)); cell('Net 30d', sign(r.net_30d), r.net_30d < 0 ? '#ff3344' : '#00ff99');
  cell('MRR', money(r.mrr)); cell('Subscribers', String(r.subscribers || 0));
  box.append(grid);
  return box;
}
function costLine(r) {
  return 'Out today: crew ' + money(r.crew_cost_today || 0) + ' · AI ' + money(r.ai_cost_today || 0) + ' · hosting ' + money(r.fixed_cost_today || 0);
}
function overviewSec() {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'All businesses'), mk('span', 'act', FIN ? 'as of ' + new Date(FIN.as_of).toLocaleTimeString() : 'loading…')); s.append(l);
  if (!FIN) { s.append(mk('div', 'dim', 'Loading the numbers…')); return s; }
  s.append(pnlBlock(FIN.total));
  const names = { flipdeck: 'FlipDeck', marnyx: 'Marnyx', deadzone: 'Deadzone', aibussol: 'Aibussol', crew: 'Crew-made products' };
  const rows = Object.entries(FIN.factories).sort((a, b) => (b[1].revenue_30d - a[1].revenue_30d) || (b[1].cost_30d - a[1].cost_30d));
  const tb = mk('div', 'pnl-table');
  const hd = mk('div', 'pnl-row hd'); ['Today →', 'In', 'Out', 'Net 30d', 'MRR'].forEach(h => hd.append(mk('span', null, h))); tb.append(hd);
  rows.forEach(([k, r]) => {
    const row = mk('div', 'pnl-row'); row.style.cursor = k in names || k.startsWith('new:') ? 'pointer' : 'default';
    row.onclick = () => { if (k !== 'crew') { selected = 'f:' + k; renderSide(true); } };
    const nm = names[k] || (k.startsWith('new:') ? k.slice(4) : k);
    row.append(mk('span', 'grow', nm), mk('span', null, money(r.revenue_today)), mk('span', null, money(r.cost_today)));
    const n = mk('span', null, (r.net_30d < 0 ? '-' : '') + money(Math.abs(r.net_30d))); n.style.color = r.net_30d < 0 ? '#ff3344' : '#00ff99'; row.append(n, mk('span', null, money(r.mrr)));
    tb.append(row);
  });
  s.append(tb);
  const a = FIN.aibussol_stats;
  if (a) s.append(mk('div', 'dim', 'Aibussol: ' + a.users_total + ' users · ' + a.active_24h + ' active today · ' + a.subscribers_active + ' subscribers · ' + a.new_users_7d + ' new this week'));
  const live = Object.entries(FIN.products || {}).map(([k, p]) => (names[k] || k) + (p.live ? ' live' : ' DOWN')).join(' · ');
  if (live) s.append(mk('div', 'dim', 'Sites: ' + live));
  (FIN.notes || []).forEach(n => s.append(mk('div', 'pnl-note', n)));
  return s;
}

function poll() {
  clearTimeout(pollTimer);
  if (!document.hidden) { refresh(); if (chatTick++ % 3 === 0) loadChat(); if (finTick++ % 15 === 0) loadFinance(); }
  pollTimer = setTimeout(poll, 4000);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
if (new URLSearchParams(location.search).get('connected') === 'etsy') { selected = 'links'; history.replaceState(null, '', '/station'); }
resize(); poll(); loadChat(true); requestAnimationFrame(frame);
</script>
</body>
</html>
"""
