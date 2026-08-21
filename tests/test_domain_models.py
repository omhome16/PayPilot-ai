"""Domain invariants: mandate state machine, money in paise, failure taxonomy behavior."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from paypilot.domain.enums import FailureMode, Intervention, MandateRail, MandateStatus
from paypilot.domain.models import Customer, FailureEvent, Mandate, Subscription

# --- Money: always paise internally -------------------------------------------


def test_subscription_amount_must_be_positive_paise() -> None:
    with pytest.raises(ValueError, match="paise"):
        Subscription(id="sub_1", customer_id="cust_1", plan_name="OTT Basic", amount_paise=0)


def test_subscription_stores_amount_exactly() -> None:
    s = Subscription(id="sub_1", customer_id="cust_1", plan_name="OTT Basic", amount_paise=19900)
    assert s.amount_paise == 19900
    assert s.amount_rupees == Decimal("199.00")


# --- Mandate state machine -----------------------------------------------------


def _active_mandate() -> Mandate:
    return Mandate(
        id="mnd_1",
        customer_id="cust_1",
        rail=MandateRail.UPI_AUTOPAY,
        status=MandateStatus.ACTIVE,
        max_amount_paise=500_000,
        created_on=date(2026, 1, 15),
    )


def test_new_mandate_starts_active() -> None:
    m = _active_mandate()
    assert m.status is MandateStatus.ACTIVE
    assert m.can_be_charged


def test_revoked_mandate_cannot_be_charged() -> None:
    m = _active_mandate().revoke()
    assert m.status is MandateStatus.REVOKED
    assert not m.can_be_charged


def test_revoking_twice_is_rejected() -> None:
    m = _active_mandate().revoke()
    with pytest.raises(Exception, match="REVOKED"):
        m.revoke()


def test_paused_mandate_cannot_be_charged_but_can_resume() -> None:
    m = _active_mandate().pause()
    assert m.status is MandateStatus.PAUSED
    assert not m.can_be_charged
    m2 = m.resume()
    assert m2.status is MandateStatus.ACTIVE
    assert m2.can_be_charged


# --- Failure taxonomy carries behavior ----------------------------------------


def test_every_failure_mode_permits_at_least_one_intervention() -> None:
    for mode in FailureMode:
        assert mode.permitted_interventions, f"{mode} permits nothing"


def test_revoked_mandate_forbids_plain_retry() -> None:
    assert Intervention.RETRY not in FailureMode.MANDATE_REVOKED.permitted_interventions
    assert Intervention.PAYMENT_LINK in FailureMode.MANDATE_REVOKED.permitted_interventions


def test_insufficient_funds_permits_smart_retry() -> None:
    assert Intervention.SMART_RETRY in FailureMode.INSUFFICIENT_FUNDS.permitted_interventions


def test_limit_exceeded_permits_rail_switch() -> None:
    """Amount over cap ⇒ re-authorize on a rail with higher caps (e.g. UPI→eNACH)."""
    assert Intervention.RAIL_SWITCH in FailureMode.LIMIT_EXCEEDED.permitted_interventions


# --- Events and customers ------------------------------------------------------


def test_failure_event_carries_context() -> None:
    ev = FailureEvent(
        id="evt_1",
        subscription_id="sub_1",
        mode=FailureMode.INSUFFICIENT_FUNDS,
        occurred_at=datetime(2026, 9, 28, 9, 30, tzinfo=UTC),
        attempt_no=1,
        amount_paise=19900,
    )
    assert ev.mode is FailureMode.INSUFFICIENT_FUNDS
    assert ev.attempt_no == 1


def test_customer_requires_valid_contact() -> None:
    with pytest.raises(Exception, match="recurring"):
        Customer(id="cust_9", name="Spammy", contact="+919999999999", vertical="ott")
