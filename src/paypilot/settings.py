"""Typed application settings, loaded from environment (.env supported).

PayPilot is test-mode only by construction: live Razorpay keys are refused at startup.
Secrets use SecretStr so they never leak into logs, reprs, or error messages.
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

    @field_validator("rzp_key_id")
    @classmethod
    def _reject_live_keys(cls, v: str) -> str:
        if not v.startswith("rzp_test_"):
            raise ValueError("PayPilot runs TEST mode only — RZP_KEY_ID must start with rzp_test_")
        return v


def get_settings() -> Settings:
    # Fields are populated from environment at runtime; mypy can't see that.
    return Settings()  # type: ignore[call-arg]
