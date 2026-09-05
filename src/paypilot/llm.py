"""Shared OpenRouter chat plumbing for every PayPilot LLM caller.

The reasoner, graph brain, script writer and dialogue brain all speak the same
wire protocol: POST /chat/completions with ``{model, messages, temperature,
max_tokens}``, read ``choices[0].message.content``, and count ``usage`` tokens.
That boilerplate (and the fenced-JSON parsing) used to live in four near-identical
copies; this module is the single home for it.

``ChatClient`` is transport-injectable (tests pass ``httpx.MockTransport``), and
``parse_json_object`` handles the "```json …```" fences models love to emit.
"""

import json
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_ATTRIBUTION = {
    # OpenRouter asks clients to self-identify (recommended etiquette)
    "HTTP-Referer": "https://github.com/omhome16/PayPilot-ai",
    "X-Title": "PayPilot.AI",
}


class ChatClient:
    """A thin, shared OpenRouter chat client (stateless per request)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        attribution: bool = True,
    ) -> None:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if attribution:
            headers.update(_ATTRIBUTION)
        self._model = model
        self._client = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport
        )
        self.tokens_used = 0

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any]]:
        """One chat completion. Returns (content, usage) — or (None, {}) on any
        network/HTTP failure so callers decide how loudly to fail."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if extra_body:
            body.update(extra_body)
        try:
            r = self._client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage", {})
            self.tokens_used += int(usage.get("total_tokens", 0))
            content = data["choices"][0]["message"].get("content")
            return (str(content).strip() if content is not None else None), usage
        except Exception:  # noqa: BLE001 — transport failures are caller policy
            return None, {}


def parse_json_object(content: str | None) -> dict[str, Any] | None:
    """Parse model output that may be wrapped in ```json fences. Returns None
    when the content is empty or not a JSON object."""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        d = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return d if isinstance(d, dict) else None
