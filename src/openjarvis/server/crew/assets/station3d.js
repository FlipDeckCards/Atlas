/* The Station in 3D: one big clear glass dome over the open factory trays, walkways at one walking level, Helix as a hologram in the middle,
   and a ring of booster engines. Loaded only when the VIEW button is switched to 3D. Needs three.js (window.THREE) loaded first. */
(function () {
  'use strict';
  const THREE = window.THREE;
  const RD = 660, HD = 380, FLOOR_Y = 10, R_IN = 306, R_OUT = 540, DW = 412, VC = DW / Math.cos(Math.PI / 8);
  let renderer = null, scene = null, camera = null, canvas = null, stage = null, opts = null, running = false, built = false, raf = 0, ro = null;
  let boostPivots = [], actions = null, launchS = null, boost = 1, trayAgents = [], rootG = null, hubCrew = [], crewSig = '', crewCheck = 0, winMesh = null, facGroup = null, sig = '', hitBoxes = [], selRing = null, walkers = [], holo = null, flames = [], lastInteract = 0;
  const cam = { az: 0.44, el: 0.42, dist: 1, zoom: 1, ly: -230 };
  const T0 = performance.now();
  const hash01 = (a, b) => { const v = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return v - Math.floor(v); };

  const tex = (w, h, fn, srgb) => { const c = document.createElement('canvas'); c.width = w; c.height = h; fn(c.getContext('2d'), w, h); const t = new THREE.CanvasTexture(c); if (srgb !== false) t.encoding = THREE.sRGBEncoding; t.anisotropy = 4; return t; };
  const polar = (a, r) => [Math.cos(a) * r, Math.sin(a) * r];
  const HULL = () => new THREE.MeshStandardMaterial({ color: 0xc4ccd6, metalness: 0.7, roughness: 0.36, envMapIntensity: 1.1 });
  let M = null;                                               // shared materials
  function mats() {
    return M || (M = {
      hull: HULL(), hullD: new THREE.MeshStandardMaterial({ color: 0x5e6a7a, metalness: 0.75, roughness: 0.42, envMapIntensity: 1 }),
      gold: new THREE.MeshStandardMaterial({ color: 0xd9b25c, metalness: 0.9, roughness: 0.3 }),
      glass: new THREE.MeshPhysicalMaterial({ color: 0xa8ecff, transparent: true, opacity: 0.11, roughness: 0.03, metalness: 0, clearcoat: 1, clearcoatRoughness: 0.04, side: THREE.DoubleSide, depthWrite: false, envMapIntensity: 1.8 }),
      noz: new THREE.MeshStandardMaterial({ color: 0x3a4658, metalness: 0.9, roughness: 0.3, side: THREE.DoubleSide }),
      dark: new THREE.MeshStandardMaterial({ color: 0x161f2c, metalness: 0.6, roughness: 0.5 }),
      deck: new THREE.MeshStandardMaterial({ color: 0x27384a, metalness: 0.6, roughness: 0.45 }),
      wall: new THREE.MeshStandardMaterial({ color: 0x66778c, metalness: 0.7, roughness: 0.4, envMapIntensity: 1.1 }),
    });
  }
  function slab(w, d, r, h, mat) {                            // a rounded-rectangle slab (x across, z along, y up)
    const sh = new THREE.Shape(), x = -w / 2, y = -d / 2;
    sh.moveTo(x + r, y); sh.lineTo(x + w - r, y); sh.quadraticCurveTo(x + w, y, x + w, y + r); sh.lineTo(x + w, y + d - r); sh.quadraticCurveTo(x + w, y + d, x + w - r, y + d); sh.lineTo(x + r, y + d); sh.quadraticCurveTo(x, y + d, x, y + d - r); sh.lineTo(x, y + r); sh.quadraticCurveTo(x, y, x + r, y);
    const g = new THREE.ExtrudeGeometry(sh, { depth: h, bevelEnabled: true, bevelThickness: 1.4, bevelSize: 1.4, bevelSegments: 2, curveSegments: 8 }); g.rotateX(-Math.PI / 2);
    return new THREE.Mesh(g, mat);
  }

  // ── the walkway lines carry a surge of colour from one end to the other: a bright band scrolling along a dim line ──
  let surgeBase = null, surges = [], surgesFixed = [];
  function surgeMap(len, dir, fixed) {
    if (!surgeBase) { surgeBase = tex(256, 4, (g, w, h) => { const gr = g.createLinearGradient(0, 0, w, 0); gr.addColorStop(0, 'rgb(120,120,120)'); gr.addColorStop(0.34, 'rgb(120,120,120)'); gr.addColorStop(0.46, 'rgb(190,190,190)'); gr.addColorStop(0.5, 'rgb(255,255,255)'); gr.addColorStop(0.54, 'rgb(190,190,190)'); gr.addColorStop(0.66, 'rgb(120,120,120)'); gr.addColorStop(1, 'rgb(120,120,120)'); g.fillStyle = gr; g.fillRect(0, 0, w, h); }, false); surgeBase.wrapS = THREE.RepeatWrapping; }
    const t = surgeBase.clone(); t.needsUpdate = true; t.wrapS = THREE.RepeatWrapping; t.repeat.set(len / 260, 1); (fixed ? surgesFixed : surges).push({ t, o: hash01(len, dir + 3), dir }); return t;
  }
  function moveSurges(t) { for (const list of [surges, surgesFixed]) for (const s of list) s.t.offset.x = ((s.o + s.dir * 0.00045 * t) % 1 + 1) % 1; }
  // ── the crew, drawn with their own robot artwork: a camera-facing sprite cut from the sprite sheet (bots.png). Every agent is the same size. ──
  const AGENT_H = 60, SHEET_COLS = 17;                                        // 1.75 times the size they were (34); Helix is still about twice their height
  let sheet = null, sheetState = 0; const cellCache = new Map(), spriteList = [];
  function loadSheet() { if (sheetState) return; sheetState = 1; const im = new Image(); im.onload = () => { sheet = im; sheetState = 2; spriteList.forEach(sp => paintAgent(sp, 0)); }; im.onerror = () => { sheetState = 3; }; im.src = (opts && opts.botUrl) || '/crew/bots.png'; }
  function cellTex(row, col) {
    const k = row * 100 + col; let t = cellCache.get(k); if (t) return t;
    const c = document.createElement('canvas'); c.width = c.height = 64; c.getContext('2d').drawImage(sheet, col * 64, row * 64, 64, 64, 0, 0, 64, 64);
    t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding; t.anisotropy = 4; cellCache.set(k, t); return t;
  }
  function makeAgent(role, tint) {
    loadSheet(); const rows = (opts && opts.botRows) || [], idx = rows.indexOf(role), row = idx < 0 ? 1 : idx;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, alphaTest: 0.2, depthWrite: true, color: 0xffffff }));
    sp.scale.set(AGENT_H, AGENT_H, 1); sp.center.set(0.5, 0.13); sp.userData.row = row; sp.userData.col = -1; sp.userData.seed = hash01(row + 3, 7) * 9;
    spriteList.push(sp); if (sheetState === 2) paintAgent(sp, 0); return sp;
  }
  function paintAgent(sp, col) { if (sheetState !== 2 || sp.userData.col === col) return; sp.userData.col = col; sp.material.map = cellTex(sp.userData.row, col); sp.material.needsUpdate = true; }
  const _cr = new THREE.Vector3(), _cf = new THREE.Vector3();
  function faceAgent(sp, vx, vz, t, moving) {                          // pick the standing picture, or the walking frame for the way they go as seen from the camera
    if (sheetState !== 2) return; if (!moving) { paintAgent(sp, 0); return; }
    _cr.set(Math.cos(cam.az), 0, -Math.sin(cam.az)); _cf.set(-Math.sin(cam.az), 0, -Math.cos(cam.az));
    const dr = vx * _cr.x + vz * _cr.z, df = vx * _cf.x + vz * _cf.z, frame = Math.floor(t * 0.008 + sp.userData.seed * 7) % 4;
    const dir = Math.abs(dr) >= Math.abs(df) * 0.8 ? (dr > 0 ? 2 : 3) : (df > 0 ? 1 : 0);          // 0 front, 1 back, 2 right, 3 left
    paintAgent(sp, 1 + dir * 4 + frame);
  }
  function dropAgent(sp) { const i = spriteList.indexOf(sp); if (i >= 0) spriteList.splice(i, 1); if (sp.parent) sp.parent.remove(sp); sp.material.dispose(); }

  // ── rocket flames: a tapering teardrop in three layers, a hot glow at the nozzle and shock diamonds ──
  let FL = null, GLOWT = null;
  function flameAssets() {
    if (FL) return;
    const ft = (stops) => { const c = document.createElement('canvas'); c.width = 64; c.height = 256; const g = c.getContext('2d'), gr = g.createLinearGradient(0, 256, 0, 0); stops.forEach(([p, col]) => gr.addColorStop(p, col)); g.fillStyle = gr; g.fillRect(0, 0, 64, 256);
      for (let i = 0; i < 26; i++) { g.fillStyle = 'rgba(255,255,255,' + (0.05 + Math.random() * 0.12) + ')'; g.fillRect(Math.random() * 64, 0, 2 + Math.random() * 5, 256); }
      const t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding; return t; };
    FL = [ft([[0, 'rgba(190,225,255,0.95)'], [0.18, 'rgba(70,150,255,0.85)'], [0.55, 'rgba(40,90,255,0.4)'], [1, 'rgba(30,50,255,0)']]),
           ft([[0, 'rgba(235,250,255,1)'], [0.2, 'rgba(120,225,255,0.92)'], [0.6, 'rgba(60,170,255,0.45)'], [1, 'rgba(60,140,255,0)']]),
           ft([[0, 'rgba(255,255,255,1)'], [0.25, 'rgba(240,252,255,0.95)'], [0.65, 'rgba(190,240,255,0.5)'], [1, 'rgba(190,240,255,0)']])];
    GLOWT = tex(128, 128, (g) => { const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64); gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.3, 'rgba(160,225,255,0.65)'); gr.addColorStop(1, 'rgba(80,160,255,0)'); g.fillStyle = gr; g.fillRect(0, 0, 128, 128); }, false);
  }
  function flame(R, L) {
    flameAssets();
    const g = new THREE.Group();
    [[1.0, 1.0, 0], [0.68, 0.8, 1], [0.36, 0.55, 2]].forEach(([rs, ls, k]) => {
      const pts = []; for (let i = 0; i <= 24; i++) { const t = i / 24, r = R * rs * Math.pow(1 - t, 0.85) * (1 + 0.18 * Math.sin(Math.min(1, t * 2.4) * Math.PI)); pts.push(new THREE.Vector2(Math.max(r, 0.001), -L * ls * t)); }
      const m = new THREE.Mesh(new THREE.LatheGeometry(pts, 24), new THREE.MeshBasicMaterial({ map: FL[k], transparent: true, side: THREE.DoubleSide, depthWrite: false, toneMapped: false })); m.renderOrder = 5 + k; g.add(m); });
    for (let k = 1; k <= 3; k++) { const d = new THREE.Mesh(new THREE.SphereGeometry(R * (0.3 - 0.05 * k), 10, 8), new THREE.MeshBasicMaterial({ color: 0xeaf8ff, transparent: true, opacity: 0.9 - k * 0.15, depthWrite: false, toneMapped: false })); d.scale.set(1, 1.9, 1); d.position.y = -L * 0.19 * k; d.renderOrder = 8; g.add(d); }
    const gl = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOWT, transparent: true, opacity: 0.85, depthWrite: false })); gl.scale.set(R * 3.2, R * 3.2, 1); gl.position.y = -R * 0.2; gl.renderOrder = 9; g.add(gl);
    flames.push(g); return g;
  }

  // ── the fixed structure: base, boosters, ring thruster, walkways, hub, dome ──
  function buildStructure() {
    const m = mats(), G = new THREE.Group(); scene.add(G); rootG = G;
    const add = (o) => { G.add(o); return o; };
    const deck = add(new THREE.Mesh(new THREE.CylinderGeometry(RD + 6, RD + 6, 14, 72), new THREE.MeshStandardMaterial({ color: 0x0d1624, metalness: 0.5, roughness: 0.6 }))); deck.position.y = -7;
    const skirt = add(new THREE.Mesh(new THREE.CylinderGeometry(RD + 52, RD + 78, 70, 72), m.hull)); skirt.position.y = -49;
    const under = add(new THREE.Mesh(new THREE.CylinderGeometry(RD + 78, RD * 0.5, 150, 72), m.hullD)); under.position.y = -159;
    const keel = add(new THREE.Mesh(new THREE.CylinderGeometry(RD * 0.5, RD * 0.3, 60, 56), m.hull)); keel.position.y = -264;
    for (const [r, y, c] of [[RD + 64, -30, 0xd9b25c], [RD + 80, -90, 0x7fe9ff], [RD * 0.78, -200, 0x7fe9ff]]) { const t = new THREE.Mesh(new THREE.TorusGeometry(r, 4, 8, 96), c === 0xd9b25c ? m.gold : new THREE.MeshBasicMaterial({ color: c })); t.rotation.x = Math.PI / 2; t.position.y = y; add(t); }
    const ringB = add(new THREE.Mesh(new THREE.TorusGeometry(RD + 6, 9, 10, 96), m.gold)); ringB.rotation.x = Math.PI / 2; ringB.position.y = 6;
    const baseGlow = add(new THREE.Mesh(new THREE.TorusGeometry(RD + 6, 5, 8, 120), new THREE.MeshBasicMaterial({ color: 0x4fb8ff }))); baseGlow.rotation.x = Math.PI / 2; baseGlow.position.y = 17;
    const win = new THREE.InstancedMesh(new THREE.BoxGeometry(5, 14, 12), new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }), 72), mm = new THREE.Matrix4(), qq = new THREE.Quaternion(); winMesh = win;
    for (let i = 0; i < 72; i++) { const a = i / 72 * 6.283; qq.setFromAxisAngle(new THREE.Vector3(0, 1, 0), -a); mm.compose(new THREE.Vector3(Math.cos(a) * (RD + 66), -49, Math.sin(a) * (RD + 66)), qq, new THREE.Vector3(1, 1, 1)); win.setMatrixAt(i, mm); win.setColorAt(i, new THREE.Color(0x00e5ff)); }
    add(win);
    // eight booster engines on gimbal knuckles, flames pointing down
    for (let i = 0; i < 8; i++) {
      const a = i / 8 * 6.283 + 0.3927, e = new THREE.Group();
      const strut = new THREE.Mesh(new THREE.BoxGeometry(80, 18, 26), m.hullD); strut.position.set(40, 0, 0); e.add(strut);
      const knuckle = new THREE.Mesh(new THREE.SphereGeometry(30, 20, 14), m.gold); knuckle.position.set(90, 0, 0); e.add(knuckle);
      for (const rot of [[Math.PI / 2, 0, 0], [0, 0, 0]]) { const c = new THREE.Mesh(new THREE.TorusGeometry(34, 3.2, 8, 28), m.hull); c.rotation.set(...rot); c.position.set(90, 0, 0); e.add(c); }
      const pivot = new THREE.Group(); boostPivots.push(pivot); pivot.position.set(90, 0, 0); pivot.rotation.z = 0.2; e.add(pivot);
      const socket = new THREE.Mesh(new THREE.CylinderGeometry(24, 30, 26, 18), m.hullD); socket.position.y = -34; pivot.add(socket);
      const body = new THREE.Mesh(new THREE.CylinderGeometry(32, 36, 130, 22), m.hull); body.position.y = -112; pivot.add(body);
      for (const y of [-64, -112, -160]) { const ring = new THREE.Mesh(new THREE.TorusGeometry(36, 3, 8, 24), y === -112 ? m.gold : m.hullD); ring.rotation.x = Math.PI / 2; ring.position.y = y; pivot.add(ring); }
      const nozzle = new THREE.Mesh(new THREE.CylinderGeometry(30, 50, 62, 24, 1, true), m.noz); nozzle.position.y = -208; pivot.add(nozzle);
      const lip = new THREE.Mesh(new THREE.TorusGeometry(50, 3.4, 8, 28), m.hull); lip.rotation.x = Math.PI / 2; lip.position.y = -239; pivot.add(lip);
      const fl = flame(40, 250); fl.position.y = -236; pivot.add(fl);
      e.position.set(Math.cos(a) * (RD + 50), -22, Math.sin(a) * (RD + 50)); e.rotation.y = -a; add(e);
    }
    // the ring thruster under the keel: a fat ring on four struts with sixteen small nozzles, each with its own flame
    { const T = new THREE.Group(); T.position.y = -272;                         // raised so the ring and its four struts meet the underside of the keel (bottom at -294)
      const ringE = new THREE.Mesh(new THREE.TorusGeometry(170, 34, 14, 64), m.hull); ringE.rotation.x = Math.PI / 2; ringE.position.y = -50; T.add(ringE);
      const ringG = new THREE.Mesh(new THREE.TorusGeometry(170, 7, 10, 64), m.gold); ringG.rotation.x = Math.PI / 2; ringG.position.y = -50; T.add(ringG);
      for (let i = 0; i < 4; i++) { const a2 = i / 4 * 6.283 + 0.785, st = new THREE.Mesh(new THREE.BoxGeometry(150, 12, 20), m.hullD); st.position.set(Math.cos(a2) * 100, -22, Math.sin(a2) * 100); st.rotation.y = -a2; T.add(st); }
      for (let i = 0; i < 16; i++) { const a2 = i / 16 * 6.283, n = new THREE.Mesh(new THREE.CylinderGeometry(15, 24, 40, 14, 1, true), m.noz); n.position.set(Math.cos(a2) * 170, -92, Math.sin(a2) * 170); T.add(n);
        const fl = flame(21, 120); fl.position.set(Math.cos(a2) * 170, -112, Math.sin(a2) * 170); T.add(fl); }
      add(T); }
    // Helix's hub: the ring deck at walking level, a gold rail, a raised dais in the middle
    const hub = new THREE.Group();
    const h1 = new THREE.Mesh(new THREE.CylinderGeometry(136, 142, FLOOR_Y, 56), m.deck); h1.position.y = FLOOR_Y / 2; hub.add(h1);
    const h1b = new THREE.Mesh(new THREE.CylinderGeometry(112, 112, 1.2, 56), new THREE.MeshStandardMaterial({ color: 0x132636, metalness: 0.4, roughness: 0.6 })); h1b.position.y = FLOOR_Y + 0.6; hub.add(h1b);
    const h3 = new THREE.Mesh(new THREE.CylinderGeometry(62, 68, 14, 48), m.dark); h3.position.y = FLOOR_Y + 7; hub.add(h3);
    for (const [r, y, c, tr] of [[138, FLOOR_Y + 2, m.gold, 3.2], [112, FLOOR_Y + 1.6, new THREE.MeshBasicMaterial({ color: 0x7fe9ff }), 1.4], [66, FLOOR_Y + 15, new THREE.MeshBasicMaterial({ color: 0x7fe9ff }), 2.4]]) { const t = new THREE.Mesh(new THREE.TorusGeometry(r, tr, 8, 72), c); t.rotation.x = Math.PI / 2; t.position.y = y; hub.add(t); }
    add(hub);
    // the octagon walkway at walking level: wide, with a proper round junction pad at each corner
    const pts = []; for (let k = 0; k < 8; k++) { const a = -Math.PI / 2 + Math.PI / 8 + k * Math.PI / 4; pts.push([Math.cos(a) * VC, Math.sin(a) * VC]); }
    const OW = 30, PAD = 27;
    for (let k = 0; k < 8; k++) {
      const [x1, z1] = pts[k], [x2, z2] = pts[(k + 1) % 8], len = Math.hypot(x2 - x1, z2 - z1), ang = Math.atan2(z2 - z1, x2 - x1), g = new THREE.Group();
      const run = len - 2 * (PAD - 6), b = new THREE.Mesh(new THREE.BoxGeometry(run, FLOOR_Y, OW), m.deck); b.position.y = FLOOR_Y / 2; g.add(b);
      const l = new THREE.Mesh(new THREE.BoxGeometry(len - 2 * PAD - 6, 1.1, 4.2), new THREE.MeshBasicMaterial({ color: new THREE.Color(0x00e5ff).multiplyScalar(1.7), map: surgeMap(len - 2 * PAD - 6, -1, true), toneMapped: false })); l.position.y = FLOOR_Y + 0.3; g.add(l);
      for (const sgn of [-1, 1]) { const rail = new THREE.Mesh(new THREE.BoxGeometry(len - 2 * PAD - 2, 4, 2.4), m.hullD); rail.position.set(0, FLOOR_Y + 2, sgn * (OW / 2 - 1.2)); g.add(rail); }
      g.position.set((x1 + x2) / 2, 0, (z1 + z2) / 2); g.rotation.y = -ang; add(g);
    }
    pts.forEach(([x, z]) => {
      const pad = new THREE.Mesh(new THREE.CylinderGeometry(PAD, PAD, FLOOR_Y + 0.8, 40), new THREE.MeshStandardMaterial({ color: 0x33485c, metalness: 0.6, roughness: 0.4 })); pad.position.set(x, (FLOOR_Y + 0.8) / 2, z); add(pad);
      const rim = new THREE.Mesh(new THREE.TorusGeometry(PAD, 2.2, 8, 40), m.gold); rim.rotation.x = Math.PI / 2; rim.position.set(x, FLOOR_Y + 0.8, z); add(rim);
      const inn = new THREE.Mesh(new THREE.TorusGeometry(PAD * 0.55, 0.9, 6, 32), new THREE.MeshBasicMaterial({ color: 0x7fe9ff })); inn.rotation.x = Math.PI / 2; inn.position.set(x, FLOOR_Y + 1.3, z); add(inn); });
    G.userData.oct = pts;
    // the dome: glass plus a geodesic hexagon frame (the dual of an icosphere)
    const dome = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2), m.glass); dome.scale.set(RD, HD, RD); dome.renderOrder = 3; G.add(dome);
    // a second, inner skin (back faces only) so the glass has a visible edge all the way round, like a real bubble
    const inner = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2), new THREE.MeshBasicMaterial({ color: 0x9fe8ff, transparent: true, opacity: 0.05, side: THREE.BackSide, depthWrite: false })); inner.scale.set(RD * 0.998, HD * 0.998, RD * 0.998); inner.renderOrder = 3; G.add(inner);
    // Helix herself is her 3D model (loadHelix); this hologram of her portrait is only a stand-in until it has loaded
    loadHelix();
    const img = new Image(); img.onload = () => {
      const holoTex = tex(512, 512, (g, w, h) => { const sw = img.naturalWidth, sh = img.naturalHeight, sq = Math.min(sw, sh); g.drawImage(img, (sw - sq) / 2, 0, sq, sq, 0, 0, w, h);
        const gr = g.createRadialGradient(w / 2, h * 0.42, h * 0.12, w / 2, h * 0.5, h * 0.52); gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, 'rgba(0,0,0,1)'); g.globalCompositeOperation = 'destination-out'; g.fillStyle = gr; g.fillRect(0, 0, w, h);
        g.globalCompositeOperation = 'source-over'; g.fillStyle = 'rgba(120,230,255,0.10)'; for (let y = 0; y < h; y += 5) g.fillRect(0, y, w, 1.6); });
      holo = new THREE.Mesh(new THREE.PlaneGeometry(190, 190), new THREE.MeshBasicMaterial({ map: holoTex, transparent: true, side: THREE.DoubleSide, depthWrite: false, toneMapped: false, opacity: 0.95 })); holo.position.y = FLOOR_Y + 112; holo.renderOrder = 7; G.add(holo);
      holo.userData.hub = true; };
    img.src = '/crew/helix.jpg';
    // the crew walking the octagon (for looks); working crew stand in their trays
    const WALK = ['researcher', 'writer', 'designer', 'marketer', 'social', 'support', 'analyst', 'qa', 'finance', 'weather', 'travel', 'event', 'trader', 'composer', 'nft', 'screenwriter', 'builder', 'operator', 'reviewer', 'briefer', 'workbench', 'researcher', 'writer', 'designer'];
    for (let i = 0; i < 20; i++) { const gw = makeAgent(WALK[i]);
      gw.userData.f0 = i / 20; gw.userData.dir = i % 3 === 0 ? -1 : 1; gw.userData.v = 0.0000055 + (i % 5) * 0.000002; gw.userData.off = (i % 2 ? 4 : -4);
      add(gw); walkers.push(gw); }
    facGroup = new THREE.Group(); G.add(facGroup);
    selRing = new THREE.Mesh(new THREE.TorusGeometry(1, 0.03, 6, 48), new THREE.MeshBasicMaterial({ color: 0xffffff })); selRing.rotation.x = Math.PI / 2; selRing.visible = false; G.add(selRing);
    built = true;
  }

  // ── Helix herself: her 3D model walking round the hub ring, much taller than the crew. If the file is not there the hologram stays. ──
  const HELIX_H = 168; let helix = null, mixer = null, lastT = 0, faceAng = 0;                       // twice the size she was, in the middle of the hub
  function loadHelix() {
    if (!THREE.GLTFLoader) return;
    new THREE.GLTFLoader().load('/crew/helix.glb?v=3', (gltf) => {
      const obj = gltf.scene, pivot = new THREE.Group(), inner = new THREE.Group();
      // a rigged model's size is only right once a frame of its animation is applied, so measure after that
      let clip = null; if (gltf.animations && gltf.animations.length) { clip = gltf.animations.find(c => /walking_woman/i.test(c.name)) || gltf.animations.find(c => /walk/i.test(c.name)) || gltf.animations[0]; mixer = new THREE.AnimationMixer(obj); const act = mixer.clipAction(clip); act.play(); act.timeScale = 0.45; mixer.update(0.05);
        const flipClip = gltf.animations.find(c => /backflip/i.test(c.name)); actions = { walk: act, flip: flipClip ? mixer.clipAction(flipClip) : null }; }
      obj.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(obj, true), size = box.getSize(new THREE.Vector3()), k = HELIX_H / Math.max(0.001, size.y);
      obj.scale.setScalar(k); obj.updateMatrixWorld(true); box.setFromObject(obj, true); obj.position.set(-(box.min.x + box.max.x) / 2, -box.min.y, -(box.min.z + box.max.z) / 2); inner.add(obj); pivot.add(inner);
      obj.traverse(o => { if (o.isMesh) { o.frustumCulled = false; if (o.material) { o.material.envMapIntensity = 1.2; } } });
      const hl = new THREE.PointLight(0x9fe8ff, 2.2, 520, 1.3); hl.position.set(0, HELIX_H * 0.6, 70); pivot.add(hl);
      pivot.userData.inner = inner; pivot.userData.animated = !!clip; helix = pivot; helix.position.set(0, FLOOR_Y + 14, 0); rootG.add(pivot);
      if (holo) holo.visible = false;
    }, undefined, () => { /* no model: the hologram stays */ });
  }
  const hw = { x: 0, z: 0, tx: 0, tz: 0, ready: false };                              // where she is on the hub and where she is heading
  function moveHelix(t) {                                                            // she strolls the centre and the ring; for take-off she comes back to the middle first
    if (!helix) return; const dt = Math.min(0.1, (t - lastT) / 1000); lastT = t; if (mixer) mixer.update(dt);
    let want = null, speed = 26;
    if (launchS !== null) {                                                          // called for take-off: straight back to the centre, then face the camera for the flip
      hw.tx = 0; hw.tz = 0; speed = 70;
      if (Math.hypot(hw.x, hw.z) < 2) want = Math.atan2(camera.position.x, camera.position.z);
    } else if (!hw.ready || Math.hypot(hw.tx - hw.x, hw.tz - hw.z) < 4) {           // pick the next spot to stroll to: anywhere from the middle out to the ring
      const a = Math.random() * 6.283185, r = Math.random() < 0.25 ? Math.random() * 40 : 70 + Math.random() * 56; hw.tx = Math.cos(a) * r; hw.tz = Math.sin(a) * r; hw.ready = true;
    }
    const dx = hw.tx - hw.x, dz = hw.tz - hw.z, dist = Math.hypot(dx, dz);
    if (dist > 1) { const st = Math.min(dist, speed * dt); hw.x += dx / dist * st; hw.z += dz / dist * st; if (want === null) want = Math.atan2(dx, dz); }
    if (want === null) want = faceAng;
    let d = want - faceAng; d = Math.atan2(Math.sin(d), Math.cos(d)); faceAng += d * Math.min(1, dt * (launchS !== null ? 6 : 4)); helix.rotation.y = faceAng;
    helix.position.set(hw.x, FLOOR_Y + 14, hw.z);
    if (actions && launchS === null && actions.walk) actions.walk.timeScale = 0.8;
    if (!helix.userData.animated) helix.userData.inner.rotation.z = 0.04 * Math.sin(t * 0.0025);
  }
  // ── take-off: Helix does a backflip and calls it, the side thrusters swing round, the whole station spins clockwise and rockets off into deep space, then comes back ──
  const LAUNCH_S = 11.6, ease3 = (x) => x < 0 ? 0 : x > 1 ? 1 : x * x * (3 - 2 * x), eIn = (x) => { x = Math.max(0, Math.min(1, x)); return x * x * x; }, eOut = (x) => { x = Math.max(0, Math.min(1, x)); return 1 - Math.pow(1 - x, 3); };
  const _away = new THREE.Vector3();
  function startLaunch() {
    if (launchS !== null || !running || document.hidden) return false;
    launchS = { t0: performance.now(), flipped: false, said: false, flipAt: Math.min(2.2, Math.hypot(hw.x, hw.z) / 70 + 0.4) };
    if (opts && opts.onLaunch) opts.onLaunch();
    return true;
  }
  function stepLaunch() {
    if (launchS === null) return; const s = (performance.now() - launchS.t0) / 1000;
    if (s >= LAUNCH_S) {                                                           // landed: everything back exactly as it was
      launchS = null; boost = 1; rootG.position.set(0, 0, 0); rootG.rotation.y = 0; boostPivots.forEach(p => { p.rotation.set(0, 0, 0.2); });
      if (actions && actions.flip) { actions.walk.reset().play(); actions.walk.timeScale = 0.45; actions.flip.stop(); } return; }
    if (actions && actions.flip && !launchS.flipped && !launchS.said && s >= launchS.flipAt) { const f = actions.flip; f.reset(); f.setLoop(THREE.LoopOnce, 1); f.clampWhenFinished = true; f.timeScale = 1; f.play(); actions.walk.crossFadeTo(f, 0.2, false); launchS.flipped = true; launchS.said = true; }
    if (actions && actions.flip && launchS.flipped && s > launchS.flipAt + 2.3) { launchS.flipped = false; actions.walk.reset(); actions.walk.timeScale = 0.45; actions.walk.play(); actions.flip.crossFadeTo(actions.walk, 0.3, false); }
    // the side thrusters swing round on their knuckles, then lock pointing straight down for the burn, then relax again on the way back
    const sw = (s - 1.5) / 2.0, back = ease3((s - 10.2) / 1.2);
    const ang = s < 1.5 ? 0.2 : sw < 1 ? 0.2 + (-0.1 - 0.2) * ease3(sw) + 0.65 * Math.sin(sw * Math.PI * 2) : -0.1 + (0.2 + 0.1) * back;
    boostPivots.forEach((p, i) => { p.rotation.z = ang; p.rotation.x = s < 1.5 || s > 11 ? 0 : 0.35 * Math.sin(Math.min(1, sw) * Math.PI * 2 + i); });
    boost = 1 + 1.7 * ease3((s - 3.0) / 1.4) * (1 - ease3((s - 10.2) / 1.2));    // the flames grow as the engines catch
    // a clockwise spin (seen from above), slow to start, fastest in flight, slow to stop; three full turns in all so it ends exactly where it began
    rootG.rotation.y = -6.283185 * 3 * (ease3((s - 3.0) / 7.8));
    // up and away, away from the viewer into deep space; a pause far out; then back, braking
    const out = eIn((s - 4.8) / 2.4) * (1 - eOut((s - 8.3) / 2.6));
    // out: straight down the line of sight so it shrinks into the middle of the screen (never up off the top); back: lifted overhead so it drops in from above
    const hi = ease3((s - 7.2) / 1.1); _away.copy(camera.position).negate().normalize();
    const hz = Math.hypot(camera.position.x, camera.position.z) || 1, hx = -camera.position.x / hz, hzz = -camera.position.z / hz;
    rootG.position.set((_away.x * 14000 * (1 - hi) + hx * 12000 * hi) * out, (_away.y * 14000 * (1 - hi) + 2400 * hi) * out, (_away.z * 14000 * (1 - hi) + hzz * 12000 * hi) * out);
  }
  // ── the factories: open work trays and the walkway bands to them; rebuilt when the modules change ──
  function band(g, a, r0, r1, wid, col, lit, dir) {
    const m = mats(), len = r1 - r0, [mx, mz] = polar(a, (r0 + r1) / 2), b = new THREE.Group(); if (len < 4) return;
    const bx = new THREE.Mesh(new THREE.BoxGeometry(len, FLOOR_Y, wid), m.deck); bx.position.y = FLOOR_Y / 2; b.add(bx);
    const l = new THREE.Mesh(new THREE.BoxGeometry(Math.max(2, len - 6), 1.1, 4.2), lit ? new THREE.MeshBasicMaterial({ color: new THREE.Color(col).multiplyScalar(1.7), map: surgeMap(Math.max(2, len - 6), dir || -1, false), toneMapped: false }) : new THREE.MeshBasicMaterial({ color: 0x1c2a38 })); l.position.y = FLOOR_Y + 0.3; b.add(l);
    for (const s of [-1, 1]) { const rail = new THREE.Mesh(new THREE.BoxGeometry(Math.max(2, len - 4), 4, 2.4), m.hullD); rail.position.set(0, FLOOR_Y + 2, s * (wid / 2 - 1.2)); b.add(rail); }
    b.position.set(mx, 0, mz); b.rotation.y = -a; g.add(b);
  }
  function labelSprite(text, state, col, lit) {
    const t = tex(512, 112, (g, w, h) => { g.fillStyle = 'rgba(6,11,18,0.9)'; g.fillRect(0, 0, w, h); g.strokeStyle = col; g.globalAlpha = lit ? 0.9 : 0.5; g.lineWidth = 5; g.strokeRect(2, 2, w - 4, h - 4); g.globalAlpha = 1; g.fillStyle = col; g.fillRect(0, 8, 9, h - 16);
      g.textAlign = 'center'; let fs = 46; g.font = '700 ' + fs + 'px "Courier New", monospace'; while (g.measureText(text).width > 440 && fs > 22) { fs -= 2; g.font = '700 ' + fs + 'px "Courier New", monospace'; }
      g.fillStyle = col; g.fillText(text, w / 2 + 4, 52); g.font = '28px "Courier New", monospace'; g.fillStyle = state === 'OPEN' ? '#ff4fb8' : state === 'COMING SOON' ? '#ff2d95' : state === 'WORKING' || state === 'ONLINE' || state === 'LIVE' ? '#00ff99' : state === 'DOWN' ? '#ff3344' : '#ff8a2a'; g.fillText(state, w / 2 + 4, 94); });
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false })); s.scale.set(150, 33, 1); s.renderOrder = 8; return s;
  }
  function tray(g, idx, m0) {
    const m = mats(), outer = idx >= 8, w = outer ? 134 : 138, len = outer ? 84 : 92, a = m0.a0 !== undefined ? m0.a0 : m0.a, D = outer ? R_OUT : R_IN, lit = !m0.expansion;
    const col = lit ? (m0.color || '#00e5ff') : '#52667a', C = new THREE.Color(col), group = new THREE.Group(), WH = 15, T = 5;
    const cm = new THREE.MeshStandardMaterial({ color: C, emissive: C, emissiveIntensity: lit ? 1.2 : 0.03, metalness: 0.4, roughness: 0.4 });
    group.add(slab(w, len, 12, FLOOR_Y, m.wall));
    const under = slab(w + 2, len + 2, 13, 3, cm); group.add(under);
    const inner = slab(w - 12, len - 12, 8, 1.4, new THREE.MeshStandardMaterial({ color: 0x0f1822, metalness: 0.5, roughness: 0.6 })); inner.position.y = FLOOR_Y; group.add(inner);
    const wall = (ww, dd, px, pz) => { const q = new THREE.Mesh(new THREE.BoxGeometry(ww, WH, dd), m.wall); q.position.set(px, FLOOR_Y + WH / 2, pz); group.add(q);
      const cap = new THREE.Mesh(new THREE.BoxGeometry(ww + 0.6, 3.2, dd + 0.6), cm); cap.position.set(px, FLOOR_Y + WH + 1.6, pz); group.add(cap); };
    const gap = 38, sideW = (w - gap) / 2;
    wall(w, T, 0, len / 2 - T / 2); wall(T, len - T, -w / 2 + T / 2, -T / 2); wall(T, len - T, w / 2 - T / 2, -T / 2);
    wall(sideW, T, -(gap / 2 + sideW / 2), -len / 2 + T / 2); wall(sideW, T, gap / 2 + sideW / 2, -len / 2 + T / 2);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) { const post = new THREE.Mesh(new THREE.CylinderGeometry(5, 5, WH + 6, 10), m.hull); post.position.set(sx * (w / 2 - 3), FLOOR_Y + (WH + 6) / 2, sz * (len / 2 - 3)); group.add(post);
      const orb = new THREE.Mesh(new THREE.SphereGeometry(6.5, 16, 12), new THREE.MeshBasicMaterial({ color: lit ? C.clone().multiplyScalar(1.5) : C, toneMapped: false })); orb.position.set(post.position.x, FLOOR_Y + WH + 6 + 5, post.position.z); group.add(orb); }
    const n = 4, running = (m0.tasks || []).filter(t => t.status === 'running');
    for (let i = 0; i < n; i++) { const cx = -w / 2 + 22 + i * ((w - 44) / (n - 1));
      const desk = new THREE.Mesh(new THREE.BoxGeometry((w - 44) / n - 8, 9, 13), new THREE.MeshStandardMaterial({ color: 0x24384a, metalness: 0.5, roughness: 0.5 })); desk.position.set(cx, FLOOR_Y + 5.5, len / 2 - T - 11); group.add(desk);
      const on = lit && (i < running.length || !m0.tasks || !m0.tasks.length), scr = new THREE.Mesh(new THREE.BoxGeometry((w - 44) / n - 14, 3, 7), new THREE.MeshBasicMaterial({ color: on ? C : 0x26323f })); scr.position.set(cx, FLOOR_Y + 10.4, len / 2 - T - 11); group.add(scr); }
    // crew with a running task here stand at their consoles
    running.slice(0, 4).forEach((tk, i) => { const cx = -w / 2 + 22 + i * ((w - 44) / (n - 1)), ag = makeAgent(tk.role); ag.userData.tray = true; ag.position.set(cx, FLOOR_Y + 1, len / 2 - T - 26); group.add(ag); trayAgents.push(ag); });
    const [px, pz] = polar(a, D); group.position.set(px, 0, pz); group.rotation.y = Math.PI / 2 - a; g.add(group);
    const hb = new THREE.Mesh(new THREE.BoxGeometry(w, 40, len), new THREE.MeshBasicMaterial({ visible: false })); hb.position.set(0, FLOOR_Y + 15, 0); group.add(hb); hb.userData = { key: m0.key, expansion: !!m0.expansion }; hitBoxes.push(hb);
    const lab = labelSprite(String(m0.label || m0.key || '').toUpperCase(), m0.state || '', col, lit); const [lx, lz] = polar(a, D + (len / 2) + 26); lab.position.set(lx, FLOOR_Y + 62, lz); g.add(lab);
    // the way in: a band from Helix's ring (or from the walkway, for the branch factories) to the open gap
    const branch = idx >= 16, WB = 26, end = D - len / 2 + 3, sd = idx % 2 ? 1 : -1;                  // surges alternate: some run out to the factory, some in to Helix
    if (branch) band(g, a, DW + 15, end, WB, C, lit, sd);                                           // off the middle of a walkway side
    else if (idx >= 8) { band(g, a, 142, VC - 22, WB, C, lit, sd); band(g, a, VC + 22, end, WB, C, lit, sd); }   // through the gap, crossing the walkway at its corner pad
    else band(g, a, 142, end, WB, C, lit, sd);                                                      // an inner factory: starts at the edge of Helix's ring, not inside it
    return { a, D };
  }
  function rebuild(mods) {
    while (facGroup.children.length) { const c = facGroup.children.pop(); c.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material && o.material.map) o.material.map.dispose(); }); }
    for (const ag of trayAgents) dropAgent(ag); trayAgents = []; hitBoxes = []; surges = []; const slots = {};
    mods.forEach((m0, i) => { slots[m0.key] = tray(facGroup, i, m0); });
    facGroup.userData.slots = slots;
  }

  // ── the scene ──
  function setup() {
    canvas = document.createElement('canvas'); canvas.id = 'cv3d'; canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab'; stage.appendChild(canvas);
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); renderer.setClearColor(0x000000, 0); renderer.outputEncoding = THREE.sRGBEncoding; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.0;
    scene = new THREE.Scene(); camera = new THREE.PerspectiveCamera(30, 1, 10, 30000);
    { const pm = new THREE.PMREMGenerator(renderer), es = new THREE.Scene();
      const g = tex(512, 256, (c, w, h) => { const gr = c.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, '#7a96d8'); gr.addColorStop(0.45, '#262d54'); gr.addColorStop(0.5, '#8a6aa8'); gr.addColorStop(0.58, '#141728'); gr.addColorStop(1, '#07080f'); c.fillStyle = gr; c.fillRect(0, 0, w, h); });
      es.add(new THREE.Mesh(new THREE.SphereGeometry(50, 24, 12), new THREE.MeshBasicMaterial({ map: g, side: THREE.BackSide })));
      for (const [x, y, z, s, col] of [[30, 25, -20, 16, 0xfff2e0], [-30, 12, 25, 10, 0x9fbaff], [0, 45, 0, 12, 0xffffff]]) { const mm = new THREE.Mesh(new THREE.PlaneGeometry(s, s), new THREE.MeshBasicMaterial({ color: col, side: THREE.DoubleSide })); mm.position.set(x, y, z); mm.lookAt(0, 0, 0); es.add(mm); }
      scene.environment = pm.fromScene(es, 0.03).texture; }
    scene.add(new THREE.AmbientLight(0x6f84b6, 0.5));
    const sun = new THREE.DirectionalLight(0xfff0dd, 2.6); sun.position.set(900, 1100, -500); scene.add(sun);
    const rimL = new THREE.DirectionalLight(0x6a8cff, 1.1); rimL.position.set(-900, 350, 700); scene.add(rimL);
    const fill = new THREE.DirectionalLight(0xffffff, 0.5); fill.position.set(0, 500, 900); scene.add(fill);
    const glow = new THREE.PointLight(0x9fe8ff, 1.0, 1400, 1.6); glow.position.set(0, 160, 0); scene.add(glow);
    const below = new THREE.DirectionalLight(0x9db8ff, 1.5); below.position.set(300, -900, 500); scene.add(below);                  // a cold light from underneath so the keel and thruster show
    buildStructure();
    // drag to look around, wheel or pinch to zoom, tap a factory to select it
    let down = null, pinch = null;
    const pts = new Map();
    canvas.style.touchAction = 'none';
    canvas.addEventListener('pointerdown', e => { canvas.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); down = { x: e.clientX, y: e.clientY, moved: 0 }; lastInteract = performance.now(); if (pts.size === 2) pinch = dist2(); });
    canvas.addEventListener('pointermove', e => {
      if (!pts.has(e.pointerId)) return; const p = pts.get(e.pointerId); lastInteract = performance.now();
      if (pts.size === 2) { pts.set(e.pointerId, [e.clientX, e.clientY]); const d = dist2(); if (pinch) cam.zoom = Math.max(0.35, Math.min(2.2, cam.zoom * (pinch / d))); pinch = d; return; }
      const dx = e.clientX - p[0], dy = e.clientY - p[1]; pts.set(e.pointerId, [e.clientX, e.clientY]); if (down) down.moved += Math.abs(dx) + Math.abs(dy);
      cam.az -= dx * 0.006; cam.el = Math.max(-0.5, Math.min(1.45, cam.el + dy * 0.005));
    });
    const dist2 = () => { const a = [...pts.values()]; return Math.hypot(a[0][0] - a[1][0], a[0][1] - a[1][1]) || 1; };
    const up = (e) => { pts.delete(e.pointerId); pinch = null; if (down && down.moved < 6 && e.type === 'pointerup') pick(e); down = null; };
    canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
    canvas.addEventListener('wheel', e => { e.preventDefault(); lastInteract = performance.now(); cam.zoom = Math.max(0.35, Math.min(2.2, cam.zoom * (e.deltaY > 0 ? 1.08 : 0.92))); }, { passive: false });
    ro = new ResizeObserver(resize); ro.observe(stage);
    resize();
  }
  function resize() {
    if (!renderer) return; const r = stage.getBoundingClientRect(), w = Math.max(10, r.width), h = Math.max(10, r.height);
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    cam.dist = Math.max(2750, 3600 / camera.aspect);                     // just far enough that the whole station and its flames fit, whatever the shape of the box
  }
  const ray = new THREE.Raycaster(), v2 = new THREE.Vector2();
  function pick(e) {
    const r = canvas.getBoundingClientRect(); v2.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(v2, camera);
    if (helix) { const h = ray.intersectObject(helix, true); if (h.length && opts && opts.onHelix) { opts.onHelix(); return; } }
    if (holo && holo.visible) { const h = ray.intersectObject(holo, false); if (h.length && opts && opts.onSelect) { opts.onSelect('hub'); return; } }
    const hits = ray.intersectObjects(hitBoxes, false); if (hits.length && opts && opts.onSelect) { const u = hits[0].object.userData; opts.onSelect(u.expansion ? 'open' : u.key, u); }
  }

  function frame() {
    raf = requestAnimationFrame(frame); if (!running) return;
    const t = performance.now() - T0, idle = performance.now() - lastInteract > 4000;
    if (idle) cam.az += 0.0009;                                          // a slow turn while you are not touching it
    const d = cam.dist * cam.zoom, ce = Math.cos(cam.el);
    camera.position.set(Math.sin(cam.az) * d * ce, Math.sin(cam.el) * d + cam.ly * 0.2, Math.cos(cam.az) * d * ce); camera.lookAt(0, cam.ly, 0);
    if (holo) holo.rotation.y = Math.atan2(camera.position.x, camera.position.z);
    for (let i = 0; i < flames.length; i++) { const f = flames[i], s = 1 + 0.07 * Math.sin(t * 0.03 + i * 1.7) + 0.03 * Math.sin(t * 0.071 + i); f.scale.y = s * boost; f.scale.x = f.scale.z = 1 + 0.03 * Math.sin(t * 0.05 + i * 2.3); }
    moveHelix(t); moveSurges(t); stepLaunch(); updateRim(t); if (t - crewCheck > 700) { crewCheck = t; syncCrew(); } moveCrew(t);
    const pts = octPts();
    if (pts) for (const w of walkers) { const u = w.userData, f = (((u.f0 + u.dir * u.v * t) % 1) + 1) % 1 * 8, k = Math.floor(f) % 8, q = f - Math.floor(f), A = pts[k], B = pts[(k + 1) % 8];
      const dx = B[0] - A[0], dz = B[1] - A[1], L = Math.hypot(dx, dz) || 1; w.position.set(A[0] + dx * q + (-dz / L) * u.off, FLOOR_Y + Math.abs(Math.sin(t * 0.012 + u.f0 * 30)) * 0.8, A[1] + dz * q + (dx / L) * u.off); faceAgent(w, dx / L * u.dir, dz / L * u.dir, t, true); }
    // the selected factory wears a ring
    const sel = opts && opts.getSelected ? String(opts.getSelected() || '') : '', slots = facGroup && facGroup.userData.slots;
    if (selRing) { const key = sel.startsWith('f:') ? sel.slice(2) : '', s = slots && slots[key]; if (s) { const [x, z] = polar(s.a, s.D); selRing.visible = true; selRing.position.set(x, FLOOR_Y + 2, z); selRing.scale.setScalar(86); selRing.material.opacity = 1; selRing.material.color.setHSL((t * 0.0004) % 1, 0.9, 0.7); } else selRing.visible = false; }
    renderer.render(scene, camera);
  }
  const RIM_PLACEHOLDER = 0;
  const RIM = [0x00e5ff, 0xff6d00, 0xb06cff, 0x00ff99, 0xff4fb8, 0xffe600, 0x6b8cff, 0xff3344], rimC = new THREE.Color();
  function updateRim(t) {                                                // each light has its own colour, rhythm and on/off pattern, plus a slow chase of light round the rim
    if (!winMesh) return; for (let i = 0; i < 72; i++) { const period = 500 + hash01(i, 1) * 1900, step = Math.floor(t / period + hash01(i, 2) * 40), on = hash01(i, step % 91 + 3) > 0.22;
      const chase = Math.max(0, Math.cos(t * 0.0007 - i / 72 * 6.283 * 2)), k = on ? 0.55 + 0.45 * Math.sin(t * (0.002 + hash01(i, 5) * 0.004) + i) + 0.4 * chase : 0.06 + 0.35 * chase;
      rimC.setHex(RIM[Math.floor(hash01(i, step % 53 + 9) * RIM.length) % RIM.length]).multiplyScalar(Math.min(1.2, k)); winMesh.setColorAt(i, rimC); }
    winMesh.instanceColor.needsUpdate = true; }
  function syncCrew() {                                                  // the crew with nothing to do walk Helix's own ring, as they do on the flat map
    const roster = opts && opts.getRoster ? opts.getRoster() : [], idle = roster.filter(a => a.role !== 'lead' && !a.working && !a.waiting), s2 = idle.map(a => a.role).join(',');
    if (s2 === crewSig || !rootG) return; crewSig = s2;
    for (const f of hubCrew) dropAgent(f); hubCrew = [];
    idle.forEach((a, i) => { const g = makeAgent(a.role), R = (k) => hash01(i + 1, k);
      g.userData.a0 = i / Math.max(1, idle.length) * 6.283 + R(2) * 0.4; g.userData.dir = R(1) < 0.5 ? -1 : 1; g.userData.v = 0.000018 + R(3) * 0.000028; g.userData.sw = 0.25 + R(4) * 0.4; g.userData.wv = 0.00008 + R(5) * 0.00012; g.userData.ph = R(6) * 6.28; g.userData.lane = 84 + R(7) * 40; g.userData.lv = 0.0001 + R(8) * 0.0002; g.userData.lp = R(9) * 6.28;
      rootG.add(g); hubCrew.push(g); }); }
  function moveCrew(t) { for (const g of hubCrew) { const u = g.userData, ang = u.a0 + u.dir * (u.v * t + u.sw * Math.sin(u.wv * t + u.ph)), r = u.lane + 8 * Math.sin(u.lv * t + u.lp), px = Math.cos(ang) * r, pz = Math.sin(ang) * r;
      const ang2 = u.a0 + u.dir * (u.v * (t + 40) + u.sw * Math.sin(u.wv * (t + 40) + u.ph)), r2 = u.lane + 8 * Math.sin(u.lv * (t + 40) + u.lp), qx = Math.cos(ang2) * r2, qz = Math.sin(ang2) * r2, moving = Math.hypot(qx - px, qz - pz) > 0.012;
      g.position.set(px, FLOOR_Y + (moving ? Math.abs(Math.sin(t * 0.012 + u.a0 * 9)) * 1.2 : 0), pz); faceAgent(g, qx - px, qz - pz, t, moving); } }
  let _oct = null; function octPts() { if (_oct) return _oct; const g = scene.children.find(c => c.userData && c.userData.oct); if (g) _oct = g.userData.oct; return _oct; }

  window.Station3D = {
    start(stageEl, o) { stage = stageEl; opts = o || {}; if (!renderer) setup(); canvas.style.display = 'block'; running = true; lastInteract = 0; resize(); if (!raf) frame(); },
    launch() { return startLaunch(); },
    look(o) { Object.assign(cam, o || {}); lastInteract = performance.now(); },
    stop() { running = false; if (canvas) canvas.style.display = 'none'; },
    update(mods) {
      if (!built || !mods) return; const s = mods.map(m => [m.key, m.label, m.color, m.expansion ? 1 : 0, m.state, (m.tasks || []).filter(t => t.status === 'running').map(t => t.role).join('+')].join('|')).join('~');
      if (s !== sig) { sig = s; rebuild(mods); }
    },
  };
})();
