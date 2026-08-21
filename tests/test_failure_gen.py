"""Failure generation contract: deterministic, calendar-aware, structurally valid."""

from collections import Counter
from datetime import date, time, timedelta

from paypilot.domain.enums import FailureMode
from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.simulator.window import WindowSpec


def _world(seed: int = 42, size: int = 300):
    pop = generate_population(PopulationSpec(size=size, seed=seed))
    events = generate_failures(
        pop,
        FailureGenSpec(
            window=WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30)),
            seed=seed,
        ),
    )
    return pop, events


# --- Determinism ---------------------------------------------------------------


def test_same_seed_same_failures() -> None:
    _, a = _world(42)
    _, b = _world(42)
    assert a == b


def test_different_seed_different_failures() -> None:
    _, a = _world(42)
    _, b = _world(7)
    assert a != b


# --- Structural validity ---------------------------------------------------------


def test_events_reference_real_subscriptions_with_matching_amounts() -> None:
    pop, events = _world()
    amounts = {s.id: s.amount_paise for s in pop.subscriptions}
    assert events, "expected a non-empty failure batch"
    for e in events:
        assert e.subscription_id in amounts
        assert e.amount_paise == amounts[e.subscription_id]
        assert e.attempt_no >= 1


def test_attempts_are_sequential_within_episodes() -> None:
    """An attempt_no=2 must have a prior attempt_no=1 for the same subscription."""
    seen: dict[str, int] = {}
    for e in sorted(_world()[1], key=lambda x: x.occurred_at):
        expected_prev = e.attempt_no - 1
        if expected_prev > 0:
            assert seen.get(e.subscription_id) == expected_prev, (
                f"attempt {e.attempt_no} without its predecessor"
            )
        seen[e.subscription_id] = e.attempt_no


def test_episode_root_mode_is_stable_within_episode() -> None:
    """All attempts of one episode carry the same root cause (diagnosis target)."""
    modes: dict[str, FailureMode] = {}
    for e in sorted(_world()[1], key=lambda x: x.occurred_at):
        key = f"{e.subscription_id}:{e.episode_no}"
        if key in modes:
            assert modes[key] == e.mode
        modes[key] = e.mode


def test_event_ids_unique() -> None:
    ids = [e.id for e in _world()[1]]
    assert len(ids) == len(set(ids))


# --- Realism ---------------------------------------------------------------------


def test_payday_crunch_concentrates_failures() -> None:
    """~30% of days are crunch days, yet they must hold the MAJORITY of failures."""
    events = _world()[1]
    crunch = sum(1 for e in events if e.occurred_at.day >= 25 or e.occurred_at.day <= 3)
    assert crunch / len(events) > 0.50


def test_mode_distribution_is_plausible() -> None:
    events = _world()[1]
    counts = Counter(e.mode for e in events)
    total = len(events)
    # insufficient funds dominates Indian auto-debit failures
    share = lambda m: counts[m] / total  # noqa: E731
    assert share(FailureMode.INSUFFICIENT_FUNDS) > 0.40
    # bounds include ±3pp sampling noise at n≈100 events (sd = sqrt(p(1-p)/n))
    assert 0.05 < share(FailureMode.MANDATE_REVOKED) < 0.18
    assert share(FailureMode.BANK_DOWNTIME) < 0.15


def test_debits_respect_quiet_hours() -> None:
    """No debit attempt between 22:00 and 08:00 IST — NPCI guardrail, even in simulation."""
    ist_offset = timedelta(hours=5, minutes=30)
    for e in _world()[1]:
        ist_time: time = (e.occurred_at + ist_offset).time()
        assert time(8, 0) <= ist_time <= time(21, 59), f"debit at {ist_time} IST"


def test_failure_rate_in_sane_band() -> None:
    """Target ~12% of monthly debits failing (research: Razorpay SaaS 8–15%)."""
    pop, events = _world()
    episodes = len({(e.subscription_id, e.episode_no) for e in events})
    rate = episodes / len(pop.subscriptions)
    assert 0.06 <= rate <= 0.20
