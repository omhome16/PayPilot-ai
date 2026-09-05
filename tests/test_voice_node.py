"""P5 integration: VOICE_NUDGE in the graph produces a compliance-checked VoiceCall."""

import datetime as dt
from pathlib import Path

import pytest

from paypilot.domain.enums import FailureMode
from paypilot.engine.policy import EpisodeView
from paypilot.voice.node import (
    DoNotCallError,
    VoiceChannelUnavailable,
    VoiceNode,
    make_voice_node,
)
from paypilot.voice.script import ScriptSafetyError, TemplateScriptWriter
from paypilot.voice.tts import NoopTTS

REF = TemplateScriptWriter()  # the deterministic reference writer (explicit, fail-loud)


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
    node = VoiceNode(merchant_name="FitZone", writer=REF)
    call = node.make_call(
        episode=_view(),
        customer_name="Priya",
        payment_url="https://rzp.io/rzp/abc123",
    )
    assert call.script_hinglish
    assert "Priya" in call.script_hinglish and "1,499" in call.script_hinglish
    assert call.audio_path is None  # TTS backend not attached yet
    assert call.source == "template"


def test_make_voice_node_factory_uses_explicit_reference_writer() -> None:
    make = make_voice_node(merchant_name="StreamFlix", writer=TemplateScriptWriter())
    call = make(_view(), "Rahul", "https://rzp.io/rzp/x")
    assert call.merchant_name == "StreamFlix"


def test_voice_node_without_writer_is_fail_loud() -> None:
    """A node with NO writer cannot speak: make_call raises (fail-loud) rather
    than silently producing a canned script for a live LLM decision."""
    node = VoiceNode(merchant_name="FitZone")  # writer=None ⇒ channel unavailable
    assert node.writer is None
    with pytest.raises(VoiceChannelUnavailable):
        node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    assert node.calls == []  # zero artifacts


def test_do_not_call_registry_refuses_artifacts() -> None:
    """Consent has teeth: an opted-out customer never receives a call artifact."""
    node = VoiceNode(merchant_name="FitZone", writer=REF)
    assert not node.is_opted_out("sub_0001:1")
    node.mark_opted_out("sub_0001:1")
    assert node.is_opted_out("sub_0001:1")
    with pytest.raises(DoNotCallError):
        node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    assert node.calls == []  # nothing was produced


def test_tts_engine_populates_audio_path() -> None:
    """With a TTS engine attached, an approved call gets a real audio file."""

    class _FakeTTS:
        def render(self, script: str, out_path: Path) -> Path | None:
            out_path.write_text("fake audio", encoding="utf-8")
            return out_path

    node = VoiceNode(merchant_name="FitZone", writer=REF, tts=_FakeTTS())
    call = node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    assert call.audio_path is not None
    assert Path(call.audio_path).exists()


def test_tts_failure_degrades_to_no_audio() -> None:
    """A broken TTS backend never breaks the recovery: audio_path stays None."""

    class _BrokenTTS:
        def render(self, script: str, out_path: Path) -> Path | None:
            raise RuntimeError("tts down")

    node = VoiceNode(merchant_name="FitZone", writer=REF, tts=_BrokenTTS())
    call = node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    assert call.audio_path is None
    assert call.script_hinglish  # the call itself still exists and is safe


def test_noop_tts_is_the_offline_default() -> None:
    assert NoopTTS().render("some script", Path("x.mp3")) is None


def test_voice_node_raises_on_unsafe_llm_script() -> None:
    """Even if an LLM writer is plugged in, unsafe output never becomes a call —
    and it never silently becomes the template: the node raises (fail-loud)."""
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
    with pytest.raises(ScriptSafetyError):
        node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    assert node.calls == []  # zero artifacts — the escalation is visible upstream


def test_salary_line_fires_only_near_payday() -> None:
    """days_to_salary is computed from the episode calendar — the salary line is
    no longer dead code: it appears near payday and never far from it."""
    node = VoiceNode(merchant_name="FitZone", writer=REF)
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


def test_strict_gate_raises_on_script_without_optout() -> None:
    """The node enforces the FULL DR12 policy (opt-out + merchant ID) itself —
    a writer that skips opt-out cannot ship a call artifact, and the violation
    is loud (raise), never a silent swap to the safe script."""

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
    with pytest.raises(ScriptSafetyError):
        node.make_call(_view(), "Priya", "https://rzp.io/rzp/abc123")
    assert node.calls == []  # zero artifacts — escalated, not papered over
