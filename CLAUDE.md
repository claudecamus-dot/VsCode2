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
  doit échouer sur le code d'avant.
- **P2 — Tout nouveau comportement** (route, service, branche de template) **arrive avec un
  test qui l'exerce.**
- **P3 — Revue de code avant TOUT commit de code produit.** Au-dessus du seuil de
  `revue-increment`, `bmad-code-review` est obligatoire : l'auto-relecture n'est pas le
  gate.
- **P4 — Tout défaut visuel du deck corrigé devient un invariant testé**
  (`tests/test_deck_qualite.py`), en plus du rendu réel `pptx-verify`.

Et une règle de preuve, transverse : **tout chiffre écrit s'appuie sur la commande qui l'a
produit — sinon marqué non mesuré.**
