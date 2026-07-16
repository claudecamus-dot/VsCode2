"""Parsing du fichier Markdown d'analyse externe (évol) — contrepartie de
`mission_export.py`. Reconstruit exactement la même forme de résultat que
`generate_global_synthesis()` (dict à 5 clés) et `generate_recommendations()`
(liste d'axes), pour que l'application en base
(`_apply_global_synthesis_result`/`_apply_recommendations_result` dans
`app.routers.synthese`) soit partagée entre génération IA et import de
fichier.

Contrat de format : les titres Markdown exacts produits par
`mission_export.build_export_markdown()` (## SYNTHÈSE GLOBALE / ### <catégorie>
puis ## RECOMMANDATIONS / #### Axe N : <titre> / ##### Recommandation N.M :
<titre> avec des puces « - Champ : valeur »). Le matching des libellés est
tolérant à la casse et aux variations mineures de formulation (accents,
« et » vs « & »), mais s'appuie sur les niveaux de titre Markdown eux-mêmes
— les conserver est indispensable pour une réintégration automatique.
"""
from __future__ import annotations

import re


def decode_text_upload(raw: bytes) -> str:
    """Décode un fichier texte téléversé sans corrompre le contenu métier.

    `bytes.decode("utf-8", errors="replace")` remplaçait silencieusement tout
    octet non-UTF-8 par « � » (U+FFFD) — un « · », un « œ » ou un accent perdu
    au décodage se retrouvait figé en base, illisible et non réparable a
    posteriori. Ici on essaie les encodages plausibles dans l'ordre avant de
    céder au remplacement destructeur : UTF-8 (avec BOM éventuel), puis
    cp1252 (défaut Windows — Word/Excel/Bloc-notes FR exportent souvent en
    cp1252), puis en dernier recours seulement le remplacement, pour ne jamais
    lever d'erreur sur un import utilisateur."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


GLOBAL_FIELD_KEYWORDS = {
    "contexte": "contexte",
    "culture": "culture_adn",
    "force": "forces_succes",
    "succ": "forces_succes",
    "amélioration": "points_amelioration",
    "amelioration": "points_amelioration",
    "douleur": "points_amelioration",
    "aspiration": "aspirations",
    "baguette": "aspirations",
}

# Ordre important : "proposition de valeur" doit être vérifié avant "valeur"
# seul, sans quoi la fiche de valeur/complexité serait mal détectée.
RECO_FIELD_PREFIXES = [
    ("proposition de valeur", "proposition_valeur"),
    ("valeur", "valeur"),
    ("complexité", "complexite"),
    ("complexite", "complexite"),
    ("objectif", "objectif"),
    ("acteurs", "acteurs"),
    ("plan d'action", "plan_actions"),
    ("plan d’action", "plan_actions"),
    ("résultats attendus", "resultats_attendus"),
    ("resultats attendus", "resultats_attendus"),
]

_H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_H3_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)
_H4_RE = re.compile(r"^####\s+(.+)$", re.MULTILINE)
_H5_RE = re.compile(r"^#####\s+(.+)$", re.MULTILINE)
_BULLET_RE = re.compile(r"^-\s*([^:]+):\s*(.*)$")


class AnalysisParseError(RuntimeError):
    """Le fichier importé n'a pas la structure attendue — message pour l'UI."""


def _split_sections(text: str, heading_re: re.Pattern) -> list[tuple[str, str]]:
    """Découpe `text` sur les titres capturés par `heading_re` : renvoie une
    liste de (titre, contenu jusqu'au prochain titre du même niveau)."""
    matches = list(heading_re.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1).strip(), text[start:end]))
    return sections


def _match_global_field(title: str) -> str | None:
    lowered = title.lower()
    for keyword, field in GLOBAL_FIELD_KEYWORDS.items():
        if keyword in lowered:
            return field
    return None


def _match_reco_field(label: str) -> str | None:
    lowered = label.strip().lower()
    for prefix, field in RECO_FIELD_PREFIXES:
        if lowered.startswith(prefix):
            return field
    return None


def _extract_title(heading_text: str) -> str:
    """« Axe 1 : Cohérence du cadre » -> « Cohérence du cadre » (tout après
    le premier ':' ; le libellé entier si pas de ':')."""
    if ":" in heading_text:
        return heading_text.split(":", 1)[1].strip()
    return heading_text.strip()


def _parse_recommendation_block(title: str, block: str) -> dict:
    fields = {
        "title": title,
        "objectif": "",
        "acteurs": "",
        "valeur": 3,
        "complexite": 3,
        "proposition_valeur": "",
        "plan_actions": "",
        "resultats_attendus": "",
    }
    current_field: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current_field and current_field not in ("valeur", "complexite"):
            fields[current_field] = "\n".join(buffer).strip()

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_field:
                buffer.append("")
            continue
        m = _BULLET_RE.match(stripped)
        matched_field = _match_reco_field(m.group(1)) if m else None
        if matched_field:
            flush()
            current_field = matched_field
            value = m.group(2).strip()
            if matched_field in ("valeur", "complexite"):
                digits = re.search(r"\d+", value)
                fields[matched_field] = max(1, min(5, int(digits.group()))) if digits else 3
                buffer = []
            else:
                buffer = [value] if value else []
        elif current_field:
            buffer.append(stripped)
    flush()

    return fields


def _parse_recommendations_section(text: str) -> list[dict]:
    axes = []
    for axis_heading, axis_block in _split_sections(text, _H4_RE):
        axis_title = _extract_title(axis_heading)
        recommendations = [
            _parse_recommendation_block(_extract_title(reco_heading), reco_block)
            for reco_heading, reco_block in _split_sections(axis_block, _H5_RE)
        ]
        if recommendations:
            axes.append({"title": axis_title, "recommendations": recommendations})
    return axes


def parse_analysis_markdown(text: str) -> dict:
    """Retourne {"global_synthesis": {...5 clés...}, "axes": [...]}.

    Lève `AnalysisParseError` si aucune des sections attendues n'est trouvée
    (le fichier n'a pas conservé la structure de titres du gabarit exporté).
    """
    global_synthesis = {
        "contexte": "",
        "culture_adn": "",
        "forces_succes": "",
        "points_amelioration": "",
        "aspirations": "",
    }
    axes: list[dict] = []
    found_any = False

    for h2_title, h2_body in _split_sections(text, _H2_RE):
        lowered = h2_title.lower()
        if "synthèse" in lowered or "synthese" in lowered:
            for h3_title, h3_body in _split_sections(h2_body, _H3_RE):
                field = _match_global_field(h3_title)
                if field:
                    global_synthesis[field] = h3_body.strip()
                    found_any = True
        elif "recommandation" in lowered:
            axes = _parse_recommendations_section(h2_body)
            if axes:
                found_any = True

    if not found_any:
        raise AnalysisParseError(
            "Structure du fichier non reconnue — conservez les titres "
            "« ## SYNTHÈSE GLOBALE », « ### Contexte », « ## RECOMMANDATIONS », "
            "« #### Axe N », « ##### Recommandation N.M » générés par l'export, "
            "sans les modifier."
        )

    return {"global_synthesis": global_synthesis, "axes": axes}
