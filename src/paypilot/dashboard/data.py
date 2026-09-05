"""Dashboard data pipeline: real runs → one JSON blob the HTML shell renders.

No fake numbers: every figure comes from running both arms on seeded worlds.
The 'focus' world additionally contributes the action mix and per-episode
journaled-decision timelines for the story view.
"""

from typing import Any

from paypilot.engine.runner import RunEngine
from paypilot.eval.multiseed import EVAL_WINDOW, run_multi_seed, scripted_strategist
from paypilot.graph.brain import FakeBrain
from paypilot.graph.policy_adapter import GraphPolicy
from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.voice.node import VoiceNode
from paypilot.voice.script import TemplateScriptWriter

_KIND_LABEL = {
    "action": "action",
    "success": "recovered ✓",
    "fail": "attempt failed",
    "voice": "voice call ✓",
    "give_up": "gave up",
    "abandon_window": "window closed",
    "clamp": "quiet-hours clamp",
}


def build_dashboard_data(
    stability_seeds: list[int],
    stability_size: int = 300,
    focus_seed: int = 42,
) -> dict[str, Any]:
    outcomes = run_multi_seed(seeds=stability_seeds, size=stability_size)
    mults = [
        o.agent_paise / o.baseline_paise if o.baseline_paise > 0 else float("inf") for o in outcomes
    ]
    finite = [m for m in mults if m != float("inf")]
    mean_mult = sum(finite) / len(finite) if finite else float("inf")
    wins = sum(1 for o in outcomes if o.agent_paise > o.baseline_paise)
    agent_share = sum(o.agent_paise / o.at_risk_paise for o in outcomes) / len(outcomes)
    base_share = sum(o.baseline_paise / o.at_risk_paise for o in outcomes) / len(outcomes)

    worlds = sorted(
        (
            {
                "seed": o.seed,
                "baseline_rupees": round(o.baseline_paise / 100),
                "agent_rupees": round(o.agent_paise / 100),
                "multiplier": (
                    round(o.agent_paise / o.baseline_paise, 2) if o.baseline_paise > 0 else "∞"
                ),
                "win": o.agent_paise > o.baseline_paise,
            }
            for o in outcomes
        ),
        key=lambda w: w["seed"],
    )

    # focus world: action mix + episode story timelines from the decision journal
    pop = generate_population(PopulationSpec(size=min(stability_size, 120), seed=focus_seed))
    events = generate_failures(pop, FailureGenSpec(window=EVAL_WINDOW, seed=focus_seed))
    gp = GraphPolicy(brain=FakeBrain(fn=scripted_strategist))
    # reference voice node: deterministic scripts, explicitly opted into (fail-loud)
    voice = VoiceNode(merchant_name="PayPilot", writer=TemplateScriptWriter())
    run = RunEngine(pop, window=EVAL_WINDOW, voice_node=voice).run(gp, events)
    voice_calls = len(voice.calls)

    mix_counter: dict[str, int] = {}
    for e in gp.journal:
        key = e.final_action or "abstain"
        mix_counter[key] = mix_counter.get(key, 0) + 1

    return {
        "headline": {
            "win_rate": f"{wins / len(outcomes):.0%}",
            "mean_multiplier": f"{mean_mult:.2f}×",
            "agent_share": f"{agent_share:.1%}",
            "baseline_share": f"{base_share:.1%}",
            "violations": str(run.compliance_violations),
        },
        "worlds": worlds,
        "focus": {
            "seed": focus_seed,
            "recovered_rupees": round(run.recovered_paise / 100),
            "at_risk_rupees": round(run.at_risk_paise / 100),
            "episodes_recovered": run.recovered_episodes,
            "episodes_total": len({(e.subscription_id, e.episode_no) for e in events}),
            "voice_calls": voice_calls,
            "action_mix": mix_counter,
            "episode_timelines": _focus_timelines(gp, run.timeline),
        },
    }


def _focus_timelines(gp: GraphPolicy, timeline: "tuple[Any, ...]") -> list[dict[str, Any]]:
    """Richest episode stories: journaled decisions + engine outcomes merged."""
    outcome_by_key: dict[str, list[tuple[str, str, bool]]] = {}
    for t in timeline:
        key = f"{t.subscription_id}:{t.episode_no}"
        label = _KIND_LABEL.get(t.kind)
        if label is None:
            continue
        ok = t.kind == "success"
        detail = (t.detail or "")[:48]
        action = detail if t.kind == "action" else label
        outcome_by_key.setdefault(key, []).append((str(t.at)[:16].replace("T", " "), action, ok))

    by_ep: dict[str, list[dict[str, Any]]] = {}
    for j in gp.journal:
        entries = by_ep.setdefault(j.episode_key, [])
        when = str(j.run_at)[:16].replace("T", " ") if j.run_at else ""
        action_text = (
            f"{j.final_action} · {j.reason[:40]}" if j.approved else f"override → {j.final_action}"
        )
        entries.append(
            {
                "time": when,
                "label": "decide",
                "action": action_text,
                "ok": True,
            }
        )

    # merge engine outcomes under each episode's decisions
    merged: dict[str, list[dict[str, Any]]] = {}
    for key, decs in by_ep.items():
        rows = list(decs)
        for when, action, ok in outcome_by_key.get(key, []):
            rows.append({"time": when, "label": "outcome", "action": action, "ok": ok})
        rows.sort(key=lambda r: r["time"])
        merged[key] = rows

    ranked = sorted(merged.items(), key=lambda kv: -len(kv[1]))[:4]
    return [
        {"title": _title(key), "subtitle": f"{len(rows)} tracked events", "entries": rows}
        for key, rows in ranked
    ]


def _title(key: str) -> str:
    sub, ep = key.split(":")
    return f"{sub} · episode {ep}"
