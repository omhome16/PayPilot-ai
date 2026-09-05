"""Pluggable TTS rendering — turns an approved call script into a real audio file.

Default is NoopTTS (audio_path stays None). EdgeTTS is available when the
``edge-tts`` package is installed; it is imported lazily so the core stays
dependency-free and CI never needs network access. Any rendering failure
degrades to ``None`` — audio is a bonus, never a correctness requirement.
"""

from pathlib import Path

from paypilot.voice.node import TTSEngine  # re-export for callers


class NoopTTS:
    """No backend installed — audio_path remains None (the honest default)."""

    def render(self, script: str, out_path: Path) -> Path | None:
        return None


class EdgeTTS:
    """Free, keyless neural TTS (Microsoft Edge voices) via the ``edge-tts`` package.

    Install with: ``uv sync --extra tts`` (declared in pyproject) or
    ``pip install edge-tts``. Lazy import means the module loads fine without it.
    """

    def __init__(self, voice: str = "hi-IN-SwaraNeural") -> None:
        self.voice = voice
        self.rendered = 0

    def render(self, script: str, out_path: Path) -> Path | None:
        try:
            import asyncio

            import edge_tts  # type: ignore[import-not-found]

            async def _save() -> None:
                communicate = edge_tts.Communicate(script, self.voice)
                await communicate.save(str(out_path))

            asyncio.run(_save())
            self.rendered += 1
            return out_path
        except Exception:  # noqa: BLE001 — degradation is the design
            return None


def main() -> None:
    """CLI: render a Hinglish script to an .mp3 via edge-tts.

    Usage: paypilot-tts "script text..." out.mp3 [--voice hi-IN-SwaraNeural]
    """
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Render a PayPilot call script to audio (edge-tts).")
    p.add_argument("script", help="the Hinglish script text, quoted")
    p.add_argument("out", help="output .mp3 path")
    p.add_argument(
        "--voice", default="hi-IN-SwaraNeural", help="edge-tts voice (default Swara, Hindi female)"
    )
    args = p.parse_args()

    engine = EdgeTTS(voice=args.voice)
    rendered = engine.render(args.script, Path(args.out))
    if rendered is None:
        print(
            "TTS rendering failed — is edge-tts installed? Try: uv sync --extra tts",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"rendered {rendered}")


__all__ = ["TTSEngine", "NoopTTS", "EdgeTTS", "main"]
