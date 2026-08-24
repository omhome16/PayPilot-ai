"""Record-once-replay-forever (D30).

save_journal(): persist a GraphPolicy's decisions to JSON.
ReplayBrain:    re-serves those recorded decisions as proposals, in per-episode
                consult order — so the engine reruns byte-identically without
                touching the LLM. Missing entries degrade to a lawful default
                (never crash the money path).
"""

import json
from pathlib import Path
from typing import Any

from paypilot.domain.enums import Intervention
from paypilot.graph.brain import BrainProposal

_LAWFUL_DEFAULT = BrainProposal(
    action=Intervention.SMART_RETRY,
    on_salary_day=True,
    reason="replay: no recorded decision — conservative default",
)


_LAWFUL_ABSTAIN = BrainProposal(
    action=Intervention.SMART_RETRY,  # never used: abstain short-circuits first
    reason="replay abstention",
)


def _abstain() -> BrainProposal:
    p = BrainProposal(action=Intervention.SMART_RETRY, reason="replay: recorded abstention")
    object.__setattr__(p, "abstain", True)
    return p


class ReplayBrain:
    """Serves previously-recorded decisions keyed by episode + consult sequence."""

    def __init__(self, entries: dict[str, list[dict[str, Any]]]) -> None:
        # key: "sub_x:n" → ordered list of records
        self._entries = entries
        self._cursor: dict[str, int] = {}

    @classmethod
    def from_file(cls, path: Path) -> "ReplayBrain":
        data = json.loads(path.read_text(encoding="utf-8"))
        indexed: dict[str, list[dict[str, Any]]] = {}
        for rec in sorted(data.get("decisions", []), key=lambda r: r["seq"]):
            indexed.setdefault(rec["episode_key"], []).append(rec)
        return cls(entries=indexed)

    def propose(self, state: dict[str, Any]) -> BrainProposal:
        key = str(state.get("episode_key", ""))
        seq = int(state.get("consult_seq", 1))
        records = self._entries.get(key)
        if not records or seq > len(records):
            return _LAWFUL_DEFAULT  # graceful degradation, never a crash
        rec = records[min(seq, len(records)) - 1]
        prop = rec["proposal"]
        if prop.get("abstain", False) or rec.get("final_action") is None:
            return _abstain()  # replay the recorded 'do nothing' exactly
        return BrainProposal(
            action=Intervention(rec["final_action"]),
            on_salary_day=bool(prop.get("on_salary_day", False)),
            days_ahead=int(prop.get("days_ahead", 2)),
            reason=f"replay: {rec['reason']}",
        )


def save_journal(entries: list[dict[str, Any]], path: Path) -> None:
    """Persist journal entries with global sequence numbers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "decisions": entries}, indent=2),
        encoding="utf-8",
    )
