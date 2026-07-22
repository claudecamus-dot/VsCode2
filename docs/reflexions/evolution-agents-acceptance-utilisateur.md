# Réflexion — faire évoluer superviseur + orchestrateur après une boucle non convergente

> 2026-07-22. Déclencheur : refonte du deck PPT — **~15 tours**, l'utilisateur répète
> « toujours KO / ma demande n'est pas traitée » à chaque itération. Le superviseur a
> produit des diagnostics, l'orchestrateur des playbooks et des plans d'amélioration —
> **et pourtant le résultat livré est resté KO du point de vue de l'utilisateur.** C'est
> un échec du dispositif lui-même, pas seulement du deck.

## 1. Le symptôme mesurable

- `runs.jsonl` : ~6 runs `export-ppt-verifie`, **tous `succes`** — alors que l'utilisateur
  rejette le deck tour après tour. Le champ issue ne porte aucun signal (déjà noté le
  2026-07-21, toujours vrai).
- git : 24 commits deck en une session, dont un **add-then-revert** (barre d'accent
  816ab02 → 09c7ba3). Série de « fix » sur `pptx_export.py` sans convergence.
- Corrections utilisateur : « toujours KO » répété ≥ 5 fois sur le même livrable.

## 2. Les 4 manquements systémiques

1. **Je vérifie un autre artefact que celui que l'utilisateur ouvre.** J'ai validé des
   slides isolées de MON `build_presentation` (données que je venais de semer, complètes),
   pendant que l'utilisateur ouvrait l'**export de l'app** (données réelles, un
   `GlobalSynthesis` vidé → 16 slides au lieu de 21, fiches de reco à moitié vides).
   Mes rendus « bons », son export cassé. cf. mémoire `feedback-verify-the-real-app-export-all-slides`.
2. **« succes » auto-décerné.** Le verdict de run repose sur MA vérification, jamais sur
   celle de l'utilisateur. Le seul oracle réel (« KO ») arrive TOUJOURS après que j'ai
   déclaré/commité « fait ».
3. **Itération à l'aveugle.** Face à « toujours KO » sans slide précise, j'ai **re-deviné**
   le problème à chaque tour au lieu de faire pointer le défaut exact. D'où l'oscillation
   (accent bar in/out, numéro navy-block puis pill, encarts cyan puis gris…).
4. **Réactif, pas préventif.** Le superviseur diagnostique APRÈS l'échec ; aucun signal
   « ce livrable a été rejeté N fois » ne déclenche un changement de MODE.

## 3. La cause racine

**L'utilisateur n'a jamais été l'oracle, sur l'artefact exact.** Tout le dispositif
(playbooks, `pptx-verify`, revue-increment, diagnostics) reste un système où **le même
modèle évalue ce que le même modèle a produit** — exactement le risque que la règle
« pas de constat sans preuve » du superviseur voulait éviter, mais appliqué à la
*validation de livrable*, pas seulement aux diagnostics. Le rendu réel prouve « ça
s'ouvre / c'est joli à mon œil », jamais « c'est ce que l'utilisateur voulait ».

## 4. Évolutions appliquées (concrètes, pas un doc de plus)

### Orchestrateur

- **Nouvelle vérification obligatoire (§4)** — livrable consommé par l'utilisateur (deck
  exporté, écran) : produire l'**artefact EXACT qu'il ouvre** (export réel de la route de
  l'app, pas un `build_presentation` maison), le rendre **ENTIER** (toutes les slides,
  pas un échantillon), puis le faire **VALIDER par l'utilisateur** avant tout « fait ».
- **Nouvel état de run `en-attente-validation` (§5)** — un livrable design-intent
  destiné à l'utilisateur n'est **jamais `succes`** tant que l'utilisateur ne l'a pas
  validé sur l'artefact exact ; le run reste `en-attente-validation`. `succes` ne se pose
  qu'après le « OK » utilisateur.
- **Règle de non-convergence (§4)** — ≥ 3 tours sur le MÊME livrable avec rejet
  utilisateur = boucle non convergente : **STOP l'itération à l'aveugle**. Reproduire
  l'artefact exact ET **demander le défaut précis** (numéro de slide / capture) au lieu
  de re-deviner.

### Superviseur

- **Nouvelle catégorie de constat `non-convergence`** — détecter « même livrable rejeté
  ≥ 3 fois » (runs répétés sur le même playbook/livrable + corrections « toujours KO »)
  comme constat **critique**, avec pour proposition le **mode acceptance** (utilisateur
  oracle sur l'artefact exact) plutôt qu'un énième correctif incrémental.

## 5. Ce que ça change concrètement, dès maintenant

Face à « toujours KO » : je ne re-devine plus. Je (1) exporte le deck depuis l'app, (2)
le rends en entier, (3) **demande à l'utilisateur de pointer la slide / le défaut précis**,
et (4) ne déclare « fait » qu'après SA validation sur CET export. La boucle
s'ouvre sur l'oracle réel au lieu de tourner sur mon auto-évaluation.
