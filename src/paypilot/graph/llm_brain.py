"""LLM brain: OpenRouter-powered THINK node (LangChain-compatible endpoint).

Same wire protocol as our Phase-3 Reasoner but returns structured BrainProposals.
Prompt embeds the research design rules (research/07). Output parsing is strict;
any malformed answer triggers one repair round, then FAILS LOUD: an unavailable or
unparseable brain raises BrainUnavailable so callers escalate to humans — a silent
'lawful default' would execute an action the LLM never justified.
"""

import json
from typing import Any

import httpx

from paypilot.domain.enums import Intervention
from paypilot.graph.brain import BrainProposal


class BrainUnavailable(RuntimeError):
    """Raised when the LLM brain cannot produce a proposal (fail-loud).

    Catch it at the graph/adapter boundary and escalate the episode to human
    review — never substitute a deterministic guess for the missing brain.
    """


PROMPT_GRAPH_V1 = """You are PayPilot's recovery strategist for Indian subscription payments.
Given a JSON situation, propose ONE next move as strict JSON:
{"action": "<one of: wait_self_heal, smart_retry, payment_link, voice_nudge, human_escalation>",
 "on_salary_day": true|false, "days_ahead": <int 0-14>, "reason": "<max 25 words>"}
Rules of thumb:
- insufficient_funds near salary (+0-3d): prefer wait_self_heal or salary-day smart_retry
- mandate_revoked / limit_exceeded: payment_link (consent-fresh channel); NEVER retries
- high amounts (>= Rs1000): voice_nudge/human_escalation after failed retries
- max ~3 customer touches; be economical. No text outside the JSON."""

_ALLOWED = {
    "wait_self_heal": Intervention.WAIT_SELF_HEAL,
    "smart_retry": Intervention.SMART_RETRY,
    "retry": Intervention.RETRY,
    "payment_link": Intervention.PAYMENT_LINK,
    "voice_nudge": Intervention.VOICE_NUDGE,
    "human_escalation": Intervention.HUMAN_ESCALATION,
}


class OpenRouterBrain:
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
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/omhome16/PayPilot-ai",
                "X-Title": "PayPilot.AI",
            },
            timeout=10.0,
            transport=transport,
        )
        self.calls = 0
        self.tokens_used = 0

    def propose(self, state: dict[str, Any]) -> BrainProposal:
        self.calls += 1
        messages: list[dict[str, str]] = [
            {"role": "system", "content": PROMPT_GRAPH_V1},
            {"role": "user", "content": json.dumps(state)},
        ]
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 150,
        }
        content = self._call(body)
        parsed = self._parse(content)
        if parsed is not None:
            return parsed
        # one repair round
        messages.append({"role": "assistant", "content": content or ""})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your last reply was not valid JSON per the schema. "
                    "Reply again with ONLY the JSON."
                ),
            }
        )
        content2 = self._call(body)
        parsed2 = self._parse(content2)
        if parsed2 is not None:
            return parsed2
        # Fail-loud: neither attempt produced a usable proposal. Never guess.
        if content is None and content2 is None:
            raise BrainUnavailable("LLM brain call failed (network/API error)")
        raise BrainUnavailable("LLM brain output unparseable after repair round")

    def _call(self, body: dict[str, Any]) -> str | None:
        try:
            r = self._client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
            self.tokens_used += int(data.get("usage", {}).get("total_tokens", 0))
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception:  # noqa: BLE001 — failure is surfaced loudly by the caller
            return None

    @staticmethod
    def _parse(content: str | None) -> BrainProposal | None:
        if not content:
            return None
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            d = json.loads(text.strip())
            action_raw = str(d.get("action", "")).lower()
            action = _ALLOWED.get(action_raw)
            if action is None:
                return None
            # Mode legality is NOT checked here — that is the guardrails' job (R1).
            # Pre-filtering would burn a repair round; the rails dispose either way.
            return BrainProposal(
                action=action,
                on_salary_day=bool(d.get("on_salary_day", False)),
                days_ahead=max(0, min(int(d.get("days_ahead", 1)), 14)),
                reason=str(d.get("reason", ""))[:200],
                raw=d,
            )
        except (json.JSONDecodeError, ValueError, KeyError):
            return None
