"""VoiceNode: turns an approved VOICE_NUDGE decision into a safe VoiceCall artifact.

Writers are pluggable (template default, LLM optional); every produced script is
safety-validated before becoming a call artifact. TTS rendering attaches later —
the artifact carries audio_path=None until then.
"""

import datetime as dt
from collections.abc import Callable
from typing import Any, Protocol

from paypilot.engine.policy import EpisodeView
from paypilot.voice.models import VoiceCall
from paypilot.voice.script import CallScript, TemplateScriptWriter


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
        ctx: dict[str, Any] = {
            "customer_name": customer_name,
            "merchant": self.merchant_name,
            "amount_rupees": round(episode.amount_paise / 100, 2),
            "mode": str(episode.mode),
            "days_to_salary": None,
            "history_note": (
                f"history: {ep_ratio(episode):.2f} on-time" if episode.profile is not None else ""
            ),
            "payment_url": payment_url,
            "attempt_no": episode.attempts_made + 1,
        }
        script = self.writer.write(ctx)  # writers safety-gate internally
        call = VoiceCall(
            episode_key=f"{episode.subscription_id}:{episode.episode_no}",
            merchant_name=self.merchant_name,
            customer_name=customer_name,
            script_hinglish=script.text,
            audio_path=None,
            created_at=dt.datetime.now(dt.UTC),
        )
        self.calls.append(call)
        return call


def ep_ratio(ep: EpisodeView) -> float:
    return float(ep.profile.on_time_ratio) if ep.profile is not None else 0.5


def make_voice_node(
    merchant_name: str,
    writer: ScriptWriter | None = None,
) -> Callable[[EpisodeView, str, str], VoiceCall]:
    """Factory for graph wiring: returns a plain make_call closure."""
    node = VoiceNode(merchant_name=merchant_name, writer=writer)

    def _make(episode: EpisodeView, customer_name: str, payment_url: str) -> VoiceCall:
        return node.make_call(episode, customer_name, payment_url)

    return _make
