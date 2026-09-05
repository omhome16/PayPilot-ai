"""Tool layer: the agent retrieves context through typed, audited tools."""

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.store import RecoveryTools, Store


def _tools(seed: int = 42, size: int = 10) -> tuple[RecoveryTools, Store]:
    store = Store.in_memory()
    store.seed_population(generate_population(PopulationSpec(size=size, seed=seed)))
    return RecoveryTools(store), store


def test_lookup_customer_returns_profile_context() -> None:
    tools, _ = _tools()
    r = tools.lookup_customer("sub_0001")
    assert r.data["found"] is True
    assert r.data["customer_id"] == "cust_0001"
    assert "profile" in r.data and r.data["profile"] is not None
    assert r.rows >= 1


def test_every_tool_call_is_audited() -> None:
    tools, store = _tools()
    tools.lookup_customer("sub_0001")
    tools.episode_history("sub_0001")
    tools.consent_status("sub_0001")
    tools.next_payday("2026-09-05")
    audit = store.rows("tool_calls")
    assert len(audit) == 4
    names = {a["tool_name"] for a in audit}
    assert names == {"lookup_customer", "episode_history", "consent_status", "next_payday"}
    # the in-memory ledger agrees with the store (both append exactly once)
    assert len(tools.calls) == 4


def test_consent_tool_reflects_registry() -> None:
    tools, store = _tools()
    assert tools.consent_status("sub_0001").data["do_not_call"] is False
    store.mark_do_not_call("cust_0001")
    assert tools.consent_status("sub_0001").data["do_not_call"] is True


def test_recent_decisions_and_episode_history_are_empty_until_written() -> None:
    tools, store = _tools()
    store.upsert_episode(
        {
            "episode_key": "sub_0002:1",
            "subscription_id": "sub_0002",
            "episode_no": 1,
            "mode": "insufficient_funds",
            "amount_paise": 19_900,
            "occurred_at": "2026-09-05T04:00:00+00:00",
            "attempts_made": 1,
            "status": "open",
        }
    )
    store.record_decision(
        episode_key="sub_0002:1",
        mode="insufficient_funds",
        attempts=1,
        chosen="smart_retry",
        reason="salary-timed",
    )
    eps = tools.episode_history("sub_0002").data["episodes"]
    assert len(eps) == 1 and eps[0]["status"] == "open"
    recs = tools.recent_decisions("sub_0002").data["decisions"]
    assert len(recs) == 1 and recs[0]["chosen"] == "smart_retry"


def test_next_payday_tool_uses_indian_calendar() -> None:
    tools, _ = _tools()
    r = tools.next_payday("2026-09-05")
    expected = IndianPaymentCalendar.with_default_festivals(year=2026).next_salary_date(
        __import__("datetime").date(2026, 9, 5)
    )
    assert r.data["next_salary_date"] == expected.isoformat()


def test_definitions_are_llm_readable() -> None:
    tools, _ = _tools()
    defs = tools.definitions()
    names = {d["name"] for d in defs}
    assert names == {
        "lookup_customer",
        "episode_history",
        "recent_decisions",
        "consent_status",
        "next_payday",
    }
    assert all("description" in d and "parameters" in d for d in defs)


def test_unknown_tool_raises() -> None:
    tools, _ = _tools()
    import pytest

    with pytest.raises(KeyError):
        tools.call("drop_database")
