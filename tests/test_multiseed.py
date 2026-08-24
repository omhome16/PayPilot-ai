"""Multi-seed evaluation: is the agent's win luck, or structure?

Runs both arms across N seeded worlds; asserts the agent wins consistently,
and provides the data for 'mean ± spread' reporting.
"""

from paypilot.eval.multiseed import SeedOutcome, run_multi_seed


def test_run_multi_seed_returns_structured_results() -> None:
    results = run_multi_seed(seeds=[1, 2], size=120)
    assert len(results) == 2
    r = results[0]
    assert isinstance(r, SeedOutcome)
    assert r.seed == 1
    assert r.at_risk_paise > 0
    assert r.baseline_paise >= 0
    assert r.agent_paise >= 0
    assert r.agent_violations == 0


def test_agent_wins_consistently_across_seeds() -> None:
    """Dominance claim, stated statistically: agent wins ≥80% of worlds AND
    delivers ≥2x mean multiplier. (Not 100% — tuning the doctrine against
    specific seeds would be overfitting; loss-worlds are documented in doc 07.)"""
    results = run_multi_seed(seeds=[1, 2, 3, 4], size=150)
    wins = sum(1 for r in results if r.agent_paise > r.baseline_paise)
    assert wins >= len(results) * 0.8
    mean_mult = sum(r.agent_paise / max(r.baseline_paise, 1) for r in results) / len(results)
    assert mean_mult >= 2.0


def test_zero_violations_everywhere() -> None:
    results = run_multi_seed(seeds=[5, 6], size=150)
    assert all(r.agent_violations == 0 and r.baseline_violations == 0 for r in results)
