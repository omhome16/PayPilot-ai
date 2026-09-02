"""Native LangGraph StateGraph wiring (D28).

Same brain + guardrails as the linear adapter — but expressed as an inspectable,
checkpointable state machine: SENSE → THINK → VALIDATE → (route) → ACT/ABSTAIN.
Every node's input/output is visible to the dashboard and to tests.
"""

import datetime as dt
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from paypilot.domain.enums import Intervention
from paypilot.engine.agent import HARD_STOP_DAYS
from paypilot.engine.policy import EpisodeView
from paypilot.graph.brain import Brain, BrainProposal, FakeBrain
from paypilot.graph.guardrails import (
    GuardrailReport,
    Guardrails,
    StandardGuardrails,
    proposal_run_at,
)


class GraphState(TypedDict, total=False):
    episode: EpisodeView
    consult_seq: int
    story: dict[str, Any]  # SENSE output (JSON-friendly)
    history_note: str  # SENSE: one-line past-behavior summary (P4.5)
    proposal: BrainProposal  # THINK output (or its approved fallback)
    report: GuardrailReport  # first validation
    report2: GuardrailReport | None  # fallback re-validation (when overridden)
    when: dt.datetime | None  # ACT schedule
    abstain: bool  # True → engine returns None (do nothing)


def _sense(state: GraphState) -> GraphState:
    ep = state["episode"]
    key = f"{ep.subscription_id}:{ep.episode_no}"
    story = {
        "episode_key": key,
        "consult_seq": state.get("consult_seq", 1),
        "mode": str(ep.mode),
        "amount_rupees": round(ep.amount_paise / 100, 2),
        "attempts": ep.attempts_made,
        "touches_used": max(ep.attempts_made - 1, 0),
        "rail": str(ep.rail),
        "vertical": ep.vertical,
        "billing_day": ep.billing_day,
    }
    history_note = ""
    if ep.profile is not None:
        ratio = round(ep.profile.on_time_ratio, 2)
        story["history"] = {
            "tenure_cycles": ep.profile.tenure_cycles,
            "on_time_ratio": ratio,
            "missed_cycles": ep.profile.missed_cycles,
            "link_affinity": round(ep.profile.link_affinity, 2),
        }
        tone = (
            "reliable payer"
            if ratio >= 0.8
            else "mixed record"
            if ratio >= 0.5
            else "history of misses"
        )
        history_note = (
            f"history: {tone} ({int(ep.profile.paid_on_time)}/{ep.profile.tenure_cycles} on time)"
        )
    return {"story": story, "history_note": history_note}


def _make_think(brain: Brain) -> "Any":
    def think(state: GraphState) -> GraphState:
        return {"proposal": brain.propose(state["story"])}

    return think


def _never() -> BrainProposal:
    return BrainProposal(action=Intervention.SMART_RETRY)


def build_recovery_graph(
    brain: Brain | None = None,
    guardrails: Guardrails | None = None,
    hard_stop_days: int = HARD_STOP_DAYS,
    pop: Any | None = None,  # Population; profiles ride inside EpisodeView
) -> Any:
    """Compile SENSE→THINK→VALIDATE→ACT/ABSTAIN into a LangGraph StateGraph."""
    brain = brain or FakeBrain()
    rails = guardrails or StandardGuardrails()

    def validate(state: GraphState) -> GraphState:
        ep = state["episode"]
        prop = state["proposal"] or _never()
        report = rails.check(ep, prop)
        out: GraphState = {"report": report}
        if not report.approved and report.fallback is not None:
            out["proposal"] = report.fallback  # the fallback becomes the proposal
            out["report2"] = rails.check(ep, report.fallback)
        return out

    def route_after_validate(state: GraphState) -> str:
        r1 = state.get("report")
        r2 = state.get("report2")
        approved = (r1 is not None and r1.approved) or (r2 is not None and r2.approved)
        return "act" if approved else "abstain"

    def act(state: GraphState) -> GraphState:
        ep = state["episode"]
        prop = state.get("proposal")
        if prop is None:  # unreachable via routing; defensive
            return {"abstain": True, "when": None}
        when = proposal_run_at(prop, ep)
        limit = ep.first_failed_at + dt.timedelta(days=hard_stop_days)
        if when > limit:  # even approved moves respect the recovery horizon
            return {"abstain": True, "when": None}
        return {"when": when, "abstain": False}

    def abstain(state: GraphState) -> GraphState:
        return {"abstain": True, "when": None}

    g = StateGraph(GraphState)
    g.add_node("sense", _sense)
    g.add_node("think", _make_think(brain))
    g.add_node("validate", validate)
    g.add_node("act", act)
    g.add_node("abstain", abstain)
    g.set_entry_point("sense")
    g.add_edge("sense", "think")
    g.add_edge("think", "validate")
    g.add_conditional_edges("validate", route_after_validate, {"act": "act", "abstain": "abstain"})
    g.add_edge("act", END)
    g.add_edge("abstain", END)
    return g.compile()
