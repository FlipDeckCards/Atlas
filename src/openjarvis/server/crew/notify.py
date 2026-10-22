"""How the crew reaches the owner: always through Helix's chat, and also as a phone push when CREW_NOTIFY_URL is set.

The chat message is written into the owner's web session, so it is waiting in the Command Console the next time Aibussol opens.
CREW_NOTIFY_URL receives a plain-text POST with Title and Click headers; an ntfy.sh topic URL (https://ntfy.sh/<your-private-topic>)
turns that into a phone notification with the free ntfy app. A failed push is logged and never stops the crew.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import httpx

from openjarvis.server.crew import notify_url

logger = logging.getLogger(__name__)

CHANNEL = "web"


class Notifier:
    def __init__(self, session_store=None, url: Optional[str] = None, client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
                 base_url: str = "") -> None:
        self.session_store = session_store
        self.url = notify_url() if url is None else url
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=10))
        self.base_url = base_url.rstrip("/")
        self.sent: list = []                     # what was sent, newest last (kept short; handy for tests and the status page)
        self.url_for = None                      # async user_id -> push URL; set by the app so each account's alerts go to ITS phone

    async def notify(self, user_id: str, title: str, text: str, *, push: bool = True) -> None:
        self.sent = (self.sent + [{"user_id": user_id, "title": title, "text": text}])[-50:]
        if self.session_store is not None:
            try:
                await self.session_store.get_or_create(user_id, CHANNEL)
                await self.session_store.append_message(user_id, CHANNEL, "assistant", f"CREW · {title}\n{text}")
            except Exception:
                logger.exception("crew notice could not be added to the chat")
        url = self.url
        if self.url_for is not None:
            try:
                url = await self.url_for(user_id)
            except Exception:
                url = ""
        if push and url:
            try:
                headers = {"Title": title[:120].encode("ascii", "ignore").decode() or "Aibussol crew"}
                if self.base_url:
                    headers["Click"] = self.base_url + "/station"
                async with self.client_factory() as c:
                    await c.post(url, content=text[:1000].encode("utf-8"), headers=headers)
            except Exception:
                logger.warning("crew push notification failed")
