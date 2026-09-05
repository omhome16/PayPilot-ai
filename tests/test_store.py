"""SQLite store: seeding, determinism, audit rows, consent, episodes."""

from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.store import Store


def _seeded(seed: int = 42, size: int = 50) -> Store:
    store = Store.in_memory()
    store.seed_population(generate_population(PopulationSpec(size=size, seed=seed)))
    return store


def test_seed_population_lands_all_domain_rows() -> None:
    store = _seeded(size=30)
    assert len(store.rows("customers")) == 30
    assert len(store.rows("subscriptions")) == 30
    assert len(store.rows("mandates")) == 30
    assert len(store.rows("customer_profiles")) == 30


def test_seeding_is_deterministic_across_stores() -> None:
    a = _seeded(seed=7, size=40)
    b = _seeded(seed=7, size=40)
    assert a.rows("customers") == b.rows("customers")
    assert a.rows("customer_profiles") == b.rows("customer_profiles")


def test_seeding_is_idempotent() -> None:
    store = _seeded(size=25)
    pop = generate_population(PopulationSpec(size=25, seed=1))
    store.seed_population(pop)
    store.seed_population(pop)
    assert len(store.rows("customers")) == 25  # never duplicated


def test_lookup_customer_joins_subscription() -> None:
    store = _seeded(size=5)
    row = store.customer_by_subscription("sub_0001")
    assert row is not None
    assert row["customer_id"] == "cust_0001"
    assert row["amount_paise"] > 0 and row["vertical"] in {"ott", "gym", "saas"}
    profile = store.profile("cust_0001")
    assert profile is not None and profile["tenure_cycles"] >= 1


def test_episodes_upsert_and_query() -> None:
    store = _seeded(size=5)
    store.upsert_episode(
        {
            "episode_key": "sub_0001:1",
            "subscription_id": "sub_0001",
            "episode_no": 1,
            "mode": "insufficient_funds",
            "amount_paise": 19_900,
            "occurred_at": "2026-09-05T04:00:00+00:00",
            "attempts_made": 1,
            "status": "open",
        }
    )
    # same episode re-upserted with more attempts — no duplicate row
    store.upsert_episode(
        {
            "episode_key": "sub_0001:1",
            "subscription_id": "sub_0001",
            "episode_no": 1,
            "mode": "insufficient_funds",
            "amount_paise": 19_900,
            "occurred_at": "2026-09-05T04:00:00+00:00",
            "attempts_made": 2,
            "status": "open",
        }
    )
    eps = store.episodes_for_subscription("sub_0001")
    assert len(eps) == 1 and eps[0]["attempts_made"] == 2


def test_decision_audit_is_append_only() -> None:
    store = _seeded(size=5)
    for i in range(3):
        store.record_decision(
            episode_key="sub_0002:1",
            mode="insufficient_funds",
            attempts=i + 1,
            chosen="smart_retry" if i < 2 else "voice_nudge",
            reason=f"step {i + 1}",
        )
    decisions = store.decisions_for_subscription("sub_0002")
    assert len(decisions) == 3
    assert decisions[0]["chosen"] == "voice_nudge"  # newest first (id DESC)
    store.record_decision(
        episode_key="sub_0002:1",
        mode="insufficient_funds",
        attempts=4,
        chosen="human_escalation",
        reason="fail-loud: brain down",
        degraded="brain_unavailable",
    )
    assert len(store.rows("decisions")) == 4


def test_consent_registry_persists() -> None:
    store = _seeded(size=5)
    assert store.is_do_not_call("cust_0001") is False
    store.mark_do_not_call("cust_0001")
    assert store.is_do_not_call("cust_0001") is True
    # idempotent
    store.mark_do_not_call("cust_0001")
    assert len(store.rows("consent")) == 1


def test_voice_call_rows() -> None:
    store = _seeded(size=5)
    store.record_voice_call(
        episode_key="sub_0003:1",
        merchant_name="FitZone",
        customer_name="Priya",
        script="Namaste!",
        source="llm",
    )
    row = store.rows("voice_calls")[0]
    assert row["episode_key"] == "sub_0003:1" and row["source"] == "llm"
