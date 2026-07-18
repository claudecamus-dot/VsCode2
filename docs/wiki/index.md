---
updated: 2026-06-30
confidence: confirmed
agents: [onboarder]
---

# Interview-to-Deck — Index Wiki

## Stack critique

Python 3.12+ · FastAPI 0.115+ · SQLAlchemy 2.0 / SQLite · Jinja2 + HTMX 2.0 · Ollama (local, llama3.1, fournisseur IA par défaut depuis le 2026-07-17 — le SDK Anthropic a été retiré du code et des dépendances suite à une limite de crédit atteinte par l'utilisateur ; OpenAI/Mistral restent disponibles comme fournisseurs alternatifs) · reportlab (export PDF par entretien)
— `CONFIRMÉ` · claude · 2026-07-17 · `requirements.txt`, `app/services/ai_common.py`

## Architecture (résumé)

Monolithe Python SSR. FastAPI route → Service → Template Jinja2 + HTMX.
Modèle `Mission` → `Trame` → `Theme` → `Question` ; entités `Interview`/`Answer`/`Verbatim`/`Synthesis`/`AgentResult`.
Import .docx non-destructif, synthèse IA ou mode démo, intégration OpenHub pour invoquer des agents.
— `CONFIRMÉ` · onboarder · 2026-06-30

## God nodes — concepts les plus connectés

| Concept | Pages liées | Criticité |
|---------|-------------|-----------|
| Mission | [technical/architecture.md](technical/architecture.md), [business/index.md](business/index.md) | Critique |
| Synthèse IA | [technical/architecture.md](technical/architecture.md), [technical/stack.md](technical/stack.md) | Haute |

## Carte des domaines métier

- [Entretien & Synthèse](business/index.md) — Capture d'interviews sur trame, synthèse transverse, génération PPT

## Incrément 9 (2026-07 — non couvert en détail par ce wiki, voir `CLAUDE.md` à la racine)

Entrée unifiée entretien libre/structuré + mission différée. Entretien libre : capture audio locale (faster-whisper) → extraction IA en 2 étapes (tours de parole puis répartition/résumé, `interview_libre_extract_ai.py`) → écrans Analyse/Synthèse dédiés, redesignés le 2026-07-17 pour clarifier qu'ils montrent l'apport d'**un seul** entretien, distinct de la synthèse globale de mission (`synthese/globale.html`) qui agrège tous les entretiens. Export PDF par entretien (`interview_pdf_export.py`, reportlab). Fournisseur IA par défaut passé d'Anthropic à Ollama (local) le 2026-07-17.
— `CONFIRMÉ` · claude · 2026-07-17 · `.roadmap/roadmap.json`, `CLAUDE.md`

## Points critiques actifs 🔴

- Aucune authentification — accessible sans restriction
- Pas de CI/CD ni de pipeline de déploiement
- Synthèse globale de mission (`synthese_ai.py::generate_global_synthesis`) construit un seul prompt sans découpage — contrairement à l'extraction par entretien libre (map-reduce depuis le 2026-07-16), une mission avec beaucoup d'entretiens/matière risque un dépassement silencieux de la fenêtre de contexte Ollama (`ollama_num_ctx()`, 8192 tokens par défaut, troncature documentée sans erreur) ; pas encore de garde-fou pour ce chemin
  — `DÉDUIT` · claude · 2026-07-17 · `app/services/synthese_ai.py:206-252`

## Zones d'ombre

- Déploiement : aucun runbook ni CI/CD détecté
- Tests : pas de seuil de couverture configuré
- Conventions de code : pas de linting/formatting imposé
- Environnements : dev local seulement (pas de staging/prod)

<!-- TODO-AGENTS:START — section générée par .claude/supervision/scan_transcripts.py, ne pas éditer à la main -->
## TODO agents 🤖

Constats automatiques du superviseur d'agents (usage mesuré dans les transcripts de session) :

- **Trier les skills BMAD** : 39 installés, 0 invocation à ce jour — décider lesquels garder, customiser ou désinstaller.
- **Skills projet sans usage** : `pptx-framed-image`, `slide-text-polish` — vérifier pertinence et déclencheurs.

Tableau de bord complet : [technical/agents-supervision.md](technical/agents-supervision.md) — régénéré à chaque session.
<!-- TODO-AGENTS:END -->
