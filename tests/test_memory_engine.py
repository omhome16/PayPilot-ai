"""P4.5 slice 3: the engine attaches profiles to EpisodeViews (fairness: both arms)."""

from datetime import date

from paypilot.domain.enums import Intervention
from paypilot.engine.naive import NaiveRetryPolicy
from paypilot.engine.runner import RunEngine
from paypilot.graph.brain import BrainProposal, FakeBrain
from paypilot.graph.policy_adapter import GraphPolicy
from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.simulator.window import WindowSpec


def _world():
    pop = generate_population(PopulationSpec(size=100, seed=42))
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    events = generate_failures(pop, FailureGenSpec(window=w, seed=42))
    return pop, w, events


def test_engine_passes_profiles_to_policies() -> None:
    pop, w, events = _world()
    seen: list[object] = []
    episodes_seen: set[tuple[str, int]] = set()

    class _Spy:
        name = "spy"

        def next_action(self, episode):
            seen.append(episode.profile)
            episodes_seen.add((episode.subscription_id, episode.episode_no))
            return None

    RunEngine(pop, window=w).run(_Spy(), events)
    # one consult per EPISODE (events can repeat within an episode), all with memory
    assert len(seen) == len(episodes_seen) >= 1
    assert all(p is not None for p in seen)


def test_history_aware_doctrine_beats_baseline_across_worlds() -> None:
    """Memory-powered doctrine (uses reliability) vs naive — the P4.5 payoff check."""

    results = []
    for seed in [1, 2, 3]:
        pop, w, events = (
            _world()
            if seed == 42
            else (
                (
                    lambda s: (
                        lambda p_, w_: (
                            p_,
                            w_,
                            generate_failures(p_, FailureGenSpec(window=w_, seed=s)),
                        )
                    )(
                        generate_population(PopulationSpec(size=150, seed=s)),
                        WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30)),
                    )
                )(seed)
            )
        )

        def doctrine(state):
            prof = state.get("history") or {}
            ratio = prof.get("on_time_ratio", 0.5)
            d = state.get("days_to_salary")
            near = d is not None and 0 <= d <= 7
            if (
                state["attempts"] <= 1
                and state["mode"] == "insufficient_funds"
                and near
                and ratio >= 0.7  # patience ONLY for proven payers (memory!)
            ):
                return BrainProposal(action=Intervention.WAIT_SELF_HEAL, on_salary_day=True)
            if state["mode"] in {"mandate_revoked", "limit_exceeded"}:
                return BrainProposal(action=Intervention.PAYMENT_LINK, days_ahead=1)
            if state["mode"] in {"insufficient_funds", "auth_timeout", "bank_downtime"}:
                return BrainProposal(
                    action=Intervention.SMART_RETRY,
                    on_salary_day=(d is not None and d <= 10),
                    days_ahead=2,
                )
            if state["amount_rupees"] >= 1000:
                return BrainProposal(action=Intervention.VOICE_NUDGE, days_ahead=2)
            return BrainProposal(action=Intervention.PAYMENT_LINK, days_ahead=1)

        base = RunEngine(pop, window=w).run(NaiveRetryPolicy(), events)
        gp = GraphPolicy(brain=FakeBrain(fn=doctrine))
        agent = RunEngine(pop, window=w).run(gp, events)
        assert agent.compliance_violations == 0
        results.append((base.recovered_paise, agent.recovered_paise))

    wins = sum(1 for b, a in results if a > b)
    assert wins >= 2  # memory-aware doctrine dominates on most worlds
