# VSCode2 — Interview-to-Deck

Transforme des entretiens (enregistrés dans l'app ou importés) en un **deck PPT de
restitution** : FastAPI + Jinja2 + HTMX, transcription et IA en local. Livrable principal :
l'export `.pptx` produit par `app/services/pptx_export/`.

## Commandes

Le venv du projet porte les dépendances — `py -m pytest` échoue en collecte
(`ModuleNotFoundError: fastapi`), il pointe le Python global.

```bash
.venv/Scripts/python.exe -m pytest -q                      # suite complète (~7 min)
.venv/Scripts/python.exe -m pytest tests/test_swot.py -q   # un fichier
.venv/Scripts/python.exe -m pytest -q -k "nom_du_test"     # un test unique
```

Sur Windows, ajouter `--basetemp` sur un dossier neuf si le teardown se plaint. Lancer
l'app et regarder un écran : skill `run-dev-server` (un port vierge — un serveur sans
`--reload` sert du code périmé).

Après une édition de `app/X.py`, rejouer aussi les fichiers de test qui CITENT le module
ou la route touchée (`grep -rl "X\." tests/` ou le chemin de route), jamais le seul
fichier neuf — un fichier de test ajouté dans le même lot ne couvre pas encore l'existant
(diagnostic superviseur 2026-09-07 : un décorateur décapité rendu 404, vu seulement par la
suite complète). `tests/test_route_inventory.py` fige la table (méthode, chemin) via
`app.openapi()` et tourne en ~1 s : un décorateur décapité y est rouge immédiatement.

Toute assertion d'ORDRE d'exécution JS passe par le harnais qui exécute réellement le
script (`tests/test_tranches_et_reprise_execution.py`), jamais par un `.index()` sur le
texte brut d'un template — aveugle à la portée d'une fermeture JS
(`feedback_inline_script_closure_appended_code_runs_outside`).

## Claude Code — configuration du projet

- `.claude/settings.json` (versionné) : garde-fou git destructif, rappel de vérif
  réelle avant commit (adapter `_WATCHED_PREFIXES`/`_VERIF_BASH` dans
  `.claude/hooks/warn_verif_before_commit.py` au canal de CE projet), gate
  orchestrateur, scan supervision en SessionStart, deny rules secrets.
- `.claude/skills/` : orchestrateur (compose et exécute les plans multi-étapes),
  superviseur (diagnostic étage 2), revue-increment (definition of done),
  veille-agentic (état de l'art), audit-technique.
- `.claude/agents/` : les sous-agents porteurs que l'orchestrateur dispatche.
- `.claude/supervision/` + `.claude/orchestration/` : dispositif de supervision.
  Journal des orchestrations : `log_run.py` (`--solde` pour requalifier un run en
  attente). Arbitrages humains : `arbitrages.json`.

Le dispositif vient du hub de supervision **VSCode5** : corriger LÀ-BAS puis
re-synchroniser, jamais localement. Les copies locales divergent — mesuré le 2026-09-02
sur le bundle `export/` (supprimé le jour même) : jusqu'à **646 lignes d'écart** avec la
source vivante. Deux fichiers portent la bannière « GÉNÉRÉ — NE PAS ÉDITER LOCALEMENT »
(`.claude/supervision/scan_transcripts.py`, `.claude/orchestration/log_run.py`) : une
correction utile chez eux se signale au hub, elle ne s'écrit pas ici.

## Discipline de gestion des tokens

Le contexte est un cache actif facturé à chaque tour, pas une mémoire gratuite.

- **Ne pas parcourir** `.venv/`, `_bmad/`, `_bmad-output/`, `.claude/skills/bmad-*`
  (skills BMAD installées) sauf demande explicite.
- **Lire avant d'écrire**, grep les appelants avant de modifier une route/service partagé
  (cf. la règle de rejeu des tests ci-dessus, même logique côté lecture).
- **Sous-agent pour toute sortie volumineuse** (suite complète ~7 min, logs longs).
- **`/compact` dès ~40 %** de fenêtre utilisée si la session doit continuer longtemps.
- **`/clear` (pas une 3e rustine) après deux corrections ratées consécutives** sur le
  même problème — repartir à froid avec un meilleur prompt bat l'insistance.

## Règles de travail

Deux familles, **numérotées distinctement** parce qu'elles étaient toutes deux citées
« R1-R4 » et se contredisaient (constat superviseur du 2026-09-02 : une autorité citée
sans texte, et le même numéro pour deux choses).

**R1-R5 — conduite du travail** (citées par `agent-orchestrator/SKILL.md` et `runs.jsonl`) :

- **R1 — Cadrer sur l'état RÉEL avant d'écrire.** Le besoin peut être déjà satisfait, ou
  l'être autrement. Correction minimale > refonte.
- **R2 — Commit scopé au périmètre.** Un correctif ne transporte pas d'isort, de renommage
  ni de passager clandestin.
- **R3 — Gate de revue AVANT le commit, jamais après.** Une revue jouée après un push ne
  protège plus rien, elle documente.
- **R4 — Propose → arbitre → applique.** Aucun correctif, aucune adoption, aucune écriture
  de fichier réel auto-appliquée sans arbitrage humain. R4 ne parle pas de coût, il parle
  d'auto-application.
- **R5 — Le journal ne s'édite pas à la main.** `log_run.py` pour écrire, `--solde` pour
  requalifier. Jamais `succes` sur un livrable que l'utilisateur doit encore valider.

**P1-P4 — code produit** (citées par `revue-increment/SKILL.md`), applicables à chaque
changement sous `app/`, pas seulement en fin d'incrément :

- **P1 — Tout bug corrigé ship avec son test de régression dans le même commit.** Le test
  doit échouer sur le code d'avant — **prouvé par `py scripts/preuve_p1.py`** dès que la
  mutation tient en un marqueur d'un seul fichier, ce qui couvre le cas courant. Pour un
  correctif multi-fichiers, prouver fichier par fichier ; la preuve à la main reste le
  dernier recours, et elle s'annonce comme telle. Le 2026-09-11, cette procédure artisanale
  a produit deux faux négatifs (échappement shell mangeant la mutation, donc « le test passe
  sur le code d'avant » alors que rien n'avait été muté) et laissé passer un test qui passait
  des DEUX côtés. L'outil refuse de conclure dans le doute : marqueur non unique, pytest qui
  sort sur une erreur de collecte, test déjà rouge, échec portant sur un test voisin.
- **P2 — Tout nouveau comportement** (route, service, branche de template) **arrive avec un
  test qui l'exerce.**
- **P3 — Revue de code avant TOUT commit de code produit.** Au-dessus du seuil de
  `revue-increment`, `bmad-code-review` est obligatoire : l'auto-relecture n'est pas le
  gate.
- **P4 — Tout défaut visuel du deck corrigé devient un invariant testé**
  (`tests/test_deck_qualite.py`), en plus du rendu réel `pptx-verify`.

- **P5 — Tout clic que l'utilisateur fait est rejoué dans un vrai navigateur.**
  `tests/test_e2e_premiers_clics.py` (Edge/Chrome headless, vrai uvicorn) : un correctif
  sur un formulaire, une route POST, un middleware ou un en-tête HTTP y ajoute son clic.
  Le 2026-09-08 le site a été livré avec un 403 CSRF sur Supprimer et Démarrer, suite
  verte — `TestClient` prouve la route, pas le navigateur.

Et une règle de preuve, transverse : **tout chiffre écrit s'appuie sur la commande qui l'a
produit — sinon marqué non mesuré.**
