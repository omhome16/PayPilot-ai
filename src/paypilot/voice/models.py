"""VoiceCall artifact + duration estimation.

A call is an immutable artifact the dashboard can play/replay.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VoiceCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    episode_key: str
    merchant_name: str
    customer_name: str
    script_hinglish: str = Field(min_length=20)  # rejects empty/whitespace
    audio_path: str | None  # None until a TTS backend renders it
    created_at: dt.datetime
    words_per_second: float = Field(default=2.5, gt=0)  # natural Hinglish pace

    @field_validator("created_at")
    @classmethod
    def _tz_aware(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @property
    def word_count(self) -> int:
        return len(self.script_hinglish.split())

    @property
    def estimated_duration_seconds(self) -> float:
        return round(self.word_count / self.words_per_second, 1)
