"""Phase 2 contracts: any policy plugs into the same engine; baseline stays fair."""

from datetime import UTC, date, datetime, timedelta

from paypilot.domain.enums import FailureMode, Intervention, MandateRail
from paypilot.engine.naive import NaiveRetryPolicy
from paypilot.engine.policy import EpisodeView, ProposedAction
from paypilot.engine.runner import RunEngine, RunResult
from paypilot.simulator.failure_gen import FailureGenSpec, generate_failures
from paypilot.simulator.population import PopulationSpec, generate_population
from paypilot.simulator.window import WindowSpec


def _september_events():
    pop = generate_population(PopulationSpec(size=300, seed=42))
    return pop, generate_failures(
        pop,
        FailureGenSpec(window=WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30)), seed=42),
    )


def _view(mode: FailureMode, attempts: int = 0) -> EpisodeView:
    return EpisodeView(
        subscription_id="sub_0001",
        episode_no=1,
        mode=mode,
        amount_paise=19900,
        first_failed_at=datetime(2026, 9, 27, 4, 30, tzinfo=UTC),
        attempts_made=attempts,
        rail=MandateRail.UPI_AUTOPAY,
        billing_day=27,
        vertical="ott",
    )


# --- The baseline policy ---------------------------------------------------------


def test_baseline_gives_up_on_consent_broken_modes() -> None:
    """Revoked/limit episodes: even a naive merchant can't fix these by retrying."""
    p = NaiveRetryPolicy()
    assert p.next_action(_view(FailureMode.MANDATE_REVOKED)) is None
    assert p.next_action(_view(FailureMode.LIMIT_EXCEEDED)) is None


def test_baseline_retries_insufficient_funds_without_timing() -> None:
    """The naive move: 'try again in a couple of days' — NO salary awareness."""
    a = NaiveRetryPolicy().next_action(_view(FailureMode.INSUFFICIENT_FUNDS))
    assert a is not None
    assert a.intervention is Intervention.SMART_RETRY  # untimed ⇒ blind discount applies
    assert (a.run_at - _view(FailureMode.INSUFFICIENT_FUNDS).first_failed_at).days == 2


def test_baseline_retries_transient_modes_with_plain_retry() -> None:
    p = NaiveRetryPolicy()
    for mode in (FailureMode.AUTH_TIMEOUT, FailureMode.BANK_DOWNTIME):
        a = p.next_action(_view(mode))
        assert a is not None and a.intervention is Intervention.RETRY


def test_baseline_gives_up_after_three_attempts() -> None:
    p = NaiveRetryPolicy()
    assert p.next_action(_view(FailureMode.AUTH_TIMEOUT, attempts=3)) is None


def test_baseline_is_deterministic_and_compliant_on_every_mode() -> None:
    p = NaiveRetryPolicy()
    for mode in FailureMode:
        for _ in range(5):
            a = p.next_action(_view(mode))
            if a is not None:
                assert a.intervention in mode.permitted_interventions
                assert a.run_at.tzinfo is not None


# --- The engine -------------------------------------------------------------------


def test_engine_runs_the_whole_september_world() -> None:
    pop, events = _september_events()
    engine = RunEngine(pop, window=WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30)))
    result = engine.run(NaiveRetryPolicy(), events)

    assert isinstance(result, RunResult)
    n_episodes = len({(e.subscription_id, e.episode_no) for e in events})
    assert result.episodes == n_episodes == 45
    assert result.recovered_episodes >= 1
    assert 0 < result.recovered_paise <= result.at_risk_paise
    assert result.compliance_violations == 0  # THE headline invariant for the baseline
    assert len(result.timeline) >= result.attempts_executed


def test_engine_is_deterministic() -> None:
    pop, events = _september_events()
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    r1 = RunEngine(pop, window=w, seed=42).run(NaiveRetryPolicy(), events)
    r2 = RunEngine(pop, window=w, seed=42).run(NaiveRetryPolicy(), events)
    assert r1 == r2


def test_engine_recovery_rate_in_plausible_band() -> None:
    """Fair-naive must land near the published 20–35% band — not 5%, not 80%."""
    pop, events = _september_events()
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    r = RunEngine(pop, window=w).run(NaiveRetryPolicy(), events)
    rate = r.recovered_episodes / r.episodes
    assert 0.10 <= rate <= 0.50, f"baseline recovered {rate:.0%} — calibration drifted!"


def test_engine_clamps_illegal_hour_proposals() -> None:
    """Gate check: a policy proposing 02:00 IST gets clamped into the legal window."""

    class _OnceThenStop:
        """Proposes one illegal-hour retry on the first RETRYABLE episode, then stops."""

        name = "midnight"

        def __init__(self) -> None:
            self.calls = 0

        def next_action(self, ep: EpisodeView) -> ProposedAction | None:
            # only propose where retry is legal — otherwise we'd test violations, not clamps
            if ep.mode not in (
                FailureMode.INSUFFICIENT_FUNDS,
                FailureMode.AUTH_TIMEOUT,
                FailureMode.BANK_DOWNTIME,
            ):
                return None
            self.calls += 1
            if self.calls > 1:
                return None
            return ProposedAction(
                intervention=Intervention.RETRY,
                run_at=datetime(2026, 9, 28, 21, 0, tzinfo=UTC),  # 02:30 IST!
            )

    pop, events = _september_events()
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    result = RunEngine(pop, window=w).run(_OnceThenStop(), events)
    clamps = [t for t in result.timeline if t.kind == "clamp"]
    assert clamps, "expected at least one quiet-hours clamp"


def test_engine_counts_compliance_violations() -> None:
    """A policy proposing an illegal move earns ONE violation entry, the episode
    closes (no retry loop), and the metric actually counts it — the headline
    'zero violations' claim is measured, not hardwired."""

    class _RetryEverything:
        """RETRY everywhere: legal for transient modes, ILLEGAL for the rest."""

        name = "retry-everything"

        def next_action(self, ep: EpisodeView) -> ProposedAction | None:
            return ProposedAction(
                intervention=Intervention.RETRY,
                run_at=ep.first_failed_at + timedelta(days=1),
            )

    pop, events = _september_events()
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    result = RunEngine(pop, window=w).run(_RetryEverything(), events)

    illegal_modes = {
        FailureMode.INSUFFICIENT_FUNDS,
        FailureMode.MANDATE_REVOKED,
        FailureMode.LIMIT_EXCEEDED,
    }
    expected = sum(1 for e in events if e.mode in illegal_modes)
    assert result.compliance_violations == expected >= 1
    assert sum(1 for t in result.timeline if t.kind == "violation") == expected


def test_engine_executes_voice_decisions_as_safe_call_artifacts() -> None:
    """A VOICE_NUDGE decision becomes a real VoiceCall artifact + a 'voice' timeline
    entry; runs stay byte-identical (artifact timestamps live outside RunResult)."""

    class _VoiceForFunds:
        """High-value insufficient-funds episodes get the personal channel."""

        name = "voice-funds"

        def next_action(self, ep: EpisodeView) -> ProposedAction | None:
            if ep.mode is FailureMode.INSUFFICIENT_FUNDS and ep.amount_paise >= 100_000:
                return ProposedAction(
                    intervention=Intervention.VOICE_NUDGE,
                    run_at=ep.first_failed_at + timedelta(days=1),
                )
            return None

    from paypilot.voice.node import VoiceNode
    from paypilot.voice.script import TemplateScriptWriter

    pop, events = _september_events()
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    # reference node: deterministic scripts, explicitly opted into (fail-loud)
    voice = VoiceNode(merchant_name="PayPilot", writer=TemplateScriptWriter())
    result = RunEngine(pop, window=w, voice_node=voice).run(_VoiceForFunds(), events)

    voice_decisions = sum(
        1 for t in result.timeline if t.kind == "action" and "voice_nudge" in t.detail
    )
    voice_calls_made = len(voice.calls)
    assert voice_calls_made >= 1
    assert len([t for t in result.timeline if t.kind == "voice"]) == voice_calls_made
    assert voice_decisions >= 1
    artifact = voice.calls[0]
    assert artifact.source == "template"
    assert "rok denge" in artifact.script_hinglish or "pause" in artifact.script_hinglish

    # determinism: identical seed → identical RunResult (voice timeline included)
    r1 = RunEngine(pop, window=w, seed=7).run(_VoiceForFunds(), events)
    r2 = RunEngine(pop, window=w, seed=7).run(_VoiceForFunds(), events)
    assert r1 == r2


def test_engine_honours_do_not_call_registry() -> None:
    """A customer who opted out gets a voice_skip entry, never a call artifact."""

    class _VoiceForFunds:
        name = "voice-funds"

        def next_action(self, ep: EpisodeView) -> ProposedAction | None:
            if ep.mode is FailureMode.INSUFFICIENT_FUNDS and ep.amount_paise >= 100_000:
                return ProposedAction(
                    intervention=Intervention.VOICE_NUDGE,
                    run_at=ep.first_failed_at + timedelta(days=1),
                )
            return None

    from paypilot.voice.node import VoiceNode
    from paypilot.voice.script import TemplateScriptWriter

    pop, events = _september_events()
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    voice = VoiceNode(merchant_name="PayPilot", writer=TemplateScriptWriter())
    # pick a big-ticket ISF episode and opt its customer out
    target = next(
        e
        for e in events
        if e.mode is FailureMode.INSUFFICIENT_FUNDS and e.amount_paise >= 100_000
    )
    voice.mark_opted_out(f"{target.subscription_id}:{target.episode_no}")
    result = RunEngine(pop, window=w, voice_node=voice).run(_VoiceForFunds(), events)

    skips = [t for t in result.timeline if t.kind == "voice_skip"]
    assert skips, "expected a voice_skip entry for the opted-out customer"
    assert any(t.subscription_id == target.subscription_id for t in skips)
    # and no call artifact exists for that episode
    opted_key = f"{target.subscription_id}:{target.episode_no}"
    assert not any(c.episode_key == opted_key for c in voice.calls)


def test_engine_escalates_loudly_when_voice_channel_cannot_execute() -> None:
    """Fail-loud: a VOICE_NUDGE the channel cannot execute (writer down) becomes a
    voice_escalate timeline entry + the episode closes — never a silent drop."""

    class _VoiceForFunds:
        """High-value insufficient-funds episodes get the personal channel."""

        name = "voice-funds"

        def next_action(self, ep: EpisodeView) -> ProposedAction | None:
            if ep.mode is FailureMode.INSUFFICIENT_FUNDS and ep.amount_paise >= 100_000:
                return ProposedAction(
                    intervention=Intervention.VOICE_NUDGE,
                    run_at=ep.first_failed_at + timedelta(days=1),
                )
            return None

    from paypilot.voice.node import VoiceNode
    from paypilot.voice.script import VoiceWriterUnavailable

    class _DownWriter:
        def write(self, ctx):
            raise VoiceWriterUnavailable("LLM provider down")

    pop, events = _september_events()
    w = WindowSpec(start=date(2026, 9, 1), end=date(2026, 9, 30))
    voice = VoiceNode(merchant_name="PayPilot", writer=_DownWriter())
    result = RunEngine(pop, window=w, voice_node=voice).run(_VoiceForFunds(), events)

    escalations = [t for t in result.timeline if t.kind == "voice_escalate"]
    assert escalations, "expected fail-loud voice_escalate entries"
    assert all("LLM provider down" in t.detail for t in escalations)
    assert len(voice.calls) == 0  # nothing was silently spoken
