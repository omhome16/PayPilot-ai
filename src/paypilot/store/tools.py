"""Agent tool layer: the recovery brain RETRIEVES context through typed tools.

Two honest flavors use these same tools:
- The LLM brain (live) chooses adaptively which tool to call, mid-reasoning.
- A deterministic caller (reference/demo) calls the tools its rules require.

Every invocation is timed and appended to the ``tool_calls`` audit table — the
dashboard's Memory/DB panel streams these as visible "query events", so the demo
proves the agent really pulls context before it decides.
"""

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any, cast

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.store.db import Store


@dataclass(frozen=True)
class ToolResult:
    """One tool invocation: what was asked, what came back, how long it took."""

    name: str
    args: dict[str, Any]
    data: dict[str, Any]
    latency_ms: float
    rows: int = 0


def _jsonable(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "value"):
        return v.value
    return v


class RecoveryTools:
    """Executors over one Store, self-describing for LLM function calling."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.calls: list[ToolResult] = []

    # -- tool definitions (also served to the LLM as a function-calling schema) ------

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "lookup_customer",
                "description": (
                    "Return the subscription's customer: plan, amount, billing day, "
                    "vertical and the customer's payment-history profile (tenure, "
                    "on-time ratio, missed cycles, link affinity)."
                ),
                "parameters": {"subscription_id": "str"},
            },
            {
                "name": "episode_history",
                "description": (
                    "Return this subscription's past failure episodes with their "
                    "outcomes (mode, amount, attempts, status)."
                ),
                "parameters": {"subscription_id": "str"},
            },
            {
                "name": "recent_decisions",
                "description": (
                    "Return the last recovery decisions already taken for this "
                    "subscription (what the agent tried and why) so the next move "
                    "is not a repeat."
                ),
                "parameters": {"subscription_id": "str", "limit": "int"},
            },
            {
                "name": "consent_status",
                "description": (
                    "Return the customer's consent state (do-not-call). NEVER "
                    "schedule a call for a do-not-call customer."
                ),
                "parameters": {"subscription_id": "str"},
            },
            {
                "name": "next_payday",
                "description": (
                    "Return the customer's next salary date for an ISO date "
                    "(Indian pay calendar), used to time retries after payday."
                ),
                "parameters": {"from_date": "ISO date"},
            },
        ]

    # -- executors -------------------------------------------------------------------

    def lookup_customer(self, subscription_id: str) -> ToolResult:
        return self._run(
            "lookup_customer",
            {"subscription_id": subscription_id},
            lambda: self._lookup_customer(subscription_id),
        )

    def episode_history(self, subscription_id: str) -> ToolResult:
        return self._run(
            "episode_history",
            {"subscription_id": subscription_id},
            lambda: {
                "episodes": self.store.episodes_for_subscription(subscription_id),
            },
        )

    def recent_decisions(self, subscription_id: str, limit: int = 5) -> ToolResult:
        return self._run(
            "recent_decisions",
            {"subscription_id": subscription_id, "limit": limit},
            lambda: {"decisions": self.store.decisions_for_subscription(subscription_id, limit)},
        )

    def consent_status(self, subscription_id: str) -> ToolResult:
        cust = self.store.customer_by_subscription(subscription_id)
        return self._run(
            "consent_status",
            {"subscription_id": subscription_id},
            lambda: {
                "do_not_call": bool(cust and self.store.is_do_not_call(cust["customer_id"])),
            },
        )

    def next_payday(self, from_date: str | dt.date) -> ToolResult:
        def _exec() -> dict[str, Any]:
            d = from_date if isinstance(from_date, dt.date) else dt.date.fromisoformat(from_date)
            cal = IndianPaymentCalendar.with_default_festivals(year=d.year)
            return {"next_salary_date": _jsonable(cal.next_salary_date(d)), "from_date": str(d)}

        return self._run("next_payday", {"from_date": str(from_date)}, _exec)

    def call(self, name: str, **kwargs: Any) -> ToolResult:
        fn = getattr(self, name, None)
        if fn is None or name.startswith("_"):
            raise KeyError(f"unknown tool: {name}")
        return cast(ToolResult, fn(**kwargs))

    # -- internals ---------------------------------------------------------------------

    def _lookup_customer(self, subscription_id: str) -> dict[str, Any]:
        c = self.store.customer_by_subscription(subscription_id)
        if c is None:
            return {"found": False, "subscription_id": subscription_id}
        profile = self.store.profile(c["customer_id"])
        return {
            "found": True,
            "customer_id": c["customer_id"],
            "name": c["name"],
            "vertical": c["vertical"],
            "plan_name": c["plan_name"],
            "amount_paise": c["amount_paise"],
            "billing_day": c["billing_day"],
            "profile": profile,
        }

    def _run(
        self,
        name: str,
        args: dict[str, Any],
        exec_fn: Any,
    ) -> ToolResult:
        t0 = time.perf_counter()
        data = exec_fn()
        latency = (time.perf_counter() - t0) * 1000.0
        rows = _count_rows(data)
        result = ToolResult(name=name, args=args, data=data, latency_ms=latency, rows=rows)
        self.calls.append(result)
        self.store.record_tool_call(tool_name=name, args=args, result=data, latency_ms=latency)
        return result


def _count_rows(data: Any) -> int:
    """Best-effort row count for the audit trail (dashboard shows 'rows returned')."""
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return len(v)
            if isinstance(v, dict) and isinstance(v.get("profile"), dict):
                return 2
    return 1
