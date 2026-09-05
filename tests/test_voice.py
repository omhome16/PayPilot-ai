"""P5 contracts: Hinglish call scripts that are useful AND consent-safe."""

import pytest

from paypilot.voice.script import (
    CallScript,
    LLMScriptWriter,
    ScriptSafetyError,
    TemplateScriptWriter,
    VoiceWriterUnavailable,
    validate_script_safety,
)


def _ctx(**over):
    ctx = {
        "customer_name": "Priya",
        "merchant": "StreamFlix",
        "amount_rupees": 199.0,
        "mode": "insufficient_funds",
        "days_to_salary": 3,
        "history_note": "history: reliable payer (12/13 on time)",
        "payment_url": "https://rzp.io/rzp/abc123",
        "attempt_no": 2,
    }
    ctx.update(over)
    return ctx


def test_template_writer_produces_hinglish_with_all_facts() -> None:
    s = TemplateScriptWriter().write(_ctx())
    assert isinstance(s, CallScript)
    assert "Priya" in s.text
    assert "199" in s.text  # amount stated
    assert "rzp.io" in s.text  # payment link offered
    assert "StreamFlix" in s.text  # merchant identified
    assert s.estimated_seconds <= 45  # short call, respects the human


def test_validator_rejects_threat_or_pressure_language() -> None:
    for bad in (
        "Pay immediately otherwise legal action will be taken.",
        "Aapko arrest ho sakta hai agar aap ne pay nahi kiya.",
        "Your account will be blacklisted forever, pay NOW or police complaint.",
    ):
        with pytest.raises(ScriptSafetyError):
            validate_script_safety(CallScript(text=bad))


def test_validator_requires_amount_and_link() -> None:
    with pytest.raises(ScriptSafetyError):
        validate_script_safety(
            CallScript(text="Namaste! Kripya apna payment jaldi complete kijiye."),
            require_amount=True,
            require_link=False,
        )
    with pytest.raises(ScriptSafetyError):
        validate_script_safety(
            CallScript(text="Namaste Priya ji, aapka Rs 199 due hai. Dhanyavaad!"),
            require_amount=True,
            require_link=True,
        )


def test_llm_writer_parses_good_response() -> None:
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        content = (
            '{"text": "Namaste Priya ji! Main StreamFlix se bol raha hoon. '
            "Aapka Rs 199 ka payment pending hai. Sab theek thaak hai to bas "
            "is link se 2 minute mein pay kar dijiye: https://rzp.io/rzp/abc123. "
            'Dhanyavaad!"}'
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
            request=req,
        )

    w = LLMScriptWriter(api_key="k", model="m", transport=httpx.MockTransport(handler))
    s = w.write(_ctx())
    assert "Priya" in s.text and "199" in s.text
    assert s.source == "llm"


def test_llm_writer_raises_on_unsafe_output_never_silently_swaps() -> None:
    """Fail-loud: unsafe (threatening) LLM output raises — it must never reach the
    caller, and it must never silently become the template script."""
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        content = '{"text": "Pay Rs 199 in 10 minutes or legal action follows."}'
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
            request=req,
        )

    w = LLMScriptWriter(api_key="k", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(ScriptSafetyError):
        w.write(_ctx())


def test_llm_writer_raises_when_script_too_long() -> None:
    """An over-long LLM script (>120 words) raises — the caller escalates instead
    of shipping an unapproved substitute."""
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        long_text = "Aapka Rs 199 ka payment pending hai. Kripya jald clear kar dijiye. " * 12
        content = f'{{"text": "{long_text}"}}'
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
            request=req,
        )

    w = LLMScriptWriter(api_key="k", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(ScriptSafetyError):
        w.write(_ctx())


def test_llm_writer_raises_when_unparseable() -> None:
    """Garbage that never becomes JSON raises VoiceWriterUnavailable — the caller
    escalates loudly instead of speaking a substitute script."""
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "totally not json"}}], "usage": {}},
            request=req,
        )

    w = LLMScriptWriter(api_key="k", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(VoiceWriterUnavailable):
        w.write(_ctx())


def test_llm_writer_raises_when_network_fails() -> None:
    """A down provider raises VoiceWriterUnavailable (fail-loud), never a template."""
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("provider down")

    w = LLMScriptWriter(api_key="k", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(VoiceWriterUnavailable):
        w.write(_ctx())
    assert w.calls == 1  # one attempt, loudly failed


def test_template_script_opens_with_history_aware_tone() -> None:
    """The call speaks the customer's data: reliable payers get acknowledgment,
    history-of-misses customers get a softer, help-first opener."""
    from paypilot.voice.script import TemplateScriptWriter

    base = {
        "merchant": "FitZone",
        "amount_rupees": 1499.0,
        "payment_url": "https://rzp.io/rzp/x",
        "days_to_salary": 20,
        "attempt_no": 1,
        "mode": "insufficient_funds",
    }

    reliable = TemplateScriptWriter().write({**base, "on_time_ratio": 0.92}).text
    assert "hamesha time pe pay karte" in reliable

    struggling = TemplateScriptWriter().write({**base, "on_time_ratio": 0.3}).text
    assert "aasaan option" in struggling

    neutral = TemplateScriptWriter().write(dict(base)).text
    assert "hamesha time pe" not in neutral and "aasaan option" not in neutral


def test_template_script_frames_revoked_mandates_as_consent_first() -> None:
    from paypilot.voice.script import TemplateScriptWriter

    ctx = {
        "merchant": "FitZone",
        "amount_rupees": 999.0,
        "payment_url": "https://rzp.io/rzp/x",
        "days_to_salary": 20,
        "attempt_no": 1,
        "mode": "mandate_revoked",
    }
    text = TemplateScriptWriter().write(ctx).text
    assert "permission" in text
