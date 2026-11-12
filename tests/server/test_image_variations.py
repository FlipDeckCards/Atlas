"""vary_image: a picture the user attached plus a prompt gives N new pictures (Gemini, reference-image mode).

Offline: the provider, storage, catalog and spend ledger are in-memory fakes. Checks that the real file reaches the provider, the count is
clamped (default 4, max 10), the cost lands in the spend ledger (the Station's Out column) per picture, and the usual gates still apply.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from openjarvis.server import hardening
from openjarvis.server.media import helix_tools, service
from openjarvis.server.media import image as image_mod
from openjarvis.server.media.base import MediaError, MediaResult
from openjarvis.server.media.catalog import MemoryCatalog
from openjarvis.server.media.image_backups import GeminiImage
from openjarvis.server.media.service import MediaServiceError
from openjarvis.server.media.storage import MemoryStorage
from tests.server._media_fakes import FakeProvider, FakeStore, make_ctx, tiny_jpeg


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("MEDIA_ENABLED", "DAILY_MEDIA_SPEND_CAP_USD", "MEDIA_GLOBAL_DAILY_CAP_USD", "MEDIA_USER_QUOTA_BYTES"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("MEDIA_ENABLED", "1")
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "5")
    monkeypatch.setenv("MEDIA_GLOBAL_DAILY_CAP_USD", "50")
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())
    monkeypatch.setattr(helix_tools, "PENDING", helix_tools.PendingActions())
    monkeypatch.setattr(helix_tools, "ATTACHED", {})


class FlakyProvider(FakeProvider):
    """Fails on the calls listed in fail_on (1-based)."""

    def __init__(self, fail_on=()):
        super().__init__("image", cost=0.04)
        self.fail_on = set(fail_on)

    async def generate(self, **request):
        self.calls.append(request)
        if len(self.calls) in self.fail_on:
            raise MediaError("busy", 503)
        return MediaResult("image", "image/jpeg", tiny_jpeg(color=(len(self.calls) * 20 % 255, 10, 10)), self.id)


@pytest.fixture
def setup(monkeypatch):
    prov = FlakyProvider()
    monkeypatch.setattr(image_mod, "get_reference_provider", lambda: prov)
    shared = (FakeStore(), MemoryCatalog(), MemoryStorage())
    return prov, shared, make_ctx("alice", *shared)


async def attach(ctx, color=(9, 99, 199)):
    saved = await helix_tools.keep_attached(ctx, [("cover.jpg", base64.b64encode(tiny_jpeg(color=color)).decode(), "image/jpeg")])
    return saved[0]["item_id"]


@pytest.mark.asyncio
async def test_the_attached_file_itself_is_saved_and_sent_to_the_provider(setup):
    prov, (store, catalog, storage), ctx = setup
    original = tiny_jpeg(color=(9, 99, 199))
    item_id = await attach(ctx)
    res = await service.vary_and_save(ctx, item_id, "make it neon", 3)
    assert len(prov.calls) == 3 and len(res["items"]) == 3
    assert all(c["reference"] == (original, "image/jpeg") and c["prompt"] == "make it neon" for c in prov.calls)   # bytes, not a description
    assert helix_tools.latest_attached("alice") == item_id
    kids = [i for i in catalog.items.values() if i.edited_from_id == item_id]
    assert len(kids) == 3 and all(i.kind == "image" for i in kids)


@pytest.mark.asyncio
@pytest.mark.parametrize("asked,expected", [(None, 4), (0, 1), (-3, 1), (7, 7), (10, 10), (11, 10), (999, 10), ("six", 4)])
async def test_the_count_defaults_to_4_and_is_capped_at_10(setup, asked, expected):
    prov, _shared, ctx = setup
    item_id = await attach(ctx)
    res = await service.vary_and_save(ctx, item_id, "x", asked)
    assert len(prov.calls) == expected and len(res["items"]) == expected


@pytest.mark.asyncio
async def test_each_picture_is_charged_to_the_spend_ledger_so_it_shows_in_out(setup):
    prov, (store, _c, _s), ctx = setup
    item_id = await attach(ctx)
    res = await service.vary_and_save(ctx, item_id, "x", 4)
    assert store.spent("media:alice") == pytest.approx(0.16) and store.spent(service.GLOBAL_KEY) == pytest.approx(0.16)
    assert res["cost_usd"] == pytest.approx(0.16)


@pytest.mark.asyncio
async def test_a_failed_picture_is_skipped_and_not_charged(setup):
    prov, (store, catalog, _s), ctx = setup
    prov.fail_on = {2}
    item_id = await attach(ctx)
    res = await service.vary_and_save(ctx, item_id, "x", 4)
    assert len(res["items"]) == 3 and res["failed"] == 1 and store.spent("media:alice") == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_all_failing_raises_and_costs_nothing(setup):
    prov, (store, _c, _s), ctx = setup
    prov.fail_on = {1, 2, 3, 4}
    item_id = await attach(ctx)
    with pytest.raises(MediaServiceError):
        await service.vary_and_save(ctx, item_id, "x", 4)
    assert store.spent("media:alice") == 0


@pytest.mark.asyncio
async def test_the_daily_cap_is_checked_for_the_whole_batch_before_any_call(setup, monkeypatch):
    prov, (store, _c, _s), ctx = setup
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "0.30")           # 10 x $0.04 = $0.40 does not fit
    item_id = await attach(ctx)
    with pytest.raises(MediaServiceError) as e:
        await service.vary_and_save(ctx, item_id, "x", 10)
    assert e.value.status == 429 and prov.calls == [] and store.spent("media:alice") == 0


@pytest.mark.asyncio
async def test_another_users_picture_is_not_found(setup):
    prov, shared, ctx = setup
    item_id = await attach(ctx)
    bob = make_ctx("bob", *shared)
    with pytest.raises(MediaServiceError) as e:
        await service.vary_and_save(bob, item_id, "x", 2)
    assert e.value.status == 404 and prov.calls == []


@pytest.mark.asyncio
async def test_the_tool_asks_for_a_yes_then_runs_on_the_attached_picture(setup):
    prov, (store, catalog, _s), ctx = setup
    item_id = await attach(ctx)
    out = await helix_tools.execute_tool(ctx, "vary_image", {"prompt": "four colour versions", "count": 12}, user_message="make versions")
    assert "NOT RUN YET" in out["text"] and prov.calls == [] and store.spend == {}
    p = helix_tools.PENDING.get("alice")
    assert p.args["item_id"] == item_id and p.args["count"] == 10 and p.estimate_usd == pytest.approx(0.40)
    p.armed = True
    done = await helix_tools.pre_turn(ctx, "yes")
    assert done["ui"][0]["type"] == "saved_many" and len(done["ui"][0]["items"]) == 10 and all(i["url"] for i in done["ui"][0]["items"])
    assert len(prov.calls) == 10 and store.spent("media:alice") == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_the_tool_without_any_picture_makes_nothing(setup):
    prov, (store, _c, _s), ctx = setup
    out = await helix_tools.execute_tool(ctx, "vary_image", {"prompt": "versions"}, user_message="make versions")
    assert "no picture" in out["text"] and helix_tools.PENDING.get("alice") is None and prov.calls == []


@pytest.mark.asyncio
async def test_the_gemini_request_carries_the_picture_as_inline_data(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        out = base64.b64encode(tiny_jpeg()).decode()
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": out}}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        res = await GeminiImage().generate("blue version", client=c, reference=(b"RAW", "image/jpeg"))
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["inlineData"] == {"mimeType": "image/jpeg", "data": base64.b64encode(b"RAW").decode()} and parts[1] == {"text": "blue version"}
    assert res.kind == "image" and res.data
