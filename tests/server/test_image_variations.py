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
    monkeypatch.setattr(service, "GEN_RETRIES", 0)                      # the tests below count calls; retries have their own test
    monkeypatch.setattr(service, "GEN_RETRY_DELAY_S", 0)


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
    assert all(c["reference"] == (original, "image/jpeg") and c["prompt"].endswith("make it neon") for c in prov.calls)   # bytes, not a description
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


def strip_png(n=7, w=1568, h=672):
    """A strip like the image model's: n figures on flat magenta."""
    import io

    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), (255, 0, 255))
    d = ImageDraw.Draw(im)
    for i in range(n):
        cx = w / n * i + w / n / 2
        d.rectangle([cx - 40, 300, cx + 40, 560], fill=(40, 120, 60))
        d.ellipse([cx - 45, 200, cx + 45, 295], fill=(240, 200, 160))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class StripProvider(FlakyProvider):
    async def generate(self, **request):
        self.calls.append(request)
        if len(self.calls) in self.fail_on:
            raise MediaError("busy", 503)
        return MediaResult("image", "image/png", strip_png(), self.id)


class TestRequestsThatBuildOnAPicture:
    """'create a new character similar to the image I just gave you' must use the picture, not the text-only image tool."""

    @pytest.mark.asyncio
    async def test_generate_image_after_an_attachment_becomes_a_reference_variation(self, setup):
        prov, (store, catalog, _s), ctx = setup
        item_id = await attach(ctx)
        msg = "create me a new character but similar to the other image i just gave you. make it the female version"
        out = await helix_tools.execute_tool(ctx, "generate_image", {"prompt": "a female archer"}, user_message=msg)
        p = helix_tools.PENDING.get("alice")
        assert "NOT RUN YET" in out["text"] and p.tool == "vary_image" and p.args["item_id"] == item_id and p.args["count"] == 1
        p.armed = True
        done = await helix_tools.pre_turn(ctx, "yes")
        assert len(prov.calls) == 1 and prov.calls[0]["reference"][1] == "image/jpeg" and done["ui"][0]["type"] == "saved_many"

    @pytest.mark.asyncio
    async def test_an_unrelated_request_still_uses_plain_generation(self, setup, monkeypatch):
        prov, shared, ctx = setup
        await attach(ctx)
        plain = FakeProvider("image")
        monkeypatch.setitem(image_mod.PROVIDERS, image_mod.DEFAULT_PROVIDER, plain)
        await helix_tools.execute_tool(ctx, "generate_image", {"prompt": "a red fox"}, user_message="make me a red fox picture")
        assert helix_tools.PENDING.get("alice").tool == "generate_image"

    @pytest.mark.asyncio
    async def test_an_old_attachment_is_not_used(self, setup, monkeypatch):
        prov, shared, ctx = setup
        await attach(ctx)
        monkeypatch.setattr(helix_tools, "ATTACHED_RECENT_S", 0.0001)
        import asyncio

        await asyncio.sleep(0.06)
        await helix_tools.execute_tool(ctx, "generate_image", {"prompt": "x"}, user_message="make one similar to that image")
        assert helix_tools.PENDING.get("alice").tool == "generate_image"

    def test_the_read_back_is_not_cut_off_mid_sentence(self):
        text = helix_tools.describe("vary_image", {"prompt": "word " * 100, "count": 1})
        assert text.endswith('\u2026"') and len(text) < 320


class TestPictureRequestsSetUpByTheServer:
    @pytest.mark.asyncio
    async def test_an_edit_is_a_single_edit_request_on_the_picture(self, setup):
        prov, (store, catalog, _s), ctx = setup
        item_id = await attach(ctx)
        early = await helix_tools.picture_request(ctx, "remove the sword from this character", True)
        p = helix_tools.PENDING.get("alice")
        assert "edit your picture" in early["reply"] and "$0.04" in early["reply"] and p.armed and p.args["edit"] and p.args["count"] == 1
        assert prov.calls == [] and store.spend == {}
        done = await helix_tools.pre_turn(ctx, "yes")
        assert len(prov.calls) == 1 and prov.calls[0]["prompt"].startswith(service.EDIT_PREFIX) and "remove the sword" in prov.calls[0]["prompt"]
        assert done["ui"][0]["items"][0]["title"].endswith("(edited)") and prov.calls[0]["reference"][1] == "image/jpeg"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,count", [("make 10 versions of this", 10), ("give me six variations", 6), ("show me some different options", 4), ("make a few more versions", 3)])
    async def test_versions_use_the_number_asked_for(self, setup, message, count):
        _prov, _shared, ctx = setup
        await attach(ctx)
        early = await helix_tools.picture_request(ctx, message, True)
        assert early and helix_tools.PENDING.get("alice").args["count"] == count

    @pytest.mark.asyncio
    async def test_text_about_something_else_is_left_to_the_model(self, setup):
        _prov, _shared, ctx = setup
        await attach(ctx)
        assert await helix_tools.picture_request(ctx, "what do you think of it?", True) is None
        assert await helix_tools.picture_request(ctx, "/image add a paragraph", True) is None
        assert await helix_tools.picture_request(ctx, "add a paragraph to my essay", False) is None        # no picture mentioned, none attached just now
        assert await helix_tools.picture_request(ctx, "remove the sword from this character", False) is not None   # the picture is pointed at

    @pytest.mark.asyncio
    async def test_no_picture_means_no_request(self, setup):
        _prov, _shared, ctx = setup
        assert await helix_tools.picture_request(ctx, "remove the sword from this character", True) is None


class TestNanoBananaPro:
    def test_reference_work_uses_pro_unless_flash_is_asked_for(self, monkeypatch):
        monkeypatch.delenv("GEMINI_REFERENCE_MODEL", raising=False)
        assert image_mod.get_reference_provider().id == "google_gemini_pro_image"
        monkeypatch.setenv("GEMINI_REFERENCE_MODEL", "flash")
        assert image_mod.get_reference_provider().id == "google_gemini_image"

    def test_model_and_price_come_from_settings_with_defaults(self, monkeypatch):
        from openjarvis.server.media.image_backups import GeminiProImage

        monkeypatch.delenv("GEMINI_PRO_IMAGE_MODEL", raising=False)
        monkeypatch.delenv("GEMINI_PRO_IMAGE_USD", raising=False)
        assert GeminiProImage().model_name() == "gemini-3-pro-image" and GeminiProImage().estimate_cost_usd() == pytest.approx(0.134)
        monkeypatch.setenv("GEMINI_PRO_IMAGE_MODEL", "gemini-3-pro-image-preview")
        monkeypatch.setenv("GEMINI_PRO_IMAGE_USD", "0.2")
        assert GeminiProImage().model_name() == "gemini-3-pro-image-preview" and GeminiProImage().estimate_cost_usd() == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_the_request_names_the_pro_model_and_skips_draft_pictures(self, monkeypatch):
        from openjarvis.server.media.image_backups import GeminiProImage

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.delenv("GEMINI_PRO_IMAGE_MODEL", raising=False)
        seen = {}
        draft, final = base64.b64encode(b"DRAFT").decode(), base64.b64encode(tiny_jpeg()).decode()

        def handler(request: httpx.Request):
            seen["url"], seen["cfg"] = str(request.url), json.loads(request.content)["generationConfig"]
            parts = [{"thought": True, "inlineData": {"mimeType": "image/png", "data": draft}}, {"inlineData": {"mimeType": "image/png", "data": final}}]
            return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            res = await GeminiProImage().generate("x", client=c, reference=(b"R", "image/png"))
        assert "models/gemini-3-pro-image:generateContent" in seen["url"] and seen["cfg"]["responseModalities"] == ["TEXT", "IMAGE"]
        assert res.data == tiny_jpeg() and res.cost_usd is None

    @pytest.mark.asyncio
    async def test_a_refused_pro_model_falls_back_to_flash_and_is_charged_at_flash_price(self, monkeypatch):
        from openjarvis.server.media.image_backups import GeminiProImage

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        urls = []

        def handler(request: httpx.Request):
            urls.append(str(request.url))
            if "gemini-3-pro-image" in str(request.url):
                return httpx.Response(404, json={"error": "model not found"})
            out = base64.b64encode(tiny_jpeg()).decode()
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": out}}]}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            res = await GeminiProImage().generate("x", client=c)
        assert "gemini-2.5-flash-image" in urls[-1] and res.cost_usd == pytest.approx(0.04)

    @pytest.mark.asyncio
    async def test_the_ledger_uses_the_price_that_was_really_paid(self, setup):
        prov, (store, _c, _s), ctx = setup

        async def cheap(**request):
            prov.calls.append(request)
            return MediaResult("image", "image/jpeg", tiny_jpeg(), prov.id, cost_usd=0.04)

        prov.estimate_cost_usd = lambda **_: 0.134
        prov.generate = cheap
        item_id = await attach(ctx)
        await service.vary_and_save(ctx, item_id, "x", 2)
        assert store.spent("media:alice") == pytest.approx(0.08)                       # two fallbacks at Flash price, not 2 x $0.134


class TestIdentityLock:
    def test_both_prompts_carry_the_lock_and_the_features(self):
        from openjarvis.server.media import spritesheet as sp

        for p in (sp.template_prompt(sp.clean_sections(None), "", "a dark hood worn up, blue glowing trim"), sp.strip_prompt("Walk", 7, "", "a dark hood worn up, blue glowing trim")):
            assert "IDENTITY LOCK" in p and "hood" in p and "Never remove" in p and "a dark hood worn up, blue glowing trim" in p
        assert "permanent features" not in sp.template_prompt(sp.clean_sections(None))            # nothing to add when the description is unavailable

    @pytest.mark.asyncio
    async def test_the_features_are_read_from_the_reference_and_put_in_the_sheet_prompt(self, setup, monkeypatch):
        prov, _shared, ctx = setup
        seen = {}

        async def fake(reference):
            seen["ref"] = reference
            return "a dark hooded cloak with the hood up"

        monkeypatch.setattr(image_mod, "describe_character", fake)
        item_id = await attach(ctx)
        await service.sprite_sheet_and_save(ctx, item_id)
        assert seen["ref"][1] == "image/jpeg" and "a dark hooded cloak with the hood up" in prov.calls[0]["prompt"]

    @pytest.mark.asyncio
    async def test_the_description_call_parses_the_answer_and_fails_quietly(self, monkeypatch):
        from openjarvis.server.media.image_backups import describe_character

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        sent = {}

        def ok(request: httpx.Request):
            sent["url"], sent["parts"] = str(request.url), json.loads(request.content)["contents"][0]["parts"]
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "A hooded cloak,  blue glow."}]}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as c:
            assert await describe_character((b"IMG", "image/png"), client=c) == "A hooded cloak, blue glow."
        assert "gemini-2.5-flash" in sent["url"] and sent["parts"][0]["inlineData"]["mimeType"] == "image/png" and "permanent design features" in sent["parts"][-1]["text"]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as c:
            assert await describe_character((b"IMG", "image/png"), client=c) == ""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert await describe_character((b"IMG", "image/png")) == ""


class TestTemplateSheet:
    def test_the_standard_panels_and_the_prompt(self):
        from openjarvis.server.media import spritesheet as sp

        secs = sp.clean_sections(None)
        assert [s[0] for s in secs][:3] == ["IDLE", "WALK", "RUN"] and len(secs) == 13 and secs[-1][0] == "ICONS"
        assert [s[0] for s in sp.clean_sections(["idle", "Backflip"])] == ["IDLE", "BACKFLIP"] and len(sp.clean_sections(["x"] * 40)) == 16
        p = sp.template_prompt(secs, "keep the glow")
        assert "plain white background" in p and "two-column grid" in p and "SKILL - TELEPORT" in p and "keep the glow" in p and "attached character" in p

    @pytest.mark.asyncio
    async def test_the_default_sheet_is_one_call_at_4k_and_one_saved_picture(self, setup):
        prov, (store, catalog, _s), ctx = setup
        item_id = await attach(ctx)
        res = await service.sprite_sheet_and_save(ctx, item_id, notes="keep the glow")
        assert len(prov.calls) == 1 and prov.calls[0]["aspect_ratio"] == "3:2" and prov.calls[0]["image_size"] == "4K" and prov.calls[0]["reference"][1] == "image/jpeg"
        assert len(res["items"]) == 1 and res["items"][0]["title"].endswith("(sprite sheet)") and len(res["rows"]) == 13
        assert store.spent("media:alice") == pytest.approx(0.04) and res["cost_usd"] == pytest.approx(0.04)

    @pytest.mark.asyncio
    async def test_the_tool_describes_the_standard_layout_and_asks_for_a_yes(self, setup):
        prov, (store, _c, _s), ctx = setup
        await attach(ctx)
        out = await helix_tools.execute_tool(ctx, "make_sprite_sheet", {}, user_message="make me a sprite sheet")
        p = helix_tools.PENDING.get("alice")
        assert "NOT RUN YET" in out["text"] and prov.calls == [] and "standard layout" in p.description and "Skill - Teleport" in p.description
        p.armed = True
        done = await helix_tools.pre_turn(ctx, "yes")
        assert done["ui"][0]["type"] == "saved_many" and len(done["ui"][0]["items"]) == 1 and len(prov.calls) == 1

    def test_pro_costs_more_at_4k_and_both_prices_can_be_set(self, monkeypatch):
        from openjarvis.server.media.image_backups import GeminiProImage

        for n in ("GEMINI_PRO_IMAGE_USD", "GEMINI_PRO_IMAGE_4K_USD"):
            monkeypatch.delenv(n, raising=False)
        g = GeminiProImage()
        assert g.estimate_cost_usd() == pytest.approx(0.134) and g.estimate_cost_usd(image_size="4K") == pytest.approx(0.24)
        monkeypatch.setenv("GEMINI_PRO_IMAGE_4K_USD", "0.3")
        assert g.estimate_cost_usd(image_size="4k") == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_the_size_goes_to_pro_and_is_dropped_when_google_refuses_it(self, monkeypatch):
        from openjarvis.server.media.image_backups import GeminiProImage

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        seen = []

        def handler(request: httpx.Request):
            conf = json.loads(request.content)["generationConfig"].get("imageConfig")
            seen.append(conf)
            if conf and "imageSize" in conf:
                return httpx.Response(400, json={"error": "bad size"})
            out = base64.b64encode(tiny_jpeg()).decode()
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": out}}]}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            res = await GeminiProImage().generate("x", client=c, aspect_ratio="3:2", image_size="4K")
        assert seen == [{"aspectRatio": "3:2", "imageSize": "4K"}, {"aspectRatio": "3:2"}] and res.data

    @pytest.mark.asyncio
    async def test_flash_never_gets_a_size(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        seen = []

        def handler(request: httpx.Request):
            seen.append(json.loads(request.content)["generationConfig"].get("imageConfig"))
            out = base64.b64encode(tiny_jpeg()).decode()
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": out}}]}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await GeminiImage().generate("x", client=c, aspect_ratio="3:2", image_size="4K")
        assert seen == [{"aspectRatio": "3:2"}]


class TestSeveralReferencePictures:
    async def attach_many(self, ctx, n=3):
        saved = await helix_tools.keep_attached(ctx, [(f"p{i}.jpg", base64.b64encode(tiny_jpeg(color=(30 * i, 90, 160))).decode(), "image/jpeg") for i in range(1, n + 1)])
        return [s["item_id"] for s in saved]

    @pytest.mark.asyncio
    async def test_all_the_pictures_attached_to_one_message_go_to_the_image_model(self, setup):
        prov, (store, _c, _s), ctx = setup
        ids = await self.attach_many(ctx, 3)
        early = await helix_tools.picture_request(ctx, "create me a character similar but completely different using the attached images as a sample", True)
        p = helix_tools.PENDING.get("alice")
        assert "based on your 3 pictures" in early["reply"] and p.args["item_ids"] == ids and p.args["count"] == 1 and p.armed
        done = await helix_tools.pre_turn(ctx, "yes")
        assert len(prov.calls) == 1 and isinstance(prov.calls[0]["reference"], list) and len(prov.calls[0]["reference"]) == 3
        assert prov.calls[0]["prompt"].startswith(service.REFERENCE_PREFIX_MANY) and done["ui"][0]["type"] == "saved_many"

    @pytest.mark.asyncio
    async def test_one_picture_is_still_sent_as_a_single_reference(self, setup):
        prov, _shared, ctx = setup
        item_id = await attach(ctx)
        await service.vary_and_save(ctx, item_id, "x", 1)
        assert isinstance(prov.calls[0]["reference"], tuple)

    @pytest.mark.asyncio
    async def test_another_users_picture_in_the_list_is_refused(self, setup):
        prov, shared, ctx = setup
        mine = await attach(ctx)
        bob = make_ctx("bob", *shared)
        theirs = await attach(bob, (5, 5, 5))
        with pytest.raises(MediaServiceError) as e:
            await service.vary_and_save(ctx, mine, "x", 1, extra_item_ids=[theirs])
        assert e.value.status == 404 and prov.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,count", [("make 3 characters like these", 3), ("create a new character", 1), ("generate two different heroes using these as a sample", 2)])
    async def test_creation_requests_with_attachments_are_picture_requests(self, setup, message, count):
        _prov, _shared, ctx = setup
        await self.attach_many(ctx, 2)
        assert await helix_tools.picture_request(ctx, message, True)
        assert helix_tools.PENDING.get("alice").args["count"] == count

    @pytest.mark.asyncio
    async def test_a_creation_request_without_a_picture_noun_is_left_to_the_model(self, setup):
        _prov, _shared, ctx = setup
        await self.attach_many(ctx, 2)
        assert await helix_tools.picture_request(ctx, "write me a poem about this", True) is None

    def test_the_provider_sends_every_reference(self):
        import asyncio

        monkey_seen = {}

        def handler(request: httpx.Request):
            monkey_seen["parts"] = json.loads(request.content)["contents"][0]["parts"]
            out = base64.b64encode(tiny_jpeg()).decode()
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": out}}]}}]})

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                await GeminiImage().generate("x", client=c, reference=[(b"A", "image/png"), (b"B", "image/jpeg")])

        import os

        os.environ["GEMINI_API_KEY"] = "k"
        try:
            asyncio.run(run())
        finally:
            os.environ.pop("GEMINI_API_KEY", None)
        parts = monkey_seen["parts"]
        assert len(parts) == 3 and parts[0]["inlineData"]["mimeType"] == "image/png" and parts[1]["inlineData"]["mimeType"] == "image/jpeg" and parts[2] == {"text": "x"}


class TestSpriteSheet:
    def test_the_prompt_asks_for_a_flat_background_strip_of_the_reference_character(self):
        from openjarvis.server.media import spritesheet

        p = spritesheet.strip_prompt("Attack", 7, "pixel art")
        assert "exactly 7 frames" in p and "magenta" in p and "attack" in p.lower() and "pixel art" in p and "attached character" in p

    def test_rows_and_frames_are_cleaned_and_capped(self):
        from openjarvis.server.media import spritesheet as sp

        assert sp.clean_animations(None) == list(sp.DEFAULT_ANIMATIONS) and len(sp.clean_animations(["x"] * 40)) == 10 and len(sp.clean_animations(["x"] * 40, True)) == 30
        assert sp.clean_frames(None) == 7 and sp.clean_frames(99) == 12 and sp.clean_frames(99, True) == 16 and sp.clean_frames(0) == 2

    def test_a_strip_becomes_seven_frames_and_the_background_goes_transparent(self):
        from openjarvis.server.media import spritesheet as sp

        keyed = sp.key_out_background(strip_png())
        assert keyed.getpixel((2, 2))[3] == 0
        frames = sp.split_strip(keyed, 7)
        assert len(frames) == 7 and all(f.width < 200 for f in frames)
        assert len(sp.split_strip(sp.key_out_background(strip_png(5)), 7)) == 7          # wrong figure count: cut into equal cells instead

    def test_the_sheets_have_the_template_layout(self):
        import io

        from PIL import Image

        from openjarvis.server.media import spritesheet as sp

        labelled, clean = sp.build({"Idle": strip_png(), "Walk": strip_png()}, 7)
        a, c = Image.open(io.BytesIO(labelled)), Image.open(io.BytesIO(clean))
        assert a.size == (sp.LABEL_W + 7 * sp.CELL + sp.PAD, 2 * sp.CELL + 2 * sp.PAD)       # label column, 7 cells, one row each
        assert c.size == (7 * sp.CELL, 2 * sp.CELL) and c.mode == "RGBA" and c.getpixel((2, 2))[3] == 0

    @pytest.mark.asyncio
    async def test_the_tool_confirms_then_saves_a_labelled_and_a_transparent_sheet(self, monkeypatch):
        prov = StripProvider()
        monkeypatch.setattr(image_mod, "get_reference_provider", lambda: prov)
        shared = (FakeStore(), MemoryCatalog(), MemoryStorage())
        ctx = make_ctx("alice", *shared)
        item_id = await attach(ctx)
        out = await helix_tools.execute_tool(ctx, "make_sprite_sheet", {"layout": "rows"}, user_message="make me a sprite sheet")
        assert "NOT RUN YET" in out["text"] and prov.calls == []
        p = helix_tools.PENDING.get("alice")
        assert p.estimate_usd == pytest.approx(0.28) and "Idle, Walk, Run, Jump, Attack, Hurt, Death" in p.description
        p.armed = True
        done = await helix_tools.pre_turn(ctx, "yes")
        assert done["ui"][0]["type"] == "saved_many" and len(done["ui"][0]["items"]) == 2
        assert len(prov.calls) == 7 and all(c["aspect_ratio"] == "21:9" and c["reference"][1] == "image/jpeg" for c in prov.calls)
        assert shared[0].spent("media:alice") == pytest.approx(0.28)
        titles = sorted(i["title"] for i in done["ui"][0]["items"])
        assert titles[0].endswith("(sprite sheet)") and titles[1].endswith("(sprite sheet, transparent)")

    @pytest.mark.asyncio
    async def test_a_sprite_sheet_request_is_set_up_by_the_server_without_any_model(self, setup):
        prov, (store, _c, _s), ctx = setup
        assert await helix_tools.sprite_sheet_request(ctx, "make me a sprite sheet") is None                    # nothing attached yet
        item_id = await attach(ctx)
        early = await helix_tools.sprite_sheet_request(ctx, "make me a sprite sheet of the attached image")
        p = helix_tools.PENDING.get("alice")
        assert "Ready to make a sprite sheet" in early["reply"] and "$0.04" in early["reply"] and p.armed and p.args["item_id"] == item_id and p.args["layout"] == "template"
        again = await helix_tools.sprite_sheet_request(ctx, "make a sprite sheet with transparent frames for unity")
        assert helix_tools.PENDING.get("alice").args["layout"] == "rows" and "$0.28" in again["reply"]
        assert prov.calls == [] and store.spend == {}                                                          # nothing is spent before the yes
        assert await helix_tools.sprite_sheet_request(ctx, "what is a sprite sheet?") is not None                # a mention with a picture still gets the read-back
        assert await helix_tools.sprite_sheet_request(ctx, "/image a fox") is None and await helix_tools.sprite_sheet_request(ctx, "make a fox") is None

    @pytest.mark.asyncio
    async def test_a_row_that_fails_is_left_out_and_not_charged(self, monkeypatch):
        prov = StripProvider(fail_on={3})
        monkeypatch.setattr(image_mod, "get_reference_provider", lambda: prov)
        shared = (FakeStore(), MemoryCatalog(), MemoryStorage())
        ctx = make_ctx("alice", *shared)
        item_id = await attach(ctx)
        res = await service.sprite_sheet_and_save(ctx, item_id, ["Idle", "Walk", "Run"], 7, layout="rows")
        assert len(res["rows"]) == 2 and len(res["missing"]) == 1 and shared[0].spent("media:alice") == pytest.approx(0.08)

    @pytest.mark.asyncio
    async def test_a_busy_provider_is_retried_once(self, monkeypatch):
        monkeypatch.setattr(service, "GEN_RETRIES", 1)
        prov = FlakyProvider(fail_on={1})
        monkeypatch.setattr(image_mod, "get_reference_provider", lambda: prov)
        shared = (FakeStore(), MemoryCatalog(), MemoryStorage())
        ctx = make_ctx("alice", *shared)
        item_id = await attach(ctx)
        res = await service.vary_and_save(ctx, item_id, "x", 1)
        assert len(res["items"]) == 1 and len(prov.calls) == 2 and shared[0].spent("media:alice") == pytest.approx(0.04)

    @pytest.mark.asyncio
    async def test_gemini_gets_the_aspect_ratio_and_repeats_without_it_when_refused(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        seen = []

        def handler(request: httpx.Request):
            cfg = json.loads(request.content)["generationConfig"]
            seen.append(cfg)
            if "imageConfig" in cfg:
                return httpx.Response(400, json={"error": "bad"})
            out = base64.b64encode(tiny_jpeg()).decode()
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": out}}]}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            res = await GeminiImage().generate("x", client=c, aspect_ratio="21:9")
        assert seen[0]["imageConfig"] == {"aspectRatio": "21:9"} and "imageConfig" not in seen[1] and res.data


class TestUnlimitedAccounts:
    """UNLIMITED_EMAILS (default: the owner and verident0115@gmail.com) have no variation, media-spend, storage or attachment caps."""

    def test_who_is_unlimited(self, monkeypatch):
        monkeypatch.delenv("UNLIMITED_EMAILS", raising=False)
        assert hardening.unlimited("verident0115@gmail.com") and hardening.unlimited("Michael.Dano1523@gmail.com")
        assert not hardening.unlimited("someone@else.com") and not hardening.unlimited("")
        assert hardening.unlimited("a@b.io", {"UNLIMITED_EMAILS": "a@b.io"}) and not hardening.unlimited("verident0115@gmail.com", {"UNLIMITED_EMAILS": "a@b.io"})

    def test_the_count_cap_lifts_to_a_ceiling_of_100(self):
        assert service.clamp_variations(40, True) == 40 and service.clamp_variations(5000, True) == 100
        assert service.clamp_variations(40, False) == 10

    @pytest.mark.asyncio
    async def test_no_daily_media_cap_and_no_storage_quota(self, setup, monkeypatch):
        prov, shared, _ctx = setup
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "0.10")
        monkeypatch.setenv("MEDIA_USER_QUOTA_BYTES", "1")
        ctx = make_ctx("verident", *shared)
        ctx.unlimited = True
        item_id = await attach(ctx)
        res = await service.vary_and_save(ctx, item_id, "x", 25)                       # $1.00 against a $0.10 cap, library over its quota
        assert len(res["items"]) == 25 and shared[0].spent("media:verident") == pytest.approx(1.0)   # still logged, so it shows in Out
        capped = make_ctx("someone", *shared)
        with pytest.raises(MediaServiceError):
            await service.vary_and_save(capped, item_id, "x", 3)                       # everyone else keeps the caps (and cannot use another's picture)

    def test_a_big_request_is_refused_unless_the_token_belongs_to_an_unlimited_account(self, monkeypatch):
        from types import SimpleNamespace

        from openjarvis.server import auth_routes
        from openjarvis.server.auth_middleware import AuthMiddleware

        monkeypatch.setenv("JWT_SECRET_KEY", "a-long-random-test-secret-0123456789abcdef")
        big = str(hardening.MAX_BODY_BYTES + 1)
        assert hardening.too_large_for_path("/api/chat", big) and not hardening.too_large_for_path("/api/chat", big, big=True)
        assert hardening.too_large_for_path("/api/chat", str(hardening.UNLIMITED_BODY_BYTES + 1), big=True)

        def caller(email):
            tok = auth_routes.create_token("u1", email) if hasattr(auth_routes, "create_token") else None
            return AuthMiddleware._unlimited_caller(SimpleNamespace(headers={"Authorization": f"Bearer {tok}"}))

        if hasattr(auth_routes, "create_token"):
            assert caller("verident0115@gmail.com") and not caller("someone@else.com")
        assert not AuthMiddleware._unlimited_caller(SimpleNamespace(headers={}))
        assert not AuthMiddleware._unlimited_caller(SimpleNamespace(headers={"Authorization": "Bearer nonsense"}))


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
