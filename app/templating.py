"""Instance Jinja2Templates partagée par les routeurs."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .models import (
    ANSWER_STATUS_LABELS,
    QUESTION_TYPE_LABELS,
    SYNTHESIS_STATUS_LABELS,
)
from .services.mode import est_mode_demo
from .services.pptx_export import field_fit_hint

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Exposé aux templates pour afficher les libellés.
templates.env.globals["QUESTION_TYPE_LABELS"] = QUESTION_TYPE_LABELS
templates.env.globals["ANSWER_STATUS_LABELS"] = ANSWER_STATUS_LABELS
templates.env.globals["SYNTHESIS_STATUS_LABELS"] = SYNTHESIS_STATUS_LABELS
# Permet à apercu.html de calculer le repère "forme" dès le rendu initial
# (GET) — avant, il n'apparaissait qu'après un premier edit HTMX (le hint
# n'était calculé que côté route POST /field), invisible tant qu'on relit
# sans modifier (revue UX du 2026-07-16).
templates.env.globals["field_fit_hint"] = field_fit_hint


def _pluriel(count, suffixe: str = "s") -> str:
    """Filtre d'accord : « 2 thème{{ n|pluriel }} » → « 2 thèmes », « 1 thème ».

    Remplace les « (s) » littéraux des compteurs (« 1 recommandation(s) »),
    peu soignés pour un outil montré en clientèle (revue UX 2026-07-23 P2-13).
    """
    try:
        # float d'abord : accepte "3" comme "3.0" (revue adversariale 2026-07-23 —
        # int("3.0") lève ValueError et rendait silencieusement le singulier).
        n = int(float(count))
    except (TypeError, ValueError):
        return ""
    return suffixe if n > 1 else ""


templates.env.filters["pluriel"] = _pluriel
# Contrat du mode démo centralisé (services/mode.py) exposé aux templates —
# base.html et entree.html recopiaient le nom du cookie et sa sémantique en dur
# (3 copies divergables, revue adversariale 2026-07-23).
templates.env.globals["est_mode_demo"] = est_mode_demo
