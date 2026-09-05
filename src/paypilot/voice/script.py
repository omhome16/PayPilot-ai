"""Hinglish call-script writers: deterministic template + LLM, both safety-gated.

Layering: writers produce a CallScript; validate_script_safety gates it. The LLM
writer NEVER lets an unsafe/unparseable output reach the caller — but it also never
SILENTLY substitutes the template: fail-loud means a failed/unsafe LLM output raises,
so the caller escalates to a human instead of speaking a script the LLM didn't
approve. The deterministic TemplateScriptWriter is a designed reference writer that
callers opt into explicitly (eval, tests, reference node) — never an implicit crutch.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from paypilot.llm import DEFAULT_TIMEOUT_S, ChatClient, LLMCallError, parse_json_object
from paypilot.voice.safety import hard_block_problems

_WORDS_PER_SECOND = 2.5
_AMOUNT_RE = re.compile(r"(?:₹|rs\.?)\s?[\d,]+", re.IGNORECASE)
_LINK_RE = re.compile(r"https?://|\brzp\.io\b", re.IGNORECASE)


@dataclass(frozen=True)
class CallScript:
    text: str
    source: str = "template"  # "template" | "llm"

    @property
    def estimated_seconds(self) -> float:
        return round(len(self.text.split()) / _WORDS_PER_SECOND, 1)


class ScriptSafetyError(ValueError):
    """Raised when a script violates voice-channel policy."""


class VoiceWriterUnavailable(RuntimeError):
    """Raised when the LLM script writer cannot produce a script (fail-loud).

    Distinct from ScriptSafetyError: this is the writer being DOWN or returning
    garbage, not a policy violation. Either way the caller must escalate rather
    than silently speaking a substitute.
    """


def validate_script_safety(
    script: CallScript,
    *,
    require_amount: bool = False,
    require_link: bool = False,
) -> None:
    """Raise ScriptSafetyError on any violation; return None when clean."""
    text = script.text.strip()

    if not text:
        raise ScriptSafetyError("empty script")

    problems = hard_block_problems(text)
    if require_amount and _AMOUNT_RE.search(text) is None:
        problems.append("required amount (₹/Rs …) missing")
    if require_link and _LINK_RE.search(text) is None:
        problems.append("required payment link missing")

    if problems:
        raise ScriptSafetyError("; ".join(problems))


class TemplateScriptWriter:
    """Deterministic Hinglish script — always available, always safe.

    Context-aware: the opener acknowledges payment history, consent-broken modes
    get a permission-first framing, and salary/attempt lines only fire when true.
    """

    def write(self, ctx: dict[str, Any]) -> CallScript:
        name = str(ctx.get("customer_name", "ji")).split()[0]
        merchant = str(ctx["merchant"])
        amount = float(ctx["amount_rupees"])
        amount_str = f"{amount:,.0f}"  # Indian-friendly grouping: 1,499
        url = str(ctx.get("payment_url", ""))
        days = ctx.get("days_to_salary")
        mode = str(ctx.get("mode", ""))
        attempt = int(ctx.get("attempt_no", 1))
        ratio = ctx.get("on_time_ratio")  # None = no customer memory

        lines = [f"Namaste {name} ji! Main {merchant} ki taraf se bol rahi hoon."]
        # the data speaking: history-aware opener
        if ratio is not None and ratio >= 0.8:
            lines.append("Aap toh hamesha time pe pay karte hain — is baar shayad slip ho gaya.")
        elif ratio is not None and ratio < 0.5:
            lines.append("Koi baat nahi — hum aapke liye ek aasaan option lekar aaye hain.")
        lines.append(f"Aapka ₹{amount_str} ka payment pending dikha raha hai.")
        if mode == "mandate_revoked":
            lines.append(
                "Aapki permission ke bina hum kuch nahi kaat sakte — "
                "pehle permission, phir payment."
            )
        if isinstance(days, int) and 0 <= days <= 7:
            lines.append(
                f"Salary bhi aa hi rahi hogi ({days} din mein), uske baad pay karna aasaan hoga."
            )
        elif attempt >= 2:
            lines.append("Humne aapko pehle bhi remind kiya tha — is baar link ek click mein hai.")
        lines.append(f"Aap is link se bas do minute mein pay kar sakte hain: {url}")
        lines.append(
            "Aur agar aap ye subscription aage nahi chalana chahte, toh humein bata dijiye — "
            "hum ise rok denge ya pause kar denge. Dhanyavaad!"
        )
        script = CallScript(text=" ".join(lines), source="template")
        validate_script_safety(script, require_amount=True, require_link=bool(url))
        return script


_PROMPT_VOICE_V1 = """You write SHORT Hinglish payment-recovery call scripts for Indian merchants.
Facts you MUST use: customer name, merchant name, pending amount, payment link.
Rules: warm respectful tone ('aap'), 40–90 words, mention the ₹ amount and the link,
offer an opt-out (pause/cancel), NEVER threaten or pressure (no legal/police talk).
Reply with ONLY strict JSON: {"text": "<script>"}"""


class LLMScriptWriter:
    """LLM-written scripts (OpenRouter-compatible), fail-loud on any failure.

    On a network/API failure or unparseable output the writer raises
    VoiceWriterUnavailable carrying the provider's own error detail; on unsafe
    output (threats, missing amount/link, over-length) validate_script_safety
    raises ScriptSafetyError. No silent template substitution — the caller
    escalates the call to a human instead.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = ChatClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )
        self.calls = 0
        self.tokens_used = 0

    def write(self, ctx: dict[str, Any]) -> CallScript:
        self.calls += 1
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _PROMPT_VOICE_V1},
            {"role": "user", "content": json.dumps(ctx)},
        ]
        try:
            content, _ = self._client.complete(messages, temperature=0.4, max_tokens=1000)
        except LLMCallError as exc:
            raise VoiceWriterUnavailable(f"LLM script writer call failed — {exc}") from exc
        self.tokens_used = self._client.tokens_used
        if content is None:
            raise VoiceWriterUnavailable("LLM script writer returned empty content")
        parsed = self._parse(content)
        if parsed is None:
            raise VoiceWriterUnavailable("LLM script output unparseable")
        script = CallScript(text=parsed, source="llm")
        # Unsafe or over-long LLM text raises here — it never becomes a call, and it
        # never silently becomes the template. The caller escalates.
        validate_script_safety(script, require_amount=True, require_link=True)
        return script

    @staticmethod
    def _parse(content: str | None) -> str | None:
        d = parse_json_object(content)
        if d is None:
            return None
        out = str(d.get("text", "")).strip()
        return out or None
