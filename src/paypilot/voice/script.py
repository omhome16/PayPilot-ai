"""Hinglish call-script writers: deterministic template + LLM with safe fallback.

Layering: writers produce a CallScript; validate_script_safety gates it. The LLM
writer NEVER lets an unsafe/unparseable output reach the caller — it degrades to
the template (which is safety-checked itself).
"""

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from paypilot.voice.safety import _ABUSE, _BLOCKLIST, _SHOUT_RUN

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


def validate_script_safety(
    script: CallScript,
    *,
    require_amount: bool = False,
    require_link: bool = False,
) -> None:
    """Raise ScriptSafetyError on any violation; return None when clean."""
    text = script.text.strip()
    low = text.lower()
    problems: list[str] = []

    if not text:
        raise ScriptSafetyError("empty script")

    for phrase in _BLOCKLIST:
        if phrase in low:
            problems.append(f"threat/intimidation language: '{phrase}'")
    for word in _ABUSE:
        if word in low:
            problems.append(f"abusive language: '{word}'")
    shout = _SHOUT_RUN.search(text)
    if shout is not None:
        problems.append(f"spam shouting: '{shout.group(0)}'")

    if require_amount and _AMOUNT_RE.search(text) is None:
        problems.append("required amount (₹/Rs …) missing")
    if require_link and _LINK_RE.search(text) is None:
        problems.append("required payment link missing")

    if problems:
        raise ScriptSafetyError("; ".join(problems))


class TemplateScriptWriter:
    """Deterministic Hinglish script — always available, always safe."""

    def write(self, ctx: dict[str, Any]) -> CallScript:
        name = str(ctx.get("customer_name", "ji")).split()[0]
        merchant = str(ctx["merchant"])
        amount = float(ctx["amount_rupees"])
        amount_str = f"{amount:,.0f}"  # Indian-friendly grouping: 1,499
        url = str(ctx.get("payment_url", ""))
        days = ctx.get("days_to_salary")
        salary_line = (
            f"Salary bhi aa hi rahi hogi ({days} din mein), uske baad pay karna aasaan hoga. "
            if isinstance(days, int) and 0 <= days <= 7
            else ""
        )
        text = (
            f"Namaste {name} ji! Main {merchant} ki taraf se bol rahi hoon. "
            f"Aapka ₹{amount_str} ka payment pending dikha raha hai. "
            f"{salary_line}"
            f"Aap is link se bas do minute mein pay kar sakte hain: {url} "
            "Aur agar aap ye subscription aage nahi chalana chahte, toh humein bata dijiye — "
            "hum ise rok denge ya pause kar denge. Dhanyavaad!"
        )
        script = CallScript(text=text, source="template")
        validate_script_safety(script, require_amount=True, require_link=bool(url))
        return script


_PROMPT_VOICE_V1 = """You write SHORT Hinglish payment-recovery call scripts for Indian merchants.
Facts you MUST use: customer name, merchant name, pending amount, payment link.
Rules: warm respectful tone ('aap'), 40–90 words, mention the ₹ amount and the link,
offer an opt-out (pause/cancel), NEVER threaten or pressure (no legal/police talk).
Reply with ONLY strict JSON: {"text": "<script>"}"""


class LLMScriptWriter:
    """LLM-written scripts (OpenRouter-compatible), safety-gated with template fallback."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10.0,
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
        content = self._call(messages)
        parsed = self._parse(content)
        if parsed is not None:
            script = CallScript(text=parsed, source="llm")
            try:
                validate_script_safety(script, require_amount=True, require_link=True)
                return script
            except ScriptSafetyError:
                pass  # unsafe LLM output never reaches the caller
        return TemplateScriptWriter().write(ctx)

    def _call(self, messages: list[dict[str, str]]) -> str | None:
        try:
            r = self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 250,
                },
            )
            r.raise_for_status()
            data = r.json()
            self.tokens_used += int(data.get("usage", {}).get("total_tokens", 0))
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception:  # noqa: BLE001 — degradation is the design
            return None

    @staticmethod
    def _parse(content: str | None) -> str | None:
        if not content:
            return None
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            d = json.loads(text.strip())
            out = str(d.get("text", "")).strip()
            return out or None
        except (json.JSONDecodeError, ValueError):
            return None
