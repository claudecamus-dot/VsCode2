"""Extraction IA d'un entretien en mode libre (incr.9), en DEUX étapes
séparées (US9.16, 2026-07-16) — décision antérieure (US9.5 : un seul appel,
un seul écran de revue sur toutes les sorties à la fois) explicitement
révisée à la demande du consultant, qui veut valider la matière factuelle
avant de laisser l'IA en tirer une synthèse interprétative :

1. `extract_turns_from_text` : depuis la transcription brute, structure la
   conversation en tours de parole (interlocuteur/question/remarque)
   groupés en sections thématiques (section_title), pour l'écran Analyse
   (rendu façon transcription structurée/éditée) et l'export Markdown
   (US9.7) ; relève aussi l'identité de l'interviewé·e si elle ressort de
   la transcription (auto-présentation en début d'entretien, typiquement).
   Revue/correction humaine de ces tours avant l'étape 2 (écran
   `libre_turns_review.html`).
2. `generate_repartition_from_turns` : à partir des tours de parole déjà
   validés (pas de la transcription brute — pour que toute correction faite
   à l'étape 1 se répercute dans la synthèse), répartit le contenu dans les
   5 catégories de `GlobalSynthesis` (même forme que
   `synthese_ai.GLOBAL_SCHEMA`) + un résumé court.

Map-reduce (US9.17, 2026-07-16) : un entretien de 1h30-3h transcrit fait
plusieurs dizaines de milliers de mots — largement au-delà de ce qu'un seul
appel peut traiter sans dépasser la fenêtre de contexte du modèle
(`ai_common.ollama_num_ctx()`, 8192 tokens par défaut ; au-delà, Ollama
tronque silencieusement le prompt, sans erreur — piège réel identifié lors
du cadrage perf du 2026-07-16). Les deux étapes découpent donc leur entrée
en tronçons (map), traitent chaque tronçon séparément, puis fusionnent
(reduce) : concaténation simple pour les tours de parole (l'ordre
chronologique EST le bon assemblage, pas besoin d'un appel IA de plus) et
premier résultat non vide pour l'identité (l'auto-présentation arrive
presque toujours au début) ; pour la répartition/résumé, un appel de
réduction dédié fusionne les synthèses partielles en une seule cohérente
(la concaténation brute donnerait un résumé répété N fois plutôt qu'un vrai
résumé unique). Un texte qui tient dans un seul tronçon ne fait qu'un appel
— le chemin court reste inchangé.

Même plomberie que `interview_extract_ai.py`/`synthese_ai.py`
(`ai_common.call_ai_json`).
"""
from __future__ import annotations

from .ai_common import AIError, call_ai_json, chunk_text_by_paragraph, ollama_chunk_max_words

MAX_TOKENS = 4000

REPARTITION_KEYS = (
    "contexte", "culture_adn", "forces_succes", "points_amelioration", "aspirations",
)
# Nombre de tours de parole par tronçon à l'étape 2 — un tour est bien plus
# court qu'un paragraphe de transcription brute, la limite est donc en
# nombre de tours plutôt qu'en mots.
_CHUNK_MAX_TURNS = 40


class InterviewLibreExtractAIError(AIError):
    """Erreur fonctionnelle d'extraction IA (mode libre) — message UI."""


def _safe_str(value) -> str:
    """Coerce une valeur JSON en chaîne, sans jamais planter : Ollama (`format:
    "json"` garantit du JSON valide, pas la forme exacte du schéma demandé) a
    déjà renvoyé un objet imbriqué là où une simple chaîne était attendue pour
    une catégorie de répartition (crash 500 réel observé le 2026-07-17 sur une
    transcription réelle, `.strip()` sur un dict). Une valeur du mauvais type
    est traitée comme absente plutôt que remontée telle quelle (un repr de
    dict Python affiché à l'écran serait pire qu'un champ vide)."""
    return value.strip() if isinstance(value, str) else ""


def _safe_dict(value) -> dict:
    """Coerce une valeur JSON en dict, sinon `{}` — même esprit que `_safe_str`.
    Ollama garantit du JSON *valide* (format json), pas la *forme* du schéma :
    il a déjà renvoyé un tableau nu là où l'objet racine `{"turns": ...}` était
    attendu (crash réel `'list' object has no attribute 'get'` observé le
    2026-07-17 sur `split_03.weba`), et un dict imbriqué là où une chaîne était
    attendue (cf. `_safe_str`). On coerce plutôt que de planter : un champ mal
    typé est traité comme absent."""
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
# Étape 1 — tours de parole (questions/réponses) + identité
# --------------------------------------------------------------------------- #
_TURNS_SYSTEM = (
    "Tu es consultant·e senior. On te donne la transcription brute d'un "
    "entretien libre (pas de questionnaire prédéfini). Fais deux choses à "
    "partir de ce seul texte, fidèlement, sans rien inventer :\n"
    "1. turns : découpe la conversation en tours de parole successifs. Pour "
    "chaque tour, indique l'interlocuteur (qui parle — nom, rôle ou "
    "« Consultant·e » si identifiable, sinon une étiquette générique), et "
    "selon la nature du propos, question (une question posée) et/ou "
    "remarque (une déclaration, un constat, une réponse). Un tour peut "
    "n'avoir qu'un des deux. Regroupe aussi la conversation en sections "
    "thématiques : quand un tour ouvre un nouveau sujet, donne-lui un "
    "section_title court (ex. « Recueillir et transcrire l'information ») ; "
    "les tours suivants qui continuent le même sujet laissent section_title "
    "vide (ils héritent de la dernière section ouverte).\n"
    "2. identite : si la personne interviewée donne son nom/prénom, son "
    "rôle/fonction, et/ou son équipe ou département (typiquement en se "
    "présentant en début d'entretien), relève-les. Laisse un champ vide "
    "s'il n'est pas mentionné explicitement — ne devine jamais un nom à "
    "partir du style de parole."
)

_TURNS_SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "interlocuteur": {"type": "string"},
                    "question": {"type": "string"},
                    "remarque": {"type": "string"},
                    "section_title": {"type": "string"},
                },
                "required": ["interlocuteur"],
                "additionalProperties": False,
            },
        },
        "identite": {
            "type": "object",
            "properties": {
                "interviewee_name": {"type": "string"},
                "interviewee_role": {"type": "string"},
                "interviewee_entity": {"type": "string"},
            },
            "required": ["interviewee_name", "interviewee_role", "interviewee_entity"],
            "additionalProperties": False,
        },
    },
    "required": ["turns", "identite"],
    "additionalProperties": False,
}

_TURNS_JSON_HINT = (
    '\nRéponds UNIQUEMENT par un objet JSON aux clés "turns" (liste de '
    '{"interlocuteur", "question"?, "remarque"?, "section_title"?}) et '
    '"identite" (objet aux clés interviewee_name/interviewee_role/'
    'interviewee_entity, chaîne vide si non mentionné).'
)


def _extract_turns_chunk(chunk: str) -> dict:
    data = call_ai_json(
        _TURNS_SYSTEM,
        f"TRANSCRIPTION :\n{chunk}",
        _TURNS_SCHEMA,
        _TURNS_JSON_HINT,
        max_tokens=MAX_TOKENS,
        error_cls=InterviewLibreExtractAIError,
    )

    # Ollama peut renvoyer soit `{"turns": [...], "identite": {...}}` (schéma
    # demandé), soit — observé sur du réel (`split_03.weba`) — un tableau nu
    # qui EST directement la liste des tours. On récupère les deux formes
    # plutôt que de planter sur `data.get(...)` quand `data` est une liste.
    if isinstance(data, list):
        rows, identite_raw = data, {}
    else:
        data = _safe_dict(data)
        rows = data.get("turns")
        rows = rows if isinstance(rows, list) else []
        identite_raw = _safe_dict(data.get("identite"))

    turns = []
    for row in rows:
        row = _safe_dict(row)
        interlocuteur = _safe_str(row.get("interlocuteur"))
        question = _safe_str(row.get("question")) or None
        remarque = _safe_str(row.get("remarque")) or None
        section_title = _safe_str(row.get("section_title")) or None
        if not interlocuteur or (question is None and remarque is None):
            continue
        turns.append({
            "interlocuteur": interlocuteur,
            "question": question,
            "remarque": remarque,
            "section_title": section_title,
        })

    identity = {
        key: _safe_str(identite_raw.get(key))
        for key in ("interviewee_name", "interviewee_role", "interviewee_entity")
    }

    return {"turns": turns, "identity": identity}


def extract_turns_from_text(text: str) -> dict:
    """Retourne `{"turns": [{"interlocuteur", "question", "remarque",
    "section_title"}, ...], "identity": {interviewee_name, interviewee_role,
    interviewee_entity}}` (chaînes vides si non détecté, jamais `None`).
    Découpe `text` en tronçons (map) si nécessaire — les tours de chaque
    tronçon se concatènent dans l'ordre (la conversation est déjà
    chronologique, pas besoin d'un appel de fusion) ; la première identité
    non vide rencontrée l'emporte (l'auto-présentation arrive presque
    toujours en tout début d'entretien). Lève `InterviewLibreExtractAIError`."""
    if not text.strip():
        raise InterviewLibreExtractAIError("Aucun texte transcrit.")

    chunks = chunk_text_by_paragraph(text, ollama_chunk_max_words())

    all_turns: list[dict] = []
    identity = {"interviewee_name": "", "interviewee_role": "", "interviewee_entity": ""}

    for chunk in chunks:
        result = _extract_turns_chunk(chunk)
        all_turns.extend(result["turns"])
        if not any(identity.values()) and any(result["identity"].values()):
            identity = result["identity"]

    if not all_turns:
        raise InterviewLibreExtractAIError(
            "Aucun tour de parole détecté dans la transcription."
        )

    return {"turns": all_turns, "identity": identity}


# --------------------------------------------------------------------------- #
# Étape 2 — répartition dans les 5 catégories + résumé, depuis les tours
# de parole déjà validés (pas la transcription brute).
# --------------------------------------------------------------------------- #
_SYNTHESE_SYSTEM = (
    "Tu es consultant·e senior. On te donne les tours de parole déjà "
    "structurés et relus (questions/réponses) d'un entretien libre. Fais "
    "deux choses à partir de cette seule matière, fidèlement, sans rien "
    "inventer :\n"
    "1. repartition : répartis la matière de l'entretien dans les 5 "
    "catégories contexte / culture_adn / forces_succes / "
    "points_amelioration / aspirations (mêmes définitions qu'une synthèse "
    "de mission classique). Si une catégorie manque de matière dans cet "
    "entretien, indique-le brièvement plutôt que de combler artificiellement.\n"
    "2. resume : un résumé de 1 à 3 phrases de l'entretien dans son "
    "ensemble — le message central à retenir, pas un simple sommaire des "
    "sujets abordés."
)

_SYNTHESE_SCHEMA = {
    "type": "object",
    "properties": {
        "repartition": {
            "type": "object",
            "properties": {
                "contexte": {"type": "string"},
                "culture_adn": {"type": "string"},
                "forces_succes": {"type": "string"},
                "points_amelioration": {"type": "string"},
                "aspirations": {"type": "string"},
            },
            "required": [
                "contexte", "culture_adn", "forces_succes",
                "points_amelioration", "aspirations",
            ],
            "additionalProperties": False,
        },
        "resume": {"type": "string"},
    },
    "required": ["repartition", "resume"],
    "additionalProperties": False,
}

_SYNTHESE_JSON_HINT = (
    '\nRéponds UNIQUEMENT par un objet JSON aux clés "repartition" (objet '
    'aux 5 clés contexte/culture_adn/forces_succes/points_amelioration/'
    'aspirations) et "resume" (chaîne, 1-3 phrases).'
)


def _turns_to_text(turns: list[dict]) -> str:
    """Reconstruit un texte lisible depuis les tours de parole validés, pour
    servir de matière à l'étape 2 — plutôt que de repasser sur la
    transcription brute, qui ignorerait les corrections faites en revue."""
    lines = []
    for turn in turns:
        section_title = turn.get("section_title")
        if section_title:
            lines.append(f"[{section_title}]")
        propos = turn.get("question") or turn.get("remarque") or ""
        lines.append(f"{turn.get('interlocuteur', '')} : {propos}")
    return "\n".join(lines)


def _chunk_turns(turns: list[dict], max_turns: int) -> list[list[dict]]:
    return [turns[i:i + max_turns] for i in range(0, len(turns), max_turns)] or [[]]


def _call_synthese(turns_text: str, extrait_hint: str = "") -> dict:
    data = call_ai_json(
        _SYNTHESE_SYSTEM,
        f"TOURS DE PAROLE{extrait_hint} :\n{turns_text}",
        _SYNTHESE_SCHEMA,
        _SYNTHESE_JSON_HINT,
        max_tokens=MAX_TOKENS,
        error_cls=InterviewLibreExtractAIError,
    )
    data = _safe_dict(data)
    repartition_raw = _safe_dict(data.get("repartition"))
    repartition = {
        key: _safe_str(repartition_raw.get(key))
        for key in REPARTITION_KEYS
    }
    resume = _safe_str(data.get("resume"))
    return {"repartition": repartition, "resume": resume}


_REDUCE_SYSTEM = (
    "Tu es consultant·e senior. On te donne plusieurs synthèses PARTIELLES "
    "d'un même entretien (chacune produite sur un extrait différent de la "
    "conversation, dans l'ordre chronologique). Fusionne-les en UNE seule "
    "synthèse cohérente, fidèlement, sans rien inventer ni répéter deux fois "
    "la même idée :\n"
    "1. repartition : pour chacune des 5 catégories, fusionne le contenu de "
    "toutes les synthèses partielles en un texte unique et cohérent (évite "
    "les doublons, garde tout ce qui est factuel).\n"
    "2. resume : un résumé final de 1 à 3 phrases pour l'entretien entier — "
    "pas un résumé des résumés, le message central à retenir sur "
    "l'ensemble de l'entretien."
)


def _reduce_partial_syntheses(partials: list[dict]) -> dict:
    """Fusionne plusieurs synthèses partielles (une par tronçon) en une
    seule — un appel IA dédié, pas une simple concaténation, pour que le
    résumé final reste un vrai résumé et non une suite de résumés
    partiels bout à bout."""
    lines = []
    for i, partial in enumerate(partials, start=1):
        lines.append(f"--- Synthèse partielle {i}/{len(partials)} ---")
        for key in REPARTITION_KEYS:
            value = partial["repartition"].get(key)
            if value:
                lines.append(f"{key} : {value}")
        if partial["resume"]:
            lines.append(f"résumé partiel : {partial['resume']}")
    text = "\n".join(lines)

    data = call_ai_json(
        _REDUCE_SYSTEM,
        f"SYNTHÈSES PARTIELLES :\n{text}",
        _SYNTHESE_SCHEMA,
        _SYNTHESE_JSON_HINT,
        max_tokens=MAX_TOKENS,
        error_cls=InterviewLibreExtractAIError,
    )
    data = _safe_dict(data)
    repartition_raw = _safe_dict(data.get("repartition"))
    repartition = {
        key: _safe_str(repartition_raw.get(key))
        for key in REPARTITION_KEYS
    }
    resume = _safe_str(data.get("resume"))
    return {"repartition": repartition, "resume": resume}


def generate_repartition_from_turns(turns: list[dict]) -> dict:
    """Retourne `{"repartition": {contexte, culture_adn, forces_succes,
    points_amelioration, aspirations}, "resume": str}` à partir des tours de
    parole déjà validés (étape 1). Découpe `turns` en tronçons (map) si
    nécessaire, synthétise chaque tronçon séparément, puis fusionne (reduce)
    en une synthèse unique via un appel IA dédié. Un seul tronçon = un seul
    appel, comportement inchangé. Lève `InterviewLibreExtractAIError`."""
    if not turns:
        raise InterviewLibreExtractAIError("Aucun tour de parole à synthétiser.")

    groups = _chunk_turns(turns, _CHUNK_MAX_TURNS)

    if len(groups) == 1:
        return _call_synthese(_turns_to_text(groups[0]))

    partials = [
        _call_synthese(_turns_to_text(group), extrait_hint=f" (extrait {i}/{len(groups)})")
        for i, group in enumerate(groups, start=1)
    ]
    return _reduce_partial_syntheses(partials)
