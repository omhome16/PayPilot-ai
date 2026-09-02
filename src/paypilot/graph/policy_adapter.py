"""GraphPolicy: runs the SENSE→THINK→VALIDATE→ACT loop inside the engine's Policy socket.

This is the Phase-4 agent. The brain (LLM in LIVE, FakeBrain in tests/replay) proposes;
guardrails dispose; every decision and override is journaled for record/replay.
"""

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.domain.enums import Intervention
from paypilot.engine.agent import HARD_STOP_DAYS
from paypilot.engine.policy import EpisodeView, ProposedAction
from paypilot.graph.brain import Brain, BrainProposal, BrainState, FakeBrain
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
    timing_on_salary_day: bool = False
    timing_days_ahead: int = 0
    abstain: bool = False  # True = recorded 'do nothing' consult


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
        hard_stop_days: int = HARD_STOP_DAYS,
    ) -> None:
        self.brain: Brain = brain or FakeBrain()
        self.guardrails: Guardrails = guardrails or StandardGuardrails()
        self.wait_budget = wait_budget_per_episode
        self.hard_stop_days = hard_stop_days
        self.wait_used: dict[str, bool] = {}
        self._consult_seq: dict[str, int] = {}  # per-episode consult counter
        self.override_log: list[GuardrailReport] = []
        self.journal: list[DecisionJournalEntry] = []

    # -- Policy API ----------------------------------------------------------------

    def next_action(self, episode: EpisodeView) -> ProposedAction | None:
        key = f"{episode.subscription_id}:{episode.episode_no}"
        seq = self._consult_seq.get(key, 0) + 1
        self._consult_seq[key] = seq

        # SENSE — build the compact story the brain reasons over
        state: dict[str, Any] = self._sense(episode)

        # THINK — the brain proposes (LLM in LIVE mode; FakeBrain in tests/replay)
        proposal = self.brain.propose(state)

        # Replay may carry an explicit abstention recorded earlier
        if proposal.abstain:
            self._journal(
                key,
                proposal,
                approved=True,
                final=None,
                when=None,
                reason="replay: recorded abstention",
            )
            return None

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
        limit = episode.first_failed_at + dt.timedelta(days=self.hard_stop_days)
        if when > limit:
            # Journal the abstention so replays align consult-for-consult
            self._journal(
                key,
                proposal,
                approved=report.approved,
                final=None,
                when=None,
                reason=f"beyond hard stop ({limit.date()})",
            )
            return None

        self._journal(
            key,
            proposal,
            approved=report.approved,
            final=proposal.action,
            when=when,
            reason=proposal.reason or report.reason,
        )
        return ProposedAction(intervention=proposal.action, run_at=when)

    def _journal(
        self,
        key: str,
        proposal: BrainProposal,
        *,
        approved: bool,
        final: Intervention | None,
        when: dt.datetime | None,
        reason: str,
    ) -> None:
        self.journal.append(
            DecisionJournalEntry(
                episode_key=key,
                proposal_action=proposal.action.value,
                approved=approved,
                final_action=final.value if final is not None else None,
                run_at=when,
                reason=reason,
                timing_on_salary_day=proposal.on_salary_day,
                timing_days_ahead=proposal.days_ahead,
                abstain=final is None,
            )
        )

    # -- record/replay support (D30) -------------------------------------------------

    def journal_entries(self) -> list[dict[str, Any]]:
        """Structured, JSON-ready records of every THINK+VALIDATE outcome."""
        out: list[dict[str, Any]] = []
        for i, e in enumerate(self.journal, start=1):
            out.append(
                {
                    "seq": i,
                    "episode_key": e.episode_key,
                    "proposal": {
                        "action": e.proposal_action,
                        "on_salary_day": e.timing_on_salary_day,
                        "days_ahead": e.timing_days_ahead,
                        "abstain": e.abstain,
                    },
                    "approved": e.approved,
                    "final_action": e.final_action,
                    "reason": e.reason,
                }
            )
        return out

    # -- internals -------------------------------------------------------------------

    def _sense(self, ep: EpisodeView) -> dict[str, Any]:
        key = f"{ep.subscription_id}:{ep.episode_no}"
        ist_date = (ep.first_failed_at + _IST).date()
        days_to_salary = (_next_salary(ist_date) - ist_date).days
        history_note = ""
        history: dict[str, Any] | None = None
        if ep.profile is not None:
            ratio = round(ep.profile.on_time_ratio, 2)
            tone = (
                "reliable payer"
                if ratio >= 0.8
                else "mixed record"
                if ratio >= 0.5
                else "history of misses"
            )
            history_note = (
                f"history: {tone} "
                f"({int(ep.profile.paid_on_time)}/{ep.profile.tenure_cycles} on time)"
            )
            history = {
                "tenure_cycles": ep.profile.tenure_cycles,
                "on_time_ratio": ratio,
                "missed_cycles": ep.profile.missed_cycles,
                "link_affinity": round(ep.profile.link_affinity, 2),
            }
        state = BrainState(
            mode=str(ep.mode),
            amount_rupees=round(ep.amount_paise / 100, 2),
            attempts=ep.attempts_made,
            touches_used=max(ep.attempts_made - 1, 0),
            wait_already_used=self.wait_used.get(key, False),
            rail=str(ep.rail),
            vertical=ep.vertical,
            days_to_salary=days_to_salary,
            history_note=history_note,
        )
        out: dict[str, Any] = {
            "episode_key": key,
            "consult_seq": self._consult_seq.get(key, 1),
            **asdict(state),
        }  # plain JSON-friendly dict for the LLM/replay path
        if history is not None:
            out["history"] = history
        return out
