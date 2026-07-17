"""Instance Jinja2Templates partagée par les routeurs."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .models import (
    ANSWER_STATUS_LABELS,
    QUESTION_TYPE_LABELS,
    SYNTHESIS_STATUS_LABELS,
)
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
