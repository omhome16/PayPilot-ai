"""AgentPolicy — the PayPilot brain (hybrid architecture, D23).

Deterministic decision core: strategy ladder + calendar math. Seeded runs stay
byte-identical (D20). Optional LLM narration rides alongside every decision via a
Reasoner; every decision lands in an in-memory DecisionRecord ledger — the agent's
memory trail that Phase 6 turns into ROI numbers and the dashboard replays.

Touch accounting (Standard appetite):
- Customer touches: ≤ 3 per episode (links/voice count; humans do NOT — humans are
  an internal handoff, not a contact)
- Human escalation: only when amount ≥ appetite.human_threshold_paise
- Hard stop: no action may be scheduled more than appetite.hard_stop_days after the
  first failure

Ladders:
- INSUFFICIENT_FUNDS: t1 salary-timed smart retry · t2 second timed retry ·
  t3 voice (≥₹1k) else final timed retry · t4+ human-or-stop
- AUTH_TIMEOUT / BANK_DOWNTIME: t1 quick plain retry · t2 salary-timed · t3+ human-or-stop
- REVOKED / LIMIT_EXCEEDED: t1 payment link (win-back channel) · t2+ human-or-stop
"""

import datetime as dt
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.domain.enums import FailureMode, Intervention
from paypilot.engine.policy import EpisodeView, ProposedAction
from paypilot.engine.reasoner import Narration, NullReasoner, Reasoner
from paypilot.settings import Settings

_IST = dt.timedelta(hours=5, minutes=30)
_PROMPT_VERSION = "v1"
HARD_STOP_DAYS = 21  # single source of truth for the recovery-horizon rail


class LadderStep(IntEnum):
    FIRST_CONTACT = 1
    SECOND_TOUCH = 2
    THIRD_TOUCH = 3


@dataclass(frozen=True)
class Appetite:
    """How pushy PayPilot may be. Compliance-adjacent config the panel can debate."""

    max_customer_touches: int = 3
    human_threshold_paise: int = 100_000  # humans only ≥ ₹1,000 (cost-aware ops)
    hard_stop_days: int = HARD_STOP_DAYS


STANDARD = Appetite()


@dataclass(frozen=True)
class DecisionRecord:
    """One row of the agent's memory ledger."""

    subscription_id: str
    episode_no: int
    mode: FailureMode
    attempts_made: int
    chosen: Intervention | None
    run_at: dt.datetime | None
    reason: str  # deterministic rationale
    narration: Narration | None  # optional LLM justification
    payload: dict[str, Any]  # exact compact context given to the reasoning layer
    prompt_version: str = _PROMPT_VERSION


class DecisionLedger:
    """In-run append-only memory of every decision."""

    def __init__(self) -> None:
        self.records: list[DecisionRecord] = []

    def add(self, rec: DecisionRecord) -> None:
        self.records.append(rec)


def _next_salary_date(after_utc: dt.datetime) -> dt.date:
    ist_date = (after_utc + _IST).date()
    cal = IndianPaymentCalendar.with_default_festivals(year=ist_date.year)
    return cal.next_salary_date(ist_date)


def _utc_at_ist_hour(d: dt.date, hour_ist: int) -> dt.datetime:
    """UTC instant of ``hour_ist``:30 IST on date d (tz-attached BEFORE offset math)."""
    naive_ist = dt.datetime(d.year, d.month, d.day, hour_ist, 30)
    return naive_ist.replace(tzinfo=dt.UTC) - _IST


class AgentPolicy:
    name = "paypilot-agent"

    def __init__(self, appetite: Appetite = STANDARD, reasoner: Reasoner | None = None) -> None:
        self.appetite = appetite
        self.reasoner: Reasoner = reasoner or NullReasoner()
        self.ledger = DecisionLedger()

    # -- Policy API --------------------------------------------------------------

    def next_action(self, episode: EpisodeView) -> ProposedAction | None:
        # Attempt accounting: episode.attempts_made counts FAILED DEBIT EVENTS so far,
        # including the opening failure (=1 on first consult). Recovery ladder steps
        # therefore key directly on attempts_made: n=1 ⇒ our first recovery contact.
        n = episode.attempts_made

        chosen, when, why = self._decide(episode, n)

        # Hard stop gate: nothing scheduled beyond the recovery window
        if when is not None:
            limit = episode.first_failed_at + dt.timedelta(days=self.appetite.hard_stop_days)
            if when > limit:
                chosen, when, why = None, None, "hard stop: beyond recovery window"

        self._record(episode, chosen, when, why)
        if chosen is None or when is None:
            return None
        return ProposedAction(intervention=chosen, run_at=when)

    # -- strategy ------------------------------------------------------------------

    def _decide(
        self, episode: EpisodeView, n: int
    ) -> tuple[Intervention | None, dt.datetime | None, str]:
        mode = episode.mode
        big_ticket = episode.amount_paise >= self.appetite.human_threshold_paise
        touches_used = n - 1  # recovery contacts we've made so far

        # Customer-touch budget exhausted → internal handoff or clean stop
        if touches_used >= self.appetite.max_customer_touches:
            return self._human_or_stop(episode, big_ticket)

        if mode in (FailureMode.MANDATE_REVOKED, FailureMode.LIMIT_EXCEEDED):
            if n == 1:
                return (
                    Intervention.PAYMENT_LINK,
                    self._plus_days(episode.first_failed_at, 1),
                    "consent-broken mode: open win-back link",
                )
            return self._human_or_stop(episode, big_ticket)

        if mode == FailureMode.INSUFFICIENT_FUNDS:
            if n <= 2:
                when = self._salary_timed_when(episode)
                return Intervention.SMART_RETRY, when, "salary-timed smart retry"
            if n == 3:
                if big_ticket:
                    return (
                        Intervention.VOICE_NUDGE,
                        self._plus_days(episode.first_failed_at, 2),
                        "high-value: personal voice touch",
                    )
                return (
                    Intervention.SMART_RETRY,
                    self._salary_timed_when(episode),
                    "final salary-timed retry",
                )
            return self._human_or_stop(episode, big_ticket)

        # transient modes: AUTH_TIMEOUT, BANK_DOWNTIME
        if n == 1:
            return (
                Intervention.RETRY,
                self._plus_days(episode.first_failed_at, 2),
                "transient infra failure: quick plain retry",
            )
        if n == 2:
            when = self._salary_timed_when(episode)
            return Intervention.SMART_RETRY, when, "persisting: apply salary timing"
        return self._human_or_stop(episode, big_ticket)

    # -- helpers ---------------------------------------------------------------------

    def _salary_timed_when(self, episode: EpisodeView) -> dt.datetime:
        base = episode.first_failed_at + dt.timedelta(days=1)
        salary_date = _next_salary_date(base)
        when = _utc_at_ist_hour(salary_date, 10)  # 10:30 IST, legal mid-morning window
        if when <= episode.first_failed_at:
            when = _utc_at_ist_hour((episode.first_failed_at + dt.timedelta(days=1)).date(), 10)
        return when

    @staticmethod
    def _plus_days(at: dt.datetime, days: int) -> dt.datetime:
        return at + dt.timedelta(days=days)

    def _human_or_stop(
        self, episode: EpisodeView, big_ticket: bool
    ) -> tuple[Intervention | None, dt.datetime | None, str]:
        if big_ticket:
            return (
                Intervention.HUMAN_ESCALATION,
                self._plus_days(episode.first_failed_at, 3),
                f"₹{episode.amount_paise / 100:.0f} clears ops threshold: internal handoff",
            )
        return None, None, "small ticket exhausted: stop cleanly"

    # -- memory ------------------------------------------------------------------------

    def _record(
        self,
        episode: EpisodeView,
        chosen: Intervention | None,
        when: dt.datetime | None,
        why: str,
    ) -> None:
        payload: dict[str, Any] = {
            "episode": {
                "mode": str(episode.mode),
                "amount_rupees": round(episode.amount_paise / 100, 2),
                "attempts_so_far": episode.attempts_made,
                "rail": str(episode.rail),
                "vertical": episode.vertical,
            },
            "action": {"chosen": chosen.value if chosen else None},
        }
        narration = self.reasoner.narrate(payload) if chosen is not None else None
        self.ledger.add(
            DecisionRecord(
                subscription_id=episode.subscription_id,
                episode_no=episode.episode_no,
                mode=episode.mode,
                attempts_made=episode.attempts_made,
                chosen=chosen,
                run_at=when,
                reason=why,
                narration=narration,
                payload=payload,
                prompt_version=_PROMPT_VERSION,
            )
        )


def build_agent_policy(settings: Settings, appetite: Appetite = STANDARD) -> AgentPolicy:
    """Wire the reasoning layer from settings: key present ⇒ OpenRouter, absent ⇒ Null."""
    reasoner: Reasoner | None = None
    key = settings.openrouter_api_key
    if key is not None:
        from paypilot.engine.reasoner import OpenRouterReasoner

        reasoner = OpenRouterReasoner(
            api_key=key.get_secret_value(), model=settings.openrouter_model
        )
    return AgentPolicy(appetite=appetite, reasoner=reasoner)
