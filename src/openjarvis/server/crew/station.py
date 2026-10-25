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
  #main { display:grid; grid-template-columns:var(--lw, 210px) 1fr var(--rw, 210px); min-height:0; }
  #left, #right { position:relative; }
  .col-grip { position:absolute; top:0; bottom:0; width:8px; cursor:ew-resize; touch-action:none; z-index:5; }
  .col-grip::after { content:""; position:absolute; top:50%; left:3px; width:2px; height:44px; margin-top:-22px; border-radius:1px; background:var(--cyan-dim); opacity:0.5; }
  .col-grip:hover::after { opacity:1; }
  #left .col-grip { right:-4px; }
  #right .col-grip { left:-4px; }
  .col { background:var(--panel); overflow-y:auto; min-height:0; scrollbar-width:thin; scrollbar-color:var(--cyan-dim) transparent; }
  #left { border-right:1px solid var(--border); }
  #right { border-left:1px solid var(--border); }
  #links-box { font-size:11px; }
  #links-box .sec { padding:8px 10px; }
  #chat-grip { height:12px; cursor:ns-resize; touch-action:none; display:flex; align-items:center; justify-content:center; }
  #chat-grip::after { content:""; width:44px; height:3px; border-radius:2px; background:var(--cyan-dim); }
  #chat-media { border-bottom:1px solid var(--border); max-height:46vh; overflow-y:auto; }
  #chat-media .viewer { padding:10px 14px; }
  #center { display:flex; flex-direction:column; min-width:0; min-height:0; }
  #center #stage { flex:1 1 auto; }
  #center #chat { flex:0 0 auto; background:var(--panel); border-top:1px solid var(--border); border-bottom:none; }
  #stage { position:relative; min-width:0; min-height:0; overflow:hidden; }
  #cv { position:absolute; inset:0; width:100%; height:100%; display:block; cursor:default; }
  #hint { position:absolute; left:14px; bottom:12px; font-size:10px; color:var(--text-dim); letter-spacing:1px; pointer-events:none; }
  .sec { padding:14px 16px; border-bottom:1px solid var(--border); }
  .vit { display:flex; justify-content:space-between; font-size:10px; letter-spacing:1px; padding:3px 0; }
  .vit .k { color:var(--text-dim); } .vit .v { color:var(--cyan); text-transform:uppercase; } .vit .v.g { color:#00ff99; } .vit .v.o { color:#ff8a2a; }
  .lib-f { background:transparent; border:1px solid var(--border); color:var(--text-dim); font-size:9px; letter-spacing:1px; padding:3px 6px; cursor:pointer; border-radius:2px; text-transform:uppercase; }
  .lib-f.on { border-color:var(--cyan); color:var(--cyan); }
  .lib-grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px; margin-bottom:8px; }
  .lib-folder { display:block; width:100%; text-align:left; background:transparent; border:0; border-bottom:1px solid var(--border); color:var(--cyan); font-size:10px; letter-spacing:1px; padding:6px 2px; margin:4px 0; cursor:pointer; }
  .lib-row { display:flex; flex-direction:column; gap:5px; align-items:stretch; padding:4px; border:1px solid var(--border); border-radius:3px; background:#0a1520; cursor:pointer; font-size:10px; min-width:0; }
  .lib-row:hover, .lib-row.sel { background:rgba(0,229,255,0.06); }
  .lib-thumb { width:100%; aspect-ratio:1; object-fit:cover; border-radius:2px; background:#050b12; }
  .lib-ico { width:100%; height:64px; display:flex; align-items:center; justify-content:center; color:var(--cyan); border:1px solid var(--border); border-radius:2px; }
  .lib-t { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:var(--text); }
  .lib-k { font-size:8px; letter-spacing:1px; color:var(--text-dim); text-transform:uppercase; }
  .viewer img, .viewer audio { width:100%; border-radius:2px; margin:8px 0; background:#050a12; }
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
  .agent.open { border-color:var(--cyan-dim); background:rgba(0,229,255,0.05); }
  .agent-chev { color:var(--text-dim); font-size:10px; margin-left:4px; }
  .agent-det { margin-top:6px; padding-top:6px; border-top:1px solid var(--border); }
  .agent-k { font-size:8px; letter-spacing:2px; color:var(--text-dim); text-transform:uppercase; margin-top:5px; }
  .agent { border:1px solid var(--border); border-radius:2px; padding:6px 8px; margin-bottom:6px; cursor:pointer; background:rgba(0,229,255,0.02); }
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
  /* the public landing page (signed out): what Aibussol is, the crew, tools, safety, pricing, and sign in / sign up */
  #auth { position:fixed; inset:0; display:none; overflow-y:auto; background:radial-gradient(ellipse at 20% 0%, rgba(90,40,160,0.18), transparent 55%), radial-gradient(ellipse at 90% 30%, rgba(0,120,150,0.14), transparent 50%), #04050a; z-index:9; }
  #auth .land { max-width:1080px; margin:0 auto; padding:28px 20px 60px; }
  #auth .nav { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:34px; }
  #auth .nav .logo { font-family:'Orbitron',monospace; font-weight:900; letter-spacing:4px; color:var(--cyan); text-shadow:0 0 18px var(--cyan); font-size:16px; }
  #auth .nav .logo span { color:var(--orange); text-shadow:0 0 18px var(--orange); }
  #auth .hero { display:grid; grid-template-columns:1.25fr 1fr; gap:34px; align-items:start; }
  #auth h1 { font-family:'Orbitron',monospace; font-weight:900; color:#e8fbff; font-size:clamp(26px,4.4vw,44px); line-height:1.15; letter-spacing:1px; margin:0 0 16px; }
  #auth h1 em { font-style:normal; color:var(--cyan); text-shadow:0 0 22px rgba(0,229,255,0.5); }
  #auth h2 { font-family:'Orbitron',monospace; font-size:13px; letter-spacing:4px; color:var(--cyan); text-transform:uppercase; margin:46px 0 14px; }
  #auth p.lead { color:var(--text); font-size:14px; line-height:1.75; margin:0 0 12px; }
  #auth .pts { display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }
  #auth .pt { border:1px solid var(--border); border-radius:2px; padding:6px 10px; font-size:11px; color:var(--text); background:rgba(0,229,255,0.03); }
  #auth .box { width:100%; border:1px solid var(--cyan-dim); padding:24px; background:var(--panel); display:flex; flex-direction:column; gap:10px; box-shadow:0 0 40px rgba(0,229,255,0.06); }
  #auth .steps { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
  #auth .card { border:1px solid var(--border); background:rgba(8,8,15,0.85); padding:14px; border-radius:2px; }
  #auth .card b { display:block; font-family:'Orbitron',monospace; font-size:11px; letter-spacing:2px; color:#e8fbff; margin-bottom:6px; }
  #auth .card span, #auth .card li { font-size:12px; line-height:1.6; color:var(--text); }
  #auth .card ul { margin:6px 0 0; padding-left:16px; }
  #auth .num { font-family:'Orbitron',monospace; color:var(--orange); font-size:18px; font-weight:900; margin-bottom:4px; }
  #auth .crew { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:10px; }
  #auth .who { display:flex; gap:10px; align-items:flex-start; border:1px solid var(--border); background:rgba(8,8,15,0.85); padding:10px; border-radius:2px; }
  #auth .who .face { width:40px; height:40px; flex:0 0 40px; border-radius:50%; border:1px solid var(--border); background-color:#060a12; background-repeat:no-repeat; }
  #auth .who .nm { font-family:'Orbitron',monospace; font-size:11px; letter-spacing:2px; font-weight:700; }
  #auth .who .rl { font-size:9px; letter-spacing:1px; color:var(--text-dim); text-transform:uppercase; margin:1px 0 4px; }
  #auth .who .tx { font-size:11px; line-height:1.55; color:var(--text); }
  #auth .grid2 { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  #auth .price { font-family:'Orbitron',monospace; font-size:28px; color:#e8fbff; font-weight:900; }
  #auth .price small { font-size:12px; color:var(--text-dim); font-weight:400; }
  #auth details { border-bottom:1px solid var(--border); padding:10px 0; }
  #auth summary { cursor:pointer; color:#e8fbff; font-size:13px; }
  #auth details p { color:var(--text); font-size:12px; line-height:1.7; margin:8px 0 0; }
  #auth .foot { margin-top:40px; color:var(--text-dim); font-size:11px; text-align:center; }
  @media (max-width: 820px) {
    #auth .hero, #auth .grid2 { grid-template-columns:1fr; }
    #auth .steps { grid-template-columns:1fr 1fr; }
  }
  #auth .box .h { font-family:'Orbitron',monospace; letter-spacing:4px; font-size:13px; color:var(--cyan); margin-bottom:6px; text-align:center; }
  #auth input { background:#050a12; border:1px solid var(--border); color:var(--text); padding:10px; font:inherit; font-size:13px; border-radius:2px; }
  #auth a { color:var(--cyan); }
  #a-err { color:var(--red); min-height:14px; font-size:11px; }
  .ans .b.tool { padding:6px 9px; font-size:14px; line-height:1; }
  .ans .b.tool.rec { border-color:var(--red); color:var(--red); animation:blink 1s infinite; }
  @keyframes blink { 50% { opacity:0.45; } }
  #img-bar { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:6px; font-size:10px; color:var(--text-dim); }
  #img-bar[hidden] { display:none; }
  #img-bar .chip { display:flex; gap:6px; align-items:center; border:1px solid var(--line, #1c3a4f); border-radius:2px; padding:2px 4px; }
  #img-bar img { width:34px; height:34px; object-fit:cover; border-radius:2px; }
  .cm img.th { max-width:140px; max-height:100px; display:block; margin-top:6px; border-radius:2px; }
  .cm .vn { font-size:10px; color:var(--orange); margin-top:4px; }
  #wake { font-family:'Share Tech Mono',monospace; letter-spacing:1px; }
  #wake.on { color:#00ff99; } #wake.rec { color:var(--red); } #wake.busy { color:var(--orange); }
  .v-row { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-top:8px; font-size:10px; color:var(--text-dim); }
  .v-row select, .v-row input { background:#050a12; border:1px solid var(--border); color:var(--text); padding:4px 6px; font:inherit; font-size:11px; max-width:110px; }
  .v-row input[type=text] { max-width:none; flex:1; }
  .xbtn { background:transparent; border:1px solid transparent; color:var(--text-dim); cursor:pointer; font-size:11px; padding:0 4px; }
  .xbtn:hover { color:var(--red); border-color:var(--red); }
  #gate { position:fixed; inset:48px 0 0 0; display:none; align-items:center; justify-content:center; background:rgba(5,5,8,0.92); z-index:5; padding:20px; }
  #gate .box { max-width:460px; border:1px solid var(--cyan-dim); padding:28px; background:var(--panel); line-height:1.7; font-size:12px; }
  #gate .box .h { font-family:'Orbitron',monospace; letter-spacing:3px; font-size:12px; margin-bottom:12px; }
  canvas { -webkit-tap-highlight-color:transparent; }
  @media (max-width: 820px) {
    body { grid-template-rows:auto 1fr; height:100dvh; }
    #top { flex-wrap:wrap; padding:8px 12px; gap:8px; }
    .hide-m, #hint { display:none; }
    #main { display:flex; flex-direction:column; overflow-y:auto; }
    #center { order:1; flex:0 0 auto; }
    #center #stage { height:min(100vw, 68dvh); flex:0 0 auto; }
    .col-grip { display:none; }
    #right { order:2; border-left:none; border-top:1px solid var(--border); overflow:visible; display:flex; flex-direction:column; }
    #right #side-body { order:1; }      /* what you tapped shows first, above the connections */
    #right #links-box { order:2; }
    #left { order:3; border-right:none; border-top:1px solid var(--border); overflow:visible; }
    #main > * { flex:0 0 auto !important; min-height:auto !important; height:auto; }   /* stacked, never squeezed on top of each other */
    #center { display:block; }
    #center #chat { position:static; }
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
  <button class="tbtn" id="t-sub" onclick="subscribe()" hidden>SUBSCRIBE</button>
  <button class="tbtn" onclick="logout()">LOGOUT</button>
</div>
<div id="main">
  <div id="left" class="col"><div id="vitals-box"></div><div id="roster-box"></div><div id="library-box"></div><div class="col-grip" id="left-grip" title="Drag to resize"></div></div>
  <div id="center">
    <div id="stage"><canvas id="cv"></canvas></div>
    <div id="chat" class="sec">
      <div id="chat-grip" title="Drag up or down to resize the chat"></div>
      <div class="lbl"><span>Helix</span><span class="act"><span id="wake">VOICE: TAP TO WAKE</span> · <button class="lib-f" id="voice-tog" title="Helix reads her replies aloud">🔊 ON</button> <button class="lib-f" id="speed-tog" title="How fast Helix speaks. Click to change.">1×</button></span></div>
      <div id="chat-media" hidden></div>
      <div id="chat-log"></div>
      <div id="img-bar" hidden></div>
      <div class="ans"><button class="b tool" id="mic" title='Talk to Helix (or say "Hey Helix", then "send")'>🎙</button><label class="b tool" for="img-in" title="Attach up to 5 images, Word (.docx) or text documents">📎</label><input id="img-in" type="file" multiple accept="image/*,.docx,.txt,.md,.markdown,.csv,.tsv,.json,.log,.rtf" hidden/><input id="chat-in" name="helix-message" type="text" maxlength="8000" placeholder='Type, or say "Hey Helix"…' autocomplete="off" data-lpignore="true" data-1p-ignore="true" data-form-type="other"/><button class="b" id="chat-send">Send</button></div>
    </div>
  </div>
  <div id="right" class="col"><div class="col-grip" id="right-grip" title="Drag to resize"></div><div id="links-box"></div><div id="side-body"></div></div>
</div>
<div id="auth"><div class="land">
  <div class="nav"><div class="logo">AIBUS<span>SOL</span></div><a href="#signin" class="b" id="nav-cta" onclick="setAuthMode('register')">GET STARTED</a></div>
  <div class="hero">
    <div>
      <h1>Tell Helix what to build.<br><em>A crew of AI agents</em> plans it, builds it and runs it.</h1>
      <p class="lead">Aibussol is your AI operations station for running businesses. Describe a goal in plain words: a product, a
        campaign, a game, a website change, a week of posts. Helix breaks it into tasks and a crew of fifteen specialist agents works
        them in parallel. You watch it live on the Station, answer their questions, and approve anything public, paid or permanent before
        it happens.</p>
      <p class="lead">Every business you run gets its own factory on the Station, with its money in and out, its users and its site
        health, plus a daily brief on your phone.</p>
      <div class="pts"><span class="pt">Talk or type, "Hey Helix"</span><span class="pt">15 specialist agents</span><span class="pt">Approve before anything goes public</span><span class="pt">P&amp;L per business</span><span class="pt">Briefs on your phone</span></div>
    </div>
    <div class="box" id="signin">
      <div class="h">AIBUS<span style="color:var(--orange)">SOL</span></div>
      <div class="dim" id="a-mode" style="text-align:center;letter-spacing:2px;font-size:10px">SIGN IN</div>
      <input id="a-email" type="email" placeholder="Email" autocomplete="email"/>
      <input id="a-pass" type="password" placeholder="Password" autocomplete="current-password"/>
      <div id="a-reg" style="display:none;flex-direction:column;gap:10px"><input id="a-name" placeholder="Display name" autocomplete="nickname"/><input id="a-invite" placeholder="Invite code (if you have one)" autocomplete="off"/></div>
      <button class="b" id="a-go">SIGN IN</button>
      <div id="a-err"></div>
      <div class="dim" style="text-align:center;font-size:11px"><span id="a-q">No account yet?</span> <a href="#" id="a-tog">Create one</a></div>
    </div>
  </div>

  <h2>How it works</h2>
  <div class="steps">
    <div class="card"><div class="num">1</div><b>SAY THE GOAL</b><span>"Build a digital planner and list it on Etsy." "Plan next week's posts." "Make my game's web version." By voice or chat.</span></div>
    <div class="card"><div class="num">2</div><b>HELIX PLANS</b><span>Helix splits it into tasks, picks the right agents and a budget, and reads the plan back. Nothing starts until you say yes.</span></div>
    <div class="card"><div class="num">3</div><b>THE CREW WORKS</b><span>Agents work in parallel inside the factory for that business. Lens reviews the work and sends back anything that is not right.</span></div>
    <div class="card"><div class="num">4</div><b>YOU APPROVE</b><span>Publishing, charging, merging code or sending email waits for your tap on Approve. Finished files land in the mission's outbox.</span></div>
  </div>

  <h2>The crew</h2>
  <div class="crew" id="land-crew"></div>

  <h2>Tools and connections</h2>
  <div class="grid2">
    <div class="card"><b>MONEY</b><ul><li>Stripe: revenue, subscriptions and payments per business; products and payment links on your Approve</li><li>Etsy draft listings</li><li>A live P&amp;L for every factory</li></ul></div>
    <div class="card"><b>CODE AND SITES</b><ul><li>GitHub: Forge commits to its own branch; merging waits for your Approve</li><li>Probe tests your live sites</li><li>Site up or down on every factory</li></ul></div>
    <div class="card"><b>GAMES ON YOUR PC</b><ul><li>The Workbench pairs your computer: Rig builds in your real Unity or Unreal projects with Claude Code, on your own Claude plan</li><li>PC, Android and web builds, tested before you see them</li></ul></div>
    <div class="card"><b>SOCIAL AND EMAIL</b><ul><li>Through Marnyx: a planned week of posts per brand, approved once and published at the right times</li><li>Email campaigns to your opted-in subscribers</li></ul></div>
    <div class="card"><b>RESEARCH AND MEDIA</b><ul><li>Live web search and deep sourced reports</li><li>Image and voice generation with a media library and editor</li></ul></div>
    <div class="card"><b>BRIEFS</b><ul><li>Herald makes your morning, afternoon and evening briefs from live data</li><li>Sent to your phone, and Helix reads them out on request</li></ul></div>
  </div>

  <h2>Safety by design</h2>
  <div class="grid2">
    <div class="card"><b>YOU HOLD THE KEYS</b><span>Anything public, paid or permanent becomes a checkpoint that only you can approve. An agent can never approve its own.</span></div>
    <div class="card"><b>OFFICIAL APIS ONLY</b><span>Agents act through each platform's official API with the access you choose, such as a Stripe restricted key or a GitHub token on the repos you pick.</span></div>
    <div class="card"><b>YOUR OWN STATION</b><span>Your keys are stored encrypted and never shown again. Your crew only sees your connections, and every account is kept separate.</span></div>
  </div>

  <h2>Pricing</h2>
  <div class="grid2">
    <div class="card"><b>MONTHLY PLAN</b><div class="price" id="land-price">Subscribe<small></small></div><span id="land-included">Your own Station, the whole crew, guided connections and phone briefs.</span></div>
    <div class="card"><b>PAY AS YOU GO</b><span id="land-payg">Extra crew work beyond what the plan includes is billed by usage, like Claude's own plans, with a monthly cap you never go past.</span></div>
    <div class="card"><b>BUDGETS</b><span>Every mission has a budget and every task checks it before spending. Crews ask before spending more.</span></div>
  </div>

  <h2>Questions</h2>
  <details><summary>Do I need to know how to code?</summary><p>No. You describe what you want; the crew writes and tests the code and asks you before anything is merged or published.</p></details>
  <details><summary>Will it post or charge anything without me?</summary><p>No. Publishing, charging, merging and sending email always wait for your Approve. Reading data and making drafts run on their own.</p></details>
  <details><summary>Can it build mobile games?</summary><p>Yes, with your computer paired: Rig builds in Unity or Unreal on your PC with Claude Code. Android and web builds work from Windows; iPhone builds need a Mac or a cloud build service.</p></details>
  <details><summary>What do I connect?</summary><p>Only what you want the crew to use. The Station walks you through each one (Stripe, GitHub, Marnyx, your phone) and tests it before saving.</p></details>
  <div class="foot">Aibussol · made by FlipDeck LLC</div>
</div></div>
<div id="gate"><div class="box"><div class="h" id="gate-h">STATION OFFLINE</div><div id="gate-t"></div></div></div>

<script>
const TOKEN_KEY = 'aibussol_token';
const token = () => localStorage.getItem(TOKEN_KEY) || '';
const PROJECT_COLORS = { flipdeck:'#ff6d00', marnyx:'#b06cff', deadzone:'#ff3344', aibussol:'#00e5ff', 'new':'#ff4fb8' };
const ROLE_COLORS = { lead:'#00e5ff', researcher:'#5ad1ff', builder:'#4dffc3', writer:'#ffc14d', designer:'#ff7ad9', operator:'#ff6d00', reviewer:'#c8ff4d', briefer:'#eef3ff', workbench:'#a970ff',
  marketer:'#ff3d6e', social:'#ffe600', support:'#6b8cff', analyst:'#00c2a8', qa:'#ff9a3d', finance:'#2bd96b' };
const STATUS_COLORS = { queued:'#2a5060', running:'#00e5ff', waiting:'#ff6d00', done:'#00ff88', failed:'#ff3344', cancelled:'#2a3a44' };
let S = null, selected = 'hub', lastDraw = 0, pollTimer = null;

function mk(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function money(x) { return '$' + Number(x || 0).toFixed(2); }
function ago(iso) { if (!iso) return ''; const s = (Date.now() - new Date(iso).getTime()) / 1000; if (s < 60) return 'just now'; if (s < 3600) return Math.floor(s/60) + 'm ago'; if (s < 86400) return Math.floor(s/3600) + 'h ago'; return Math.floor(s/86400) + 'd ago'; }
function projColor(p) {
  const k = String(p || '').toLowerCase().replace(/[^a-z0-9]/g, ''), b = S && S.businesses ? S.businesses.find(x => x.key === k) : null;
  return b ? b.color : PROJECT_COLORS[k] || '#00e5ff';
}

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
  const a = mk('a', null, 'Reload'); a.href = '/'; t.appendChild(a);
}

// ── sign in / create an account (the same /api/auth endpoints as before) ──
let authMode = 'login', booted = false;
function showAuth(msg) {
  document.getElementById('auth').style.display = 'block';
  document.getElementById('a-err').textContent = msg || '';
  landing();
}
// the landing page's crew cards and live pricing (public: /crew/plan.json)
const LAND_CREW = [
  ['lead', 'Helix', 'Lead', 'Talks with you, plans each goal and assigns the crew'],
  ['researcher', 'Scout', 'Research', 'Live web search and deep sourced reports'],
  ['builder', 'Forge', 'Code', 'Writes code in your repos on its own branch'],
  ['writer', 'Quill', 'Writing', 'Copy, listings, posts, emails and docs'],
  ['designer', 'Prism', 'Design', 'Brand direction, layouts and images'],
  ['operator', 'Relay', 'Operator', 'Uses your connected platforms on your Approve'],
  ['workbench', 'Rig', 'Workbench', 'Builds games on your PC in Unity or Unreal'],
  ['reviewer', 'Lens', 'Review', 'Checks every deliverable and sends back fixes'],
  ['briefer', 'Herald', 'Briefs', 'Your daily briefs from live data, on your phone'],
  ['marketer', 'Spark', 'Marketing', 'Launch plans, SEO, ads copy and email campaigns'],
  ['social', 'Echo', 'Social', 'A planned week of posts for every brand'],
  ['support', 'Haven', 'Support', 'FAQs, help articles and reply templates'],
  ['analyst', 'Sage', 'Analyst', 'Your numbers, KPIs, trends and forecasts'],
  ['qa', 'Probe', 'QA', 'Tests your live sites and writes bug reports'],
  ['finance', 'Tally', 'Finance', 'Budgets, cash flow, pricing and margin math'],
];
let landed = false;
async function landing() {
  if (landed) return; landed = true;
  const box = document.getElementById('land-crew'), A = 40;
  LAND_CREW.forEach(([role, name, rl, tx]) => {
    const c = mk('div', 'who'), f = mk('div', 'face'), row = BOT_ROWS.indexOf(role);
    f.style.backgroundImage = role === 'lead' ? 'url(/crew/helix.jpg)' : 'url(/crew/bots.png?v=' + BOT_VER + ')';
    if (role === 'lead') { f.style.backgroundSize = 'cover'; f.style.backgroundPosition = 'center 20%'; }
    else { f.style.backgroundSize = (17 * A) + 'px ' + (A * BOT_ROWS.length) + 'px'; f.style.backgroundPosition = '-1px ' + (-A * Math.max(0, row) + 2) + 'px'; }
    f.style.borderColor = alpha(ROLE_COLORS[role] || '#00e5ff', 0.5);
    const t = mk('div'); const n = mk('div', 'nm', name); n.style.color = ROLE_COLORS[role] || '#00e5ff';
    t.append(n, mk('div', 'rl', rl), mk('div', 'tx', tx)); c.append(f, t); box.append(c);
  });
  try {
    const p = await (await fetch('/crew/plan.json')).json();
    if (p.price_usd) {
      const el = document.getElementById('land-price'); el.textContent = '$' + Number(p.price_usd).toFixed(2);
      const sm = mk('small', null, ' / ' + (p.interval || 'month')); el.append(sm);
    }
    if (p.included_usd != null) document.getElementById('land-included').textContent =
      'Your own Station, the whole crew, guided connections and phone briefs, with $' + Number(p.included_usd).toFixed(2) + ' of crew work included every month.';
    if (p.payg) document.getElementById('land-payg').textContent = 'Need more? Extra crew work is billed by usage at ' + p.multiplier + '× its AI cost, like Claude\'s own plans, never more than $' + Number(p.cap_usd).toFixed(0) + ' extra in a month.';
    else document.getElementById('land-payg').textContent = 'The plan includes a set amount of crew work each month; when it is used up, the crew pauses until the next month. Pay-as-you-go extras are coming.';
  } catch (e) {}
}
function setAuthMode(m) {
  authMode = m;
  document.getElementById('a-mode').textContent = m === 'login' ? 'SIGN IN' : 'CREATE AN ACCOUNT';
  document.getElementById('a-go').textContent = m === 'login' ? 'SIGN IN' : 'CREATE ACCOUNT';
  document.getElementById('a-q').textContent = m === 'login' ? 'No account yet?' : 'Already have one?';
  document.getElementById('a-tog').textContent = m === 'login' ? 'Create one' : 'Sign in';
  document.getElementById('a-reg').style.display = m === 'register' ? 'flex' : 'none';
  document.getElementById('a-pass').autocomplete = m === 'login' ? 'current-password' : 'new-password';
  document.getElementById('a-err').textContent = '';
}
async function doAuth() {
  const email = document.getElementById('a-email').value.trim(), password = document.getElementById('a-pass').value;
  const err = document.getElementById('a-err'), btn = document.getElementById('a-go');
  if (!email || !password) { err.textContent = 'Email and password, please.'; return; }
  btn.disabled = true; btn.textContent = 'CONNECTING…'; err.textContent = '';
  const body = authMode === 'login' ? { email, password }
    : { email, password, invite_code: document.getElementById('a-invite').value.trim(), display_name: document.getElementById('a-name').value.trim() || email.split('@')[0] };
  try {
    const r = await fetch(authMode === 'login' ? '/api/auth/login' : '/api/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.token) {
      try { localStorage.setItem(TOKEN_KEY, d.token); } catch (e) {}
      document.getElementById('auth').style.display = 'none';
      document.getElementById('a-pass').value = '';
      boot();
    } else err.textContent = typeof d.detail === 'string' ? d.detail : 'That did not work. Check your email and password.';
  } catch (e) { err.textContent = 'No connection.'; }
  btn.disabled = false; btn.textContent = authMode === 'login' ? 'SIGN IN' : 'CREATE ACCOUNT';
}
document.getElementById('a-go').onclick = doAuth;
document.getElementById('a-pass').addEventListener('keydown', e => { if (e.key === 'Enter') doAuth(); });
document.getElementById('a-tog').onclick = (e) => { e.preventDefault(); setAuthMode(authMode === 'login' ? 'register' : 'login'); };
function crewOff(msg) {
  S = null; setState('CREW OFF');
  document.getElementById('roster-box').replaceChildren();
  document.getElementById('links-box').replaceChildren();
  const side = document.getElementById('side-body'); side.textContent = '';
  const sec = mk('div', 'sec'); sec.append(mk('div', 'lbl', 'Station'), mk('div', 'dim', msg));
  if (/subscri|plan/i.test(msg)) {
    const b = mk('button', 'b', 'SUBSCRIBE'); b.style.marginTop = '10px'; b.onclick = subscribe;
    sec.append(mk('div', 'dim', 'A subscription gives you your own Station: Helix and the whole crew, your own businesses and connections, briefs on your phone. Extra crew work past the plan is billed by usage, with a monthly cap.'), b);
  }
  side.append(sec);
  document.getElementById('vitals-box').replaceChildren(vitalsSec());
  document.getElementById('library-box').replaceChildren(librarySec());
}
function boot() {
  if (booted || !token()) return;
  booted = true;
  refresh(); loadChat(true); loadFinance(); loadLibrary(); refreshSubscription(); checkBriefs();
}

async function refresh() {
  if (!token()) { showAuth(); return; }
  const r = await api('/api/crew/state').catch(() => null);
  if (!r) { setState('NO CONNECTION'); return; }
  if (r.status === 401) { try { localStorage.removeItem(TOKEN_KEY); } catch (e) {} showAuth('Your session ended. Sign in again.'); return; }
  if (r.status === 403) { crewOff('Subscribe to unlock your own crew. Chat, voice and your library work here.'); return; }
  if (!r.data.enabled) { crewOff(r.data.detail || 'The crew is not switched on.'); return; }
  gate(null);
  S = r.data;
  spinOnMissionChange(S.missions || []);                  // a mission started or finished: spin the ring
  document.getElementById('c-run').textContent = S.running;
  document.getElementById('c-spend').textContent = money(S.spend.today_usd) + (S.spend.daily_cap_usd == null ? ' · no cap' : ' of ' + money(S.spend.daily_cap_usd) + ' cap');
  setState(S.blocked ? 'HOLD: ' + String(S.blocked).toUpperCase() : 'CREW READY');
  document.getElementById('t-night').classList.toggle('on', !!S.settings.night_shift);
  document.getElementById('t-pause').classList.toggle('on', !!S.settings.paused);
  document.getElementById('t-pause').textContent = S.settings.paused ? 'RESUME ALL' : 'PAUSE ALL';
  if (selected !== 'hub' && selected !== 'links' && selected !== 'setup' && !String(selected).startsWith('f:') && !String(selected).startsWith('m:') && !S.missions.find(m => m.id === selected)) selected = 'hub';
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
  if (!S) {                                                    // no crew on this account: chat, vitals, library and the viewer still work
    document.getElementById('vitals-box').replaceChildren(vitalsSec());
    document.getElementById('library-box').replaceChildren(librarySec());
    renderChatMedia();
    return;
  }
  side.textContent = '';
  document.getElementById('roster-box').replaceChildren(rosterSec());
  document.getElementById('links-box').replaceChildren(linksSec());
  document.getElementById('vitals-box').replaceChildren(vitalsSec());
  document.getElementById('library-box').replaceChildren(librarySec());
  if (selected === 'setup') side.append(backSec(), setupSec());
  else if (selected === 'hub' || selected === 'links' || String(selected).startsWith('m:')) {
    if (!isOwner() && (!S.businesses.length || !S.connections.stripe.connected)) side.append(setupSec());
    side.append(overviewSec(), inboxSec(), missionsSec(), ledgerSec(S.events, 'Station Ledger'));
  }
  else if (String(selected).startsWith('f:')) side.append(backSec(), factorySec(selected.slice(2)));
  else {
    const m = S.missions.find(x => x.id === selected);
    side.append(backSec(), missionSec(m), inboxSec(m.id), outboxSec(m), ledgerSec(S.events.filter(e => e.mission_id === m.id), 'Mission Ledger'));
  }
  renderChatMedia();
  if (focused) { const el = document.getElementById(focused); if (el) el.focus(); }
}

function renderChatMedia() {
  const box = document.getElementById('chat-media'); if (!box) return;
  if (!String(selected).startsWith('m:')) { box.hidden = true; box.replaceChildren(); return; }
  if (box.contains(document.activeElement) && document.activeElement.value) return;   // never rebuild under someone's fingers
  const x = mk('button', 'xbtn', '✕'); x.title = 'Close'; x.style.float = 'right';
  x.onclick = () => { selected = 'hub'; LIB.open = null; renderSide(true); };
  const m = mediaSec(); m.prepend(x);
  box.replaceChildren(m); box.hidden = false;
}

// ── system vitals (top left) ──
const PAGE_START = Date.now();
function vitalsSec() {
  const s = mk('div', 'sec'); s.append(mk('div', 'lbl', 'System vitals'));
  const row = (k, v, cls) => { const r = mk('div', 'vit'); r.append(mk('span', 'k', k), mk('span', 'v' + (cls ? ' ' + cls : ''), v)); s.append(r); };
  const up = Math.floor((Date.now() - PAGE_START) / 1000), hh = String(Math.floor(up / 3600)).padStart(2, '0'), mm = String(Math.floor(up / 60) % 60).padStart(2, '0');
  const lead = (S && S.roster || []).find(a => a.role === 'lead');
  const queued = S ? S.missions.reduce((n, m) => n + m.tasks.filter(t => t.status === 'queued').length, 0) : 0;
  row('Status', S ? (S.blocked ? String(S.blocked).split(' ')[0] : 'nominal') : (token() ? 'online' : 'connecting'), S && !S.blocked ? 'g' : 'o');
  row('Engine', lead ? lead.model : '—');
  row('Crew', S ? S.running + ' running · ' + queued + ' queued' : '—');
  row('Media', LIB.on ? 'on' : 'off', LIB.on ? 'g' : '');
  row('Session', hh + ':' + mm);
  return s;
}

// ── media library (below the crew) and its viewer (right panel) ──
const LIB = { on: false, kind: 'all', items: [], open: null, note: '', folded: new Set() };
async function loadLibrary() {
  const st = await api('/api/media/status').catch(() => null);
  LIB.on = !!(st && st.ok && st.data && st.data.enabled);
  if (LIB.on) {
    const r = await api('/api/media/library' + (LIB.kind === 'all' ? '' : '?kind=' + LIB.kind)).catch(() => null);
    LIB.items = r && r.ok ? (r.data.items || []) : [];
    LIB.note = r && r.ok ? '' : 'The library is unavailable right now.';
  }
  renderSide();
}
function librarySec() {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Library'), mk('span', 'act', LIB.on ? LIB.items.length + ' items' : 'off')); s.append(l);
  if (!LIB.on) { s.append(mk('div', 'dim', 'Media is switched off on the server.')); return s; }
  const f = mk('div'); f.style.cssText = 'display:flex;gap:4px;margin-bottom:6px;flex-wrap:wrap';
  [['all', 'All'], ['image', 'Images'], ['sound', 'Sound']].forEach(([k, t]) => {
    const b = mk('button', 'lib-f' + (LIB.kind === k ? ' on' : ''), t); b.onclick = () => { LIB.kind = k; loadLibrary(); }; f.append(b);
  });
  s.append(f);
  if (LIB.note) s.append(mk('div', 'dim', LIB.note));
  else if (!LIB.items.length) s.append(mk('div', 'dim', 'Nothing saved yet. Ask Helix (or Prism) to make something.'));
  // one folder per day (newest first); the newest day opens, the others stay closed until you tap them
  const dayOf = it => new Date(it.created_at || Date.now()).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  const va = mk('button', 'lib-f' + (LIB.viewAll ? ' on' : ''), LIB.viewAll ? 'Fold days' : 'View all');
  va.onclick = () => { LIB.viewAll = !LIB.viewAll; renderSide(true); };
  s.append(va);
  const days = [];
  LIB.items.slice(0, 200).forEach(it => { const d = dayOf(it); let g = days.find(x => x.day === d); if (!g) { g = { day: d, items: [] }; days.push(g); } g.items.push(it); });
  days.forEach((g, i) => {
    const open = LIB.viewAll || (i === 0 ? !LIB.folded.has(g.day) : !!(LIB.opened && LIB.opened.has(g.day)));
    const head = mk('button', 'lib-folder', (open ? '▾ ' : '▸ ') + g.day + '  ·  ' + g.items.length);
    head.onclick = () => { if (i === 0) { if (LIB.folded.has(g.day)) LIB.folded.delete(g.day); else LIB.folded.add(g.day); } else { LIB.opened = LIB.opened || new Set(); if (LIB.opened.has(g.day)) LIB.opened.delete(g.day); else LIB.opened.add(g.day); } renderSide(true); };
    s.append(head);
    if (!open) return;
    const grid = mk('div', 'lib-grid'); s.append(grid);
    // a design pack (color and B&W PNG and JPG, one slug) shows as ONE tile; its color PNG is the picture
    const tiles = [], designs = new Map();
    g.items.forEach(it => {
      if (it.provider !== 'aibussol-design') { tiles.push({ it }); return; }
      const key = String(it.title).replace(/ (color|bw)$/, '');
      let t = designs.get(key);
      if (!t) { t = { design: true, key, files: [] }; designs.set(key, t); tiles.push(t); }
      t.files.push(it);
    });
    tiles.forEach(t => {
      const rep = t.design ? (t.files.find(f => /color$/.test(f.title)) || t.files[0]) : t.it;
      const label = t.design ? t.key : rep.title;
      const sub = t.design ? 'design pack · ' + t.files.length + ' files' : rep.kind;
      const r = mk('div', 'lib-row' + (selected === 'm:' + rep.id ? ' sel' : ''));
      if (rep.kind === 'image') { const im = mk('img', 'lib-thumb'); im.alt = ''; im.loading = 'lazy'; mediaBlob(rep).then(u => { im.src = u; }).catch(() => { im.style.opacity = '0.3'; }); r.append(im); }
      else r.append(mk('span', 'lib-ico', rep.kind === 'sound' ? '♪' : '▶'));
      const box = mk('div'); box.style.minWidth = '0'; box.append(mk('div', 'lib-t', label), mk('div', 'lib-k', sub)); r.append(box);
      r.onclick = () => openMedia(rep.id);
      grid.append(r);
    });
  });
  return s;
}
// the file for a library item, loaded with the owner's sign-in and kept as a local blob URL (see GET /api/media/items/{id}/file)
const BLOBS = new Map();
function mediaBlob(it) {
  if (BLOBS.has(it.id)) return BLOBS.get(it.id);
  const p = fetch('/api/media/items/' + encodeURIComponent(it.id) + '/file', { headers: { 'Authorization': 'Bearer ' + token() } })
    .then(async r => { if (!r.ok) throw new Error(String(r.status)); return URL.createObjectURL(await r.blob()); });
  BLOBS.set(it.id, p); p.catch(() => BLOBS.delete(it.id));
  return p;
}
async function openMedia(id) {
  selected = 'm:' + id; LIB.open = null; renderSide(true);
  const r = await api('/api/media/items/' + encodeURIComponent(id)).catch(() => null);
  LIB.open = r && r.ok ? r.data.item : { error: (r && r.data && r.data.detail) || 'That item could not be opened.' };
  renderSide(true);
}
function mediaSec() {
  const s = mk('div', 'sec viewer'), it = LIB.open;
  s.append(mk('div', 'lbl', 'Library item'));
  if (!it) { s.append(mk('div', 'dim', 'Opening…')); return s; }
  if (it.error) { s.append(mk('div', 'dim', it.error)); return s; }
  s.append(mk('div', 'h', it.title));
  const kb = it.size_bytes > 1048576 ? (it.size_bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(it.size_bytes / 1024)) + ' KB';
  s.append(mk('div', 'dim', it.kind + ' · ' + kb + (it.duration_seconds ? ' · ' + it.duration_seconds + 's' : '')));
  const media = document.createElement(it.kind === 'image' ? 'img' : 'audio');
  if (it.kind === 'sound') media.controls = true;
  mediaBlob(it).then(u => { media.src = u; }).catch(() => { media.alt = 'The file could not be loaded.'; });
  s.append(media);
  if (it.kind === 'image' || it.kind === 'sound') {
    // edits make a NEW item (the original is kept): preview first, then save
    const OPS = { image: { crop: ['left', 'top', 'right', 'bottom'], resize: ['width', 'height'], brightness: ['factor'], contrast: ['factor'] },
                  sound: { trim: ['start', 'end'], fade_in: ['seconds'], fade_out: ['seconds'], volume: ['factor'] } }[it.kind];
    const r1 = mk('div', 'v-row'), sel = document.createElement('select');
    Object.keys(OPS).forEach(o => { const op = document.createElement('option'); op.value = o; op.textContent = o.replace('_', ' '); sel.append(op); });
    r1.append(mk('span', null, 'Edit'), sel); s.append(r1);
    const params = mk('div', 'v-row'); s.append(params);
    let inputs = {};
    const build = () => {
      params.textContent = ''; inputs = {};
      OPS[sel.value].forEach(name => {
        const inp = document.createElement('input'); inp.type = 'number'; inp.step = 'any'; inp.placeholder = name;
        const dflt = { start: 0, end: it.duration_seconds || '', seconds: 1, factor: 1 };
        if (name in dflt) inp.value = dflt[name];
        if (it.kind === 'image' && media.naturalWidth) {
          if (sel.value === 'crop') inp.value = { left: 0, top: 0, right: media.naturalWidth, bottom: media.naturalHeight }[name];
          if (sel.value === 'resize') inp.value = name === 'width' ? media.naturalWidth : media.naturalHeight;
        }
        params.append(mk('span', null, name), inp); inputs[name] = inp;
      });
    };
    sel.onchange = build; build(); if (it.kind === 'image') media.addEventListener('load', build, { once: true });
    const collect = () => { const p2 = {}; Object.keys(inputs).forEach(k => { if (inputs[k].value !== '') p2[k] = Number(inputs[k].value); }); return p2; };
    const r2 = mk('div', 'v-row'), bPrev = mk('button', 'lib-f', 'Preview'), bSave = mk('button', 'lib-f', 'Save as new'), bDisc = mk('button', 'lib-f', 'Discard');
    bSave.hidden = bDisc.hidden = true; r2.append(bPrev, bSave, bDisc); s.append(r2);
    const st = mk('div', 'dim'); st.style.marginTop = '6px'; let prev = null;
    bPrev.onclick = async () => {
      st.textContent = 'Making a preview…';
      const r = await api('/api/media/items/' + it.id + '/edit', 'POST', { operation: sel.value, params: collect(), preview: true }).catch(() => null);
      if (!r || !r.ok) { st.textContent = (r && r.data.detail) || 'That edit could not be previewed.'; return; }
      prev = { op: sel.value, params: collect() }; media.src = 'data:' + r.data.preview.mime + ';base64,' + r.data.preview.data_base64;
      bSave.hidden = bDisc.hidden = false; st.textContent = 'Preview only. Nothing is saved yet.';
    };
    bDisc.onclick = () => { prev = null; mediaBlob(it).then(u => { media.src = u; }).catch(() => {}); bSave.hidden = bDisc.hidden = true; st.textContent = ''; };
    bSave.onclick = async () => {
      if (!prev) return;
      const r = await api('/api/media/items/' + it.id + '/edit', 'POST', { operation: prev.op, params: prev.params, preview: false }).catch(() => null);
      if (!r || !r.ok) { st.textContent = (r && r.data.detail) || 'Could not save the edit.'; return; }
      loadLibrary(); openMedia(r.data.item.id);
    };
    const r3 = mk('div', 'v-row'), pr = document.createElement('input'); pr.type = 'text'; pr.maxLength = 500; pr.value = it.prompt || ''; pr.placeholder = it.kind === 'sound' ? 'Text to speak' : 'Describe the image';
    const bRegen = mk('button', 'lib-f', 'Re-generate'); r3.append(pr, bRegen); s.append(r3, st);
    bRegen.onclick = async () => {
      const text = pr.value.trim(); if (!text) { st.textContent = 'Write a prompt first.'; return; }
      if (!confirm('Make a new version from this prompt? It costs a little and counts against your media budget. The original stays.')) return;
      st.textContent = 'Generating…';
      const r = await api('/api/media/items/' + it.id + '/regenerate', 'POST', { prompt: text }).catch(() => null);
      if (!r || !r.ok) { st.textContent = (r && r.data.detail) || 'That could not be made.'; return; }
      loadLibrary(); openMedia(r.data.item.id);
    };
  } else if (it.prompt) s.append(mk('div', 'dim', 'Prompt: ' + it.prompt));
  const btns = mk('div'); btns.style.cssText = 'display:flex;gap:8px;margin-top:10px';
  const dl = mk('button', 'b', 'Download');
  dl.onclick = async () => { const r = await api('/api/media/items/' + it.id + '?download=true').catch(() => null); if (r && r.ok) location.href = r.data.item.url; };
  const del = mk('button', 'b', 'Delete'); del.style.borderColor = '#ff3344'; del.style.color = '#ff3344';
  del.onclick = async () => {
    if (!confirm('Delete "' + it.title + '" from your library? This cannot be undone.')) return;
    const r = await api('/api/media/items/' + it.id, 'DELETE').catch(() => null);
    if (r && r.ok) { selected = 'hub'; LIB.open = null; loadLibrary(); } else alert((r && r.data && r.data.detail) || 'It could not be deleted.');
  };
  btns.append(dl, del); s.append(btns);
  return s;
}

// ── subscribe and log out (the same billing and sign-in as the main page) ──
function logout() { try { localStorage.removeItem(TOKEN_KEY); } catch (e) {} location.href = '/'; }
async function refreshSubscription() {
  const btn = document.getElementById('t-sub');
  const r = await api('/api/billing/status').catch(() => null);
  if (!r || !r.ok) return;
  btn.hidden = !r.data.enabled; btn.disabled = !!r.data.active; btn.textContent = r.data.active ? 'SUBSCRIBED' : 'SUBSCRIBE';
}
async function subscribe() {
  const btn = document.getElementById('t-sub');
  btn.disabled = true; btn.textContent = 'OPENING…';
  const r = await api('/api/billing/checkout', 'POST', {}).catch(() => null);
  if (r && r.ok && r.data.url) { location.href = r.data.url; return; }
  btn.textContent = r && r.data && r.data.detail ? String(r.data.detail).slice(0, 40).toUpperCase() : 'UNAVAILABLE';
  setTimeout(() => { btn.disabled = false; btn.textContent = 'SUBSCRIBE'; }, 4000);
}

function backSec() {
  const s = mk('div', 'sec'); const b = mk('button', 'b', '← Helix inbox'); b.onclick = () => { selected = 'hub'; renderSide();
    if (window.matchMedia('(max-width: 820px)').matches) document.getElementById('center').scrollIntoView({ behavior: 'smooth', block: 'start' }); };
  s.append(b); return s;
}

// approvals can be given by voice: Helix reads a new checkpoint aloud, then "approve" or "decline" presses that card's own button
const SPOKEN_Q = new Set(); let _qSeeded = false, pendingVoice = null;
function inboxSec(missionId) {
  const s = mk('div', 'sec');
  const qs = S.questions.filter(q => !missionId || q.mission_id === missionId);
  const l = mk('div', 'lbl'); l.append(mk('span', null, missionId ? 'Waiting on you' : 'Helix inbox'), mk('span', 'act', qs.length ? qs.length + ' open' : 'all clear'));
  s.append(l);
  if (!qs.length) { s.append(mk('div', 'dim', missionId ? 'Nothing from this mission needs you.' : 'Nothing needs you. Questions, approvals and budget requests from the crew land here and in Helix chat.')); return s; }
  if (!_qSeeded) { S.questions.forEach(q => SPOKEN_Q.add(q.id)); _qSeeded = true; }   // existing cards are not read out again on load
  qs.forEach(q => {
    if ((q.kind === 'checkpoint' || q.kind === 'ops') && !SPOKEN_Q.has(q.id)) {
      SPOKEN_Q.add(q.id); pendingVoice = { id: q.id, until: Date.now() + 120000 };
      speakReply('Approval needed. ' + q.text + '. Say approve to confirm, or decline.', null, false);
    }
    s.append(questionCard(q));
  });
  return s;
}

function questionCard(q) {
  const m = S.missions.find(x => x.id === q.mission_id);
  const isQ = q.kind === 'question';
  const c = mk('div', 'card' + (isQ ? ' q' : ''));
  c.setAttribute('data-qid', q.id);
  c.append(mk('div', 'k', (q.kind === 'checkpoint' || q.kind === 'ops' ? 'Approval needed' : q.kind === 'budget' ? 'Budget request' : 'Question') + (m ? ' · ' + (m.title || 'mission') : '')));
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

const openAgents = new Set();
function rosterSec() {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Crew roster'), mk('span', 'act', (S.roster || []).length + ' on the station')); s.append(l);
  (S.roster || []).forEach(a => {
    const c = mk('div', 'agent');
    const top = mk('div', 'agent-top'); const d = mk('span', 'avatar'); d.style.borderColor = alpha(ROLE_COLORS[a.role] || '#00e5ff', 0.5);
    if (a.role === 'lead') { d.style.backgroundImage = 'url(/crew/helix.jpg)'; d.style.backgroundSize = '240%'; d.style.backgroundPosition = '50% 14%'; }
    else { const row = BOT_ROWS.indexOf(a.role); d.style.backgroundImage = 'url(/crew/bots.png?v=' + BOT_VER + ')'; const A = 38; d.style.backgroundSize = (17 * A) + 'px ' + (A * BOT_ROWS.length) + 'px'; d.style.backgroundPosition = '-4px ' + (-A * Math.max(0, row) - 1) + 'px'; }
    const nm = mk('span', 'agent-name', a.name); nm.style.color = ROLE_COLORS[a.role] || '#00e5ff';
    // compact card: face, name, role and status; what it can and cannot do shows on hover
    const who = mk('div', 'grow'); who.style.minWidth = '0'; who.append(nm, mk('div', 'agent-role', (a.role === 'lead' ? 'lead' : a.role) + ' · ' + a.model));
    top.append(d, who); top.style.marginBottom = '2px';
    // click the card to open it: what this agent does and what it cannot do (stays open across refreshes)
    const open = openAgents.has(a.role);
    c.classList.toggle('open', open);
    const chev = mk('span', 'agent-chev', open ? '▴' : '▾'); top.append(chev);
    c.append(top);
    if (open) {
      const det = mk('div', 'agent-det');
      det.append(mk('div', 'agent-k', 'Does'), mk('div', 'agent-can', a.can), mk('div', 'agent-k', 'Limits'), mk('div', 'agent-cannot', a.cannot),
                 mk('div', 'agent-k', 'Model'), mk('div', 'agent-cannot', a.model || '—'));
      if (a.herald) {
        const go = mk('button', 'b', a.working ? 'Making a brief…' : 'Send a brief now'); go.style.marginTop = '8px'; go.disabled = !!a.working;
        go.onclick = async (ev) => {
          ev.stopPropagation(); go.disabled = true; go.textContent = 'Making a brief…';
          const r = await api('/api/crew/brief/run', 'POST', {}).catch(() => null);
          go.textContent = r && r.ok ? 'Sent. Check your phone and Helix chat.' : ((r && r.data && r.data.detail) || 'It could not be made just now.');
          setTimeout(() => refresh(), 1500);
        };
        det.append(go);
      }
      c._det = det;
    }
    c.onclick = () => { if (openAgents.has(a.role)) openAgents.delete(a.role); else openAgents.add(a.role); renderSide(true); };
    const st = a.status_text || (a.working ? a.working + ' working' : a.waiting ? 'waiting on you' : 'standing by');
    const sl = mk('div', 'agent-st', st + (a.done ? ' · ' + a.done + ' done' : '')); if (a.working) sl.style.color = '#00ff88'; else if (a.waiting) sl.style.color = '#ff6d00';
    c.append(sl); if (c._det) c.append(c._det); s.append(c);
  });
  return s;
}

function factorySec(key) {
  const b = bays.find(x => x.key === key) || mods.find(x => x.key === key && x.platform) || factories().find(x => x.key === key);
  const s = mk('div', 'sec');
  if (!b) { s.append(mk('div', 'dim', 'That factory is gone.')); return s; }
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Factory'), mk('span', 'tag', b.state)); s.append(l);
  const h = mk('div', 'h', b.label); h.style.color = b.color; s.append(h);
  if (b.platform) {
    const c = S.connections && S.connections[key];
    const cr = mk('div', 'row'); cr.style.cursor = 'pointer'; cr.onclick = () => { selected = 'links'; renderSide(true); };
    const d = mk('span', 'dot'); d.style.background = c && c.connected ? '#00ff88' : '#ff6d00';
    cr.append(d, mk('span', 'grow', c && c.connected ? 'Connected' : (c && c.built ? 'Not connected. Tap to connect.' : 'Not built yet.')), mk('span', 'tag', 'Connections'));
    s.append(cr);
  }
  if (CATALOG_KEYS.includes(key)) s.append(catalogSec(key));
  const site = (S && S.products && S.products[key]) || (FIN && FIN.products && FIN.products[key]) || null;
  if (site) s.append(mk('div', 'dim', (site.live ? 'Site live' : 'Site NOT answering') + (site.url ? ' · ' + site.url : '') + (site.checked_at ? ' · checked ' + ago(site.checked_at) : '')));
  const r = FIN && FIN.factories ? FIN.factories[key] : null;
  if (r) { s.append(pnlBlock(r, 'P&L')); s.append(mk('div', 'dim', costLine(r))); }
  else s.append(mk('div', 'dim', FIN ? 'No money in or out for this factory yet.' : 'Loading the numbers…'));
  if (FIN && FIN.product_stats && FIN.product_stats[key]) {
    const ul = mk('div', 'lbl'); ul.style.marginTop = '12px'; ul.textContent = 'Users'; s.append(ul);
    s.append(mk('div', 'dim', usersLine(key)));
    s.append(trafficChart(key));
  }
  // who is working here right now
  const crewHere = b.tasks.filter(t => ['running', 'waiting', 'queued'].includes(t.status));
  const cl = mk('div', 'lbl'); cl.style.marginTop = '12px'; cl.textContent = 'Crew in this factory'; s.append(cl);
  if (!crewHere.length) s.append(mk('div', 'dim', 'Nobody is working here right now.'));
  crewHere.forEach(t => {
    const who = (S.roster || []).find(a => a.role === t.role);
    const row = mk('div', 'row'); const d = mk('span', 'dot'); d.style.background = ROLE_COLORS[t.role] || '#00e5ff';
    row.append(d, mk('span', 'grow', (who ? who.name : t.role) + ' · ' + t.title), mk('span', 'tag', t.status)); s.append(row);
  });
  if (!isOwner() && S.businesses.some(x => x.key === key)) {
    const rm = mk('button', 'b no', 'Remove from Station'); rm.style.marginTop = '8px';
    rm.onclick = async () => { if (!confirm('Remove ' + b.label + ' from your Station? Its missions and files stay.')) return;
      await api('/api/crew/businesses/' + encodeURIComponent(key) + '/remove', 'POST', {}); selected = 'hub'; refresh(); loadFinance(); };
    s.append(rm);
  }
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

const CATALOG_KEYS = ['etsy', 'stripe', 'google_play', 'app_store', 'deadzone'];
const CATALOG_TITLE = { etsy: 'Listings in the shop', stripe: 'Products on Stripe', google_play: 'Apps on Google Play', app_store: 'Apps on the App Store', deadzone: 'Games on the Deadzone site' };
const CAT = {};
function loadCatalog(key, force) {
  const c = CAT[key];
  if (!force && c && (c.loading || Date.now() - c.at < 60000)) return;
  CAT[key] = Object.assign(c || {}, { loading: true, at: Date.now() });
  api('/api/crew/catalog/' + key).then(r => {
    CAT[key] = { data: r.ok ? r.data : { items: [], note: (r.data && r.data.detail) || 'The list could not be loaded.' }, at: Date.now() };
    if (selected === 'f:' + key) renderSide();
  }).catch(() => { CAT[key] = { data: { items: [], note: 'The list could not be loaded.' }, at: Date.now() }; });
}
function catalogSec(key) {
  const s = mk('div'); s.style.marginTop = '12px';
  const l = mk('div', 'lbl'); l.append(mk('span', null, CATALOG_TITLE[key] || 'Catalog'));
  const rf = mk('span', 'tag', '↻'); rf.style.cursor = 'pointer'; rf.title = 'Refresh'; rf.onclick = () => { loadCatalog(key, true); renderSide(); };
  l.append(rf); s.append(l);
  loadCatalog(key);
  const c = CAT[key], d = c && c.data;
  if (!d) { s.append(mk('div', 'dim', 'Loading…')); return s; }
  if (d.note) s.append(mk('div', 'dim', d.note));
  (d.items || []).forEach(it => {
    const row = mk('div', 'row');
    const dot = mk('span', 'dot'); dot.style.background = /sale|playable|live|crew/.test(it.status) ? '#00ff88' : '#ffd23f';
    const g = mk('span', 'grow');
    if (it.url) { const a = mk('a', null, it.name); a.href = it.url; a.target = '_blank'; a.rel = 'noopener'; a.style.color = 'inherit'; g.append(a); } else g.textContent = it.name;
    row.append(dot, g);
    if (it.price) row.append(mk('span', 'dim', it.price));
    if (it.status) row.append(mk('span', 'tag', it.status));
    if (d.editable) {
      const x = mk('button', 'xbtn', '✕'); x.title = 'Remove from this list';
      x.onclick = async () => { const r = await api('/api/crew/catalog/' + key, 'POST', { remove: it.name }); if (r.ok) CAT[key] = { data: r.data, at: Date.now() }; renderSide(); };
      row.append(x);
    }
    s.append(row);
  });
  if (d.editable) {
    const add = mk('button', 'b', '+ Add a live app'); add.style.marginTop = '6px';
    add.onclick = async () => {
      const name = prompt('App name, as it shows in the store'); if (!name) return;
      const url = prompt('Store link (optional, https://...)') || '';
      const r = await api('/api/crew/catalog/' + key, 'POST', { add_name: name, add_url: url });
      if (r.ok) CAT[key] = { data: r.data, at: Date.now() }; renderSide();
    };
    s.append(add);
  }
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
    if (!['done', 'cancelled'].includes(m.status)) {
      const x = mk('button', 'xbtn', '✕'); x.title = 'Cancel this mission';
      x.onclick = async (ev) => { ev.stopPropagation(); if (!confirm('Cancel "' + (m.title || m.goal) + '"? Its open work stops.')) return; await api('/api/crew/missions/' + m.id + '/cancel', 'POST'); refresh(); };
      r.append(x);
    }
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
  if (!['done', 'cancelled'].includes(m.status)) {
    const bw = mk('div', 'btns'); bw.style.marginTop = '10px';
    const inp = document.createElement('input'); inp.type = 'number'; inp.step = '0.5'; inp.min = String(Math.max(0.05, m.spent_usd || 0).toFixed(2));
    inp.value = (m.budget_usd || 0).toFixed(2); inp.style.width = '110px';
    const msg = mk('span', 'dim');
    const set = mk('button', 'b', 'Set budget');
    set.onclick = async () => {
      const r = await api('/api/crew/missions/' + m.id + '/budget', 'POST', { budget_usd: parseFloat(inp.value) });
      if (r.ok) { refresh(); } else { msg.textContent = (r.data && r.data.detail) || 'Could not set the budget.'; }
    };
    bw.append(mk('span', 'dim', 'Budget $'), inp, set, msg);
    s.append(bw);
  }
  const btns = mk('div', 'btns'); btns.style.marginTop = '10px';
  const act = (a, label, cls) => { const b = mk('button', 'b' + (cls ? ' ' + cls : ''), label); b.onclick = async () => { if (a === 'cancel' && !confirm('Cancel this mission? Its open work stops.')) return; await api('/api/crew/missions/' + m.id + '/' + a, 'POST'); refresh(); }; btns.append(b); };
  if (m.status === 'active' || m.status === 'planning') act('pause', 'Pause');
  if (m.status === 'paused' || m.status === 'stalled') act('resume', 'Resume');
  if (!['done', 'cancelled'].includes(m.status)) act('cancel', 'Cancel', 'no');
  if (m.status === 'done' || m.status === 'stalled') act('report', (m.outputs || []).some(o => o.name === 'mission_report.md') ? 'Refresh report' : 'Write report');
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
  if (m.summary) { const sm = mk('div', 'dim', m.summary); sm.style.whiteSpace = 'pre-wrap'; if (m.status === 'done') { const h = mk('div', 'lbl', 'Mission report'); h.style.marginTop = '12px'; s.append(h); } s.append(sm); }
  return s;
}

function outboxSec(m) {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Outbox'), mk('span', 'act', m.outputs.length + ' file(s)')); s.append(l);
  if (!m.outputs.length) { s.append(mk('div', 'dim', 'Finished files land here.')); return s; }
  m.outputs.slice().reverse().sort((x, y) => String(y.created_at || '').localeCompare(String(x.created_at || ''))).forEach(o => {        // newest file on top
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

// ── a subscriber's setup: add businesses, then connect each service step by step ──
let GUIDE = null;
function usageLine(u) {
  return 'This month: ' + u.included_pct + '% of included crew work used' +
    (u.payg ? ' · extra usage ' + money(u.extra_billed_usd) + ' of ' + money(u.extra_cap_usd) + ' cap' : (u.included_pct >= 100 ? ' · the crew resumes on the 1st' : ''));
}
function linkText(el, text) {                                   // plain text with its https links clickable
  String(text).split(/(https:\/\/[^\s)]+)/).forEach(part => {
    if (/^https:\/\//.test(part)) { const a = mk('a', null, part); a.href = part; a.target = '_blank'; a.rel = 'noopener'; a.style.color = 'var(--cyan)'; el.append(a); }
    else el.append(document.createTextNode(part));
  });
  return el;
}
function guideBox(kind) {
  const g = S.guides[kind], box = mk('div', 'pnl');
  box.append(mk('div', 'pnl-t', g.label));
  g.steps.forEach((t, i) => box.append(linkText(mk('div', 'dim'), (g.steps.length > 1 ? (i + 1) + '. ' : '') + t)));
  const inp = mk(kind === 'rules' ? 'textarea' : 'input'); inp.placeholder = g.field; inp.style.marginTop = '8px';
  if (kind !== 'rules') inp.type = 'text';
  if (kind === 'rules') { inp.value = S.house_rules || ''; inp.maxLength = 2000; }
  if (kind === 'phone') { const a = new Uint8Array(6); crypto.getRandomValues(a); inp.value = 'https://ntfy.sh/aibussol-' + Array.from(a, x => x.toString(16).padStart(2, '0')).join(''); }
  if (kind === 'stripe' || kind === 'github') inp.autocomplete = 'off';
  box.append(inp);
  let ex = null;
  if (g.extra) { ex = mk('input'); ex.type = 'text'; ex.placeholder = g.extra; ex.style.marginTop = '6px'; box.append(ex); }
  const msg = mk('div', 'dim'); const row = mk('div', 'row');
  const go = mk('button', 'b', kind === 'rules' ? 'SAVE' : 'TEST & SAVE'), no = mk('button', 'b no', 'Close');
  go.onclick = async () => {
    go.disabled = true; msg.textContent = kind === 'rules' ? 'Saving…' : 'Testing with ' + g.label.split(' (')[0] + '…';
    const x = await api('/api/crew/connect/' + kind, 'POST', { value: inp.value, extra: ex ? ex.value : '' }).catch(() => null);
    go.disabled = false;
    if (x && x.ok) { GUIDE = null; refresh(); loadFinance(); }
    else msg.textContent = x && typeof x.data.detail === 'string' ? x.data.detail : 'That did not work. Try again.';
  };
  no.onclick = () => { GUIDE = null; renderSide(true); };
  row.append(go, no); box.append(row, msg);
  if (kind === 'stripe') box.append(mk('div', 'pnl-note', 'Your key is tested, stored encrypted and never shown again. Anything that charges or publishes still waits for your Approve.'));
  return box;
}
function setupSec() {
  const s = mk('div', 'sec');
  const l = mk('div', 'lbl'); l.append(mk('span', null, 'Set up your Station'), mk('span', 'act', (S.businesses.length) + ' of 6 businesses')); s.append(l);
  s.append(mk('div', 'dim', 'Every agent on the crew works for you. Add your businesses, then connect what they should use. Each step is checked before it is saved.'));
  const steps = [
    ['Add a business', S.businesses.length > 0],
    ['Connect Stripe (your payments and P&L)', S.connections.stripe && S.connections.stripe.connected],
    ['Connect your phone (alerts and briefs)', S.connections.phone && S.connections.phone.connected],
    ['Connect GitHub (for code and websites)', S.connections.github && S.connections.github.connected],
    ['Pair your PC (games in Unity or Unreal with Claude Code)', S.connections.workbench && S.connections.workbench.paired],
  ];
  steps.forEach(([t, ok], i) => {
    const r = mk('div', 'row'); const d = mk('span', 'dot'); d.style.background = ok ? '#00ff88' : '#ff6d00';
    r.append(d, mk('span', 'grow', (i + 1) + '. ' + t + (ok ? ' · done' : '')));
    if (!ok && i > 0) { const b = mk('button', 'b', 'Start'); b.onclick = () => { GUIDE = ['', 'stripe', 'phone', 'github', ''][i] || null; selected = 'links'; renderSide(true);
      if (i === 4) document.getElementById('links-box').scrollIntoView({ behavior: 'smooth' }); }; r.append(b); }
    s.append(r);
  });
  if (S.businesses.length < 6) {
    const box = mk('div', 'pnl'); box.append(mk('div', 'pnl-t', 'Add a business'));
    const nm = mk('input'); nm.type = 'text'; nm.placeholder = 'Name, e.g. Bloom Candles'; nm.maxLength = 40; nm.id = 'biz-name';
    const url = mk('input'); url.type = 'text'; url.placeholder = 'Website (optional), e.g. bloomcandles.com'; url.maxLength = 200; url.style.marginTop = '6px'; url.id = 'biz-url';
    const msg = mk('div', 'dim'); const go = mk('button', 'b', 'ADD TO STATION'); go.style.marginTop = '6px';
    go.onclick = async () => {
      go.disabled = true; const x = await api('/api/crew/businesses', 'POST', { name: nm.value, url: url.value }).catch(() => null); go.disabled = false;
      if (x && x.ok) { selected = 'hub'; refresh(); loadFinance(); } else msg.textContent = x && typeof x.data.detail === 'string' ? x.data.detail : 'Could not add it.';
    };
    box.append(nm, url, go, msg); s.append(box);
  }
  if (S.usage) s.append(mk('div', 'dim', usageLine(S.usage)));
  return s;
}

function linksSec() {
  const s = mk('div', 'sec');
  s.append(Object.assign(mk('div', 'lbl'), { textContent: 'Connections' }));
  // connected first, then built but not connected, then not built yet (same order within each group)
  const rank = c => (c && c.connected ? 2 : c && c.built ? 1 : 0);
  Object.entries(S.connections || {}).sort((a, b) => rank(b[1]) - rank(a[1])).forEach(([key, c]) => {
    const r = mk('div', 'row'); const d = mk('span', 'dot'); d.style.background = c.connected ? '#00ff88' : c.built ? '#ff6d00' : '#2a3a44';
    const g = mk('div', 'grow'); g.append(mk('div', null, c.label + (c.connected ? ' · connected' : c.built ? ' · not connected' : ' · not built yet')));
    if (!c.connected && c.setup) g.append(mk('div', 'dim', c.setup));
    if (c.note) g.append(mk('div', 'dim', c.note));
    r.append(d, g);
    if (c.guide) {
      const b = mk('button', c.connected ? 'b no' : 'b', c.connected ? 'Disconnect' : 'Connect');
      b.onclick = async () => {
        if (!c.connected) { GUIDE = c.guide; renderSide(true); return; }
        if (confirm('Disconnect ' + c.label + '? Its key is deleted.')) { await api('/api/crew/connect/' + c.guide + '/disconnect', 'POST', {}); refresh(); }
      };
      r.append(b);
    }
    if (key === 'etsy' && c.can_connect) {
      const b = mk('button', 'b', 'Connect'); b.onclick = async () => { const x = await api('/api/crew/connect/etsy'); if (x.ok && x.data.url) location.href = x.data.url; else alert(x.data.detail || 'Etsy cannot be connected yet.'); };
      r.append(b);
    }
    if (key === 'google' && c.can_connect) {
      const b = mk('button', 'b', 'Connect'); b.onclick = async () => { const x = await api('/api/crew/connect/google'); if (x.ok && x.data.url) location.href = x.data.url; else alert(x.data.detail || 'Google cannot be connected yet.'); };
      r.append(b);
    }
    if (key === 'google' && c.connected) {
      const b = mk('button', 'b no', 'Disconnect'); b.onclick = async () => { if (confirm('Forget the Google connection? Helix will no longer see your Gmail or Drive.')) { await api('/api/crew/connect/google/disconnect', 'POST'); refresh(); } };
      r.append(b);
    }
    if (key === 'workbench') {
      const b = mk('button', 'b', c.paired ? 'Re-pair' : 'Pair your PC');
      b.onclick = async () => {
        if (c.paired && !confirm('Make a new PC token? The old one stops working, so restart the helper with the new token.')) return;
        const x = await api('/api/crew/workbench/pair', 'POST', {}); if (x.ok) { PAIR = x.data; renderSide(true); } else alert(x.data.detail || 'Could not pair right now.');
      };
      r.append(b);
    }
    if (key === 'etsy' && c.connected) {
      const b = mk('button', 'b no', 'Disconnect'); b.onclick = async () => { if (confirm('Forget the Etsy connection?')) { await api('/api/crew/connect/etsy/disconnect', 'POST'); refresh(); } };
      r.append(b);
    }
    s.append(r);
  });
  if (GUIDE && S.guides && S.guides[GUIDE]) s.append(guideBox(GUIDE));
  if (!isOwner() && S.guides) {
    const rr_ = mk('div', 'row'); const d = mk('span', 'dot'); d.style.background = S.house_rules ? '#00ff88' : '#ff6d00';
    const g = mk('div', 'grow'); g.append(mk('div', null, 'Your house rules' + (S.house_rules ? ' · set' : ' · optional'))); rr_.append(d, g);
    const b = mk('button', 'b', 'Edit'); b.onclick = () => { GUIDE = 'rules'; renderSide(true); }; rr_.append(b); s.append(rr_);
  }
  if (PAIR) {
    // shown once: the token and the exact commands for the PC
    const box = mk('div', 'pnl'); box.append(mk('div', 'pnl-t', 'Pair your PC (shown once)'));
    const steps = [
      '1. On your PC, install Python 3 (python.org) and Claude Code, and sign in to Claude Code.',
      '2. Download the helper: ' + location.origin + PAIR.download,
      '3. In the folder you saved it to, run (one line, your own paths):',
    ];
    steps.forEach(t => box.append(mk('div', 'dim', t)));
    const cmd = 'python aibussol_workbench.py --server ' + location.origin + ' --token ' + PAIR.token +
      ' --project "Deadzone=C:\\path\\to\\DeadzoneRun" --unity "C:\\Program Files\\Unity\\Hub\\Editor\\<version>\\Editor\\Unity.exe"';
    const pre = mk('div', null, cmd); pre.style.cssText = 'font-size:10px;background:#050a12;border:1px solid var(--border);padding:8px;margin:6px 0;overflow-wrap:anywhere;user-select:all';
    box.append(pre);
    const row = mk('div'); row.style.cssText = 'display:flex;gap:6px';
    const cp = mk('button', 'lib-f', 'Copy command'); cp.onclick = () => { try { navigator.clipboard.writeText(cmd); cp.textContent = 'Copied'; } catch (e) {} };
    const dl = mk('a', 'lib-f', 'Download helper'); dl.href = PAIR.download; dl.setAttribute('download', 'aibussol_workbench.py');
    const ok = mk('button', 'lib-f', 'Done'); ok.onclick = () => { PAIR = null; renderSide(true); };
    row.append(cp, dl, ok); box.append(row);
    box.append(mk('div', 'pnl-note', 'Add one --project NAME=FOLDER for each project (the name matches the mission project, e.g. Deadzone, FlipDeck). The token is not shown again; Re-pair makes a new one. After the first run, just: python aibussol_workbench.py'));
    s.append(box);
  }
  s.append(mk('div', 'dim', 'Official APIs only. Read and draft actions run on their own; anything public, paid or permanent waits for your Approve.'));
  return s;
}
let PAIR = null;

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
const BOT_VER = 'b7705b0c';   // changes whenever bots.png changes, so browsers never mix an old sheet with new code
const BOT_ROWS = ['lead', 'researcher', 'builder', 'writer', 'designer', 'operator', 'reviewer', 'briefer', 'workbench',
                  'marketer', 'social', 'support', 'analyst', 'qa', 'finance'], BOT_CELL = 64;
// sheet columns: 0 standing (front), then 4 walk frames each for front, back, right, left
const BOT_DIRS = { front: 0, back: 1, right: 2, left: 3 };
function dirOf(dx, dy, prev) { if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) return prev || 'front'; return Math.abs(dx) >= Math.abs(dy) * 0.8 ? (dx > 0 ? 'right' : 'left') : (dy > 0 ? 'front' : 'back'); }
const botSheet = new Image(); let botReady = false;
botSheet.onload = () => { botReady = true; }; botSheet.src = '/crew/bots.png?v=' + BOT_VER;
// Michael's own artwork, used when the file exists (a 404 leaves the built-in drawing): asteroids, the satellite, and Helix walking the deck
const HELIX_ON_DECK = false;                                    // Helix does not walk the ring (it did not look right); her art stays in assets/art for another use
const ART = { rocks: [], satellite: null, helix: null, helixViews: null };
function loadArt(name, done) { const im = new Image(); im.onload = () => done(im); im.onerror = () => {}; im.src = '/crew/art/' + name; }
['asteroid1.png', 'asteroid2.png', 'asteroid3.png'].forEach(n => loadArt(n, im => ART.rocks.push(im)));
loadArt('satellite.png', im => { ART.satellite = im; });
if (HELIX_ON_DECK) loadArt('helix-walk.png', im => { ART.helix = im; });
if (HELIX_ON_DECK) loadArt('helix-views.png', im => { ART.helixViews = im; });     // four square pictures in a row: front, back, facing right, facing left; her walk is animated in code           // the same sheet layout as bots.png, one row: standing, then 4 walk frames each for front, back, right, left
// the businesses on this Station: the owner's products, or a subscriber's own (from the server)
function prods() {
  return (S && S.businesses ? S.businesses : []).map(b => ({ key: b.key, label: String(b.name).toUpperCase(), color: b.color || '#ff4fb8', url: b.url || '' }));
}
function bizName(k) {
  const b = S && S.businesses ? S.businesses.find(x => x.key === k) : null;
  return b ? b.name : k === 'aibussol' ? 'Aibussol' : k === 'crew' ? 'Crew-made products' : k.startsWith('new:') ? k.slice(4) : k;
}
function isOwner() { return !!(S && S.owner); }
const PLATFORMS = [
  { key: 'stripe', label: 'STRIPE', color: '#4f6bff' },
  { key: 'etsy', label: 'ETSY', color: '#ffd23f' },
  { key: 'google_play', label: 'GOOGLE PLAY', color: '#34d17a' },
  { key: 'app_store', label: 'APP STORE', color: '#3fc8ff' },
];
const RING = ['p0', 'stripe', 'p1', 'etsy', 'p2', 'google_play', 'new', 'app_store'];   // clockwise from the top
const MAX_EXTRA_BRANCHES = 8;
const WORKING = ['active', 'planning', 'paused', 'stalled'];

// The size the station has when it first loads with the columns and the chat at their default sizes: the window minus the two 210px columns, the
// 48px top bar and the chat box. Dragging a column or the chat changes the window onto the station, but not the size of the station itself.
const DEFAULT_COL = 210, TOP_BAR = 48, DEFAULT_CHAT = 225;
function designSize() {
  try { if (window.matchMedia('(max-width: 820px)').matches) return [W, H]; } catch (e) {}
  return [Math.max(520, window.innerWidth - 2 * DEFAULT_COL), Math.max(380, window.innerHeight - TOP_BAR - DEFAULT_CHAT)];
}
let _seed = 20261024;
function srand() { _seed = (_seed * 1664525 + 1013904223) % 4294967296; return _seed / 4294967296; }      // the same sky every time the canvas is resized
function resize() {
  _seed = 20261024;
  const r = cv.getBoundingClientRect(); DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = r.width; H = r.height; cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR); ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  stars = Array.from({ length: Math.round(W * H / 2200) }, () => ({ x: srand() * W, y: srand() * H, z: srand(), p: srand() * 6.28 }));
  sky = document.createElement('canvas'); sky.width = Math.max(1, Math.round(W)); sky.height = Math.max(1, Math.round(H));
  const s = sky.getContext('2d');
  s.fillStyle = '#04050a'; s.fillRect(0, 0, W, H);
  for (const [fx, fy, rad, col] of [[0.18, 0.25, 0.55, 'rgba(90,40,160,0.16)'], [0.85, 0.8, 0.6, 'rgba(0,120,150,0.13)'], [0.7, 0.15, 0.35, 'rgba(255,90,0,0.05)']]) {
    const g = s.createRadialGradient(W * fx, H * fy, 0, W * fx, H * fy, Math.max(W, H) * rad);
    g.addColorStop(0, col); g.addColorStop(1, 'rgba(0,0,0,0)'); s.fillStyle = g; s.fillRect(0, 0, W, H);
  }
  drawMilkyWay(s, W, H);
  mini = mini ? Object.assign(mini, { x: Math.min(mini.x, W + 40), y: Math.min(mini.y, H) }) : { x: W * 0.13, y: H * 0.2, vx: 0.0055, vy: 0.0012, rot: 0.35 };
  layout();
}
window.addEventListener('resize', resize);
// dragging a column or the chat changes the stage's box: redraw the canvas at its real size (it was being stretched, which squished the ring)
(function () {
  const stage = document.getElementById('stage'); if (!stage || !window.ResizeObserver) return;
  let last = '';
  new ResizeObserver(() => {
    const r = stage.getBoundingClientRect(), key = Math.round(r.width) + 'x' + Math.round(r.height);
    if (key === last) return;
    last = key;
    resize();                                              // resizing the canvas clears it, so the picture is painted again in the same step:
    paint(performance.now());                              // the browser never gets to show an empty station while you drag
  }).observe(stage);
})();

const PLATFORM_KEY = { etsy: 'etsy', stripe: 'stripe', googleplay: 'google_play', playstore: 'google_play', appstore: 'app_store', ios: 'app_store' };
function factoryKey(m) {
  const p = String(m.project || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  if ((p === 'aibussol' && isOwner()) || prods().some(f => f.key === p)) return p;
  if (PLATFORM_KEY[p]) return PLATFORM_KEY[p];
  const words = ((m.title || '') + ' ' + (m.goal || '')).toLowerCase();
  const biz = (S && S.businesses ? S.businesses : []).find(b => b.name && words.includes(String(b.name).toLowerCase()));
  if (biz) return biz.key;                                                       // work for a business lives in that business's factory
  if (/\betsy\b/i.test((m.title || '') + ' ' + (m.goal || ''))) return 'etsy';   // Etsy work lives in the Etsy factory
  return 'aibussol';                                                             // no business owns this mission: the crew works it from the core, it gets no factory of its own
}


function fillBay(b) {
  const ms = S ? S.missions.filter(m => m.status !== 'cancelled') : [];
  b.missions = ms.filter(m => factoryKey(m) === b.key);
  const current = b.missions.filter(m => WORKING.includes(m.status));
  b.tasks = current.flatMap(m => m.tasks.filter(t => t.status !== 'cancelled'));
  b.planning = current.some(m => m.status === 'planning');
  b.title = current.length ? (current[0].title || current[0].goal) : '';
  b.state = b.tasks.some(t => t.status === 'waiting') ? 'NEEDS YOU' : b.tasks.some(t => t.status === 'running') ? 'WORKING'
    : b.planning ? 'PLANNING' : current.length ? 'QUEUED' : 'IDLE';              // finished work is just idle: nothing running here
  if (b.state === 'IDLE') b.powered = true;                                     // an idle factory stays lit and its tube keeps flowing
  // the product itself: LIVE or DOWN from the server's check of its site, shown whenever no crew is working there
  const site = S && S.products ? S.products[b.key] : null;
  if (site && b.state === 'IDLE') {
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


// ── deep space: a Milky Way in the far upper-right corner, shooting stars from any side at any depth, and a small station adrift ──
function drawMilkyWay(c, w, h) {
  const L = Math.max(w, h) * 0.78;
  c.save(); c.translate(w * 0.9, h * 0.1); c.rotate(2.35);                 // a diagonal band running from the corner toward the middle
  const glow = (len, thick, col, a) => {
    c.save(); c.scale(1, thick / len);
    const g = c.createRadialGradient(0, 0, 0, 0, 0, len);
    g.addColorStop(0, col.replace('A', a)); g.addColorStop(0.55, col.replace('A', a * 0.45)); g.addColorStop(1, col.replace('A', 0));
    c.fillStyle = g; c.beginPath(); c.arc(0, 0, len, 0, 6.29); c.fill(); c.restore();
  };
  glow(L, L * 0.34, 'rgba(70,60,150,A)', 0.2);                           // wide violet haze
  glow(L * 0.85, L * 0.17, 'rgba(120,150,220,A)', 0.2);                  // blue-white arm
  glow(L * 0.5, L * 0.07, 'rgba(255,225,190,A)', 0.24);                  // warm bright core
  c.save(); c.translate(0, L * 0.015); glow(L * 0.7, L * 0.02, 'rgba(3,4,10,A)', 0.55); c.restore();   // the dark dust lane
  for (let i = 0; i < 520; i++) {                                         // thousands of faint suns, thickest on the band's middle
    const u = (srand() * 2 - 1), v = (srand() + srand() + srand() - 1.5) / 1.5;
    const x = u * L * (0.2 + 0.8 * srand()), y = v * L * 0.12 * (1 - Math.abs(u) * 0.6);
    const a = (0.1 + srand() * 0.5) * (1 - Math.abs(u) * 0.7);
    c.fillStyle = srand() < 0.2 ? 'rgba(255,214,170,' + a + ')' : 'rgba(205,225,255,' + a + ')';
    c.fillRect(x, y, srand() < 0.1 ? 1.6 : 0.9, srand() < 0.1 ? 1.6 : 0.9);
  }
  c.restore();
}
let meteors = [], nextMeteor = 0, mini = null, lastSkyT = 0;
function spawnMeteor(t) {
  const d = Math.random();                                                // 0 = far away (small, slow, faint), 1 = close (big, fast, bright)
  const side = Math.floor(Math.random() * 4), m = 30;
  const sx = side === 0 ? -m : side === 1 ? W + m : Math.random() * W, sy = side === 2 ? -m : side === 3 ? H + m : Math.random() * H;
  const tx = W * (0.15 + Math.random() * 0.7), ty = H * (0.15 + Math.random() * 0.7), dx = tx - sx, dy = ty - sy, n = Math.hypot(dx, dy) || 1;
  const speed = 0.22 + 0.7 * d, reach = Math.max(W, H) * (0.35 + Math.random() * 0.5);
  meteors.push({ t0: t, sx, sy, ux: dx / n, uy: dy / n, speed, len: 36 + 170 * d, w: 0.7 + 2.1 * d, dur: reach / speed, a: 0.35 + 0.65 * d, warm: Math.random() < 0.25 });
  if (meteors.length > 6) meteors.shift();
}
function drawMeteors(t) {
  if (!reducedMotion && t > nextMeteor) { spawnMeteor(t); nextMeteor = t + 2200 + Math.random() * 7000; }
  meteors = meteors.filter(k => t - k.t0 < k.dur);
  for (const k of meteors) {
    const e = t - k.t0, p = e / k.dur, hx = k.sx + k.ux * k.speed * e, hy = k.sy + k.uy * k.speed * e;
    const fade = Math.min(1, p * 8, (1 - p) * 4) * k.a, tx = hx - k.ux * k.len, ty = hy - k.uy * k.len;
    const col = k.warm ? '255,214,160' : '200,230,255';
    const g = ctx.createLinearGradient(hx, hy, tx, ty);
    g.addColorStop(0, 'rgba(' + col + ',' + fade + ')'); g.addColorStop(1, 'rgba(' + col + ',0)');
    ctx.strokeStyle = g; ctx.lineWidth = k.w; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(tx, ty); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,' + fade + ')'; ctx.beginPath(); ctx.arc(hx, hy, k.w * 0.9, 0, 6.29); ctx.fill();
  }
}
let rocks = [], nextRock = 0;
function spawnRock(t) {                                                   // an occasional tumbling rock drifting through the far background
  const d = Math.random();                                                // 0 = far (small, slow, dim), 1 = near (big, faster)
  const side = Math.floor(Math.random() * 4), m = 60;
  const sx = side === 0 ? -m : side === 1 ? W + m : Math.random() * W, sy = side === 2 ? -m : side === 3 ? H + m : Math.random() * H;
  const tx = W * (0.1 + Math.random() * 0.8), ty = H * (0.1 + Math.random() * 0.8), dx = tx - sx, dy = ty - sy, n = Math.hypot(dx, dy) || 1;
  const r = 7 + 26 * d, speed = 0.018 + 0.05 * d, pts = 9 + Math.floor(Math.random() * 4);
  const shape = Array.from({ length: pts }, (_, i) => ({ a: i / pts * 6.2832, k: 0.72 + Math.random() * 0.4 }));
  const craters = Array.from({ length: 3 }, () => ({ x: (Math.random() - 0.5) * r * 0.9, y: (Math.random() - 0.5) * r * 0.9, r: r * (0.1 + Math.random() * 0.14) }));
  rocks.push({ pick: Math.floor(Math.random() * 1000), t0: t, sx, sy, ux: dx / n, uy: dy / n, speed, r, spin: (Math.random() - 0.5) * 0.0012, rot0: Math.random() * 6.28, shape, craters, a: 0.45 + 0.5 * d, tint: Math.random() < 0.35 ? [128, 108, 92] : [112, 118, 128],
               dur: (Math.hypot(W, H) * 1.15) / speed });
  if (rocks.length > 3) rocks.shift();
}
function drawRocks(t) {
  if (!reducedMotion && t > nextRock) { spawnRock(t); nextRock = t + 14000 + Math.random() * 26000; }
  rocks = rocks.filter(k => t - k.t0 < k.dur);
  for (const k of rocks) {
    const e = t - k.t0, x = k.sx + k.ux * k.speed * e, y = k.sy + k.uy * k.speed * e;
    if (x < -90 || x > W + 90 || y < -90 || y > H + 90) continue;
    const [cr, cg, cb] = k.tint;
    if (ART.rocks.length) {                                                // Michael's asteroid art, tumbling
      const im = ART.rocks[k.pick % ART.rocks.length], d = k.r * 2.4;
      ctx.save(); ctx.translate(x, y); ctx.rotate(k.rot0 + k.spin * e); ctx.globalAlpha = k.a; ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(im, -d / 2, -d * (im.naturalHeight / im.naturalWidth) / 2, d, d * (im.naturalHeight / im.naturalWidth)); ctx.restore(); continue;
    }
    ctx.save(); ctx.translate(x, y); ctx.rotate(k.rot0 + k.spin * e); ctx.globalAlpha = k.a;
    ctx.beginPath(); k.shape.forEach((v, i) => { const px = Math.cos(v.a) * k.r * v.k, py = Math.sin(v.a) * k.r * v.k; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }); ctx.closePath();
    const g = ctx.createRadialGradient(-k.r * 0.4, -k.r * 0.4, k.r * 0.1, 0, 0, k.r * 1.2);      // lit from the upper left, like the sun
    g.addColorStop(0, `rgb(${cr + 60},${cg + 55},${cb + 50})`); g.addColorStop(0.55, `rgb(${cr},${cg},${cb})`); g.addColorStop(1, `rgb(${cr * 0.3 | 0},${cg * 0.3 | 0},${cb * 0.3 | 0})`);
    ctx.fillStyle = g; ctx.fill(); ctx.strokeStyle = 'rgba(0,0,0,0.35)'; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = 'rgba(0,0,0,0.28)';
    k.craters.forEach(c => { ctx.beginPath(); ctx.arc(c.x, c.y, c.r, 0, 6.29); ctx.fill(); });
    ctx.restore();
  }
}
function drawMiniStation(t, dt) {                                         // far away and tiny: it only drifts and turns very slowly
  if (!mini) return;
  if (!reducedMotion) {
    mini.x += mini.vx * dt; mini.y += mini.vy * dt; mini.rot += 0.00006 * dt;
    if (mini.x > W + 60) { mini.x = -60; mini.y = H * (0.08 + Math.random() * 0.8); }
  }
  const u = Math.max(0.55, Math.min(1.1, SC)) * 1.35;
  if (ART.satellite) {                                                    // Michael's satellite art, drifting and turning slowly
    const d = 96 * u, im = ART.satellite;
    ctx.save(); ctx.translate(mini.x, mini.y); ctx.rotate(mini.rot); ctx.globalAlpha = 0.95; ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(im, -d / 2, -d * (im.naturalHeight / im.naturalWidth) / 2, d, d * (im.naturalHeight / im.naturalWidth)); ctx.restore();
    return;
  }
  ctx.save(); ctx.translate(mini.x, mini.y); ctx.rotate(mini.rot); ctx.scale(u, u); ctx.globalAlpha = 0.8;
  ctx.shadowColor = 'rgba(90,170,255,0.5)'; ctx.shadowBlur = 8;
  ctx.fillStyle = '#7f93a8'; ctx.fillRect(-26, -2.2, 52, 4.4);              // truss
  for (const sx of [-1, 1]) {                                               // solar wings, with their cell grid
    ctx.fillStyle = '#1d4f8f'; ctx.fillRect(sx * 27 - (sx < 0 ? 13 : 0), -9, 13, 18);
    ctx.strokeStyle = 'rgba(160,210,255,0.5)'; ctx.lineWidth = 0.5;
    for (let i = 1; i < 4; i++) { const gx = sx * 27 + (sx < 0 ? -13 : 0) + i * 3.25; ctx.beginPath(); ctx.moveTo(gx, -9); ctx.lineTo(gx, 9); ctx.stroke(); }
    ctx.beginPath(); ctx.moveTo(sx * 27 - (sx < 0 ? 13 : 0), 0); ctx.lineTo(sx * 27 + (sx < 0 ? 0 : 13), 0); ctx.stroke();
  }
  ctx.shadowBlur = 0; ctx.fillStyle = '#c4cfda'; ctx.fillRect(-6, -5, 12, 10);   // the hub
  ctx.fillStyle = '#9fb0c2'; ctx.beginPath(); ctx.arc(0, 0, 4.6, 0, 6.29); ctx.fill();
  ctx.strokeStyle = '#6a7d90'; ctx.lineWidth = 1; ctx.beginPath(); ctx.ellipse(0, 0, 11, 3.4, 0, 0, 6.29); ctx.stroke();      // habitat ring
  ctx.beginPath(); ctx.moveTo(0, -5); ctx.lineTo(0, -12); ctx.stroke();                                                     // antenna
  const blink = Math.sin(t * 0.004) > 0.4;
  ctx.fillStyle = blink ? '#ff4455' : 'rgba(255,68,85,0.25)'; ctx.beginPath(); ctx.arc(0, -12, 1.3, 0, 6.29); ctx.fill();
  ctx.fillStyle = blink ? 'rgba(80,255,150,0.25)' : '#50ff96'; ctx.beginPath(); ctx.arc(-26, 0, 1.2, 0, 6.29); ctx.fill();
  ctx.restore();
}

function drawSky(t) {
  const dt = lastSkyT ? Math.min(100, t - lastSkyT) : 16; lastSkyT = t;
  if (sky) ctx.drawImage(sky, 0, 0, W, H);
  for (const s of stars) {
    const tw = 0.35 + 0.65 * Math.abs(Math.sin(t * 0.0006 * (0.4 + s.z) + s.p));
    ctx.fillStyle = 'rgba(170,225,255,' + (0.12 + 0.5 * s.z) * tw + ')';
    const x = (s.x + t * 0.003 * s.z) % W; const sz = s.z > 0.9 ? 1.8 : 1;
    ctx.fillRect(x, s.y, sz, sz);
  }
  drawMiniStation(t, dt);
  drawRocks(t);
  drawMeteors(t);
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
  const m = fillBay({ key: p.key, label: p.label, brand: p.color, color: p.color, platform: true });
  const st = m.state;
  return Object.assign(m, { powered: on, connState: on ? 'ONLINE' : built ? 'OFFLINE' : 'COMING SOON',
           state: m.tasks.length ? st : on ? 'ONLINE' : built ? 'OFFLINE' : 'COMING SOON', idleText: on ? 'CONNECTED' : built ? 'TAP TO CONNECT' : 'UNDER CONSTRUCTION' });
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
  return prods().map(f => fillBay(Object.assign({}, f))).concat([coreBay()], newKeys().map(newBay));
}

// the ring turns slowly all the time, as if it floated in space; a click on Helix, a mission starting or one finishing speeds it up for
// a couple of turns, then it eases back down to the slow turn (no jump: the extra turns are folded into the slow angle when they end)
const DRIFT = { angle: 0, rate: 2 * Math.PI / 150, last: 0 };     // one slow turn in 150 seconds
const SPIN = { on: false, t0: 0, dur: 3400, turns: 2, cur: 0 };
let reducedMotion = false;
try { reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}
function poseMods(extra) {
  mods.forEach(m => {
    const a = (m.a0 === undefined ? m.a : m.a0) + extra;
    Object.assign(m, { a, ux: Math.cos(a), uy: Math.sin(a), cx: core.x + Math.cos(a) * m.D, cy: core.y + Math.sin(a) * m.D, rot: a + Math.PI / 2 });
  });
}
function startSpin() {
  if (SPIN.on || !mods.length || reducedMotion) return;
  SPIN.on = true; SPIN.t0 = performance.now(); SPIN.cur = 0;
}
function stepMotion(t) {
  const dt = DRIFT.last ? Math.min(0.1, Math.max(0, (t - DRIFT.last) / 1000)) : 0; DRIFT.last = t;
  if (!reducedMotion) DRIFT.angle = (DRIFT.angle + DRIFT.rate * dt) % (2 * Math.PI);
  if (SPIN.on) {
    const p = (t - SPIN.t0) / SPIN.dur;
    if (p >= 1) { SPIN.on = false; SPIN.cur = 0; DRIFT.angle = (DRIFT.angle + SPIN.turns * 2 * Math.PI) % (2 * Math.PI); }
    else SPIN.cur = SPIN.turns * 2 * Math.PI * (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);   // slow start, fast middle, slow stop
  }
  poseMods(DRIFT.angle + SPIN.cur);
}
let missionSeen = null;                                   // mission id -> last status; null until the first state arrives
function spinOnMissionChange(missions) {
  const now = new Map(missions.map(m => [m.id, m.status]));
  if (missionSeen) {
    for (const [id, st] of now) {
      const before = missionSeen.get(id);
      if ((before === undefined && (st === 'planning' || st === 'active')) || (before !== undefined && before !== 'done' && st === 'done')) { startSpin(); break; }
    }
  }
  missionSeen = now;
}

function layout() {
  const pl = prods(), nk = newKeys();
  // the ring grows: the 8 fixed slots, then one more branch for every business past the 4th and every extra mission factory
  const extras = pl.slice(4).map(b => fillBay(Object.assign({}, b)))
    .concat((pl[3] ? nk : nk.slice(1)).map(newBay)).slice(0, MAX_EXTRA_BRANCHES);
  const N = RING.length + extras.length;
  const [dw, dh] = designSize();                          // the ring's scale follows the browser window, never the panels you dragged
  // the optimal scale for this window, shrunk evenly (never stretched) only when the station's box is really too small to hold it
  SC = Math.max(0.34, Math.min(1.25, Math.min(dw / 1060, dh / 800), Math.min(W / 1060, H / 800)));
  if (N > 8) SC *= Math.sin(Math.PI / N) / Math.sin(Math.PI / 8);             // more branches, smaller modules, so the ring keeps its size
  core = { x: W / 2, y: H / 2, r: 94 * SC };
  const mw = 184 * SC, mh = 116 * SC, gap = 46 * SC;
  const D = Math.max(core.r + 52 * SC + mh / 2, (mw + gap) / (2 * Math.sin(Math.PI / N)));
  const prod = {};
  const open = (i) => ({ key: 'open' + i, label: 'EXPANSION BAY', color: '#ff4fb8', expansion: true, tasks: [], missions: [], state: 'OPEN',
                         idleText: isOwner() ? 'NEXT BUSINESS' : 'TAP TO ADD A BUSINESS' });
  for (let i = 0; i < 3; i++) prod['p' + i] = pl[i] ? fillBay(Object.assign({}, pl[i])) : open(i);
  const plat = {}; PLATFORMS.forEach(p => { plat[p.key] = platformModule(p); });
  const fresh = pl[3] ? fillBay(Object.assign({}, pl[3])) : nk.length ? newBay(nk[0]) : open(3);
  const slots = RING.map(k => k === 'new' ? fresh : (prod[k] || plat[k])).concat(extras);
  mods = slots.map((m, i) => {
    const a = -Math.PI / 2 + i * 2 * Math.PI / N;
    return Object.assign(m, { a, ux: Math.cos(a), uy: Math.sin(a), cx: core.x + Math.cos(a) * D, cy: core.y + Math.sin(a) * D, w: mw, h: mh, rot: a + Math.PI / 2, D });
  });
  mods.forEach(m => { m.a0 = m.a; });
  poseMods(DRIFT.angle + SPIN.cur);
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
  const live = m.tasks.some(k => k.status === 'running') || (m.platform && m.powered) || !!(site && site.live) || m.state === 'IDLE';
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
  const sel = selected === 'f:' + m.key;
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
    if (tk.status !== 'running' && tk.status !== 'queued') return;          // finished or failed: they head back to Helix's deck (the mission panel keeps the result)
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
  // the plate lies flat against the factory's outer end, centred on it, turned with the factory so the name always faces Helix, whatever angle the ring has turned to
  ctx.save(); ctx.translate(m.cx, m.cy); ctx.rotate(m.rot); ctx.translate(0, -m.h / 2 - 5 * SC - ph / 2);
  const ox = 0, x = -pw / 2, y = -ph / 2;
  ctx.fillStyle = 'rgba(6,11,18,0.94)'; rr(x, y, pw, ph, 3); ctx.fill();
  ctx.strokeStyle = alpha(m.color, 0.55); ctx.lineWidth = 1; rr(x, y, pw, ph, 3); ctx.stroke();
  ctx.fillStyle = m.color; ctx.fillRect(x, y + 3, 2, ph - 6);
  ctx.textAlign = 'center';
  ctx.font = '700 ' + fs + 'px Orbitron, monospace'; ctx.fillStyle = m.color; ctx.fillText(name, ox, y + 5 * SC + fs);
  ctx.font = fs2 + 'px Share Tech Mono, monospace';
  ctx.fillStyle = st === 'DOWN' ? '#ff3344' : st === 'NEEDS YOU' || st === 'OFFLINE' || st === 'IDLE' ? '#ff8a2a' : st === 'WORKING' || st === 'ONLINE' || st === 'LIVE' ? '#00ff99' : 'rgba(142,207,224,0.65)';
  ctx.fillText(st, ox, y + 7 * SC + fs + fs2);
  ctx.restore();
}

function drawCore(t) {
  const { x, y, r } = core, open = S ? S.questions.length : 0;
  if (window._helixTalking) {                                         // Helix is speaking: her core pulses
    const pulse = 0.35 + 0.3 * Math.abs(Math.sin(t * 0.012));
    ctx.save(); ctx.strokeStyle = 'rgba(255,138,42,' + pulse + ')'; ctx.lineWidth = 3 * SC + 1; ctx.shadowColor = '#ff8a2a'; ctx.shadowBlur = 18;
    ctx.beginPath(); ctx.arc(x, y, r + 5 * SC, 0, 6.29); ctx.stroke(); ctx.restore();
  }
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
  // one figure per crew member (the 14 agents, never one per task): working at the core if they have a running task here, else idle on the deck;
  // an agent busy in a factory is in that factory
  const onDeck = [], coreRunning = new Set(core.bay ? core.bay.tasks.filter(k => k.status === 'running').map(k => k.role) : []);
  (S && S.roster ? S.roster : []).filter(a => a.role !== 'lead').forEach(a => {
    if (coreRunning.has(a.role)) onDeck.push({ role: a.role, status: 'running' });
    else if (!a.working && !a.waiting) onDeck.push({ role: a.role, status: 'idle' });
  });
  // each figure has its own pace and mind: it wanders around the deck, turns back, pauses, and drifts between the inner and outer lane
  const hash = (str) => { let h = 7; for (let c = 0; c < str.length; c++) h = (h * 31 + str.charCodeAt(c)) % 100003; return h; };
  if (HELIX_ON_DECK && (ART.helix || ART.helixViews)) onDeck.push({ role: 'lead', status: 'idle' });                                           // Helix herself, walking among her crew
  onDeck.forEach((a, i) => {
    const h = hash(a.role), R = (k) => { const v = Math.sin(h * 12.9898 + k * 78.233) * 43758.5453; return v - Math.floor(v); };
    const dir = R(1) < 0.5 ? -1 : 1, a0 = -Math.PI / 2 + i * (Math.PI * 2 / Math.max(onDeck.length, 1)) + R(2) * 0.5;
    const v = 0.00001 + R(3) * 0.00003, A = 0.3 + R(4) * 0.4, w = 0.00008 + R(5) * 0.00014, ph = R(6) * 6.28;       // steady walk plus a back-and-forth swing
    const lane = ring * (0.1 + R(7) * 0.2), w2 = 0.0001 + R(8) * 0.0003, ph2 = R(9) * 6.28;
    const at = (tt) => { const ang = a0 + dir * (v * tt + A * Math.sin(w * tt + ph)), rr = track + lane * Math.sin(w2 * tt + ph2); return [x + Math.cos(ang) * rr, y + Math.sin(ang) * rr + 4 * sc]; };
    const [px, py] = at(t), [qx, qy] = at(t + 40), step = Math.hypot(qx - px, qy - py) / 40;
    const moving = step > 0.0012;                                                                            // slowed to a stop at the turn-around points: stands still
    const face = moving ? dirOf(qx - px, qy - py, 'front') : 'front';
    if (a.role === 'lead' && !ART.helix && ART.helixViews) {
      const d = BOT_DIRS[face] != null ? BOT_DIRS[face] : 0, cell = ART.helixViews.naturalHeight, ph = t * 0.008 + i, Hh = 62;
      const bob = moving ? Math.abs(Math.sin(ph)) * 2.4 : Math.sin(t * 0.002 + i) * 0.6, lean = moving ? Math.sin(ph) * 0.07 : 0;       // a walk made in code from still pictures
      ctx.save(); ctx.imageSmoothingQuality = 'high'; ctx.translate(px, py + 10.5); ctx.rotate(lean);
      const aura = ctx.createRadialGradient(0, -Hh * 0.5, 2, 0, -Hh * 0.5, Hh * 0.75);                  // she is the lead: a soft glow so she stands out on the dark deck
      aura.addColorStop(0, 'rgba(0,229,255,0.38)'); aura.addColorStop(1, 'rgba(0,229,255,0)');
      ctx.fillStyle = aura; ctx.fillRect(-Hh, -Hh * 1.4 - bob, Hh * 2, Hh * 1.8);
      ctx.drawImage(ART.helixViews, d * cell, 0, cell, cell, -Hh / 2, -Hh * 0.95 - bob, Hh, Hh); ctx.restore();
      return;
    }
    if (a.role === 'lead' && ART.helix) {
      const d = BOT_DIRS[face] != null ? BOT_DIRS[face] : 0, col = moving ? 1 + d * 4 + Math.floor(t * 0.006 + i) % 4 : 0, Hh = 44;
      ctx.save(); ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(ART.helix, col * BOT_CELL, 0, BOT_CELL, BOT_CELL, px - Hh / 2, py + 10.5 - Hh * 0.873, Hh, Hh); ctx.restore();
      return;
    }
    drawBot(px, py, ROLE_COLORS[a.role] || '#00e5ff', a.status, t, i * 4.1, face, sc, moving);
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
  paint(t);
}
function paint(t) {                                       // one whole picture of the station, never a partial one
  const dt = Math.min(100, t - (lastT || t)); lastT = t; lastDraw = t;
  ctx.clearRect(0, 0, W, H);
  drawSky(t);
  stepMotion(t);
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
  return m.expansion ? (isOwner() ? 'hub' : 'setup') : 'f:' + m.key;
}
cv.addEventListener('click', e => {
  const r = cv.getBoundingClientRect(), h = hit(e.clientX - r.left, e.clientY - r.top);
  if (Math.hypot(e.clientX - r.left - core.x, e.clientY - r.top - core.y) <= core.r + 8) startSpin();      // click Helix: the whole ring spins around him
  if (h) {
    selected = h; renderSide(true);
    // on a phone the panel sits below the station and the chat: bring it into view
    if (window.matchMedia('(max-width: 820px)').matches) document.getElementById('right').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
});
cv.addEventListener('mousemove', e => { const r = cv.getBoundingClientRect(); cv.style.cursor = hit(e.clientX - r.left, e.clientY - r.top) ? 'pointer' : 'default'; });

// ── one chat with Helix: the same conversation as the main screen ────────────────────────────────
let chatSig = '', chatBusy = false, chatTick = 0;
const LIVE = new Map();        // message text -> {img, openId}: things the history does not store, kept while the page is open
function chatMsg(role, text) {
  const d = mk('div', 'cm ' + (role === 'user' ? 'u' : 'a'));
  d.append(mk('div', 'tg', role === 'user' ? 'YOU' : (String(text).startsWith('CREW ·') ? 'CREW' : 'HELIX')), mk('div', null, text));
  const extra = LIVE.get(role + '|' + text);
  if (extra && extra.imgs) extra.imgs.forEach(u => { const im = mk('img', 'th'); im.src = u; im.alt = 'attached image'; d.append(im); });
  if (extra && extra.openId) { const b = mk('button', 'lib-f', 'Open'); b.style.marginTop = '6px'; b.onclick = () => openMedia(extra.openId); d.append(b); }
  if (role !== 'user' && String(text).length > 2 && text !== '…') d.append(listenButton(text));       // every reply of hers can be read aloud again
  return d;
}
let _listenBtn = null;
function listenButton(text) {
  const row = mk('div'); row.style.marginTop = '6px';
  const b = mk('button', 'lib-f', '🔊 Listen');
  b.onclick = () => {
    if (_listenBtn === b && _curAudio) { stopSpeech(); b.textContent = '🔊 Listen'; _listenBtn = null; return; }          // a second tap stops it
    if (_listenBtn && _listenBtn !== b) _listenBtn.textContent = '🔊 Listen';
    _listenBtn = b; b.textContent = '⏹ Stop';
    speakReply(String(text), row, true).finally(() => {
      const t = setInterval(() => { if (_listenBtn !== b) { clearInterval(t); } else if (!_curAudio) { clearInterval(t); b.textContent = '🔊 Listen'; _listenBtn = null; } }, 500);     // back to Listen when she finishes
    });
  };
  row.append(b);
  return row;
}
function lastHelixText() {
  const msgs = [...document.querySelectorAll('#chat-log .cm.a')].filter(x => x.firstChild && x.firstChild.textContent === 'HELIX' && x.children[1]);
  return msgs.length ? msgs[msgs.length - 1].children[1].textContent : '';
}
// briefs from the feed show in the chat with a Listen button (what Herald and the old brief tasks post)
const BRIEFS = [];
const BRIEF_SEEN_KEY = 'aibussol_seen_briefs';
function seenBriefs() { try { return JSON.parse(localStorage.getItem(BRIEF_SEEN_KEY) || '[]'); } catch (e) { return []; } }
function markBriefSeen(id) { try { const s2 = seenBriefs(); if (!s2.includes(id)) { s2.push(id); localStorage.setItem(BRIEF_SEEN_KEY, JSON.stringify(s2.slice(-60))); } } catch (e) {} }
async function checkBriefs() {
  const r = await api('/api/briefs/latest').catch(() => null);
  if (!r || !r.ok) return;
  const FRESH_MS = 18 * 3600 * 1000;                                        // a brief older than 18 hours is history, not news: a new browser does not replay the backlog
  const fresh = (r.data.items || []).filter(b => !seenBriefs().includes(b.id) && (Date.now() - new Date(b.created_at).getTime()) < FRESH_MS).reverse();
  (r.data.items || []).forEach(b => markBriefSeen(b.id));                  // everything the feed holds counts as seen, old or new
  fresh.slice(-2).forEach(b => BRIEFS.push(b));                          // a long backlog (a new browser has seen none) shows only the 2 newest, not all of them
  if (fresh.length) loadChat(true);
}
function briefCard(b) {
  const d = mk('div', 'cm a');
  d.append(mk('div', 'tg', 'HELIX · ' + String(b.period).toUpperCase() + ' BRIEF · ' + new Date(b.created_at).toLocaleString()));
  const body = mk('div', null, String(b.body || '')); body.style.whiteSpace = 'pre-wrap'; d.append(body);
  const row = mk('div'); row.style.marginTop = '6px';
  const listen = mk('button', 'lib-f', '🔊 Listen'), note = mk('span', 'dim'); note.style.marginLeft = '8px';
  listen.onclick = async () => {
    listen.disabled = true;
    const r = await api('/api/briefs/' + encodeURIComponent(b.id) + '/listen', 'POST').catch(() => null);
    if (!r || !r.ok || !r.data.text) note.textContent = (r && r.data && r.data.detail) || 'It could not be played right now.';
    else { note.textContent = r.data.remaining + ' listens left today'; speakReply(r.data.text, d, true); }
    listen.disabled = false;
  };
  row.append(listen, note); d.append(row);
  return d;
}

// up to 5 images and documents (Word .docx, text) attached to a message: images are looked at, documents are read by Helix and passed to the crew
let pending = [];
const DOC_EXT = /\.(docx|txt|md|markdown|csv|tsv|json|log|rtf)$/i, MAX_ATTACH = 5, MAX_DOC_BYTES = 3 * 1024 * 1024, MAX_IMG_BYTES = 5 * 1024 * 1024, MAX_ALL_BYTES = 12 * 1024 * 1024;
function attachNote(text) { const log = document.getElementById('chat-log'); log.append(chatMsg('assistant', text)); log.scrollTop = log.scrollHeight; }
function paintAttach() {
  const bar = document.getElementById('img-bar'); bar.textContent = ''; bar.hidden = !pending.length;
  pending.forEach((p, i) => {
    const c = mk('span', 'chip');
    if (p.doc) c.append(mk('span', null, '📄')); else { const im = mk('img'); im.src = p.url; im.alt = ''; c.append(im); }
    c.append(mk('span', null, p.name.slice(0, 24)));
    const x = mk('button', 'xbtn', '✕'); x.title = 'Remove ' + p.name; x.onclick = () => { pending.splice(i, 1); paintAttach(); }; c.append(x); bar.append(c);
  });
}
document.getElementById('img-in').addEventListener('change', async () => {
  const inp = document.getElementById('img-in'), files = Array.from(inp.files || []); inp.value = '';
  for (const f of files) {
    const img = String(f.type).startsWith('image/'), doc = !img && DOC_EXT.test(f.name);
    if (pending.length >= MAX_ATTACH) { attachNote('Up to ' + MAX_ATTACH + ' files at a time. Send this message first, then attach more.'); break; }
    if (!img && !doc) { attachNote(/\.doc$/i.test(f.name) ? f.name + ': old .doc files cannot be read. Save it as .docx or .txt and attach that.' : f.name + ': attach images, Word (.docx) or text documents (.txt, .md, .csv, .json).'); continue; }
    if (f.size > (doc ? MAX_DOC_BYTES : MAX_IMG_BYTES)) { attachNote(f.name + ' is too large (limit ' + (doc ? 3 : 5) + ' MB).'); continue; }
    if (pending.reduce((n, p) => n + p.size, 0) + f.size > MAX_ALL_BYTES) { attachNote('Those files are too large together (limit 12 MB).'); continue; }
    const url = await new Promise(res => { const rd = new FileReader(); rd.onload = e => res(String(e.target.result)); rd.onerror = () => res(''); rd.readAsDataURL(f); });
    if (url) pending.push({ url, b64: url.split(',')[1], name: f.name, size: f.size, doc, mime: img ? f.type : '' });
  }
  paintAttach();
});
function clearImg() { pending = []; paintAttach(); document.getElementById('img-in').value = ''; }

// ── voice: "Hey Helix" wake word, the mic button, and Helix reading her replies aloud ──
let voiceOn = true;
try { voiceOn = localStorage.getItem('aibussol_voice') !== 'off'; } catch (e) {}
function paintVoice() { const b = document.getElementById('voice-tog'); b.textContent = voiceOn ? '🔊 ON' : '🔇 OFF'; b.classList.toggle('on', voiceOn); }
let _curAudio = null, _speakGen = 0;
function stopSpeech() {
  _speakGen++;
  if (_curAudio) { const a = _curAudio; _curAudio = null; try { a.pause(); } catch (e) {} window._helixTalking = false; wakeLabel(wakeOn ? 'SAY "HEY HELIX"' : 'VOICE: TAP TO WAKE', wakeOn ? 'on' : ''); if (!isRec) resumeWake(); }
}
document.getElementById('voice-tog').onclick = () => { voiceOn = !voiceOn; if (!voiceOn) stopSpeech(); try { localStorage.setItem('aibussol_voice', voiceOn ? 'on' : 'off'); } catch (e) {} paintVoice(); };
paintVoice();
const SPEEDS = [0.75, 0.9, 1, 1.15, 1.3, 1.5, 1.75, 2];
let voiceSpeed = 1;
try { const v = parseFloat(localStorage.getItem('aibussol_voice_speed')); if (SPEEDS.includes(v)) voiceSpeed = v; } catch (e) {}
function applySpeed(a) { if (!a) return; try { a.preservesPitch = true; a.mozPreservesPitch = true; a.webkitPreservesPitch = true; a.playbackRate = voiceSpeed; } catch (e) {} }
function paintSpeed() { const b = document.getElementById('speed-tog'); b.textContent = voiceSpeed + '×'; b.classList.toggle('on', voiceSpeed !== 1); }
document.getElementById('speed-tog').onclick = () => {
  voiceSpeed = SPEEDS[(SPEEDS.indexOf(voiceSpeed) + 1) % SPEEDS.length];
  try { localStorage.setItem('aibussol_voice_speed', String(voiceSpeed)); } catch (e) {}
  applySpeed(_curAudio); paintSpeed();                         // takes effect on what she is saying right now
};
paintSpeed();
let _voiceNote = false, _wakeErrors = 0;
function voiceNote(text) {                                      // a voice problem is shown for a few seconds instead of failing silently
  _voiceNote = true; wakeLabel(text, 'busy');
  setTimeout(() => { _voiceNote = false; wakeLabel(wakeOn ? 'SAY "HEY HELIX"' : 'VOICE: TAP TO WAKE', wakeOn ? 'on' : ''); }, 6000);
}
function wakeLabel(text, cls) { const w = document.getElementById('wake'); w.textContent = text; w.className = cls || ''; }
let _audioUnlocked = false;
function unlockAudio() { if (_audioUnlocked) return; _audioUnlocked = true; try { const c = new (window.AudioContext || window.webkitAudioContext)(); c.resume().then(() => c.close()).catch(() => {}); } catch (e) {} }
const SPEECH_CHUNK = 1400;
function speechChunks(text) {
  const sentences = (String(text).match(/[^.!?\n]+[.!?]*|\n+/g) || []).map(x => x.trim()).filter(Boolean);
  const out = []; let cur = '';
  for (let snip of sentences) {
    while (snip.length > SPEECH_CHUNK) { if (cur) { out.push(cur); cur = ''; } out.push(snip.slice(0, SPEECH_CHUNK)); snip = snip.slice(SPEECH_CHUNK); }
    if (cur && (cur + ' ' + snip).length > SPEECH_CHUNK) { out.push(cur); cur = snip; } else cur = cur ? cur + ' ' + snip : snip;
  }
  if (cur) out.push(cur);
  return out.length ? out : [''];
}
// markdown symbols are read aloud as words, so strip them: **bold**, # headers, bullets, numbered lists, code ticks
function cleanForSpeech(text) {
  return String(text)
    .replace(/```[\s\S]*?```/g, ' ')
    // money read the way people say it: $2.00 is "2 dollars", $2.19 is "2 dollars and 19 cents"
    .replace(/\$(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?/g, (m, d, c) => {
      const dol = parseInt(d.replace(/,/g, ''), 10), dw = dol + (dol === 1 ? ' dollar' : ' dollars');
      if (!c || parseInt(c, 10) === 0) return dw;
      const cents = parseInt(c.length === 1 ? c + '0' : c, 10), cw = cents + (cents === 1 ? ' cent' : ' cents');
      return dol === 0 ? cw : dw + ' and ' + cw;
    })
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm, '')
    .replace(/\*\*|__|[*_`#>|]/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim();
}
async function speakReply(text, el, always) {
  if (!voiceOn && !always) return;
  stopSpeech();                       // one voice at a time: a new reply replaces the one still playing
  const gen = _speakGen;
  try {
    const parts = [];
    for (const chunk of speechChunks(cleanForSpeech(text))) {
      const res = await fetch('/api/voice/speak', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token(), 'Content-Type': 'application/json' }, body: JSON.stringify({ message: chunk }) });
      if (gen !== _speakGen) return;
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        if (el) el.append(mk('div', 'vn', 'Voice unavailable: ' + (typeof d.detail === 'string' ? d.detail : 'HTTP ' + res.status)));
        resumeWake(); return;
      }
      parts.push(await res.arrayBuffer());
      if (gen !== _speakGen) return;
    }
    const total = parts.reduce((n, p2) => n + p2.byteLength, 0), joined = new Uint8Array(total); let off = 0;
    for (const p2 of parts) { joined.set(new Uint8Array(p2), off); off += p2.byteLength; }
    const url = URL.createObjectURL(new Blob([joined.buffer], { type: 'audio/mpeg' })), audio = new Audio(url);
    _curAudio = audio;
    applySpeed(audio);
    const done = () => { if (_curAudio === audio) { _curAudio = null; window._helixTalking = false; wakeLabel(wakeOn ? 'SAY "HEY HELIX"' : 'VOICE: TAP TO WAKE', wakeOn ? 'on' : ''); if (!isRec) resumeWake(); } URL.revokeObjectURL(url); };
    audio.onplay = () => { window._helixTalking = true; wakeLabel('HELIX IS SPEAKING', 'busy'); };
    audio.onended = done; audio.onerror = done;
    pauseWake();
    audio.play().catch(done);
  } catch (e) { resumeWake(); }
}
let isRec = false, recorder = null, chunks = [], SR = null, wakeOn = false, srRunning = false, srMode = 'wake';
const WAKE_RE = /\b(?:he?y|hi|ok|a)\s*h[aeiou]{0,2}l+[aeiou]?[xcks]s?\b/;
const norm = t => t.toLowerCase().replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim();
setInterval(() => { if (wakeOn && SR && !srRunning && !isRec) { try { SR.start(); } catch (e) {} } }, 2000);
function initWake() {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) { wakeLabel('VOICE: USE THE MIC'); return; }
  SR = new Rec(); SR.continuous = true; SR.interimResults = true; SR.lang = 'en-US';
  wakeOn = true; srMode = 'wake';
  const me = SR; let fired = false;
  SR.onstart = () => { srRunning = true; if (_wakeErrors === 0 && !isRec) wakeLabel('SAY "HEY HELIX"', 'on'); };
  SR.onresult = (e) => {
    if (_wakeErrors) { _wakeErrors = 0; if (!isRec && !_voiceNote) wakeLabel('SAY "HEY HELIX"', 'on'); }          // the service answered again
    let heard = ''; for (let i = e.resultIndex; i < e.results.length; i++) heard += ' ' + e.results[i][0].transcript;
    const t = norm(heard);
    if (pendingVoice && Date.now() < pendingVoice.until && /\b(approve|decline)\b/.test(t)) {
      const pv = pendingVoice, yes = /\bapprove\b/.test(t);
      const btn = document.querySelector('[data-qid="' + pv.id + '"] .b.' + (yes ? 'ok' : 'no'));
      if (btn && !btn.disabled) { pendingVoice = null; btn.click(); wakeLabel(yes ? 'APPROVED BY VOICE' : 'DECLINED BY VOICE', 'on'); return; }
    }
    if (srMode === 'wake') { if (!fired && !isRec && WAKE_RE.test(t)) { fired = true; setTimeout(() => { fired = false; }, 3000); startRec(); } }
    else if (srMode === 'send' && isRec && /\bsend\b/.test(t)) stopRec();
  };
  SR.onend = () => { srRunning = false; if (wakeOn && SR === me) { const again = () => { if (wakeOn && SR === me && !srRunning) { try { SR.start(); } catch (e) { setTimeout(again, 400); } } }; again(); } };
  SR.onerror = (e) => {
    if (e.error === 'not-allowed') { wakeOn = false; SR = null; wakeLabel('MIC BLOCKED'); }
    else if (['network', 'service-not-allowed', 'audio-capture', 'language-not-supported'].includes(e.error) && !isRec && !_voiceNote) {
      wakeLabel('WAKE WORD OFFLINE (' + e.error.toUpperCase() + ') · TAP THE MIC', 'busy');       // the browser's own speech service is not answering; the mic button still works
      _wakeErrors = (_wakeErrors || 0) + 1;
    }
  };
  try { SR.start(); wakeLabel('SAY "HEY HELIX"', 'on'); } catch (e) { wakeLabel('VOICE: USE THE MIC'); }
}
function pauseWake() { wakeOn = false; if (SR) { try { SR.abort(); } catch (e) {} SR = null; } }
function resumeWake() { if (!wakeOn && _wakeStarted) initWake(); }
async function startRec() {
  if (isRec) return;
  try {
    srMode = 'send';
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = []; recorder = new MediaRecorder(stream);
    recorder.ondataavailable = e => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop()); isRec = false; srMode = 'wake';
      document.getElementById('mic').classList.remove('rec'); wakeLabel('LISTENING TO YOU…', 'busy');
      const fd = new FormData(); fd.append('audio', new Blob(chunks, { type: 'audio/webm' }), 'recording.webm');
      try {
        const res = await fetch('/api/voice/transcribe', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token() }, body: fd });
        const d = await res.json().catch(() => ({}));
        const clean = String(d.text || '').trim().replace(/^(hey\s+helix|hey\s+helics|hey\s+helic|a\s+helix)[,.]?\s*/i, '').replace(/[,.]?\s*send\.?\s*$/i, '').trim();
        if (clean) { document.getElementById('chat-in').value = clean; await sendChat(); }
        else voiceNote(res.ok ? "I DIDN'T CATCH THAT, TRY AGAIN" : 'VOICE ERROR: ' + String(d.detail || res.status).slice(0, 90));
      } catch (e) { voiceNote('VOICE ERROR: COULD NOT REACH THE SERVER'); }
      if (!_voiceNote) wakeLabel(wakeOn ? 'SAY "HEY HELIX"' : 'VOICE: TAP TO WAKE', wakeOn ? 'on' : '');
    };
    recorder.start(); isRec = true;
    document.getElementById('mic').classList.add('rec'); wakeLabel('RECORDING · SAY "SEND" OR TAP 🎙', 'rec');
  } catch (e) { srMode = 'wake'; wakeLabel('MIC BLOCKED'); }
}
function stopRec() { if (isRec && recorder) recorder.stop(); }
document.getElementById('mic').onclick = () => { unlockAudio(); startWake(); isRec ? stopRec() : startRec(); };
let _wakeStarted = false;
function startWake() { if (_wakeStarted || !token()) return; _wakeStarted = true; initWake(); }
document.addEventListener('click', () => { unlockAudio(); startWake(); });
document.addEventListener('keydown', () => { unlockAudio(); startWake(); });
async function loadChat(force) {
  if (chatBusy && !force) return;
  const r = await api('/api/session/history').catch(() => null);
  if (!r || !r.ok) return;
  const msgs = (r.data.messages || []).slice(-30);
  const sig = msgs.length + '|' + BRIEFS.length + '|' + (msgs.length ? String(msgs[msgs.length - 1].content).slice(-80) : '');
  if (sig === chatSig && !force) return;
  chatSig = sig;
  const log = document.getElementById('chat-log');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  log.textContent = '';
  if (!msgs.length && !BRIEFS.length) log.append(mk('div', 'dim', 'Talk to Helix here, type or say "Hey Helix". Say "build me…" to start a mission.'));
  BRIEFS.slice(-2).forEach(b => log.append(briefCard(b)));                // new briefs sit above the conversation (the 2 newest), so the latest replies are what you see at the bottom
  msgs.forEach(m => log.append(chatMsg(m.role, m.content)));
  if (atBottom || force) log.scrollTop = log.scrollHeight;
}
async function sendChat() {
  const inp = document.getElementById('chat-in');
  const text = inp.value.trim() || (pending.length ? 'Please look at the attached ' + (pending.length > 1 ? 'files.' : pending[0].doc ? 'document.' : 'image.') : '');
  if (!text || chatBusy) return;
  if (/^(?:hey helix[,.]?\s*)?(?:please\s+)?(?:re-?read|read (?:that|it|the last (?:message|reply|one)|(?:that|it) (?:again|back))|say (?:that|it) again|repeat (?:that|it|the last (?:message|reply|one)))\b/i.test(text)) {
    const last = lastHelixText();                                                      // "read that again": spoken from the chat itself, no model call
    inp.value = '';
    if (last) { speakReply(last, null, true); return; }
  }
  chatBusy = true; inp.value = '';
  const log = document.getElementById('chat-log');
  const media = text.match(/^\/(image|sound)\s+([\s\S]+)/i);           // "/image a red fox", "/sound hello there"
  if (media) {
    log.append(chatMsg('user', text)); const wait = chatMsg('assistant', media[1].toLowerCase() === 'image' ? 'Making the image…' : 'Making the sound…'); log.append(wait); log.scrollTop = log.scrollHeight;
    const kind = media[1].toLowerCase(), r = await api('/api/media/' + kind, 'POST', kind === 'image' ? { prompt: media[2].trim() } : { text: media[2].trim() }).catch(() => null);
    if (r && r.ok && r.data.item) { wait.lastChild.textContent = 'Saved: ' + r.data.item.title; const b = mk('button', 'lib-f', 'Open'); b.style.marginTop = '6px'; b.onclick = () => openMedia(r.data.item.id); wait.append(b); loadLibrary(); }
    else wait.lastChild.textContent = (r && typeof r.data.detail === 'string') ? r.data.detail : 'That could not be made right now.';
    chatBusy = false; return;
  }
  const files = pending; clearImg();
  const shownText = text + files.filter(f => f.doc).map(f => '\n📄 ' + f.name).join('');
  if (files.some(f => !f.doc)) LIVE.set('user|' + shownText, { imgs: files.filter(f => !f.doc).map(f => f.url) });
  log.append(chatMsg('user', shownText)); const wait = chatMsg('assistant', '…'); log.append(wait); log.scrollTop = log.scrollHeight;
  const body = { message: text, client_date: new Date().toLocaleString('en-US', { timeZoneName: 'short' }) };
  if (files.length) body.attachments = files.map(f => ({ name: f.name, data: f.b64, mime: f.mime }));
  const r = await api('/api/chat', 'POST', body).catch(() => null);
  const reply = r ? (r.data.reply || r.data.detail || 'No answer.') : 'No connection.';
  wait.lastChild.textContent = typeof reply === 'string' ? reply : 'No answer.';
  log.scrollTop = log.scrollHeight;
  chatBusy = false;
  const acts = (r && r.data.ui_actions) || [];
  acts.forEach(a => {
    if (a.type === 'saved' && a.item) { LIVE.set('assistant|' + reply, { openId: a.item.id }); loadLibrary(); }
    else if (a.type === 'open_item' && a.id) openMedia(a.id);
  });
  if (acts.some(a => a.type === 'crew_refresh')) setTimeout(refresh, 1200);
  if (r && r.ok && typeof reply === 'string') speakReply(reply, wait);
  setTimeout(() => loadChat(true), 500);
}
document.getElementById('chat-send').onclick = sendChat;
document.getElementById('chat-in').addEventListener('keydown', e => { if (e.key === 'Enter') { unlockAudio(); sendChat(); } });
// the browser sometimes fills the message box with a saved sign-in email after load: clear it unless Michael typed it
(() => {
  const ci = document.getElementById('chat-in'); let typed = false;
  // autofill also fires input events, so count only real key presses and pastes as typing
  ci.addEventListener('keydown', e => { if (e.key === 'Enter') typed = false; else if (e.key && e.key.length === 1) typed = true; });
  ci.addEventListener('paste', () => { typed = true; });
  const clearEmail = () => { if (!typed && /@/.test(ci.value)) ci.value = ''; };
  ci.addEventListener('input', clearEmail);
  ci.addEventListener('focus', clearEmail);
  clearEmail(); setInterval(clearEmail, 400);            // keeps running: autofill can land long after load
  window.addEventListener('pageshow', clearEmail);
})();

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
// users line and the daily traffic chart (crew/product_stats.py)
function usersLine(k) {
  const p = FIN && FIN.product_stats ? FIN.product_stats[k] : null;
  if (!p) return 'loading…';
  if (!p.connected) return p.note || 'not connected';
  const bits = [];
  if (p.users_total != null) bits.push(p.users_total + ' users');
  if (p.active_24h != null) bits.push(p.active_24h + ' active today');
  if (p.paying != null) bits.push(p.paying + ' paying');
  if (p.new_24h != null) bits.push(p.new_24h + ' new today');
  if (p.new_7d != null) bits.push(p.new_7d + ' new this week');
  const x = p.extra || {};
  if (x.posts_published_7d != null) bits.push(x.posts_published_7d + ' posts published this week', x.posts_failed_7d + ' failed', x.posts_queued + ' queued');
  return bits.join(' · ') || 'no user table found';
}
const chartRange = {};
function trafficChart(k) {
  const p = FIN.product_stats[k], wrap = mk('div');
  if (!p.connected || !p.traffic) { wrap.append(mk('div', 'dim', p.connected ? 'No daily data to chart yet.' : '')); return wrap; }
  const tr = p.traffic, days = chartRange[k] || 30;
  const head = mk('div', 'lbl'); head.style.marginTop = '12px';
  head.append(mk('span', null, tr.kind === 'views' ? 'Daily traffic' : 'Sign-ups per day'));
  const sw = mk('span'); sw.style.cssText = 'display:flex;gap:4px';
  [7, 30, 90].forEach(n => { const b = mk('button', 'lib-f' + (n === days ? ' on' : ''), n + 'd'); b.onclick = (ev) => { ev.stopPropagation(); chartRange[k] = n; renderSide(true); }; sw.append(b); });
  head.append(sw); wrap.append(head);
  // fill every day in the range (days with nothing recorded are zero), last day = today so far
  const byDay = {}; tr.days.forEach(r => { byDay[r.d] = r; });
  const pts = [], today = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/Los_Angeles' }));
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today); d.setDate(d.getDate() - i);
    const key = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    const r = byDay[key] || {}; pts.push({ d: key, a: tr.kind === 'views' ? (r.views || 0) : (r.signups || 0), b: tr.kind === 'views' ? (r.visitors || 0) : null });
  }
  const series = tr.kind === 'views' ? [['Page views', '#4f8cff', 'a'], ['Unique visitors', '#ffc14d', 'b']] : [['Sign-ups', '#00e5ff', 'a']];
  const leg = mk('div'); leg.style.cssText = 'display:flex;gap:12px;font-size:10px;margin-bottom:4px';
  series.forEach(([n, c]) => { const l = mk('span', null, '● ' + n); l.style.color = c; leg.append(l); });
  wrap.append(leg);
  const cv = document.createElement('canvas'); cv.style.cssText = 'width:100%;height:150px;display:block';
  wrap.append(cv);
  requestAnimationFrame(() => {
    const W2 = cv.clientWidth || 300, H2 = 150, dpr = window.devicePixelRatio || 1;
    cv.width = W2 * dpr; cv.height = H2 * dpr; const c = cv.getContext('2d'); c.scale(dpr, dpr);
    const padL = 28, padB = 18, padT = 6, w = W2 - padL - 4, h = H2 - padB - padT;
    const max = Math.max(4, ...pts.map(p2 => Math.max(p2.a, p2.b || 0)));
    const step = Math.pow(10, Math.floor(Math.log10(max))), top = Math.ceil(max / step) * step;
    c.font = '9px Share Tech Mono, monospace'; c.fillStyle = 'rgba(142,207,224,0.55)'; c.strokeStyle = 'rgba(142,207,224,0.08)'; c.lineWidth = 1;
    for (let g = 0; g <= 4; g++) { const y = padT + h - h * g / 4; c.beginPath(); c.moveTo(padL, y); c.lineTo(padL + w, y); c.stroke(); c.textAlign = 'right'; c.fillText(String(Math.round(top * g / 4)), padL - 4, y + 3); }
    const X = i => padL + (pts.length < 2 ? w / 2 : w * i / (pts.length - 1)), Y = v => padT + h - h * v / top;
    c.textAlign = 'center';
    [0, Math.floor((pts.length - 1) / 2), pts.length - 1].forEach(i => { const p2 = pts[i]; c.textAlign = i === 0 ? 'left' : i === pts.length - 1 ? 'right' : 'center'; c.fillText(i === pts.length - 1 ? 'today' : p2.d.slice(5).replace('-', '/'), X(i), H2 - 4); });
    series.forEach(([, col, f]) => {
      c.strokeStyle = col; c.lineWidth = 1.6; c.beginPath(); pts.forEach((p2, i) => { const y = Y(p2[f] || 0); i ? c.lineTo(X(i), y) : c.moveTo(X(i), y); }); c.stroke();
      if (days <= 30) { c.fillStyle = col; pts.forEach((p2, i) => { c.beginPath(); c.arc(X(i), Y(p2[f] || 0), 1.8, 0, 6.29); c.fill(); }); }
    });
  });
  if (tr.kind === 'views') wrap.append(mk('div', 'pnl-note', 'Unique visitors are estimated per browser, the same way FlipDeck\'s own analytics page counts them.'));
  else wrap.append(mk('div', 'pnl-note', 'This product does not record page views, so the chart shows new sign-ups per day.'));
  return wrap;
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
  // the main factories always show (zeros until they have activity); missions (new: rows) and the crew total stay out
  const MAIN = ['flipdeck', 'marnyx', 'deadzone', 'aibussol', 'etsy', 'google_play', 'app_store'];
  const LABEL = { flipdeck: 'FlipDeck', marnyx: 'Marnyx', deadzone: 'Deadzone', aibussol: 'Aibussol', etsy: 'Etsy', google_play: 'Google Play', app_store: 'App Store' };
  // one row per main factory and per business that has a factory (a business you add gets its row here); mission factories stay out
  const keys = [...new Set([...MAIN, ...(S && S.businesses ? S.businesses.map(b => b.key).filter(Boolean) : [])])];
  const ZERO = { revenue_today: 0, cost_today: 0, revenue_30d: 0, cost_30d: 0, net_30d: 0, mrr: 0 };
  const names = {}; keys.forEach(k => { names[k] = LABEL[k] || bizName(k); });
  // only the main factories: one row per product or business. Missions (new: rows) and the crew total stay out; tap a factory for its missions.
  const rows = keys.map(k => [k, FIN.factories[k] || ZERO]).sort((a, b) => (b[1].revenue_30d - a[1].revenue_30d) || (b[1].cost_30d - a[1].cost_30d));
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
  return s;
}

function poll() {
  clearTimeout(pollTimer);
  if (!document.hidden && token()) { refresh(); if (chatTick++ % 3 === 0) loadChat(); if (finTick++ % 15 === 0) { loadFinance(); loadLibrary(); refreshSubscription(); checkBriefs(); } }
  pollTimer = setTimeout(poll, 4000);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
if (['etsy', 'google'].includes(new URLSearchParams(location.search).get('connected'))) { selected = 'links'; history.replaceState(null, '', '/station'); }
(function columnGrips() {
  const main = document.getElementById('main'); if (!main) return;
  const CLAMP = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const apply = (name, px) => main.style.setProperty(name, Math.round(px) + 'px');
  try { const l = Number(localStorage.getItem('colLW')), r = Number(localStorage.getItem('colRW'));
    if (l >= 150) apply('--lw', l); if (r >= 150) apply('--rw', r); } catch (e) {}
  const drag = (gripId, side) => {
    const grip = document.getElementById(gripId); if (!grip) return;
    grip.addEventListener('pointerdown', e => {
      const startX = e.clientX, startW = document.getElementById(side === 'left' ? 'left' : 'right').getBoundingClientRect().width;
      grip.setPointerCapture(e.pointerId);
      const move = ev => { const dx = ev.clientX - startX; const w = CLAMP(side === 'left' ? startW + dx : startW - dx, 150, window.innerWidth * 0.4);
        apply(side === 'left' ? '--lw' : '--rw', w); };
      const up = () => { grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up);
        try { const w = document.getElementById(side === 'left' ? 'left' : 'right').getBoundingClientRect().width;
          localStorage.setItem(side === 'left' ? 'colLW' : 'colRW', String(Math.round(w))); } catch (e2) {} };
      grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
    });
  };
  drag('left-grip', 'left'); drag('right-grip', 'right');
})();
(function chatGrip() {
  const grip = document.getElementById('chat-grip'), log = document.getElementById('chat-log'); if (!grip || !log) return;
  try { const saved = Number(localStorage.getItem('chatLogH')); if (saved >= 60) log.style.height = saved + 'px'; } catch (e) {}
  grip.addEventListener('pointerdown', e => {
    const startY = e.clientY, startH = log.getBoundingClientRect().height;
    grip.setPointerCapture(e.pointerId);
    const move = ev => { log.style.height = Math.round(Math.max(60, Math.min(window.innerHeight * 0.7, startH + (startY - ev.clientY)))) + 'px'; };
    const up = () => { grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up);
      try { localStorage.setItem('chatLogH', String(Math.round(log.getBoundingClientRect().height))); } catch (e2) {} };
    grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
  });
})();
resize(); requestAnimationFrame(frame);
if (token()) boot(); else showAuth();
poll();
</script>
</body>
</html>
"""
