"""VoiceNode: turns an approved VOICE_NUDGE decision into a safe VoiceCall artifact.

Writers are pluggable (template default, LLM optional); every produced script is
safety-validated before becoming a call artifact. Two gates apply:
1. Writers gate their own output (blocklists, required amount/link).
2. The node re-checks the STRICT channel policy (merchant identified, opt-out
   offered — DR12) and degrades to the template on any violation. Unsafe text
   can never become a call, no matter how it was produced.

TTS rendering attaches later — the artifact carries audio_path=None until then.
"""

import datetime as dt
from collections.abc import Callable
from typing import Any, Protocol

from paypilot.domain.calendar import IndianPaymentCalendar
from paypilot.engine.policy import EpisodeView
from paypilot.voice.models import VoiceCall
from paypilot.voice.safety import validate_script
from paypilot.voice.script import CallScript, TemplateScriptWriter

_IST = dt.timedelta(hours=5, minutes=30)


class ScriptWriter(Protocol):
    def write(self, ctx: dict[str, Any]) -> CallScript: ...


class VoiceNode:
    def __init__(
        self,
        merchant_name: str,
        writer: ScriptWriter | None = None,
    ) -> None:
        self.merchant_name = merchant_name
        self.writer: ScriptWriter = writer or TemplateScriptWriter()
        self.calls: list[VoiceCall] = []

    def make_call(
        self,
        episode: EpisodeView,
        customer_name: str,
        payment_url: str,
    ) -> VoiceCall:
        brief = self.brief(episode)
        ctx: dict[str, Any] = {
            "customer_name": customer_name,
            "merchant": self.merchant_name,
            "amount_rupees": brief["amount_rupees"],
            "mode": brief["mode"],
            "days_to_salary": brief["days_to_salary"],
            "on_time_ratio": brief["on_time_ratio"],
            "history_note": (
                f"history: {brief['on_time_ratio']:.2f} on-time"
                if brief["on_time_ratio"] is not None
                else ""
            ),
            "payment_url": payment_url,
            "attempt_no": brief["failed_attempts"],
        }
        script = self.writer.write(ctx)  # writers safety-gate internally
        report = validate_script(script.text, merchant_name=self.merchant_name)
        if not report.ok:
            # strict DR12 gate failed (e.g. LLM forgot the opt-out) → always-safe template
            script = TemplateScriptWriter().write(ctx)
        call = VoiceCall(
            episode_key=f"{episode.subscription_id}:{episode.episode_no}",
            merchant_name=self.merchant_name,
            customer_name=customer_name,
            script_hinglish=script.text,
            source=script.source,
            audio_path=None,
            created_at=dt.datetime.now(dt.UTC),
        )
        self.calls.append(call)
        return call

    def brief(self, episode: EpisodeView) -> dict[str, Any]:
        """The data + strategy context behind one call — shown in UIs and APIs so
        the demo can prove the script is driven by the customer's situation."""
        days = self._days_to_salary(episode)
        profile = episode.profile
        if profile is None:
            ratio: float | None = None
            tone = "unknown"
        else:
            ratio = round(profile.on_time_ratio, 2)
            tone = "reliable" if ratio >= 0.8 else "mixed" if ratio >= 0.5 else "history_of_misses"
        return {
            "mode": str(episode.mode),
            "amount_rupees": round(episode.amount_paise / 100, 2),
            "failed_attempts": episode.attempts_made,
            "days_to_salary": days,
            "on_time_ratio": ratio,
            "history_tone": tone,
        }

    @staticmethod
    def _days_to_salary(episode: EpisodeView) -> int:
        """Calendar-aware scripts: the salary line only fires when it's true."""
        ist_date = (episode.first_failed_at + _IST).date()
        cal = IndianPaymentCalendar.with_default_festivals(year=ist_date.year)
        return (cal.next_salary_date(ist_date) - ist_date).days


def make_voice_node(
    merchant_name: str,
    writer: ScriptWriter | None = None,
) -> Callable[[EpisodeView, str, str], VoiceCall]:
    """Factory for graph wiring: returns a plain make_call closure."""
    node = VoiceNode(merchant_name=merchant_name, writer=writer)

    def _make(episode: EpisodeView, customer_name: str, payment_url: str) -> VoiceCall:
        return node.make_call(episode, customer_name, payment_url)

    return _make
