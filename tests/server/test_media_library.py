"""The media library: storage, catalog, access, quota, presigned URLs, edits, delete. Offline (in-memory storage and catalog, fake providers)."""

from __future__ import annotations

import base64
import datetime as dt
import logging
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from openjarvis.server import auth_routes, hardening
from openjarvis.server.app import create_app
from openjarvis.server.media import edits, service, sigv4
from openjarvis.server.media import image as image_mod
from openjarvis.server.media import sound as sound_mod
from openjarvis.server.media.catalog import (
    MediaItem,
    MemoryCatalog,
    decode_cursor,
    valid_id,
)
from openjarvis.server.media.storage import (
    PRESIGN_SECONDS,
    MemoryStorage,
    R2Storage,
    StorageError,
)
from tests.server._media_fakes import (
    API_KEY,
    JWT_SECRET,
    FakeProvider,
    FakeStore,
    tiny_mp3,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("MEDIA_ENABLED", "DAILY_MEDIA_SPEND_CAP_USD", "MEDIA_GLOBAL_DAILY_CAP_USD", "MEDIA_USER_QUOTA_BYTES", "R2_BUCKET", "R2_ACCESS_KEY_ID",
              "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "RL_MEDIA_READ_USER_MAX", "RL_MEDIA_EDIT_USER_MAX"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())


def media_on(monkeypatch, cap="5"):
    monkeypatch.setenv("MEDIA_ENABLED", "1")
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", cap)


@pytest.fixture
def world(monkeypatch):
    store, storage, catalog = FakeStore(), MemoryStorage(), MemoryCatalog()
    app = create_app(MagicMock(), "m", api_key=API_KEY, require_auth=True)
    app.state.session_store, app.state.media_storage, app.state.media_catalog = store, storage, catalog
    img, snd = FakeProvider("image"), FakeProvider("sound")
    monkeypatch.setitem(image_mod.PROVIDERS, image_mod.DEFAULT_PROVIDER, img)
    monkeypatch.setitem(sound_mod.PROVIDERS, sound_mod.DEFAULT_PROVIDER, snd)
    client = TestClient(app, raise_server_exceptions=False)
    return type("W", (), {"client": client, "store": store, "storage": storage, "catalog": catalog, "img": img, "snd": snd})()


def hdr(uid):
    return {"Authorization": "Bearer " + auth_routes.create_token(uid, uid + "@x.io")}


def make_image(w, uid="alice", prompt="a red fox"):
    r = w.client.post("/api/media/image", json={"prompt": prompt}, headers=hdr(uid))
    assert r.status_code == 200, r.text
    return r.json()["item"]


def make_sound(w, uid="alice", text="hello there"):
    r = w.client.post("/api/media/sound", json={"text": text}, headers=hdr(uid))
    assert r.status_code == 200, r.text
    return r.json()["item"]


# ── saving ──────────────────────────────────────────────────────────────────────────────────────

class TestSave:
    def test_a_generation_is_stored_under_the_users_prefix_and_recorded_in_the_catalog(self, world, monkeypatch):
        media_on(monkeypatch)
        item = make_image(world)
        row = world.catalog.items[item["id"]]
        assert row.user_id == "alice" and row.kind == "image" and row.mime_type == "image/jpeg" and row.provider == "fake_image"
        assert row.storage_key == f"users/alice/{item['id']}.jpg" and row.size_bytes == len(world.storage.objects[row.storage_key][0])
        assert row.prompt == "a red fox" and row.title == "a red fox" and row.edited_from_id is None
        sound = make_sound(world)
        srow = world.catalog.items[sound["id"]]
        assert srow.storage_key.endswith(".mp3") and srow.duration_seconds and 0.5 < srow.duration_seconds < 1.5

    def test_the_answer_never_carries_the_file_the_owner_id_or_the_storage_key(self, world, monkeypatch):
        media_on(monkeypatch)
        r = world.client.post("/api/media/image", json={"prompt": "x"}, headers=hdr("alice"))
        body = r.text
        assert "data_base64" not in body and "storage_key" not in body and '"user_id"' not in body
        assert r.json()["item"]["url"].startswith("memory://")

    def test_no_catalog_row_means_nothing_is_returned_and_no_file_is_left_behind(self, world, monkeypatch):
        media_on(monkeypatch)

        async def boom(item):
            raise RuntimeError("db down")

        monkeypatch.setattr(world.catalog, "insert", boom)
        r = world.client.post("/api/media/image", json={"prompt": "x"}, headers=hdr("alice"))
        assert r.status_code == 500 and "item" not in r.json()
        assert world.storage.objects == {}

    def test_a_storage_failure_returns_an_error_and_no_row(self, world, monkeypatch):
        media_on(monkeypatch)

        async def fail(key, data, ctype):
            raise StorageError()

        monkeypatch.setattr(world.storage, "put", fail)
        r = world.client.post("/api/media/image", json={"prompt": "x"}, headers=hdr("alice"))
        assert r.status_code == 502 and world.catalog.items == {}
        assert world.store.spent("media:alice") > 0                    # the provider call happened, so its cost counts

    def test_without_storage_settings_generation_is_refused_and_no_provider_is_called(self, world, monkeypatch):
        media_on(monkeypatch)
        del world.client.app.state.media_storage
        r = world.client.post("/api/media/image", json={"prompt": "x"}, headers=hdr("alice"))
        assert r.status_code == 503 and r.json()["error"] == "media_storage_not_configured" and world.img.calls == []


# ── private files, owner-only access ────────────────────────────────────────────────────────────

class TestAccess:
    def test_user_b_cannot_read_edit_regenerate_or_delete_user_as_items(self, world, monkeypatch):
        media_on(monkeypatch)
        a_item = make_image(world, "alice")
        iid = a_item["id"]
        b = hdr("bob")
        assert world.client.get(f"/api/media/items/{iid}", headers=b).status_code == 404
        assert world.client.get(f"/api/media/items/{iid}?download=true", headers=b).status_code == 404
        assert world.client.post(f"/api/media/items/{iid}/edit", json={"operation": "brightness", "params": {"factor": 1.5}}, headers=b).status_code == 404
        assert world.client.post(f"/api/media/items/{iid}/edit", json={"operation": "brightness", "params": {"factor": 1.5}, "preview": True}, headers=b).status_code == 404
        assert world.client.post(f"/api/media/items/{iid}/regenerate", json={"prompt": "mine now"}, headers=b).status_code == 404
        assert world.client.delete(f"/api/media/items/{iid}", headers=b).status_code == 404
        assert len(world.img.calls) == 1                                     # bob's regenerate never reached the provider
        assert iid in world.catalog.items and world.storage.objects          # nothing of alice's was touched
        assert world.client.get("/api/media/library", headers=b).json()["items"] == []
        assert [i["id"] for i in world.client.get("/api/media/library", headers=hdr("alice")).json()["items"]] == [iid]

    def test_bad_ids_and_missing_ids_are_404_not_500(self, world, monkeypatch):
        media_on(monkeypatch)
        for bad in ("nope", "00000000-0000-0000-0000-000000000000", "1" * 40, "a" * 200):
            assert world.client.get(f"/api/media/items/{bad}", headers=hdr("alice")).status_code in (404, 405)
        assert not valid_id("x") and valid_id("0a1b2c3d-0000-4000-8000-000000000000")

    def test_everything_needs_a_token(self, world, monkeypatch):
        media_on(monkeypatch)
        item = make_image(world)
        for method, path in (("get", "/api/media/library"), ("get", f"/api/media/items/{item['id']}"), ("delete", f"/api/media/items/{item['id']}")):
            assert getattr(world.client, method)(path).status_code == 401
        assert world.client.post(f"/api/media/items/{item['id']}/edit", json={"operation": "crop"}).status_code == 401
        assert world.client.post(f"/api/media/items/{item['id']}/regenerate", json={"prompt": "x"}).status_code == 401

    def test_the_whole_library_is_off_when_media_is_off(self, world):
        for r in (world.client.get("/api/media/library", headers=hdr("alice")), world.client.get("/api/media/items/" + "0" * 8 + "-0000-4000-8000-" + "0" * 12, headers=hdr("alice")),
                  world.client.post("/api/media/image", json={"prompt": "x"}, headers=hdr("alice"))):
            assert r.status_code == 403 and r.json()["error"] == "media_disabled"

    def test_listing_is_newest_first_filterable_and_paged(self, world, monkeypatch):
        media_on(monkeypatch)
        ids = []
        for i in range(5):
            ids.append(make_image(world, prompt=f"fox {i}")["id"])
            world.catalog.items[ids[-1]].created_at = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=i)
        snd = make_sound(world)
        world.catalog.items[snd["id"]].created_at = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        r1 = world.client.get("/api/media/library?kind=image&limit=2", headers=hdr("alice")).json()
        assert [i["title"] for i in r1["items"]] == ["fox 4", "fox 3"] and r1["next_cursor"]
        r2 = world.client.get(f"/api/media/library?kind=image&limit=2&cursor={r1['next_cursor']}", headers=hdr("alice")).json()
        assert [i["title"] for i in r2["items"]] == ["fox 2", "fox 1"]
        assert [i["kind"] for i in world.client.get("/api/media/library?kind=sound", headers=hdr("alice")).json()["items"]] == ["sound"]
        assert world.client.get("/api/media/library?kind=video", headers=hdr("alice")).json()["items"] == []
        assert decode_cursor("garbage") is None
        assert world.client.get("/api/media/library?limit=0", headers=hdr("alice")).status_code == 422

    def test_browsing_and_editing_have_their_own_rate_limits(self, world, monkeypatch):
        media_on(monkeypatch)
        monkeypatch.setenv("RL_MEDIA_READ_USER_MAX", "2")
        codes = [world.client.get("/api/media/library", headers=hdr("alice")).status_code for _ in range(4)]
        assert codes == [200, 200, 429, 429]
        monkeypatch.setenv("RL_MEDIA_EDIT_USER_MAX", "1")
        item = world.catalog and make_image(world, "bob")
        a = world.client.post(f"/api/media/items/{item['id']}/edit", json={"operation": "brightness", "params": {"factor": 1.1}, "preview": True}, headers=hdr("bob"))
        b = world.client.post(f"/api/media/items/{item['id']}/edit", json={"operation": "brightness", "params": {"factor": 1.1}, "preview": True}, headers=hdr("bob"))
        assert (a.status_code, b.status_code) == (200, 429)


# ── presigned URLs ──────────────────────────────────────────────────────────────────────────────

class TestUrls:
    def test_urls_work_for_fifteen_minutes_and_then_stop(self):
        now = [dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)]
        st = MemoryStorage(clock=lambda: now[0])
        st.objects["users/a/x.jpg"] = (b"bytes", "image/jpeg")
        url = st.presign_get("users/a/x.jpg")
        assert PRESIGN_SECONDS == 900 and st.fetch(url) == b"bytes"
        now[0] += dt.timedelta(minutes=14, seconds=59)
        assert st.fetch(url) == b"bytes"
        now[0] += dt.timedelta(seconds=2)
        with pytest.raises(PermissionError):
            st.fetch(url)

    def test_the_r2_url_is_signed_for_900_seconds_and_names_only_the_host_as_a_signed_header(self):
        st = R2Storage("acct123", "AKEXAMPLE", "SECRETEXAMPLE", "aibussol-media")
        now = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
        url = st.presign_get("users/alice/abc.jpg", now=now)
        assert url.startswith("https://acct123.r2.cloudflarestorage.com/aibussol-media/users/alice/abc.jpg?")
        for part in ("X-Amz-Expires=900", "X-Amz-Algorithm=AWS4-HMAC-SHA256", "X-Amz-SignedHeaders=host", "X-Amz-Date=20261014T120000Z", "%2Fauto%2Fs3%2Faws4_request", "X-Amz-Signature="):
            assert part in url, part
        assert "SECRETEXAMPLE" not in url
        later = st.presign_get("users/alice/abc.jpg", now=now + dt.timedelta(minutes=1))
        assert later != url                                                    # the signature depends on the time
        assert st.presign_get("users/bob/abc.jpg", now=now).split("X-Amz-Signature=")[1] != url.split("X-Amz-Signature=")[1]      # and on the object
        assert "response-content-disposition=attachment" in st.presign_get("users/alice/abc.jpg", filename="fox.jpg", now=now)

    def test_the_signer_matches_the_example_in_aws_documentation(self):
        url = sigv4.presign_get(host="examplebucket.s3.amazonaws.com", path="/test.txt", access_key="AKIAIOSFODNN7EXAMPLE",
                                secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1", expires=86400,
                                now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
        assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")

    def test_the_item_endpoint_hands_out_a_fresh_url_and_a_download_variant(self, world, monkeypatch):
        media_on(monkeypatch)
        item = make_image(world, prompt="Fox in snow")
        got = world.client.get(f"/api/media/items/{item['id']}", headers=hdr("alice")).json()["item"]
        assert got["url"].startswith("memory://") and got["url_expires_in"] == 900
        assert "storage_key" not in got and "user_id" not in got

    @respx.mock
    def test_r2_calls_are_signed_and_the_secret_and_token_never_leak(self, caplog):
        st = R2Storage("acct123", "AKEXAMPLE", "SECRET-do-not-log", "aibussol-media")
        put = respx.put("https://acct123.r2.cloudflarestorage.com/aibussol-media/users/a/1.jpg").mock(return_value=httpx.Response(200))
        import asyncio

        asyncio.run(st.put("users/a/1.jpg", b"data", "image/jpeg"))
        h = put.calls[0].request.headers
        assert h["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKEXAMPLE/") and "SignedHeaders=" in h["authorization"]
        assert "SECRET-do-not-log" not in str(dict(h)) and h["x-amz-content-sha256"] and h["content-type"] == "image/jpeg"
        respx.delete("https://acct123.r2.cloudflarestorage.com/aibussol-media/users/a/1.jpg").mock(return_value=httpx.Response(500, text="SECRET-do-not-log"))
        with caplog.at_level(logging.DEBUG), pytest.raises(StorageError) as e:
            asyncio.run(st.delete("users/a/1.jpg"))
        assert "SECRET-do-not-log" not in caplog.text and "SECRET-do-not-log" not in str(e.value)
        respx.get("https://acct123.r2.cloudflarestorage.com/aibussol-media/users/a/1.jpg").mock(side_effect=httpx.ConnectError("could not connect with SECRET-do-not-log"))
        with caplog.at_level(logging.DEBUG), pytest.raises(StorageError):
            asyncio.run(st.get("users/a/1.jpg"))
        assert "SECRET-do-not-log" not in caplog.text

    def test_r2_settings_come_only_from_the_environment(self):
        assert R2Storage.from_env({}) is None
        assert R2Storage.from_env({"CLOUDFLARE_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "k"}) is None
        s = R2Storage.from_env({"CLOUDFLARE_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s"})
        assert s.bucket == "aibussol-media" and s.host == "a.r2.cloudflarestorage.com"
        assert R2Storage.from_env({"CLOUDFLARE_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "other"}).bucket == "other"


# ── quota ───────────────────────────────────────────────────────────────────────────────────────

class TestQuota:
    def test_the_default_quota_is_one_gigabyte_per_user(self):
        assert service.quota_bytes({}) == 1024 ** 3 and service.quota_bytes({"MEDIA_USER_QUOTA_BYTES": "5000"}) == 5000

    def test_a_full_library_blocks_before_the_provider_is_called(self, world, monkeypatch):
        media_on(monkeypatch)
        item = make_image(world)
        monkeypatch.setenv("MEDIA_USER_QUOTA_BYTES", str(world.catalog.items[item["id"]].size_bytes))
        calls = len(world.img.calls)
        r = world.client.post("/api/media/image", json={"prompt": "again"}, headers=hdr("alice"))
        assert r.status_code == 413 and r.json()["error"] == "storage_quota_reached" and "full" in r.json()["detail"]
        assert len(world.img.calls) == calls                                # no money spent on something that cannot be saved
        assert world.client.post("/api/media/image", json={"prompt": "again"}, headers=hdr("bob")).status_code == 200      # per user

    def test_a_result_that_would_not_fit_is_refused_and_not_saved(self, world, monkeypatch):
        media_on(monkeypatch)
        monkeypatch.setenv("MEDIA_USER_QUOTA_BYTES", "100")                # smaller than any image
        r = world.client.post("/api/media/image", json={"prompt": "x"}, headers=hdr("alice"))
        assert r.status_code == 413 and world.catalog.items == {} and world.storage.objects == {}

    def test_an_edit_that_would_not_fit_is_refused(self, world, monkeypatch):
        media_on(monkeypatch)
        item = make_image(world)
        monkeypatch.setenv("MEDIA_USER_QUOTA_BYTES", str(world.catalog.items[item["id"]].size_bytes + 10))
        r = world.client.post(f"/api/media/items/{item['id']}/edit", json={"operation": "brightness", "params": {"factor": 1.4}}, headers=hdr("alice"))
        assert r.status_code == 413 and len(world.catalog.items) == 1
        p = world.client.post(f"/api/media/items/{item['id']}/edit", json={"operation": "brightness", "params": {"factor": 1.4}, "preview": True}, headers=hdr("alice"))
        assert p.status_code == 200                                         # a preview saves nothing, so it is allowed

    def test_the_usage_is_counted_from_the_catalog_and_deleting_frees_room(self, world, monkeypatch):
        media_on(monkeypatch)
        a, b = make_image(world), make_image(world)
        lib = world.client.get("/api/media/library", headers=hdr("alice")).json()
        assert lib["used_bytes"] == sum(world.catalog.items[i].size_bytes for i in (a["id"], b["id"])) and lib["quota_bytes"] == 1024 ** 3
        world.client.delete(f"/api/media/items/{a['id']}", headers=hdr("alice"))
        assert world.client.get("/api/media/library", headers=hdr("alice")).json()["used_bytes"] == world.catalog.items[b["id"]].size_bytes


# ── edits ───────────────────────────────────────────────────────────────────────────────────────

def _bytes(w, item_id):
    return w.storage.objects[w.catalog.items[item_id].storage_key][0]


class TestImageEdits:
    @pytest.mark.parametrize("op,params", [("crop", {"left": 0, "top": 0, "right": 32, "bottom": 24}), ("resize", {"width": 32}), ("brightness", {"factor": 1.5}), ("contrast", {"factor": 0.5})])
    def test_each_edit_makes_a_new_item_and_leaves_the_original_unchanged(self, world, monkeypatch, op, params):
        media_on(monkeypatch)
        orig = make_image(world)
        before = _bytes(world, orig["id"])
        r = world.client.post(f"/api/media/items/{orig['id']}/edit", json={"operation": op, "params": params}, headers=hdr("alice"))
        assert r.status_code == 200 and r.json()["saved"] is True
        new = r.json()["item"]
        assert new["id"] != orig["id"] and new["edited_from_id"] == orig["id"] and op in new["title"] and new["kind"] == "image"
        assert _bytes(world, orig["id"]) == before and world.catalog.items[orig["id"]].edited_from_id is None
        assert _bytes(world, new["id"]) != before and world.catalog.items[new["id"]].user_id == "alice"
        assert world.catalog.items[orig["id"]].size_bytes == len(before)

    def test_the_results_have_the_expected_size(self, world, monkeypatch):
        media_on(monkeypatch)
        import io

        from PIL import Image

        orig = make_image(world)                                            # 64 x 48
        crop = world.client.post(f"/api/media/items/{orig['id']}/edit", json={"operation": "crop", "params": {"left": 8, "top": 8, "right": 40, "bottom": 32}}, headers=hdr("alice")).json()["item"]
        assert Image.open(io.BytesIO(_bytes(world, crop["id"]))).size == (32, 24)
        res = world.client.post(f"/api/media/items/{orig['id']}/edit", json={"operation": "resize", "params": {"width": 32}}, headers=hdr("alice")).json()["item"]
        assert Image.open(io.BytesIO(_bytes(world, res["id"]))).size == (32, 24)         # aspect ratio kept

    def test_a_preview_returns_the_result_but_saves_nothing(self, world, monkeypatch):
        media_on(monkeypatch)
        orig = make_image(world)
        r = world.client.post(f"/api/media/items/{orig['id']}/edit", json={"operation": "brightness", "params": {"factor": 2}, "preview": True}, headers=hdr("alice"))
        assert r.status_code == 200 and r.json()["saved"] is False and r.json()["preview"]["mime"] == "image/jpeg"
        assert base64.b64decode(r.json()["preview"]["data_base64"]) != _bytes(world, orig["id"])
        assert len(world.catalog.items) == 1 and len(world.storage.objects) == 1

    @pytest.mark.parametrize("op,params", [("crop", {"left": 0, "top": 0, "right": 9999, "bottom": 10}), ("crop", {"left": 5}), ("resize", {}), ("resize", {"width": 99999}),
                                           ("brightness", {"factor": 99}), ("contrast", {"factor": "x"}), ("sharpen", {})])
    def test_bad_requests_are_refused_with_a_message(self, world, monkeypatch, op, params):
        media_on(monkeypatch)
        orig = make_image(world)
        r = world.client.post(f"/api/media/items/{orig['id']}/edit", json={"operation": op, "params": params}, headers=hdr("alice"))
        assert r.status_code in (400, 422) and r.json().get("detail") and len(world.catalog.items) == 1

    def test_video_items_cannot_be_edited(self, world, monkeypatch):
        media_on(monkeypatch)
        vid = MediaItem(id="0a1b2c3d-0000-4000-8000-000000000001", user_id="alice", kind="video", title="v", mime_type="video/mp4", size_bytes=1, storage_key="users/alice/v.mp4")
        world.catalog.items[vid.id] = vid
        r = world.client.post(f"/api/media/items/{vid.id}/edit", json={"operation": "trim", "params": {}}, headers=hdr("alice"))
        assert r.status_code == 400 and "video" in r.json()["detail"].lower()
        assert edits.OPERATIONS["video"] == ()


class TestSoundEdits:
    @pytest.fixture
    def sound(self, world, monkeypatch):
        media_on(monkeypatch)
        world.snd._data = tiny_mp3(3.0)
        return make_sound(world)

    def test_trim_makes_a_shorter_new_item_and_keeps_the_original(self, world, sound):
        before = _bytes(world, sound["id"])
        r = world.client.post(f"/api/media/items/{sound['id']}/edit", json={"operation": "trim", "params": {"start": 0.5, "end": 1.5}}, headers=hdr("alice"))
        assert r.status_code == 200, r.text
        new = r.json()["item"]
        assert new["edited_from_id"] == sound["id"] and new["kind"] == "sound" and 0.7 < new["duration_seconds"] < 1.4 and sound["duration_seconds"] > 2.5
        assert _bytes(world, sound["id"]) == before and _bytes(world, new["id"]) != before

    @pytest.mark.parametrize("op,params", [("fade_in", {"seconds": 1}), ("fade_out", {"seconds": 1}), ("volume", {"factor": 0.5})])
    def test_fades_and_volume_make_new_items(self, world, sound, op, params):
        before = _bytes(world, sound["id"])
        r = world.client.post(f"/api/media/items/{sound['id']}/edit", json={"operation": op, "params": params}, headers=hdr("alice"))
        assert r.status_code == 200, r.text
        new = r.json()["item"]
        assert new["edited_from_id"] == sound["id"] and abs(new["duration_seconds"] - sound["duration_seconds"]) < 0.4
        assert _bytes(world, new["id"]) != before and _bytes(world, sound["id"]) == before

    def test_a_preview_of_a_sound_edit_saves_nothing(self, world, sound):
        r = world.client.post(f"/api/media/items/{sound['id']}/edit", json={"operation": "volume", "params": {"factor": 2}, "preview": True}, headers=hdr("alice"))
        assert r.status_code == 200 and r.json()["saved"] is False and r.json()["preview"]["mime"] == "audio/mpeg"
        assert len(world.catalog.items) == 1

    @pytest.mark.parametrize("op,params", [("trim", {"start": 2, "end": 1}), ("trim", {"start": 50, "end": 60}), ("fade_in", {"seconds": 0}), ("volume", {"factor": 9}), ("reverb", {})])
    def test_bad_sound_edits_are_refused(self, world, sound, op, params):
        r = world.client.post(f"/api/media/items/{sound['id']}/edit", json={"operation": op, "params": params}, headers=hdr("alice"))
        assert r.status_code == 400 and len(world.catalog.items) == 1

    def test_the_ffmpeg_arguments_are_numbers_only(self):
        with pytest.raises(edits.EditError):
            edits.apply_sound(tiny_mp3(1.0), "trim", {"start": "0; rm -rf /", "end": 1})
        assert edits.ffmpeg_exe()


class TestRegenerate:
    def test_makes_a_new_item_from_the_edited_prompt_and_counts_against_the_cap(self, world, monkeypatch):
        media_on(monkeypatch, cap="0.03")
        orig = make_image(world, prompt="a red fox")                       # 0.01
        r = world.client.post(f"/api/media/items/{orig['id']}/regenerate", json={"prompt": "a blue fox"}, headers=hdr("alice"))
        assert r.status_code == 200
        new = r.json()["item"]
        assert new["edited_from_id"] == orig["id"] and new["prompt"] == "a blue fox" and new["id"] != orig["id"]
        assert world.catalog.items[orig["id"]].prompt == "a red fox"
        assert round(world.store.spent("media:alice"), 4) == 0.02
        world.client.post(f"/api/media/items/{orig['id']}/regenerate", json={"prompt": "third"}, headers=hdr("alice"))     # 0.03
        calls = len(world.img.calls)
        r = world.client.post(f"/api/media/items/{orig['id']}/regenerate", json={"prompt": "fourth"}, headers=hdr("alice"))
        assert r.status_code == 429 and len(world.img.calls) == calls          # the cap is checked before the provider

    def test_regenerating_a_sound_uses_the_speech_provider(self, world, monkeypatch):
        media_on(monkeypatch)
        orig = make_sound(world, text="hello")
        r = world.client.post(f"/api/media/items/{orig['id']}/regenerate", json={"prompt": "goodbye"}, headers=hdr("alice"))
        assert r.status_code == 200 and world.snd.calls[-1]["text"] == "goodbye" and r.json()["item"]["kind"] == "sound"

    def test_regenerate_is_refused_when_media_has_no_budget(self, world, monkeypatch):
        media_on(monkeypatch)
        orig = make_image(world)
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "0")
        calls = len(world.img.calls)
        assert world.client.post(f"/api/media/items/{orig['id']}/regenerate", json={"prompt": "x"}, headers=hdr("alice")).status_code == 403
        assert len(world.img.calls) == calls


# ── delete ──────────────────────────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_removes_the_object_and_the_row(self, world, monkeypatch):
        media_on(monkeypatch)
        keep, gone = make_image(world), make_image(world)
        key = world.catalog.items[gone["id"]].storage_key
        r = world.client.delete(f"/api/media/items/{gone['id']}", headers=hdr("alice"))
        assert r.status_code == 200 and gone["id"] not in world.catalog.items and key not in world.storage.objects
        assert keep["id"] in world.catalog.items and len(world.storage.objects) == 1
        assert world.client.get(f"/api/media/items/{gone['id']}", headers=hdr("alice")).status_code == 404
        assert world.client.delete(f"/api/media/items/{gone['id']}", headers=hdr("alice")).status_code == 404

    def test_if_the_file_cannot_be_deleted_the_row_is_kept(self, world, monkeypatch):
        media_on(monkeypatch)
        item = make_image(world)
        world.storage.fail_delete = True
        r = world.client.delete(f"/api/media/items/{item['id']}", headers=hdr("alice"))
        assert r.status_code == 502 and item["id"] in world.catalog.items

    def test_deleting_an_original_keeps_its_edits(self, world, monkeypatch):
        media_on(monkeypatch)
        orig = make_image(world)
        new = world.client.post(f"/api/media/items/{orig['id']}/edit", json={"operation": "brightness", "params": {"factor": 1.3}}, headers=hdr("alice")).json()["item"]
        world.client.delete(f"/api/media/items/{orig['id']}", headers=hdr("alice"))
        assert new["id"] in world.catalog.items and world.catalog.items[new["id"]].edited_from_id is None
        assert world.storage.objects                                           # the edit's file is still there


# ── schema and UI wiring ────────────────────────────────────────────────────────────────────────

class TestSchemaAndUi:
    def test_the_media_items_table_has_the_requested_columns_and_index(self):
        from openjarvis.server.media.catalog import MEDIA_ITEMS_DDL as ddl

        for col in ("id               UUID PRIMARY KEY", "user_id", "kind", "title", "prompt", "provider", "mime_type", "size_bytes", "storage_key",
                    "duration_seconds", "edited_from_id   UUID REFERENCES media_items (id)", "created_at"):
            assert col in ddl, col
        assert "CHECK (kind IN ('image', 'sound', 'video'))" in ddl and "media_items_user_created_idx ON media_items (user_id, created_at)" in ddl
        assert "IF NOT EXISTS" in ddl and "DROP" not in ddl.upper()
        from pathlib import Path

        import openjarvis.server.session_store as ss

        assert "MEDIA_ITEMS_DDL" in Path(ss.__file__).read_text(encoding="utf-8")

    def test_the_page_has_the_library_sidebar_the_viewer_panel_and_the_open_button(self):
        from pathlib import Path

        import openjarvis.server.atlas_ui as ui

        html = Path(ui.__file__).read_text(encoding="utf-8")
        assert 'id="library-block"' in html and ">Library<" in html
        for label in (">All<", ">Images<", ">Sound<", ">Video<"):
            assert label in html
        core, viewer = html.index('id="core-area"'), html.index('id="viewer-panel"')
        assert core < viewer < html.index('id="panel-right"')                  # the viewer sits in the center column, under the core interface
        for word in ("'Download'", "'Re-generate'", "'Delete'", "'Preview'", "'Save as new'", "'Discard'"):
            assert word in html, word
        assert "function addOpenButton" in html and "'Saved: '" in html
        send = html[html.index("async function sendMedia"):html.index("// CHANGE 3")]
        assert "data:" not in send and "createElement('img')" not in send and "createElement('audio')" not in send      # the file is never put in the chat
        assert "/image" in html and "/sound" in html and "(image|sound)" in html                                       # the shortcuts still work

    def test_video_is_not_offered(self):
        from openjarvis.server.media.routes import router

        assert not any("video" in getattr(r, "path", "") for r in router.routes)
