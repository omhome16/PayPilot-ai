"""Outcome model contract: calibrated probabilities, context-sensitive, compliance-aware."""

import pytest

from paypilot.domain.enums import FailureMode, Intervention
from paypilot.simulator.outcome import OutcomeModel


def _model() -> OutcomeModel:
    return OutcomeModel(seed=42)


# --- Compliance is physical, not advisory ---------------------------------------


def test_forbidden_intervention_is_rejected() -> None:
    """RETRY after mandate revocation violates consent — the model refuses, always."""
    with pytest.raises(ValueError, match="not permitted"):
        _model().probability(Intervention.RETRY, FailureMode.MANDATE_REVOKED)


# --- Calibration bounds (documented in SIMULATOR_ASSUMPTIONS.md) ------------------


def test_all_permitted_cells_within_documented_bounds() -> None:
    m = _model()
    for mode in FailureMode:
        for iv in sorted(mode.permitted_interventions, key=lambda x: x.value):
            p = m.probability(iv, mode)
            assert 0.02 <= p <= 0.85, f"{iv}/{mode} = {p} outside [0.02, 0.85]"


def test_smart_retry_benefits_from_salary_proximity() -> None:
    """Timing is the whole point: retrying near salary day must beat retrying mid-crunch."""
    m = _model()
    p_near = m.probability(
        Intervention.SMART_RETRY, FailureMode.INSUFFICIENT_FUNDS, days_to_salary=2
    )
    p_far = m.probability(
        Intervention.SMART_RETRY, FailureMode.INSUFFICIENT_FUNDS, days_to_salary=15
    )
    assert p_near > p_far


def test_repeat_attempts_decay() -> None:
    m = _model()
    p1 = m.probability(Intervention.RETRY, FailureMode.AUTH_TIMEOUT, attempt_no=1)
    p3 = m.probability(Intervention.RETRY, FailureMode.AUTH_TIMEOUT, attempt_no=3)
    assert p3 < p1


# --- Deterministic draws -----------------------------------------------------------


def test_draws_are_deterministic_given_seed() -> None:
    a, b = OutcomeModel(seed=7), OutcomeModel(seed=7)
    calls = [(Intervention.PAYMENT_LINK, FailureMode.MANDATE_REVOKED)] * 20
    assert [a.draw(*c) for c in calls] == [b.draw(*c) for c in calls]


def test_draw_frequency_tracks_probability() -> None:
    m = OutcomeModel(seed=99)
    n = 4_000
    hits = sum(m.draw(Intervention.SMART_RETRY, FailureMode.INSUFFICIENT_FUNDS) for _ in range(n))
    rate = hits / n
    expected = m.probability(Intervention.SMART_RETRY, FailureMode.INSUFFICIENT_FUNDS)
    assert abs(rate - expected) < 0.05  # binomial sd ≈ 0.008 at n=4000; generous guard
