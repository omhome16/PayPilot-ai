"""Settings must load from environment and refuse non-test Razorpay keys."""

import pytest
from pydantic import ValidationError

from paypilot.settings import Settings


def test_settings_load_from_env(monkeypatch) -> None:
    monkeypatch.setenv("RZP_KEY_ID", "rzp_test_abc123")
    monkeypatch.setenv("RZP_KEY_SECRET", "sekrit")
    monkeypatch.setenv("RZP_WEBHOOK_SECRET", "whsec_xyz")
    s = Settings(_env_file=None)  # hermetic: ignore any local .env
    assert s.rzp_key_id == "rzp_test_abc123"
    assert s.rzp_key_secret.get_secret_value() == "sekrit"
    assert s.rzp_webhook_secret.get_secret_value() == "whsec_xyz"
    assert s.env == "dev"  # default


def test_settings_reject_live_key(monkeypatch) -> None:
    """PayPilot is test-mode only by construction — a live key must be refused."""
    monkeypatch.setenv("RZP_KEY_ID", "rzp_live_DANGER")
    monkeypatch.setenv("RZP_KEY_SECRET", "x")
    monkeypatch.setenv("RZP_WEBHOOK_SECRET", "x")
    import pytest

    with pytest.raises(ValueError, match="TEST mode"):
        Settings(_env_file=None)


def test_missing_required_key_raises(monkeypatch) -> None:
    for var in ("RZP_KEY_ID", "RZP_KEY_SECRET", "RZP_WEBHOOK_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
