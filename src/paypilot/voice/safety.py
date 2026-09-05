"""Script safety validation — the voice channel obeys the same rails as everything else.

Required elements (DR12, TRAI-style norms):
- identifies the merchant by name
- states the pending amount (₹ figure)
- offers an explicit opt-out (pause/cancel/stop)

Hard blocks: threats/legal intimidation, abusive language, spam shouting (ALL-CAPS runs).
"""

import re
from dataclasses import dataclass, field

_BLOCKLIST = (
    "legal action",
    "police",
    "court",
    "arrest",
    "kanooni kaarwahi",
    "report karenge",
)
_ABUSE = ("bekaar", "nalayak", "bewakoof", "stupid", "idiot")
_SHOUT_RUN = re.compile(r"[A-Z]{4,}!")  # e.g. "ABHI ABHI ABHI!!!"
_OPTOUT_WORDS = ("rok denge", "pause", "cancel", "band kar", "chhod", "nahi chalana chahte")
_AMOUNT_RE = re.compile(r"₹\s?[\d,]+")
MAX_WORDS = 120  # ≈48s at 2.5 wps — a call must respect the human's time (DR11)


@dataclass(frozen=True)
class ScriptReport:
    ok: bool
    violations: list[str] = field(default_factory=list)


def validate_script(script: str, *, merchant_name: str) -> ScriptReport:
    text = script.strip()
    low = text.lower()
    problems: list[str] = []

    if not text:
        return ScriptReport(ok=False, violations=["empty script"])

    if merchant_name.split()[0].lower() not in low:
        problems.append(f"merchant name '{merchant_name}' not mentioned")

    if not _AMOUNT_RE.search(text):
        problems.append("pending amount (₹…) not mentioned")

    if not any(w in low for w in _OPTOUT_WORDS):
        problems.append("no opt-out offer (pause/stop/rocks denge) found")

    for phrase in _BLOCKLIST:
        if phrase in low:
            problems.append(f"threat/intimidation language blocked: '{phrase}'")

    for word in _ABUSE:
        if word in low:
            problems.append(f"abusive language blocked: '{word}'")

    shout = _SHOUT_RUN.search(text)
    if shout is not None:
        problems.append(f"spam shouting detected: '{shout.group(0)}'")

    words = len(text.split())
    if words > MAX_WORDS:
        problems.append(f"script too long: {words} words (max {MAX_WORDS})")

    return ScriptReport(ok=not problems, violations=problems)
