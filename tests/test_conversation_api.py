"""Voice conversation endpoints: seeded demo store, fail-loud without an LLM key."""

import json

from fastapi.testclient import TestClient

from paypilot.api import create_app
from paypilot.settings import Settings


def _settings() -> Settings:
    return Settings(
        rzp_key_id="rzp_test_key",
        rzp_key_secret="rzp_test_secret",  # noqa: S106 — fixture value
        rzp_webhook_secret="whsec_test_abc",  # noqa: S106 — fixture value
    )


def _client() -> TestClient:
    return TestClient(create_app(settings=_settings()))


def test_voice_demo_lists_seeded_customers() -> None:
    data = _client().get("/voice/demo").json()
    assert data["llm_configured"] is False  # no OPENROUTER key in tests
    assert data["merchant"] == "PayPilot"
    subs = {c["subscription_id"] for c in data["customers"]}
    assert "sub_0001" in subs and "sub_0007" in subs


def test_voice_open_fails_loud_without_llm_key() -> None:
    """No dialogue brain ⇒ the call escalates to human review immediately (fail-loud)."""
    c = _client()
    r = c.post(
        "/voice/open",
        content=json.dumps(
            {
                "subscription_id": "sub_0007",
                "mode": "insufficient_funds",
                "amount_paise": 99_900,
                "attempts_made": 2,
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["llm_configured"] is False
    assert data["session_id"].startswith("call_")
    turn = data["turn"]
    assert turn["degraded"] is True and turn["done"] is True
    assert turn["outcome"]["kind"] == "human"
    assert "no LLM dialogue brain" in turn["note"]
    conv = data["conversation"]
    assert conv["done"] is True


def test_voice_open_retrieves_real_history_via_tools() -> None:
    """sub_0007 was seeded with two past episodes — the session's tool events prove
    context came from the database, not from thin air."""
    c = _client()
    r = c.post(
        "/voice/open",
        content=json.dumps({"subscription_id": "sub_0007"}),
        headers={"Content-Type": "application/json"},
    )
    data = r.json()
    conv = data["conversation"]
    tools = {t["tool"] for t in conv["tool_events"]}
    assert tools == {"lookup_customer", "episode_history", "recent_decisions", "consent_status"}
    history = next(t for t in conv["tool_events"] if t["tool"] == "episode_history")
    assert "2 past failure episode" in history["summary"]


def test_voice_turn_unknown_session_404() -> None:
    r = _client().post(
        "/voice/turn",
        content=json.dumps({"session_id": "nope", "text": "hello"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 404


def test_voice_open_unknown_subscription_404() -> None:
    r = _client().post(
        "/voice/open",
        content=json.dumps({"subscription_id": "sub_9999"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 404


def test_voice_open_bad_mode_422() -> None:
    r = _client().post(
        "/voice/open",
        content=json.dumps({"subscription_id": "sub_0001", "mode": "martian"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
