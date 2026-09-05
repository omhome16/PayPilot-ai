"""Phase 4 core: the agentic recovery graph — FakeBrain, guardrails, Policy adapter."""

import datetime as dt
from datetime import date, datetime

from paypilot.domain.enums import FailureMode, Intervention, MandateRail
from paypilot.engine.policy import EpisodeView
from paypilot.graph.brain import BrainProposal, FakeBrain
from paypilot.graph.guardrails import GuardrailReport, Guardrails, StandardGuardrails
from paypilot.graph.policy_adapter import GraphPolicy


def _view(mode=FailureMode.INSUFFICIENT_FUNDS, attempts=0, amount_paise=19_900, failed_at=None):
    return EpisodeView(
        subscription_id="sub_0001",
        episode_no=1,
        mode=mode,
        amount_paise=amount_paise,
        first_failed_at=failed_at or datetime(2026, 9, 27, 5, 0, tzinfo=dt.UTC),
        attempts_made=attempts,
        rail=MandateRail.UPI_AUTOPAY,
        billing_day=27,
        vertical="ott",
    )


# --- FakeBrain -------------------------------------------------------------------


def test_fakebrain_returns_scripted_proposal() -> None:
    brain = FakeBrain(default_action=Intervention.WAIT_SELF_HEAL, on_salary_day=True)
    p = brain.propose({"mode": "insufficient_funds"})
    assert p.action is Intervention.WAIT_SELF_HEAL
    assert p.on_salary_day is True


def test_fakebrain_supports_scripted_sequence() -> None:
    brain = FakeBrain.sequence(
        [
            BrainProposal(action=Intervention.WAIT_SELF_HEAL, on_salary_day=True),
            BrainProposal(action=Intervention.VOICE_NUDGE, days_ahead=2),
        ]
    )
    assert brain.propose({}).action is Intervention.WAIT_SELF_HEAL
    assert brain.propose({}).action is Intervention.VOICE_NUDGE
    assert brain.propose({}).action is Intervention.HUMAN_ESCALATION  # exhausted → safe


# --- Guardrails -------------------------------------------------------------------


def test_guardrails_reject_forbidden_intervention_with_fallback() -> None:
    g: Guardrails = StandardGuardrails()
    report = g.check(
        _view(mode=FailureMode.MANDATE_REVOKED),
        BrainProposal(action=Intervention.RETRY, days_ahead=2),
    )
    assert isinstance(report, GuardrailReport)
    assert report.approved is False
    assert "forbidden" in report.reason
    assert report.fallback is not None
    assert report.fallback.intervention is Intervention.PAYMENT_LINK  # safe ladder


def test_guardrails_cap_wait_to_once_per_episode() -> None:
    g: Guardrails = StandardGuardrails()
    ok = g.check(
        _view(),
        BrainProposal(action=Intervention.WAIT_SELF_HEAL, on_salary_day=True),
        wait_already_used=False,
    )
    blocked = g.check(
        _view(), BrainProposal(action=Intervention.WAIT_SELF_HEAL), wait_already_used=True
    )
    assert ok.approved is True
    assert blocked.approved is False
    assert blocked.fallback is not None


def test_guardrails_enforce_touch_budget_and_hard_stop() -> None:
    g: Guardrails = StandardGuardrails()
    late = g.check(
        _view(failed_at=datetime(2026, 9, 1, 5, 0, tzinfo=dt.UTC)),
        BrainProposal(action=Intervention.SMART_RETRY, days_ahead=40),
    )
    assert late.approved is False  # beyond 21-day window


def test_guardrails_require_threshold_for_humans() -> None:
    g: Guardrails = StandardGuardrails()
    small = g.check(
        _view(amount_paise=19_900),
        BrainProposal(action=Intervention.HUMAN_ESCALATION, days_ahead=2),
    )
    assert small.approved is False
    big = g.check(
        _view(amount_paise=150_000),
        BrainProposal(action=Intervention.HUMAN_ESCALATION, days_ahead=2),
    )
    assert big.approved is True


def test_guardrail_fallback_is_itself_legal_for_the_mode() -> None:
    """Big-ticket AUTH_TIMEOUT: voice is NOT permitted there, so the safe fallback
    must be a permitted intervention — otherwise the loop dead-ends into abstain."""
    g: Guardrails = StandardGuardrails()
    illegal = BrainProposal(action=Intervention.PAYMENT_LINK, days_ahead=1)
    report = g.check(_view(mode=FailureMode.AUTH_TIMEOUT, amount_paise=150_000), illegal)
    assert report.approved is False
    assert report.fallback is not None
    fb = report.fallback
    assert fb.action in FailureMode.AUTH_TIMEOUT.permitted_interventions
    # the adapter re-validates every fallback — it must pass
    assert g.check(_view(mode=FailureMode.AUTH_TIMEOUT, amount_paise=150_000), fb).approved


def test_graph_policy_escalates_loudly_when_brain_unavailable() -> None:
    """Fail-loud: a dead brain escalates to human review — even on a small ticket —
    and the failure is journaled + counted, never silently replaced by a guess."""
    from paypilot.graph.llm_brain import BrainUnavailable

    class _BrokenBrain:
        def propose(self, state) -> BrainProposal:
            raise BrainUnavailable("provider down")

    gp = GraphPolicy(brain=_BrokenBrain())
    act = gp.next_action(_view(amount_paise=19_900))  # small ticket: still escalates
    assert act is not None
    assert act.intervention is Intervention.HUMAN_ESCALATION
    assert gp.brain_failures == 1
    entry = gp.journal[-1]
    assert "fail-loud" in entry.reason


def test_big_transient_episode_with_illegal_proposal_still_acts() -> None:
    brain = FakeBrain(default_action=Intervention.PAYMENT_LINK)  # illegal for AUTH_TIMEOUT
    gp = GraphPolicy(brain=brain)
    act = gp.next_action(_view(mode=FailureMode.AUTH_TIMEOUT, amount_paise=150_000))
    assert act is not None
    assert act.intervention in FailureMode.AUTH_TIMEOUT.permitted_interventions


# --- The Policy adapter (graph ⇄ engine socket) -------------------------------------


def test_graph_policy_produces_valid_actions_and_logs_overrides() -> None:
    brain = FakeBrain.sequence(
        [
            BrainProposal(action=Intervention.RETRY, days_ahead=2),  # illegal for REVOKED
        ]
    )
    gp = GraphPolicy(brain=brain)
    act = gp.next_action(_view(mode=FailureMode.MANDATE_REVOKED))
    assert act is not None and act.intervention is Intervention.PAYMENT_LINK  # fallback ran
    assert gp.override_log and "forbidden" in gp.override_log[0].reason


def test_graph_policy_wait_lands_on_salary_day() -> None:
    brain = FakeBrain(default_action=Intervention.WAIT_SELF_HEAL, on_salary_day=True)
    gp = GraphPolicy(brain=brain)
    act = gp.next_action(_view())  # failed 27 Sep IST → salary 1 Oct
    assert act is not None and act.intervention is Intervention.WAIT_SELF_HEAL
    ist_day = (act.run_at + dt.timedelta(hours=5, minutes=30)).day
    assert ist_day == 1


def test_full_engine_integration_beats_baseline() -> None:
    from paypilot.engine.naive import NaiveRetryPolicy
    from paypilot.engine.runner import RunEngine
    from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
    from paypilot.simulator.population import PopulationSpec, generate_population
    from paypilot.simulator.window import WindowSpec

    pop = generate_population(PopulationSpec(size=300, seed=42))
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    events = generate_failures(pop, FailureGenSpec(window=w, seed=42))

    base = RunEngine(pop, window=w).run(NaiveRetryPolicy(), events)

    # Scripted brain plays "smart merchant": wait on fresh crunch, then ladder.
    def smart(state) -> BrainProposal:
        n = state["attempts"]
        if n <= 1:
            return BrainProposal(action=Intervention.WAIT_SELF_HEAL, on_salary_day=True)
        if state["amount_rupees"] >= 1000:
            return BrainProposal(action=Intervention.VOICE_NUDGE, days_ahead=2)
        return BrainProposal(action=Intervention.PAYMENT_LINK, days_ahead=1)

    agent = RunEngine(pop, window=w).run(GraphPolicy(brain=FakeBrain(fn=smart)), events)
    assert agent.compliance_violations == 0
    assert agent.recovered_paise > base.recovered_paise
