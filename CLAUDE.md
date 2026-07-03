# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Interview-to-Deck: an internal tool for consultants running qualitative interviews during audits/studies. Pipeline: capture interviews against a structured trame (Mission → Trame → Theme → Question) → cross-interview AI synthesis → PowerPoint restitution deck generated from a corporate template. Codebase and docs are French; domain vocabulary (`trame`, `synthèse`, `verbatim`, `recommandation`, `axe`) is established product terminology — keep it as-is, don't translate/anglicize.

Progress tracking lives in `.roadmap/roadmap.json` (rendered via the `roadmap-keeper` skill). Deeper onboarding docs are in `docs/wiki/` (architecture, conventions, stack) but can lag behind the actual code/git history — verify against `git log`/`git status` rather than trusting either as current truth.

## Commands

```bash
# Setup
python -m venv .venv
.venv\Scripts\activate                 # Windows; .venv/bin/activate on POSIX
pip install -r requirements.txt -r requirements-dev.txt

# Run the dev server (auto-reload), http://127.0.0.1:8000/missions
uvicorn app.main:app --reload

# Tests (FastAPI TestClient + pytest, disposable SQLite DB — never touches data/app.db)
pytest -q
pytest tests/test_mission_trame_flow.py::test_export_pptx_produces_valid_file  # single test
pytest -k "pptx or export"             # subset by keyword
```

There is no linter/formatter configured (no ruff/black/eslint) and no pre-commit hooks — match the surrounding file's existing style.

## Architecture

**Request flow**: FastAPI route (`app/routers/*.py`) → service function (`app/services/*.py`) → Jinja2 template (`app/templates/`), server-rendered with HTMX 2.0 for partial updates (autosave fields, dynamic panels). No SPA/build step on the frontend.

**Data model** (`app/models.py`, SQLAlchemy 2.0 / SQLite): `Mission` owns one `Trame` (→ `Theme` → `Question`) and many `Interview` (→ `Answer`/`Verbatim`). A mission also has one `GlobalSynthesis` (5 fixed categories: contexte, culture_adn, forces_succes, points_amelioration, aspirations) and several `RecommendationAxis` (→ `Recommendation`, scored 1-5 on valeur/complexité). The PPT export and the web "aperçu" editor are two different renderings of the same `GlobalSynthesis`/`RecommendationAxis`/`Recommendation` data — there is no separate "slide" entity.

**AI provider abstraction** (`app/services/ai_common.py`): all AI calls (theme/global synthesis, recommendations, trame/interview extraction) go through one `call_ai_json(system, prompt, schema, json_hint, error_cls=...)` function. The active provider is chosen by `AI_PROVIDER` (anthropic/openai/mistral, default anthropic) with no automatic fallback between providers — a missing key raises a clear error naming the exact env var expected (`api_key_env_name()`). `synthese_ai.py`, `trame_extract_ai.py`, `interview_extract_ai.py` each define their own system prompt/schema/error class but never touch an SDK directly. `SYNTHESE_DEMO=1` enables a rule-based offline fallback (no AI, no cost) used when no key is configured.

**PPT export** (`app/services/pptx_export.py` + `app/services/pptx_deck.py`): `build_presentation()` either starts from a client-uploaded `.pptx` template (inherits theme/masters/logo — `_clear_slides()` must drop each removed slide's relationship via `prs.part.drop_rel()`, not just clear `sldIdLst`, or PowerPoint silently refuses to open the file even though python-pptx parses it fine) or builds a blank 16:9 deck (in which case the native title placeholder, inherited from python-pptx's 4:3 default, is repositioned explicitly per-slide in `_new_slide()` rather than by mutating layout/master placeholders, which is unreliable). Every slide-building function pulls sizing from `pptx_deck.D.TYPE` (the one type scale) and adapts font size to content length via `D.ajuster_police`/`D.estimer_lignes`/`D.tronquer_a_lignes` rather than assuming fixed line counts. `build_presentation()` always ends with `D.verifier_geometrie()`, raising if any shape ends up off-slide. A `.pptx` that parses in python-pptx and passes this check can still fail to open in real PowerPoint or look wrong visually — always verify a real export with the `pptx-verify` skill (render + eyeball) before considering a layout change done. See the `pptx-deck` and `restitution-deck-design` skills for the design system this generator follows.

**Web editor / PPT preview parity**: `app/templates/synthese/apercu.html` is a tabbed editor (one tab per slide) whose form fields reuse the same autosave endpoints as the rest of the app (`/syntheses/globale/{mission_id}/field`, `/recommandations/{id}/field`, `/recommandations/axes/{id}/field`). The "shape-aware" hint shown under each field comes from `pptx_export.field_fit_hint()`, which reuses the exact geometry constants and font-fitting functions the real PPT generator uses — treat that coupling as intentional; if you change a slide's layout constants in `pptx_export.py`, update `FIELD_SHAPE` too or the hint will drift from reality.

**OpenHub agents integration** (`app/services/openhub_agents.py`) is unrelated to Claude Code's own skill/agent system — it shells out to an external `opencode` CLI (`.opencode/agents/`, `.opencode/skills/`) to power the app's own "Agents" page, with a simulated-response fallback when `opencode` isn't on PATH.
