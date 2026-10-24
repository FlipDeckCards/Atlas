"""FastAPI application factory for the OpenJarvis API server."""


from __future__ import annotations

import logging
import pathlib
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from openjarvis.server.analytics_routes import router as analytics_router
from openjarvis.server.api_routes import include_all_routes
from openjarvis.server.atlas_ui import router as atlas_ui_router
from openjarvis.server.auth_routes import router as auth_router
from openjarvis.server.briefs import router as briefs_router
from openjarvis.server.comparison import comparison_router
from openjarvis.server.connectors_router import create_connectors_router
from openjarvis.server.crew.routes import router as crew_router
from openjarvis.server.dashboard import dashboard_router
from openjarvis.server.digest_routes import create_digest_router
from openjarvis.server.media.routes import router as media_router
from openjarvis.server.research_router import router as research_router
from openjarvis.server.routes import router
from openjarvis.server.upload_router import router as upload_router

logger = logging.getLogger(__name__)


class PermissionsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Permissions-Policy"] = "microphone=*, camera=*"
        response.headers["Feature-Policy"] = "microphone *; camera *"
        return response


def _restore_sendblue_bindings(app: FastAPI) -> None:
    """Restore SendBlue channel bindings from the database on startup."""
    try:
        mgr = getattr(app.state, "agent_manager", None)
        if mgr is None:
            return

        for agent in mgr.list_agents():
            agent_id = agent.get("id", agent.get("agent_id", ""))
            bindings = mgr.list_channel_bindings(agent_id)
            for b in bindings:
                if b.get("channel_type") != "sendblue":
                    continue
                config = b.get("config", {})
                api_key_id = config.get("api_key_id", "")
                api_secret_key = config.get("api_secret_key", "")
                from_number = config.get("from_number", "")
                if not api_key_id or not api_secret_key:
                    continue

                from openjarvis.channels.sendblue import SendBlueChannel

                sb = SendBlueChannel(
                    api_key_id=api_key_id,
                    api_secret_key=api_secret_key,
                    from_number=from_number,
                )
                sb.connect()
                app.state.sendblue_channel = sb

                bridge = getattr(app.state, "channel_bridge", None)
                if bridge and hasattr(bridge, "_channels"):
                    bridge._channels["sendblue"] = sb
                else:
                    from openjarvis.server.channel_bridge import ChannelBridge
                    from openjarvis.server.session_store import SessionStore

                    session_store = SessionStore()
                    engine = getattr(app.state, "engine", None)
                    dr_agent = None
                    if engine:
                        from openjarvis.server.agent_manager_routes import (
                            _build_deep_research_tools,
                        )

                        tools = _build_deep_research_tools(engine=engine, model="")
                        if tools:
                            from openjarvis.agents.deep_research import (
                                DeepResearchAgent,
                            )

                            model_name = getattr(app.state, "model", "") or getattr(
                                engine, "_model", ""
                            )
                            dr_agent = DeepResearchAgent(
                                engine=engine,
                                model=model_name,
                                tools=tools,
                            )

                    bus = getattr(app.state, "bus", None)
                    if bus is None:
                        from openjarvis.core.events import EventBus

                        bus = EventBus()

                    app.state.channel_bridge = ChannelBridge(
                        channels={"sendblue": sb},
                        session_store=session_store,
                        bus=bus,
                        agent_manager=mgr,
                        deep_research_agent=dr_agent,
                    )

                logger.info("Restored SendBlue channel binding: %s", from_number)
                return
    except Exception as exc:
        logger.debug("SendBlue binding restore skipped: %s", exc)


_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#050508"/>'
    '<text x="16" y="22" font-family="monospace" font-size="18" font-weight="700" text-anchor="middle" fill="#00e5ff">A</text></svg>'
)
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


class _NoCacheStaticFiles(StaticFiles):
    async def __call__(self, scope, receive, send):
        async def _send_with_headers(message):
            if message["type"] == "http.response.start":
                extra = [(k.encode(), v.encode()) for k, v in _NO_CACHE_HEADERS.items()]
                existing = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in (b"etag", b"last-modified")
                ]
                message = {**message, "headers": existing + extra}
            await send(message)

        await super().__call__(scope, receive, _send_with_headers)


def create_app(
    engine,
    model: str,
    *,
    agent=None,
    bus=None,
    engine_name: str = "",
    agent_name: str = "",
    channel_bridge=None,
    config=None,
    memory_backend=None,
    speech_backend=None,
    agent_manager=None,
    agent_scheduler=None,
    api_key: str = "",
    webhook_config: dict | None = None,
    cors_origins: list[str] | None = None,
    require_auth: bool = False,
) -> FastAPI:
    if require_auth and not api_key:
        from openjarvis.server.hardening import StartupConfigError

        raise StartupConfigError(
            "Refusing to build the server without an API key: auth must be enforced on /api/* and /v1/*."
        )
    app = FastAPI(
        title="OpenJarvis API",
        description="OpenAI-compatible API server for OpenJarvis",
        version="0.1.0",
    )

    _origins = (
        cors_origins
        if cors_origins is not None
        else [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(PermissionsMiddleware)

    app.state.engine = engine
    app.state.model = model
    app.state.agent = agent
    app.state.bus = bus
    app.state.engine_name = engine_name
    app.state.agent_name = agent_name or (
        getattr(agent, "agent_id", None) if agent else None
    )
    app.state.channel_bridge = channel_bridge
    app.state.config = config
    app.state.memory_backend = memory_backend
    app.state.speech_backend = speech_backend
    app.state.agent_manager = agent_manager
    app.state.agent_scheduler = agent_scheduler
    app.state.session_start = time.time()
    app.state.api_key = api_key

    app.state.trace_store = None
    try:
        from openjarvis.core.config import load_config
        from openjarvis.traces.store import TraceStore

        cfg = config if config is not None else load_config()
        if cfg.traces.enabled:
            app.state.trace_store = TraceStore(db_path=cfg.traces.db_path)
    except Exception:
        pass

    app.state.analytics_client = None
    app.state.analytics_bridge = None
    try:
        from openjarvis.analytics import (
            AnalyticsClient,
            EventBridge,
            is_analytics_enabled,
        )
        from openjarvis.core.config import load_config

        _cfg = config if config is not None else load_config()
        if is_analytics_enabled(_cfg.analytics):
            _client = AnalyticsClient(_cfg.analytics)
            app.state.analytics_client = _client
            _bus_ref = getattr(app.state, "bus", None)
            if _bus_ref is not None:
                _bridge = EventBridge(_bus_ref, _client)
                _bridge.start()
                app.state.analytics_bridge = _bridge

            @app.on_event("shutdown")
            async def _shutdown_analytics() -> None:
                bridge = getattr(app.state, "analytics_bridge", None)
                if bridge is not None:
                    try:
                        bridge.stop()
                    except Exception:
                        pass
                client = getattr(app.state, "analytics_client", None)
                if client is not None:
                    try:
                        client.shutdown()
                    except Exception:
                        pass
    except Exception as _exc:
        logger.debug("Analytics init skipped: %s", _exc)

    app.include_router(router)
    app.include_router(dashboard_router)
    app.include_router(comparison_router)
    app.include_router(create_connectors_router())
    app.include_router(create_digest_router())
    app.include_router(upload_router)
    app.include_router(research_router)
    app.include_router(analytics_router)
    from openjarvis.server.billing import router as billing_router
    app.include_router(billing_router)
    app.include_router(atlas_ui_router)
    app.include_router(media_router)
    app.include_router(briefs_router)
    app.include_router(crew_router)
    app.include_router(auth_router)
    include_all_routes(app)

    @app.on_event("startup")
    async def _startup_session_store() -> None:
        from openjarvis.server.session_store import SessionStore
        store = SessionStore()
        await store.connect()
        app.state.session_store = store

    @app.on_event("startup")
    async def _startup_crew() -> None:
        # the crew engine (server/crew): only when CREW_ENABLED is true and the database is up; its tables are created add-only
        from openjarvis.server.crew import crew_enabled

        store = getattr(app.state, "session_store", None)
        pool = getattr(store, "_pool", None)
        if not crew_enabled() or pool is None or getattr(app.state, "crew_engine", None) is not None:
            return
        try:
            import os

            import litellm

            from openjarvis.server.crew.engine import CrewEngine
            from openjarvis.server.crew.notify import Notifier
            from openjarvis.server.crew.store import CrewStore

            def crew_media_ctx(uid: str):
                # Prism's images go through the normal media service: its switches, caps, rate limits and storage quota all apply
                from openjarvis.server.media import service as ms

                if not ms.media_enabled() or ms.media_cap_usd() <= 0:
                    return None
                storage = getattr(app.state, "media_storage", None) or ms.storage_from_env()
                if storage is None:
                    return None
                catalog = getattr(app.state, "media_catalog", None) or ms.MediaCatalog(pool)
                return ms.Ctx(uid, "crew", store, catalog, storage)

            notifier = Notifier(store, base_url=os.environ.get("PUBLIC_BASE_URL", "https://aibussol.com"))
            engine = CrewEngine(CrewStore(pool), litellm.acompletion, media_ctx_fn=crew_media_ctx, notifier=notifier)
            from openjarvis.server.briefs import BriefStore
            from openjarvis.server.crew.herald import Herald

            async def crew_is_owner(uid: str) -> bool:
                # owner / admin accounts (ANALYTICS_ADMIN_EMAILS, default the owner's email): no daily cap, budgets top up on their own
                from openjarvis.server.admin_analytics import is_admin

                async with pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT email FROM users WHERE id = $1", uid)
                return bool(row) and is_admin(row["email"])

            engine.owner_fn = crew_is_owner

            async def crew_name(uid: str) -> str:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT display_name, email FROM users WHERE id = $1", uid)
                return (row["display_name"] or row["email"].split("@")[0]) if row else ""

            engine.name_fn = crew_name

            async def crew_push_url(uid: str) -> str:
                # the owner's alerts go to CREW_NOTIFY_URL; everyone else's only to the phone topic THEY connected
                from openjarvis.server.crew import notify_url, tenant

                if await engine.is_owner(uid):
                    return notify_url()
                return (await tenant.get(engine.store, uid, "notify_url")) or ""

            notifier.url_for = crew_push_url
            engine.herald = Herald(engine, getattr(app.state, "brief_store", None) or BriefStore(pool), pool=pool, notifier=notifier)
            engine.pool = pool
            from openjarvis.server.crew.sentinel import Sentinel

            engine.sentinel = Sentinel(engine, owner_id_fn=engine.herald.owner_id)       # Helix watches the owner's deploys and investigates failures
            from openjarvis.server.crew.weekly import WeeklySocial

            engine.weekly = WeeklySocial(engine, engine.herald)
            await engine.start()
            app.state.crew_engine = engine
            logger.info("crew engine started")
        except Exception:
            logger.exception("crew engine did not start; the rest of Aibussol runs normally")

    @app.on_event("shutdown")
    async def _shutdown_crew() -> None:
        engine = getattr(app.state, "crew_engine", None)
        if engine is not None:
            await engine.stop()

    @app.on_event("shutdown")
    async def _shutdown_session_store() -> None:
        store = getattr(app.state, "session_store", None)
        if store:
            await store.close()

    _restore_sendblue_bindings(app)

    try:
        from openjarvis.server.middleware import create_security_middleware

        middleware_cls = create_security_middleware()
        if middleware_cls is not None:
            app.add_middleware(middleware_cls)
    except Exception as exc:
        logger.debug("Security middleware init skipped: %s", exc)

    if api_key:
        try:
            from openjarvis.server.auth_middleware import AuthMiddleware

            app.add_middleware(AuthMiddleware, api_key=api_key)
        except Exception as exc:
            logger.debug("Auth middleware init skipped: %s", exc)

    if webhook_config:
        try:
            from openjarvis.server.webhook_routes import create_webhook_router

            webhook_router = create_webhook_router(
                bridge=channel_bridge,
                twilio_auth_token=webhook_config.get("twilio_auth_token", ""),
                bluebubbles_password=webhook_config.get("bluebubbles_password", ""),
                whatsapp_verify_token=webhook_config.get("whatsapp_verify_token", ""),
                whatsapp_app_secret=webhook_config.get("whatsapp_app_secret", ""),
            )
            app.include_router(webhook_router)
        except Exception as exc:
            logger.debug("Webhook routes init skipped: %s", exc)

    static_dir = pathlib.Path(__file__).parent / "static"
    if static_dir.is_dir():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                _NoCacheStaticFiles(directory=assets_dir),
                name="static-assets",
            )

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            # a small inline icon: browsers ask for /favicon.ico on every visit, and it used to fall through to the page below and fail
            return Response(content=_FAVICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

        @app.get("/{full_path:path}")
        async def spa_catch_all(full_path: str):
            if full_path:
                candidate = (static_dir / full_path).resolve()
                resolved_root = static_dir.resolve()
                if candidate.is_relative_to(resolved_root) and candidate.is_file():
                    return FileResponse(candidate, headers=_NO_CACHE_HEADERS)
            index = static_dir / "index.html"
            if not index.is_file():          # a missing page is a 404, never a 500
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return FileResponse(index, headers=_NO_CACHE_HEADERS)

    return app


__all__ = ["create_app"]
