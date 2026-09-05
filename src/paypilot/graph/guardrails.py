"""Guardrails: the unbreakable rails every brain proposal must pass (D29).

The LLM can propose anything; nothing executes without passing here. Every refusal
produces a logged reason AND a safe fallback so the loop always continues lawfully.
Rules consolidated from: compliance types (Phase 1), appetite (Phase 3),
DR2/DR5/DR6/DR9 guardrails (research/07).
"""

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from paypilot.domain.calendar import ist_date_of, next_salary_date, utc_at_ist_hour
from paypilot.domain.enums import FailureMode, Intervention
from paypilot.engine.agent import STANDARD, Appetite
from paypilot.engine.policy import EpisodeView
from paypilot.graph.brain import BrainProposal


@dataclass(frozen=True)
class GuardrailReport:
    approved: bool
    reason: str
    fallback: BrainProposal | None = None  # set on refusal: safe move to run instead


def proposal_run_at(proposal: BrainProposal, episode: EpisodeView) -> dt.datetime:
    """Resolve a proposal's schedule against the episode (salary-day aware)."""
    if proposal.on_salary_day:
        d = next_salary_date(ist_date_of(episode.first_failed_at + dt.timedelta(days=1)))
        return utc_at_ist_hour(d, 10)
    return episode.first_failed_at + dt.timedelta(days=proposal.days_ahead)


class Guardrails(Protocol):
    def check(
        self,
        episode: EpisodeView,
        proposal: BrainProposal,
        *,
        wait_already_used: bool = False,
    ) -> GuardrailReport: ...


def _salary_timed() -> BrainProposal:
    return BrainProposal(
        action=Intervention.SMART_RETRY,
        on_salary_day=True,
        reason="guardrail fallback: salary-timed retry",
    )


def _link_fallback() -> BrainProposal:
    return BrainProposal(
        action=Intervention.PAYMENT_LINK,
        days_ahead=1,
        reason="guardrail fallback: win-back link",
    )


class StandardGuardrails:
    def __init__(self, appetite: Appetite = STANDARD) -> None:
        self.appetite = appetite

    # -- helpers -------------------------------------------------------------------

    def _safe_ladder_fallback(self, episode: EpisodeView) -> BrainProposal:
        mode = episode.mode
        big = episode.amount_paise >= self.appetite.human_threshold_paise
        if mode in (FailureMode.MANDATE_REVOKED, FailureMode.LIMIT_EXCEEDED):
            return _link_fallback()
        if big:
            # the fallback must itself be LEGAL for the mode: voice only where permitted,
            # ops handoff everywhere (HUMAN_ESCALATION is in every permission set)
            if Intervention.VOICE_NUDGE in mode.permitted_interventions:
                return BrainProposal(
                    action=Intervention.VOICE_NUDGE,
                    days_ahead=2,
                    reason="guardrail fallback: voice for high-value",
                )
            return BrainProposal(
                action=Intervention.HUMAN_ESCALATION,
                days_ahead=2,
                reason="guardrail fallback: ops handoff for high-value",
            )
        return _salary_timed()

    # -- the rail ------------------------------------------------------------------

    def check(
        self,
        episode: EpisodeView,
        proposal: BrainProposal,
        *,
        wait_already_used: bool = False,
    ) -> GuardrailReport:
        a = proposal.action

        # R1 compliance: the mode's permission set is law
        if a not in episode.mode.permitted_interventions:
            fb = (
                _link_fallback()
                if episode.mode in (FailureMode.MANDATE_REVOKED, FailureMode.LIMIT_EXCEEDED)
                else self._safe_ladder_fallback(episode)
            )
            return GuardrailReport(
                approved=False,
                reason=f"{a} forbidden for {episode.mode}",
                fallback=fb,
            )

        # R2 patience discipline: WAIT once per episode only
        if a is Intervention.WAIT_SELF_HEAL and wait_already_used:
            return GuardrailReport(
                approved=False,
                reason="WAIT already used this episode",
                fallback=self._safe_ladder_fallback(episode),
            )

        # R3 humans are expensive: threshold gate
        if a is Intervention.HUMAN_ESCALATION and (
            episode.amount_paise < self.appetite.human_threshold_paise
        ):
            return GuardrailReport(
                approved=False,
                reason=(
                    f"human escalation below ₹{self.appetite.human_threshold_paise / 100:.0f} "
                    "threshold"
                ),
                fallback=self._safe_ladder_fallback(episode),
            )

        # R4 recovery horizon: nothing scheduled beyond hard stop
        limit_dt = episode.first_failed_at + dt.timedelta(days=self.appetite.hard_stop_days)
        when = proposal_run_at(proposal, episode)
        if when > limit_dt:
            return GuardrailReport(
                approved=False,
                reason=f"schedule {when.date()} beyond {limit_dt.date()} hard stop",
                fallback=_link_fallback(),
            )

        return GuardrailReport(approved=True, reason="within rails")
