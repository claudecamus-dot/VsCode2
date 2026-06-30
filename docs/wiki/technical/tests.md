---
updated: 2026-06-30
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

- Dossier `tests/` séparé, un seul fichier : `test_mission_trame_flow.py`
  — `CONFIRMÉ` · onboarder · 2026-06-30

## Seuil de couverture

- Non configuré — pas de `pytest.ini` ou `pyproject.toml` avec seuil
  — `CONFIRMÉ` · onboarder · 2026-06-30

## Philosophie

- Test-after : les tests sont écrits après l'implémentation (aucun label Beads TDD, pas de mention TDD dans README)
  — `DÉDUIT` · onboarder · 2026-06-30

## Tests existants (6 tests)

1. `test_create_mission_and_view_trame` — Création mission + consultation trame
2. `test_add_theme_and_question` — Ajout thème + question
3. `test_import_docx_creates_theme_and_questions` — Import .docx en mémoire
4. `test_agents_page_lists_available_skills` — Page agents liste les skills
5. `test_dynamic_skill_execution_uses_opencode_runtime` — Mock du runtime opencode
6. `test_interview_capture_and_save_answer` — Saisie d'entretien + réponse

## Commandes

```bash
# Tests unitaires
pytest -v

# Couverture (non configuré)
# pytest --cov=app
```

— `CONFIRMÉ` · onboarder · 2026-06-30

## Conventions de nommage

- Fichiers test : `test_*.py`
- Fonctions test : `test_*` (convention pytest par défaut)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `tests/test_mission_trame_flow.py`
