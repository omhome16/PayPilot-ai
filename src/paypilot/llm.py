"""Shared OpenRouter chat plumbing for every PayPilot LLM caller.

The reasoner, graph brain, script writer and dialogue brain all speak the same
wire protocol: POST /chat/completions with ``{model, messages, temperature,
max_tokens}``, read ``choices[0].message.content``, and count ``usage`` tokens.
That boilerplate (and the fenced-JSON parsing) used to live in four near-identical
copies; this module is the single home for it.

``ChatClient`` is transport-injectable (tests pass ``httpx.MockTransport``), and
``parse_json_object`` handles the "```json …```" fences models love to emit.

Failure policy (fail-loud, never opaque): a failed call raises ``LLMCallError``
carrying the provider status and response body — callers translate that into
their domain exceptions. Nothing here swallows an error into a bare ``None``;
"why did the brain die" must always be answerable from the message.

Reasoning models (e.g. free nvidia/minimax tiers) spend tokens thinking before
answering, so token budgets upstream are sized generously and ``parse_json_object``
tolerates prose around the JSON object.
"""

import json
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_S = 60.0
_MAX_RETRIES = 2  # extra attempts for 429/5xx (free-tier upstream congestion)
_RETRY_BACKOFF_S = (1.5, 4.0)

_ATTRIBUTION = {
    # OpenRouter asks clients to self-identify (recommended etiquette)
    "HTTP-Referer": "https://github.com/omhome16/PayPilot-ai",
    "X-Title": "PayPilot.AI",
}


class LLMCallError(RuntimeError):
    """A chat call failed (transport or HTTP). The message carries the provider
    status/body so the real cause (bad key, rate limit, outage) is visible."""


class ChatClient:
    """A thin, shared OpenRouter chat client (stateless per request)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
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
        self._timeout = timeout
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
        """One chat completion. Returns (content, usage) on success.

        Raises ``LLMCallError`` on transport or HTTP failure — 429/5xx are
        retried with a short backoff (free-tier congestion is routine); other
        statuses fail immediately with the provider's own error body.
        """
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if extra_body:
            body.update(extra_body)

        last_error: LLMCallError | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                r = self._client.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                raise LLMCallError(
                    f"provider timed out after {self._timeout:.0f}s "
                    f"(model {self._model} too slow for this budget)"
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMCallError(f"transport error: {exc}") from exc
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError as exc:
                    raise LLMCallError(
                        f"provider returned non-JSON body: {r.text[:200]}"
                    ) from exc
                usage = data.get("usage", {})
                self.tokens_used += int(usage.get("total_tokens", 0))
                content = data["choices"][0]["message"].get("content")
                return (str(content).strip() if content is not None else None), usage
            if r.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES:
                delay = _retry_delay_s(r, attempt)
                time.sleep(delay)
                last_error = LLMCallError(
                    f"HTTP {r.status_code} (attempt {attempt + 1}): {r.text[:200]}"
                )
                continue
            raise LLMCallError(f"HTTP {r.status_code}: {r.text[:300]}")
        raise LLMCallError(
            f"HTTP {r.status_code} after {attempt + 1} attempts: {r.text[:300]}"
        ) from last_error


def _retry_delay_s(response: httpx.Response, attempt: int) -> float:
    """Honour Retry-After when sane; otherwise step through the backoff ladder."""
    raw = response.headers.get("retry-after")
    if raw is not None:
        try:
            return min(max(float(raw), 0.5), 10.0)
        except ValueError:
            pass
    return _RETRY_BACKOFF_S[min(attempt, len(_RETRY_BACKOFF_S) - 1)]


def parse_json_object(content: str | None) -> dict[str, Any] | None:
    """Parse model output that may be wrapped in ```json fences — or embedded in
    reasoning prose. Returns None when the content is empty or holds no JSON
    object."""
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
        d = _extract_first_json_object(text)
    return d if isinstance(d, dict) else None


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Scan for the first balanced ``{…}`` object that parses as JSON.

    Reasoning models often emit thoughts before (or around) the payload; the
    balanced-brace scan tolerates that without pretending prose is JSON.
    """
    start = 0
    for _ in range(5):  # bounded: at most five candidate objects
        begin = text.find("{", start)
        if begin == -1:
            return None
        end = _balanced_end(text, begin)
        if end is None:
            return None
        try:
            d = json.loads(text[begin : end + 1])
        except (json.JSONDecodeError, ValueError):
            start = begin + 1
            continue
        return d if isinstance(d, dict) else None
    return None


def _balanced_end(text: str, begin: int) -> int | None:
    """Index of the ``}`` matching the ``{`` at ``begin`` (string-literal aware)."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(begin, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None
