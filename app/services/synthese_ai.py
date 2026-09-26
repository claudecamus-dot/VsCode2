"""Génération IA de la synthèse d'un thème (US4.2).

Appelle le fournisseur IA actif (`AI_PROVIDER` — ollama par défaut, ou
openai/mistral) via `ai_common.call_ai_json()`, en sortie structurée JSON.

Dégradation gracieuse : si la clé API du fournisseur actif est absente ou son
SDK non installé, `is_configured()` renvoie False (l'UI propose alors la
saisie manuelle) et `generate_theme_synthesis()` lève `SynthesisAIError` avec
un message lisible.
"""
from __future__ import annotations

import logging
import re

from .ai_common import (
    AIError,
    call_ai_json,
    chunk_text_by_paragraph,
    is_configured,
    ollama_chunk_max_words,
)

logger = logging.getLogger(__name__)

MAX_TOKENS = 2000

SYSTEM = (
    "Tu es consultant·e senior. À partir des réponses de plusieurs personnes "
    "interviewées sur un même thème, tu produis une synthèse transverse en "
    "français, factuelle et nuancée :\n"
    "- summary : 3 à 5 points saillants (les enseignements clés du thème) ;\n"
    "- convergences : ce sur quoi les personnes se rejoignent ;\n"
    "- divergences : désaccords, tensions ou angles morts.\n"
    "Reste fidèle aux propos, n'invente rien. Si un champ manque de matière, "
    "indique-le brièvement. Rédige en puces courtes, une idée par ligne."
)

_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON aux clés "
    '"summary", "convergences", "divergences".'
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "convergences": {"type": "string"},
        "divergences": {"type": "string"},
    },
    "required": ["summary", "convergences", "divergences"],
    "additionalProperties": False,
}


class SynthesisAIError(AIError):
    """Erreur fonctionnelle d'appel IA — le message est destiné à l'UI."""


def _build_prompt(theme, by_question, verbatims) -> str:
    lines = [f"THÈME : {theme.title}", ""]
    for q in theme.questions:
        rows = by_question.get(q.id) or []
        if not rows:
            continue
        lines.append(f"Question : {q.label}")
        for r in rows:
            who = r["interviewee"]
            if r.get("role"):
                who += f" ({r['role']})"
            answer = " / ".join(p for p in (r.get("value"), r.get("text")) if p)
            lines.append(f"  - {who} : {answer}")
        lines.append("")
    if verbatims:
        lines.append("VERBATIMS (citations mot pour mot) :")
        for v in verbatims:
            lines.append(f"  « {v['quote']} » — {v['interviewee']}")
    return "\n".join(lines)


def _call_claude(system: str, prompt: str, schema: dict, json_hint: str, max_tokens: int = MAX_TOKENS) -> dict:
    """Appel IA générique (fournisseur actif — voir `ai_common.PROVIDER`),
    sortie JSON structurée. Lève SynthesisAIError.

    Factorisé pour être réutilisé par la synthèse par thème, la synthèse
    globale et la génération de recommandations — seuls system/prompt/schema
    changent. Le nom historique (`_call_claude`) est conservé pour limiter le
    diff des 3 sites d'appel ci-dessous ; le fournisseur réel dépend d'
    `AI_PROVIDER` (ollama par défaut).
    """
    return call_ai_json(system, prompt, schema, json_hint, max_tokens=max_tokens, error_cls=SynthesisAIError)


def generate_theme_synthesis(theme, by_question, verbatims) -> dict:
    """Retourne {summary, convergences, divergences}. Lève SynthesisAIError."""
    prompt = _build_prompt(theme, by_question, verbatims)
    data = _call_claude(SYSTEM, prompt, _SCHEMA, _JSON_HINT)
    return {
        "summary": (data.get("summary") or "").strip(),
        "convergences": (data.get("convergences") or "").strip(),
        "divergences": (data.get("divergences") or "").strip(),
    }


# --------------------------------------------------------------------------- #
# Synthèse globale (évol) : mêmes entretiens, mais regroupés en 5 catégories
# fixes transverses à tous les thèmes (contexte, culture, forces, points
# d'amélioration, aspirations) — calqué sur un rapport de restitution réel.
# --------------------------------------------------------------------------- #
GLOBAL_SYSTEM_HEAD = (
    "Tu es consultant·e senior en conduite du changement. À partir de "
    "l'ensemble des réponses de tous les entretiens d'une mission (tous "
    "thèmes de trame confondus), tu produis une synthèse transverse en "
    "français qui regroupe le contenu en sous-thèmes émergents nommés — "
    "jamais un simple dump question par question. Pour chaque catégorie, "
    "structure ta réponse en quelques sous-thèmes courts, chacun suivi de "
    "puces factuelles fidèles aux propos recueillis :\n"
)
GLOBAL_SYSTEM_TAIL = (
    "N'invente rien ; si une catégorie manque de matière, indique-le "
    "brièvement plutôt que de combler artificiellement."
)


def global_system(axes) -> str:
    """Prompt système construit sur les AXES DE LA MISSION (2026-07-27).

    Les 5 catégories étaient énumérées en dur ici ; elles sont désormais
    configurables (`mission_axes`), et la liste envoyée au modèle doit suivre —
    sinon l'IA continuerait de remplir des rubriques que la mission n'étudie
    plus, et laisserait vides celles qu'elle a ajoutées. `hint` porte la
    description de l'axe (celles des 5 défauts sont les textes historiques,
    repris mot pour mot)."""
    # La clé JSON ET le libellé, explicitement appariés : la matière fournie au
    # modèle est étiquetée par LIBELLÉ (« Outillage & données : … ») alors que
    # la réponse est attendue par CLÉ (`outillage_donnees`). Pour les 5 axes
    # historiques la correspondance est triviale (contexte ≈ Contexte) ; pour un
    # axe ajouté elle ne l'est pas, et un modèle local rendait alors cette
    # rubrique VIDE de façon reproductible — constaté sur un passage réel
    # (revue d'incrément 2026-07-27), invisible pour la suite mockée.
    lignes = [
        f"- {axe.key} (rubrique « {axe.label} ») : {axe.hint or axe.label}"
        + (" ;\n" if i < len(axes) - 1 else ".\n")
        for i, axe in enumerate(axes)
    ]
    return GLOBAL_SYSTEM_HEAD + "".join(lignes) + GLOBAL_SYSTEM_TAIL


def global_json_hint(axes) -> str:
    cles = ", ".join(f'"{axe.key}"' for axe in axes)
    return f"\nRéponds UNIQUEMENT par un objet JSON aux clés {cles}."


def global_schema(axes) -> dict:
    cles = [axe.key for axe in axes]
    return {
        "type": "object",
        "properties": {key: {"type": "string"} for key in cles},
        "required": cles,
        "additionalProperties": False,
    }


def global_keys(axes) -> tuple:
    return tuple(axe.key for axe in axes)


class _AxeParDefaut:
    """Axe minimal (key/label/hint) pour les appelants qui n'en fournissent
    pas — ce module ne dépend pas de la couche ORM, et une mission non encore
    semée doit produire exactement la synthèse d'avant 2026-07-27."""

    __slots__ = ("key", "label", "hint")

    def __init__(self, key: str, label: str, hint: str):
        self.key, self.label, self.hint = key, label, hint


def _axes_par_defaut() -> list:
    from .mission_axes import DEFAUTS

    return [_AxeParDefaut(*d) for d in DEFAUTS]


def _global_material_blocks(material_by_theme, material_libre=None, axes=None) -> list[str]:
    """Un bloc de texte par thème et par entretien libre — l'unité de découpe
    du map-reduce (on ne coupe jamais au milieu d'un thème ou d'un entretien).

    material_by_theme : liste de (theme, by_question, verbatims), un
    triplet par thème — même matière que `_theme_material` (synthese.py),
    mais pour tous les thèmes de la trame plutôt qu'un seul.

    material_libre (incr.9, US9.6) : liste de (interview, repartition), la
    répartition déjà produite par `interview_libre_extract_ai.py` pour
    chaque entretien en mode libre (pas de trame, donc pas de thème/question
    à traverser) — injectée comme section à part, à côté de celles par
    thème, pour que la synthèse globale tienne compte des deux."""
    blocks = []
    for theme, by_question, verbatims in material_by_theme:
        if not by_question and not verbatims:
            continue
        lines = [f"=== THÈME : {theme.title} ==="]
        for q in theme.questions:
            rows = by_question.get(q.id) or []
            if not rows:
                continue
            lines.append(f"Question : {q.label}")
            for r in rows:
                who = r["interviewee"]
                if r.get("role"):
                    who += f" ({r['role']})"
                answer = " / ".join(p for p in (r.get("value"), r.get("text")) if p)
                lines.append(f"  - {who} : {answer}")
        if verbatims:
            lines.append("Verbatims :")
            for v in verbatims:
                lines.append(f"  « {v['quote']} » — {v['interviewee']}")
        blocks.append("\n".join(lines))
    libelles = {axe.key: axe.label for axe in (axes or _axes_par_defaut())}
    for interview, repartition in material_libre or []:
        lines = [f"=== ENTRETIEN LIBRE : {interview.interviewee_name} ==="]
        # Les AXES DE LA MISSION, pas les 5 clés historiques (correctif 2026-07-28,
        # trouvé en revue adversariale) : `libelles` était construite ici puis jamais
        # lue, et la boucle restait figée sur les 5 défauts. Or `Interview.repartition`
        # est produit sur un schéma construit depuis les axes — la matière recueillie
        # pour un axe SUR MESURE n'atteignait donc jamais le prompt de synthèse, et la
        # rubrique correspondante ressortait vide sans que rien ne le signale.
        for key, label in libelles.items():
            value = (repartition or {}).get(key)
            if value:
                lines.append(f"{label} : {value}")
        # Le garde de non-vacuité porte sur les lignes RETENUES, pas sur la répartition
        # brute : un entretien dont la répartition ne porte que des clés hors axes (axe
        # supprimé depuis) produisait un bloc réduit à son en-tête — trompeur pour le
        # modèle, et consommant du budget de tronçonnage pour rien.
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return blocks


def _chunk_blocks(blocks: list[str], max_words: int) -> list[list[str]]:
    """Groupe les blocs (thème/entretien) en tronçons d'AU PLUS `max_words`
    mots — même budget que le map-reduce de l'extraction libre
    (`OLLAMA_CHUNK_MAX_WORDS`).

    Un bloc qui TIENT dans le budget n'est jamais coupé : c'est la propriété
    d'origine, et elle compte (un thème coupé en deux se synthétise mal).

    Un bloc plus long que le budget, lui, est REDÉCOUPÉ (2026-09-10, constat
    d'audit performance). La version précédente le laissait former son propre
    tronçon — son docstring l'assumait — et un bloc de thème agrégeant tous les
    entretiens partait donc tel quel vers Ollama, où il pouvait dépasser
    `ollama_timeout()`. Le passage en tâche de fond a supprimé le blocage du
    navigateur, pas la perte : `partials` est une liste locale, l'exception la
    jette, et la relance repart de zéro sur tous les tronçons déjà réussis.

    Le redécoupage se fait sur les frontières de LIGNES, et chaque fragment
    reprend l'en-tête du bloc (sa première ligne : « === THÈME : … === » ou
    « === ENTRETIEN LIBRE : … === »).

    Une première version déléguait à `chunk_text_by_paragraph`. C'était un
    mauvais choix ici, mesuré par une revue adversariale (2026-09-10, F1) : ces
    blocs joignent leurs lignes par un simple `
`, donc cette fonction n'y voit
    QU'UN paragraphe et retombe sur un découpage par MOTS — les sauts de ligne
    devenaient des espaces, les fragments coupaient au milieu d'une phrase, et
    l'en-tête ne survivait que dans le premier sur cinq. Le reduce fusionnait
    ensuite ces morceaux anonymes comme s'ils étaient des synthèses de thème.
    Un découpage qui respecte le budget mais détruit l'attribution des propos
    n'achète rien : c'est la fidélité aux propos qui fait la valeur de la
    synthèse.
    """
    bornes: list[str] = []
    for block in blocks:
        if len(block.split()) <= max_words:
            bornes.append(block)
            continue
        lignes = block.split("\n")
        if len(lignes) == 1:
            # Bloc d'UNE seule ligne, plus long que le budget : il n'a pas de
            # corps à répartir, donc rien à découper sur des frontières de
            # lignes. Sans ce cas, la boucle ci-dessous ne produisait AUCUN
            # fragment et le bloc disparaissait purement et simplement — perte
            # silencieuse de matière d'entretien, trouvée par le test de
            # non-régression avant tout commit.
            bornes.extend(chunk_text_by_paragraph(block, max_words))
            continue
        entete = lignes[0]
        cout_entete = len(entete.split())
        courant: list[str] = []
        mots = 0
        for ligne in lignes[1:]:
            n = len(ligne.split())
            if courant and cout_entete + mots + n > max_words:
                bornes.append("\n".join([entete, *courant]))
                courant, mots = [], 0
            courant.append(ligne)
            mots += n
        if courant:
            bornes.append("\n".join([entete, *courant]))
        # Filet : une LIGNE seule plus longue que le budget (une réponse fleuve,
        # un verbatim très long) ne peut pas être bornée par un découpage sur
        # des frontières de lignes. Elle passe alors par le découpage par mots,
        # qui porte la garantie. Rare — mais c'est exactement le cas que la
        # version d'origine laissait filer vers Ollama.
        for trop_long in [b for b in bornes if len(b.split()) > max_words]:
            bornes.remove(trop_long)
            bornes.extend(chunk_text_by_paragraph(trop_long, max_words))

    chunks: list[list[str]] = []
    current: list[str] = []
    current_words = 0
    for block in bornes:
        words = len(block.split())
        if current and current_words + words > max_words:
            chunks.append(current)
            current, current_words = [], 0
        current.append(block)
        current_words += words
    if current:
        chunks.append(current)
    return chunks or [[]]


def _clean_global(data, keys) -> dict:
    """Coerce la réponse JSON vers les clés d'axes attendues.

    Une valeur qui n'est pas une chaîne est APLATIE en puces (`_coerce_bullets`),
    plus jamais jetée (2026-07-28) : `format: "json"` garantit du JSON valide, pas
    le type demandé, et le modèle par défaut rend régulièrement une LISTE
    d'objets `{"sous_theme": …, "facteur": …}` là où le schéma attend une chaîne.
    La garde stricte str-sinon-"" vidait alors la rubrique EN SILENCE — mesuré
    contre un Ollama réel (`contexte=54c, points_amelioration=0c` alors que le
    modèle avait bien produit deux sous-thèmes pour la seconde), ce qui livrait
    une synthèse amputée à l'écran. Exactement le défaut déjà corrigé côté SWOT
    le 2026-07-21 ([[feedback-ollama-json-type-coercion-flatten-not-drop]]) et
    resté ici, où `_clean_swot` supposait à tort que « le prompt élicite des
    chaînes » suffisait.

    Une chaîne, elle, reste INTACTE : le prompt demande des sous-thèmes nommés
    suivis de puces, et passer ce texte dans `_coerce_bullets` transformerait les
    titres de sous-thème en puces."""
    if not isinstance(data, dict):
        data = {}
    return {
        key: (data[key].strip() if isinstance(data.get(key), str) else _coerce_bullets(data.get(key)))
        for key in keys
    }


def global_reduce_system(axes) -> str:
    """Prompt de fusion des synthèses partielles — mêmes catégories que le
    prompt de synthèse, donc construites sur les axes de la mission."""
    cles = ", ".join(axe.key for axe in axes)
    return (
        "Tu es consultant·e senior en conduite du changement. On te donne "
        "plusieurs synthèses PARTIELLES d'une même mission (chacune produite sur "
        "un sous-ensemble différent des thèmes et entretiens). Fusionne-les en "
        "UNE seule synthèse transverse cohérente, fidèlement, sans rien inventer "
        f"ni répéter deux fois la même idée : pour chacune des catégories "
        f"({cles}), fusionne le contenu de toutes les synthèses partielles en "
        "sous-thèmes émergents nommés suivis de puces factuelles — garde tout ce "
        "qui est factuel, élimine les doublons. Si une catégorie manque de "
        "matière, indique-le brièvement."
    )


def _reduce_partial_globals(mission, partials: list[dict], axes) -> dict:
    """Fusionne les synthèses globales partielles (une par tronçon) en une
    seule — un appel IA dédié, comme `_reduce_partial_syntheses` côté
    extraction libre : la concaténation brute donnerait 5 catégories répétées
    N fois, pas une synthèse transverse."""
    keys = global_keys(axes)
    lines = [f"MISSION : {mission.name}", ""]
    for i, partial in enumerate(partials, start=1):
        lines.append(f"--- Synthèse partielle {i}/{len(partials)} ---")
        for key in keys:
            if partial.get(key):
                lines.append(f"{key} : {partial[key]}")
        lines.append("")
    data = _call_claude(
        global_reduce_system(axes), "\n".join(lines),
        global_schema(axes), global_json_hint(axes),
    )
    return _clean_global(data, keys)


# Tronçons DÉJÀ réussis d'un map-reduce en cours, par mission — constat
# audit-technique performance VSCode2 (2026-09-11) : `partials` vivait
# uniquement dans une variable locale à `generate_global_synthesis` ; une
# exception sur un tronçon TARDIF (ou sur l'appel de réduction final) perdait
# tous les tronçons déjà réussis, et la relance (même déclenchée dans la
# foulée, mission et matière inchangées) repayait depuis le tronçon 1 — pour
# une mission volumineuse, jusqu'à re-consommer la quasi-totalité des ~100 min
# déjà mesurées côté `global_synthesis_job`.
#
# Volontairement EN MÉMOIRE DE PROCESSUS, jamais sur disque ni en base — même
# arbitrage que `pptx_export.images._ECHECS_FETCH` : ça couvre le cas de loin
# le plus fréquent (l'utilisateur relance depuis l'écran, serveur inchangé)
# sans migration de schéma ni changement de contrat pour les appelants/tests
# existants (`generate_global_synthesis` garde exactement sa signature). Un
# redémarrage reperd le cache — déjà le cas de TOUT le job, `generation_status`
# étant lui-même remis à "error" au démarrage (`reconcile_running_on_startup`).
#
# Clé = id de mission ; valeur = (empreinte du plan de tronçons, tronçons déjà
# réussis). L'empreinte évite de resservir des tronçons obsolètes si la
# matière a changé entre deux tentatives (nouvel entretien importé, etc.) —
# sans elle, une reprise partielle mélangerait de la matière d'avant et
# d'après dans la même synthèse.
_PARTIAL_GLOBALS_CACHE: dict[int, tuple[str, list[dict]]] = {}


def _fingerprint_groups(groups: list[list[str]]) -> str:
    import hashlib

    h = hashlib.sha256()
    for group in groups:
        for bloc in group:
            h.update(bloc.encode("utf-8"))
            h.update(b"\x1e")
        h.update(b"\x1f")
    return h.hexdigest()


def _cle_partial_cache(mission) -> int:
    """Clé de cache stable pour une VRAIE mission persistée (son `id`, toujours
    renseigné : `run_global_synthesis_job` la recharge par `db.get(Mission,
    mission_id)`). Repli sur l'identité Python de l'objet pour les tests
    unitaires qui passent un double sans attribut `id` (`SimpleNamespace`) —
    chaque appel de test y construit un objet neuf, donc jamais de collision
    entre deux tests, seulement entre deux appels sur le MÊME objet mission
    (le cas visé)."""
    mission_id = getattr(mission, "id", None)
    return mission_id if mission_id is not None else id(mission)


def generate_global_synthesis(mission, material_by_theme, material_libre=None, axes=None) -> dict:
    """Retourne un dict aux clés des AXES de la mission. Lève SynthesisAIError.

    Map-reduce (2026-07-18) : sur une mission fournie (nombreux entretiens ou
    entretiens longs), le prompt unique dépassait la fenêtre de contexte du
    modèle local (Ollama tronque silencieusement au-delà de `num_ctx`) et le
    temps d'un seul appel CPU dépassait `OLLAMA_TIMEOUT` (timeout réel observé
    le 2026-07-17 sur un entretien de ~37 min). La matière est donc découpée
    en tronçons aux frontières de thème/entretien (map), synthétisée tronçon
    par tronçon, puis fusionnée par un appel de réduction dédié — même
    pattern que `interview_libre_extract_ai.generate_repartition_from_turns`.
    Une mission qui tient dans un tronçon ne fait qu'un appel, comportement
    inchangé.

    Les tronçons déjà réussis survivent à une exception sur un tronçon tardif
    ou sur la réduction finale (cf. `_PARTIAL_GLOBALS_CACHE`) : une relance
    sur la même mission et la même matière reprend là où l'échec a eu lieu au
    lieu de re-payer les tronçons déjà obtenus."""
    # `axes` optionnel : les appelants historiques (et les tests) qui ne le
    # passent pas retombent sur les 5 axes par défaut, comportement inchangé.
    axes = list(axes) if axes else _axes_par_defaut()
    system, schema, hint = global_system(axes), global_schema(axes), global_json_hint(axes)
    keys = global_keys(axes)

    header = f"MISSION : {mission.name}"
    blocks = _global_material_blocks(material_by_theme, material_libre, axes)
    groups = _chunk_blocks(blocks, ollama_chunk_max_words())

    if len(groups) == 1:
        data = _call_claude(system, "\n\n".join([header, *groups[0]]), schema, hint)
        return _clean_global(data, keys)

    cle = _cle_partial_cache(mission)
    empreinte = _fingerprint_groups(groups)
    cache = _PARTIAL_GLOBALS_CACHE.get(cle)
    if cache is not None and cache[0] == empreinte:
        partials = list(cache[1])  # copie : jamais la liste mise en cache elle-même
    else:
        partials = []
    debut = len(partials)

    # Une exception ici (tronçon tardif OU réduction finale) se propage telle
    # quelle — AUCUN try/except à ajouter pour ça : les tronçons réussis sont
    # déjà dans le cache au moment où elle serait levée, écrits à chaque
    # succès ci-dessous.
    for i, group in enumerate(groups[debut:], start=debut + 1):
        prompt = "\n\n".join([f"{header} (extrait {i}/{len(groups)})", *group])
        partials.append(_clean_global(_call_claude(system, prompt, schema, hint), keys))
        _PARTIAL_GLOBALS_CACHE[cle] = (empreinte, list(partials))
    resultat = _reduce_partial_globals(mission, partials, axes)
    # Succès (map ET réduction) : plus besoin de la reprise pour cette mission.
    _PARTIAL_GLOBALS_CACHE.pop(cle, None)
    return resultat


# --------------------------------------------------------------------------- #
# Recommandations (évol) : dérivées de la synthèse globale déjà générée (pas
# des réponses brutes), regroupées en quelques axes transverses — chaque
# fiche suit un schéma fixe calqué sur un rapport de restitution réel.
# --------------------------------------------------------------------------- #
RECO_MAX_TOKENS = 6000

RECO_SYSTEM = (
    "Tu es consultant·e senior en transformation organisationnelle. À "
    "partir d'une synthèse transverse d'entretiens (contexte, culture, "
    "forces, points d'amélioration, aspirations), identifie 3 à 4 axes de "
    "recommandation qui recoupent l'ensemble du sujet — pas un axe par "
    "thème d'origine, mais une nouvelle structuration stratégique à "
    "l'échelle de la mission. Pour chaque axe, propose 2 à 4 "
    "recommandations concrètes. Pour chaque recommandation, renseigne "
    "exactement ces champs :\n"
    "- title : titre court de l'action ;\n"
    "- objectif : le manque ou problème adressé ;\n"
    "- acteurs : qui est impliqué (ex. CODIR, managers, équipes, RH) ;\n"
    "- valeur : note de 1 (faible) à 5 (fort impact) ;\n"
    "- complexite : note de 1 (simple) à 5 (complexe) ;\n"
    "- proposition_valeur : une phrase résumant le bénéfice ;\n"
    "- plan_actions : puces d'actions concrètes (ateliers, chantiers) ;\n"
    "- resultats_attendus : puces des bénéfices attendus.\n"
    "Reste ancré dans la matière fournie, n'invente pas de faits nouveaux."
)

RECO_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON à la clé \"axes\", liste "
    'd\'objets {"title", "recommendations": [...]}.'
)

RECO_SCHEMA = {
    "type": "object",
    "properties": {
        "axes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "recommendations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "objectif": {"type": "string"},
                                "acteurs": {"type": "string"},
                                "valeur": {"type": "integer"},
                                "complexite": {"type": "integer"},
                                "proposition_valeur": {"type": "string"},
                                "plan_actions": {"type": "string"},
                                "resultats_attendus": {"type": "string"},
                            },
                            "required": [
                                "title", "objectif", "acteurs", "valeur",
                                "complexite", "proposition_valeur",
                                "plan_actions", "resultats_attendus",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "recommendations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["axes"],
    "additionalProperties": False,
}


def _clamp_score(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, n))


def _build_reco_prompt(global_synthesis, axes=None) -> str:
    """Matière des recommandations (et, par ricochet, du SWOT et des
    difficultés, qui en dérivent) : les rubriques suivent désormais les AXES de
    la mission. Sans axes fournis, les 5 historiques — le contenu d'un axe
    ajouté serait sinon absent du prompt, donc jamais restitué."""
    lines = ["SYNTHÈSE TRANSVERSE DE LA MISSION", ""]
    axes = list(axes) if axes else _axes_par_defaut()
    fields = [(axe.label, global_synthesis.contenu(axe.key)) for axe in axes]
    for label, content in fields:
        if (content or "").strip():
            lines.append(f"=== {label} ===")
            lines.append(content.strip())
            lines.append("")
    return "\n".join(lines)


def generate_recommendations(global_synthesis, axes=None) -> list[dict]:
    """Retourne une liste d'axes {"title", "recommendations": [...]}.
    Lève SynthesisAIError."""
    prompt = _build_reco_prompt(global_synthesis, axes)
    data = _call_claude(RECO_SYSTEM, prompt, RECO_SCHEMA, RECO_JSON_HINT, max_tokens=RECO_MAX_TOKENS)
    axes = []
    for axis in data.get("axes") or []:
        recos = []
        for r in axis.get("recommendations") or []:
            recos.append(
                {
                    "title": (r.get("title") or "").strip(),
                    "objectif": (r.get("objectif") or "").strip(),
                    "acteurs": (r.get("acteurs") or "").strip(),
                    "valeur": _clamp_score(r.get("valeur")),
                    "complexite": _clamp_score(r.get("complexite")),
                    "proposition_valeur": (r.get("proposition_valeur") or "").strip(),
                    "plan_actions": (r.get("plan_actions") or "").strip(),
                    "resultats_attendus": (r.get("resultats_attendus") or "").strip(),
                }
            )
        axes.append({"title": (axis.get("title") or "").strip(), "recommendations": recos})
    return axes


# --------------------------------------------------------------------------- #
# SWOT (Palier 1 restitution, 2026-07-21) : dérivée de la synthèse globale déjà
# générée (comme les recommandations, via `_build_reco_prompt`). Forces /
# faiblesses = regard INTERNE ; opportunités / menaces = regard EXTERNE (marché,
# concurrence, risques), non déductibles des 5 catégories internes — d'où le
# cadrage explicite du prompt. Un seul appel (la synthèse est déjà condensée),
# pas de map-reduce. Cf. docs/reflexions/restitution-mission.md §5.1.
# --------------------------------------------------------------------------- #
SWOT_SYSTEM = (
    "Tu es consultant·e senior en stratégie. À partir d'une synthèse "
    "transverse d'entretiens (contexte, culture, forces, points "
    "d'amélioration, aspirations), produis une matrice SWOT en français à "
    "quatre quadrants, chacun en quelques puces factuelles :\n"
    "- forces : atouts INTERNES de l'organisation (ce qui fonctionne, leviers "
    "en place) ;\n"
    "- faiblesses : limites INTERNES (douleurs, manques, ce qui bloque) ;\n"
    "- opportunites : facteurs EXTERNES favorables (marché, technologies, "
    "évolutions du secteur, attentes clients) que l'organisation pourrait "
    "saisir ;\n"
    "- menaces : facteurs EXTERNES défavorables (concurrence, risques "
    "réglementaires ou techniques, tendances adverses).\n"
    "Forces et faiblesses s'appuient directement sur la matière fournie. "
    "Opportunités et menaces demandent un regard EXTERNE : déduis-les "
    "prudemment du contexte et des aspirations, sans inventer de faits "
    "chiffrés ; si la matière ne permet pas de les étayer, reste bref et "
    "prudent plutôt que d'affabuler."
)

SWOT_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON aux clés "
    '"forces", "faiblesses", "opportunites", "menaces". La valeur de chaque '
    "clé est UNE chaîne de caractères, une puce par ligne préfixée de « - » "
    "(surtout pas une liste JSON ni un objet imbriqué)."
)

SWOT_SCHEMA = {
    "type": "object",
    "properties": {
        "forces": {"type": "string"},
        "faiblesses": {"type": "string"},
        "opportunites": {"type": "string"},
        "menaces": {"type": "string"},
    },
    "required": ["forces", "faiblesses", "opportunites", "menaces"],
    "additionalProperties": False,
}

SWOT_KEYS = ("forces", "faiblesses", "opportunites", "menaces")


def _bullet_line(item) -> str:
    """Un élément de quadrant → UNE ligne de texte (sans préfixe puce, marqueurs
    de puce et blancs retirés). Un objet imbriqué = une seule puce : ses valeurs
    textuelles sont JOINTES (pas éclatées en puces sans lien) — sinon
    `[{"force": "Cloud", "impact": "élevé"}]` deviendrait deux puces «Cloud» et
    «élevé» décorrélées. Une liste imbriquée (rare) est aplatie de même."""
    if isinstance(item, str):
        return item.strip().lstrip("-•").strip()
    if isinstance(item, dict):
        return " — ".join(t for t in (_bullet_line(v) for v in item.values()) if t)
    if isinstance(item, list):
        return " ; ".join(t for t in (_bullet_line(x) for x in item) if t)
    return ""


def _coerce_bullets(value) -> str:
    """Aplati une valeur de quadrant en texte à puces, quelle que soit la forme
    renvoyée par le modèle. Ollama (`format: "json"` garantit du JSON valide,
    pas le type EXACT demandé) renvoie très souvent une LISTE par quadrant —
    parfois une liste de petits objets `{"poids": "..."}` — là où le schéma
    attend une chaîne. La garde stricte str-sinon-"" jetait alors tout le
    contenu : le passage réel du 2026-07-21 ressortait les 4 quadrants VIDES
    malgré une génération pertinente (même défaut retrouvé le 2026-07-28 dans
    `_clean_global`, qui s'appuie désormais lui aussi sur cet aplatissement).
    On aplati
    plutôt que de perdre. Défense en profondeur — `SWOT_JSON_HINT` demande déjà
    une chaîne, mais un 7-8B local n'obéit pas de façon fiable. Les lignes/puces
    vides (ex. `"- \n- "` renvoyé par le modèle pour un quadrant sans matière)
    sont éliminées, pour ne pas faire passer du vide pour du contenu."""
    if isinstance(value, str):
        items: list = value.splitlines()
    elif isinstance(value, dict):
        items = list(value.values())
    elif isinstance(value, list):
        items = value
    else:
        return ""
    lines = [f"- {t}" for t in (_bullet_line(item) for item in items) if t]
    return "\n".join(lines)


def _clean_swot(data) -> dict:
    """Coerce la réponse JSON vers les 4 quadrants : listes/objets APLATIS en
    puces via `_coerce_bullets`, jamais jetés — le prompt SWOT (« chacun en
    quelques puces ») élicite des listes qu'il ne faut pas perdre (correctif du
    2026-07-21). Différence avec `_clean_global`, qui applique la même règle
    depuis le 2026-07-28 : ici une CHAÎNE passe aussi par l'aplatissement (une
    suite de puces), là elle reste intacte (des sous-thèmes nommés suivis de
    puces, que l'aplatissement écraserait)."""
    if not isinstance(data, dict):
        data = {}
    return {key: _coerce_bullets(data.get(key)) for key in SWOT_KEYS}


def ai_precondition_error(global_synthesis, missing_synthesis_message: str) -> str | None:
    """Garde commune aux routes qui dérivent un artefact (SWOT, executive summary,
    difficultés, recommandations) de la synthèse globale déjà produite : service
    IA indisponible, puis synthèse absente/vide. `None` si les deux préconditions
    sont réunies — l'appelant peut alors lancer sa génération. Message "service
    indisponible" partagé (identique aux 4 sites) ; message "synthèse absente"
    laissé au paramètre car il nomme l'artefact concerné (« la SWOT en
    découle », etc.). Ne couvre PAS generate_global_synthesis (synthese.py) :
    cette route dérive d'un précondition différent (matière brute présente),
    pas de la synthèse globale elle-même — pattern proche en surface mais
    sémantiquement distinct, volontairement laissé hors de cette factorisation."""
    if not is_configured():
        return (
            "Service IA indisponible — utilisez l'export pour lancer une "
            "analyse externe, puis importez le résultat."
        )
    if global_synthesis is None or not global_synthesis.has_content:
        return missing_synthesis_message
    return None


def generate_swot(global_synthesis, axes=None) -> dict:
    """Retourne un dict aux 4 clés de `MissionSwot`. Lève SynthesisAIError.
    Dérivée de la synthèse globale (mêmes 5 catégories que les recommandations,
    via `_build_reco_prompt`), un seul appel — la synthèse est déjà condensée."""
    prompt = _build_reco_prompt(global_synthesis, axes)
    data = _call_claude(SWOT_SYSTEM, prompt, SWOT_SCHEMA, SWOT_JSON_HINT)
    return _clean_swot(data)


# --------------------------------------------------------------------------- #
# Executive summary (piste F restitution, 2026-07-21) : synthèse d'ouverture
# « so what » (constat + points clés + message à retenir), dérivée de la synthèse
# globale (comme la SWOT). Pattern des vraies restitutions OCTO — cf.
# docs/reflexions/restitution-mission.md §F. Un seul appel, pas de map-reduce.
# --------------------------------------------------------------------------- #
EXEC_SUMMARY_SYSTEM = (
    "Tu es consultant·e senior. À partir d'une synthèse transverse d'entretiens "
    "(contexte, culture, forces, points d'amélioration, aspirations), produis "
    "l'executive summary d'une restitution en français — le « so what » du "
    "rapport, percutant et court :\n"
    "- headline : UNE phrase de constat d'ensemble (la situation telle qu'elle est) ;\n"
    "- points : 2 à 4 points clés en puces (les enseignements majeurs) ;\n"
    "- key_message : UNE phrase, le message à retenir / la décision à prendre.\n"
    "Reste factuel, synthétique, orienté décision."
)

EXEC_SUMMARY_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON aux clés \"headline\", \"points\", "
    "\"key_message\". \"headline\" et \"key_message\" sont des chaînes ; "
    "\"points\" est une chaîne à puces (une par ligne, préfixée « - »)."
)

EXEC_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "string"},
        "key_message": {"type": "string"},
    },
    "required": ["headline", "points", "key_message"],
    "additionalProperties": False,
}

EXEC_SUMMARY_KEYS = ("headline", "points", "key_message")


def _coerce_line(value) -> str:
    """Aplati une valeur (str/list/dict) en UNE ligne — même défense que
    `_coerce_bullets` contre les types inattendus d'Ollama (cf.
    feedback-ollama-json-type-coercion-flatten-not-drop), mais SANS marqueurs de
    puces : `headline` et `key_message` sont des phrases, pas des listes."""
    bulleted = _coerce_bullets(value)
    if not bulleted:
        return ""
    lignes = [ln.lstrip("-•* \t").strip() for ln in bulleted.split("\n")]
    return " ".join(ln for ln in lignes if ln)


def _clean_executive_summary(data) -> dict:
    """Coerce la réponse JSON vers les 3 champs de `MissionExecutiveSummary` :
    `points` aplati en puces (comme la SWOT), `headline`/`key_message` en une
    ligne — jamais jeter un type inattendu, aplatir."""
    if not isinstance(data, dict):
        data = {}
    return {
        "headline": _coerce_line(data.get("headline")),
        "points": _coerce_bullets(data.get("points")),
        "key_message": _coerce_line(data.get("key_message")),
    }


def generate_executive_summary(global_synthesis, axes=None) -> dict:
    """Retourne un dict aux 3 clés de `MissionExecutiveSummary`. Lève
    SynthesisAIError. Dérivée de la synthèse globale (comme la SWOT), un seul
    appel — la synthèse est déjà condensée."""
    prompt = _build_reco_prompt(global_synthesis, axes)
    data = _call_claude(
        EXEC_SUMMARY_SYSTEM, prompt, EXEC_SUMMARY_SCHEMA, EXEC_SUMMARY_JSON_HINT
    )
    return _clean_executive_summary(data)


# --------------------------------------------------------------------------- #
# Difficultes (piste F restitution, planche « Difficultes » + inserts citation) :
# liste ORDONNEE (hierarchie) de difficultes derivees de la synthese globale
# (surtout points_amelioration), chacune pouvant porter un verbatim en encadre
# sur la slide. Cf. docs/reflexions/restitution-mission.md §D.1. Un seul appel.
# --------------------------------------------------------------------------- #
DIFFICULTES_SYSTEM = (
    "Tu es consultant·e senior. À partir d'une synthèse transverse d'entretiens "
    "(surtout les points d'amélioration), identifie les 3 à 6 DIFFICULTÉS majeures "
    "de l'organisation, ordonnées de la plus structurante à la moins. Chacune est "
    "formulée en UNE phrase courte et factuelle — un CONSTAT (ce qui coince), pas "
    "une solution. Reste ancré dans la matière fournie, n'invente pas de faits."
)

DIFFICULTES_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON à la clé \"difficultes\", une liste "
    "de chaînes (une phrase par difficulté, ordre = hiérarchie)."
)

DIFFICULTES_SCHEMA = {
    "type": "object",
    "properties": {
        "difficultes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["difficultes"],
    "additionalProperties": False,
}


def _clean_difficulties(data) -> list:
    """Coerce vers une liste de libellés non vides. Meme defense Ollama que la
    SWOT : une difficulte renvoyee en liste/dict (ex. {"label": "..."}) est
    APLATIE en une ligne via _coerce_line, jamais jetee (cf.
    feedback-ollama-json-type-coercion-flatten-not-drop)."""
    if not isinstance(data, dict):
        data = {}
    items = data.get("difficultes")
    if items is None:
        # Repli clé anglaise : un modèle local (7-8B) répond parfois "difficulties"
        # malgré le prompt FR — sans ce repli, génération « réussie » mais vide.
        items = data.get("difficulties")
    if not isinstance(items, list):
        items = [items] if items else []
    out = []
    for it in items:
        line = _coerce_line(it)
        if line:
            out.append(line)
    return out


def generate_difficulties(global_synthesis, axes=None) -> list:
    """Retourne une liste ORDONNEE de libellés de difficultés (hierarchie),
    derivee de la synthese globale (surtout points_amelioration), un seul appel.
    Leve SynthesisAIError."""
    prompt = _build_reco_prompt(global_synthesis, axes)
    data = _call_claude(
        DIFFICULTES_SYSTEM, prompt, DIFFICULTES_SCHEMA, DIFFICULTES_JSON_HINT
    )
    return _clean_difficulties(data)


# --------------------------------------------------------------------------- #
# Indicateurs de suivi (US9.27 b) et matrice risques-contrôles (US9.27 c) :
# deux listes dérivées de la synthèse globale ET des recommandations déjà
# produites (un KPI suit un axe, un risque menace la trajectoire). Même patron
# que les difficultés : un seul appel, liste ordonnée, types Ollama APLATIS
# (cf. feedback-ollama-json-type-coercion-flatten-not-drop), repli clé anglaise.
# --------------------------------------------------------------------------- #
def _build_trajectoire_prompt(global_synthesis, axes=None, reco_axes=None) -> str:
    """Matière des KPIs / risques : la synthèse (comme la SWOT) + les axes de
    recommandation et les intitulés de leurs recos, quand ils existent."""
    prompt = _build_reco_prompt(global_synthesis, axes)
    lignes = []
    for axis in reco_axes or []:
        titre = (getattr(axis, "title", "") or "").strip()
        if not titre:
            continue
        lignes.append(f"- Axe « {titre} »")
        for r in getattr(axis, "recommendations", None) or []:
            rt = (getattr(r, "title", "") or "").strip()
            if rt:
                lignes.append(f"    · {rt}")
    if lignes:
        prompt += "\n=== AXES DE RECOMMANDATION ===\n" + "\n".join(lignes) + "\n"
    return prompt


def _first_key(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _items_list(data, *keys) -> list:
    """La liste d'items d'une réponse, quelle que soit sa forme : clé attendue,
    clé de repli (anglais…), liste nue, objet unique, ou objet à UNE seule clé
    imprévue — jamais « génération réussie mais vide » par simple nom de clé."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    items = _first_key(data, *keys)
    if items is None and len(data) == 1:
        (items,) = data.values()
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = [items] if items else []
    return items


_SNAP_MIN = 4  # en deçà, une inclusion est une coïncidence (« Data », « SI »)


def _snap_axe(value: str, axe_titres: list[str]) -> str:
    """Ramène l'axe cité par le modèle à l'intitulé EXACT d'un axe de la mission :
    égalité (casse/espaces) d'abord ; sinon inclusion de l'un dans l'autre, en
    retenant la correspondance la PLUS LONGUE (pas la première de la liste), et
    jamais sur un terme de moins de `_SNAP_MIN` caractères ; sinon tel quel.

    Départage à longueur commune égale : le plus petit écart de longueur entre
    l'intitulé et la valeur, puis, à égalité parfaite, le PREMIER de la liste
    (ordre stable). Un warning nomme la valeur et les candidats ex aequo."""
    v = (value or "").strip()
    if not v or not axe_titres:
        return v
    low = " ".join(v.split()).casefold()  # espaces internes normalisés (BOUNDARY-2)
    for t in axe_titres:
        if " ".join(t.split()).casefold() == low:
            return t
    meilleurs = []
    for t in axe_titres:
        tl = t.strip().casefold()
        commun = tl if tl in low else (low if low in tl else "")
        if len(commun) >= _SNAP_MIN:
            meilleurs.append((len(commun), -abs(len(tl) - len(low)), t))
    if meilleurs:
        choix = max(meilleurs, key=lambda m: (m[0], m[1]))
        ex_aequo = [m[2] for m in meilleurs if m[0] == choix[0]]
        if len(ex_aequo) >= 2:
            logger.warning(
                "snap_axe : %r rapproché de %r parmi des candidats ex aequo %r",
                v, choix[2], ex_aequo,
            )
        return choix[2]
    return v


# Nom public du rapprochement — les routeurs n'importent pas de nom préfixé `_`
# (convention du dépôt, relevée en contre-revue du correctif BOUNDARY-2).
snap_axe = _snap_axe


KPIS_SYSTEM = (
    "Tu es consultant·e senior en audit. À partir d'une synthèse transverse "
    "d'entretiens et des axes de recommandation, propose 3 à 6 INDICATEURS DE SUIVI "
    "(KPI) permettant de mesurer la mise en œuvre et l'effet des recommandations. "
    "Pour chacun : un libellé court, une cible ou une modalité de mesure concrète "
    "(valeur, fréquence, source), et l'axe de recommandation qu'il suit s'il y en a "
    "un (reprends son intitulé exact, sinon laisse vide). Reste ancré dans la matière."
)

KPIS_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON à la clé \"kpis\", une liste d'objets "
    "{\"libelle\": chaîne, \"cible\": chaîne, \"axe\": chaîne}."
)

KPIS_SCHEMA = {
    "type": "object",
    "properties": {
        "kpis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "libelle": {"type": "string"},
                    "cible": {"type": "string"},
                    "axe": {"type": "string"},
                },
                "required": ["libelle", "cible", "axe"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["kpis"],
    "additionalProperties": False,
}


def _clean_kpis(data, axe_titres=None) -> list[dict]:
    """Coerce vers une liste de {libelle, cible, axe} à libellé non vide. Un item
    CHAÎNE devient un libellé seul ; un champ liste/dict est aplati en une ligne."""
    titres = [t for t in (axe_titres or []) if (t or "").strip()]
    out = []
    for it in _items_list(data, "kpis", "indicateurs", "indicators", "KPIs"):
        if isinstance(it, dict):
            libelle = _coerce_line(_first_key(it, "libelle", "libellé", "label",
                                              "indicateur", "nom", "name", "kpi"))
            cible = _coerce_line(_first_key(it, "cible", "target", "mesure",
                                            "measure", "objectif", "valeur"))
            axe = _coerce_line(_first_key(it, "axe", "axis", "axe_recommandation"))
        else:
            libelle, cible, axe = _coerce_line(it), "", ""
        if libelle:
            out.append({"libelle": libelle, "cible": cible, "axe": _snap_axe(axe, titres)})
    return out


def generate_kpis(global_synthesis, axes=None, reco_axes=None) -> list[dict]:
    """Liste ordonnée de KPIs {libelle, cible, axe}. Lève SynthesisAIError."""
    prompt = _build_trajectoire_prompt(global_synthesis, axes, reco_axes)
    data = _call_claude(KPIS_SYSTEM, prompt, KPIS_SCHEMA, KPIS_JSON_HINT)
    titres = [(getattr(a, "title", "") or "") for a in reco_axes or []]
    return _clean_kpis(data, titres)


RISQUES_SYSTEM = (
    "Tu es consultant·e senior en audit. À partir d'une synthèse transverse "
    "d'entretiens et des recommandations, identifie 3 à 8 RISQUES majeurs pour "
    "l'organisation ou la trajectoire proposée. Pour chacun : le risque en une "
    "phrase, sa gravité et sa probabilité notées 1 (faible), 2 (moyenne) ou 3 "
    "(élevée), le contrôle ou la mesure qui le couvre, et si ce contrôle est "
    "\"existant\" (déjà en place d'après la matière) ou \"propose\" (à mettre en "
    "place). N'invente pas de faits absents de la matière."
)

RISQUES_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON à la clé \"risques\", une liste d'objets "
    "{\"risque\": chaîne, \"gravite\": entier 1-3, \"probabilite\": entier 1-3, "
    "\"controle\": chaîne, \"controle_type\": \"existant\" ou \"propose\"}."
)

RISQUES_SCHEMA = {
    "type": "object",
    "properties": {
        "risques": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "risque": {"type": "string"},
                    "gravite": {"type": "integer"},
                    "probabilite": {"type": "integer"},
                    "controle": {"type": "string"},
                    "controle_type": {"type": "string", "enum": ["existant", "propose"]},
                },
                "required": ["risque", "gravite", "probabilite", "controle", "controle_type"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["risques"],
    "additionalProperties": False,
}

# Ordre de test : « moyen » avant « bas »/« faible » n'importe pas, mais « élevé »
# doit passer avant « faible » (« peu élevée » est rare, « faible » sans ambiguïté).
_NIVEAU_MOTS = (
    (3, ("élev", "eleve", "high", "fort", "haut", "critique", "majeur", "severe", "sévère")),
    (1, ("faible", "low", "bas", "mineur", "minor", "rare")),
    (2, ("moyen", "medium", "modér", "moder", "moderate")),
)


def _coerce_niveau(value, defaut: int = 2) -> int:
    """Niveau 1-3 depuis ce qu'un modèle local renvoie vraiment : entier, flottant,
    « 3 », « 3/3 », « élevée », « High », une liste ou un dict qui en contient un.
    Une échelle 1-5 est ramenée à 1-3 (≥ 3 → 3). Illisible → `defaut`, jamais d'erreur."""
    if isinstance(value, bool) or value is None:
        return defaut
    if isinstance(value, (int, float)):
        n = int(round(value))
    elif isinstance(value, str):
        v = value.strip().casefold()
        m = re.match(r"(\d+(?:[.,]\d+)?)\s*(?:/\s*(\d+))?", v)
        if not m:
            for niveau, mots in _NIVEAU_MOTS:
                if any(mot in v for mot in mots):
                    return niveau
            return defaut
        n = float(m.group(1).replace(",", "."))
        # Même mise à l'échelle qu'un « x/y » de _coerce_score03 : « 2/5 » vaut
        # 1 (2·3/5 arrondi), pas 2 — divergence relevée par la salle (YUI-2).
        sur = int(m.group(2)) if m.group(2) else 3
        brut = n
        if sur and sur != 3:
            n = n * 3 / sur
        n = int(round(n))
        # « 1/6 » vaut 0.5, arrondi bancaire à 0 : un numérateur positif ne
        # tombe pas sous le plancher 1 (sinon `defaut`, possiblement 3).
        if brut > 0:
            n = max(1, n)
    elif isinstance(value, dict):
        inner = _first_key(value, "niveau", "valeur", "value", "score")
        if inner is None:
            inner = next(iter(value.values()), None)
        return _coerce_niveau(inner, defaut)
    elif isinstance(value, list):
        return _coerce_niveau(value[0], defaut) if value else defaut
    else:
        return defaut
    if n <= 0:
        return defaut
    return min(3, n)


# Nom public (même convention que `snap_axe`) : synthese_ecriture l'importe.
coerce_niveau = _coerce_niveau


def _coerce_controle_type(value) -> str:
    v = _coerce_line(value).casefold()
    return "existant" if v.startswith(("exist", "en place", "actuel", "current")) else "propose"


def _clean_risks(data) -> list[dict]:
    """Coerce vers une liste de {risque, gravite, probabilite, controle,
    controle_type} à risque non vide — champs texte aplatis, niveaux bornés 1-3."""
    out = []
    for it in _items_list(data, "risques", "risks", "matrice", "risques_controles"):
        if isinstance(it, dict):
            risque = _coerce_line(_first_key(it, "risque", "risk", "libelle", "label", "nom"))
            gravite = _coerce_niveau(_first_key(it, "gravite", "gravité", "severity",
                                                "impact", "severite"))
            proba = _coerce_niveau(_first_key(it, "probabilite", "probabilité",
                                              "probability", "likelihood", "occurrence"))
            controle = _coerce_line(_first_key(it, "controle", "contrôle", "control",
                                               "mesure", "mitigation", "controles"))
            ctype = _coerce_controle_type(_first_key(it, "controle_type", "type",
                                                     "statut", "status"))
        else:
            risque, gravite, proba, controle, ctype = _coerce_line(it), 2, 2, "", "propose"
        if risque:
            out.append({"risque": risque, "gravite": gravite, "probabilite": proba,
                        "controle": controle, "controle_type": ctype})
    return out


def generate_risks(global_synthesis, axes=None, reco_axes=None) -> list[dict]:
    """Liste ordonnée de risques de la matrice risques-contrôles. Lève SynthesisAIError."""
    prompt = _build_trajectoire_prompt(global_synthesis, axes, reco_axes)
    data = _call_claude(RISQUES_SYSTEM, prompt, RISQUES_SCHEMA, RISQUES_JSON_HINT)
    return _clean_risks(data)


# --------------------------------------------------------------------------- #
# Grille de maturité par pilier (incr.10 palier 3) : un score 0-3 + une
# justification par THÈME de trame, dérivés de la synthèse globale. Même patron
# que KPIs/risques : un appel, aplatissement Ollama, pilier ramené au titre
# EXACT d'un thème (les piliers inventés sont écartés : la grille suit la trame).
# --------------------------------------------------------------------------- #
MATURITE_SYSTEM = (
    "Tu es consultant·e senior en audit. À partir d'une synthèse transverse "
    "d'entretiens, évalue la MATURITÉ de l'organisation sur chacun des piliers "
    "listés (et seulement eux), sur l'échelle : 0 = absent, 1 = émergent (pratiques "
    "isolées), 2 = structuré (pratiques définies et partagées), 3 = maîtrisé "
    "(pratiques pilotées et améliorées). Pour chaque pilier : le score entier et "
    "une justification d'UNE phrase courte, ancrée dans la matière. Sans matière "
    "sur un pilier, mets 0 et dis-le."
)

MATURITE_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON à la clé \"maturite\", une liste "
    "d'objets {\"pilier\": chaîne (intitulé exact), \"score\": entier 0-3, "
    "\"justification\": chaîne}."
)

MATURITE_SCHEMA = {
    "type": "object",
    "properties": {
        "maturite": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pilier": {"type": "string"},
                    "score": {"type": "integer"},
                    "justification": {"type": "string"},
                },
                "required": ["pilier", "score", "justification"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["maturite"],
    "additionalProperties": False,
}

_SCORE_MOTS = (
    (3, ("maîtris", "maitris", "optimis", "mastered", "advanced", "avancé")),
    (2, ("structur", "défini", "defini", "defined", "intermédiaire")),
    (1, ("émerg", "emerg", "initial", "partiel", "isolé", "basic", "faible")),
    (0, ("absent", "inexistant", "aucun", "none", "néant")),
)


def _coerce_score03(value, defaut: int = 0) -> int:
    """Score 0-3 depuis ce qu'un modèle local renvoie : entier, flottant, « 2 »,
    « 2/3 », « structuré », une liste ou un dict qui en contient un. Une note sur 5
    (« 4/5 ») est ramenée sur 3 ; illisible → `defaut`, jamais d'erreur."""
    if isinstance(value, bool) or value is None:
        return defaut
    if isinstance(value, (int, float)):
        n = int(round(value))
        return max(0, min(3, n))
    if isinstance(value, str):
        v = value.strip().casefold()
        m = re.match(r"(-?\d+(?:[.,]\d+)?)\s*(?:/\s*(\d+))?", v)
        if m:
            n = float(m.group(1).replace(",", "."))
            sur = int(m.group(2)) if m.group(2) else 3
            if sur and sur != 3:
                n = n * 3 / sur
            return max(0, min(3, int(round(n))))
        for score, mots in _SCORE_MOTS:
            if any(mot in v for mot in mots):
                return score
        return defaut
    if isinstance(value, dict):
        inner = _first_key(value, "score", "niveau", "valeur", "value")
        if inner is None:
            inner = next(iter(value.values()), None)
        return _coerce_score03(inner, defaut)
    if isinstance(value, list):
        return _coerce_score03(value[0], defaut) if value else defaut
    return defaut


def _clean_maturite(data, piliers: list[str]) -> list[dict]:
    """{pilier, score, justification} pour les piliers CONNUS seulement (titre
    exact d'un thème, via `_snap_axe`), dans l'ordre de la trame. Une ligne par
    THÈME (position), pas par texte : deux thèmes de même titre gardent chacun
    leur ligne — la n-ième réponse d'un titre remplit sa n-ième occurrence."""
    titres = [p for p in piliers if (p or "").strip()]
    slots: list[dict | None] = [None] * len(titres)
    for it in _items_list(data, "maturite", "maturité", "maturity", "piliers", "grille"):
        if isinstance(it, dict):
            brut = _coerce_line(_first_key(it, "pilier", "theme", "thème", "pillar", "label", "nom"))
            score = _coerce_score03(_first_key(it, "score", "niveau", "note", "maturite", "level"))
            just = _coerce_line(_first_key(it, "justification", "commentaire", "raison",
                                           "rationale", "justif"))
        else:
            continue  # une chaîne nue ne porte ni pilier sûr ni score
        pilier = _snap_axe(brut, titres)
        libre = next((i for i, t in enumerate(titres) if t == pilier and slots[i] is None), None)
        if libre is not None:
            slots[libre] = {"pilier": pilier, "score": score, "justification": just}
    return [x for x in slots if x is not None]


def generate_maturite(global_synthesis, axes=None, piliers=None) -> list[dict]:
    """Grille de maturité sur les `piliers` (titres de thèmes). Lève SynthesisAIError."""
    piliers = [p for p in (piliers or []) if (p or "").strip()]
    prompt = _build_reco_prompt(global_synthesis, axes)
    prompt += "\n=== PILIERS À ÉVALUER ===\n" + "\n".join(f"- {p}" for p in piliers) + "\n"
    data = _call_claude(MATURITE_SYSTEM, prompt, MATURITE_SCHEMA, MATURITE_JSON_HINT)
    return _clean_maturite(data, piliers)
