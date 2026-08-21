"""Population generator contract: deterministic, realistic, structurally sound."""

from datetime import date

from paypilot.domain.contacts import normalize_indian_contact
from paypilot.domain.enums import MandateRail, MandateStatus
from paypilot.simulator.population import PopulationSpec, generate_population


def _pop(seed: int = 42, size: int = 300):
    return generate_population(PopulationSpec(size=size, seed=seed, year=2026))


# --- Determinism (the fair-A/B guarantee) --------------------------------------


def test_same_seed_produces_identical_population() -> None:
    assert _pop(42) == _pop(42)


def test_different_seed_produces_different_population() -> None:
    assert _pop(42) != _pop(7)


# --- Structure ------------------------------------------------------------------


def test_size_respected_and_all_ids_linked() -> None:
    p = _pop(size=300)
    assert len(p.customers) == len(p.subscriptions) == len(p.mandates) == 300

    cust_ids = {c.id for c in p.customers}
    for s in p.subscriptions:
        assert s.customer_id in cust_ids
    for m in p.mandates:
        assert m.customer_id in cust_ids


def test_one_active_subscription_and_mandate_per_customer() -> None:
    p = _pop()
    for c in p.customers:
        subs = [s for s in p.subscriptions if s.customer_id == c.id]
        mnds = [m for m in p.mandates if m.customer_id == c.id]
        assert len(subs) == 1 and len(mnds) == 1
        assert mnds[0].status is MandateStatus.ACTIVE


def test_mandate_headroom_covers_subscription_amount() -> None:
    p = _pop()
    amounts = {s.customer_id: s.amount_paise for s in p.subscriptions}
    for m in p.mandates:
        assert m.max_amount_paise >= amounts[m.customer_id]


# --- Realism ---------------------------------------------------------------------


def test_all_contacts_pass_razorpay_style_validation() -> None:
    for c in _pop().customers:
        assert normalize_indian_contact(c.contact) == c.contact


def test_amounts_within_vertical_bands() -> None:
    bands = {  # paise
        "ott": (9_900, 29_900),
        "gym": (99_900, 199_900),
        "saas": (499_900, 1_499_900),
    }
    subs = {s.id: s for s in _pop().subscriptions}
    custs = {c.id: c for c in _pop().customers}
    for s in subs.values():
        lo, hi = bands[custs[s.customer_id].vertical]
        assert lo <= s.amount_paise <= hi, f"{s.plan_name} out of band"


def test_mandate_rail_mix_is_realistic() -> None:
    rails = [m.rail for m in _pop().mandates]
    n = len(rails)
    share = lambda r: rails.count(r) / n  # noqa: E731
    assert 0.45 <= share(MandateRail.UPI_AUTOPAY) <= 0.75
    assert 0.15 <= share(MandateRail.ENACH) <= 0.45
    assert 0.02 <= share(MandateRail.CARD) <= 0.20


def test_billing_days_within_month_and_created_dates_sane() -> None:
    p = _pop()
    # billing_day is stored on the subscription cycle metadata via plan cycle day
    for s in p.subscriptions:
        assert 1 <= s.billing_day <= 28
    for m in p.mandates:
        assert date(2024, 6, 1) <= m.created_on <= date(2026, 8, 21)
