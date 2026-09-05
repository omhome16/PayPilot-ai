"""Command Center: served SPA + the JSON surfaces each panel polls."""

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


def test_command_center_page_renders_all_panels() -> None:
    r = _client().get("/command")
    assert r.status_code == 200
    for panel in ("overview", "live", "graph", "voice", "memory", "ledger", "eval", "lab"):
        assert f"panel-{panel}" in r.text


def test_static_assets_are_served() -> None:
    c = _client()
    for path, needle in (("/static/command.css", "--blue"), ("/static/command.js", "refreshLive")):
        r = c.get(path)
        assert r.status_code == 200
        assert needle in r.text


def test_agent_trace_returns_real_node_steps() -> None:
    r = _client().post(
        "/agent/trace",
        content=json.dumps(
            {"mode": "insufficient_funds", "amount_paise": 150_000, "attempts_made": 3}
        ),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    steps = r.json()["steps"]
    nodes = [s["node"] for s in steps]
    assert "sense" in nodes and "think" in nodes and "validate" in nodes
    assert nodes[-1] in ("act", "abstain")
    detail = next(s for s in steps if s["node"] == "think")["detail"]
    assert "action" in detail  # the real BrainProposal, not staged


def test_agent_trace_outage_shows_fail_loud_escalation() -> None:
    r = _client().post(
        "/agent/trace",
        content=json.dumps({"mode": "insufficient_funds", "outage": True}),
        headers={"Content-Type": "application/json"},
    )
    steps = r.json()["steps"]
    nodes = [s["node"] for s in steps]
    assert nodes[-1] == "escalate"
    assert "act" not in nodes  # nothing executed


def test_store_tables_and_rows() -> None:
    c = _client()
    tables = c.get("/store/tables").json()["tables"]
    names = {t["name"] for t in tables}
    assert {"customers", "subscriptions", "decisions", "tool_calls", "consent"} <= names
    rows = c.get("/store/rows?table=customers&limit=5").json()["rows"]
    assert len(rows) == 5
    assert c.get("/store/rows?table=nope").status_code == 404


def test_webhook_decision_persists_to_the_store() -> None:
    """Live decisions land in the demo store — the Ledger + Memory panels are real."""
    c = _client()
    r = c.post(
        "/monitor/fire",
        content=json.dumps({"mode": "mandate_revoked", "amount_paise": 99_900, "attempt_no": 1}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "payment_link"
    decisions = c.get("/store/rows?table=decisions&limit=50").json()["rows"]
    assert any(d["chosen"] == "payment_link" for d in decisions)
    # the fail-loud voice path records a DEGRADED escalation decision (no key in tests)
    r2 = c.post(
        "/monitor/fire",
        content=json.dumps(
            {"mode": "insufficient_funds", "amount_paise": 150_000, "attempt_no": 3}
        ),
        headers={"Content-Type": "application/json"},
    )
    assert r2.json()["action"] == "human_escalation"
    decisions = c.get("/store/rows?table=decisions&limit=50").json()["rows"]
    degraded = [d for d in decisions if d["chosen"] == "human_escalation" and d["degraded"]]
    assert degraded  # the escalation is on the ledger with its degraded reason


def test_quick_eval_returns_real_numbers() -> None:
    r = _client().post(
        "/eval/quick",
        content=json.dumps({"worlds": 1, "size": 80}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["worlds"] == 1 and "wins" in d and "mean_multiplier" in d
    assert d["agent_recovered_rupees"] >= 0
