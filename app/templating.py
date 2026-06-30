"""Instance Jinja2Templates partagée par les routeurs."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .models import (
    ANSWER_STATUS_LABELS,
    QUESTION_TYPE_LABELS,
    SYNTHESIS_STATUS_LABELS,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Exposé aux templates pour afficher les libellés.
templates.env.globals["QUESTION_TYPE_LABELS"] = QUESTION_TYPE_LABELS
templates.env.globals["ANSWER_STATUS_LABELS"] = ANSWER_STATUS_LABELS
templates.env.globals["SYNTHESIS_STATUS_LABELS"] = SYNTHESIS_STATUS_LABELS
