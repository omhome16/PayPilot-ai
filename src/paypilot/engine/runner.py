"""RunEngine — processes failure episodes chronologically through a Policy.

Design invariants (all test-enforced):
- BOTH arms face identical worlds: same seed ⇒ same episodes ⇒ same draws available
- Policies only ever see EpisodeView; the engine owns gates, outcomes, audit
- Compliance: an illegal proposal is counted, logged, and closes the episode (no retry loop)
- Quiet hours: proposals outside 08:00–21:59 IST are clamped (logged as kind="clamp")
- Horizon: actions beyond window.end + grace are abandoned (recovery window closed)

Mechanics: a min-heap of scheduled moments. Each entry is either an EPISODE OPENING
(policy gets consulted) or a PROPOSED ACTION (gated, then executed against the outcome
model). A failed execution consults the policy again — that loop is the whole recovery
game; the policy's quality decides when it stops.
"""

import datetime as dt
import heapq
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.domain.enums import FailureMode, MandateRail
from paypilot.domain.models import FailureEvent
from paypilot.engine.policy import EpisodeView, Policy, ProposedAction
from paypilot.simulator.outcome import OutcomeModel
from paypilot.simulator.population import Population
from paypilot.simulator.window import WindowSpec

_IST_OFFSET = dt.timedelta(hours=5, minutes=30)
_LEGAL_START = 8  # 08:00 IST
_LEGAL_END = 21  # last legal start hour (through 21:59)
_GRACE_DAYS = 7


@dataclass(frozen=True)
class TimelineEntry:
    kind: str  # episode_start | action | success | fail | clamp | give_up | abandon | violation
    at: dt.datetime
    subscription_id: str
    episode_no: int
    detail: str


@dataclass(frozen=True)
class RunResult:
    policy_name: str
    episodes: int
    at_risk_paise: int
    recovered_paise: int
    recovered_episodes: int
    attempts_executed: int
    compliance_violations: int
    timeline: tuple[TimelineEntry, ...] = field(default_factory=tuple)

    @property
    def recovery_rate(self) -> float:
        return self.recovered_episodes / self.episodes if self.episodes else 0.0


@dataclass
class _EpisodeState:
    mode: FailureMode
    amount_paise: int
    rail: MandateRail
    billing_day: int
    vertical: str
    attempts_made: int
    recovered: bool = False
    closed: bool = False


def _clamp_to_legal_hours(when_utc: dt.datetime) -> dt.datetime:
    """Move a UTC instant into the legal IST debit window (08:00–21:59)."""
    ist = when_utc + _IST_OFFSET
    if _LEGAL_START <= ist.hour <= _LEGAL_END:
        return when_utc
    # push to 10:00 IST the same day (or next day if the clamp crossed midnight)
    target = ist.replace(hour=10, minute=0, second=0, microsecond=0)
    if ist.hour > _LEGAL_END:
        target += dt.timedelta(days=1)
    return target - _IST_OFFSET


class RunEngine:
    def __init__(self, population: Population, window: WindowSpec, seed: int = 42) -> None:
        self._subs_by_id = {s.id: s for s in population.subscriptions}
        self._cust_by_id = {c.id: c for c in population.customers}
        self._mandates = list(population.mandates)
        self._rail_by_customer = {m.customer_id: m.rail for m in population.mandates}
        self._window = window
        self._seed = seed

    def run(self, policy: Policy, events: tuple[FailureEvent, ...]) -> RunResult:
        # Note: the engine draws NO random numbers of its own. All stochasticity flows
        # through OutcomeModel(seed), so identical seeds give byte-identical runs.
        model = OutcomeModel(seed=self._seed)
        cal = IndianPaymentCalendar.with_default_festivals(year=self._window.start.year)
        horizon = self._window.end + dt.timedelta(days=_GRACE_DAYS)

        episodes, first_failure_at = self._open_episodes(events)
        timeline: list[TimelineEntry] = []
        seq = count()
        # heap entries: (when_utc, seq, sub_id, ep_no, proposal_or_None)
        HeapItem = tuple[dt.datetime, int, str, int, ProposedAction | None]
        heap: list[HeapItem] = []

        recovered_paise = 0
        recovered_episodes = 0
        attempts_executed = 0
        violations = 0

        for sub_id, ep_no in episodes:
            first_at = first_failure_at[(sub_id, ep_no)]
            heapq.heappush(heap, (first_at, next(seq), sub_id, ep_no, None))
            st = episodes[(sub_id, ep_no)]
            timeline.append(
                TimelineEntry(
                    "episode_start",
                    first_at,
                    sub_id,
                    ep_no,
                    f"{st.mode} · ₹{st.amount_paise / 100:.0f}",
                )
            )

        while heap:
            at, _, sub_id, ep_no, proposal = heapq.heappop(heap)
            key = (sub_id, ep_no)
            st = episodes[key]
            if st.closed or st.recovered:
                continue

            if proposal is None:
                # ---- episode opening: consult the policy ----
                self._consult(policy, episodes, key, at, heap, seq, timeline)
                continue

            # ---- a proposed action: gate it ----
            clamped = _clamp_to_legal_hours(at)
            if clamped != at:
                timeline.append(
                    TimelineEntry(
                        "clamp",
                        clamped,
                        sub_id,
                        ep_no,
                        f"{proposal.intervention} moved into legal hours",
                    )
                )
            if clamped > dt.datetime.combine(horizon, dt.time(23, 59, 59), tzinfo=dt.UTC):
                st.closed = True
                timeline.append(
                    TimelineEntry("abandon_window", clamped, sub_id, ep_no, "past recovery horizon")
                )
                continue

            ist_date = (clamped + _IST_OFFSET).date()
            days_to_salary = (cal.next_salary_date(ist_date) - ist_date).days

            attempt_no = st.attempts_made + 1
            attempts_executed += 1
            timeline.append(
                TimelineEntry(
                    "action",
                    clamped,
                    sub_id,
                    ep_no,
                    f"{proposal.intervention} (attempt {attempt_no})",
                )
            )

            success = model.draw(
                proposal.intervention,
                st.mode,
                attempt_no=attempt_no,
                days_to_salary=days_to_salary,
            )
            if success:
                st.recovered = True
                recovered_episodes += 1
                recovered_paise += st.amount_paise
                timeline.append(
                    TimelineEntry(
                        "success", clamped, sub_id, ep_no, f"₹{st.amount_paise / 100:.0f} recovered"
                    )
                )
            else:
                st.attempts_made = attempt_no
                timeline.append(
                    TimelineEntry("fail", clamped, sub_id, ep_no, f"attempt {attempt_no} failed")
                )
                self._consult(policy, episodes, key, clamped, heap, seq, timeline)

        return RunResult(
            policy_name=policy.name,
            episodes=len(episodes),
            at_risk_paise=sum(st.amount_paise for st in episodes.values()),
            recovered_paise=recovered_paise,
            recovered_episodes=recovered_episodes,
            attempts_executed=attempts_executed,
            compliance_violations=violations,
            timeline=tuple(timeline),
        )

    # -- internals ---------------------------------------------------------------

    def _open_episodes(
        self, events: tuple[FailureEvent, ...]
    ) -> tuple[dict[tuple[str, int], _EpisodeState], dict[tuple[str, int], dt.datetime]]:
        episodes: dict[tuple[str, int], _EpisodeState] = {}
        first_at: dict[tuple[str, int], dt.datetime] = {}
        for e in sorted(events, key=lambda x: x.occurred_at):
            key = (e.subscription_id, e.episode_no)
            if key not in episodes:
                sub = self._subs_by_id[e.subscription_id]
                cust = self._cust_by_id[sub.customer_id]
                episodes[key] = _EpisodeState(
                    mode=e.mode,
                    amount_paise=e.amount_paise,
                    rail=self._rail_of(sub.customer_id),
                    billing_day=sub.billing_day,
                    vertical=cust.vertical,
                    attempts_made=1,  # the opening failure IS attempt 1
                )
                first_at[key] = e.occurred_at
        return episodes, first_at

    def _rail_of(self, customer_id: str) -> MandateRail:
        return self._rail_by_customer[customer_id]

    def _consult(
        self,
        policy: Policy,
        episodes: dict[tuple[str, int], _EpisodeState],
        key: tuple[str, int],
        now: dt.datetime,
        heap: list[tuple[dt.datetime, int, str, int, ProposedAction | None]],
        seq: Iterator[int],
        timeline: list[TimelineEntry],
    ) -> None:
        sub_id, ep_no = key
        st = episodes[key]
        view = EpisodeView(
            subscription_id=sub_id,
            episode_no=ep_no,
            mode=st.mode,
            amount_paise=st.amount_paise,
            first_failed_at=now,
            attempts_made=st.attempts_made,
            rail=st.rail,
            billing_day=st.billing_day,
            vertical=st.vertical,
        )
        proposal = policy.next_action(view)
        if proposal is None:
            st.closed = True
            timeline.append(
                TimelineEntry("give_up", now, sub_id, ep_no, f"after {st.attempts_made} attempts")
            )
            return
        if proposal.intervention not in st.mode.permitted_interventions:
            # counted, logged, episode closed — no retry loop for violations
            timeline.append(
                TimelineEntry(
                    "violation",
                    now,
                    sub_id,
                    ep_no,
                    f"{proposal.intervention} forbidden for {st.mode}",
                )
            )
            st.closed = True
            return
        heapq.heappush(heap, (proposal.run_at, next(seq), sub_id, ep_no, proposal))
        timeline.append(
            TimelineEntry(
                "action",
                proposal.run_at,
                sub_id,
                ep_no,
                f"scheduled {proposal.intervention} as attempt {st.attempts_made + 1}",
            )
        )
