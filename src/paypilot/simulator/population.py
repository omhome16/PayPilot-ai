"""Synthetic subscriber population — Level-1 fidelity: structured distributions over
realistic Indian patterns (verticals, payday-aware billing days, mandate-rail mix),
fully seeded so both eval arms face the IDENTICAL world."""

import datetime as dt
import random
from dataclasses import dataclass

from paypilot.domain.contacts import normalize_indian_contact
from paypilot.domain.enums import MandateRail, MandateStatus
from paypilot.domain.models import Customer, Mandate, Subscription


@dataclass(frozen=True)
class PopulationSpec:
    size: int = 300
    seed: int = 42
    year: int = 2026


@dataclass(frozen=True)
class Population:
    customers: tuple[Customer, ...]
    subscriptions: tuple[Subscription, ...]
    mandates: tuple[Mandate, ...]


# Vertical bands in paise: (low, high, share of population, plan-name pool)
_VERTICALS: dict[str, tuple[int, int, float, tuple[str, ...]]] = {
    "ott": (
        9_900,
        29_900,
        0.50,
        ("Binge Basic", "CineMonthly", "StreamLite", "PrimeFlicks"),
    ),
    "gym": (
        99_900,
        199_900,
        0.30,
        ("IronMonth", "FitClub Monthly", "PulseGym Pass"),
    ),
    "saas": (
        499_900,
        1_499_900,
        0.20,
        ("Tools Pro", "LedgerCloud", "DeskSuite", "GrowStack"),
    ),
}

_FIRST_NAMES = (
    "Aarav",
    "Priya",
    "Rohan",
    "Ananya",
    "Vikram",
    "Neha",
    "Arjun",
    "Kavya",
    "Siddharth",
    "Meera",
    "Karthik",
    "Divya",
    "Aditya",
    "Isha",
    "Rahul",
    "Sneha",
)
_LAST_NAMES = (
    "Sharma",
    "Patel",
    "Reddy",
    "Iyer",
    "Khan",
    "Verma",
    "Nair",
    "Gupta",
    "Joshi",
    "Mehta",
    "Rao",
    "Singh",
    "Das",
    "Kulkarni",
    "Chopra",
    "Menon",
)


def _realistic_contact(rng: random.Random) -> str:
    """Indian mobile starting 6-9, avoiding recurring runs Razorpay would reject."""
    while True:
        digits = [str(rng.choice("6789"))]
        digits += [str(rng.randint(0, 9)) for _ in range(9)]
        candidate = "+91" + "".join(digits)
        try:
            return normalize_indian_contact(candidate)
        except ValueError:
            continue  # rare: regenerate on a 5-run of identical digits


def _pick_vertical(rng: random.Random) -> str:
    x = rng.random()
    if x < _VERTICALS["ott"][2]:
        return "ott"
    if x < _VERTICALS["ott"][2] + _VERTICALS["gym"][2]:
        return "gym"
    return "saas"


def generate_population(spec: PopulationSpec) -> Population:
    rng = random.Random(spec.seed)  # noqa: S311 — seeded PRNG is the design (reproducible worlds), not crypto

    customers: list[Customer] = []
    subscriptions: list[Subscription] = []
    mandates: list[Mandate] = []

    earliest = dt.date(2024, 6, 1).toordinal()
    latest = dt.date(2026, 8, 21).toordinal()

    for i in range(1, spec.size + 1):
        vertical = _pick_vertical(rng)
        lo, hi, _, plans = _VERTICALS[vertical]

        customer = Customer(
            id=f"cust_{i:04d}",
            name=f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}",
            contact=_realistic_contact(rng),
            vertical=vertical,
        )
        subscription = Subscription(
            id=f"sub_{i:04d}",
            customer_id=customer.id,
            plan_name=rng.choice(plans),
            amount_paise=rng.randint(lo, hi),
            billing_day=rng.randint(1, 28),
        )
        mandate = Mandate(
            id=f"mnd_{i:04d}",
            customer_id=customer.id,
            rail=rng.choices(
                [MandateRail.UPI_AUTOPAY, MandateRail.ENACH, MandateRail.CARD],
                weights=[60, 30, 10],
                k=1,
            )[0],
            status=MandateStatus.ACTIVE,
            max_amount_paise=int(subscription.amount_paise * rng.uniform(1.2, 2.0)),
            created_on=dt.date.fromordinal(rng.randint(earliest, latest)),
        )

        customers.append(customer)
        subscriptions.append(subscription)
        mandates.append(mandate)

    return Population(
        customers=tuple(customers),
        subscriptions=tuple(subscriptions),
        mandates=tuple(mandates),
    )
