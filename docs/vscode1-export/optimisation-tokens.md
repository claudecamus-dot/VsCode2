# Cadrage — comment limiter l'utilisation de tokens

> **But** : cadrer où les tokens Claude sont réellement dépensés sur ce projet et
> quels leviers les réduisent, sans dégrader la qualité. Document de cadrage
> (pas une procédure figée). Créé le 2026-07-08.

## 1. D'abord : distinguer runtime vs travail assisté

Le premier réflexe utile est de séparer deux mondes qui n'ont **rien à voir**
côté tokens :

- **Exécution de l'application (runtime)** — un animateur clique « exporter PPT ».
  Le pipeline `server.js` → Puppeteer → `python-pptx` est **100 % déterministe :
  zéro token Claude**. Idem pour toute l'app (Express + SQLite). Il n'y a donc
  **rien à optimiser côté tokens dans le produit lui-même**.
- **Travail de développement assisté par Claude** — c'est **là** que les tokens
  sont consommés : exploration de code, génération/itération, boucle de
  vérification, sous-agents. Tout l'enjeu est ici.

> Corollaire : « faire un programme qui n'utilise pas de tokens » n'a de sens que
> pour le **travail d'itération** (ex. régénérer un deck sans repasser par Claude),
> pas pour le runtime (déjà gratuit en tokens).

## 2. Les postes de dépense (travail assisté) et leurs leviers

| Poste | Ce qui coûte | Levier |
| --- | --- | --- |
| **Lecture de contexte** | Relire de gros fichiers (`server.js` 929 l.), transcripts | Outils dédiés (Grep/Glob ciblés) plutôt que `cat` ; lire les portions utiles ; s'appuyer sur le **wiki** et les **mémoires** au lieu de re-dériver |
| **Commandes shell verbeuses** | Sorties `git`, `npm`, `ls` massives | **RTK** (proxy déjà branché en hook `PreToolUse`) filtre et compacte — 60-90 % d'économie sur les ops dev. `rtk gain` pour mesurer |
| **Sous-agents** | Chaque agent démarre à froid et re-dérive le contexte | Ne lancer un sous-agent que si demandé/justifié ; préférer le travail inline quand le contexte est déjà chargé |
| **Boucle de vérification** | Rendus, captures, re-lectures | Vérifier une fois, au bon moment (rendu réel avant de conclure), pas en boucle ; sur le PPT, filtrer d'abord avec `render_diff.py` (§6bis) |
| **Ré-explication** | Re-cadrer des décisions déjà prises | **Documenter les décisions** (wiki, `export/*.md`, mémoires) pour ne pas les rejouer |
| **Cache prompt** | TTL ~5 min ; un long silence casse le cache | Enchaîner les actions ; éviter les pauses inutiles de plusieurs minutes |

## 3. Ce qui est déjà en place

- **RTK** (Rust Token Killer) : hook global réécrivant les commandes
  (`git status` → `rtk git status`), transparent. Vérifier : `rtk gain`,
  `rtk discover` (repère les opportunités manquées dans l'historique).
- **Wiki vivant** (`docs/wiki.html` + `docs/wiki/`) et **mémoires projet** :
  réduisent la re-exploration à chaque session.
- **Pipeline d'export déterministe** : aucune dépendance LLM au runtime.

## 4. Pistes spécifiques « itérer sur le deck sans Claude »

Objectif : qu'une retouche de design (couleurs, marges, textes) ne nécessite pas
de repasser par l'agent.

- **Paramétrer le générateur** : externaliser les réglages de style dans un
  `params.json` (couleurs déjà largement dérivées du thème — cf. `template-octo.md`),
  pour qu'un humain ajuste sans régénérer via Claude.
- **Aperçu rapide non-LLM** : un rendu PowerPoint COM/LibreOffice piloté par un
  script (déjà utilisé pour la vérif) sert d'aperçu — pas besoin de Claude pour
  « voir » le résultat.
- **Tests géométrie + lint** en CI (`test-export-ppt.py`, `slide_lint`) :
  attrapent les régressions sans intervention LLM.

## 5. Ce qu'on ne sacrifie pas

Réduire les tokens **ne doit pas** rogner sur : la vérification par rendu réel
(un défaut visuel ne se voit qu'au rendu), la qualité rédactionnelle, ni la
fidélité au template. L'optimisation porte sur le **gaspillage** (relectures,
re-dérivation, sorties verbeuses), pas sur les étapes de qualité.

## 6. Mesure réelle (2026-07-08)

`rtk gain --history` (portée globale, 1226 commandes) : 940,1K tokens en
entrée, 192,2K économisés (20,4 %). Poste dominant : `rtk read` (157 appels,
76,8K économisés, 17 % en moyenne) — cohérent avec le levier « lire les
portions utiles » de la section 2. Suivent `git diff --cached` (4 appels,
49,6 % d'économie) et `lint eslint .` (99,4 % d'économie, sortie très
verbeuse compactée). `rtk discover` ne remonte aucune commande manquée sur
les 30 derniers jours (0 session scannée côté historique Claude Code —
l'outil regarde l'historique brut, qui n'est pas peuplé ici) : pas
d'opportunité RTK non exploitée détectée par l'outil lui-même.

## 6bis. Nouveau levier prototypé (2026-07-08) — diff pixel avant eye-check

Constat : la boucle de vérification PPT (`pptx-verify`) relit **chaque**
slide en pleine résolution à chaque itération, même quand une seule zone a
changé — c'est le poste vision le plus répété du chantier deck, pas couvert
par les leviers RTK (qui portent sur le shell, pas sur la lecture d'image).

Prototype : `~/.claude/skills/pptx-verify/scripts/render_diff.py` — compare
un rendu baseline et un rendu candidat (fichier à fichier ou dossier à
dossier `slide-NN.png`), filtre le bruit de rasterisation (`--noise`, défaut
24/255 par canal) et ne signale « à revoir » que les slides dont la surface
changée dépasse un seuil (`--threshold`, défaut 0,2 % des pixels) ; peut
exporter un crop zoomé de la seule zone modifiée (`--crop-dir`). Exit code 0
= rien à regarder, 1 = au moins un slide signalé.

**Garde-fou explicite** : c'est un filtre *avant* l'eye-check (étape 3bis du
skill `pptx-verify`), jamais un remplacement — un slide « skip » veut dire
« identique au dernier rendu vu », pas « visuellement correct » sur un
premier rendu. Ne dispense pas de la checklist (libellés cryptiques, valeurs
mal alignées, etc.) que seul un œil peut attraper.

Vérifié : 9 tests unitaires (`tests/test_render_diff_unit.py`, fonctions
pures sur PNG synthétiques en mémoire) + 6 tests fonctionnels
(`tests/test_render_diff_functional.py`, CLI en sous-processus : code de
sortie, mode dossier, crop, erreur de taille, effet du seuil) — tous verts.

## 7. À explorer (reprise de session)

- POC `params.json` + aperçu non-LLM pour l'itération design du deck.
- Évaluer le coût des sous-agents sur les dernières sessions (inline vs délégué).
- Mesurer sur le chantier PPT en cours le gain réel de `render_diff.py`
  (nombre de slides évitées à l'eye-check sur les prochaines itérations).

*Lié : `~/.claude/RTK.md` (référence RTK), [`../docs/wiki.html`](../docs/wiki.html),
[`points-amelioration-ppt.md`](points-amelioration-ppt.md).*
