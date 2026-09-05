"""Typed application settings, loaded from environment (.env supported).

PayPilot is test-mode only by construction: live Razorpay keys are refused at startup.
The product is LLM-only: the server refuses to start without OPENROUTER_API_KEY —
there is no scripted-brain fallback. Secrets use SecretStr so they never leak into
logs, reprs, or error messages.
"""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rzp_key_id: str
    rzp_key_secret: SecretStr
    rzp_webhook_secret: SecretStr

    env: str = "dev"
    log_level: str = "INFO"
    merchant_name: str = "PayPilot"  # spoken in voice scripts; validated for ID enforcement

    # --- LLM layer (REQUIRED for the server; the product has no scripted fallback) ---
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Reasoning models on free tiers think before answering — give them room.
    openrouter_timeout_s: float = 90.0

    @field_validator("rzp_key_id")
    @classmethod
    def _reject_live_keys(cls, v: str) -> str:
        if not v.startswith("rzp_test_"):
            raise ValueError("PayPilot runs TEST mode only — RZP_KEY_ID must start with rzp_test_")
        return v

    @field_validator("openrouter_api_key", mode="before")
    @classmethod
    def _empty_key_is_no_key(cls, v: object) -> object:
        """``OPENROUTER_API_KEY=`` (empty) must mean 'unconfigured', not a key
        of empty string that later fails with an opaque 401."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


def get_settings() -> Settings:
    # Fields are populated from environment at runtime; mypy can't see that.
    return Settings()  # type: ignore[call-arg]
