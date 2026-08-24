"""GraphPolicy: runs the SENSE→THINK→VALIDATE→ACT loop inside the engine's Policy socket.

This is the Phase-4 agent. The brain (LLM in LIVE, FakeBrain in tests/replay) proposes;
guardrails dispose; every decision and override is journaled for record/replay.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.domain.enums import Intervention
from paypilot.engine.policy import EpisodeView, ProposedAction
from paypilot.graph.brain import Brain, FakeBrain
from paypilot.graph.guardrails import (
    GuardrailReport,
    Guardrails,
    StandardGuardrails,
    proposal_run_at,
)

_IST = dt.timedelta(hours=5, minutes=30)


@dataclass(frozen=True)
class DecisionJournalEntry:
    episode_key: str
    proposal_action: str
    approved: bool
    final_action: str | None
    run_at: dt.datetime | None
    reason: str


def _next_salary(ist_date: dt.date) -> dt.date:
    cal = IndianPaymentCalendar.with_default_festivals(year=ist_date.year)
    return cal.next_salary_date(ist_date)


class GraphPolicy:
    name = "paypilot-graph"

    def __init__(
        self,
        brain: Brain | None = None,
        guardrails: Guardrails | None = None,
        wait_budget_per_episode: int = 1,
    ) -> None:
        self.brain: Brain = brain or FakeBrain()
        self.guardrails: Guardrails = guardrails or StandardGuardrails()
        self.wait_budget = wait_budget_per_episode
        self.wait_used: dict[str, bool] = {}
        self.override_log: list[GuardrailReport] = []
        self.journal: list[DecisionJournalEntry] = []

    # -- Policy API ----------------------------------------------------------------

    def next_action(self, episode: EpisodeView) -> ProposedAction | None:
        key = f"{episode.subscription_id}:{episode.episode_no}"

        # SENSE — build the compact story the brain reasons over
        state: dict[str, Any] = self._sense(episode)

        # THINK — the brain proposes (LLM in LIVE mode; FakeBrain in tests/replay)
        proposal = self.brain.propose(state)

        # VALIDATE — rails dispose; safe fallbacks run on refusal
        report = self.guardrails.check(
            episode,
            proposal,
            wait_already_used=self.wait_used.get(key, False),
        )
        if not report.approved:
            self.override_log.append(report)
            if report.fallback is None:
                raise RuntimeError("guardrail refusal without safe fallback")
            proposal = report.fallback

        # ACT — resolve timing and respect the engine horizon
        if proposal.action is Intervention.WAIT_SELF_HEAL:
            self.wait_used[key] = True
        when = proposal_run_at(proposal, episode)
        limit = episode.first_failed_at + dt.timedelta(days=21)
        if when > limit:
            return None  # even fallbacks respect the horizon

        self.journal.append(
            DecisionJournalEntry(
                episode_key=key,
                proposal_action=proposal.action.value,
                approved=report.approved,
                final_action=proposal.action.value,
                run_at=when,
                reason=proposal.reason or report.reason,
            )
        )
        return ProposedAction(intervention=proposal.action, run_at=when)

    # -- internals -------------------------------------------------------------------

    def _sense(self, ep: EpisodeView) -> dict[str, Any]:
        ist_date = (ep.first_failed_at + _IST).date()
        days_to_salary = (_next_salary(ist_date) - ist_date).days
        state = BrainStateData(
            mode=str(ep.mode),
            amount_rupees=round(ep.amount_paise / 100, 2),
            attempts=ep.attempts_made,
            touches_used=max(ep.attempts_made - 1, 0),
            wait_already_used=self.wait_used.get(f"{ep.subscription_id}:{ep.episode_no}", False),
            rail=str(ep.rail),
            vertical=ep.vertical,
            days_to_salary=days_to_salary,
        )
        return {**state.__dict__}  # plain JSON-friendly dict for the LLM path


# Imported late to avoid a cycle in docs; simple data holder:
from dataclasses import dataclass as _dc  # noqa: E402


@_dc(frozen=True)
class BrainStateData:
    mode: str
    amount_rupees: float
    attempts: int
    touches_used: int
    wait_already_used: bool
    rail: str
    vertical: str
    days_to_salary: int
