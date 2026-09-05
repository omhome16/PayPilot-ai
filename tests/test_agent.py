"""Agent brain contracts: deterministic strategy + optional LLM narration, hermetically."""

import json
from datetime import UTC, date, datetime, timedelta

import httpx

from paypilot.domain.enums import FailureMode, Intervention, MandateRail
from paypilot.engine.agent import STANDARD, AgentPolicy, DecisionRecord
from paypilot.engine.policy import EpisodeView
from paypilot.engine.reasoner import NullReasoner, OpenRouterReasoner

_IST_DELTA = timedelta(hours=5, minutes=30)


def _view(
    mode: FailureMode = FailureMode.INSUFFICIENT_FUNDS,
    attempts: int = 1,
    amount_paise: int = 19_900,
    failed_at: datetime | None = None,
) -> EpisodeView:
    return EpisodeView(
        subscription_id="sub_0001",
        episode_no=1,
        mode=mode,
        amount_paise=amount_paise,
        first_failed_at=failed_at or datetime(2026, 9, 27, 5, 0, tzinfo=UTC),
        attempts_made=attempts,
        rail=MandateRail.UPI_AUTOPAY,
        billing_day=27,
        vertical="ott",
    )


# --- The deterministic strategy core ---------------------------------------------


def test_isf_retry_times_onto_salary_date() -> None:
    """THE edge: failed 27 Sep (crunch) → retry lands ON 1 Oct (salary), not blindly."""
    a = AgentPolicy(appetite=STANDARD)
    act = a.next_action(_view())
    assert act is not None and act.intervention is Intervention.SMART_RETRY
    assert (act.run_at + _IST_DELTA).day == 1 and (act.run_at + _IST_DELTA).month == 10


def test_transient_first_move_is_quick_plain_retry() -> None:
    a = AgentPolicy(appetite=STANDARD)
    act = a.next_action(_view(mode=FailureMode.BANK_DOWNTIME))
    assert act is not None and act.intervention is Intervention.RETRY


def test_revoked_goes_straight_to_payment_link() -> None:
    """Baseline earns ₹0 here. The agent opens the win-back channel instead."""
    a = AgentPolicy(appetite=STANDARD)
    act = a.next_action(_view(mode=FailureMode.MANDATE_REVOKED))
    assert act is not None and act.intervention is Intervention.PAYMENT_LINK


def test_ladder_escalates_after_repeated_failures() -> None:
    a = AgentPolicy(appetite=STANDARD)
    second = a.next_action(_view(attempts=2))
    assert second is not None
    assert second.intervention in (
        Intervention.VOICE_NUDGE,
        Intervention.RAIL_SWITCH,
        Intervention.RETRY,
        Intervention.SMART_RETRY,
    )


def test_human_escalation_only_above_rupee_threshold() -> None:
    a = AgentPolicy(appetite=STANDARD)
    big = a.next_action(_view(attempts=4, amount_paise=150_000))  # ₹1,500
    small = a.next_action(_view(attempts=4, amount_paise=19_900))  # ₹199
    assert big is not None and big.intervention is Intervention.HUMAN_ESCALATION
    assert small is None  # never pay ops wages to chase ₹199


def test_agent_never_violates_compliance_across_full_ladder() -> None:
    a = AgentPolicy(appetite=STANDARD)
    for mode in FailureMode:
        for attempts in range(1, 7):
            act = a.next_action(_view(mode=mode, attempts=attempts))
            if act is not None:
                assert act.intervention in mode.permitted_interventions


def test_every_decision_lands_in_the_memory_ledger() -> None:
    a = AgentPolicy(appetite=STANDARD)
    before = len(a.ledger.records)
    a.next_action(_view())
    assert len(a.ledger.records) == before + 1
    rec = a.ledger.records[-1]
    assert isinstance(rec, DecisionRecord)
    assert rec.prompt_version.startswith("v")


# --- The reasoning layer (mocked transport — CI never touches the network) ---------


def _ok_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "x",
            "model": "test",
            "created": 0,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40},
        },
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


def test_openrouter_reasoner_sends_compact_payload_and_parses() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode())
        return _ok_response("Salary-day timing chosen because balance crunch resolves on payday.")

    r = OpenRouterReasoner(
        api_key="sk-or-test", model="z-ai/glm-4.6", transport=httpx.MockTransport(handler)
    )
    n = r.narrate({"episode": {"mode": "insufficient_funds"}, "action": "smart_retry"})
    assert n is not None and "Salary-day" in n.text
    assert n.tokens_prompt == 120 and n.tokens_completion == 40
    assert captured["auth"] == "Bearer sk-or-test"
    body = captured["body"]
    assert body["temperature"] <= 0.3  # low-variance narration
    assert body["max_tokens"] <= 1200  # bounded, with room for reasoning models
    assert body["metadata"]["prompt_version"].startswith("v")


def test_reasoner_degrades_gracefully_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=httpx.Request("POST", "https://x"))

    r = OpenRouterReasoner(api_key="k", model="m", transport=httpx.MockTransport(handler))
    assert r.narrate({"a": 1}) is None  # never raises into the money path


def test_null_reasoner_costs_nothing() -> None:
    assert NullReasoner().narrate({"any": "payload"}) is None


def test_factory_wires_real_or_null_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("RZP_KEY_ID", "rzp_test_k")
    monkeypatch.setenv("RZP_KEY_SECRET", "s")
    monkeypatch.setenv("RZP_WEBHOOK_SECRET", "w")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from paypilot.engine.agent import build_agent_policy
    from paypilot.settings import Settings

    assert isinstance(build_agent_policy(Settings(_env_file=None)).reasoner, NullReasoner)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    assert isinstance(build_agent_policy(Settings(_env_file=None)).reasoner, OpenRouterReasoner)


def test_agent_beats_baseline_on_money() -> None:
    """Integration + THE claim: same socket, same world, agent ≥ 2× baseline ₹.

    Episode COUNT is a vanity metric (agent fights fewer, richer battles on purpose);
    rupees recovered is the merchant truth and the number we publish.
    """
    from paypilot.engine.naive import NaiveRetryPolicy
    from paypilot.engine.runner import RunEngine
    from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
    from paypilot.simulator.population import PopulationSpec, generate_population
    from paypilot.simulator.window import WindowSpec

    pop = generate_population(PopulationSpec(size=300, seed=42))
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    events = generate_failures(pop, FailureGenSpec(window=w, seed=42))

    base = RunEngine(pop, window=w).run(NaiveRetryPolicy(), events)
    agent = RunEngine(pop, window=w).run(AgentPolicy(appetite=STANDARD), events)

    assert agent.compliance_violations == 0
    assert agent.recovered_paise > base.recovered_paise * 2
