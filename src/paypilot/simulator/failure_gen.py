"""Failure-event generation — WHEN debits fail and WHY (root causes), calendar-aware.

Design notes (see learning/phase-01 doc):
- An EPISODE is one logical failure incident: attempt_no=1 always exists; transient modes
  (bank downtime, auth timeout) may see a second natural attempt a few days later.
- Root cause is sampled ONCE per episode and stamped on every attempt — this is what the
  agent will learn to diagnose from context.
- Crunch multipliers concentrate failures in the 25th–3rd cash-tight window; billing days
  themselves are salary-skewed in the population generator.
"""

import datetime as dt
import random
from dataclasses import dataclass
from itertools import count

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.domain.enums import FailureMode
from paypilot.domain.models import FailureEvent
from paypilot.simulator.population import Population
from paypilot.simulator.window import WindowSpec

_IST_OFFSET = dt.timedelta(hours=5, minutes=30)

# Base monthly failure probability outside any special context. Calibrated so the
# blended rate lands ~15% (research: Razorpay-platform subscriptions fail 8–15%/mo;
# our window deliberately includes crunch stress).
_BASE_FAIL_RATE = 0.08
_CRUNCH_MULTIPLIER = 3.2  # cash-tight days fail far more often
_FESTIVAL_MULTIPLIER = 1.5  # spending surges strain balances
_WEEKEND_MULTIPLIER = 1.2  # payroll/banking friction

# Root-cause prior (SIMULATOR_ASSUMPTIONS.md §failure-modes): insufficient funds dominates.
# Revoked weighted up slightly to offset transient modes' extra retry events in the
# event-level denominator (episode-level share stays ~10%).
_MODE_WEIGHTS: dict[FailureMode, float] = {
    FailureMode.INSUFFICIENT_FUNDS: 0.52,
    FailureMode.AUTH_TIMEOUT: 0.14,
    FailureMode.BANK_DOWNTIME: 0.09,
    FailureMode.LIMIT_EXCEEDED: 0.12,
    FailureMode.MANDATE_REVOKED: 0.13,
}
_TRANSIENT_MODES = frozenset({FailureMode.AUTH_TIMEOUT, FailureMode.BANK_DOWNTIME})
_TRANSIENT_SECOND_ATTEMPT_P = 0.30  # P(bank-side auto-retry | transient root cause)
_RETRY_GAP_DAYS = 3


@dataclass(frozen=True)
class FailureGenSpec:
    window: WindowSpec
    seed: int = 42


def _debit_datetime_utc(day: dt.date, rng: random.Random) -> dt.datetime:
    """A debit moment inside NPCI-friendly hours (08:00–21:59 IST), timezone-aware UTC."""
    ist_hour = rng.randint(8, 21)
    ist_minute = rng.randint(0, 59)
    ist_naive = dt.datetime(day.year, day.month, day.day, ist_hour, ist_minute)
    return ist_naive.replace(tzinfo=dt.UTC) - _IST_OFFSET  # UTC instant of that IST wall-clock


def generate_failures(population: Population, spec: FailureGenSpec) -> tuple[FailureEvent, ...]:
    rng = random.Random(spec.seed)  # noqa: S311 — seeded PRNG is the design (reproducible worlds)
    cal = IndianPaymentCalendar.with_default_festivals(year=spec.window.start.year)
    modes = list(_MODE_WEIGHTS)
    weights = [_MODE_WEIGHTS[m] for m in modes]

    subs_by_customer = {s.customer_id: s for s in population.subscriptions}
    events: list[FailureEvent] = []
    counter = count(1)

    for sub in sorted(subs_by_customer.values(), key=lambda s: s.id):
        # Monthly debit candidates within the window (billing_day clamped to <= 28).
        months: list[tuple[int, int]] = []
        y, m = spec.window.start.year, spec.window.start.month
        while (y, m) <= (spec.window.end.year, spec.window.end.month):
            months.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

        for yy, mm in months:
            day = dt.date(yy, mm, sub.billing_day)
            if not (spec.window.start <= day <= spec.window.end):
                continue

            p_fail = _BASE_FAIL_RATE
            if cal.in_payday_crunch(day):
                p_fail *= _CRUNCH_MULTIPLIER
            if cal.festival_on_or_near(day) is not None:
                p_fail *= _FESTIVAL_MULTIPLIER
            if cal.is_weekend(day):
                p_fail *= _WEEKEND_MULTIPLIER

            if rng.random() >= min(p_fail, 0.55):
                continue

            mode = rng.choices(modes, weights=weights, k=1)[0]
            episode = next(counter)
            base_moment = _debit_datetime_utc(day, rng)

            events.append(
                FailureEvent(
                    id=f"evt_{episode:05d}",
                    subscription_id=sub.id,
                    mode=mode,
                    occurred_at=base_moment,
                    attempt_no=1,
                    amount_paise=sub.amount_paise,
                )
            )

            if mode in _TRANSIENT_MODES and rng.random() < _TRANSIENT_SECOND_ATTEMPT_P:
                events.append(
                    FailureEvent(
                        id=f"evt_{next(counter):05d}",
                        subscription_id=sub.id,
                        mode=mode,
                        occurred_at=base_moment + dt.timedelta(days=_RETRY_GAP_DAYS),
                        attempt_no=2,
                        amount_paise=sub.amount_paise,
                    )
                )

    return tuple(events)
