"""NaiveRetryPolicy — the fair-naive baseline (Phase 2 decision D17).

What a typical small Indian merchant's team does, no more:
- insufficient funds → "try again in a couple of days" (no salary awareness)
- transient failures (auth timeout / bank downtime) → plain same-day retry
- revoked / limit-exceeded episodes → give up immediately (retrying a revoked mandate
  is impossible in the real world; our outcome model would refuse it anyway)
- after 3 total attempts → give up

Deliberately NOT: diagnosis nuance, salary-timed retries, payment links, voice,
rail switching, escalation. Those are exactly what PayPilot adds.
"""

import datetime as dt

from paypilot.domain.enums import FailureMode, Intervention
from paypilot.engine.policy import EpisodeView, ProposedAction

_RETRY_GAP_DAYS = 2
_MAX_ATTEMPTS = 3


class NaiveRetryPolicy:
    name = "naive-retry-x3"

    def next_action(self, episode: EpisodeView) -> ProposedAction | None:
        if episode.attempts_made >= _MAX_ATTEMPTS:
            return None

        match episode.mode:
            case FailureMode.INSUFFICIENT_FUNDS:
                intervention = Intervention.SMART_RETRY  # untimed ⇒ blind discount
                run_at = episode.first_failed_at + dt.timedelta(days=_RETRY_GAP_DAYS)
            case FailureMode.AUTH_TIMEOUT | FailureMode.BANK_DOWNTIME:
                intervention = Intervention.RETRY
                run_at = episode.first_failed_at + dt.timedelta(days=_RETRY_GAP_DAYS)
            case FailureMode.MANDATE_REVOKED | FailureMode.LIMIT_EXCEEDED:
                return None  # consent-broken / cap-broken: retrying cannot help

        return ProposedAction(intervention=intervention, run_at=run_at)
