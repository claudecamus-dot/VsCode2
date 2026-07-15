"""Tests du sélecteur multi-fournisseur IA (AI_PROVIDER) — la logique de
dispatch/dégradation d'`ai_common.py`, indépendamment de tout appel réseau
réel (les fonctions `_call_*` sont monkeypatchées)."""
from __future__ import annotations

import pytest

from app.services import ai_common


def test_active_provider_defaults_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert ai_common.active_provider() == "anthropic"


def test_active_provider_falls_back_on_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "not-a-real-provider")
    assert ai_common.active_provider() == "anthropic"


@pytest.mark.parametrize(
    "provider,expected_env",
    [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"), ("mistral", "MISTRAL_API_KEY")],
)
def test_api_key_env_name_matches_provider(
    monkeypatch: pytest.MonkeyPatch, provider: str, expected_env: str
) -> None:
    monkeypatch.setenv("AI_PROVIDER", provider)
    assert ai_common.api_key_env_name() == expected_env


def test_active_model_uses_per_provider_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNTHESE_MODEL", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    assert ai_common.active_model() == "gpt-4o"
    monkeypatch.setenv("AI_PROVIDER", "mistral")
    assert ai_common.active_model() == "mistral-large-latest"


def test_active_model_override_applies_regardless_of_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("SYNTHESE_MODEL", "gpt-4o-mini")
    assert ai_common.active_model() == "gpt-4o-mini"


def test_is_configured_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ai_common.is_configured() is False


def test_is_configured_true_with_key_and_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "fake-key")
    assert ai_common.is_configured() is True


def test_call_ai_json_missing_key_raises_error_cls_with_provider_env_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class MyError(ai_common.AIError):
        pass

    with pytest.raises(MyError, match="OPENAI_API_KEY"):
        ai_common.call_ai_json("sys", "prompt", {}, "", error_cls=MyError)


def test_call_ai_json_dispatches_to_configured_provider_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "fake-key")

    calls: list[str] = []

    def fake_mistral_call(system, prompt, schema, json_hint, model, max_tokens):
        calls.append("mistral")
        assert model == "mistral-large-latest"
        return '{"answer": "ok"}'

    def fake_anthropic_call(*args, **kwargs):
        calls.append("anthropic")
        return "{}"

    monkeypatch.setattr(ai_common, "_call_mistral", fake_mistral_call)
    monkeypatch.setattr(ai_common, "_call_anthropic", fake_anthropic_call)
    monkeypatch.setitem(ai_common._CALLERS, "mistral", fake_mistral_call)
    monkeypatch.setitem(ai_common._CALLERS, "anthropic", fake_anthropic_call)
    monkeypatch.setitem(ai_common._SDK_LOADERS, "mistral", lambda: object())

    result = ai_common.call_ai_json("sys", "prompt", {"type": "object"}, "\nJSON only.")
    assert result == {"answer": "ok"}
    assert calls == ["mistral"]  # jamais l'autre fournisseur


def test_call_ai_json_wraps_unexpected_exception_in_error_cls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setitem(ai_common._SDK_LOADERS, "anthropic", lambda: object())

    def boom(*args, **kwargs):
        raise ConnectionError("réseau coupé")

    monkeypatch.setitem(ai_common._CALLERS, "anthropic", boom)

    class MyError(ai_common.AIError):
        pass

    with pytest.raises(MyError):
        ai_common.call_ai_json("sys", "prompt", {}, "", error_cls=MyError)


def test_call_ai_json_invalid_json_response_raises_error_cls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setitem(ai_common._SDK_LOADERS, "anthropic", lambda: object())
    monkeypatch.setitem(ai_common._CALLERS, "anthropic", lambda *a, **k: "pas du json")

    class MyError(ai_common.AIError):
        pass

    with pytest.raises(MyError, match="JSON"):
        ai_common.call_ai_json("sys", "prompt", {}, "", error_cls=MyError)


# --------------------------------------------------------------------------- #
# Ollama (2026-07-15) — fournisseur local, sans clé API. is_configured()/
# call_ai_json() ne doivent jamais exiger OLLAMA_HOST (optionnel, défaut
# localhost:11434), contrairement aux fournisseurs cloud.
# --------------------------------------------------------------------------- #
def test_ollama_is_configured_without_any_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ai_common.is_configured() is True


def test_ollama_host_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ai_common.ollama_host() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:12345/")
    assert ai_common.ollama_host() == "http://localhost:12345"


def test_ollama_active_model_defaults_to_llama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("SYNTHESE_MODEL", raising=False)
    assert ai_common.active_model() == "llama3.1"


def test_call_ai_json_dispatches_to_ollama_without_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    def fake_ollama_call(system, prompt, schema, json_hint, model, max_tokens):
        assert model == "llama3.1"
        return '{"answer": "ok"}'

    monkeypatch.setitem(ai_common._CALLERS, "ollama", fake_ollama_call)
    result = ai_common.call_ai_json("sys", "prompt", {"type": "object"}, "\nJSON only.")
    assert result == {"answer": "ok"}


def test_call_ai_json_ollama_unreachable_raises_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serveur Ollama injoignable (pas installé/pas démarré) : message clair
    plutôt qu'une exception réseau brute — même contrat que les autres
    fournisseurs (clé absente, SDK manquant)."""
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")  # port improbable

    class MyError(ai_common.AIError):
        pass

    with pytest.raises(MyError, match="Ollama"):
        ai_common.call_ai_json("sys", "prompt", {}, "", error_cls=MyError, max_tokens=10)
