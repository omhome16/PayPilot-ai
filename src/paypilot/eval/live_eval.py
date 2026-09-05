"""Live LLM-brain evaluation — the honest answer to 'is the LLM actually doing anything?'.

Runs the REAL OpenRouter brain through the same GraphPolicy rails on seeded worlds,
journals every decision for replay, and compares it against BOTH the naive baseline
and the scripted doctrine. Requires OPENROUTER_API_KEY; the harness refuses to run
without one rather than faking LLM numbers. Batch evaluation (EVAL_REPORT.md) stays
on the deterministic scripted brain so CI never touches the network.
"""

import statistics
from dataclasses import dataclass
from pathlib import Path

from paypilot.engine.naive import NaiveRetryPolicy
from paypilot.engine.runner import RunEngine
from paypilot.eval.multiseed import EVAL_WINDOW, scripted_strategist
from paypilot.graph.brain import Brain, FakeBrain
from paypilot.graph.llm_brain import OpenRouterBrain
from paypilot.graph.policy_adapter import GraphPolicy
from paypilot.graph.replay import save_journal
from paypilot.settings import Settings, get_settings
from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
from paypilot.simulator.population import PopulationSpec, generate_population


@dataclass(frozen=True)
class LiveWorldOutcome:
    seed: int
    baseline_paise: int
    scripted_paise: int
    llm_paise: int
    llm_consults: int
    llm_overrides: int  # proposals the rails refused / replaced
    llm_failures: int = 0  # fail-loud: brain unavailable → episodes escalated to humans


def build_live_brain(settings: Settings | None = None) -> Brain | None:
    """The OpenRouter brain when a key is configured; None otherwise."""
    s = settings or get_settings()
    key = s.openrouter_api_key
    if key is None:
        return None
    return OpenRouterBrain(api_key=key.get_secret_value(), model=s.openrouter_model)


def run_live_brain_eval(
    seeds: list[int],
    size: int = 100,
    brain: Brain | None = None,
    journal_dir: Path | None = None,
    settings: Settings | None = None,
) -> list[LiveWorldOutcome]:
    """Three arms per world: naive baseline, scripted doctrine, LIVE LLM brain."""
    brain = brain or build_live_brain(settings)
    if brain is None:
        raise RuntimeError(
            "no OPENROUTER_API_KEY configured — the live eval refuses to fake LLM numbers"
        )
    outcomes: list[LiveWorldOutcome] = []
    for seed in seeds:
        pop = generate_population(PopulationSpec(size=size, seed=seed))
        events = generate_failures(pop, FailureGenSpec(window=EVAL_WINDOW, seed=seed))

        base = RunEngine(pop, window=EVAL_WINDOW).run(NaiveRetryPolicy(), events)
        scripted = RunEngine(pop, window=EVAL_WINDOW).run(
            GraphPolicy(brain=FakeBrain(fn=scripted_strategist)), events
        )
        gp = GraphPolicy(brain=brain)
        llm = RunEngine(pop, window=EVAL_WINDOW).run(gp, events)
        if journal_dir is not None:
            save_journal(gp.journal_entries(), journal_dir / f"live-seed-{seed}.json")

        outcomes.append(
            LiveWorldOutcome(
                seed=seed,
                baseline_paise=base.recovered_paise,
                scripted_paise=scripted.recovered_paise,
                llm_paise=llm.recovered_paise,
                llm_consults=len(gp.journal),
                llm_overrides=len(gp.override_log),
                llm_failures=gp.brain_failures,
            )
        )
    return outcomes


def render_live_markdown(outcomes: list[LiveWorldOutcome], tokens_used: int, model: str) -> str:
    lines: list[str] = []
    wins_vs_base = sum(1 for o in outcomes if o.llm_paise > o.baseline_paise)
    wins_vs_scripted = sum(1 for o in outcomes if o.llm_paise > o.scripted_paise)
    mult_vs_base = [
        o.llm_paise / o.baseline_paise if o.baseline_paise > 0 else float("inf") for o in outcomes
    ]
    finite = [m for m in mult_vs_base if m != float("inf")]
    mean_mult = statistics.mean(finite) if finite else float("inf")

    lines.append("# PayPilot Live LLM-Brain Evaluation")
    lines.append("")
    lines.append(
        f"The **real OpenRouter brain** (`{model}`) ran through the same SENSE→THINK→VALIDATE→ACT "
        "graph and guardrails as every other arm — only the brain differed per world. "
        "Every decision is journaled (see `data/runs/live-seed-*.json`) and replayable."
    )
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    lines.append(
        f"- **LLM vs naive baseline:** {wins_vs_base}/{len(outcomes)} worlds won, "
        f"mean {mean_mult:.2f}×"
    )
    lines.append(f"- **LLM vs scripted doctrine:** {wins_vs_scripted}/{len(outcomes)} worlds won")
    lines.append(
        f"- **Rails activity:** {sum(o.llm_overrides for o in outcomes)} proposals overridden "
        f"across {sum(o.llm_consults for o in outcomes)} consultations"
    )
    total_failures = sum(o.llm_failures for o in outcomes)
    if total_failures:
        lines.append(
            f"- **Fail-loud:** ⚠️ {total_failures} consults hit an unavailable brain and "
            "escalated to human review (never silently degraded)"
        )
    lines.append(f"- **LLM cost:** {tokens_used:,} tokens")
    lines.append("")
    lines.append("## Per-world detail")
    lines.append("")
    lines.append(
        "| world | baseline ₹ | scripted doctrine ₹ | live LLM ₹ | LLM consults | overrides |"
    )
    lines.append("|---|---|---|---|---|---|")
    for o in outcomes:
        lines.append(
            f"| {o.seed} | {o.baseline_paise / 100:,.0f} | {o.scripted_paise / 100:,.0f} "
            f"| {o.llm_paise / 100:,.0f} | {o.llm_consults} | {o.llm_overrides} |"
        )
    lines.append("")
    lines.append(
        "_Live runs are non-deterministic by nature (network LLM); the journaled decisions "
        "replay byte-identically. Small worlds keep the cost bounded._"
    )
    return "\n".join(lines)


def main() -> None:
    """CLI (``paypilot-live-eval``): [seeds...] optional; defaults to 1 2 3."""
    import sys

    settings = get_settings()
    brain = build_live_brain(settings)
    if brain is None:
        print("OPENROUTER_API_KEY is not set — add it to .env to measure the live LLM brain.")
        raise SystemExit(1)
    seeds = [int(a) for a in sys.argv[1:]] or [1, 2, 3]
    journal_dir = Path("data/runs")
    outcomes = run_live_brain_eval(seeds=seeds, size=100, brain=brain, journal_dir=journal_dir)
    tokens = brain.tokens_used if isinstance(brain, OpenRouterBrain) else 0
    text = render_live_markdown(outcomes, tokens, settings.openrouter_model)
    out_path = Path("LIVE_EVAL.md")
    out_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n{out_path} written; journals in {journal_dir}/")


if __name__ == "__main__":
    main()
