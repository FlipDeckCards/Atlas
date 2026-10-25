"""Google Play connection, offline: a service account key is signed into a JWT, traded for a token, and used to read reviews.
A fake Google answers; nothing real is called and no real key is used."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from openjarvis.server.crew import connections as conns
from openjarvis.server.crew import guide
from openjarvis.server.crew import tenant
from openjarvis.server.crew.store import MemoryCrewStore

PKG = "com.example.app"


def _key_json() -> str:
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "client_email": "play-reader@proj.iam.gserviceaccount.com", "private_key": pem,
                       "token_uri": "https://oauth2.googleapis.com/token"})


def _factory(review_status=200):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "oauth2.googleapis.com":
            assert b"jwt-bearer" in req.content
            return httpx.Response(200, json={"access_token": "tok"})
        assert req.headers["authorization"] == "Bearer tok"
        if review_status != 200:
            return httpx.Response(review_status, json={})
        return httpx.Response(200, json={"reviews": [{"comments": [{"userComment": {"starRating": 5, "text": "Love it", "lastModified": {"seconds": "1790000000"}}}]}]})

    return (lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


def run(c):
    return asyncio.run(c)


def test_connect_tests_the_key_then_stores_it_encrypted():
    store, cf, key = MemoryCrewStore(), _factory()[0], _key_json()
    msg = run(guide.test_and_save(store, "u", "google_play", key, PKG, cf))
    assert "connected" in msg
    assert "BEGIN PRIVATE KEY" not in (run(store.get_secret("u", "play_key")) or "")
    assert run(tenant.get(store, "u", "play_key")) == key


def test_connect_explains_a_missing_permission_without_saving():
    store = MemoryCrewStore()
    with pytest.raises(guide.GuideError, match="not given this key access"):
        run(guide.test_and_save(store, "u", "google_play", _key_json(), PKG, _factory(403)[0]))
    assert run(store.get_secret("u", "play_key")) is None


def test_a_bad_paste_is_refused_and_never_echoed():
    with pytest.raises(guide.GuideError) as e:
        run(guide.test_and_save(MemoryCrewStore(), "u", "google_play", '{"type": "service_account", "private_key": "SECRETTEXT"}', PKG, _factory()[0]))
    assert "SECRETTEXT" not in str(e.value)


def test_list_reviews_reads_newest_reviews_and_status_shows_connected():
    key = _key_json()
    env = {"GOOGLE_PLAY_KEY": key, "GOOGLE_PLAY_APPS": PKG}
    st = run(conns.status(MemoryCrewStore(), "u", env))["google_play"]
    assert st["connected"] and st["built"]
    out = run(conns.run("google_play", "list_reviews", {}, store=MemoryCrewStore(), user_id="u", env=env, client_factory=_factory()[0]))
    assert PKG in out and "5/5" in out and "Love it" in out
    assert conns.risk_of("google_play", "list_reviews") == "read"


def test_not_connected_without_a_key():
    assert not run(conns.status(MemoryCrewStore(), "u", {}))["google_play"]["connected"]


def _reviews_factory(replied=False):
    posts = []
    now = int(__import__("time").time())

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok"})
        if req.method == "POST":
            posts.append((str(req.url), json.loads(req.content)))
            return httpx.Response(200, json={"result": {"replyText": "ok"}})
        comments = [{"userComment": {"starRating": 2, "text": "Crashes", "lastModified": {"seconds": str(now)}}}]
        if replied:
            comments.append({"developerComment": {"text": "Sorry!"}})
        return httpx.Response(200, json={"reviews": [{"reviewId": "gp:AOqpTOEabc123", "comments": comments}]})

    return (lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))), posts


def test_week_summary_and_brief_line():
    from openjarvis.server.crew import herald

    env = {"GOOGLE_PLAY_KEY": _key_json(), "GOOGLE_PLAY_APPS": PKG}
    wk = run(conns.play_week(env, _reviews_factory()[0]))
    assert wk["new"] == 1 and wk["unanswered"] == 1 and wk["stars"] == 2
    assert herald.play_line(wk) == "- Google Play: 1 new review this week, 2.0 stars, 1 need a reply."
    wk2 = run(conns.play_week(env, _reviews_factory(replied=True)[0]))
    assert herald.play_line(wk2).endswith("all answered.")
    assert herald.play_line({"new": 0}) == "- Google Play: no new reviews this week."
    assert "could not read" in herald.play_line(None, "ConnectionError_")


def test_listing_shows_ids_and_reply_status_and_a_reply_is_a_checkpoint():
    env = {"GOOGLE_PLAY_KEY": _key_json(), "GOOGLE_PLAY_APPS": PKG}
    out = run(conns.run("google_play", "list_reviews", {}, store=MemoryCrewStore(), user_id="u", env=env, client_factory=_reviews_factory()[0]))
    assert "id=gp:AOqpTOEabc123" in out and "no reply yet" in out
    assert conns.risk_of("google_play", "reply_review") == "checkpoint"
    text = conns.describe_action("google_play", "reply_review", {"package": PKG, "review_id": "gp:AOqpTOEabc123", "reply_text": "Thanks, we will look into it."})
    assert "PUBLIC" in text and "Thanks, we will look into it." in text


def test_reply_posts_once_to_the_right_review_and_refuses_bad_input():
    env = {"GOOGLE_PLAY_KEY": _key_json(), "GOOGLE_PLAY_APPS": PKG}
    cf, posts = _reviews_factory()
    ok = {"package": PKG, "review_id": "gp:AOqpTOEabc123", "reply_text": "Thank you for telling us."}
    run(conns.run("google_play", "reply_review", ok, store=MemoryCrewStore(), user_id="u", env=env, client_factory=cf))
    assert len(posts) == 1 and posts[0][0].endswith(f"/{PKG}/reviews/gp:AOqpTOEabc123:reply") and posts[0][1] == {"replyText": "Thank you for telling us."}
    for bad in ({**ok, "reply_text": "x" * 351}, {**ok, "package": "com.other.app"}, {**ok, "review_id": "../x"}, {**ok, "reply_text": ""}):
        with pytest.raises(conns.ConnectionError_):
            run(conns.run("google_play", "reply_review", bad, store=MemoryCrewStore(), user_id="u", env=env, client_factory=cf))
    assert len(posts) == 1
