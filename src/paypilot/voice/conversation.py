"""Two-way conversational recovery calls — the voice channel that LISTENS.

A call is no longer a monologue: it is a bounded turn-taking conversation.

- The customer speaks (typed / simulated / browser STT — the pipeline is the same).
- A deterministic intent classifier tags what was said (input adapter, like STT).
- The LLM dialogue brain reasons over REAL context — retrieved from the SQLite
  store through RecoveryTools on every session open — and answers + acts.
- Every agent utterance is safety-validated before it could be spoken.
- Mid-call "stop calling" honours consent instantly (do-not-call registry + store).
- Fail-loud: an unavailable or unsafe brain NEVER improvises a canned recovery
  script; the call terminates with outcome=human and a system notice.

The customer never walks away hanging: a turn budget bounds the call and the
outcome is always actionable (pay_now → link, pay_later → salary-timed retry,
cancel → consent honoured, human → ops handoff, closed → done).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from paypilot.store.tools import RecoveryTools
from paypilot.voice.script import (
    CallScript,
    ScriptSafetyError,
    VoiceWriterUnavailable,
    validate_script_safety,
)

MAX_REPLY_WORDS = 60  # ≈24s of speech per agent turn (DR11: respect the human's time)
MAX_CUSTOMER_TURNS = 6  # hard turn budget — calls end, they never dangle

_ALLOWED_OUTCOMES = {"none", "pay_now", "pay_later", "cancel", "human", "closed"}

# deterministic intent classifier — an INPUT adapter (like STT), never a decision-maker
_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("pay_now", ("abhi pay", "pay kar diya", "kar diya", "done", "ho gaya")),
    ("pay_now", ("link bhejo", "bhej do", "pay kiya")),
    ("will_pay_later", ("salary", "agle hafte", "next week", "baad mein", "kal")),
    ("will_pay_later", ("mahine", "5th", "10th", "15th", "20th")),
    ("will_pay_later", ("pay karunga", "pay karungi", "arrear")),
    ("cancel", ("nahi chahiye", "nahi chalana", "band kar", "cancel", "stop")),
    ("cancel", ("rok do", "chhod", "pause", "bundle")),
    ("need_help", ("link nahi", "nahi mila", "kaise", "error", "issue")),
    ("need_help", ("nhi aaya", "problem")),
    ("human", ("human", "manager", "baat karo", "shikayat", "complaint", "insaan")),
    ("ack", ("ok", "theek", "haan", "hmm", "achha", "thank", "dhanyavaad", "sure")),
]

_REPLY_STRIP = re.compile(r"\s+")


@dataclass(frozen=True)
class CallOutcome:
    """What the conversation achieved — an EXECUTABLE result, not a narrative."""

    kind: str  # none | pay_now | pay_later | cancel | human | closed
    intervention: str | None = None  # mapped recovery action for the caller
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "intervention": self.intervention, "note": self.note}


@dataclass(frozen=True)
class Turn:
    """One agent utterance: what was said, whether it was safe, and any action."""

    text: str
    source: str  # llm | system
    outcome: CallOutcome | None = None
    done: bool = False
    degraded: bool = False
    note: str = ""
    violations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": "agent",
            "text": self.text,
            "source": self.source,
            "safety": {"ok": not self.violations, "violations": self.violations},
            "outcome": self.outcome.as_dict() if self.outcome else None,
            "done": self.done,
            "degraded": self.degraded,
            "note": self.note,
        }


def classify_intent(text: str) -> str:
    """Deterministic tag of what the customer said (demo input adapter)."""
    low = re.sub(r"[^a-z0-9\\s]", " ", text.lower())
    low = _REPLY_STRIP.sub(" ", low)
    for intent, phrases in _INTENT_RULES:
        if any(p in low for p in phrases):
            return intent
    return "unclear"


def validate_reply(text: str) -> list[str]:
    """Safety gate for ONE conversational agent turn (blocklist + length)."""
    try:
        validate_script_safety(CallScript(text=text), require_amount=False, require_link=False)
    except ScriptSafetyError as exc:
        return [str(exc)]
    words = len(text.split())
    if words > MAX_REPLY_WORDS:
        return [f"reply too long: {words} words (max {MAX_REPLY_WORDS})"]
    return []


def _outcome_for(kind: str, note: str) -> CallOutcome:
    mapping = {
        "pay_now": "payment_link",
        "pay_later": "smart_retry",  # salary-timed by the caller/scheduler
        "human": "human_escalation",
        "closed": None,
        "cancel": None,  # consent change, not a debit attempt
        "none": None,
    }
    return CallOutcome(kind=kind, intervention=mapping[kind], note=note)


class DialogueBrain(Protocol):
    def respond(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return strict {'reply', 'outcome', 'note', 'done'} or raise (fail-loud)."""
        ...


_PROMPT_DIALOGUE_V1 = """You are PayPilot's LIVE voice agent for Indian subscription
payment recovery. You are speaking to {customer_name} on behalf of {merchant}.
Context JSON includes the customer's payment-history profile, the failed-payment
episode, past recovery decisions, consent state and the customer's last reply
(tagged by a deterministic intent classifier — trust the raw text over the tag).

Ground rules:
- Warm, respectful Hinglish, address as 'aap'. NEVER threaten, pressure, or mention
  legal/police/court. Offer the pause/cancel option naturally when relevant.
- MAX 60 words per reply. Be short and human.
- If the customer is ready to pay: give the payment link {payment_url} and outcome pay_now.
- If they say they will pay later (salary etc.): acknowledge, outcome pay_later.
- If they want to stop/cancel: agree politely, outcome cancel.
- If they need a human or are distressed: outcome human.
- Otherwise keep the conversation usefully short: outcome none, done=false.
- End the call (done=true, outcome closed) only after resolution or a natural goodbye.
Reply with ONLY strict JSON:
{{"reply": "<Hinglish <=60 words>", "outcome": "none|pay_now|pay_later|cancel|human|closed",
  "note": "<why, max 20 words>", "done": true|false}}"""


class OpenRouterDialogueBrain:
    """LLM dialogue driver (OpenRouter-compatible), fail-loud like every writer."""

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

    def respond(self, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _PROMPT_DIALOGUE_V1.format(**context["_prompt"])},
            {"role": "user", "content": json.dumps(context["_state"], default=str)},
        ]
        content = self._call(messages)
        if content is None:
            raise VoiceWriterUnavailable("dialogue brain call failed (network/API)")
        parsed = self._parse(content)
        if parsed is not None:
            return parsed
        # one repair round
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": "That was not valid JSON per the schema. "
                "Reply again with ONLY the JSON.",
            }
        )
        content2 = self._call(messages)
        parsed2 = self._parse(content2)
        if parsed2 is not None:
            return parsed2
        if content2 is None:
            raise VoiceWriterUnavailable("dialogue brain call failed (network/API)")
        raise VoiceWriterUnavailable("dialogue brain output unparseable after repair round")

    def _call(self, messages: list[dict[str, str]]) -> str | None:
        try:
            r = self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 300,
                },
            )
            r.raise_for_status()
            data = r.json()
            self.tokens_used += int(data.get("usage", {}).get("total_tokens", 0))
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception:  # noqa: BLE001 — surfaced loudly by respond()
            return None

    @staticmethod
    def _parse(content: str | None) -> dict[str, Any] | None:
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
        reply = str(d.get("reply", "")).strip()
        outcome = str(d.get("outcome", "none")).strip().lower()
        if not reply or outcome not in _ALLOWED_OUTCOMES:
            return None
        return {
            "reply": reply,
            "outcome": outcome,
            "note": str(d.get("note", ""))[:120],
            "done": bool(d.get("done", False)),
        }


class Conversation:
    """One live two-way call session. Bounded, safe, actionable — or escalated loud."""

    def __init__(
        self,
        *,
        merchant_name: str,
        customer_name: str,
        episode_key: str,
        context: dict[str, Any],
        payment_url: str,
        brain: DialogueBrain | None,
        tools: RecoveryTools | None = None,
        max_customer_turns: int = MAX_CUSTOMER_TURNS,
    ) -> None:
        self.merchant_name = merchant_name
        self.customer_name = customer_name
        self.episode_key = episode_key
        self.context = context  # episode story: mode, amount, attempts, dates...
        self.payment_url = payment_url
        self.brain = brain
        self.tools = tools
        self.max_turns = max_customer_turns
        self.transcript: list[dict[str, Any]] = []
        self.tool_events: list[dict[str, Any]] = []
        self.done = False
        self.degraded = False
        self.outcome: CallOutcome | None = None
        self._customer_turns = 0
        self._intents: list[str] = []

    # -- lifecycle ---------------------------------------------------------------

    def open(self) -> Turn:
        """First agent turn: retrieve context through tools, then speak."""
        state = self._state()
        turn = self._agent_turn(state)
        self._append_agent(turn)
        return turn

    def respond(self, customer_text: str) -> Turn:
        """One customer utterance in, one validated agent utterance out."""
        customer_text = customer_text.strip()
        if self.done:
            return self._system_turn("Call already closed.", done=True)
        intent = classify_intent(customer_text)
        self._intents.append(intent)
        self.transcript.append({"role": "customer", "text": customer_text, "intent": intent})
        self._customer_turns += 1

        if intent == "cancel":
            # consent is honoured MID-CALL — registry + store, then a graceful close
            self._honour_opt_out()
            turn = self._system_turn(
                "Aapki baat samajh gayi — subscription band kar di gayi hai. "
                "Koi aur payment nahi hoga. Dhanyavaad!",
                done=True,
                outcome=_outcome_for("cancel", "customer asked to stop — consent honoured"),
            )
            self._append_agent(turn)
            return turn

        state = self._state()
        turn = self._agent_turn(state)
        self._append_agent(turn)
        if not turn.done and self._customer_turns >= self.max_turns:
            # the budget is spent without resolution → human ops follows up (never dangling)
            turn = self._system_turn(
                "Aapse baat karke achha laga. Aapki convenience ke liye hum aapko "
                "dobara kabhi call karenge — abhi ke liye dhanyavaad!",
                done=True,
                outcome=_outcome_for("human", "turn budget reached — human ops follows up"),
                degraded=False,
            )
            self._append_agent(turn)
        return turn

    # -- internals ---------------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        """Context handed to the brain: tool-retrieved rows + episode + transcript."""
        state: dict[str, Any] = {"episode": self.context, "transcript": self.transcript}
        if self.tools is not None and self.context.get("subscription_id"):
            sub = self.context["subscription_id"]
            for tool_call in (
                self.tools.lookup_customer(sub),
                self.tools.episode_history(sub),
                self.tools.recent_decisions(sub),
                self.tools.consent_status(sub),
            ):
                state[tool_call.name] = tool_call.data
                self.tool_events.append(
                    {
                        "tool": tool_call.name,
                        "rows": tool_call.rows,
                        "latency_ms": round(tool_call.latency_ms, 2),
                        "summary": _summarise(tool_call.data),
                    }
                )
        if self._intents:
            state["customer_intent_last"] = self._intents[-1]
        return state

    def _agent_turn(self, state: dict[str, Any]) -> Turn:
        if self.brain is None:
            # fail-loud: no dialogue brain → the call escalates, never improvises
            self.done = True
            self.degraded = True
            self.outcome = _outcome_for("human", "no dialogue brain configured (fail-loud)")
            return Turn(
                text="",
                source="system",
                outcome=self.outcome,
                done=True,
                degraded=True,
                note="no LLM dialogue brain configured — call escalated to human review",
            )
        prompt_ctx = {
            "customer_name": self.customer_name,
            "merchant": self.merchant_name,
            "payment_url": self.payment_url,
        }
        try:
            raw = self.brain.respond({"_prompt": prompt_ctx, "_state": state})
        except (VoiceWriterUnavailable, ScriptSafetyError) as exc:
            return self._fail_loud(f"brain unavailable: {exc}")
        reply = str(raw.get("reply", ""))
        violations = validate_reply(reply)
        if violations:
            return self._fail_loud("unsafe brain output: " + "; ".join(violations))
        outcome_kind = str(raw.get("outcome", "none"))
        note = str(raw.get("note", ""))
        done = bool(raw.get("done", False))
        outcome = _outcome_for(outcome_kind, note)
        if outcome_kind in ("pay_now", "pay_later", "human", "cancel", "closed"):
            self.outcome = outcome
            self._record_outcome(outcome)
            if outcome_kind == "cancel":
                self._honour_opt_out()
        return Turn(
            text=reply,
            source="llm",
            outcome=outcome,
            done=done,
            note=note,
            violations=[],
        )

    def _fail_loud(self, reason: str) -> Turn:
        """Terminal degraded state: humans take over; a SYSTEM notice closes, not a fake reply."""
        self.done = True
        self.degraded = True
        self.outcome = _outcome_for("human", f"fail-loud escalation: {reason}")
        return Turn(
            text="",
            source="system",
            outcome=self.outcome,
            done=True,
            degraded=True,
            note=reason,
            violations=["agent could not respond safely"],
        )

    def _honour_opt_out(self) -> None:
        self.done = True
        if self.tools is not None and self.context.get("subscription_id"):
            cust = self.tools.store.customer_by_subscription(self.context["subscription_id"])
            if cust:
                self.tools.store.mark_do_not_call(cust["customer_id"])

    def _record_outcome(self, outcome: CallOutcome) -> None:
        if self.tools is None or not self.context.get("subscription_id"):
            return
        mode = str(self.context.get("mode", "unknown"))
        attempts = int(self.context.get("attempts_made", 1))
        chosen = outcome.intervention
        if chosen is not None:
            self.tools.store.record_decision(
                episode_key=self.episode_key,
                mode=mode,
                attempts=attempts,
                chosen=chosen,
                reason=f"voice conversation outcome: {outcome.kind} — {outcome.note}",
            )

    def _append_agent(self, turn: Turn) -> None:
        self.transcript.append({"role": "agent", "text": turn.text, "source": turn.source})
        if turn.done:
            self.done = True
            if turn.outcome is not None:
                self.outcome = turn.outcome
            self.degraded = self.degraded or turn.degraded

    def _system_turn(
        self,
        text: str,
        *,
        done: bool,
        outcome: CallOutcome | None = None,
        degraded: bool = False,
    ) -> Turn:
        return Turn(text=text, source="system", outcome=outcome, done=done, degraded=degraded)

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode_key": self.episode_key,
            "customer_name": self.customer_name,
            "merchant": self.merchant_name,
            "done": self.done,
            "degraded": self.degraded,
            "outcome": self.outcome.as_dict() if self.outcome else None,
            "transcript": self.transcript,
            "tool_events": self.tool_events,
        }


def _summarise(data: dict[str, Any]) -> str:
    """One-line human summary of a tool result (shown on the dashboard)."""
    if "found" in data:
        if not data["found"]:
            return "customer not found"
        prof = data.get("profile")
        ratio = (
            prof["paid_on_time"] / prof["tenure_cycles"]
            if prof and prof["tenure_cycles"]
            else 0
        )
        return (
            f"{data['name']} · {data['plan_name']} ₹{data['amount_paise'] / 100:.0f} · "
            f"billing day {data['billing_day']} · on-time {ratio:.0%}"
        )
    if "episodes" in data:
        return f"{len(data['episodes'])} past failure episode(s)"
    if "decisions" in data:
        return f"{len(data['decisions'])} prior decision(s)"
    if "do_not_call" in data:
        return f"do-not-call: {data['do_not_call']}"
    if "next_salary_date" in data:
        return f"next salary {data['next_salary_date']}"
    return ""
