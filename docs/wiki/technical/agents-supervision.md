---
updated: 2026-07-22
generated-by: .claude/supervision/scan_transcripts.py (superviseur d'agents, étage 1)
---

# Supervision des agents — tableau de bord d'usage

> ⚠️ **Page générée automatiquement** (hook SessionStart → `.claude/supervision/scan_transcripts.py`).
> **Ne pas éditer à la main** — toute modification serait écrasée au prochain scan.
> Conception et phasage : [../../reflexions/agent-superviseur.md](../../reflexions/agent-superviseur.md).

Dernier scan : 2026-07-22T21:36:13+02:00 · **36 sessions** (transcripts) · **77** invocations de skills · **48** lancements de sous-agents.

## Skills — usage réel

| Skill | Famille | Invocations | Première | Dernière |
| --- | --- | --- | --- | --- |
| `run-dev-server` | projet | 20 | 2026-07-03 | 2026-07-22 |
| `agent-orchestrator` | projet | 9 | 2026-07-17 | 2026-07-22 |
| `agent-supervisor` | projet | 8 | 2026-07-18 | 2026-07-21 |
| `pptx-verify` | global | 6 | 2026-07-03 | 2026-07-22 |
| `revue-increment` | projet | 6 | 2026-07-18 | 2026-07-22 |
| `update-config` | (builtin/session) | 6 | 2026-07-03 | 2026-07-16 |
| `bmad-code-review` | BMAD | 5 | 2026-07-20 | 2026-07-22 |
| `roadmap-keeper` | global | 5 | 2026-06-25 | 2026-07-15 |
| `run` | (builtin/session) | 3 | 2026-06-29 | 2026-07-03 |
| `pptx-deck` | global | 2 | 2026-07-02 | 2026-07-03 |
| `skill-creator` | global | 2 | 2026-07-03 | 2026-07-03 |
| `slide-text-polish` | projet | 2 | 2026-07-22 | 2026-07-22 |
| `claude-api` | (builtin/session) | 1 | 2026-06-29 | 2026-06-29 |
| `init` | (builtin/session) | 1 | 2026-07-03 | 2026-07-03 |
| `restitution-deck-design` | global | 1 | 2026-07-22 | 2026-07-22 |

## Sous-agents

| Sous-agent | Lancements | Premier | Dernier |
| --- | --- | --- | --- |
| `Explore` | 20 | 2026-06-30 | 2026-07-22 |
| `general-purpose` | 20 | 2026-07-15 | 2026-07-22 |
| `claude` | 4 | 2026-07-16 | 2026-07-16 |
| `Plan` | 3 | 2026-07-06 | 2026-07-17 |
| `claude-code-guide` | 1 | 2026-07-03 | 2026-07-03 |

## Jamais utilisés

**projet** — 5/10 jamais invoqués :

`deck-design-library`, `deck-design-review`, `pptx-framed-image`, `priority-matrix`, `swot-matrix`

**BMAD** — 38/39 jamais invoqués :

<details><summary>Voir la liste</summary>

`bmad-advanced-elicitation`, `bmad-agent-analyst`, `bmad-agent-architect`, `bmad-agent-dev`, `bmad-agent-pm`, `bmad-agent-tech-writer`, `bmad-agent-ux-designer`, `bmad-architecture`, `bmad-brainstorming`, `bmad-check-implementation-readiness`, `bmad-checkpoint-preview`, `bmad-correct-course`, `bmad-create-epics-and-stories`, `bmad-create-story`, `bmad-customize`, `bmad-dev-auto`, `bmad-dev-story`, `bmad-document-project`, `bmad-domain-research`, `bmad-editorial-review-prose`, `bmad-editorial-review-structure`, `bmad-forge-idea`, `bmad-generate-project-context`, `bmad-help`, `bmad-index-docs`, `bmad-market-research`, `bmad-party-mode`, `bmad-prd`, `bmad-prfaq`, `bmad-product-brief`, `bmad-retrospective`, `bmad-review-adversarial-general`, `bmad-review-edge-case-hunter`, `bmad-shard-doc`, `bmad-sprint-planning`, `bmad-sprint-status`, `bmad-technical-research`, `bmad-ux`

</details>

## TODO agents (constats automatiques)

1. **Skills projet sans usage** : `deck-design-library`, `deck-design-review`, `priority-matrix`, `swot-matrix` — vérifier pertinence et déclencheurs.

## Arbitrages enregistrés

_Constats clos par décision humaine (`.claude/supervision/arbitrages.json`) — l'usage réel reste mesuré ci-dessus._

- **`famille:orchestrateur+superviseur`** (2026-07-22) : Demande utilisateur 2026-07-22 (« c'est toujours KO, ma demande n'est pas traitée — fais évoluer supervisor + orchestrator ») : évolutions APPLIQUÉES après ~15 tours de boucle deck non convergente. Orchestrateur §4 : vérif obligatoire « livrable utilisateur = artefact EXACT de l'app (export réel, pas build maison), rendu ENTIER, validé PAR l'utilisateur » + règle de non-convergence (≥3 rejets → demander le défaut précis, ne pas re-deviner). Orchestrateur §5 : nouvel état de run 'en-attente-validation' (jamais 'succes' auto-décerné sur un livrable utilisateur). Superviseur §3 : catégorie 'non-convergence' (constat critique) + write_diagnostic.py l'accepte. Réflexion : docs/reflexions/evolution-agents-acceptance-utilisateur.md. Mémoires feedback-non-convergence-user-is-oracle + feedback-verify-the-real-app-export-all-slides.
- **`export-ppt-verifie`** (2026-07-22) : Rétrospective 2026-07-22 (« charte VSCode4 affirmée de mémoire → add-then-revert ») arbitrée : l'étape CADRAGE du playbook exige désormais, quand la demande cite un deck/charte de référence (VSCode3/4, template client), de RENDRE 2-3 slides de la référence (pptx-verify) et d'en extraire les motifs AVANT d'implémenter — interdit d'affirmer une conformité charte de mémoire. Preuve : 816ab02 (ajoute barre d'accent « charte VSCode4 ») → 09c7ba3 (la retire « VSCode4 n'en a pas »), + sommaire/numéro/encarts corrigés seulement après render VSCode4. Mémoire feedback-ground-charte-claims-in-a-render.
- **`run-dev-server`** (2026-07-22) : Rétrospective 2026-07-22 arbitrée : run-dev-server/SKILL.md documente le repli quand le screenshot Edge échoue (error 577 malgré les flags, ou hang) — se limiter à la vérif de structure servie (curl) et le DIRE explicitement, ne jamais prétendre avoir vu le rendu. Un échec unique de test réel opt-in (LibreOffice/Ollama) sous charge machine se réexécute isolé avant d'être qualifié régression.
- **`restitution-deck-design`** (2026-07-22) : Diagnostic 2026-07-22 (ppt-designer jamais lancé) arbitré : la passe design n'est plus CONDITIONNELLE auto-jugée mais OBLIGATOIRE dès que le diff touche un layout/composant/couleur de slide (seuil objectif, comme pptx-verify) — étape design-review du playbook export-ppt-verifie amendée. Déclencheur : la skill avait 0 invocation malgré 6+ runs deck, d'où un design ad hoc et une qualité insuffisante signalée à répétition. Lancée pour la 1re fois le 2026-07-22 (encarts gris, sommaire teardrop, image propre).
- **`export-ppt-verifie`** (2026-07-22) : Plan d'amélioration demandé (2026-07-22, « pour que mes demandes soient prises en compte ») appliqué : une exigence utilisateur explicite (surtout visuelle) est PERSISTANTE d'un tour au suivant — checklist des éléments demandés reportée d'itération en itération et vérifiée au rendu (revue-increment §1 + contrat verification-rendu du playbook). Une contrainte de gabarit se résout en dessinant l'élément, jamais en l'omettant. Déclencheur : numéro de chapitre écarté 3 fois puis corrigé (commit 482c301). Mémoire feedback-persistent-user-request-draw-dont-omit.
- **`famille:BMAD`** (2026-07-18) : Tri exécuté : 7 skills retirés, 39 conservés (routage sur demande explicite via bmad-help) — docs/reflexions/tri-skills-bmad.md, commit f604c39.
- **`pptx-framed-image`** (2026-07-18) : Conservée malgré zéro invocation — reliée au playbook export-ppt-verifie (étape conditionnelle cadres-photo).
- **`slide-text-polish`** (2026-07-18) : Conservée malgré zéro invocation — reliée au playbook export-ppt-verifie (étape conditionnelle polish-texte).
- **`restitution-deck-design`** (2026-07-18) : Conservée malgré zéro invocation — reliée au playbook export-ppt-verifie (étape conditionnelle design-review).
- **`bmad-code-review`** (2026-07-19) : Proposition retenue telle quelle : seuil explicite ajouté à revue-increment/SKILL.md (>5 fichiers produit ou logique à risque -> bmad-code-review obligatoire, sinon revue inline) — la délégation implicite ne se déclenchait jamais en pratique.
- **`ai_common.py`** (2026-07-19) : Proposition retenue telle quelle : règle explicite ajoutée à revue-increment/SKILL.md §2 (correctif timeout/perf IA -> mesurer à la taille maximale réellement configurée, pas un prompt jouet) — capitalisée en mémoire feedback-ai-timeout-fix-verify-at-configured-scale.
- **`revue-increment`** (2026-07-20) : Proposition retenue telle quelle : règle ajoutée à revue-increment/SKILL.md §2 — un correctif appliqué en réponse à une revue externe (bmad-code-review, sous-agent adversarial) doit être relu lui-même avant commit, la revue d'origine n'ayant validé que le code d'avant. Confirmé le jour même : un 4e bug a été trouvé ainsi (Palier 2), non vu par les 2 sous-agents de revue.
- **`revue-increment`** (2026-07-20) : Proposition retenue telle quelle : règle ajoutée à revue-increment/SKILL.md §4 — si les docs auto-générées de supervision (agents-supervision.md, wiki.html) apparaissent modifiées en git status en fin de séance, les inclure dans le commit plutôt que les laisser dériver (constaté : 4 commits consécutifs sans les inclure).
- **`bmad-code-review`** (2026-07-20) : Proposition retenue telle quelle : angle mort de mesure documenté dans catalogue.md — les sous-skills bmad-review-adversarial-general/bmad-review-edge-case-hunter, invoquées par des sous-agents via prompt naturel, n'apparaissent pas dans state.json ; absence de trace n'implique pas absence d'exécution.
- **`revue-design-parallele`** (2026-07-20) : Proposition ① du diagnostic 2026-07-20 retenue et appliquée : garde déterministe ajoutée au contrat de l'étape consolidation (playbook revue-design-parallele) — quand le fan-out sert à une énumération exhaustive AVANT suppression/renommage, un grep -r final de chaque identifiant retiré est obligatoire et prime sur les rapports des sous-agents (extraits, non exhaustifs). Preuve : réf bmad-spec (bmad-architecture:29) ratée par un fan-out de 3 Explore, rattrapée seulement au grep final avant un git rm (runs 2026-07-18).
- **`revue-increment`** (2026-07-20) : Règle définie sur demande (analyse superviseur 2026-07-20, mode d'échec « tests indiqués OK alors qu'ils sont KO ») : revue-increment/SKILL.md §2 + contrat 'tests' du playbook dev-verifie — le verdict pass/fail se lit sur la ligne de synthèse RÉELLE de pytest, jamais sur un résumé filtré du proxy rtk (déjà mal reporté un run, mémoire feedback-rtk-pytest-false-no-tests-collected) ni un [100%] de sortie tronquée ; et une suite verte qui MOCKE l'intégration modifiée (ex. extract_turns_from_text, appels Ollama/Whisper) exige au moins un passage réel de bout en bout avant « livré ».
- **`revue-increment`** (2026-07-20) : Règle définie sur demande (analyse superviseur 2026-07-20, mode d'échec « dev avec bug ou ne respectant pas la demande ») : revue-increment/SKILL.md §1 (conformité exigence par exigence — chaque point explicite de la demande coché contre le diff, toute exigence écartée dite dans « Reste ») + §7 (demande référençant un état introuvable -> clarifier avant de coder, run 12) + contrat 'implementation' du playbook dev-verifie. Le volet « bug malgré tests verts » reste couvert par le seuil bmad-code-review (>5 fichiers produit / logique à risque) et la relecture du correctif lui-même (arbitrages 2026-07-19/20).
- **`revue-increment`** (2026-07-21) : Constat sanity du 2026-07-21 (priorité 3) validé et appliqué : le seuil bmad-code-review de revue-increment/SKILL.md déclenche désormais une revue adversariale pour TOUTE modification du JS de concurrence de record_libre.html / record.html (MediaRecorder, timers de rotation, compteurs pendingX, gardes de ré-entrance/génération), quel que soit le nombre de fichiers — le risque y est par fichier (concurrence), et ce fichier a un historique de 7 bugs data-loss trouvés par revue adversariale (runs 20/22) qu'une modif à 1 fichier (run 23) avait failli contourner en auto-relecture seule.
- **`pytest`** (2026-07-21) : Proposition superviseur du 2026-07-21 appliquée : cause racine du crash de teardown pytest Windows neutralisée dans tests/conftest.py (monkeypatch gardé de cleanup_dead_symlinks, rendu non fatal) — une suite complète sort désormais exit 0 avec la ligne « N passed » visible (vérifié : 246 passed, aucun traceback). Remplace la vigilance manuelle documentée dans feedback-pytest-windows-teardown-noise (3 fausses alertes rien qu'au 2026-07-21).
- **`runs.jsonl`** (2026-07-21) : Proposition superviseur du 2026-07-21 appliquée : critères d'issue de run définis dans agent-orchestrator/SKILL.md §5 (succes = livrable produit + exigences explicites couvertes + vérifications obligatoires faites ; partiel = au moins une exigence non livrée, une vérif sautée, ou une escalade non résolue à la remise ; echec = objectif non atteint / run abandonné). Run à escalade PR non résolue re-étiqueté « partiel » (runs.jsonl : 28 succes / 1 partiel) — le champ toujours-succes ne portait aucun signal.
- **`revue-increment`** (2026-07-21) : Proposition superviseur du 2026-07-21 appliquée : revue-increment/SKILL.md §2 allégé (règles terses + section « Leçons capitalisées » indexant les [[feedback-*]]) — les war-stories datées vivent dans les mémoires liées, la checklist les référence sans les recopier (net -23 lignes ; sections §1-§7, Phase B, Verdict intactes ; aucune exigence perdue).
- **`revue-increment`** (2026-07-22) : Constat superviseur du 2026-07-22 (prio 3) appliqué : mémoire feedback-self-review-weak-gate-vs-adversarial-review + règle revue-increment/SKILL.md §2 — au-dessus du seuil bmad-code-review (fidélité frontend, JS de concurrence, >5 fichiers produit), l'auto-relecture n'est PAS le gate ; ne pas présenter « prêt à committer » sur self-review + tests verts, lister les zones à risque pour la revue. Preuve : 2 fois (2026-07-21 exec summary, 2026-07-22 bug répartition) un « rien à corriger » — une fois adossé à un harness Node 6/6 vert + pytest vert — a précédé la découverte par bmad-code-review de défauts réels dont une régression.
- **`export-ppt-verifie`** (2026-07-22) : Constat superviseur du 2026-07-22 (prio 1) arbitré : NE PAS rouvrir les arbitrages PPT — la prémisse du playbook (les 3 skills conditionnelles gagnent leur place via export-ppt-verifie) sera exercée lors de la GÉNÉRATION du deck PPT complet à venir, à laquelle ce constat est relié. Une slide isolée ajoutée au générateur existant (ex. Executive Summary, commit 2e43358) se vérifie par pptx-verify seul et ne déclenche pas le playbook, qui vise un livrable = deck de restitution complet.

## Diagnostic qualitatif (étage 2 — `agent-supervisor`)

_Diagnostic à jour — rien à signaler, tous les constats précédents ont été arbitrés._

---

_Étage O-C (croisement modèle × tâche × reprises, exploitation de `runs.jsonl`) : voir `.claude/orchestration/routing-hints.json`, régénéré à chaque session._
