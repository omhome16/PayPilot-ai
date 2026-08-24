"""P5 contracts: Hinglish call scripts that are useful AND consent-safe."""

import pytest

from paypilot.voice.script import (
    CallScript,
    LLMScriptWriter,
    ScriptSafetyError,
    TemplateScriptWriter,
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


def test_llm_writer_falls_back_to_template_on_garbage_or_unsafe() -> None:
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        # unsafe (threatening) LLM output must never reach the caller
        content = '{"text": "Pay Rs 199 in 10 minutes or legal action follows."}'
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
            request=req,
        )

    w = LLMScriptWriter(api_key="k", model="m", transport=httpx.MockTransport(handler))
    s = w.write(_ctx())
    assert s.source == "template"  # graceful, safe degradation
    assert "legal action" not in s.text.lower()
