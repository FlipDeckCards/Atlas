"""Robinhood Crypto connector: signing, configuration and what Helix is told about it (no network calls)."""

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openjarvis.server.crew import connections, robinhood


def _env(api_key="test-key-id"):
    raw = bytes(range(32))
    return {"ROBINHOOD_API_KEY": api_key,
            "ROBINHOOD_PRIVATE_KEY": base64.b64encode(raw).decode(),
            "ROBINHOOD_PUBLIC_KEY": "unused-for-signing"}, raw


def test_configured_needs_key_and_private_key():
    env, _ = _env()
    assert robinhood.configured(env)
    assert not robinhood.configured({"ROBINHOOD_API_KEY": "k"})
    assert not robinhood.configured({})


def test_signature_verifies_against_the_documented_message():
    env, raw = _env()
    headers = robinhood.sign_headers("GET", robinhood.ACCOUNTS_PATH, "", env, ts=1700000000)
    assert headers["x-api-key"] == "test-key-id"
    assert headers["x-timestamp"] == "1700000000"
    message = f"test-key-id1700000000{robinhood.ACCOUNTS_PATH}GET".encode()
    pub = Ed25519PrivateKey.from_private_bytes(raw).public_key()
    pub.verify(base64.b64decode(headers["x-signature"]), message)   # raises if the signature is wrong


def test_bad_private_key_is_refused_without_echoing_it():
    env = {"ROBINHOOD_API_KEY": "k", "ROBINHOOD_PRIVATE_KEY": base64.b64encode(b"short").decode()}
    try:
        robinhood.sign_headers("GET", robinhood.ACCOUNTS_PATH, "", env)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "32 bytes" in str(e)
        assert base64.b64encode(b"short").decode() not in str(e)


def test_helix_is_told_when_connected_without_actions():
    status = {"robinhood": {"connected": True, "built": True, "label": "Robinhood (crypto)"}}
    text = connections.describe_for_agent(status)
    assert "Robinhood (crypto): connected" in text


def test_helix_is_told_when_not_connected():
    status = {"robinhood": {"connected": False, "built": True, "label": "Robinhood (crypto)"}}
    text = connections.describe_for_agent(status)
    assert "Robinhood (crypto): NOT connected" in text
