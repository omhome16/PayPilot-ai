"""WAIT extension: patience as strategy (DR9)."""

from paypilot.domain.enums import FailureMode, Intervention


def test_wait_exists_and_is_permitted_only_for_isf() -> None:
    from paypilot.domain.interventions_ext import verify_wait_self_heal_wiring

    verify_wait_self_heal_wiring()
    assert Intervention.WAIT_SELF_HEAL in FailureMode.INSUFFICIENT_FUNDS.permitted_interventions
    for mode in FailureMode:
        if mode is not FailureMode.INSUFFICIENT_FUNDS:
            assert Intervention.WAIT_SELF_HEAL not in mode.permitted_interventions


def test_wait_has_calibrated_cell_within_bounds() -> None:
    from paypilot.simulator.outcome import OutcomeModel

    p = OutcomeModel(seed=1).probability(
        Intervention.WAIT_SELF_HEAL, FailureMode.INSUFFICIENT_FUNDS
    )
    assert 0.02 <= p <= 0.85
