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


def test_ollama_active_model_defaults_to_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut ollama = qwen2.5:3b-instruct depuis le 2026-07-22 : llama3.1:8b était
    inutilisable sur CPU (0 tour / timeout, cf. tests/test_ollama_integration.py)."""
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("SYNTHESE_MODEL", raising=False)
    assert ai_common.active_model() == "qwen2.5:3b-instruct"


def test_call_ai_json_dispatches_to_ollama_without_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    def fake_ollama_call(system, prompt, schema, json_hint, model, max_tokens):
        assert model == "qwen2.5:3b-instruct"
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
    monkeypatch.delenv("OLLAMA_TEMPERATURE", raising=False)

    ai_common._call_ollama("sys", "prompt", {}, "\nJSON.", "qwen2.5:3b-instruct", 4000)

    assert captured["payload"]["options"]["num_ctx"] == 8192
    assert captured["payload"]["options"]["num_predict"] == 4000
    # Température basse envoyée à Ollama (défaut 0.2, pas le 0.8 d'Ollama) — extraction
    # fidèle/reproductible, correctif du 0-tour intermittent (2026-07-22).
    assert captured["payload"]["options"]["temperature"] == 0.2
    assert captured["timeout"] == 300


def test_ollama_temperature_defaults_to_02(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_TEMPERATURE", raising=False)
    assert ai_common.ollama_temperature() == 0.2
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0")
    assert ai_common.ollama_temperature() == 0.0


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


def test_ollama_chunk_max_words_defaults_to_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 par défaut depuis le 2026-07-19 (mesuré en réel : 1800 mots — l'ancien
    défaut — prend ~570s à chaud, quasi le double d'ollama_timeout()=300s)."""
    monkeypatch.delenv("OLLAMA_CHUNK_MAX_WORDS", raising=False)
    assert ai_common.ollama_chunk_max_words() == 400
    monkeypatch.setenv("OLLAMA_CHUNK_MAX_WORDS", "250")
    assert ai_common.ollama_chunk_max_words() == 250


# --------------------------------------------------------------------------- #
# Découpage map-reduce (chunk_text_by_paragraph) — GARANTIE : aucun tronçon ne
# dépasse max_words, y compris quand UN SEUL paragraphe est plus long que la
# limite (cas qui produisait un « tronçon trop volumineux » / timeout Ollama
# même après avoir baissé OLLAMA_CHUNK_MAX_WORDS — correctif 2026-07-20).
# --------------------------------------------------------------------------- #
def _words(chunk: str) -> int:
    return len(chunk.split())


def test_chunk_splits_a_single_oversized_paragraph() -> None:
    """Un seul paragraphe de 300 mots avec une limite de 100 doit produire
    plusieurs tronçons, AUCUN ne dépassant la limite — avant le correctif il
    restait un unique tronçon de 300 mots qui ignorait complètement la limite."""
    para = " ".join(f"mot{i}" for i in range(300))  # 1 seul paragraphe, pas de \n\n
    chunks = ai_common.chunk_text_by_paragraph(para, 100)
    assert len(chunks) >= 3
    assert all(_words(c) <= 100 for c in chunks)
    # Aucun mot perdu ni dupliqué dans le découpage.
    assert " ".join(chunks).split() == para.split()


def test_chunk_never_exceeds_max_words_across_mixed_paragraphs() -> None:
    """Mélange de paragraphes courts et d'un paragraphe géant : tous les
    tronçons restent ≤ max_words (la version précédente pouvait dépasser de la
    taille d'un paragraphe entier, voire d'un paragraphe géant complet)."""
    court = "Consultant : une remarque brève."
    geant = " ".join(f"w{i}" for i in range(250))
    text = f"{court}\n\n{geant}\n\n{court}"
    chunks = ai_common.chunk_text_by_paragraph(text, 60)
    assert chunks, "au moins un tronçon"
    assert all(_words(c) <= 60 for c in chunks)


def test_chunk_keeps_short_text_as_single_chunk() -> None:
    """Le chemin court (un seul appel IA) reste inchangé pour un texte qui
    tient déjà sous la limite."""
    text = "Para un.\n\nPara deux."
    assert ai_common.chunk_text_by_paragraph(text, 400) == [text]


def test_chunk_zero_max_words_does_not_crash() -> None:
    """Un réglage aberrant (0) ne doit jamais lever `ValueError` (range step 0)
    — on borne à 1 mot minimum plutôt que de planter l'extraction."""
    chunks = ai_common.chunk_text_by_paragraph("un deux trois", 0)
    assert all(_words(c) <= 1 for c in chunks)
    assert " ".join(chunks).split() == ["un", "deux", "trois"]


def test_call_ollama_retries_once_on_timeout_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un timeout isolé (pic de charge, rechargement inattendu) ne doit pas
    faire échouer tout de suite — une relance ciblée peut réussir."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("premier appel trop lent")
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return b'{"message": {"content": "{}"}}'
        return FakeResponse()

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    result = ai_common._call_ollama("sys", "prompt", {}, "\nJSON.", "llama3.1", 100)
    assert result == "{}"
    assert len(calls) == 2, "doit avoir relancé exactement une fois"


def test_call_ollama_gives_up_after_second_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un tronçon structurellement trop gros échoue de la même façon aux deux
    tentatives — pas de boucle infinie, message orienté vers le vrai levier
    (OLLAMA_CHUNK_MAX_WORDS), pas seulement « premier appel »."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise TimeoutError("toujours trop lent")

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ai_common.AIError, match="OLLAMA_CHUNK_MAX_WORDS"):
        ai_common._call_ollama("sys", "prompt", {}, "\nJSON.", "llama3.1", 100)
    assert len(calls) == 2, "exactement 2 tentatives, jamais plus"


def test_call_ollama_connection_refused_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contrairement à un timeout, un serveur injoignable ne se rétablit pas
    tout seul entre deux appels immédiats — pas de relance gaspillée."""
    import urllib.error

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ai_common.AIError, match="Impossible de joindre Ollama"):
        ai_common._call_ollama("sys", "prompt", {}, "\nJSON.", "llama3.1", 100)
    assert len(calls) == 1, "pas de relance sur un serveur injoignable"


# --------------------------------------------------------------------------- #
# Redécoupage sur timeout (2026-09-04) — cas réel : sur 4 tranches d'un
# entretien, une seule dépassait le délai ; la relance identique de
# `_call_ollama` échouait pareil, et l'entretien entier devenait non
# enregistrable. Le message renvoyait alors régler OLLAMA_CHUNK_MAX_WORDS à la
# main, globalement, pour un seul tronçon — le code sait le faire lui-même.
# --------------------------------------------------------------------------- #
def test_troncons_degressifs_ne_decoupe_pas_quand_l_appel_aboutit() -> None:
    """Chemin nominal inchangé : un seul appel, un seul résultat."""
    vus = []

    def appel(chunk):
        vus.append(chunk)
        return {"ok": chunk}

    texte = " ".join(f"mot{i}" for i in range(300))
    assert ai_common.call_par_troncons_degressifs(texte, appel) == [{"ok": texte}]
    assert vus == [texte]


def test_troncons_degressifs_redecoupe_le_troncon_qui_timeoute() -> None:
    """Le tronçon entier dépasse le délai, ses moitiés passent : on rend les
    deux résultats au lieu de laisser échouer la tranche."""
    vus = []

    def appel(chunk):
        vus.append(chunk)
        if len(chunk.split()) > 200:
            raise ai_common._timeout_error("Ollama n'a pas répondu à temps")
        return {"mots": len(chunk.split())}

    texte = " ".join(f"mot{i}" for i in range(400))
    resultats = ai_common.call_par_troncons_degressifs(texte, appel)
    assert [r["mots"] for r in resultats] == [200, 200]
    assert len(vus) == 3, "1 tentative entière + 2 moitiés"


def test_troncons_degressifs_sur_compte_impair_rend_deux_moities_pas_trois() -> None:
    """`_split_en_deux_moities` coupe en EXACTEMENT deux, y compris sur un
    compte de mots impair (bmad-code-review 2026-09-04, finding F10) : l'ancien
    `chunk_text_by_paragraph(chunk, mots // 2)` bornait chaque bloc à `mots // 2`
    au lieu d'imposer deux blocs, et rendait un résidu d'UN mot en tronçon à
    part — reparti en appel IA complet, sans jamais y trouver de tour."""
    vus = []

    def appel(chunk):
        vus.append(chunk)
        if len(chunk.split()) > 200:
            raise ai_common._timeout_error("Ollama n'a pas répondu à temps")
        return {"mots": len(chunk.split())}

    texte = " ".join(f"mot{i}" for i in range(399))
    resultats = ai_common.call_par_troncons_degressifs(texte, appel)
    assert len(resultats) == 2, "un résidu isolé produirait un 3e résultat"
    assert sorted(r["mots"] for r in resultats) == [199, 200]
    assert len(vus) == 3, "1 tentative entière + exactement 2 moitiés"


def test_troncons_degressifs_ne_redecoupe_pas_une_erreur_qui_n_est_pas_un_timeout() -> None:
    """Un JSON invalide ou un serveur injoignable ne se répare pas en coupant :
    l'erreur remonte telle quelle, sans multiplier les appels."""
    vus = []

    def appel(chunk):
        vus.append(chunk)
        raise ai_common.AIError("Réponse IA non exploitable (JSON invalide).")

    texte = " ".join(f"mot{i}" for i in range(400))
    with pytest.raises(ai_common.AIError, match="JSON invalide"):
        ai_common.call_par_troncons_degressifs(texte, appel)
    assert len(vus) == 1


def test_troncons_degressifs_s_arrete_au_plancher_de_mots() -> None:
    """Sous le plancher, un timeout ne vient plus de la taille : couper encore
    ne ferait que multiplier les appels lents. L'erreur remonte."""
    vus = []

    def appel(chunk):
        vus.append(chunk)
        raise ai_common._timeout_error("trop lent quoi qu'il arrive")

    texte = " ".join(f"mot{i}" for i in range(50))
    with pytest.raises(ai_common.AIError, match="trop lent"):
        ai_common.call_par_troncons_degressifs(texte, appel)
    assert len(vus) == 1, "aucun redécoupage sous le plancher"


def test_troncons_degressifs_borne_la_profondeur() -> None:
    """Un poste qui timeoute à toutes les tailles ne déclenche pas une
    récursion sans fin : UN seul niveau de découpage (bmad-code-review
    2026-09-04, finding F4 — 2 niveaux pouvaient multiplier par 3-4 le pire cas
    synchrone que `RECUP_TRANCHES_MAX` bornait ailleurs), et le premier
    sous-tronçon qui échoue arrête tout (800 → 400, soit 2 appels : le tronçon
    entier puis sa première moitié).

    Cet abandon franc est voulu : rendre les moitiés déjà obtenues ferait
    perdre le reste SANS que personne le compte, alors qu'une tranche en échec,
    elle, est comptée et signalée à l'écran."""
    vus = []

    def appel(chunk):
        vus.append(chunk)
        raise ai_common._timeout_error("Ollama n'a pas répondu à temps")

    texte = " ".join(f"mot{i}" for i in range(800))
    with pytest.raises(ai_common.AIError):
        ai_common.call_par_troncons_degressifs(texte, appel)
    assert [len(c.split()) for c in vus] == [800, 400]


def test_call_ai_json_propage_le_drapeau_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le ré-emballage dans la classe de l'appelant ne doit pas perdre le
    signal : sans lui, l'extracteur ne peut plus distinguer un tronçon trop
    lourd (redécoupable) d'une panne qu'un redécoupage ne réparerait pas."""
    class ErreurAppelant(ai_common.AIError):
        pass

    def fake_urlopen(req, timeout=None):
        raise TimeoutError("trop lent")

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ErreurAppelant) as exc:
        ai_common.call_ai_json("sys", "prompt", {}, "\nJSON.", error_cls=ErreurAppelant)
    assert exc.value.timeout is True
