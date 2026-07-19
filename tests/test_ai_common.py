"""Tests du sélecteur multi-fournisseur IA (AI_PROVIDER) — la logique de
dispatch/dégradation d'`ai_common.py`, indépendamment de tout appel réseau
réel (les fonctions `_call_*` sont monkeypatchées)."""
from __future__ import annotations

import pytest

from app.services import ai_common


def test_active_provider_defaults_to_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert ai_common.active_provider() == "ollama"


def test_active_provider_falls_back_on_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "not-a-real-provider")
    assert ai_common.active_provider() == "ollama"


@pytest.mark.parametrize(
    "provider,expected_env",
    [("openai", "OPENAI_API_KEY"), ("mistral", "MISTRAL_API_KEY"), ("ollama", "OLLAMA_HOST")],
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

    def fake_openai_call(*args, **kwargs):
        calls.append("openai")
        return "{}"

    monkeypatch.setattr(ai_common, "_call_mistral", fake_mistral_call)
    monkeypatch.setattr(ai_common, "_call_openai", fake_openai_call)
    monkeypatch.setitem(ai_common._CALLERS, "mistral", fake_mistral_call)
    monkeypatch.setitem(ai_common._CALLERS, "openai", fake_openai_call)
    monkeypatch.setitem(ai_common._SDK_LOADERS, "mistral", lambda: object())

    result = ai_common.call_ai_json("sys", "prompt", {"type": "object"}, "\nJSON only.")
    assert result == {"answer": "ok"}
    assert calls == ["mistral"]  # jamais l'autre fournisseur


def test_call_ai_json_wraps_unexpected_exception_in_error_cls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setitem(ai_common._SDK_LOADERS, "openai", lambda: object())

    def boom(*args, **kwargs):
        raise ConnectionError("réseau coupé")

    monkeypatch.setitem(ai_common._CALLERS, "openai", boom)

    class MyError(ai_common.AIError):
        pass

    with pytest.raises(MyError):
        ai_common.call_ai_json("sys", "prompt", {}, "", error_cls=MyError)


def test_call_ai_json_invalid_json_response_raises_error_cls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setitem(ai_common._SDK_LOADERS, "openai", lambda: object())
    monkeypatch.setitem(ai_common._CALLERS, "openai", lambda *a, **k: "pas du json")

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


def test_ollama_num_ctx_defaults_to_8192(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans ce réglage explicite, Ollama retombe sur son défaut de 2048
    tokens, qui tronque silencieusement (sans erreur) toute transcription
    d'entretien un peu longue — bug de correctness corrigé le 2026-07-16."""
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    assert ai_common.ollama_num_ctx() == 8192
    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    assert ai_common.ollama_num_ctx() == 16384


def test_ollama_timeout_defaults_to_300(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)
    assert ai_common.ollama_timeout() == 300
    monkeypatch.setenv("OLLAMA_TIMEOUT", "60")
    assert ai_common.ollama_timeout() == 60


def test_call_ollama_payload_includes_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vérifie que `_call_ollama` transmet bien `num_ctx` à Ollama — pas
    seulement que la fonction existe (le bug corrigé était précisément une
    clé absente du payload envoyé)."""
    import json as json_module

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"message": {"content": "{}"}}'

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json_module.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)

    ai_common._call_ollama("sys", "prompt", {}, "\nJSON.", "llama3.1", 4000)

    assert captured["payload"]["options"]["num_ctx"] == 8192
    assert captured["payload"]["options"]["num_predict"] == 4000
    assert captured["timeout"] == 300


def test_ollama_keep_alive_defaults_to_30m(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    assert ai_common.ollama_keep_alive() == "30m"
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "1h")
    assert ai_common.ollama_keep_alive() == "1h"


def test_call_ollama_payload_includes_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans `keep_alive`, Ollama décharge le modèle après son défaut serveur
    de 5 minutes — trop court pour relire un écran de revue entre deux
    appels, ce qui fait repayer un chargement à froid pouvant lui-même
    dépasser OLLAMA_TIMEOUT (constat 2026-07-19)."""
    import json as json_module

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"message": {"content": "{}"}}'

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json_module.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "45m")

    ai_common._call_ollama("sys", "prompt", {}, "\nJSON.", "llama3.1", 4000)

    assert captured["payload"]["keep_alive"] == "45m"


def test_warm_up_ollama_noop_for_other_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le préchauffage n'a de sens que pour ollama (serveur local à
    précharger) — pas d'appel réseau superflu pour openai/mistral."""
    monkeypatch.setenv("AI_PROVIDER", "openai")

    def fail_if_called(req, timeout=None):
        raise AssertionError("urlopen ne devrait pas être appelé")

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fail_if_called)
    ai_common.warm_up_ollama()  # ne lève pas, ne fait rien


def test_warm_up_ollama_sends_empty_messages_to_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requête sans `messages` : Ollama charge le modèle en mémoire sans
    générer de texte — précisément ce qui manquait pour que le premier appel
    réel de l'utilisateur n'en paie pas le coût (même principe que
    `audio_transcribe.warm_up()` pour Whisper)."""
    import json as json_module

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["payload"] = json_module.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("SYNTHESE_MODEL", "llama3.1")

    ai_common.warm_up_ollama()

    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["payload"]["messages"] == []
    assert captured["payload"]["model"] == "llama3.1"
    assert captured["payload"]["keep_alive"] == "30m"


def test_warm_up_ollama_propagates_errors_to_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ne doit PAS avaler l'exception elle-même — c'est l'appelant
    (`app.main.lifespan`, comme pour `audio_transcribe.warm_up()`) qui
    encadre l'appel d'un try/except pour ne jamais bloquer le démarrage du
    serveur si Ollama n'est pas encore lancé."""
    monkeypatch.setenv("AI_PROVIDER", "ollama")

    def fake_urlopen(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OSError):
        ai_common.warm_up_ollama()


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
