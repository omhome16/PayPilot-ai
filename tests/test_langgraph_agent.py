"""Native LangGraph wiring (D28): same decisions, now through a real StateGraph.

The adapter remains the Policy; the graph makes every step inspectable/checkpointable
and proves our loop IS a state machine — not just a linear function.
"""

from datetime import UTC, datetime

from paypilot.domain.enums import FailureMode, Intervention
from paypilot.engine.policy import EpisodeView
from paypilot.graph.brain import BrainProposal, FakeBrain
from paypilot.graph.guardrails import GuardrailReport
from paypilot.graph.langgraph_agent import build_recovery_graph


def _view(mode=FailureMode.MANDATE_REVOKED):
    return EpisodeView(
        subscription_id="sub_0001",
        episode_no=1,
        mode=mode,
        amount_paise=150_000,
        first_failed_at=datetime(2026, 9, 27, 5, 0, tzinfo=UTC),
        attempts_made=0,
        rail=None,
        billing_day=27,
        vertical="gym",
    )


def test_graph_invokes_and_returns_state_with_decision() -> None:
    brain = FakeBrain(fn=lambda s: BrainProposal(action=Intervention.PAYMENT_LINK, days_ahead=1))
    app = build_recovery_graph(brain)
    final = app.invoke({"episode": _view(), "consult_seq": 1})
    assert "proposal" in final and "report" in final and "when" in final
    assert final["report"].approved is True
    assert final["when"] is not None


def test_graph_routes_through_fallback_on_illegal_proposal() -> None:
    from paypilot.domain.enums import Intervention

    brain = FakeBrain(default_action=Intervention.RETRY)  # illegal for REVOKED
    app = build_recovery_graph(brain)
    final = app.invoke({"episode": _view(), "consult_seq": 1})
    assert final["report"].approved is False
    assert "forbidden" in final["report"].reason
    # the fallback proposal was re-validated and approved:
    assert final["report2"].approved is True


def test_graph_routes_to_escalate_when_brain_unavailable() -> None:
    """Fail-loud: a brain that raises BrainUnavailable ends the episode flagged
    escalated — no deterministic substitute proposal is fabricated."""
    from paypilot.graph.llm_brain import BrainUnavailable

    class _BrokenBrain:
        def propose(self, state) -> BrainProposal:
            raise BrainUnavailable("provider down")

    app = build_recovery_graph(_BrokenBrain())
    final = app.invoke({"episode": _view(), "consult_seq": 1})
    assert final.get("escalated") is True
    assert "provider down" in final.get("escalate_reason", "")
    assert final.get("abstain") is True and final.get("when") is None


def test_graph_abstains_when_proposal_and_fallback_both_refused() -> None:
    """Late-crunch ISF episode: humans refused (small ticket) → salary-timed fallback
    also refused (lands past the 21-day stop) → graph must abstain lawfully."""
    v = EpisodeView(
        subscription_id="sub_x",
        episode_no=1,
        mode=FailureMode.INSUFFICIENT_FUNDS,
        amount_paise=19_900,  # small ticket
        first_failed_at=datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
        attempts_made=0,
        rail=None,
        billing_day=1,
        vertical="ott",
    )
    from paypilot.domain.enums import Intervention

    brain = FakeBrain(default_action=Intervention.HUMAN_ESCALATION)
    app = build_recovery_graph(brain)
    final = app.invoke({"episode": v, "consult_seq": 1})
    assert final["abstain"] is True and final["when"] is None


def test_graph_act_node_horizon_backstop() -> None:
    """Defense-in-depth: even with lax guardrails that approve anything, ACT itself
    refuses to schedule beyond the recovery horizon."""

    class _LaxGuardrails:
        def check(self, episode, proposal, *, wait_already_used=False):
            return GuardrailReport(approved=True, reason="lax")

    from paypilot.domain.enums import Intervention

    v = EpisodeView(
        subscription_id="sub_y",
        episode_no=1,
        mode=FailureMode.INSUFFICIENT_FUNDS,
        amount_paise=150_000,
        first_failed_at=datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
        attempts_made=0,
        rail=None,
        billing_day=1,
        vertical="saas",
    )
    brain = FakeBrain(fn=lambda s: BrainProposal(action=Intervention.SMART_RETRY, days_ahead=40))
    app = build_recovery_graph(brain, guardrails=_LaxGuardrails())
    final = app.invoke({"episode": v, "consult_seq": 1})
    assert final["abstain"] is True and final["when"] is None
