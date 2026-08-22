"""The reasoning layer: optional LLM narration behind a stable interface.

AI-engineering practices encoded here:
- Provider abstraction: Reasoner Protocol; OpenRouter is one implementation, Null another.
- Context engineering: callers pass COMPACT JSON-serializable payloads (no prose walls);
  the prompt template is versioned (PROMPT_V1) so stored narrations stay interpretable.
- Cost discipline: temperature 0.2 (low-variance), max_tokens bounded, usage captured.
- Graceful degradation: any network/API failure returns None — never raises into the
  money path. A broken LLM must not break a recovery run.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

PROMPT_V1 = """You are PayPilot's recovery narrator. Given a JSON decision payload about a \
failed subscription payment in India, explain in ONE sentence (max 30 words) WHY the chosen \
recovery action fits the situation. Be concrete: reference timing, consent, or channel logic. \
No preamble, no markdown."""

_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class Narration:
    text: str
    model: str
    prompt_version: str
    tokens_prompt: int = 0
    tokens_completion: int = 0


class Reasoner(Protocol):
    def narrate(self, decision_payload: dict[str, Any]) -> Narration | None:
        """Explain ONE decision. Returns None when narration is off/unavailable."""
        ...


class NullReasoner:
    """LLM layer switched off — zero cost, zero latency, zero narrations."""

    def narrate(self, decision_payload: dict[str, Any]) -> Narration | None:
        return None


class OpenRouterReasoner:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._prompt_version = "v1"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter attribution headers (recommended etiquette)
            "HTTP-Referer": "https://github.com/omhome16/PayPilot-ai",
            "X-Title": "PayPilot.AI",
        }
        self._client = httpx.Client(
            base_url=base_url, headers=headers, timeout=_TIMEOUT_S, transport=transport
        )

    def narrate(self, decision_payload: dict[str, Any]) -> Narration | None:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": PROMPT_V1},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"prompt_version": self._prompt_version, **decision_payload}
                    ),
                },
            ],
            "temperature": 0.2,
            "max_tokens": 160,
            "metadata": {"prompt_version": self._prompt_version},
        }
        try:
            r = self._client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return Narration(
                text=str(content).strip(),
                model=self._model,
                prompt_version=self._prompt_version,
                tokens_prompt=int(usage.get("prompt_tokens", 0)),
                tokens_completion=int(usage.get("completion_tokens", 0)),
            )
        except Exception:  # noqa: BLE001 — degradation IS the feature here
            return None
