"""The image studio's server side: erase and remove_background (transparency kept), and the 'change this' box (an AI edit, confirmed first).

Offline: pictures are made with Pillow; the image model, storage, catalog and spend ledger are in-memory fakes.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from openjarvis.server import hardening
from openjarvis.server.media import edits, helix_tools, service
from openjarvis.server.media import image as image_mod
from openjarvis.server.media.catalog import MemoryCatalog
from openjarvis.server.media.service import MediaServiceError
from openjarvis.server.media.storage import MemoryStorage
from tests.server._media_fakes import FakeProvider, FakeStore, make_ctx, tiny_jpeg


def figure_png(bg=(255, 255, 255), size=(200, 160)) -> bytes:
    """A blue figure on a flat background, with a background-coloured 'eye' inside it that is not connected to the outside."""
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.ellipse([50, 30, 150, 140], fill=(30, 60, 200))
    d.ellipse([90, 70, 110, 90], fill=bg)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def opened(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


class TestRemoveBackground:
    def test_the_outside_goes_and_the_figure_stays(self):
        out, mime = edits.apply_image(figure_png(), "image/png", "remove_background", {})
        im = opened(out)
        assert mime == "image/png" and im.mode == "RGBA"
        assert im.getpixel((2, 2))[3] == 0 and im.getpixel((197, 157))[3] == 0                 # corners are clear
        assert im.getpixel((100, 110))[3] == 255                                              # the figure is solid

    def test_a_same_coloured_detail_inside_is_kept_unless_asked(self):
        kept = opened(edits.apply_image(figure_png(), "image/png", "remove_background", {})[0])
        assert kept.getpixel((100, 80))[3] == 255                                             # the eye is not connected to the edge
        gone = opened(edits.apply_image(figure_png(), "image/png", "remove_background", {"inside": True})[0])
        assert gone.getpixel((100, 80))[3] == 0

    def test_the_background_colour_is_read_from_the_edge_and_tolerance_matters(self):
        im = opened(edits.apply_image(figure_png(bg=(230, 190, 60)), "image/png", "remove_background", {})[0])
        assert im.getpixel((3, 3))[3] == 0 and im.getpixel((100, 110))[3] == 255
        noisy = Image.new("RGB", (100, 100), (255, 255, 255))
        noisy.paste((235, 235, 235), (0, 0, 100, 50))                                          # a slightly different top half
        buf = io.BytesIO()
        noisy.save(buf, "PNG")
        strict = opened(edits.apply_image(buf.getvalue(), "image/png", "remove_background", {"tolerance": 1})[0])
        loose = opened(edits.apply_image(buf.getvalue(), "image/png", "remove_background", {"tolerance": 40})[0])
        assert strict.getpixel((50, 10))[3] == 255 and loose.getpixel((50, 10))[3] == 0

    def test_bad_numbers_and_huge_pictures_are_refused(self):
        with pytest.raises(edits.EditError):
            edits.apply_image(figure_png(), "image/png", "remove_background", {"tolerance": 500})
        old = edits.MAX_BG_PIXELS
        edits.MAX_BG_PIXELS = 100
        try:
            with pytest.raises(edits.EditError) as e:
                edits.apply_image(figure_png(), "image/png", "remove_background", {})
            assert e.value.status_code == 413
        finally:
            edits.MAX_BG_PIXELS = old


class TestErase:
    def test_painted_strokes_become_transparent_and_the_rest_stays(self):
        out, mime = edits.apply_image(figure_png(), "image/png", "erase", {"strokes": [[10, 60, 80, 140, 80]]})
        im = opened(out)
        assert mime == "image/png" and im.getpixel((100, 80))[3] == 0 and im.getpixel((70, 80))[3] == 0      # along the stroke
        assert im.getpixel((100, 130))[3] == 255 and im.getpixel((5, 5))[3] == 255                            # elsewhere untouched

    def test_a_single_dab_is_a_round_hole(self):
        im = opened(edits.apply_image(figure_png(), "image/png", "erase", {"strokes": [[12, 100, 110]]})[0])
        assert im.getpixel((100, 110))[3] == 0 and im.getpixel((100, 135))[3] == 255

    @pytest.mark.parametrize("strokes", [None, [], "x", [[5]], [[5, 1, 2, 3]], [[True, 1, 2]], [["a", 1, 2]], [[5, float("nan"), 2]], [[5, 1e9, 2]], [[5, 1, 2]] * 401])
    def test_bad_strokes_are_refused(self, strokes):
        with pytest.raises(edits.EditError):
            edits.apply_image(figure_png(), "image/png", "erase", {"strokes": strokes})


class TestTransparencyIsKept:
    def test_edits_of_a_transparent_picture_stay_transparent_pngs(self):
        im = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse([20, 10, 100, 70], fill=(200, 40, 40, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        for op, params in [("crop", {"left": 0, "top": 0, "right": 100, "bottom": 60}), ("resize", {"width": 60}), ("brightness", {"factor": 1.5}), ("contrast", {"factor": 0.5})]:
            out, mime = edits.apply_image(buf.getvalue(), "image/png", op, params)
            r = opened(out)
            assert mime == "image/png" and r.mode == "RGBA" and r.getpixel((1, 1))[3] == 0, op

    def test_a_brightness_change_does_not_touch_the_alpha(self):
        im = Image.new("RGBA", (40, 40), (100, 100, 100, 128))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        r = opened(edits.apply_image(buf.getvalue(), "image/png", "brightness", {"factor": 2.0})[0])
        assert r.getpixel((5, 5))[3] == 128 and r.getpixel((5, 5))[0] > 150

    def test_a_jpeg_stays_a_jpeg_and_a_flat_png_stays_a_png(self):
        assert edits.apply_image(tiny_jpeg(), "image/jpeg", "resize", {"width": 32})[1] == "image/jpeg"
        assert edits.apply_image(figure_png(), "image/png", "resize", {"width": 100})[1] == "image/png"

    def test_a_crop_beyond_four_thousand_pixels_is_allowed(self):
        im = Image.new("RGB", (5200, 300), (10, 10, 10))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        r = opened(edits.apply_image(buf.getvalue(), "image/png", "crop", {"left": 4500, "top": 0, "right": 5200, "bottom": 300})[0])
        assert r.size == (700, 300)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("MEDIA_ENABLED", "DAILY_MEDIA_SPEND_CAP_USD", "MEDIA_GLOBAL_DAILY_CAP_USD", "MEDIA_USER_QUOTA_BYTES"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("MEDIA_ENABLED", "1")
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "5")
    monkeypatch.setenv("MEDIA_GLOBAL_DAILY_CAP_USD", "50")
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())
    monkeypatch.setattr(service, "GEN_RETRIES", 0)


class TestStudioPage:
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        from openjarvis.server.app import create_app

        monkeypatch.setenv("JWT_SECRET_KEY", "a-long-random-test-secret-0123456789abcdef")
        monkeypatch.setenv("OPENJARVIS_API_KEY", "test-system-key-0123456789")
        return TestClient(create_app(engine=None, model="m"))

    def test_the_studio_is_a_page_of_its_own_and_holds_no_data(self, monkeypatch):
        r = self.client(monkeypatch).get("/studio?id=anything")                          # no sign-in needed to get the page; the media API checks the login
        assert r.status_code == 200 and "text/html" in r.headers["content-type"] and r.headers["cache-control"] == "no-cache"
        for tool in ("Crop", "Erase", "Remove BG", "Change", "Adjust", "/ai-edit", "remove_background", "'erase'", "aibussol_token"):
            assert tool in r.text, tool

    def test_the_stations_library_opens_pictures_in_the_studio_tab(self):
        from openjarvis.server.crew.station import STATION_PAGE

        assert "window.open('/studio?id='" in STATION_PAGE and "openMedia(rep.id, rep.kind)" in STATION_PAGE
        assert "media-changed" in STATION_PAGE                                          # the studio tells the Station to refresh its library


class TestAiEdit:
    @pytest.fixture
    def setup(self, monkeypatch):
        prov = FakeProvider("image", cost=0.134)
        monkeypatch.setattr(image_mod, "get_reference_provider", lambda: prov)
        shared = (FakeStore(), MemoryCatalog(), MemoryStorage())
        return prov, shared, make_ctx("alice", *shared)

    async def attach(self, ctx):
        import base64

        saved = await helix_tools.keep_attached(ctx, [("a.jpg", base64.b64encode(tiny_jpeg()).decode(), "image/jpeg")])
        return saved[0]["item_id"]

    @pytest.mark.asyncio
    async def test_one_edit_is_made_from_the_typed_instruction_and_charged(self, setup):
        prov, (store, catalog, _s), ctx = setup
        item_id = await self.attach(ctx)
        out = await service.ai_edit_and_save(ctx, item_id, "remove the sword")
        assert len(prov.calls) == 1 and prov.calls[0]["prompt"].startswith(service.EDIT_PREFIX) and prov.calls[0]["prompt"].endswith("remove the sword")
        assert out["item"]["title"].endswith("(edited)") and out["item"]["url"] and out["cost_usd"] == pytest.approx(0.134)
        assert store.spent("media:alice") == pytest.approx(0.134) and catalog.items[out["item"]["id"]].edited_from_id == item_id

    @pytest.mark.asyncio
    async def test_the_caps_and_ownership_still_apply(self, setup, monkeypatch):
        prov, shared, ctx = setup
        item_id = await self.attach(ctx)
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "0.10")
        with pytest.raises(MediaServiceError) as e:
            await service.ai_edit_and_save(ctx, item_id, "remove the sword")
        assert e.value.status == 429 and prov.calls == []
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "5")
        with pytest.raises(MediaServiceError) as e2:
            await service.ai_edit_and_save(make_ctx("bob", *shared), item_id, "remove the sword")
        assert e2.value.status == 404 and prov.calls == []

    def test_the_route_refuses_an_unconfirmed_call_and_reports_the_cost_estimate(self, monkeypatch):
        from fastapi.testclient import TestClient

        from openjarvis.server import auth_routes
        from openjarvis.server.app import create_app
        from openjarvis.server.media.routes import AiEditRequest

        assert AiEditRequest(prompt="remove the sword").confirmed is False
        monkeypatch.setenv("JWT_SECRET_KEY", "a-long-random-test-secret-0123456789abcdef")
        monkeypatch.setenv("OPENJARVIS_API_KEY", "test-system-key-0123456789")
        app = create_app(engine=None, model="m")
        client = TestClient(app)
        hdr = {"Authorization": f"Bearer {auth_routes.create_token('alice', 'a@x.io')}"}
        r = client.post("/api/media/items/00000000-0000-0000-0000-000000000000/ai-edit", json={"prompt": "remove the sword"}, headers=hdr)
        assert r.status_code == 400 and r.json()["error"] == "confirmation_required"
        assert client.get("/api/media/status", headers=hdr).json()["estimates"]["ai_edit"] > 0
