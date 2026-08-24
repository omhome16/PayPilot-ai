"""P4.5 slice 2: profiles enrich the graph's story and personalize outcomes."""

from datetime import UTC, datetime

from paypilot.domain.enums import FailureMode, Intervention
from paypilot.engine.policy import EpisodeView
from paypilot.graph.langgraph_agent import build_recovery_graph
from paypilot.simulator.population import PopulationSpec, generate_population


def _view():
    return EpisodeView(
        subscription_id="sub_0001",
        episode_no=1,
        mode=FailureMode.INSUFFICIENT_FUNDS,
        amount_paise=19_900,
        first_failed_at=datetime(2026, 9, 27, 5, 0, tzinfo=UTC),
        attempts_made=0,
        rail=None,
        billing_day=27,
        vertical="ott",
    )


def test_sense_story_includes_customer_history() -> None:
    pop = generate_population(PopulationSpec(size=5, seed=42))
    sub = pop.subscriptions[0]
    profile = next(p for p in pop.profiles if p.customer_id == sub.customer_id)
    v = EpisodeView(
        subscription_id=sub.id,
        episode_no=1,
        mode=FailureMode.MANDATE_REVOKED,
        amount_paise=sub.amount_paise,
        first_failed_at=datetime(2026, 9, 15, 5, 0, tzinfo=UTC),
        attempts_made=0,
        rail=None,
        billing_day=sub.billing_day,
        vertical=sub.plan_name,
        profile=profile,
    )
    app = build_recovery_graph(pop=pop)
    final = app.invoke({"episode": v, "consult_seq": 1})
    story = final["story"]
    assert story["history"]["on_time_ratio"] == round(profile.on_time_ratio, 2)
    assert story["history"]["tenure_cycles"] == profile.tenure_cycles
    assert "history:" in final["history_note"]


def pytest_approx(x: float, tol: float = 1e-9):
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return abs(float(other) - x) <= tol  # type: ignore[arg-type]

    return _Approx()


def test_outcome_model_personalizes_by_reliability() -> None:
    """Reliable payers self-heal more often than unreliable ones (context modifier)."""
    from paypilot.domain.models import CustomerProfile
    from paypilot.simulator.outcome import OutcomeModel

    m = OutcomeModel(seed=3)
    hi = CustomerProfile(
        customer_id="c",
        tenure_cycles=12,
        paid_on_time=12,
        missed_cycles=0,
        reliability=0.95,
        link_affinity=0.5,
    )
    lo = CustomerProfile(
        customer_id="c",
        tenure_cycles=12,
        paid_on_time=2,
        missed_cycles=10,
        reliability=0.15,
        link_affinity=0.5,
    )
    p_hi = m.probability(
        Intervention.WAIT_SELF_HEAL,
        FailureMode.INSUFFICIENT_FUNDS,
        days_to_salary=3,
        profile=hi,
    )
    p_lo = m.probability(
        Intervention.WAIT_SELF_HEAL,
        FailureMode.INSUFFICIENT_FUNDS,
        days_to_salary=3,
        profile=lo,
    )
    assert p_hi > p_lo
