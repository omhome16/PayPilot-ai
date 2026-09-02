"""Multi-seed evaluation (PLAN risk #4 / D30 stability leg).

Runs BOTH arms across N seeded worlds. The scripted strategist encodes our
recovery doctrine once (research/07 rules) so every world is fought by the same
brain — only the world changes. Output feeds 'mean ± spread' reporting.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from paypilot.domain.enums import Intervention
from paypilot.engine.naive import NaiveRetryPolicy
from paypilot.engine.runner import RunEngine
from paypilot.graph.brain import BrainProposal, FakeBrain
from paypilot.graph.policy_adapter import GraphPolicy
from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.simulator.window import WindowSpec

_WINDOW = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))


def scripted_strategist(state: dict[str, Any]) -> BrainProposal:
    """Doctrine v3 (research/07 rules, mode-aware).

    v2 lesson (seed-1): patience needs calendar context.
    v3 lesson (20-world sweep): voice/humans are LOW-probability channels on
    transient modes where timed retries excel — save them for consent-broken
    episodes and last resorts. Expected value beats channel fanciness.
    """
    mode = state["mode"]
    transient = mode in {"insufficient_funds", "auth_timeout", "bank_downtime"}
    consent_broken = mode in {"mandate_revoked", "limit_exceeded"}
    days_to_salary = state.get("days_to_salary")
    near_payday = days_to_salary is not None and 0 <= days_to_salary <= 7

    # 1) fresh cash-crunch near payday → strategic patience
    if state["attempts"] <= 1 and mode == "insufficient_funds" and near_payday:
        return BrainProposal(
            action=Intervention.WAIT_SELF_HEAL,
            on_salary_day=True,
            reason="fresh crunch near payday; patience first",
        )

    # 2) consent-broken mandates → win-back link (the one channel that fits)
    if consent_broken:
        return BrainProposal(
            action=Intervention.PAYMENT_LINK,
            days_ahead=1,
            reason="consent-fresh self-serve win-back",
        )

    # 3) transient modes → timed retries are the highest-EV move
    if transient:
        return BrainProposal(
            action=Intervention.SMART_RETRY,
            on_salary_day=(days_to_salary is not None and days_to_salary <= 10),
            days_ahead=2,
            reason="timed retry on recoverable rail",
        )

    # 4) anything else → escalate by value
    if state["amount_rupees"] >= 1000:
        return BrainProposal(
            action=Intervention.VOICE_NUDGE,
            days_ahead=2,
            reason="high-value episode earns personal channel",
        )
    return BrainProposal(
        action=Intervention.PAYMENT_LINK,
        days_ahead=1,
        reason="one-click self-serve win-back",
    )


@dataclass(frozen=True)
class SeedOutcome:
    seed: int
    episodes: int
    at_risk_paise: int
    baseline_paise: int
    agent_paise: int
    baseline_recovered_episodes: int
    agent_recovered_episodes: int
    baseline_violations: int
    agent_violations: int


def run_multi_seed(seeds: list[int], size: int = 300) -> list[SeedOutcome]:
    """Fight the full batch in each seeded world; return per-world outcomes."""
    outcomes: list[SeedOutcome] = []
    for seed in seeds:
        pop = generate_population(PopulationSpec(size=size, seed=seed))
        events = generate_failures(pop, FailureGenSpec(window=_WINDOW, seed=seed))

        base = RunEngine(pop, window=_WINDOW).run(NaiveRetryPolicy(), events)

        gp = GraphPolicy(brain=FakeBrain(fn=scripted_strategist))
        agent = RunEngine(pop, window=_WINDOW).run(gp, events)

        outcomes.append(
            SeedOutcome(
                seed=seed,
                episodes=len({(e.subscription_id, e.episode_no) for e in events}),
                at_risk_paise=base.at_risk_paise,
                baseline_paise=base.recovered_paise,
                agent_paise=agent.recovered_paise,
                baseline_recovered_episodes=base.recovered_episodes,
                agent_recovered_episodes=agent.recovered_episodes,
                baseline_violations=base.compliance_violations,
                agent_violations=agent.compliance_violations,
            )
        )
    return outcomes
