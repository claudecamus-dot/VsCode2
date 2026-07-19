"""Plomberie IA partagée — dégradation gracieuse, multi-fournisseur.

Factorisé depuis `synthese_ai.py` : `trame_extract_ai.py` et
`interview_extract_ai.py` ont besoin exactement des mêmes helpers
(configuration, appel SDK, parsing JSON, messages d'erreur lisibles).

Le fournisseur est choisi par la variable d'environnement `AI_PROVIDER`
(`ollama` par défaut — local, gratuit — ou `openai` / `mistral`) — un seul
fournisseur actif à la fois, pas de repli automatique de l'un vers l'autre :
si la clé du fournisseur configuré manque, l'appelant obtient un message
clair plutôt qu'un essai silencieux sur un autre fournisseur (comportement
surprenant, coût/latence doublés). `call_ai_json()` est le point d'entrée
unique : les modules appelants ne connaissent plus le SDK sous-jacent.

`ollama` (2026-07-15) tourne en local (serveur HTTP sur la machine, aucune
donnée envoyée à l'extérieur) — seul fournisseur qui rend possible une
analyse d'entretien libre (`interview_libre_extract_ai.py`) sans connexion à
une IA externe, en complément de la transcription déjà locale
(`audio_transcribe.py`, faster-whisper). Nécessite qu'Ollama tourne sur le
poste (https://ollama.com) et qu'un modèle soit déjà tiré (`ollama pull
<modèle>`) — sinon `call_ai_json()` échoue avec un message explicite plutôt
qu'un plantage brut. Pas de SDK à proprement parler : requêtes HTTP via
`urllib` (stdlib), donc zéro dépendance Python supplémentaire. Qualité
d'extraction JSON structurée nettement en retrait par rapport à
Claude/GPT-4/Mistral-large sur un modèle 7-8B — à valider en pratique,
un modèle plus costaud (14B+) donne de meilleurs résultats si le poste suit.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    # Pas un vrai secret (serveur local sans authentification) — adresse du
    # serveur Ollama, optionnelle (défaut http://localhost:11434). Traité à
    # part dans is_configured()/call_ai_json(), qui ne l'exigent pas comme
    # ils exigeraient une vraie clé API.
    "ollama": "OLLAMA_HOST",
}

# Modèles par défaut, surchargeables par la variable d'environnement
# SYNTHESE_MODEL quel que soit le fournisseur actif.
_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "mistral": "mistral-large-latest",
    "ollama": "llama3.1",
}


class AIError(RuntimeError):
    """Erreur fonctionnelle d'appel IA — le message est destiné à l'UI."""


def _openai():
    try:
        import openai

        return openai
    except ModuleNotFoundError:
        return None


def _mistral():
    try:
        from mistralai.client import Mistral

        return Mistral
    except ModuleNotFoundError:
        return None


def _ollama():
    """Toujours "disponible" : pas de SDK à installer (requêtes HTTP via la
    stdlib). La vraie question — le serveur répond-il ? — se vérifie à
    l'appel (voir `_call_ollama`), pas ici, comme pour les autres
    fournisseurs (dont la clé est vérifiée statiquement mais pas testée)."""
    return True


_SDK_LOADERS = {"openai": _openai, "mistral": _mistral, "ollama": _ollama}


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def ollama_num_ctx() -> int:
    """Fenêtre de contexte Ollama (tokens) — 8192 par défaut. Sans ce réglage
    explicite, Ollama retombe sur son défaut de 2048 tokens, qui tronque
    silencieusement (sans erreur) tout prompt plus long — un piège réel sur
    une transcription d'entretien un peu longue. Surchargeable par
    `OLLAMA_NUM_CTX` si un modèle/poste supporte plus."""
    try:
        return int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
    except ValueError:
        return 8192


def ollama_timeout() -> int:
    """Délai HTTP (secondes) avant abandon d'un appel Ollama — 300s par
    défaut. Un `num_ctx` plus grand ou un CPU sans GPU dédié peuvent pousser
    un appel bien au-delà de l'ancien délai fixe de 180s. Surchargeable par
    `OLLAMA_TIMEOUT`."""
    try:
        return int(os.environ.get("OLLAMA_TIMEOUT", "300"))
    except ValueError:
        return 300


def ollama_chunk_max_words() -> int:
    """Taille de tronçon (mots) pour le découpage map-reduce de
    `interview_libre_extract_ai.py` — 1800 par défaut. Sur un poste CPU sans
    GPU dédié, un tronçon de cette taille peut dépasser `ollama_timeout()`
    avec un gros modèle ; réduire cette valeur diminue le temps par appel (au
    prix de plus d'appels). Surchargeable par `OLLAMA_CHUNK_MAX_WORDS`."""
    try:
        return int(os.environ.get("OLLAMA_CHUNK_MAX_WORDS", "1800"))
    except ValueError:
        return 1800


def chunk_text_by_paragraph(text: str, max_words: int) -> list[str]:
    """Découpe un texte long en tronçons d'environ `max_words` mots, sur des
    frontières de paragraphe (ligne vide) pour ne jamais couper une idée au
    milieu. Un seul tronçon si le texte tient déjà dedans — le chemin court
    (un seul appel IA) reste inchangé. Partagée entre `interview_libre_extract_ai.py`
    (transcription → tours de parole) et `interview_extract_ai.py` (transcription
    → réponses en mode structuré, depuis 2026-07-19) : même risque de
    dépassement de `ollama_num_ctx()`/`ollama_timeout()` sur un texte long,
    même découpage."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for para in paragraphs:
        words = len(para.split())
        if current and current_words + words > max_words:
            chunks.append("\n\n".join(current))
            current, current_words = [], 0
        current.append(para)
        current_words += words
    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text]


def ollama_keep_alive() -> str:
    """Durée pendant laquelle Ollama garde le modèle chargé en mémoire après
    un appel — `"30m"` par défaut (le défaut serveur d'Ollama est 5 minutes,
    trop court pour un usage interactif normal : le temps de relire un écran
    de revue avant de cliquer sur l'étape suivante suffit à faire décharger
    le modèle, qui recharge alors « à froid » sur l'appel suivant — c'est
    précisément ce qui produit un `OLLAMA_TIMEOUT` alors que la génération
    elle-même prend quelques secondes une fois le modèle chaud, cf.
    `warm_up_ollama()`). Surchargeable par `OLLAMA_KEEP_ALIVE` (accepte le
    format Ollama : `"10m"`, `"1h"`, `-1` pour ne jamais décharger)."""
    return os.environ.get("OLLAMA_KEEP_ALIVE", "").strip() or "30m"


def active_provider() -> str:
    """Fournisseur configuré — `AI_PROVIDER`, replié sur `ollama` (local,
    gratuit) si absent ou non reconnu (ne jamais planter sur une variable mal
    renseignée). Lu à chaque appel (pas mis en cache au chargement du module)
    pour rester testable et pour refléter un changement d'environnement à
    chaud."""
    provider = os.environ.get("AI_PROVIDER", "ollama").strip().lower()
    return provider if provider in _SDK_LOADERS else "ollama"


def active_model() -> str:
    provider = active_provider()
    return os.environ.get("SYNTHESE_MODEL", "").strip() or _DEFAULT_MODELS[provider]


def api_key_env_name() -> str:
    """Nom de la variable d'environnement attendue pour le fournisseur actif —
    utilisé pour des messages d'UI qui restent corrects quel que soit `AI_PROVIDER`."""
    return _API_KEY_ENV[active_provider()]


def is_configured() -> bool:
    """Vrai si une génération IA réelle est possible (SDK du fournisseur actif
    installé + sa clé API présente). Ollama n'a pas de clé à proprement
    parler (serveur local) — seul le SDK (toujours "installé") compte ; la
    disponibilité réelle du serveur n'est vérifiée qu'à l'appel."""
    provider = active_provider()
    sdk = _SDK_LOADERS[provider]()
    if provider == "ollama":
        return sdk is not None
    return sdk is not None and bool(os.environ.get(_API_KEY_ENV[provider]))


def demo_enabled() -> bool:
    """Vrai si le mode démo hors-ligne (sans IA, sans clé) est activé."""
    return os.environ.get("SYNTHESE_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def _friendly(exc) -> str:
    name = type(exc).__name__
    if "Authentication" in name or "PermissionDenied" in name:
        return "Clé API refusée (authentification)."
    if "RateLimit" in name:
        return "Limite de débit atteinte — réessayez dans un instant."
    if "Connection" in name or "Timeout" in name:
        return "Impossible de joindre l'API IA (réseau)."
    return f"Erreur lors de l'appel à l'IA : {getattr(exc, 'message', None) or exc}"


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise AIError("Réponse IA non exploitable (JSON invalide).")


def _call_openai(system: str, prompt: str, schema: dict, json_hint: str, model: str, max_tokens: int) -> str:
    openai = _openai()
    client = openai.OpenAI()
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system + json_hint},
            {"role": "user", "content": prompt},
        ],
    )
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "content_filter":
        raise AIError("Génération refusée par le modèle.")
    return choice.message.content or ""


def _call_mistral(system: str, prompt: str, schema: dict, json_hint: str, model: str, max_tokens: int) -> str:
    Mistral = _mistral()
    client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY"))
    resp = client.chat.complete(
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system + json_hint},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content or ""


def _call_ollama(system: str, prompt: str, schema: dict, json_hint: str, model: str, max_tokens: int) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system + json_hint},
            {"role": "user", "content": prompt},
        ],
        "format": "json",
        "stream": False,
        "keep_alive": ollama_keep_alive(),
        "options": {"num_predict": max_tokens, "num_ctx": ollama_num_ctx()},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_host()}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout_msg = (
        "Ollama n'a pas répondu à temps — le modèle est peut-être trop "
        "gros pour ce poste, ou en cours de chargement (premier appel, ou "
        "rechargement après une pause : le serveur précharge le modèle à "
        "son démarrage, mais Ollama le décharge après OLLAMA_KEEP_ALIVE "
        "d'inactivité, 30 minutes par défaut). Si cela se reproduit : "
        "augmentez OLLAMA_TIMEOUT ou OLLAMA_KEEP_ALIVE, réduisez "
        "OLLAMA_CHUNK_MAX_WORDS (voir .env.example), ou choisissez un "
        "modèle plus léger (SYNTHESE_MODEL)."
    )
    try:
        with urllib.request.urlopen(req, timeout=ollama_timeout()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        # Un timeout de lecture arrive enveloppé dans URLError(reason=timeout) :
        # sans ce test, l'UI dirait « vérifiez qu'Ollama tourne » alors qu'il
        # tourne — il est juste trop lent (cas réel sur poste CPU, 2026-07-17).
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise AIError(timeout_msg) from exc
        raise AIError(
            f"Impossible de joindre Ollama sur {ollama_host()} — vérifiez qu'il "
            f"tourne (`ollama serve`) et qu'un modèle est disponible "
            f"(`ollama pull {model}`)."
        ) from exc
    except TimeoutError as exc:
        raise AIError(timeout_msg) from exc
    if "error" in data:
        raise AIError(f"Erreur Ollama : {data['error']}")
    return (data.get("message") or {}).get("content") or ""


_CALLERS = {
    "openai": _call_openai,
    "mistral": _call_mistral,
    "ollama": _call_ollama,
}


def call_ai_json(
    system: str,
    prompt: str,
    schema: dict,
    json_hint: str,
    *,
    max_tokens: int = 2000,
    error_cls: type[AIError] = AIError,
) -> dict:
    """Appel IA générique, sortie JSON structurée, quel que soit le
    fournisseur actif (`AI_PROVIDER`). Lève `error_cls` (une sous-classe
    d'`AIError` fournie par l'appelant, pour que les routeurs puissent
    continuer à distinguer synthèse / extraction trame / extraction
    entretien par type d'exception) avec un message toujours lisible côté
    UI, y compris si le SDK du fournisseur diffère de ce qui est attendu ici
    (garde-fou générique en plus des erreurs connues)."""
    provider = active_provider()
    sdk = _SDK_LOADERS[provider]()
    if sdk is None:
        raise error_cls(f"Le SDK « {provider} » n'est pas installé.")
    if provider != "ollama":
        key_env = _API_KEY_ENV[provider]
        if not os.environ.get(key_env):
            raise error_cls(f"Clé absente : définissez {key_env} pour activer la génération IA.")

    try:
        text = _CALLERS[provider](system, prompt, schema, json_hint, active_model(), max_tokens)
    except AIError as exc:
        raise error_cls(str(exc)) from exc
    except Exception as exc:  # garde-fou : jamais de 500 brute sur un appel IA
        raise error_cls(_friendly(exc)) from exc

    try:
        return _parse_json(text)
    except AIError as exc:
        raise error_cls(str(exc)) from exc


def warm_up_ollama() -> None:
    """Charge le modèle en mémoire dès le démarrage du serveur (fournisseur
    ollama actif seulement), pour que le premier appel réel de l'utilisateur
    n'en paie pas le coût — même principe que `audio_transcribe.warm_up()`
    pour Whisper. Requête sans `messages` : Ollama charge le modèle et
    répond immédiatement, sans générer de texte (documenté par Ollama comme
    façon de précharger un modèle). Silencieuse en cas d'échec (Ollama pas
    encore démarré, serveur injoignable) — le premier appel réel retentera
    et remontera l'erreur normale (`_call_ollama`) le cas échéant."""
    if active_provider() != "ollama":
        return
    payload = json.dumps({
        "model": active_model(), "messages": [], "keep_alive": ollama_keep_alive(),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_host()}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=ollama_timeout()):
        pass
