"""Commodity scout (owner, 2026-11-18): Polymarket US lists 15-minute Up/Down markets on gold, silver, WTI oil and natural gas
(event addresses like gold-updown-15m-2026-11-19-0345z). Before any desk watches them, this finds out on the server what the bot can
read: each window's market address, its order book (price and size at the top), the fields Polymarket gives for the market, how the
last window settled, and whether Polymarket publishes a live price for the commodity (the crypto desks read theirs from
/v1/asset-prices/history as crypto:<coin>). It places no orders and changes nothing; it only writes "SCOUT" lines to the server log.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("polybot.scout")

COMMODITIES = ("gold", "silver", "wti", "natgas")
WINDOW_S = 900
# names Polymarket might use for the live price feed: the first one that returns 1-second prints wins
PRICE_NAMES = {"gold": ("gold", "xau", "xauusd", "gc"), "silver": ("silver", "xag", "xagusd", "si"),
               "wti": ("wti", "oil", "cl", "crude", "crudeoil"), "natgas": ("natgas", "ng", "naturalgas", "natural_gas")}
PRICE_KINDS = ("commodity", "commodities", "metal", "metals", "energy", "futures", "crypto", "asset")


def event_slug(coin: str, start: int) -> str:
    return f"{coin}-updown-15m-{dt.datetime.fromtimestamp(start, dt.timezone.utc):%Y-%m-%d-%H%M}z"


def window_start(now: float) -> int:
    return int(now // WINDOW_S * WINDOW_S)


class Scout:
    def __init__(self, client: Any) -> None:
        self.c = client
        self.price_id: Dict[str, Optional[str]] = {}        # coin -> the asset id that worked (None: none found yet)
        self.fields_logged: set = set()
        self.last_market: Dict[str, str] = {}
        self.latest: Dict[str, Dict[str, Any]] = {}            # coin -> the last scan's row (shown on the Station)
        self.results: Dict[str, List[Dict[str, Any]]] = {c: [] for c in COMMODITIES}   # coin -> settled windows, newest last
        self.scanned_at: Optional[float] = None

    async def _prices(self, asset_id: str, start: int) -> int:
        try:
            d = await self.c._req("GET", "/v1/asset-prices/history",
                                  params={"assets": asset_id, "from": start, "to": 0, "bucket": "ASSET_PRICE_BUCKET_1S"}, tries=1)
        except Exception:
            return 0
        n = 0
        for s in (d or {}).get("series") or []:
            n += sum(1 for c in (s.get("candles") or []) if int(c.get("tickCount") or 0) > 0)
        return n

    async def find_price_feed(self, coin: str, now: float) -> Optional[str]:
        if self.price_id.get(coin):
            return self.price_id[coin]
        for kind in PRICE_KINDS:
            for name in PRICE_NAMES[coin]:
                aid = f"{kind}:{name}"
                n = await self._prices(aid, int(now) - 120)
                if n > 0:
                    self.price_id[coin] = aid
                    logger.warning("SCOUT %s: live price feed found as %s (%d one-second prints in the last 2 minutes).", coin, aid, n)
                    return aid
                await asyncio.sleep(0.3)
        logger.warning("SCOUT %s: no live price feed under any of %d names tried.", coin, len(PRICE_KINDS) * len(PRICE_NAMES[coin]))
        self.price_id[coin] = None
        return None

    async def scan(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = time.time() if now is None else now
        start = window_start(now)
        out = []
        for coin in COMMODITIES:
            ev = event_slug(coin, start)
            row: Dict[str, Any] = {"coin": coin, "event": ev}
            try:
                markets = await self.c.event_markets(ev)
            except Exception as exc:
                logger.warning("SCOUT %s: event %s not readable (%s).", coin, ev, exc)
                out.append(row)
                continue
            if not markets:
                logger.warning("SCOUT %s: event %s has no markets (closed hours?).", coin, ev)
                out.append(row)
                continue
            m = markets[0]
            mslug = m.get("slug") or ""
            row["market"] = mslug
            if coin not in self.fields_logged:
                self.fields_logged.add(coin)
                logger.warning("SCOUT %s: market %s fields: %s", coin, mslug,
                               {k: (v if not isinstance(v, (dict, list)) else type(v).__name__) for k, v in list(m.items())[:40]})
            try:
                bk = await self.c.book(mslug)
                bids, offers = bk.get("bids") or [], bk.get("offers") or bk.get("asks") or []
                row["book"] = {"bids": bids[:3], "offers": offers[:3]}
            except Exception as exc:
                row["book"] = f"unreadable ({exc})"
            prev = self.last_market.get(coin)
            if prev and prev != mslug:
                try:
                    st = await self.c.settlement(prev)
                    row["prev_settlement"] = (prev, st)
                    if st is not None and (st >= 0.99 or st <= 0.01):
                        self.results[coin].append({"market": prev, "result": "UP" if st >= 0.5 else "DOWN", "ts": now})
                        del self.results[coin][:-200]
                except Exception:
                    pass
            self.last_market[coin] = mslug
            row["price_feed"] = await self.find_price_feed(coin, now)
            logger.warning("SCOUT %s: %s", coin, row)
            out.append(row)
            self.latest[coin] = {**row, "window_start": start, "window_end": start + WINDOW_S}
        self.scanned_at = now
        return out

    def summary(self) -> Dict[str, Any]:
        """What the Station shows: per commodity, the current window, its top of book, the price feed status and the results so far."""
        out = []
        for coin in COMMODITIES:
            r = self.latest.get(coin) or {}
            res = self.results.get(coin) or []
            ups = sum(1 for x in res if x["result"] == "UP")
            out.append({"coin": coin, "event": r.get("event"), "market": r.get("market"), "book": r.get("book"),
                        "window_start": r.get("window_start"), "window_end": r.get("window_end"),
                        "price_feed": self.price_id.get(coin), "feed_checked": coin in self.price_id,
                        "settled": len(res), "ups": ups, "downs": len(res) - ups, "recent": [x["result"] for x in res[-12:]]})
        return {"scanned_at": self.scanned_at, "desks": out}

    async def forever(self, first_delay: float = 45.0) -> None:
        await asyncio.sleep(first_delay)
        while True:
            try:
                await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SCOUT scan failed; trying again next window")
            now = time.time()
            await asyncio.sleep(max(30.0, window_start(now) + WINDOW_S + 60 - now))   # one minute into each new window
