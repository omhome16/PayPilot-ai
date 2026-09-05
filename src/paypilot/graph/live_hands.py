"""Live hands: REAL Payment Links on Razorpay test API (Phase-0-spike-proven path).

Idempotency: every creation carries a unique per-(episode, attempt) key so network
retries can never double-create a link. Errors degrade to None — the graph then
falls back to simulated handling; a live-API outage never breaks a run.
"""

import base64
import hashlib
import hmac

import httpx

from paypilot.settings import Settings


class LivePaymentLinkError(RuntimeError):
    """Raised only for configuration problems (bad/missing keys), not network blips."""


def _basic_auth(key_id: str, secret: str) -> str:
    return base64.b64encode(f"{key_id}:{secret}".encode()).decode()


def create_payment_link(
    settings: Settings,
    *,
    amount_paise: int,
    episode_key: str,
    attempt_no: int,
    customer_name: str,
    customer_contact: str,
    description: str = "PayPilot recovery payment link",
) -> str | None:
    """Create a real (test-mode) Payment Link. Returns short_url or None on failure.

    Idempotency-Key derives from episode+attempt, so retrying this function after a
    timeout re-fetches the SAME link instead of creating a duplicate.
    """
    if not settings.rzp_key_id.startswith("rzp_test_"):
        raise LivePaymentLinkError("live links allowed only with TEST-mode keys")

    idem = f"paypilot:{episode_key}:a{attempt_no}"
    secret = settings.rzp_key_secret.get_secret_value()
    try:
        resp = httpx.post(
            "https://api.razorpay.com/v1/payment_links",
            headers={
                "Authorization": f"Basic {_basic_auth(settings.rzp_key_id, secret)}",
                "Content-Type": "application/json",
                "Idempotency-Key": idem,
            },
            json={
                "amount": amount_paise,
                "currency": "INR",
                "accept_partial": False,
                "description": f"{description} [{idem}]",
                "customer": {"name": customer_name, "contact": customer_contact},
                "notify": {"sms": False, "email": False},
                "notes": {"episode_key": episode_key, "attempt": str(attempt_no)},
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        short = data.get("short_url")
        return str(short) if short else None
    except httpx.HTTPError:
        # Network/5xx/validation blip → graceful degradation; caller falls back.
        return None


def verify_webhook_signature(body: bytes, signature: str, webhook_secret: str) -> bool:
    """HMAC-SHA256 check of Razorpay webhook payloads (docs: X-Razorpay-Signature)."""
    expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
