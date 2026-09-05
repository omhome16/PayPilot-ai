"""Persistence + retrieval layer (SQLite): the agent's memory is a real database.

Everything the recovery agent knows or does can land here — customers,
subscriptions, failure episodes, decisions (the ledger), voice calls, tool-call
audit trails and consent state. A repository interface keeps SQLite the default
and Postgres an option behind the same methods.

Why SQLite? Zero infra, file-backed, transactional, deterministic reads, in the
stdlib — right for a buildathon demo AND honest enough to be the real store a
deployment would start from.
"""

from paypilot.store.db import Store
from paypilot.store.tools import RecoveryTools, ToolResult

__all__ = ["RecoveryTools", "Store", "ToolResult"]
