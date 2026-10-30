"""HTTP routes for the crew and the Station page.

  GET  /station                              the Station page (HTML only; its data comes from /api/crew/state with the login token)
  GET  /api/crew/state                       missions, tasks, open questions, the ledger, spend, settings, connections
  POST /api/crew/missions                    start a mission from the Station's form (the tap is the confirmation)
  POST /api/crew/missions/{id}/{action}      pause | resume | cancel
  POST /api/crew/questions/{id}/answer       {answer} for a question, {approve: true|false, answer?} for a checkpoint or budget request
  GET  /api/crew/outputs/{id}                download one outbox file
  POST /api/crew/settings                    {night_shift?, paused?}
  POST /api/crew/run                         look for work now instead of waiting for the next tick
  GET  /api/crew/connect/etsy                the Etsy authorization URL to open (OAuth with PKCE)
  POST /api/crew/connect/etsy/disconnect     forget the Etsy tokens
  GET  /api/crew/connect/google              the Google (Gmail + Drive) authorization URL to open
  POST /api/crew/connect/google/disconnect   forget the Google login (and revoke it at Google)
  GET  /crew/connect/google/callback         Google sends the browser back here
  GET  /crew/connect/etsy/callback           Etsy sends the browser back here; the one-time state ties it to the user who started it

Every /api/crew route is behind the normal login (auth middleware) and is for owner accounts only.
"""

from __future__ import annotations

import html
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from pydantic import BaseModel, Field

from openjarvis.server import hardening
from openjarvis.server.crew import connections as conns
from openjarvis.server.crew import crew_access, crew_enabled
from openjarvis.server.crew.station import STATION_PAGE

logger = logging.getLogger(__name__)
router = APIRouter()


class MissionIn(BaseModel):
    goal: str = Field(..., min_length=8, max_length=4000)
    budget_usd: Optional[float] = None


class AnswerIn(BaseModel):
    answer: str = Field("", max_length=2000)
    approve: Optional[bool] = None


class SettingsIn(BaseModel):
    night_shift: Optional[bool] = None
    paused: Optional[bool] = None


def _who(request: Request):
    return getattr(request.state, "user_id", "") or "", getattr(request.state, "user_email", "") or ""


async def _gate(request: Request, need_engine: bool = True) -> Any:
    """(user_id, engine) for an owner or an active subscriber, or the error response to send."""
    uid, email = _who(request)
    if not uid or not email:
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    if not await crew_access(request):
        return JSONResponse({"detail": "Subscribe to unlock your own crew: agents that research, write, design, code and run your businesses."}, status_code=403)
    engine = getattr(request.app.state, "crew_engine", None)
    if need_engine and (engine is None or not crew_enabled()):
        return JSONResponse({"detail": "The crew is off. Set CREW_ENABLED=true on Render and redeploy."}, status_code=503)
    return uid, engine


def _err(exc: Exception, code: int = 400) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=code)


@router.get("/privacy", response_class=HTMLResponse)
async def public_privacy():
    from openjarvis.server.crew import legal

    return HTMLResponse(legal.privacy_html())


@router.get("/terms", response_class=HTMLResponse)
async def public_terms():
    from openjarvis.server.crew import legal

    return HTMLResponse(legal.terms_html())


@router.get("/")
@router.get("/station")
async def station_page():
    """The Station is Aibussol's home page (sign-in, Helix chat with voice, the crew, the library). The old page lives at /classic."""
    return HTMLResponse(STATION_PAGE, headers={"Cache-Control": "no-cache"})


_HELIX_PORTRAIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "helix.jpg")


@router.get("/crew/helix.jpg")
async def helix_portrait():
    """Helix's portrait for the Station's core (a public image, like the 3D head on the main page)."""
    if not os.path.isfile(_HELIX_PORTRAIT):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(_HELIX_PORTRAIT, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


_ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "art")
ART_FILES = {"asteroid1.png", "asteroid2.png", "asteroid3.png", "satellite.png", "helix-walk.png", "helix-views.png"}      # Michael's own artwork for the Station's sky and deck


@router.get("/crew/art/{name}")
async def station_art(name: str):
    """The artwork the Station draws when it exists (asteroids, the satellite, Helix walking). A missing file is a plain 404 and the Station
    draws its built-in version instead."""
    path = os.path.join(_ART_DIR, name)
    if name not in ART_FILES or not os.path.isfile(path):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MANIFEST = {
    "name": "Aibussol", "short_name": "Helix", "description": "Helix and your crew, one tap from your home screen.",
    "start_url": "/station", "scope": "/", "display": "standalone", "orientation": "portrait-primary",
    "background_color": "#050508", "theme_color": "#050508",
    "icons": [{"src": "/crew/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "/crew/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}
SERVICE_WORKER = ("// Aibussol: installable app shell. It never caches anything, so the Station and Helix are always live.\n"
                  "self.addEventListener('install', () => self.skipWaiting());\n"
                  "self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));\n"
                  "self.addEventListener('fetch', () => {});\n")


@router.get("/manifest.webmanifest")
async def manifest():
    """What lets a phone install Aibussol as an app (name, icon, full screen, opens on the Station)."""
    return JSONResponse(MANIFEST, media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sw.js")
async def service_worker():
    return Response(SERVICE_WORKER, media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/crew/icons/{icon}.png")
async def crew_icons(icon: str):
    if icon not in ("icon-192", "icon-512", "apple-touch-icon") or not os.path.isfile(os.path.join(_ASSETS, icon + ".png")):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(os.path.join(_ASSETS, icon + ".png"), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/crew/three.min.js")
async def crew_three():
    """three.js, served from here (no outside host), loaded only when the Station is switched to its 3D view."""
    return FileResponse(os.path.join(_ASSETS, "three.min.js"), media_type="application/javascript", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/crew/helix.glb")
async def crew_helix_model():
    """Helix's own 3D model (the 3D view falls back to a hologram of her portrait when the file is not there)."""
    path = os.path.join(_ASSETS, "helix.glb")
    if not os.path.isfile(path):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(path, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/crew/station3d.js")
async def crew_station3d():
    return FileResponse(os.path.join(_ASSETS, "station3d.js"), media_type="application/javascript", headers={"Cache-Control": "no-cache"})


_BOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bots.png")


@router.get("/crew/bots.png")
async def crew_bots():
    """The crew's sprites: Michael's robot model rendered once per role (front, right, left)."""
    if not os.path.isfile(_BOTS):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(_BOTS, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


# ── automatic delivery of digital products sold with a crew payment link (public: the buyer has no account) ──

async def _paid_delivery(request: Request, session_id: str):
    """-> (mission_id, [file names], buyer email) for a paid Checkout Session of a crew payment link with delivery on, else None."""
    import re as _re

    import httpx

    if not _re.fullmatch(r"cs_(live|test)_[A-Za-z0-9]{10,200}", session_id or ""):
        return None
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    engine = getattr(request.app.state, "crew_engine", None)
    if not key or engine is None:
        return None
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://api.stripe.com/v1/checkout/sessions/{session_id}", auth=(key, ""))
        if r.status_code != 200:
            return None
        sess = r.json()
        if sess.get("payment_status") not in ("paid", "no_payment_required") or not sess.get("payment_link"):
            return None
        r = await c.get(f"https://api.stripe.com/v1/payment_links/{sess['payment_link']}", auth=(key, ""))
        if r.status_code != 200:
            return None
        meta = r.json().get("metadata") or {}
    files = [f for f in str(meta.get("deliver_files") or "").split(",") if f]
    if meta.get("app") != "crew" or not meta.get("mission") or not files:
        return None
    return meta["mission"], files, ((sess.get("customer_details") or {}).get("email") or "")


def _delivered_name(name: str, content: str) -> str:
    # a crew "pdf" that is really text is delivered as .txt, so it opens instead of looking broken
    return name[:-4] + ".txt" if name.lower().endswith(".pdf") and not content.startswith("%PDF") else name


@router.get("/deliver")
async def deliver_page(request: Request, session_id: str = ""):
    got = await _paid_delivery(request, session_id)
    if not got:
        return HTMLResponse("<h2 style='font-family:sans-serif'>We could not find a paid order for this link.</h2>"
                            "<p style='font-family:sans-serif'>If you just paid, refresh in a few seconds. Still stuck? Reply to your Stripe receipt email.</p>",
                            status_code=404)
    mission, files, _ = got
    engine = request.app.state.crew_engine
    outs = {o["name"]: o for o in await engine.store.list_outputs(mission)}
    rows = []
    for f in files:
        if f in outs:
            full = await engine.store.get_output(outs[f]["id"])
            rows.append(f'<li><a href="/deliver/file?session_id={html.escape(session_id)}&amp;name={html.escape(f)}">'
                        f'{html.escape(_delivered_name(f, (full or {}).get("content") or ""))}</a></li>')
    page = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Your download</title><style>body{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.6}"
            "a{color:#0a66c2}</style></head><body><h1>Thank you for your purchase</h1><p>Your files are ready. Save them now; this page "
            "stays available from your receipt link.</p><ul>" + "".join(rows) + "</ul></body></html>")
    return HTMLResponse(page)


@router.get("/deliver/file")
async def deliver_file(request: Request, session_id: str = "", name: str = ""):
    got = await _paid_delivery(request, session_id)
    if not got or name not in got[1]:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    engine = request.app.state.crew_engine
    out = next((o for o in await engine.store.list_outputs(got[0]) if o["name"] == name), None)
    full = await engine.store.get_output(out["id"]) if out else None
    if not full:
        return JSONResponse({"detail": "That file is no longer available."}, status_code=404)
    content = full.get("content") or ""
    fname = _delivered_name(name, content)
    kind = "text/markdown" if fname.endswith(".md") else "text/csv" if fname.endswith(".csv") else "text/plain"
    return Response(content=content.encode("utf-8"), media_type=kind + "; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname.replace(chr(34), "")}"', "Cache-Control": "private, no-store"})


_PLAN_CACHE: dict = {}


@router.get("/crew/plan.json")
async def crew_plan():
    """Public: the plan price (read from Stripe, cached an hour) and the usage terms, for the landing page."""
    import time

    import httpx

    from openjarvis.server import billing
    from openjarvis.server.crew import usage

    hit = _PLAN_CACHE.get("plan")
    if hit and time.monotonic() - hit[0] < 3600:
        return hit[1]
    p = usage.plan()
    out = {"included_usd": p["included_usd"], "multiplier": p["multiplier"], "cap_usd": p["cap_usd"], "payg": p["payg"], "price_usd": None, "interval": "month"}
    cfg = billing.config()
    if cfg["secret_key"] and cfg["price"]:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"https://api.stripe.com/v1/prices/{cfg['price']}", auth=(cfg["secret_key"], ""))
            if r.status_code == 200:
                d = r.json()
                if d.get("unit_amount") is not None:
                    out["price_usd"] = round(d["unit_amount"] / 100, 2)
                out["interval"] = (d.get("recurring") or {}).get("interval") or "month"
        except Exception:
            logger.exception("plan price lookup failed")
    _PLAN_CACHE["plan"] = (time.monotonic(), out)
    return out


OWNER_BUSINESSES = (
    {"key": "flipdeck", "name": "FlipDeck", "url": "https://flipdeckcards.com", "color": "#ff6d00",
     "description": "FlipDeck sells trading-card flip decks and related products through its own website."},
    {"key": "marnyx", "name": "Marnyx", "url": "https://marnyx.onrender.com", "color": "#b06cff",
     "description": "Marnyx is an autonomous marketing app that drafts and publishes social posts for small businesses."},
    {"key": "deadzone", "name": "Deadzone", "url": "https://cybervoid-6tbo.onrender.com", "color": "#ff3344",
     "description": "Deadzone is a mobile game studio project, with its backend and iOS build kept here."},
    {"key": "polymarket", "name": "Polymarket Desk", "url": "https://polymarket.us", "color": "#00e5ff",
     "description": "Polymarket Desk runs four trading bots that make markets in Polymarket US's Up/Down contracts: BTC on 15-minute windows, ETH, SOL and XRP on 5-minute windows."})
OWNER_FIXED_KEYS = {b["key"] for b in OWNER_BUSINESSES}


async def owner_businesses(engine, uid: str):
    """The owner's rows on the Station: the fixed four, then any business Helix or the owner added."""
    from openjarvis.server.crew import tenant

    stored = await tenant.businesses(engine.store, uid)
    return list(OWNER_BUSINESSES) + [b for b in stored if b["key"] not in OWNER_FIXED_KEYS]


class CatalogEdit(BaseModel):
    add_name: str = Field("", max_length=120)
    add_url: str = Field("", max_length=300)
    remove: str = Field("", max_length=140)


@router.get("/api/crew/catalog/{key}")
async def crew_catalog(key: str, request: Request):
    """What a factory has for sale or live: Etsy listings, Stripe products, the games on the Deadzone site, store apps."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import catalog, tenant

    owner = await engine.is_owner(uid)
    if key == "stripe" and not owner:
        return {"items": [], "note": ""}
    biz = await owner_businesses(engine, uid) if owner else await tenant.businesses(engine.store, uid)
    return await catalog.for_factory(key, store=engine.store, uid=uid, env=await engine.env_for(uid),
                                     cf=engine.client_factory or conns._default_client, businesses=biz)


@router.post("/api/crew/catalog/{key}")
async def crew_catalog_edit(key: str, body: CatalogEdit, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import catalog

    if key not in catalog.MANUAL:
        return JSONResponse({"detail": "That list comes from the platform itself."}, status_code=400)
    return await catalog.manual_edit(engine.store, uid, key, add={"name": body.add_name, "url": body.add_url} if body.add_name.strip() else None,
                                     remove=body.remove)


@router.get("/api/crew/state")
async def crew_state(request: Request):
    g = await _gate(request, need_engine=False)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if engine is None or not crew_enabled():
        return {"enabled": False, "detail": "The crew is off. Set CREW_ENABLED=true on Render and redeploy."}
    st = await engine.state(uid)
    from openjarvis.server.crew import guide, tenant, workbench

    owner = await engine.is_owner(uid)
    envx = await engine.env_for(uid)
    st["owner"] = owner
    st["connections"] = await conns.status(engine.store, uid, envx)
    if not owner:
        # a subscriber's own connections, each with its guided steps
        for kind in ("stripe", "github", "marnyx", "google_play"):
            st["connections"][kind].update(guide=kind, setup="" if st["connections"][kind]["connected"] else "Connect it step by step.")
        phone = bool(await tenant.get(engine.store, uid, "notify_url"))
        st["connections"]["phone"] = {"connected": phone, "built": True, "label": "Phone alerts and briefs", "actions": [], "guide": "phone",
                                      "setup": "" if phone else "Get alerts and your briefs on your phone."}
        # platform-level items are the owner's to set up; a subscriber never sees Render instructions
        if not st["connections"].get("web_research", {}).get("connected"):
            st["connections"].pop("web_research", None)
        for k_ in ("etsy", "google"):                       # platform-level OAuth apps: a subscriber sees "Coming soon" until the owner has set the app up
            c_ = st["connections"].get(k_)
            if c_ and not c_["connected"] and not c_.get("can_connect"):
                c_.update(built=False, setup="Coming soon.")
        st["guides"] = guide.GUIDES
        st["house_rules"] = (await tenant.get(engine.store, uid, "house_rules")) or ""
        st["usage"] = await engine.usage(uid)
    if owner:
        # the owner's keys live on Render, except Marnyx, which is pasted once on the Station like a subscriber's
        st["connections"]["marnyx"].update(guide="marnyx")
        st["connections"]["google_play"].update(guide="google_play", setup="" if st["connections"]["google_play"]["connected"] else "Connect it step by step.")
        st["guides"] = {"marnyx": guide.GUIDES["marnyx"], "google_play": guide.GUIDES["google_play"]}
    st["businesses"] = await tenant.businesses(engine.store, uid) if not owner else await owner_businesses(engine, uid)
    if not owner:
        st["products"] = await engine.products_for(uid, st["businesses"])

    pc, paired = engine.presence.status(uid), bool(await engine.store.get_secret(uid, workbench.TOKEN_SECRET))
    st["connections"]["workbench"].update(connected=bool(pc.get("online")), paired=paired, info=pc,
                                          setup="" if pc.get("online") else ("Paired; start the helper on your PC." if paired else "Pair your PC so Rig can build in your real projects with Claude Code."))
    st["enabled"] = True
    return st


@router.get("/api/crew/finance")
async def crew_finance(request: Request):
    """P&L per factory and in total (crew/finance.py), the products' live status and Aibussol's own counts."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server import admin_analytics
    from openjarvis.server.crew.finance import KNOWN, Finance, factory_key

    pool = getattr(getattr(request.app.state, "session_store", None), "_pool", None)
    missions = await engine.store.list_missions(uid, 200)
    if not await engine.is_owner(uid):
        # a subscriber: their own Stripe (if connected) and their crew's spend, nothing of the owner's
        from openjarvis.server.crew import tenant

        biz = await tenant.businesses(engine.store, uid)
        known = tuple(b["key"] for b in biz)
        envx = await engine.env_for(uid)
        cache = getattr(request.app.state, "crew_finance_users", None)
        if cache is None:
            cache = request.app.state.crew_finance_users = {}
        fin_u = cache.get(uid)
        sub_env = {"STRIPE_SECRET_KEY": envx.get("STRIPE_SECRET_KEY", ""), "CREW_STRIPE_UNTAGGED": known[0] if known else "business"}
        if fin_u is None or fin_u.env != sub_env:
            fin_u = cache[uid] = Finance(engine.store, pool=None, env=sub_env)
        keys = list(known) + [factory_key(m["project"], m["title"], m["goal"], m["id"], known) for m in missions]
        out = await fin_u.report(uid, missions, keys, owner=False)
        out["products"] = await engine.products_for(uid, biz)
        out["product_stats"], out["aibussol_stats"] = {}, None
        return out
    fin = getattr(request.app.state, "crew_finance", None)
    if fin is None:
        fin = request.app.state.crew_finance = Finance(engine.store, pool=pool)
    keys = list(KNOWN) + ["polymarket"] + [factory_key(m["project"], m["title"], m["goal"], m["id"]) for m in missions]
    trading = None
    if pool is not None:
        try:
            from openjarvis.server.crew import desk

            per = await desk.live_pnl_all(pool)                                # live trading profit only; paper never counts as money
            trading = {"polymarket": {k: round(sum(v[k] for v in per.values()), 2) for k in ("today", "d30")}}    # one factory, all three desks
        except Exception:
            logger.exception("desk P&L unavailable")
    out = await fin.report(uid, missions, keys, trading=trading)
    out["products"] = engine.products
    from openjarvis.server.crew.product_stats import ProductStats

    ps = getattr(request.app.state, "crew_product_stats", None)
    if ps is None:
        ps = request.app.state.crew_product_stats = ProductStats(pool=pool)
    try:
        out["product_stats"] = await ps.all()
    except Exception:
        logger.exception("product stats unavailable")
        out["product_stats"] = {}
    try:
        out["aibussol_stats"] = await admin_analytics.gather(pool) if pool is not None else None
    except Exception:
        logger.exception("aibussol stats unavailable")
        out["aibussol_stats"] = None
    return out


# ── the Polymarket Desk (owner only): scoreboard, trade log, pause / resume ───────────────────────────

class DeskControlIn(BaseModel):
    action: str = Field(..., max_length=20)
    asset: str = Field("btc", max_length=8)


async def _desk_gate(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if not await engine.is_owner(uid):
        return JSONResponse({"detail": "Owner only."}, status_code=403)
    pool = getattr(getattr(request.app.state, "session_store", None), "_pool", None) or getattr(engine, "pool", None)
    if pool is None:
        return JSONResponse({"detail": "No database."}, status_code=503)
    return uid, engine, pool


@router.get("/api/crew/desk")
async def crew_desk(request: Request, limit: int = 50, asset: str = "btc"):
    """The desk's scoreboard (win rate, total profit, trades; paper and live kept apart), its trade log and its latest status."""
    g = await _desk_gate(request)
    if isinstance(g, JSONResponse):
        return g
    from openjarvis.server.crew import desk

    return await desk.summary(g[2], trade_limit=max(1, min(limit, 500)), asset=(asset or "btc").lower())


@router.post("/api/crew/desk/control")
async def crew_desk_control(body: DeskControlIn, request: Request):
    """Pause, resume, kill or back to paper from the Station. Going live is never done here: it is an Approve card from Helix."""
    g = await _desk_gate(request)
    if isinstance(g, JSONResponse):
        return g
    from openjarvis.server.crew import desk

    asset = (body.asset or "btc").lower()
    if asset not in desk.KEYS:
        return JSONResponse({"detail": "Unknown desk."}, status_code=400)
    if body.action not in ("pause", "resume", "flatten", "paper", "go_live", "reset_day"):
        return JSONResponse({"detail": "Unknown action."}, status_code=400)
    out = await desk.control(g[2], body.action, asset=asset)
    if "needs_approval" in out:                       # going live is never done here: it becomes an Approve card in the Helix inbox
        await g[1].store.add_question("", "", g[0], "ops", out["needs_approval"], ["Approve", "Decline"], out["action"])
        return {"done": "An Approve card is waiting in the Helix inbox. Tap Approve there to go live."}
    await g[1].store.add_event(g[0], "ops", f"{desk.NAMES[asset]}: {out['done']}")
    return out


# ── a subscriber's own connections (guided) and businesses ─────────────────────────────────────────

class ConnectIn(BaseModel):
    value: str = Field("", max_length=4000)
    extra: str = Field("", max_length=2000)


class BusinessIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=40)
    url: str = Field("", max_length=200)
    color: str = Field("", max_length=7)
    description: str = Field("", max_length=300)


@router.post("/api/crew/connect/{kind}")
async def crew_connect(kind: str, body: ConnectIn, request: Request):
    if kind not in ("stripe", "github", "phone", "rules", "marnyx", "google_play"):
        return JSONResponse({"detail": "Unknown connection."}, status_code=404)
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import guide

    if await engine.is_owner(uid) and kind not in ("marnyx", "google_play"):
        return JSONResponse({"detail": "The owner's connections are set on Render (Atlas → Environment)."}, status_code=409)
    hit = hardening.enforce({"chat_user": uid})
    if hit:
        return JSONResponse({"detail": "Too many tries. Wait a minute."}, status_code=429, headers={"Retry-After": str(hit[1])})
    try:
        msg = await guide.test_and_save(engine.store, uid, kind, body.value, body.extra, engine.client_factory)
    except guide.GuideError as exc:
        return _err(exc)
    except Exception:
        logger.exception("guided connect failed")
        return JSONResponse({"detail": "That service could not be reached just now. Try again."}, status_code=502)
    await engine.store.add_event(uid, "connection", msg)
    return {"ok": True, "message": msg}


@router.post("/api/crew/connect/{kind}/disconnect")
async def crew_connect_off(kind: str, request: Request):
    if kind == "etsy":                                     # Etsy is OAuth, with its own route below
        return await crew_disconnect_etsy(request)
    if kind == "google":                                   # so is Google
        return await crew_disconnect_google(request)
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import guide

    if await engine.is_owner(uid) and kind not in ("marnyx", "google_play"):
        return JSONResponse({"detail": "The owner's connections are set on Render."}, status_code=409)
    try:
        await guide.disconnect(engine.store, uid, kind)
    except guide.GuideError as exc:
        return _err(exc)
    await engine.store.add_event(uid, "connection", f"{kind.title()} disconnected.")
    return {"ok": True}


class RenderKeyIn(BaseModel):
    api_key: str = Field("", max_length=200)


@router.post("/api/crew/render/connect")
async def crew_render_connect(body: RenderKeyIn, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import tenant

    key = body.api_key.strip()
    if len(key) < 12:
        return JSONResponse({"detail": "That does not look like a Render API key."}, status_code=400)
    try:
        async with (engine.client_factory or conns._default_client)() as c:
            r = await c.get(f"{conns.RENDER_API}/services", headers={"Authorization": f"Bearer {key}"}, params={"limit": 1})
    except Exception:
        return JSONResponse({"detail": "Render could not be reached just now. Try again."}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"detail": "Render did not accept that key. Create a new API key in Render and paste it again."}, status_code=400)
    await tenant.put(engine.store, uid, "render_key", key, engine.env)
    engine.drop_products_cache(uid)
    await engine.store.add_event(uid, "connection", "Render connected.")
    return {"ok": True}


@router.post("/api/crew/render/disconnect")
async def crew_render_disconnect(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import tenant

    await tenant.put(engine.store, uid, "render_key", "", engine.env)
    engine.drop_products_cache(uid)
    await engine.store.add_event(uid, "connection", "Render disconnected.")
    return {"ok": True}


@router.post("/api/crew/businesses")
async def crew_business_add(body: BusinessIn, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import tenant

    if tenant.slug(body.name) in OWNER_FIXED_KEYS:
        return JSONResponse({"detail": "That business is already on the Station."}, status_code=409)
    try:
        items = await tenant.add_business(engine.store, uid, body.name, body.url, body.color, body.description)
    except ValueError as exc:
        return _err(exc)
    await engine.store.add_event(uid, "business", f"Added {body.name.strip()} to the Station.")
    engine.drop_products_cache(uid)
    return {"businesses": items}


@router.post("/api/crew/businesses/{key}/remove")
async def crew_business_remove(key: str, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import tenant

    items = await tenant.remove_business(engine.store, uid, key)
    engine.drop_products_cache(uid)
    return {"businesses": items}


@router.post("/api/crew/missions")
async def crew_start(body: MissionIn, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    hit = hardening.enforce({"chat_user": uid})
    if hit:
        return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429, headers={"Retry-After": str(hit[1])})
    try:
        m = await engine.start_mission(uid, body.goal, body.budget_usd)
    except ValueError as exc:
        return _err(exc)
    return JSONResponse({"mission": m}, status_code=201)


class MissionBudgetIn(BaseModel):
    budget_usd: float = Field(..., ge=0.05, le=10000)


@router.post("/api/crew/missions/{mission_id}/budget")
async def crew_mission_budget(mission_id: str, body: MissionBudgetIn, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    try:
        return {"mission": await engine.set_mission_budget(uid, mission_id, body.budget_usd)}
    except LookupError as exc:
        return _err(exc, 404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/crew/missions/{mission_id}/{action}")
async def crew_mission_action(mission_id: str, action: str, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    fn = {"pause": engine.pause_mission, "resume": engine.resume_mission, "cancel": engine.cancel_mission, "report": engine.write_report}.get(action)
    if fn is None:
        return JSONResponse({"detail": "Unknown action."}, status_code=404)
    try:
        return {"mission": await fn(uid, mission_id)}
    except LookupError as exc:
        return _err(exc, 404)


@router.post("/api/crew/questions/{question_id}/answer")
async def crew_answer(question_id: str, body: AnswerIn, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    try:
        return await engine.answer(uid, question_id, answer=body.answer, approve=body.approve)
    except LookupError as exc:
        return _err(exc, 404)
    except ValueError as exc:
        return _err(exc, 409 if "already" in str(exc) else 400)


@router.get("/api/crew/outputs/{output_id}")
async def crew_output(output_id: str, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    o = await engine.store.get_output(output_id)
    if not o or o["user_id"] != uid:
        return JSONResponse({"detail": "Not found."}, status_code=404)
    return output_response(o)


def output_response(o: Dict[str, Any]) -> Response:
    """The file as a download. A binary file (a book PDF) is kept as 'base64:' text in the outbox and sent back as the real file."""
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in o["name"]) or "output.txt"
    head = {"Content-Disposition": f'attachment; filename="{safe}"'}
    if str(o["content"]).startswith("base64:"):
        import base64
        import binascii

        try:
            raw = base64.b64decode(o["content"][7:])
        except (binascii.Error, ValueError):
            return JSONResponse({"detail": "That file is damaged."}, status_code=500)
        return Response(raw, media_type="application/pdf" if safe.lower().endswith(".pdf") else "application/octet-stream", headers=head)
    return Response(o["content"], media_type="text/plain; charset=utf-8", headers=head)


class EngineIn(BaseModel):
    mode: str = Field(..., max_length=10)


@router.post("/api/crew/helix-engine")
async def crew_helix_engine(body: EngineIn, request: Request):
    """Pick the engine Helix thinks on from the roster: auto, haiku or sonnet. Owner only."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if not await engine.is_owner(uid):
        return JSONResponse({"detail": "Only the owner picks Helix's engine."}, status_code=403)
    try:
        mode = await engine.set_helix_mode(uid, body.mode.strip().lower())
    except ValueError as exc:
        return _err(exc)
    await engine.store.add_event(uid, "settings", f"Helix engine set to {mode}.")
    return {"mode": mode}


class CreditIn(BaseModel):
    balance_usd: float = Field(..., ge=0, le=1_000_000)
    warn_usd: Optional[float] = Field(None, ge=0, le=1_000_000)


@router.get("/api/crew/credits")
async def crew_credits(request: Request):
    """Credits left on the paid AI APIs for the System Vitals box (crew/credits.py). Owner only."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if not await engine.is_owner(uid):
        return JSONResponse({"detail": "Credits are shown to the owner only."}, status_code=403)
    from openjarvis.server.crew import credits

    pool = getattr(getattr(request.app.state, "session_store", None), "_pool", None)
    return await credits.report(engine.store, uid, await engine.env_for(uid), pool=pool)


@router.get("/api/crew/spend")
async def crew_spend(request: Request, days: int = 30):
    """Where the AI money went over the last N days (crew/spend.py). Owner only."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if not await engine.is_owner(uid):
        return JSONResponse({"detail": "The spend breakdown is shown to the owner only."}, status_code=403)
    from openjarvis.server.crew import engine as eng_mod
    from openjarvis.server.crew import spend

    pool = getattr(getattr(request.app.state, "session_store", None), "_pool", None)
    names = {k: v.get("name", k) for k, v in eng_mod.ROLES.items()}
    return await spend.report(engine.store, uid, pool=pool, env=await engine.env_for(uid), role_names=names, days=max(1, min(int(days), 90)))


@router.post("/api/crew/credits/{name}")
async def crew_credits_set(name: str, body: CreditIn, request: Request):
    """Save the balance read off the Anthropic or Google billing page."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if not await engine.is_owner(uid):
        return JSONResponse({"detail": "Credits are shown to the owner only."}, status_code=403)
    import datetime as dt

    from openjarvis.server.crew import credits

    try:
        rec = await credits.save_balance(engine.store, uid, name, body.balance_usd, body.warn_usd, dt.datetime.now(dt.timezone.utc))
    except ValueError as exc:
        return _err(exc)
    return {"saved": rec}


@router.post("/api/crew/settings")
async def crew_settings(body: SettingsIn, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    cur = await engine.store.get_settings(uid)
    ns = cur["night_shift"] if body.night_shift is None else body.night_shift
    ps = cur["paused"] if body.paused is None else body.paused
    await engine.store.set_settings(uid, ns, ps)
    if ns != cur["night_shift"] or ps != cur["paused"]:
        await engine.store.add_event(uid, "settings", f'Night Shift {"on" if ns else "off"}; crew {"paused" if ps else "running"}.')
    return {"settings": {"night_shift": ns, "paused": ps}}


@router.post("/api/crew/brief/run")
async def crew_brief_now(request: Request):
    """Herald makes a brief right now (stored in the feed, posted to Helix chat, pushed to the phone)."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if engine.herald is None:
        return JSONResponse({"detail": "Herald is not running."}, status_code=503)
    if not await engine.is_owner(uid):
        # a subscriber's own brief: their money, sites and crew only, into their ledger and onto their own phone
        from openjarvis.server.crew import herald as hd
        from openjarvis.server.crew import tenant

        fin = await crew_finance(request)
        fin = fin if isinstance(fin, dict) else None
        names = {b_["key"]: b_["name"] for b_ in await tenant.businesses(engine.store, uid)}
        crew = await engine.herald._crew_counts(uid)
        glance = hd.glance_lines(fin, (fin or {}).get("products") or {}, None, crew, names=names, owner=False)
        act = (f"{crew['waiting']} item(s) wait for you in Helix's inbox." if crew.get("waiting") else "Nothing waits on you.")
        await engine.store.add_event(uid, "brief", "Brief: " + " ".join(x.lstrip("- ") for x in glance)[:900])
        await engine._notify(uid, "Your Aibussol brief", hd.push_text(glance, act))
        return {"ok": True, "glance": glance, "act": act, "pushed": bool(await tenant.get(engine.store, uid, "notify_url"))}
    out = await engine.herald.run()
    return out if out.get("ok") else JSONResponse(out, status_code=409 if "already" in out.get("detail", "") else 502)


# ── the Workbench: Michael's PC as Rig (crew/workbench.py) ─────────────────────────────────────────────

_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench_helper.py")


@router.post("/api/crew/workbench/pair")
async def workbench_pair(request: Request):
    """A new helper token for this account, shown once (any older token stops working)."""
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import workbench

    token = await workbench.pair(engine.store, uid)
    await engine.store.add_event(uid, "connection", "Workbench paired (a new PC token was made).")
    return {"token": token, "download": "/crew/workbench/aibussol_workbench.py"}


@router.post("/api/crew/workbench/unpair")
async def workbench_unpair(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import workbench

    await workbench.unpair(engine.store, uid)
    await engine.store.add_event(uid, "connection", "Workbench unpaired.")
    return {"ok": True}


@router.get("/crew/workbench/aibussol_workbench.py")
async def workbench_download():
    """The helper script (no secrets in it: the token is given on the command line once)."""
    return FileResponse(_HELPER, media_type="text/x-python", filename="aibussol_workbench.py", headers={"Cache-Control": "no-cache"})


async def _pc(request: Request):
    """(user_id, engine) for a helper request with a valid PC token, else an error response."""
    engine = getattr(request.app.state, "crew_engine", None)
    if engine is None:
        return JSONResponse({"detail": "The crew is off."}, status_code=503)
    hit = hardening.enforce({"chat_ip": hardening.client_ip(request)})
    if hit:
        return JSONResponse({"detail": "Too many requests."}, status_code=429, headers={"Retry-After": str(hit[1])})
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    from openjarvis.server.crew import workbench

    uid = await workbench.verify(engine.store, token) if scheme.lower() == "bearer" else None
    if not uid:
        return JSONResponse({"detail": "Not paired. Pair your PC on the Station."}, status_code=401)
    return uid, engine


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@router.post("/crew/workbench/hello")
async def workbench_hello(request: Request):
    g = await _pc(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    engine.presence.hello(uid, await _json(request))
    return {"ok": True}


@router.post("/crew/workbench/next")
async def workbench_next(request: Request):
    g = await _pc(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    return {"job": await engine.workbench_next(uid)}


@router.post("/crew/workbench/{task_id}/progress")
async def workbench_progress(task_id: str, request: Request):
    g = await _pc(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    try:
        await engine.workbench_progress(uid, task_id, str((await _json(request)).get("note") or "")[:300])
    except (LookupError, ValueError) as exc:
        return _err(exc, 404 if isinstance(exc, LookupError) else 409)
    return {"ok": True}


@router.post("/crew/workbench/{task_id}/done")
async def workbench_done(task_id: str, request: Request):
    g = await _pc(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    body = await _json(request)
    try:
        await engine.workbench_done(uid, task_id, bool(body.get("ok")), str(body.get("summary") or ""), int(body.get("minutes") or 0))
    except (LookupError, ValueError) as exc:
        return _err(exc, 404 if isinstance(exc, LookupError) else 409)
    return {"ok": True}


@router.post("/api/crew/run")
async def crew_run_now(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    blocked = await engine.may_run(uid)
    started = 0 if blocked else await engine.tick()
    return {"started": started, "blocked": blocked}


@router.get("/api/crew/connect/etsy")
async def crew_connect_etsy(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, _ = g
    if not conns.etsy_app_configured():
        return JSONResponse({"detail": conns.SETUP["etsy"]}, status_code=409)
    return {"url": conns.etsy_connect_url(uid)}


@router.post("/api/crew/connect/etsy/disconnect")
async def crew_disconnect_etsy(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    for name in ("etsy_access", "etsy_refresh", "etsy_expires", "etsy_shop_id"):
        await engine.store.delete_secret(uid, name)
    await engine.store.add_event(uid, "connection", "Etsy disconnected.")
    return {"ok": True}


@router.get("/crew/connect/etsy/callback")
async def crew_etsy_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    engine = getattr(request.app.state, "crew_engine", None)
    if engine is None:
        return HTMLResponse("<p>The crew is off.</p>", status_code=503)
    if error:
        return HTMLResponse(f"<p>Etsy did not connect: {html.escape(error[:200])}. <a href='/station'>Back to the Station</a></p>", status_code=400)
    try:
        uid = await conns.etsy_finish_connect(engine.store, state, code)
    except conns.ConnectionError_ as exc:
        logger.warning("etsy connect refused: %s", exc)
        return HTMLResponse(f"<p>{html.escape(str(exc))} <a href='/station'>Back to the Station</a></p>", status_code=400)
    except Exception:
        logger.exception("etsy connect failed")
        return HTMLResponse("<p>Etsy could not be connected right now. <a href='/station'>Back to the Station</a></p>", status_code=502)
    note = await engine.store.get_secret(uid, "etsy_shop_note")
    await engine.store.add_event(uid, "connection", note or "Etsy connected.")
    return RedirectResponse("/station?connected=etsy", status_code=303)



@router.get("/api/crew/connect/google")
async def crew_connect_google(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    envx = await engine.env_for(uid)
    if not conns.google_app_configured(envx):
        return JSONResponse({"detail": conns.SETUP["google"] if await engine.is_owner(uid) else "Google is coming soon."}, status_code=409)
    return {"url": conns.google_connect_url(uid, envx)}


@router.post("/api/crew/connect/google/disconnect")
async def crew_disconnect_google(request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    refresh = await engine.store.get_secret(uid, "google_refresh")
    if refresh:                                             # ask Google to cancel the login too, not just forget it here
        try:
            async with (engine.client_factory or conns._default_client)() as c:
                await c.post("https://oauth2.googleapis.com/revoke", data={"token": refresh})
        except Exception:
            logger.warning("google revoke failed (the saved login is forgotten anyway)")
    for name in ("google_access", "google_refresh", "google_expires", "google_email"):
        await engine.store.delete_secret(uid, name)
    await engine.store.add_event(uid, "connection", "Google disconnected.")
    return {"ok": True}


@router.get("/crew/connect/google/callback")
async def crew_google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    engine = getattr(request.app.state, "crew_engine", None)
    if engine is None:
        return HTMLResponse("<p>The crew is off.</p>", status_code=503)
    if error:
        return HTMLResponse(f"<p>Google did not connect: {html.escape(error[:200])}. <a href='/station'>Back to the Station</a></p>", status_code=400)
    try:
        pending = conns.GOOGLE_STATES._items.get(state or "")
        envx = await engine.env_for(pending["user_id"]) if pending else None
        uid = await conns.google_finish_connect(engine.store, state, code, envx, client_factory=engine.client_factory)
    except conns.ConnectionError_ as exc:
        logger.warning("google connect refused: %s", exc)
        return HTMLResponse(f"<p>{html.escape(str(exc))} <a href='/station'>Back to the Station</a></p>", status_code=400)
    except Exception:
        logger.exception("google connect failed")
        return HTMLResponse("<p>Google could not be connected right now. <a href='/station'>Back to the Station</a></p>", status_code=502)
    await engine.store.add_event(uid, "connection", "Google (Gmail and Drive) connected.")
    return RedirectResponse("/station?connected=google", status_code=303)
