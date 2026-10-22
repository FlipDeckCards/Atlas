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
from openjarvis.server.crew import crew_allowed, crew_enabled
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


def _gate(request: Request, need_engine: bool = True) -> Any:
    """(user_id, engine) for an owner, or the error response to send."""
    uid, email = _who(request)
    if not uid or not email:
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    if not crew_allowed(email):
        return JSONResponse({"detail": "The crew is for the owner account."}, status_code=403)
    engine = getattr(request.app.state, "crew_engine", None)
    if need_engine and (engine is None or not crew_enabled()):
        return JSONResponse({"detail": "The crew is off. Set CREW_ENABLED=true on Render and redeploy."}, status_code=503)
    return uid, engine


def _err(exc: Exception, code: int = 400) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=code)


@router.get("/station")
async def station_page():
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


@router.get("/api/crew/state")
async def crew_state(request: Request):
    g = _gate(request, need_engine=False)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    if engine is None or not crew_enabled():
        return {"enabled": False, "detail": "The crew is off. Set CREW_ENABLED=true on Render and redeploy."}
    st = await engine.state(uid)
    st["connections"] = await conns.status(engine.store, uid)
    st["enabled"] = True
    return st


@router.get("/api/crew/finance")
async def crew_finance(request: Request):
    """P&L per factory and in total (crew/finance.py), the products' live status and Aibussol's own counts."""
    g = _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    from openjarvis.server import admin_analytics
    from openjarvis.server.crew.finance import KNOWN, Finance, factory_key

    pool = getattr(getattr(request.app.state, "session_store", None), "_pool", None)
    fin = getattr(request.app.state, "crew_finance", None)
    if fin is None:
        fin = request.app.state.crew_finance = Finance(engine.store, pool=pool)
    missions = await engine.store.list_missions(uid, 200)
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


@router.post("/api/crew/missions")
async def crew_start(body: MissionIn, request: Request):
    g = _gate(request)
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
    g = _gate(request)
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
    g = _gate(request)
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
    g = _gate(request)
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
    g = _gate(request)
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
    g = _gate(request)
    if isinstance(g, JSONResponse):
        return g
    _, engine = g
    if engine.herald is None:
        return JSONResponse({"detail": "Herald is not running."}, status_code=503)
    out = await engine.herald.run()
    return out if out.get("ok") else JSONResponse(out, status_code=409 if "already" in out.get("detail", "") else 502)


@router.post("/api/crew/run")
async def crew_run_now(request: Request):
    g = _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, engine = g
    blocked = await engine.may_run(uid)
    started = 0 if blocked else await engine.tick()
    return {"started": started, "blocked": blocked}


@router.get("/api/crew/connect/etsy")
async def crew_connect_etsy(request: Request):
    g = _gate(request)
    if isinstance(g, JSONResponse):
        return g
    uid, _ = g
    if not conns.etsy_app_configured():
        return JSONResponse({"detail": conns.SETUP["etsy"]}, status_code=409)
    return {"url": conns.etsy_connect_url(uid)}


@router.post("/api/crew/connect/etsy/disconnect")
async def crew_disconnect_etsy(request: Request):
    g = _gate(request)
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
