"""Run the desk.

  python -m openjarvis.polybot run          the desk, forever (the Render background worker's start command)
  python -m openjarvis.polybot status       print the latest status the desk wrote
  python -m openjarvis.polybot check        check the API keys (signed balance read) and the market feed, trade nothing

Environment (the worker's, set on Render):
  DATABASE_URL            Aibussol's database (status, fills, windows, events, control); without it, JSON files in POLYBOT_DATA_DIR
  POLYMARKET_KEY_ID       API key id from polymarket.us/developer          (needed for live and for `check`)
  POLYMARKET_SECRET_KEY   its base64 Ed25519 secret                         (never paste it into a chat)
  POLYBOT_ALLOW_LIVE      true lets the worker trade live once the owner approves it in Aibussol (default false: paper only)
  POLYBOT_BANKROLL_USD / _MAX_WINDOW_USD / _MAX_ORDER_USD / _DAILY_LOSS_USD / _MIN_EDGE   the money rules (defaults 50 / 5 / 2 / 10 / 0.04)
  POLYBOT_HORIZON         5m (default), 15m or 1h
  CREW_NOTIFY_URL         ntfy topic for phone pushes (every live entry and settlement; an hourly line in paper)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time


async def _store(cfg):
    from openjarvis.polybot.state import FileStore, PgStore

    return await PgStore.connect(cfg.database_url) if cfg.database_url else FileStore(cfg.data_dir)


def _notifier(url: str):
    if not url:
        return None

    async def push(title: str, text: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, content=text[:1000].encode("utf-8"), headers={"Title": title.encode("ascii", "ignore").decode()[:120] or "Desk"})
    return push


async def _run() -> int:
    from openjarvis.polybot.client import PolymarketUS
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    cfg = from_env()
    store = await _store(cfg)
    client = PolymarketUS(cfg.key_id, cfg.secret_key)
    desk = Desk(cfg, client, store, notify=_notifier(cfg.notify_url))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, desk.stop)          # Render sends SIGTERM on deploys: cancel orders, then exit
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await desk.run()
    finally:
        await client.close()
        await store.close()
    return 0


async def _status() -> int:
    from openjarvis.polybot.config import from_env

    store = await _store(from_env())
    try:
        print(json.dumps({"status": await store.get("status"), "control": await store.get("control")}, indent=1, default=str))
    finally:
        await store.close()
    return 0


async def _check() -> int:
    from openjarvis.polybot.client import PMError, PolymarketUS
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import is_series, window_from_market

    cfg = from_env()
    c = PolymarketUS(cfg.key_id, cfg.secret_key)
    ok = True
    try:
        now = int(time.time())
        px = await c.index_prices(cfg.asset, now - 30, 0)
        print(f"Index feed: {len(px)} prints in the last 30s, latest {px[-1] if px else None}")
        ms = await c.markets(categories=["crypto"], active=True, closed=False)
        ws = [w for w in (window_from_market(m) for m in ms if is_series(m, cfg.asset, cfg.horizon)) if w]
        print(f"{cfg.asset.upper()} {cfg.horizon} Up/Down windows listed: {len(ws)}" + (f", e.g. {ws[0].slug}" if ws else ""))
        if cfg.key_id and cfg.secret_key:
            try:
                print(f"API keys OK. Buying power: ${await c.buying_power()}")
            except PMError as exc:
                ok = False
                print(f"API keys FAILED: {exc}")
        else:
            print("No API keys set (fine for paper).")
        print(f"Live allowed on this worker: {cfg.allow_live}")
    finally:
        await c.close()
    return 0 if ok else 1


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(prog="python -m openjarvis.polybot")
    ap.add_argument("command", choices=["run", "status", "check"])
    a = ap.parse_args(argv)
    return asyncio.run({"run": _run, "status": _status, "check": _check}[a.command]())


if __name__ == "__main__":
    raise SystemExit(main())
