"""Record-once-replay-forever (D30): capture the brain's real decisions once,
then replay them deterministically — headline numbers stay crisp AND honest."""

import tempfile
from datetime import date
from pathlib import Path

from paypilot.domain.enums import Intervention
from paypilot.engine.naive import NaiveRetryPolicy
from paypilot.engine.runner import RunEngine
from paypilot.graph.brain import BrainProposal, FakeBrain
from paypilot.graph.policy_adapter import GraphPolicy
from paypilot.graph.replay import ReplayBrain, save_journal
from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.simulator.window import WindowSpec


def _world():
    pop = generate_population(PopulationSpec(size=300, seed=42))
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    events = generate_failures(pop, FailureGenSpec(window=w, seed=42))
    return pop, w, events


def _smart(state):
    n = state["attempts"]
    if n <= 1:
        return BrainProposal(action=Intervention.WAIT_SELF_HEAL, on_salary_day=True)
    if state["amount_rupees"] >= 1000:
        return BrainProposal(action=Intervention.VOICE_NUDGE, days_ahead=2)
    return BrainProposal(action=Intervention.PAYMENT_LINK, days_ahead=1)


def test_journal_records_proposals_with_sequence_numbers() -> None:
    gp = GraphPolicy(brain=FakeBrain(fn=_smart))
    pop, w, events = _world()
    RunEngine(pop, window=w).run(gp, events)
    assert gp.journal_entries(), "expected journaled decisions"
    e0 = gp.journal_entries()[0]
    assert e0["seq"] == 1 and e0["episode_key"].startswith("sub_")
    assert e0["proposal"]["action"] in {"wait_self_heal", "voice_nudge", "payment_link"}


def test_save_and_replay_reproduces_identical_runresult() -> None:
    pop, w, events = _world()

    original = GraphPolicy(brain=FakeBrain(fn=_smart))
    r_original = RunEngine(pop, window=w).run(original, events)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "journal.json"
        save_journal(original.journal_entries(), path)

        replayer = GraphPolicy(brain=ReplayBrain.from_file(path))
        r_replay = RunEngine(pop, window=w).run(replayer, events)

    # THE guarantee: replay is byte-identical to the recorded run
    assert r_replay.recovered_paise == r_original.recovered_paise
    assert r_replay.recovered_episodes == r_original.recovered_episodes
    assert r_replay.attempts_executed == r_original.attempts_executed
    assert r_replay.timeline == r_original.timeline


def test_replay_brain_is_safe_when_journal_is_incomplete() -> None:
    """Missing entries degrade to a lawful default instead of crashing."""
    rb = ReplayBrain(entries={})
    p = rb.propose({"episode_key": "sub_9999:1", "consult_seq": 1})
    assert p.action is Intervention.SMART_RETRY  # lawful-for-ISF conservative default


def test_replayed_agent_still_beats_baseline() -> None:
    """Even the replay (not just the live recording) beats the naive arm."""
    pop, w, events = _world()
    base = RunEngine(pop, window=w).run(NaiveRetryPolicy(), events)

    original = GraphPolicy(brain=FakeBrain(fn=_smart))
    RunEngine(pop, window=w).run(original, events)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "journal.json"
        save_journal(original.journal_entries(), path)
        replay_run = RunEngine(pop, window=w).run(
            GraphPolicy(brain=ReplayBrain.from_file(path)), events
        )
    assert replay_run.recovered_paise > base.recovered_paise
