"""P5 integration: VOICE_NUDGE in the graph produces a compliance-checked VoiceCall."""

import datetime as dt

from paypilot.domain.enums import FailureMode
from paypilot.engine.policy import EpisodeView
from paypilot.voice.node import VoiceNode, make_voice_node


def _view():
    return EpisodeView(
        subscription_id="sub_0001",
        episode_no=1,
        mode=FailureMode.INSUFFICIENT_FUNDS,
        amount_paise=149_900,
        first_failed_at=dt.datetime(2026, 9, 27, 5, 0, tzinfo=dt.UTC),
        attempts_made=2,
        rail=None,
        billing_day=27,
        vertical="gym",
    )


def test_voice_node_produces_safe_call_artifact() -> None:
    node = VoiceNode(merchant_name="FitZone")
    call = node.make_call(
        episode=_view(),
        customer_name="Priya",
        payment_url="https://rzp.io/rzp/abc123",
    )
    assert call.script_hinglish
    assert "Priya" in call.script_hinglish and "1,499" in call.script_hinglish
    assert call.audio_path is None  # TTS backend not attached yet


def test_make_voice_node_factory_uses_template_by_default() -> None:
    make = make_voice_node(merchant_name="StreamFlix")
    call = make(_view(), "Rahul", "https://rzp.io/rzp/x")
    assert call.merchant_name == "StreamFlix"


def test_voice_node_rejects_unsafe_llm_script() -> None:
    """Even if an LLM writer is plugged in, unsafe output never becomes a call."""
    import httpx

    from paypilot.voice.script import LLMScriptWriter

    def handler(req: httpx.Request) -> httpx.Response:
        content = '{"text": "Pay Rs 1499 now or legal action will be taken."}'
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
            request=req,
        )

    llm = LLMScriptWriter(api_key="k", model="m", transport=httpx.MockTransport(handler))
    node = VoiceNode(merchant_name="FitZone", writer=llm)
    call = node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    # fell back to the safe template — no threats in the artifact
    assert "legal action" not in call.script_hinglish.lower()
    assert call.source == "template"


def test_salary_line_fires_only_near_payday() -> None:
    """days_to_salary is computed from the episode calendar — the salary line is
    no longer dead code: it appears near payday and never far from it."""
    node = VoiceNode(merchant_name="FitZone")
    near = node.make_call(_view(), "Priya", "https://rzp.io/rzp/a")  # failed 27 Sep → ~4d
    assert "Salary bhi aa hi rahi hogi" in near.script_hinglish

    far = EpisodeView(
        subscription_id="sub_0001",
        episode_no=1,
        mode=FailureMode.INSUFFICIENT_FUNDS,
        amount_paise=149_900,
        first_failed_at=dt.datetime(2026, 9, 5, 5, 0, tzinfo=dt.UTC),  # 5 Sep → ~26d to payday
        attempts_made=2,
        rail=None,
        billing_day=5,
        vertical="gym",
    )
    far_call = node.make_call(far, "Priya", "https://rzp.io/rzp/b")
    assert "Salary bhi aa hi rahi hogi" not in far_call.script_hinglish


def test_strict_gate_drops_script_without_optout() -> None:
    """The node enforces the FULL DR12 policy (opt-out + merchant ID) itself —
    a writer that skips opt-out cannot ship a call artifact."""

    class _NoOptOutWriter:
        def write(self, ctx):
            from paypilot.voice.script import CallScript

            return CallScript(
                text=(
                    f"Namaste Priya ji! Main {ctx['merchant']} ki taraf se bol rahi hoon. "
                    f"Aapka ₹1,499 ka payment pending hai. Link: {ctx['payment_url']}"
                ),
                source="llm",
            )

    node = VoiceNode(merchant_name="FitZone", writer=_NoOptOutWriter())
    call = node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    assert call.source == "template"  # degraded to the always-safe script
    assert "rok denge" in call.script_hinglish or "pause" in call.script_hinglish
