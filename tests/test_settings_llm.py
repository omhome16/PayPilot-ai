"""LLM-layer settings: OpenRouter is OPTIONAL by design.

No key configured ⇒ PayPilot runs identically with reasoning/narration OFF
(NullReasoner). A key appearing in env is the ONLY switch — no code change.
"""

from paypilot.settings import Settings


def _base_env(monkeypatch) -> None:
    monkeypatch.setenv("RZP_KEY_ID", "rzp_test_abc123")
    monkeypatch.setenv("RZP_KEY_SECRET", "s")
    monkeypatch.setenv("RZP_WEBHOOK_SECRET", "w")


def test_without_key_llm_layer_is_off(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.openrouter_api_key is None


def test_with_key_settings_carry_it_as_secret(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-testing")
    monkeypatch.setenv("OPENROUTER_MODEL", "z-ai/glm-4.6")
    s = Settings(_env_file=None)
    assert s.openrouter_api_key is not None
    assert s.openrouter_api_key.get_secret_value() == "sk-or-v1-testing"
    assert s.openrouter_model == "z-ai/glm-4.6"


def test_default_base_url_is_openrouter(monkeypatch) -> None:
    _base_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.openrouter_base_url == "https://openrouter.ai/api/v1"
