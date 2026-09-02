"""HTTP layer: a signed Razorpay webhook receiver wired to the recovery agent.

``POST /webhooks/razorpay`` — verify the HMAC signature (X-Razorpay-Signature),
map a ``payment.failed`` event onto a FailureEvent, consult the agent policy
(deterministic ladder, optional LLM narration), and answer with the next
recovery action. Everything downstream of the signature check is the same
Policy/EpisodeView socket the simulator uses — one brain, two worlds.

Settings keep the product test-mode-only by construction (see settings.py).
"""

import datetime as dt
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from paypilot.domain.enums import FailureMode, Intervention, MandateRail
from paypilot.domain.models import FailureEvent
from paypilot.engine.agent import AgentPolicy, build_agent_policy
from paypilot.engine.policy import EpisodeView
from paypilot.graph.live_hands import create_payment_link, verify_webhook_signature
from paypilot.settings import Settings, get_settings
from paypilot.voice.node import VoiceNode
from paypilot.voice.script import LLMScriptWriter


def create_app(settings: Settings | None = None, policy: AgentPolicy | None = None) -> FastAPI:
    settings = settings or get_settings()
    agent = policy or build_agent_policy(settings)
    webhook_secret = settings.rzp_webhook_secret.get_secret_value()
    # LLM-written scripts when a key is present; the always-safe template otherwise
    voice_writer = (
        LLMScriptWriter(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.openrouter_model,
        )
        if settings.openrouter_api_key is not None
        else None
    )
    voice = VoiceNode(merchant_name=settings.merchant_name, writer=voice_writer)

    app = FastAPI(title="PayPilot.AI", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.env}

    @app.post("/webhooks/razorpay", response_model=None)
    async def razorpay_webhook(request: Request) -> dict[str, Any] | JSONResponse:
        body = await request.body()
        signature = request.headers.get("X-Razorpay-Signature", "")
        if not verify_webhook_signature(body, signature, webhook_secret):
            return JSONResponse(status_code=401, content={"detail": "invalid signature"})

        try:
            payload = json.loads(body)
            parsed = _parse_failure_event(payload)
        except (KeyError, TypeError, ValueError):
            return JSONResponse(status_code=422, content={"detail": "unsupported payload"})
        if parsed is None:
            return {"handled": False, "detail": "event type ignored"}
        event, rail = parsed
        view = _episode_view(event, rail)
        notes = payload["payload"]["payment"]["entity"].get("notes") or {}
        customer_name = str(notes.get("customer_name", "customer"))
        customer_contact = str(notes.get("customer_contact", ""))

        action = agent.next_action(view)
        reason = agent.ledger.records[-1].reason if agent.ledger.records else ""
        if action is None:
            return {"handled": True, "action": None, "reason": reason}

        response: dict[str, Any] = {
            "handled": True,
            "action": action.intervention.value,
            "run_at": action.run_at.isoformat(),
            "reason": reason,
        }
        if action.intervention is Intervention.VOICE_NUDGE:
            response["voice_call"] = _make_voice_call(
                settings, voice, event, view, customer_name, customer_contact
            )
        return response

    return app


def _make_voice_call(
    settings: Settings,
    voice: VoiceNode,
    event: FailureEvent,
    view: EpisodeView,
    customer_name: str,
    customer_contact: str,
) -> dict[str, Any]:
    """Voice decision → call artifact. Tries a REAL test-mode Payment Link first;
    degrades to a deterministic placeholder so the demo never breaks."""
    episode_key = f"{event.subscription_id}:{event.episode_no}"
    url = create_payment_link(
        settings,
        amount_paise=event.amount_paise,
        episode_key=episode_key,
        attempt_no=event.attempt_no,
        customer_name=customer_name,
        customer_contact=customer_contact or "9876543210",
    )
    if url is None:
        url = f"https://rzp.io/i/{event.subscription_id}-ep{event.episode_no}"
    call = voice.make_call(view, customer_name, url)
    return {
        "script": call.script_hinglish,
        "source": call.source,
        "estimated_duration_seconds": call.estimated_duration_seconds,
        "audio_path": call.audio_path,
    }


def _parse_failure_event(payload: Any) -> tuple[FailureEvent, MandateRail] | None:
    """Map a Razorpay ``payment.failed`` webhook body onto (FailureEvent, rail).

    Subscription/episode context rides in ``notes`` (what our integrations set);
    returns None for any other event type. Raises for malformed bodies.
    """
    if not isinstance(payload, dict) or str(payload.get("event", "")) != "payment.failed":
        return None
    entity = payload["payload"]["payment"]["entity"]
    notes = entity.get("notes") or {}
    mode = FailureMode(str(notes.get("failure_mode", FailureMode.INSUFFICIENT_FUNDS.value)))
    rail = MandateRail(str(notes.get("rail", MandateRail.UPI_AUTOPAY.value)))
    occurred_at = dt.datetime.fromtimestamp(int(entity["created_at"]), tz=dt.UTC)
    event = FailureEvent(
        id=str(entity["id"]),
        subscription_id=str(
            notes.get("subscription_id") or entity.get("subscription_id") or entity["id"]
        ),
        mode=mode,
        occurred_at=occurred_at,
        attempt_no=max(int(notes.get("attempt_no", 1)), 1),
        amount_paise=int(entity["amount"]),
        episode_no=max(int(notes.get("episode_no", 1)), 1),
    )
    return event, rail


def _episode_view(event: FailureEvent, rail: MandateRail) -> EpisodeView:
    """The context the agent may know at webhook time — no simulator peeking."""
    return EpisodeView(
        subscription_id=event.subscription_id,
        episode_no=event.episode_no,
        mode=event.mode,
        amount_paise=event.amount_paise,
        first_failed_at=event.occurred_at,
        attempts_made=event.attempt_no,
        rail=rail,
        billing_day=event.occurred_at.day,
        vertical="unknown",
    )
