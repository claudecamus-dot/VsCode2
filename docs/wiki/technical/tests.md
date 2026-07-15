---
updated: 2026-07-10
confidence: confirmed
agents: [onboarder]
---

# Stratégie de tests — Interview-to-Deck

## Frameworks

- Unitaires : pytest + FastAPI TestClient
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `tests/test_mission_trame_flow.py`
- E2E : Aucun
  — `CONFIRMÉ` · onboarder · 2026-06-30

## Organisation

- Dossier `tests/` séparé, 5 fichiers : `test_mission_trame_flow.py`, `test_ai_common.py`, `test_interview_extract_ai.py`, `test_pptx_deck.py`, `conftest.py`
  — `CONFIRMÉ` · onboarder · 2026-07-10

## Seuil de couverture

- Non configuré — pas de `pytest.ini` ou `pyproject.toml` avec seuil
  — `CONFIRMÉ` · onboarder · 2026-06-30

## Philosophie

- Test-after : les tests sont écrits après l'implémentation (aucun label Beads TDD, pas de mention TDD dans README)
  — `DÉDUIT` · onboarder · 2026-06-30

## Tests existants (56 tests)

- `test_mission_trame_flow.py` (32 tests) — flux mission/trame/entretien de bout en bout : création mission, thèmes/questions, import `.docx`, saisie d'entretien et réponses, export PPT, page agents
- `test_ai_common.py` (11 tests) — abstraction `call_ai_json` et sélection de provider IA
- `test_interview_extract_ai.py` (8 tests) — extraction IA de trame/réponses depuis une transcription d'entretien
- `test_pptx_deck.py` (5 tests) — helpers de mise en page du générateur PPT (`pptx_deck.py`)
  — `CONFIRMÉ` · onboarder · 2026-07-10 · `tests/`

## Commandes

```bash
# Tests unitaires
pytest -q

# Test unique
pytest tests/test_mission_trame_flow.py::test_export_pptx_produces_valid_file

# Sous-ensemble par mot-clé
pytest -k "pptx or export"

# Couverture (non configuré)
# pytest --cov=app
```

— `CONFIRMÉ` · onboarder · 2026-07-10

## Conventions de nommage

- Fichiers test : `test_*.py`
- Fonctions test : `test_*` (convention pytest par défaut)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `tests/test_mission_trame_flow.py`
