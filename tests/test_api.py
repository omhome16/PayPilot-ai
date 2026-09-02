"""Webhook receiver: signature gate → event mapping → agent decision.

The product front door — a forged or unsigned webhook never reaches the agent,
and every payment.failed event produces the same decision the simulator would.
"""

import hashlib
import hmac
import json
from typing import Any

from fastapi.testclient import TestClient

from paypilot.api import create_app
from paypilot.settings import Settings

_SECRET = "whsec_test_abc"  # noqa: S105 — fixture value, not a credential


def _settings() -> Settings:
    return Settings(
        rzp_key_id="rzp_test_key",
        rzp_key_secret="rzp_test_secret",  # noqa: S106 — fixture value, not a credential
        rzp_webhook_secret=_SECRET,
    )


def _client() -> TestClient:
    return TestClient(create_app(settings=_settings()))


def _signed_headers(body: bytes) -> dict[str, str]:
    sig = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Razorpay-Signature": sig, "Content-Type": "application/json"}


def _failure_payload(**note_overrides: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {
        "failure_mode": "insufficient_funds",
        "subscription_id": "sub_live_1",
    }
    notes.update(note_overrides)
    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_1",
                    "amount": 150_000,
                    "currency": "INR",
                    "created_at": 1_788_000_000,
                    "notes": notes,
                }
            }
        },
    }


def _post(client: TestClient, payload: dict[str, Any], *, sign: bool = True):
    body = json.dumps(payload).encode()
    headers = _signed_headers(body) if sign else {"Content-Type": "application/json"}
    return client.post("/webhooks/razorpay", content=body, headers=headers)


def test_health() -> None:
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_rejects_unsigned_and_forged_webhooks() -> None:
    c = _client()
    unsigned = _post(c, _failure_payload(), sign=False)
    forged_body = json.dumps(_failure_payload()).encode()
    forged_sig = hmac.new(b"wrong-secret", forged_body, hashlib.sha256).hexdigest()
    forged = c.post(
        "/webhooks/razorpay",
        content=forged_body,
        headers={"X-Razorpay-Signature": forged_sig, "Content-Type": "application/json"},
    )
    assert unsigned.status_code == 401
    assert forged.status_code == 401


def test_payment_failed_returns_the_agent_decision() -> None:
    r = _post(_client(), _failure_payload())
    assert r.status_code == 200
    data = r.json()
    assert data["handled"] is True
    # deterministic ladder: fresh insufficient-funds episode → salary-timed smart retry
    assert data["action"] == "smart_retry"
    assert data["reason"]
    assert "T" in data["run_at"]  # ISO instant


def test_consent_broken_failure_routes_to_win_back_link() -> None:
    r = _post(_client(), _failure_payload(failure_mode="mandate_revoked"))
    assert r.status_code == 200
    assert r.json()["action"] == "payment_link"


def test_non_failure_event_is_ignored() -> None:
    payload = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_2"}}}}
    r = _post(_client(), payload)
    assert r.status_code == 200
    assert r.json()["handled"] is False


def test_unknown_failure_mode_maps_to_422() -> None:
    r = _post(_client(), _failure_payload(failure_mode="martian_protocol"))
    assert r.status_code == 422


def test_zero_amount_maps_to_422() -> None:
    payload = _failure_payload()
    payload["payload"]["payment"]["entity"]["amount"] = 0
    r = _post(_client(), payload)
    assert r.status_code == 422


def test_voice_decision_returns_safe_call_artifact() -> None:
    """Big-ticket, thrice-failed insufficient-funds → VOICE_NUDGE → the response
    carries a generated Hinglish call script with the mandatory opt-out."""
    payload = _failure_payload(attempt_no=3)
    payload["payload"]["payment"]["entity"]["amount"] = 150_000
    r = _post(_client(), payload)
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "voice_nudge"
    vc = data["voice_call"]
    assert vc["source"] == "template"  # no OPENROUTER key in test settings
    assert vc["audio_path"] is None
    assert vc["estimated_duration_seconds"] > 0
    low = vc["script"].lower()
    assert "rok denge" in low or "pause" in low
    assert "₹1,500" in vc["script"]
    strategy = vc["strategy"]
    assert strategy["failed_attempts"] == 3
    assert strategy["days_to_salary"] == 3  # 2026-08-29 IST → Sep 1 salary
    assert strategy["safety"]["ok"] is True and strategy["safety"]["violations"] == []


def test_monitor_page_renders() -> None:
    r = _client().get("/monitor")
    assert r.status_code == 200
    assert "Live Recovery Monitor" in r.text
    assert "monitor/simulate" in r.text


def test_monitor_simulate_scenarios_drive_the_agent() -> None:
    c = _client()
    expected = {"fresh_funds": "smart_retry", "revoked": "payment_link", "voice": "voice_nudge"}
    for scenario, action in expected.items():
        r = c.post(
            "/monitor/simulate",
            content=json.dumps({"scenario": scenario}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200, scenario
        assert r.json()["action"] == action, scenario

    data = c.get("/monitor/data").json()
    actions = [e["action"] for e in data["events"]]
    for action in expected.values():
        assert action in actions
    counters = data["counters"]
    assert counters["events"] >= 3 and counters["voice_nudge"] >= 1
    voice_event = next(e for e in data["events"] if e["action"] == "voice_nudge")
    assert voice_event["voice"]["script"]
    assert voice_event["voice"]["strategy"]["safety"]["ok"] is True


def test_monitor_simulate_unknown_scenario_422() -> None:
    r = _client().post(
        "/monitor/simulate",
        content=json.dumps({"scenario": "lunar_eclipse"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
