"""VoiceNode: turns an approved VOICE_NUDGE decision into a safe VoiceCall artifact.

Writers are pluggable; every produced script is safety-validated before becoming
a call artifact. Two gates apply:
1. Writers gate their own output (blocklists, required amount/link, length).
2. The node re-checks the STRICT channel policy (merchant identified, opt-out
   offered — DR12) and REFUSES on any violation. Unsafe text can never become a
   call, no matter how it was produced — and it is never silently replaced: the
   node raises so the caller escalates to a human (fail-loud).

A node constructed with writer=None has NO script capacity: make_call raises
VoiceChannelUnavailable. Deterministic script production is an explicit choice
(pass TemplateScriptWriter for the reference path); it is never an implicit
fallback for a broken or absent LLM writer.

Consent has teeth: a customer who opted out is in the do-not-call registry and
make_call refuses to produce an artifact for them (DR12 — the offer is only
meaningful if it is honoured).

TTS rendering is pluggable: a TTSEngine, when supplied, renders the approved
script to a real audio file and the artifact's audio_path becomes that path.
"""

import datetime as dt
from pathlib import Path
from typing import Any, Protocol

from paypilot.domain.calendar import ist_date_of, next_salary_date
from paypilot.engine.policy import EpisodeView
from paypilot.voice.models import VoiceCall
from paypilot.voice.safety import validate_script
from paypilot.voice.script import CallScript, ScriptSafetyError


class DoNotCallError(ValueError):
    """Raised when make_call is attempted for a customer who opted out."""


class VoiceChannelUnavailable(RuntimeError):
    """Raised when no script writer is configured (fail-loud).

    The voice channel cannot speak at all — the caller must escalate the episode
    to human review rather than fake an artifact.
    """


class TTSEngine(Protocol):
    def render(self, script: str, out_path: Path) -> Path | None: ...


class ScriptWriter(Protocol):
    def write(self, ctx: dict[str, Any]) -> CallScript: ...


class VoiceNode:
    def __init__(
        self,
        merchant_name: str,
        writer: ScriptWriter | None = None,
        tts: TTSEngine | None = None,
    ) -> None:
        self.merchant_name = merchant_name
        # writer=None means the channel is UNAVAILABLE (fail-loud), never a default.
        self.writer: ScriptWriter | None = writer
        self.tts: TTSEngine | None = tts
        self.calls: list[VoiceCall] = []
        self._do_not_call: set[str] = set()

    # -- consent registry -----------------------------------------------------

    def mark_opted_out(self, episode_key: str) -> None:
        """Record that this customer asked to stop being called (DR12 honouring)."""
        self._do_not_call.add(episode_key)

    def is_opted_out(self, episode_key: str) -> bool:
        return episode_key in self._do_not_call

    # -- the call -------------------------------------------------------------

    def make_call(
        self,
        episode: EpisodeView,
        customer_name: str,
        payment_url: str,
    ) -> VoiceCall:
        episode_key = f"{episode.subscription_id}:{episode.episode_no}"
        if self.is_opted_out(episode_key):
            raise DoNotCallError(f"{episode_key} opted out — no call artifact produced")
        if self.writer is None:
            raise VoiceChannelUnavailable(
                f"{episode_key}: no script writer configured — voice channel unavailable"
            )
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
        script = self.writer.write(ctx)  # writers gate their own output (raise on fail)
        report = validate_script(script.text, merchant_name=self.merchant_name)
        if not report.ok:
            # Fail-loud: the strict DR12 gate was bypassed by a writer. Never ship the
            # text and never silently swap in the template — raise for human review.
            raise ScriptSafetyError("; ".join(report.violations))
        call = VoiceCall(
            episode_key=episode_key,
            merchant_name=self.merchant_name,
            customer_name=customer_name,
            script_hinglish=script.text,
            source=script.source,
            audio_path=None,
            created_at=dt.datetime.now(dt.UTC),
        )
        if self.tts is not None:
            call = self._render_audio(call)
        self.calls.append(call)
        return call

    def _render_audio(self, call: VoiceCall) -> VoiceCall:
        """Render the approved script to audio; failure degrades to audio_path=None."""
        try:
            out = Path("data/audio") / f"{call.episode_key.replace(':', '_')}.mp3"
            out.parent.mkdir(parents=True, exist_ok=True)
            rendered = self.tts.render(call.script_hinglish, out) if self.tts else None
            if rendered is not None:
                return call.model_copy(update={"audio_path": str(rendered)})
        except Exception:  # noqa: BLE001, S110 — a TTS failure must never break a recovery
            return call  # degraded: audio_path stays None, call remains safe
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
        ist_date = ist_date_of(episode.first_failed_at)
        return (next_salary_date(ist_date) - ist_date).days
