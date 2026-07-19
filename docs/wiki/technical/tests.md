---
updated: 2026-07-19
confidence: confirmed
agents: [onboarder, claude]
---

# Stratégie de tests — Interview-to-Deck

## Frameworks

- Unitaires : pytest + FastAPI TestClient
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `tests/test_mission_trame_flow.py`
- E2E : Aucun
  — `CONFIRMÉ` · onboarder · 2026-06-30

## Organisation

- Dossier `tests/` séparé, 13 fichiers (12 `test_*.py` + `conftest.py`, qui isole `APP_DB_PATH` avant tout import de `app.*` — jamais `data/app.db`, la base dev réelle)
  — `CONFIRMÉ` · claude · 2026-07-19 · `tests/`

## Seuil de couverture

- Non configuré — pas de `pytest.ini` ou `pyproject.toml` avec seuil
  — `CONFIRMÉ` · onboarder · 2026-06-30

## Philosophie

- Test-after : les tests sont écrits après l'implémentation (aucun label Beads TDD, pas de mention TDD dans README)
  — `DÉDUIT` · onboarder · 2026-06-30

## Tests existants (154 tests)

- `test_interview_libre.py` (34 tests) — extraction IA de l'entretien libre (tours/répartition), flux HTTP complet (wizard 3 étapes, boutons de retour non destructifs), régénération d'analyse, nettoyage des brouillons
- `test_mission_trame_flow.py` (33 tests) — flux mission/trame/entretien de bout en bout : création mission, thèmes/questions, import `.docx`, saisie d'entretien et réponses, synthèse globale/recommandations, export PPT (dont un test de rendu réel via LibreOffice, ~15 min), page agents
- `test_ai_common.py` (21 tests) — abstraction `call_ai_json`, sélection de provider IA, timeout Ollama
- `test_agent_supervision.py` (14 tests) — scan déterministe étage 1, routing-hints, diagnostic étage 2 (`write_diagnostic.py`)
- `test_agent_orchestration.py` (13 tests) — journal `log_run.py`, hook `orchestrator_gate.py`, playbooks, inventaire git des agents
- `test_interview_extract_ai.py` (8 tests) — extraction IA de trame/réponses depuis une transcription d'entretien
- `test_audio_transcribe_edge_cases.py` (8 tests) — cas limites de la transcription audio locale
- `test_interview_pdf_export.py` (7 tests, 2026-07-19) — export PDF par entretien, contenu réel extrait via `pymupdf`, rendu multiligne
- `test_pptx_deck.py` (5 tests) — helpers de mise en page du générateur PPT (`pptx_deck.py`)
- `test_audio_transcribe_parallel.py` (5 tests) — transcription audio en parallèle
- `test_synthese_ai_global.py` (4 tests, 2026-07-19) — map-reduce de la synthèse globale de mission
- `test_audio_transcribe_tts_smoke.py` (2 tests) — pipeline réel via un clip TTS généré
  — `CONFIRMÉ` · claude · 2026-07-19 · `tests/` (`pytest --collect-only -q`)

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
