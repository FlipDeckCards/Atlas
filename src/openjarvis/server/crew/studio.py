"""The image studio: a page of its own (opened in a new browser tab from the Station's library) for viewing and editing one picture.

The whole picture is fitted to the screen (wheel or pinch to zoom, drag to pan, double-click to zoom), with these tools:
  Crop        drag on the picture to choose the area (handles, move, aspect presets)
  Erase       paint over what to remove; it becomes transparent
  Remove BG   make the flat background transparent (for sprites); same-coloured details inside the figure stay unless asked
  Change      type what to change and the image model (Nano Banana Pro) edits the picture; asks about the cost first
  Adjust      resize, brightness, contrast
Every edit is previewed first and saved as a NEW library item; the original is never changed. The page signs in with the Station's own token
(localStorage 'aibussol_token') and uses the same media API as the Station; no new server routes are needed beyond serving this page.
"""

STUDIO_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image studio</title>
<style>
  :root { --bg:#020609; --panel:#07121c; --border:rgba(0,229,255,0.25); --cyan:#00e5ff; --text:#cfeffc; --dim:#7aa3b3; --err:#ff3344; --ok:#00ff88; --orange:#ff8a2a; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text); font-family:'Share Tech Mono', ui-monospace, Menlo, Consolas, monospace; font-size:12px; overflow:hidden; }
  button, input, select, textarea { font-family:inherit; font-size:11px; }
  #app { display:flex; flex-direction:column; height:100%; }
  .bar, .foot { display:flex; align-items:center; gap:8px; padding:8px 12px; flex:0 0 auto; flex-wrap:wrap; }
  .bar { border-bottom:1px solid var(--border); }
  .foot { border-top:1px solid var(--border); }
  .title { flex:1; min-width:120px; color:var(--cyan); font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
  .meta { color:var(--dim); white-space:nowrap; }
  .btn { background:transparent; border:1px solid var(--border); color:var(--dim); padding:5px 9px; letter-spacing:1px; cursor:pointer; border-radius:2px; }
  .btn:hover:not(:disabled), .btn.on { border-color:var(--cyan); color:var(--cyan); }
  .btn:disabled { opacity:0.35; cursor:default; }
  .btn.go { border-color:var(--ok); color:var(--ok); }
  .btn.danger:hover { border-color:var(--err); color:var(--err); }
  #body { flex:1; min-height:0; display:flex; }
  #stage { position:relative; flex:1; min-width:0; overflow:hidden; cursor:grab; touch-action:none; background:#050b12; }
  #stage.grab { cursor:grabbing; }
  #stage.bg1 { background-color:#d4d4d4; background-image:linear-gradient(45deg,#9c9c9c 25%,transparent 25%,transparent 75%,#9c9c9c 75%),linear-gradient(45deg,#9c9c9c 25%,transparent 25%,transparent 75%,#9c9c9c 75%); background-size:20px 20px; background-position:0 0,10px 10px; }
  #stage.bg2 { background:#ffffff; }
  #img { position:absolute; left:0; top:0; transform-origin:0 0; max-width:none; user-select:none; -webkit-user-drag:none; }
  #ov { position:absolute; left:0; top:0; width:100%; height:100%; pointer-events:none; }
  #msg { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; text-align:center; padding:20px; color:var(--dim); pointer-events:none; }
  #panel { flex:0 0 340px; overflow-y:auto; border-left:1px solid var(--border); padding:10px 12px; background:var(--panel); }
  #panel.collapsed { display:none; }
  .row { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:8px; }
  .row label { color:var(--dim); font-size:10px; }
  .row input[type=number], .row select { background:rgba(0,229,255,0.04); border:1px solid var(--border); color:var(--text); padding:4px 5px; width:74px; }
  .row select { width:auto; }
  .row input[type=range] { flex:1; min-width:120px; }
  textarea { width:100%; background:rgba(0,229,255,0.04); border:1px solid var(--border); color:var(--text); padding:6px; resize:vertical; }
  .tabs { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:10px; }
  .tabs .btn { flex:1 1 auto; text-align:center; }
  .hint { color:var(--dim); font-size:10px; line-height:1.5; margin:4px 0 8px; }
  .result { border:1px solid var(--ok); padding:8px; margin-bottom:10px; background:rgba(0,255,136,0.05); }
  .result .row { margin-bottom:0; }
  #status { min-height:16px; margin-top:6px; color:var(--ok); line-height:1.4; }
  #status.err { color:var(--err); }
  .sec { margin-bottom:12px; }
  .sec h4 { margin:0 0 6px; color:var(--cyan); font-weight:400; letter-spacing:1px; font-size:10px; text-transform:uppercase; }
  @media (max-width:800px) { #body { flex-direction:column; } #panel { flex:0 0 42%; border-left:none; border-top:1px solid var(--border); } .hide-m { display:none; } }
</style></head>
<body>
<div id="app">
  <div class="bar">
    <span class="title" id="title">Image studio</span><span class="meta" id="meta"></span>
    <button class="btn" id="b-prev">&lsaquo; Prev</button><button class="btn" id="b-next">Next &rsaquo;</button>
    <button class="btn" id="b-tools">Tools</button><button class="btn" id="b-down">Download</button>
  </div>
  <div id="body">
    <div id="stage" class="bg0"><img id="img" alt="" draggable="false"><canvas id="ov"></canvas><div id="msg">Loading&hellip;</div></div>
    <aside id="panel"></aside>
  </div>
  <div class="foot">
    <button class="btn" id="z-out">&minus;</button><span class="meta" id="z-pct">100%</span><button class="btn" id="z-in">+</button>
    <button class="btn" id="z-fit">Fit</button><button class="btn" id="z-100">100%</button><button class="btn" id="z-bg">Background</button>
    <span class="meta hide-m" style="margin-left:auto">Scroll or pinch to zoom &middot; drag to pan (Space or right-drag while a tool is on) &middot; double-click to zoom &middot; &larr; &rarr; other pictures</span>
  </div>
</div>
<script>
(() => {
  const $ = id => document.getElementById(id);
  const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const stage = $('stage'), img = $('img'), ov = $('ov'), panel = $('panel'), msgEl = $('msg');
  const token = () => { try { return localStorage.getItem('aibussol_token') || ''; } catch (e) { return ''; } };
  const money = x => '$' + Number(x).toFixed(2);
  const fmtBytes = n => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';

  async function api(path, method, body) {
    const r = await fetch(path, { method: method || 'GET', headers: Object.assign({ Authorization: 'Bearer ' + token() }, body ? { 'Content-Type': 'application/json' } : {}), body: body ? JSON.stringify(body) : undefined });
    let data = {}; try { data = await r.json(); } catch (e) { /* not JSON */ }
    return { ok: r.ok, status: r.status, data: data };
  }
  const BLOBS = new Map();
  function blobFor(item) {                                   // the file, fetched with the sign-in and kept as a local blob URL
    if (BLOBS.has(item.id)) return BLOBS.get(item.id);
    const p = fetch('/api/media/items/' + encodeURIComponent(item.id) + '/file', { headers: { Authorization: 'Bearer ' + token() } })
      .then(async r => { if (!r.ok) throw new Error(String(r.status)); return URL.createObjectURL(await r.blob()); });
    BLOBS.set(item.id, p); p.catch(() => BLOBS.delete(item.id));
    return p;
  }

  // ── state ──
  const S = { id: '', item: null, list: [], url: '', nw: 0, nh: 0, scale: 1, fit: 1, x: 0, y: 0, fitted: true, bg: 0,
              tool: '', crop: null, aspect: 0, strokes: [], cur: null, brush: 40, hover: null, preview: null, space: false, busy: false, est: null, tol: 20, inside: false };

  // ── the picture: fit, zoom, pan ──
  const size = () => ({ w: stage.clientWidth, h: stage.clientHeight });
  function clampView() {
    const { w, h } = size(), iw = S.nw * S.scale, ih = S.nh * S.scale;
    S.x = iw <= w ? (w - iw) / 2 : Math.min(0, Math.max(w - iw, S.x));        // a smaller picture stays centred; a bigger one cannot leave the view
    S.y = ih <= h ? (h - ih) / 2 : Math.min(0, Math.max(h - ih, S.y));
  }
  function apply() {
    clampView();
    img.style.transform = 'translate(' + S.x + 'px,' + S.y + 'px) scale(' + S.scale + ')';
    img.style.imageRendering = S.scale >= 3 ? 'pixelated' : 'auto';           // pixel art stays crisp when zoomed in
    $('z-pct').textContent = Math.round(S.scale * 100) + '%';
    drawOverlay();
  }
  function fitView() {
    if (!S.nw) return;
    const { w, h } = size();
    S.fit = Math.max(0.01, Math.min((w - 24) / S.nw, (h - 24) / S.nh, 2));
    S.scale = S.fit; S.fitted = true; S.x = S.y = 0; apply();
  }
  function zoomAt(f, cx, cy) {
    const ns = Math.min(24, Math.max(S.fit, S.scale * f));
    if (ns === S.scale) return;
    S.x = cx - (cx - S.x) * (ns / S.scale); S.y = cy - (cy - S.y) * (ns / S.scale);
    S.scale = ns; S.fitted = ns <= S.fit * 1.001; apply();
  }
  const zoomMid = f => { const { w, h } = size(); zoomAt(f, w / 2, h / 2); };
  const toImg = e => { const r = stage.getBoundingClientRect(); const sx = e.clientX - r.left, sy = e.clientY - r.top; return { sx: sx, sy: sy, x: (sx - S.x) / S.scale, y: (sy - S.y) / S.scale }; };
  const toScr = (x, y) => [x * S.scale + S.x, y * S.scale + S.y];
  img.addEventListener('load', () => {
    if (img.naturalWidth !== S.nw || img.naturalHeight !== S.nh) { S.nw = img.naturalWidth; S.nh = img.naturalHeight; img.style.width = S.nw + 'px'; msgEl.textContent = ''; fitView(); syncCrop(); }
    else apply();                                                            // the same size (a brightness preview): zoom and position stay
  });
  new ResizeObserver(() => { sizeOverlay(); if (!S.nw) return; if (S.fitted) fitView(); else apply(); }).observe(stage);
  function sizeOverlay() { const d = window.devicePixelRatio || 1; ov.width = Math.round(stage.clientWidth * d); ov.height = Math.round(stage.clientHeight * d); }

  function setBg(n) { S.bg = n; stage.className = (stage.className.replace(/\bbg\d\b/g, '') + ' bg' + n).trim(); }
  function setStatus(text, bad) { const s = $('status'); if (s) { s.textContent = text || ''; s.className = bad ? 'err' : ''; } }

  // ── overlay: crop box, brush strokes, brush circle ──
  const HANDLE = 10;
  function cropScr() { const c = S.crop; const a = toScr(c.l, c.t), b = toScr(c.r, c.b); return { x0: a[0], y0: a[1], x1: b[0], y1: b[1] }; }
  function drawOverlay() {
    const d = window.devicePixelRatio || 1, ctx = ov.getContext('2d'), W = stage.clientWidth, H = stage.clientHeight;
    ctx.setTransform(d, 0, 0, d, 0, 0); ctx.clearRect(0, 0, W, H);
    if (S.preview || !S.nw) return;
    if (S.tool === 'crop' && S.crop) {
      const r = cropScr();
      ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(0, 0, W, H); ctx.clearRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
      ctx.strokeStyle = '#00e5ff'; ctx.lineWidth = 2; ctx.strokeRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
      ctx.strokeStyle = 'rgba(0,229,255,0.35)'; ctx.lineWidth = 1; ctx.beginPath();
      for (let k = 1; k < 3; k++) { const gx = r.x0 + (r.x1 - r.x0) * k / 3, gy = r.y0 + (r.y1 - r.y0) * k / 3; ctx.moveTo(gx, r.y0); ctx.lineTo(gx, r.y1); ctx.moveTo(r.x0, gy); ctx.lineTo(r.x1, gy); }
      ctx.stroke();
      ctx.fillStyle = '#00e5ff';
      [[r.x0, r.y0], [(r.x0 + r.x1) / 2, r.y0], [r.x1, r.y0], [r.x0, (r.y0 + r.y1) / 2], [r.x1, (r.y0 + r.y1) / 2], [r.x0, r.y1], [(r.x0 + r.x1) / 2, r.y1], [r.x1, r.y1]]
        .forEach(p => ctx.fillRect(p[0] - 4, p[1] - 4, 8, 8));
      ctx.font = '11px monospace'; const label = Math.round(S.crop.r - S.crop.l) + ' × ' + Math.round(S.crop.b - S.crop.t);
      ctx.fillStyle = 'rgba(0,0,0,0.7)'; ctx.fillRect(r.x0, Math.max(0, r.y0 - 18), ctx.measureText(label).width + 10, 16); ctx.fillStyle = '#00e5ff'; ctx.fillText(label, r.x0 + 5, Math.max(12, r.y0 - 6));
    }
    if (S.tool === 'erase') {
      ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = 'rgba(255,50,110,0.55)'; ctx.fillStyle = 'rgba(255,50,110,0.55)';
      const all = S.cur ? S.strokes.concat([S.cur]) : S.strokes;
      all.forEach(s => {
        ctx.lineWidth = Math.max(1, 2 * s.r * S.scale);
        if (s.pts.length === 1) { const p = toScr(s.pts[0][0], s.pts[0][1]); ctx.beginPath(); ctx.arc(p[0], p[1], Math.max(0.5, s.r * S.scale), 0, 6.2832); ctx.fill(); return; }
        ctx.beginPath(); s.pts.forEach((pt, i) => { const p = toScr(pt[0], pt[1]); if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); }); ctx.stroke();
      });
      if (S.hover && !S.space) { ctx.lineWidth = 1.5; ctx.strokeStyle = '#ffffff'; ctx.beginPath(); ctx.arc(S.hover.sx, S.hover.sy, S.brush / 2, 0, 6.2832); ctx.stroke(); }
    }
  }

  // ── crop geometry ──
  function cropHit(sx, sy) {
    if (!S.crop) return null;
    const r = cropScr(), near = (a, b) => Math.abs(a - b) <= HANDLE;
    const inX = sx >= r.x0 - HANDLE && sx <= r.x1 + HANDLE, inY = sy >= r.y0 - HANDLE && sy <= r.y1 + HANDLE;
    let h = '';
    if (inX && near(sy, r.y0)) h += 'n'; else if (inX && near(sy, r.y1)) h += 's';
    if (inY && near(sx, r.x0)) h += 'w'; else if (inY && near(sx, r.x1)) h += 'e';
    if (h) return h;
    return (sx > r.x0 && sx < r.x1 && sy > r.y0 && sy < r.y1) ? 'move' : null;
  }
  const CURSORS = { n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize', ne: 'nesw-resize', sw: 'nesw-resize', nw: 'nwse-resize', se: 'nwse-resize', move: 'move' };
  function cropMove(d, p) {
    const W = S.nw, H = S.nh, cx = v => Math.min(W, Math.max(0, v)), cy = v => Math.min(H, Math.max(0, v)), MIN = 8;
    const x = cx(p.x), y = cy(p.y);
    if (d.mode === 'new') {
      const dx = x >= d.ax ? 1 : -1, dy = y >= d.ay ? 1 : -1; let w = Math.abs(x - d.ax), h = Math.abs(y - d.ay);
      if (S.aspect) { h = w / S.aspect; const maxH = dy > 0 ? H - d.ay : d.ay; if (h > maxH) { h = maxH; w = h * S.aspect; } const maxW = dx > 0 ? W - d.ax : d.ax; if (w > maxW) { w = maxW; h = w / S.aspect; } }
      const x2 = d.ax + dx * w, y2 = d.ay + dy * h; S.crop = { l: Math.min(d.ax, x2), r: Math.max(d.ax, x2), t: Math.min(d.ay, y2), b: Math.max(d.ay, y2) };
    } else if (d.mode === 'move') {
      const o = d.orig, w = o.r - o.l, h = o.b - o.t, nl = Math.min(W - w, Math.max(0, o.l + (p.x - d.start.x))), nt = Math.min(H - h, Math.max(0, o.t + (p.y - d.start.y)));
      S.crop = { l: nl, t: nt, r: nl + w, b: nt + h };
    } else {
      const c = Object.assign({}, d.orig), m = d.mode;
      if (S.aspect && m.length === 2) {                                         // a corner with a fixed shape: the opposite corner stays put
        const ax = m.includes('w') ? d.orig.r : d.orig.l, ay = m.includes('n') ? d.orig.b : d.orig.t;
        let w = Math.max(MIN, Math.abs(x - ax)), h = w / S.aspect; const maxH = m.includes('n') ? ay : H - ay; if (h > maxH) { h = maxH; w = h * S.aspect; } const maxW = m.includes('w') ? ax : W - ax; if (w > maxW) { w = maxW; h = w / S.aspect; }
        const x2 = m.includes('w') ? ax - w : ax + w, y2 = m.includes('n') ? ay - h : ay + h;
        S.crop = { l: Math.min(ax, x2), r: Math.max(ax, x2), t: Math.min(ay, y2), b: Math.max(ay, y2) };
      } else {
        if (m.includes('w')) c.l = Math.min(x, c.r - MIN); if (m.includes('e')) c.r = Math.max(x, c.l + MIN);
        if (m.includes('n')) c.t = Math.min(y, c.b - MIN); if (m.includes('s')) c.b = Math.max(y, c.t + MIN);
        S.crop = c;
      }
    }
    syncCrop(); drawOverlay();
  }
  function setAspect(v) {                                                       // reshape the current box to the chosen shape, around its centre
    S.aspect = v; if (!v || !S.crop) return;
    const c = S.crop, mx = (c.l + c.r) / 2, my = (c.t + c.b) / 2; let w = c.r - c.l, h = w / v;
    if (h > S.nh) { h = S.nh; w = h * v; } if (w > S.nw) { w = S.nw; h = w / v; }
    S.crop = { l: Math.max(0, Math.min(S.nw - w, mx - w / 2)), t: Math.max(0, Math.min(S.nh - h, my - h / 2)), r: 0, b: 0 };
    S.crop.r = S.crop.l + w; S.crop.b = S.crop.t + h; syncCrop(); drawOverlay();
  }
  let cropInputs = null;
  function syncCrop() { if (!cropInputs) return; const c = S.crop || { l: '', t: '', r: '', b: '' }; ['l', 't', 'r', 'b'].forEach(k => { cropInputs[k].value = c[k] === '' ? '' : Math.round(c[k]); }); }

  // ── pointer: tools draw, otherwise drag pans; wheel and pinch zoom ──
  const pts = new Map(); let pinch = 0, drag = null;
  const toolOn = () => (S.tool === 'crop' || S.tool === 'erase') && !S.preview && !S.space && !S.busy;
  const dist = () => { const v = Array.from(pts.values()); return Math.hypot(v[0].x - v[1].x, v[0].y - v[1].y); };
  stage.addEventListener('contextmenu', e => e.preventDefault());
  stage.addEventListener('wheel', e => { e.preventDefault(); const r = stage.getBoundingClientRect(); zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  stage.addEventListener('dblclick', e => {
    if (toolOn()) return;
    const r = stage.getBoundingClientRect();
    if (S.scale > S.fit * 1.05) fitView(); else zoomAt(Math.max(1, S.fit * 2.5) / S.scale, e.clientX - r.left, e.clientY - r.top);
  });
  stage.addEventListener('pointerdown', e => {
    try { stage.setPointerCapture(e.pointerId); } catch (err) { /* a pointer that is already gone */ } const first = pts.size === 0; pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) { drag = null; pinch = dist(); }
    if (first && e.button === 0 && toolOn()) {
      const p = toImg(e);
      if (S.tool === 'crop') {
        const hit = cropHit(p.sx, p.sy);
        if (hit) drag = { mode: hit, start: p, orig: Object.assign({}, S.crop) };
        else { const x = Math.min(S.nw, Math.max(0, p.x)), y = Math.min(S.nh, Math.max(0, p.y)); S.crop = { l: x, t: y, r: x, b: y }; drag = { mode: 'new', ax: x, ay: y }; }
      } else {
        S.cur = { r: Math.max(0.5, (S.brush / 2) / S.scale), pts: [[p.x, p.y]], last: [p.sx, p.sy] }; drag = { mode: 'erase' }; drawOverlay();
      }
      return;
    }
    stage.classList.add('grab');
  });
  stage.addEventListener('pointermove', e => {
    const p = pts.get(e.pointerId);
    if (!p) {
      const q = toImg(e);
      if (S.tool === 'crop' && toolOn()) stage.style.cursor = CURSORS[cropHit(q.sx, q.sy)] || 'crosshair';
      else if (S.tool === 'erase' && toolOn()) { stage.style.cursor = 'none'; S.hover = { sx: q.sx, sy: q.sy }; drawOverlay(); }
      else stage.style.cursor = '';
      return;
    }
    if (drag && pts.size === 1) {
      const q = toImg(e);
      if (drag.mode === 'erase') { const dx = q.sx - S.cur.last[0], dy = q.sy - S.cur.last[1]; if (dx * dx + dy * dy >= 4) { S.cur.pts.push([q.x, q.y]); S.cur.last = [q.sx, q.sy]; } S.hover = { sx: q.sx, sy: q.sy }; drawOverlay(); }
      else cropMove(drag, q);
      p.x = e.clientX; p.y = e.clientY; return;
    }
    if (pts.size === 1) { S.x += e.clientX - p.x; S.y += e.clientY - p.y; S.fitted = false; p.x = e.clientX; p.y = e.clientY; apply(); }
    else if (pts.size === 2) {
      p.x = e.clientX; p.y = e.clientY; const v = Array.from(pts.values()), d = dist(), r = stage.getBoundingClientRect();
      if (pinch > 0) zoomAt(d / pinch, (v[0].x + v[1].x) / 2 - r.left, (v[0].y + v[1].y) / 2 - r.top);
      pinch = d;
    }
  });
  function lift(e) {
    if (drag && pts.size === 1) {
      if (drag.mode === 'erase') {
        let s = S.cur; S.cur = null;
        if (s.pts.length > 300) { const k = Math.ceil(s.pts.length / 300); s.pts = s.pts.filter((_, i) => i % k === 0 || i === s.pts.length - 1); }
        if (S.strokes.length >= 400) setStatus('That is a lot of strokes. Preview the erase first, then carry on.', true); else S.strokes.push(s);
        syncErase();
      } else if (S.crop && (S.crop.r - S.crop.l < 8 || S.crop.b - S.crop.t < 8)) { S.crop = null; syncCrop(); }
      drag = null; drawOverlay();
    }
    pts.delete(e.pointerId); pinch = 0; if (!pts.size) stage.classList.remove('grab');
  }
  stage.addEventListener('pointerup', lift); stage.addEventListener('pointercancel', lift);
  stage.addEventListener('pointerleave', () => { S.hover = null; drawOverlay(); });

  // ── edits: preview first, then save as a NEW item ──
  async function runPreview(op, params) {
    if (S.busy) return false;
    setStatus('Working…');
    const r = await api('/api/media/items/' + encodeURIComponent(S.id) + '/edit', 'POST', { operation: op, params: params, preview: true });
    if (!r.ok) { setStatus(r.data.detail || 'That edit could not be previewed.', true); return false; }
    S.preview = { op: op, params: params };
    img.src = 'data:' + r.data.preview.mime + ';base64,' + r.data.preview.data_base64;
    if (op === 'erase' || op === 'remove_background') setBg(1);              // the checkerboard shows what is transparent
    showResult(true); setStatus('Preview only. Nothing is saved yet.'); drawOverlay();
    return true;
  }
  function discardPreview() { if (!S.preview) return; S.preview = null; img.src = S.url; showResult(false); setStatus(''); drawOverlay(); }
  async function savePreview() {
    if (!S.preview || S.busy) return;
    setStatus('Saving…');
    const r = await api('/api/media/items/' + encodeURIComponent(S.id) + '/edit', 'POST', { operation: S.preview.op, params: S.preview.params, preview: false });
    if (!r.ok) { setStatus(r.data.detail || 'Could not save the edit.', true); return; }
    changed(); await refreshList(); await load(r.data.item.id, true); setStatus('Saved as a new picture: ' + r.data.item.title);
  }
  function showResult(on) { const r = $('result'); if (r) r.hidden = !on; }
  function changed() { try { if (window.opener) window.opener.postMessage({ type: 'media-changed' }, location.origin); } catch (e) { /* the Station is closed */ } }

  // ── the tools panel ──
  const TOOLS = [['crop', 'Crop'], ['erase', 'Erase'], ['bg', 'Remove BG'], ['change', 'Change'], ['adjust', 'Adjust']];
  function buildPanel() {
    panel.textContent = '';
    const tabs = mk('div', 'tabs');
    TOOLS.forEach(([k, label]) => { const b = mk('button', 'btn' + (S.tool === k ? ' on' : ''), label); b.onclick = () => setTool(S.tool === k ? '' : k); tabs.append(b); });
    panel.append(tabs);
    const result = mk('div', 'result'); result.id = 'result'; result.hidden = !S.preview;
    const rr = mk('div', 'row'); const save = mk('button', 'btn go', 'Save as new'), disc = mk('button', 'btn', 'Discard');
    save.onclick = savePreview; disc.onclick = discardPreview; rr.append(mk('span', 'meta', 'Preview'), save, disc); result.append(rr, mk('div', 'hint', 'The original picture is kept. Saving makes a new one in your library.')); panel.append(result);
    const body = mk('div'); body.id = 'toolbody'; panel.append(body);
    panel.append(Object.assign(mk('div'), { id: 'status' }));
    const more = mk('div', 'row'); more.style.marginTop = '18px';
    const del = mk('button', 'btn danger', 'Delete picture'); del.onclick = deleteItem; more.append(del); panel.append(more);
    renderTool();
  }
  function setTool(k) {
    if (S.busy) return;
    if (S.preview) discardPreview();
    S.tool = k; S.hover = null; stage.style.cursor = ''; buildPanel(); drawOverlay();
  }
  function renderTool() {
    const body = $('toolbody'); body.textContent = ''; cropInputs = null;
    if (!S.tool) { body.append(mk('div', 'hint', 'Pick a tool. Crop: drag on the picture. Erase: paint over what to remove. Remove BG: clear a flat background. Change: describe an edit and the image model makes it. Adjust: resize, brightness, contrast.')); return; }
    ({ crop: toolCrop, erase: toolErase, bg: toolBg, change: toolChange, adjust: toolAdjust })[S.tool](body);
  }
  function numInput(v) { const i = document.createElement('input'); i.type = 'number'; i.step = 'any'; if (v != null) i.value = v; return i; }
  function slider(min, max, step, val) { const i = document.createElement('input'); i.type = 'range'; i.min = min; i.max = max; i.step = step; i.value = val; return i; }

  function toolCrop(body) {
    body.append(mk('div', 'hint', 'Drag on the picture to choose the area. Drag the squares to resize it, or drag inside it to move it.'));
    const r1 = mk('div', 'row'); cropInputs = {};
    [['l', 'left'], ['t', 'top'], ['r', 'right'], ['b', 'bottom']].forEach(([k, label]) => {
      const i = numInput(); i.min = 0; cropInputs[k] = i;
      i.onchange = () => { const v = Number(i.value); if (!S.crop) S.crop = { l: 0, t: 0, r: S.nw, b: S.nh }; S.crop[k] = v; const c = S.crop; c.l = Math.max(0, Math.min(c.l, c.r - 8)); c.t = Math.max(0, Math.min(c.t, c.b - 8)); c.r = Math.min(S.nw, Math.max(c.r, c.l + 8)); c.b = Math.min(S.nh, Math.max(c.b, c.t + 8)); syncCrop(); drawOverlay(); };
      r1.append(mk('label', null, label), i);
    });
    body.append(r1); syncCrop();
    const r2 = mk('div', 'row'), sel = document.createElement('select');
    [['Free shape', 0], ['Square 1:1', 1], ['4:3', 4 / 3], ['3:2', 3 / 2], ['16:9', 16 / 9], ['3:4', 3 / 4], ['2:3', 2 / 3], ['9:16', 9 / 16]].forEach(([t, v]) => { const o = document.createElement('option'); o.value = v; o.textContent = t; if (Math.abs(S.aspect - v) < 1e-9) o.selected = true; sel.append(o); });
    sel.onchange = () => setAspect(Number(sel.value)); r2.append(mk('label', null, 'Shape'), sel); body.append(r2);
    const r3 = mk('div', 'row'), all = mk('button', 'btn', 'Select all'), apply = mk('button', 'btn go', 'Preview crop');
    all.onclick = () => { S.crop = { l: 0, t: 0, r: S.nw, b: S.nh }; syncCrop(); drawOverlay(); };
    apply.onclick = () => { const c = S.crop; if (!c) { setStatus('Drag on the picture to choose the area first.', true); return; } runPreview('crop', { left: Math.round(c.l), top: Math.round(c.t), right: Math.round(c.r), bottom: Math.round(c.b) }); };
    r3.append(all, apply); body.append(r3);
  }
  let eraseNote = null;
  function syncErase() { if (eraseNote) eraseNote.textContent = S.strokes.length ? S.strokes.length + ' stroke' + (S.strokes.length === 1 ? '' : 's') + ' painted' : 'Nothing painted yet'; }
  function toolErase(body) {
    body.append(mk('div', 'hint', 'Paint over what you want gone; it becomes transparent. Hold Space (or right-drag) to move the picture, scroll to zoom. Ctrl+Z undoes a stroke.'));
    const r1 = mk('div', 'row'), br = slider(4, 160, 1, S.brush), val = mk('span', 'meta', S.brush + ' px');
    br.oninput = () => { S.brush = Number(br.value); val.textContent = S.brush + ' px'; }; r1.append(mk('label', null, 'Brush'), br, val); body.append(r1);
    eraseNote = mk('div', 'hint'); body.append(eraseNote); syncErase();
    const r2 = mk('div', 'row'), undo = mk('button', 'btn', 'Undo stroke'), clear = mk('button', 'btn', 'Clear'), go = mk('button', 'btn go', 'Preview erase');
    undo.onclick = undoStroke; clear.onclick = () => { S.strokes = []; syncErase(); drawOverlay(); };
    go.onclick = () => {
      if (!S.strokes.length) { setStatus('Paint over the parts to remove first.', true); return; }
      const strokes = S.strokes.map(s => [Math.round(s.r * 10) / 10].concat(...s.pts.map(p => [Math.round(p[0] * 10) / 10, Math.round(p[1] * 10) / 10])));
      runPreview('erase', { strokes: strokes });
    };
    r2.append(undo, clear, go); body.append(r2);
  }
  function undoStroke() { if (S.tool !== 'erase' || S.preview) return; S.strokes.pop(); syncErase(); drawOverlay(); }
  function toolBg(body) {
    body.append(mk('div', 'hint', 'Clears the flat background (its colour is read from the picture’s edge). Raise the tolerance if specks of background are left; lower it if parts of the figure disappear.'));
    const r1 = mk('div', 'row'), t = slider(0, 100, 1, S.tol), val = mk('span', 'meta', String(S.tol));
    t.oninput = () => { S.tol = Number(t.value); val.textContent = String(S.tol); }; r1.append(mk('label', null, 'Tolerance'), t, val); body.append(r1);
    const r2 = mk('div', 'row'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = S.inside; cb.id = 'cb-inside'; cb.onchange = () => { S.inside = cb.checked; };
    const lab = mk('label', null, 'Also clear enclosed areas (between the legs, inside loops)'); lab.htmlFor = 'cb-inside'; lab.style.flex = '1 1 200px'; r2.append(cb, lab); body.append(r2);
    const go = mk('button', 'btn go', 'Preview'); go.onclick = () => runPreview('remove_background', { tolerance: S.tol, inside: S.inside }); const r3 = mk('div', 'row'); r3.append(go); body.append(r3);
    t.onchange = () => { if (S.preview && S.preview.op === 'remove_background') { const keep = S.preview; S.preview = null; runPreview('remove_background', { tolerance: S.tol, inside: S.inside }).then(ok => { if (!ok) S.preview = keep; }); } };
  }
  function toolChange(body) {
    body.append(mk('div', 'hint', 'Describe the change and the image model (Nano Banana Pro) edits this picture. Everything else is kept the same.'));
    const ta = document.createElement('textarea'); ta.rows = 4; ta.maxLength = 500; ta.placeholder = 'For example: remove the sword, make the cloak red, add a glowing blue aura'; body.append(ta);
    const cost = S.est != null ? 'about ' + money(S.est) : 'a few cents';
    body.append(mk('div', 'hint', 'Costs ' + cost + ' and counts toward your media budget. The original is kept; the edit is saved as a new picture. It can take up to a minute.'));
    const go = mk('button', 'btn go', 'Make the change'), row = mk('div', 'row'); row.append(go); body.append(row);
    go.onclick = async () => {
      const text = ta.value.trim(); if (text.length < 3) { setStatus('Say what to change first.', true); return; }
      if (!confirm('Edit this picture with Nano Banana Pro?\n\n"' + text.slice(0, 200) + '"\n\nEstimated cost ' + cost + '. The original stays.')) return;
      S.busy = true; go.disabled = true; ta.disabled = true; setStatus('Working… this can take up to a minute.');
      const r = await api('/api/media/items/' + encodeURIComponent(S.id) + '/ai-edit', 'POST', { prompt: text, confirmed: true });
      S.busy = false; go.disabled = false; ta.disabled = false;
      if (!r.ok || !r.data.item) { setStatus(r.data.detail || 'That could not be made right now. Please try again.', true); return; }
      changed(); await refreshList(); await load(r.data.item.id, true); setStatus('Saved as a new picture (about ' + money(r.data.cost_usd || 0) + ').');
    };
  }
  function toolAdjust(body) {
    const sec = (title, parts) => { const d = mk('div', 'sec'); d.append(mk('h4', null, title)); parts.forEach(p => d.append(p)); body.append(d); };
    const w = numInput(S.nw), h = numInput(S.nh), lock = document.createElement('input'); lock.type = 'checkbox'; lock.checked = true; lock.id = 'cb-lock';
    w.oninput = () => { if (lock.checked && S.nw) h.value = Math.round(Number(w.value) * S.nh / S.nw); }; h.oninput = () => { if (lock.checked && S.nh) w.value = Math.round(Number(h.value) * S.nw / S.nh); };
    const rr = mk('div', 'row'); const lb = mk('label', null, 'keep shape'); lb.htmlFor = 'cb-lock'; rr.append(mk('label', null, 'width'), w, mk('label', null, 'height'), h, lock, lb);
    const rgo = mk('div', 'row'), go = mk('button', 'btn go', 'Preview resize'); go.onclick = () => runPreview('resize', { width: Number(w.value), height: Number(h.value) }); rgo.append(go);
    sec('Resize (pixels)', [rr, rgo]);
    [['brightness', 'Brightness'], ['contrast', 'Contrast']].forEach(([op, label]) => {
      const s = slider(0.1, 3, 0.05, 1), val = mk('span', 'meta', '1.00'); s.oninput = () => { val.textContent = Number(s.value).toFixed(2); };
      const r = mk('div', 'row'), b = mk('button', 'btn go', 'Preview'); b.onclick = () => runPreview(op, { factor: Number(s.value) }); r.append(s, val, b); sec(label, [r]);
    });
  }
  async function deleteItem() {
    if (!S.item || !confirm('Delete "' + S.item.title + '" from your library? This cannot be undone.')) return;
    const r = await api('/api/media/items/' + encodeURIComponent(S.id), 'DELETE');
    if (!r.ok) { setStatus(r.data.detail || 'It could not be deleted.', true); return; }
    changed(); const i = S.list.findIndex(x => x.id === S.id); S.list = S.list.filter(x => x.id !== S.id);
    const next = S.list[i] || S.list[i - 1];
    if (next) await load(next.id, true); else { showMsg('Deleted. You can close this tab.'); try { window.close(); } catch (e) { /* the tab was not opened by the Station */ } }
  }

  // ── loading, other pictures ──
  function showMsg(text) { S.nw = 0; img.removeAttribute('src'); msgEl.textContent = text; drawOverlay(); }
  async function refreshList() { const r = await api('/api/media/library?kind=image&limit=100'); if (r.ok) S.list = (r.data.items || []).filter(i => i.kind === 'image'); }
  function updateNav() { const i = S.list.findIndex(x => x.id === S.id); $('b-prev').disabled = i <= 0; $('b-next').disabled = i < 0 || i >= S.list.length - 1; $('meta').textContent = S.item ? fmtBytes(S.item.size_bytes) + (i >= 0 ? ' · ' + (i + 1) + ' of ' + S.list.length : '') : ''; }
  async function load(id, push) {
    if (!token()) { showMsg('Sign in on the Station first, then open the picture again.'); return; }
    msgEl.textContent = 'Loading…';
    const r = await api('/api/media/items/' + encodeURIComponent(id));
    if (!r.ok || !r.data.item) { showMsg((r.data && r.data.detail) || 'That picture could not be opened.'); return; }
    const item = r.data.item;
    if (item.kind !== 'image') { showMsg('That item is not a picture.'); return; }
    let url; try { url = await blobFor(item); } catch (e) { showMsg('The picture could not be loaded.'); return; }
    S.id = id; S.item = item; S.url = url; S.preview = null; S.crop = null; S.strokes = []; S.cur = null; S.nw = 0;
    document.title = item.title + ' — Image studio'; $('title').textContent = item.title;
    try { history[push ? 'pushState' : 'replaceState'](null, '', '/studio?id=' + encodeURIComponent(id)); } catch (e) { /* not essential */ }
    setBg(0); img.src = url; buildPanel(); updateNav();
  }
  const step = d => { const i = S.list.findIndex(x => x.id === S.id), n = S.list[i + d]; if (i >= 0 && n && !S.busy) load(n.id, true); };
  window.addEventListener('popstate', () => { const id = new URLSearchParams(location.search).get('id'); if (id && id !== S.id) load(id, false); });

  $('b-prev').onclick = () => step(-1); $('b-next').onclick = () => step(1);
  $('b-tools').onclick = () => panel.classList.toggle('collapsed');
  $('b-down').onclick = async () => {
    if (!S.id) return;
    const r = await api('/api/media/items/' + encodeURIComponent(S.id) + '?download=true');
    if (r.ok && r.data.item) { const a = document.createElement('a'); a.href = r.data.item.url; a.rel = 'noopener'; a.download = ''; document.body.appendChild(a); a.click(); a.remove(); }
    else setStatus((r.data && r.data.detail) || 'Could not download.', true);
  };
  $('z-out').onclick = () => zoomMid(1 / 1.5); $('z-in').onclick = () => zoomMid(1.5); $('z-fit').onclick = fitView; $('z-100').onclick = () => { const { w, h } = size(); zoomAt(1 / S.scale, w / 2, h / 2); };
  $('z-bg').onclick = () => setBg((S.bg + 1) % 3);

  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || '');
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !typing) { e.preventDefault(); undoStroke(); return; }
    if (e.key === 'Escape') { if (S.preview) discardPreview(); else if (S.crop) { S.crop = null; syncCrop(); drawOverlay(); } return; }
    if (typing) return;
    if (e.key === ' ') { S.space = true; stage.style.cursor = 'grab'; e.preventDefault(); return; }
    if (e.key === 'ArrowLeft') step(-1); else if (e.key === 'ArrowRight') step(1);
    else if (e.key === '+' || e.key === '=') zoomMid(1.5); else if (e.key === '-' || e.key === '_') zoomMid(1 / 1.5);
    else if (e.key === '0') fitView(); else if (e.key === '1') { const { w, h } = size(); zoomAt(1 / S.scale, w / 2, h / 2); } else return;
    e.preventDefault();
  });
  document.addEventListener('keyup', e => { if (e.key === ' ') { S.space = false; stage.style.cursor = ''; } });
  window.addEventListener('beforeunload', e => { if (S.preview) { e.preventDefault(); e.returnValue = ''; } });

  (async () => {
    sizeOverlay();
    const id = new URLSearchParams(location.search).get('id');
    if (!token()) { showMsg('Sign in on the Station first, then open the picture again.'); return; }
    if (!id) { showMsg('No picture was chosen. Open one from the Station’s library.'); return; }
    api('/api/media/status').then(r => { if (r.ok && r.data.estimates && r.data.estimates.ai_edit != null) { S.est = r.data.estimates.ai_edit; if (S.tool === 'change') renderTool(); } });
    await refreshList(); await load(id, false);
  })();
})();
</script>
</body></html>
"""
