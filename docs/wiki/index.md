---
updated: 2026-07-19
confidence: confirmed
agents: [onboarder, claude]
---

# Interview-to-Deck — Index Wiki

## Exemple de restitution générée

Un deck de restitution complet produit par le générateur (données de démo, template de marque OCTO) : couverture éditoriale, sommaire quali chapitré, executive summary, synthèse, difficultés, SWOT, verbatims, axes et fiches de recommandation — avec les **chips** et **badges-icônes** de la charte (VSCode4). Télécharger : [`docs/exemples/deck-restitution-exemple.pptx`](../exemples/deck-restitution-exemple.pptx) (.pptx, ~4 Mo).
— `EXEMPLE` · généré par `app/services/pptx_export.py` · 2026-07-22

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
| Orchestrateur/superviseur d'agents | [technical/architecture.md](technical/architecture.md), [technical/agents-supervision.md](technical/agents-supervision.md) | Haute (outillage Claude Code, pas le produit lui-même) |

## Carte des domaines métier

- [Entretien & Synthèse](business/index.md) — Capture d'interviews sur trame, synthèse transverse, génération PPT

## Incrément 9 — entrée unifiée + entretien libre (2026-07-15 → 2026-07-19)

Entrée unifiée entretien libre/structuré + mission différée. Entretien libre : capture audio locale (faster-whisper) → extraction IA en 2 étapes (tours de parole puis répartition/résumé, `interview_libre_extract_ai.py`) → écran de lecture unique par entretien (`libre_analyse.html`, libellé « Aperçu » depuis le 2026-07-19), montrant l'apport d'**un seul** entretien, distinct de la synthèse globale de mission (`synthese/globale.html`) qui agrège tous les entretiens. Export PDF par entretien (`interview_pdf_export.py`, reportlab — couvert par des tests et corrigé le 2026-07-19 pour le rendu multiligne). Fournisseur IA par défaut passé d'Anthropic à Ollama (local) le 2026-07-17.

Suites livrées après le cadrage initial : régénération contrôlée de l'analyse d'un entretien libre (écran de revue avant écrasement, 2026-07-18) ; map-reduce de la synthèse globale de mission (résout le risque de troncature silencieuse noté ci-dessous jusqu'au 2026-07-18) ; nettoyage des missions brouillon abandonnées et boutons de retour non destructifs dans le wizard libre (2026-07-18) ; passe UX (7 correctifs : garde-fou régénération synthèse/recommandations, cohérence des libellés, breadcrumbs, 2026-07-19).
— `CONFIRMÉ` · claude · 2026-07-19 · `.roadmap/roadmap.json`, `CLAUDE.md`

## Outillage Claude Code du projet (2026-07-17/19 — hors produit, périmètre `.claude/`)

BMAD-METHOD installé (39 skills `bmad-*` après tri, routage sur demande explicite) et un système de supervision/orchestration d'agents propre au projet : scan déterministe de l'usage réel des skills/sous-agents (étage 1, 0 token), diagnostic qualitatif à la demande (étage 2, `agent-supervisor`), orchestrateur par défaut des demandes multi-étapes (`agent-orchestrator`, playbooks déclaratifs). Voir [technical/architecture.md](technical/architecture.md#supervision-et-orchestration-des-agents-2026-07-1718-incréments-o-a-à-o-c) pour le détail, et la section « Supervision des agents » de ce wiki pour le tableau de bord vivant.
— `CONFIRMÉ` · claude · 2026-07-19 · `.claude/skills/agent-orchestrator/`, `.claude/skills/agent-supervisor/`

## Points critiques actifs 🔴

- Aucune authentification — accessible sans restriction
- Pas de CI/CD ni de pipeline de déploiement
- Vitesse d'inférence Ollama sur poste CPU sans GPU dédié — un entretien de ~37min a atteint `OLLAMA_TIMEOUT` avant l'ajout du map-reduce (voir juste en dessous) ; la marge reste faible sur les très gros entretiens
  — `CONFIRMÉ` · claude · 2026-07-19 · `app/services/ai_common.py`

~~Synthèse globale de mission sans découpage map-reduce~~ — **résolu le 2026-07-18**, `generate_global_synthesis()` applique désormais le même map-reduce que l'extraction par entretien libre.
  — `CONFIRMÉ` · claude · 2026-07-19 · `app/services/synthese_ai.py:_chunk_blocks,_reduce_partial_globals`

## Zones d'ombre

- Déploiement : aucun runbook ni CI/CD détecté
- Tests : pas de seuil de couverture configuré
- Conventions de code : pas de linting/formatting imposé
- Environnements : dev local seulement (pas de staging/prod)

<!-- TODO-AGENTS:START — section générée par .claude/supervision/scan_transcripts.py, ne pas éditer à la main -->
## TODO agents 🤖

Constats automatiques du superviseur d'agents (usage mesuré dans les transcripts de session) :

- **Skills projet sans usage** : `deck-design-library`, `priority-matrix`, `swot-matrix` — vérifier pertinence et déclencheurs.

Tableau de bord complet : [technical/agents-supervision.md](technical/agents-supervision.md) — régénéré à chaque session.
<!-- TODO-AGENTS:END -->
