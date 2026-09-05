"""Two-way voice: LLM-driven conversation with real context, safety, consent, outcomes."""

import json

import httpx

from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.store import RecoveryTools, Store
from paypilot.voice.conversation import (
    Conversation,
    OpenRouterDialogueBrain,
    classify_intent,
    validate_reply,
)

_MERCHANT = "FitZone"


def _world(sub_id: str = "sub_0001", size: int = 10) -> tuple[RecoveryTools, Store, dict]:
    store = Store.in_memory()
    store.seed_population(generate_population(PopulationSpec(size=size, seed=42)))
    tools = RecoveryTools(store)
    row = store.customer_by_subscription(sub_id)
    assert row is not None
    ctx = {
        "subscription_id": sub_id,
        "mode": "insufficient_funds",
        "amount_paise": 99_900,
        "attempts_made": 2,
        "billing_day": row["billing_day"],
        "plan_name": row["plan_name"],
        "episode_no": 1,
    }
    return tools, store, ctx


def _brain(handler) -> OpenRouterDialogueBrain:
    return OpenRouterDialogueBrain(api_key="k", model="m", transport=httpx.MockTransport(handler))


def _reply_json(
    reply: str, outcome: str = "none", done: bool = False, note: str = ""
) -> httpx.Response:
    content = json.dumps({"reply": reply, "outcome": outcome, "note": note, "done": done})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {}})


def test_intent_classifier_tags_customer_replies() -> None:
    assert classify_intent("main abhi pay kar deta hoon") == "pay_now"
    assert classify_intent("salary aane ke baad kar dunga") == "will_pay_later"
    assert classify_intent("mujhe ye subscription nahi chahiye, band kar do") == "cancel"
    assert classify_intent("link nahi mila") == "need_help"
    assert classify_intent("kuch bhi") == "unclear"


def test_reply_validator_blocks_threats_and_overlong() -> None:
    assert validate_reply("Namaste ji, sab theek hai?") == []
    assert validate_reply("Pay now or police case!") != []
    long = "Kripya jald pay kijiye. " * 20
    assert validate_reply(long) != []


def test_open_retrieves_context_through_tools_then_speaks() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _reply_json(
            "Namaste! FitZone se bol raha hoon, aapka ₹999 ka payment pending hai.",
            done=False,
        )

    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=_brain(handler),
        tools=tools,
    )
    turn = conv.open()
    assert turn.source == "llm" and turn.done is False and not turn.violations
    # the tools really ran against the store and were audited
    assert calls["n"] == 1
    audit = store.rows("tool_calls")
    names = {a["tool_name"] for a in audit}
    assert names == {"lookup_customer", "episode_history", "recent_decisions", "consent_status"}
    assert len(conv.tool_events) == 4


def test_customer_paying_now_yields_payment_link_outcome() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:  # the opening line never resolves the call
            return _reply_json("Namaste! FitZone se bol raha hoon.", done=False)
        return _reply_json(
            "Bilkul! Yeh raha link: https://rzp.io/rzp/abc",
            outcome="pay_now",
            done=True,
            note="customer ready to pay",
        )

    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=_brain(handler),
        tools=tools,
    )
    conv.open()
    turn = conv.respond("haan main abhi pay kar deta hoon")
    assert turn.outcome is not None and turn.outcome.kind == "pay_now"
    assert turn.outcome.intervention == "payment_link"
    assert turn.done is True
    # the outcome became a real ledger decision
    decisions = store.rows("decisions")
    assert any(d["chosen"] == "payment_link" for d in decisions)


def test_will_pay_later_schedules_salary_timed_retry() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply_json("Namaste! Kya madad kar sakta hoon?", done=False)
        return _reply_json(
            "Bilkul, koi jaldi nahi. Salary aane ke baad pay kar dena.",
            outcome="pay_later",
            done=True,
            note="customer said pay after salary",
        )

    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=_brain(handler),
        tools=tools,
    )
    conv.open()
    turn = conv.respond("main salary aane ke baad pay kar dunga")
    assert turn.outcome is not None and turn.outcome.intervention == "smart_retry"
    assert any(d["chosen"] == "smart_retry" for d in store.rows("decisions"))


def test_cancel_honours_consent_mid_call() -> None:
    tools, store, ctx = _world()
    cust = store.customer_by_subscription("sub_0001")
    assert store.is_do_not_call(cust["customer_id"]) is False

    def handler(req: httpx.Request) -> httpx.Response:
        return _reply_json("Namaste! Main aapki kaise madad kar sakti hoon?", done=False)

    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=_brain(handler),
        tools=tools,
    )
    conv.open()
    # consent is honoured MID-CALL — before any further brain turn
    turn = conv.respond("mujhe ye subscription nahi chahiye, band kar do")
    assert turn.done is True
    assert turn.outcome is not None and turn.outcome.kind == "cancel"
    assert store.is_do_not_call(cust["customer_id"]) is True  # registry updated in the store
    assert conv.degraded is False  # a clean, human-respecting close


def test_fail_loud_without_dialogue_brain() -> None:
    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=None,
        tools=tools,
    )
    turn = conv.open()
    assert turn.degraded is True and turn.done is True
    assert turn.outcome is not None and turn.outcome.kind == "human"
    assert turn.source == "system" and turn.text == ""
    assert "no LLM dialogue brain" in turn.note


def test_fail_loud_when_brain_network_fails() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("provider down")

    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=_brain(handler),
        tools=tools,
    )
    turn = conv.open()
    assert turn.degraded is True and turn.outcome is not None
    assert turn.outcome.kind == "human"
    assert "brain unavailable" in turn.note


def test_fail_loud_when_brain_output_is_unsafe() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _reply_json("Pay Rs 999 right now or the police will come.")

    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=_brain(handler),
        tools=tools,
    )
    turn = conv.open()
    assert turn.degraded is True and turn.done is True
    assert turn.violations  # the unsafe text never reached the transcript as agent speech
    assert all(t["role"] != "agent" or not t["text"] for t in conv.transcript)


def test_turn_budget_closes_with_human_followup() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _reply_json("Achha, samajh gaya. Aur kuch?", done=False)

    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=_brain(handler),
        tools=tools,
        max_customer_turns=3,
    )
    conv.open()
    last = None
    for _ in range(5):
        last = conv.respond("theek hai")
        if conv.done:
            break
    assert conv.done is True
    assert last is not None and last.outcome is not None
    assert last.outcome.kind == "human"  # human ops follows up — the call never dangles
    assert sum(1 for t in conv.transcript if t["role"] == "customer") <= 3


def test_respond_after_close_is_refused() -> None:
    tools, store, ctx = _world()
    conv = Conversation(
        merchant_name=_MERCHANT,
        customer_name="Priya",
        episode_key="sub_0001:1",
        context=ctx,
        payment_url="https://rzp.io/rzp/abc",
        brain=None,
        tools=tools,
    )
    conv.open()
    turn = conv.respond("hello?")
    assert turn.done is True
