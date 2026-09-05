"""SQLite store: the PayPilot memory — schema, seeding, queries, audit rows.

Design goals:
- **Determinism**: reads are plain SQL over seeded rows; seeding a Population is a
  single transaction, so the same seed always yields the same store. The measured
  eval pipeline never touches the network through here.
- **Audit**: decisions, voice calls and tool calls are append-only rows with UTC
  timestamps — the dashboard's Memory/DB panel and the audit ledger both read this.
- **Thread safety**: the FastAPI demo serves many panels that poll concurrently, so
  EVERY sqlite interaction is serialized behind one RLock (a single sqlite3
  connection must never be used by two threads at once).
- **Idempotent seeding**: INSERT OR REPLACE keyed on natural ids, so re-seeding a
  world (or re-running a demo) never duplicates rows.

Money stays INTEGER paise everywhere, matching the domain boundary rule.
"""

import datetime as dt
import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from paypilot.domain.models import Customer, CustomerProfile, Mandate, Subscription
from paypilot.simulator.population import Population

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    contact      TEXT NOT NULL,
    vertical     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id           TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    plan_name    TEXT NOT NULL,
    amount_paise INTEGER NOT NULL,
    billing_day  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS mandates (
    id              TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    rail            TEXT NOT NULL,
    status          TEXT NOT NULL,
    max_amount_paise INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS customer_profiles (
    customer_id   TEXT PRIMARY KEY REFERENCES customers(customer_id),
    tenure_cycles INTEGER NOT NULL,
    paid_on_time  INTEGER NOT NULL,
    missed_cycles INTEGER NOT NULL,
    reliability   REAL NOT NULL,
    link_affinity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS consent (
    customer_id TEXT PRIMARY KEY REFERENCES customers(customer_id),
    do_not_call INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS failure_episodes (
    episode_key    TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    episode_no     INTEGER NOT NULL,
    mode           TEXT NOT NULL,
    amount_paise   INTEGER NOT NULL,
    occurred_at    TEXT NOT NULL,
    attempts_made  INTEGER NOT NULL DEFAULT 1,
    status         TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    episode_key TEXT NOT NULL,
    mode        TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    chosen      TEXT,
    run_at      TEXT,
    reason      TEXT NOT NULL,
    degraded    TEXT
);
CREATE TABLE IF NOT EXISTS voice_calls (
    episode_key   TEXT PRIMARY KEY,
    ts            TEXT NOT NULL,
    merchant_name TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    script        TEXT NOT NULL,
    source        TEXT NOT NULL,
    audio_path    TEXT,
    outcome       TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    episode_key TEXT,
    tool_name  TEXT NOT NULL,
    args       TEXT NOT NULL,
    result     TEXT NOT NULL,
    latency_ms REAL NOT NULL
);
"""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class Store:
    """Thread-safe repository over SQLite. All operations serialize on one RLock."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @classmethod
    def in_memory(cls) -> "Store":
        return cls(":memory:")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- generic access (dashboard / DB browser) --------------------------------

    def tables(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return sorted(str(r["name"]) for r in rows if r["name"])

    def rows(self, table: str, limit: int = 200) -> list[dict[str, Any]]:
        if table not in self.tables():
            return []
        with self._lock:
            out = self._conn.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid DESC LIMIT ?',  # noqa: S608 — table is whitelisted against sqlite_master
                (limit,),
            )
            result = [dict(r) for r in out.fetchall()]
        return result

    # -- seeding ----------------------------------------------------------------

    def seed_population(self, population: Population) -> None:
        """Insert a whole Population (idempotent). Deterministic for a given seed."""
        with self._lock, self._conn:
            for c in population.customers:
                self._conn.execute(
                    "INSERT OR REPLACE INTO customers VALUES (?,?,?,?)",
                    (c.id, c.name, c.contact, c.vertical),
                )
            for s in population.subscriptions:
                self._conn.execute(
                    "INSERT OR REPLACE INTO subscriptions VALUES (?,?,?,?,?)",
                    (s.id, s.customer_id, s.plan_name, s.amount_paise, s.billing_day),
                )
            for m in population.mandates:
                self._conn.execute(
                    "INSERT OR REPLACE INTO mandates VALUES (?,?,?,?,?)",
                    (m.id, m.customer_id, m.rail.value, m.status.value, m.max_amount_paise),
                )
            for p in population.profiles:
                self._conn.execute(
                    "INSERT OR REPLACE INTO customer_profiles VALUES (?,?,?,?,?,?)",
                    (
                        p.customer_id,
                        p.tenure_cycles,
                        p.paid_on_time,
                        p.missed_cycles,
                        round(p.reliability, 6),
                        round(p.link_affinity, 6),
                    ),
                )

    def clear(self) -> None:
        with self._lock, self._conn:
            for t in self.tables():  # names come from sqlite_master, not user input
                self._conn.execute(f'DELETE FROM "{t}"')  # noqa: S608

    # -- reads the recovery brain queries ----------------------------------------

    def customer_by_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT c.*, s.id AS subscription_id, s.plan_name, s.amount_paise, s.billing_day "
                "FROM subscriptions s JOIN customers c ON c.customer_id = s.customer_id "
                "WHERE s.id = ?",
                (subscription_id,),
            ).fetchone()
        return dict(row) if row else None

    def profile(self, customer_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM customer_profiles WHERE customer_id = ?", (customer_id,)
            ).fetchone()
        return dict(row) if row else None

    def is_do_not_call(self, customer_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT do_not_call FROM consent WHERE customer_id = ?", (customer_id,)
            ).fetchone()
        return bool(row and row["do_not_call"])

    def mark_do_not_call(self, customer_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO consent (customer_id, do_not_call) VALUES (?, 1) "
                "ON CONFLICT(customer_id) DO UPDATE SET do_not_call = 1",
                (customer_id,),
            )

    def episodes_for_subscription(self, subscription_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM failure_episodes WHERE subscription_id = ? ORDER BY episode_no",
                (subscription_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_episode(self, episode: dict[str, Any]) -> None:
        """Persist/refresh one failure episode. ``episode`` carries the natural key."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO failure_episodes "
                "(episode_key, subscription_id, episode_no, mode, amount_paise, occurred_at, "
                " attempts_made, status) VALUES (?,?,?,?,?,?,?,?)",
                (
                    episode["episode_key"],
                    episode["subscription_id"],
                    episode["episode_no"],
                    episode["mode"],
                    episode["amount_paise"],
                    episode["occurred_at"],
                    episode.get("attempts_made", 1),
                    episode.get("status", "open"),
                ),
            )

    # -- append-only audit ---------------------------------------------------------

    def record_decision(
        self,
        *,
        episode_key: str,
        mode: str,
        attempts: int,
        chosen: str | None,
        reason: str,
        run_at: str | None = None,
        degraded: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO decisions "
                "(ts, episode_key, mode, attempts, chosen, run_at, reason, degraded) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (_now(), episode_key, mode, attempts, chosen, run_at, reason, degraded),
            )

    def decisions_for_subscription(
        self, subscription_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decisions WHERE episode_key LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (f"{subscription_id}:%", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_voice_call(
        self,
        *,
        episode_key: str,
        merchant_name: str,
        customer_name: str,
        script: str,
        source: str,
        audio_path: str | None = None,
        outcome: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO voice_calls "
                "(episode_key, ts, merchant_name, customer_name, script, source, "
                "audio_path, outcome) VALUES (?,?,?,?,?,?,?,?)",
                (
                    episode_key,
                    _now(),
                    merchant_name,
                    customer_name,
                    script,
                    source,
                    audio_path,
                    outcome,
                ),
            )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        latency_ms: float,
        episode_key: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tool_calls (ts, episode_key, tool_name, args, result, latency_ms) "
                "VALUES (?,?,?,?,?,?)",
                (
                    _now(),
                    episode_key,
                    tool_name,
                    json.dumps(args, default=str),
                    json.dumps(result, default=str),
                    round(latency_ms, 3),
                ),
            )

    # -- read iterators for streaming exports ---------------------------------------

    def iter_decisions(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            rows = list(
                self._conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
            )
        for r in rows:
            yield dict(r)

    def iter_episodes(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            rows = list(
                self._conn.execute(
                    "SELECT * FROM failure_episodes ORDER BY subscription_id, episode_no"
                ).fetchall()
            )
        for r in rows:
            yield dict(r)

    # convenience: python-typed seeding helpers used by callers holding domain objects
    def seed_domain_rows(
        self,
        customers: Sequence[Customer],
        subscriptions: Sequence[Subscription],
        mandates: Sequence[Mandate],
        profiles: Sequence[CustomerProfile],
    ) -> None:
        """Seed from individual domain-model collections (tests / partial worlds)."""
        self.seed_population(
            Population(
                customers=tuple(customers),
                subscriptions=tuple(subscriptions),
                mandates=tuple(mandates),
                profiles=tuple(profiles),
            )
        )
