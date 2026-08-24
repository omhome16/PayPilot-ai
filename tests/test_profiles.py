"""P4.5 contracts: hidden behavioral traits GENERATE history — informative, never leaking."""

from paypilot.domain.models import CustomerProfile
from paypilot.simulator.population import PopulationSpec, generate_population


def test_population_includes_one_profile_per_customer() -> None:
    pop = generate_population(PopulationSpec(size=50, seed=42))
    assert len(pop.profiles) == len(pop.subscriptions)
    assert {p.customer_id for p in pop.profiles} == {s.customer_id for s in pop.subscriptions}


def test_history_counts_are_consistent() -> None:
    for p in generate_population(PopulationSpec(size=40, seed=7)).profiles:
        assert isinstance(p, CustomerProfile)
        assert p.paid_on_time + p.missed_cycles == p.tenure_cycles
        assert 1 <= p.tenure_cycles <= 36
        assert 0.0 <= p.reliability <= 1.0
        assert 0.0 <= p.link_affinity <= 1.0


def test_reliability_drives_paid_history() -> None:
    """A 0.95-reliable customer must have a better on-time ratio than a 0.2 one."""
    pop = generate_population(PopulationSpec(size=300, seed=11))
    hi = max(pop.profiles, key=lambda p: p.reliability)
    lo = min(pop.profiles, key=lambda p: p.reliability)
    hi_ratio = hi.paid_on_time / hi.tenure_cycles
    lo_ratio = lo.paid_on_time / lo.tenure_cycles
    assert hi_ratio > lo_ratio


def test_deterministic_same_seed_same_traits() -> None:
    a = [
        (p.customer_id, p.reliability, p.link_affinity, p.tenure_cycles)
        for p in generate_population(PopulationSpec(size=20, seed=99)).profiles
    ]
    b = [
        (p.customer_id, p.reliability, p.link_affinity, p.tenure_cycles)
        for p in generate_population(PopulationSpec(size=20, seed=99)).profiles
    ]
    assert a == b
