"""Tests for live hands (mocked HTTP) and the LLM brain (mocked responses)."""

import json

import httpx
import pytest

from paypilot.domain.enums import Intervention
from paypilot.graph.live_hands import (
    LivePaymentLinkError,
    create_payment_link,
    verify_webhook_signature,
)
from paypilot.graph.llm_brain import BrainUnavailable, OpenRouterBrain
from paypilot.settings import Settings


def _settings() -> Settings:
    return Settings(
        rzp_key_id="rzp_test_key123",
        rzp_key_secret="test-only-not-real",  # type: ignore[arg-type]  # noqa: S106
        rzp_webhook_secret="test-only-hook",  # type: ignore[arg-type]  # noqa: S106
        _env_file=None,
    )


class _StubSecret:
    def get_secret_value(self) -> str:
        return "sekrit"


class _StubSettings:
    """Mimics Settings for layer-2 defense tests (layer 1 already refuses live keys)."""

    rzp_key_id: str
    rzp_key_secret: _StubSecret

    def __init__(self, key_id: str) -> None:
        self.rzp_key_id = key_id
        self.rzp_key_secret = _StubSecret()


# --- Payment Links -----------------------------------------------------------------


def test_creates_link_and_uses_idempotency_key(monkeypatch) -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["idem"] = req.headers.get("Idempotency-Key")
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(
            200,
            json={"id": "plink_1", "short_url": "https://rzp.io/rzp/abc123"},
            request=req,
        )

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: handler(
            httpx.Request("POST", url, **{k: v for k, v in kw.items() if k in ("headers", "json")})
        ),
    )
    url = create_payment_link(
        _settings(),
        amount_paise=19_900,
        episode_key="sub_0001:1",
        attempt_no=2,
        customer_name="Priya",
        customer_contact="+919876543210",
    )

    assert url == "https://rzp.io/rzp/abc123"
    assert captured["idem"] == "paypilot:sub_0001:1:a2"  # stable per (episode, attempt)
    assert captured["auth"].startswith("Basic ")
    assert captured["body"]["amount"] == 19_900


def test_live_links_refuse_live_keys() -> None:
    """Layer-2 check: even if a live key bypassed Settings validation, the executor
    refuses. (Layer 1 = Settings validator, tested separately.)"""
    s = _StubSettings("rzp_live_DANGER")
    assert str(s.rzp_key_id).startswith("rzp_live_")  # precondition sanity
    with pytest.raises(LivePaymentLinkError):
        create_payment_link(
            s,
            amount_paise=100,
            episode_key="e",
            attempt_no=1,
            customer_name="x",
            customer_contact="+919876543210",
        )


def test_network_failure_degrades_to_none(monkeypatch) -> None:
    def boom(*a, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    assert (
        create_payment_link(
            _settings(),
            amount_paise=100,
            episode_key="e",
            attempt_no=1,
            customer_name="x",
            customer_contact="+919876543210",
        )
        is None
    )


def test_webhook_signature_verification() -> None:
    import hashlib
    import hmac

    body = b'{"event":"payment_link.paid"}'
    sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, sig, "whsec") is True
    assert verify_webhook_signature(body, "deadbeef", "whsec") is False


# --- LLM Brain ----------------------------------------------------------------------


def _brain(handler) -> OpenRouterBrain:
    return OpenRouterBrain(api_key="k", model="m", transport=httpx.MockTransport(handler))


def test_llm_brain_parses_valid_json_proposal() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "action": "wait_self_heal",
                "on_salary_day": True,
                "days_ahead": 4,
                "reason": "reliable payer near payday",
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 88},
            },
            request=req,
        )

    p = _brain(handler).propose({"mode": "insufficient_funds"})
    assert p.action is Intervention.WAIT_SELF_HEAL and p.on_salary_day is True
    assert p.raw  # untouched model output preserved for audit


def test_llm_brain_repairs_after_bad_json_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        content = (
            "oops not json"
            if calls["n"] == 1
            else json.dumps({"action": "payment_link", "days_ahead": 1, "reason": "win-back"})
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {},
            },
            request=req,
        )

    p = _brain(handler).propose({"mode": "mandate_revoked"})
    assert p.action is Intervention.PAYMENT_LINK
    assert calls["n"] == 2


def test_llm_brain_raises_after_double_parse_failure() -> None:
    """Fail-loud: two unparseable replies raise BrainUnavailable — never a silent
    'lawful default' the guardrails would then execute."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "still not json"}}],
                "usage": {},
            },
            request=req,
        )

    with pytest.raises(BrainUnavailable):
        _brain(handler).propose({"mode": "insufficient_funds"})


def test_llm_brain_raises_on_http_errors() -> None:
    """A down provider raises BrainUnavailable — the graph escalates to humans."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=req)

    with pytest.raises(BrainUnavailable):
        _brain(handler).propose({"mode": "bank_downtime"})
