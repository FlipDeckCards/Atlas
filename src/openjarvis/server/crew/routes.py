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


_BOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bots.png")


@router.get("/crew/bots.png")
async def crew_bots():
    """The crew's sprites: Michael's robot model rendered once per role (front, right, left)."""
    if not os.path.isfile(_BOTS):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(_BOTS, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


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
        for kind in ("stripe", "github", "marnyx"):
            st["connections"][kind].update(guide=kind, setup="" if st["connections"][kind]["connected"] else "Connect it step by step.")
        phone = bool(await tenant.get(engine.store, uid, "notify_url"))
        st["connections"]["phone"] = {"connected": phone, "built": True, "label": "Phone alerts and briefs", "actions": [], "guide": "phone",
                                      "setup": "" if phone else "Get alerts and your briefs on your phone."}
        # platform-level items are the owner's to set up; a subscriber never sees Render instructions
        if not st["connections"].get("web_research", {}).get("connected"):
            st["connections"].pop("web_research", None)
        etsy = st["connections"].get("etsy")
        if etsy and not etsy["connected"] and not etsy.get("can_connect"):
            etsy.update(built=False, setup="Coming soon.")
        st["guides"] = guide.GUIDES
        st["house_rules"] = (await tenant.get(engine.store, uid, "house_rules")) or ""
        st["usage"] = await engine.usage(uid)
    if owner:
        # the owner's keys live on Render, except Marnyx, which is pasted once on the Station like a subscriber's
        st["connections"]["marnyx"].update(guide="marnyx")
        st["guides"] = {"marnyx": guide.GUIDES["marnyx"]}
    st["businesses"] = await tenant.businesses(engine.store, uid) if not owner else [
        {"key": "flipdeck", "name": "FlipDeck", "url": "https://flipdeckcards.com", "color": "#ff6d00"},
        {"key": "marnyx", "name": "Marnyx", "url": "https://marnyx.onrender.com", "color": "#b06cff"},
        {"key": "deadzone", "name": "Deadzone", "url": "https://cybervoid-6tbo.onrender.com", "color": "#ff3344"}]
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
    keys = list(KNOWN) + [factory_key(m["project"], m["title"], m["goal"], m["id"]) for m in missions]
    out = await fin.report(uid, missions, keys)
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


# ── a subscriber's own connections (guided) and businesses ─────────────────────────────────────────

class ConnectIn(BaseModel):
    value: str = Field("", max_length=4000)
    extra: str = Field("", max_length=2000)


class BusinessIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=40)
    url: str = Field("", max_length=200)
    color: str = Field("", max_length=7)


@router.post("/api/crew/connect/{kind}")
async def crew_connect(kind: str, body: ConnectIn, request: Request):
    if kind not in ("stripe", "github", "phone", "rules", "marnyx"):
        return JSONResponse({"detail": "Unknown connection."}, status_code=404)
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import guide

    if await engine.is_owner(uid) and kind != "marnyx":
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
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import guide

    if await engine.is_owner(uid) and kind != "marnyx":
        return JSONResponse({"detail": "The owner's connections are set on Render."}, status_code=409)
    try:
        await guide.disconnect(engine.store, uid, kind)
    except guide.GuideError as exc:
        return _err(exc)
    await engine.store.add_event(uid, "connection", f"{kind.title()} disconnected.")
    return {"ok": True}


@router.post("/api/crew/businesses")
async def crew_business_add(body: BusinessIn, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server.crew import tenant

    if await engine.is_owner(uid):
        return JSONResponse({"detail": "The owner's businesses are FlipDeck, Marnyx and Deadzone; new ones are created by missions."}, status_code=409)
    try:
        items = await tenant.add_business(engine.store, uid, body.name, body.url, body.color)
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


@router.post("/api/crew/missions/{mission_id}/{action}")
async def crew_mission_action(mission_id: str, action: str, request: Request):
    g = await _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    fn = {"pause": engine.pause_mission, "resume": engine.resume_mission, "cancel": engine.cancel_mission}.get(action)
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
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in o["name"]) or "output.txt"
    return Response(o["content"], media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe}"'})


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
        return HTMLResponse(f"<p>{html.escape(str(exc))} <a href='/station'>Back to the Station</a></p>", status_code=400)
    except Exception:
        logger.exception("etsy connect failed")
        return HTMLResponse("<p>Etsy could not be connected right now. <a href='/station'>Back to the Station</a></p>", status_code=502)
    await engine.store.add_event(uid, "connection", "Etsy connected.")
    return RedirectResponse("/station?connected=etsy", status_code=303)
