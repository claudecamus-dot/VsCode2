# Sous-cadrage P5-a — Espace démo vs réel + 1ère page (modèle VSCode1)

> Statut : **cadrage validé, décisions tranchées (2026-07-22)** — Palier 1 (P5a-1)
> en cours d'implémentation. Fait suite à la demande « lance le chantier de 1ère
> page démo versus réel comme pour VSCode1 ».
>
> **Décisions (2026-07-22, arbitrées avec l'utilisateur)** :
> 1. **Mécanisme d'isolation → (A) flag `Mission.is_demo` same-DB** (le vrai modèle
>    démo/réel de VSCode1, §1.1) — PAS la base séparée que §6.1 du cadrage parent
>    avait prescrite par erreur (celle-ci décrivait la séparation d'environnement
>    de VSCode1). Le cadrage parent §6.1 est à rectifier.
> 2. **Mission démo → modifiable + rejouable** (comme VSCode1) : pleinement
>    utilisable, réinitialisable par re-seed.
> 3. **1ère page** : `/` = choix Démo / Réel → redirige vers l'entrée 3-choix
>    existante (calque VSCode1).
>
> Périmètre de CE sous-cadrage : **la 1ère page démo/réel + le cloisonnement
> démo/réel** (le point d'entrée). La **cinématique de génération** du deck
> (`refonte-deck-restitution.md` §6.2) reste un chantier distinct (P5-b), non
> couvert ici.

## 1. Ce que fait VSCode1 réellement (le modèle de référence)

VSCode1 sépare **deux concepts orthogonaux** qu'il ne faut pas confondre :

### 1.1 « Mode démo vs réel » — c'est CELUI de la demande (la « 1ère page »)

Mécanisme **léger, même base de données** :

| Brique | VSCode1 | Fichier |
| --- | --- | --- |
| **1ère page** (`/`) | 2 cartes « Démonstration » (liseré cyan) / « Usage réel » (liseré navy), chacune un bouton | `app/src/public/index.html` |
| **Choix → cookie** | le bouton pose `document.cookie = 'mode=demo\|reel; path=/; max-age=1an; samesite=lax'` puis va à la console | idem, `<script>` |
| **Lecture serveur** | `estModeDemo(req.headers.cookie)` → `true/false` (testable isolément) | `app/src/mode.js` |
| **Flag de données** | colonne **`est_demo`** sur l'entité de tête (`sessions`), **défaut 0** (réel — on ne marque JAMAIS des données existantes démo par accident) ; migration idempotente | `app/src/db.js` |
| **Filtrage / tag** | listings `… WHERE est_demo = ?` ; créations `INSERT … est_demo = ?` — le mode courant filtre ET tague | `app/src/server.js` |
| **Bandeau** | sticky top « MODE DÉMO — données fictives · changer de mode » (cyan, texte foncé, lien vers `/`) quand cookie `mode=demo` | `app/src/public/env-banner.js` |
| **Jeu démo** | script qui sème des sessions fictives `est_demo=1` | `app/scripts/seed-demo.js` |

→ **Aucune base séparée, aucun port dédié, aucun lancement spécifique.** Un seul
serveur, une seule base, un **drapeau** + un **cookie** + une **page de choix** +
un **bandeau**. Réversible, éprouvé, minimal.

### 1.2 « Environnement » DEV / PRE-PROD / PROD — un AUTRE sujet

Séparation **infra** : `APP_ENV` (bandeau), `PORT`, `DB_PATH` **une base par
environnement** (`.env.dev` / `.env.preprod` / `.env.prod`, lancés par
`node --env-file=…`), bandeau d'environnement distinct (`/api/env`, rien en PROD).

C'est CE mécanisme (bases séparées + ports) que le cadrage parent
`refonte-deck-restitution.md` §6.1 a décrit comme « le modèle VSCode1 » pour la
démo — **mais c'est la séparation d'environnement de VSCode1, pas sa séparation
démo/réel.** La vraie démo/réel de VSCode1 est le flag same-DB du §1.1.

## 2. Point d'attention — divergence avec le cadrage parent §6.1

`refonte-deck-restitution.md` §6.1 a acté (arbitrage 2026-07-22) :
« **bases séparées dès le départ, modèle VSCode1** (base démo dédiée
`data/demo/app.db`) ». Après lecture du code VSCode1, ce n'est **pas** ainsi que
VSCode1 sépare démo et réel : il le fait par un **flag `est_demo` sur la même
base** (§1.1). La séparation par base distincte, chez VSCode1, sert les
**environnements** (dev/preprod/prod), pas la démo.

→ **Arbitrage à rouvrir** (§5) : suivre le vrai modèle démo/réel de VSCode1
(flag same-DB, plus léger, « comme pour VSCode1 » au sens strict) **ou** la
décision écrite §6.1 (base démo séparée). Les deux sont défendables ; ils n'ont
pas le même coût ni le même profil de risque.

## 3. Transposition à Interview-to-Deck

L'entité de tête ici est **`Mission`** (elle possède interviews, trame, synthèses,
SWOT, difficultés, verbatims, axes — tout le contenu d'une restitution). Le pendant
du `est_demo` VSCode1 est donc **`Mission.is_demo`** : une mission démo emporte tout
son contenu (isolation naturelle par la hiérarchie existante).

- **1ère page** : l'entrée unifiée existante `/` (incr.9 — 3 choix : entretien
  libre / structuré / nouvelle mission) devient l'écran de **2ᵉ** niveau ; `/`
  affiche d'abord le choix **Démo / Réel** (2 cartes, charte OCTO comme VSCode1),
  puis route vers l'entrée à 3 choix (ex. `/demarrer`). Le cookie `mode` persiste
  (retour direct possible pour un habitué).
- **Cookie `mode`** posé au choix ; lu côté serveur par un helper `est_mode_demo()`
  (pendant Python de `mode.js`, testable) → filtre les listings de missions
  (`/missions`, hub) et tague `Mission.is_demo` à la création (les 3 chemins de
  création : brouillon libre/structuré, nouvelle mission).
- **Bandeau** « MODE DÉMO — données fictives · changer de mode » (cyan, sticky,
  lien `/`) injecté sur toutes les pages quand `mode=demo` — un partial Jinja
  inclus dans le layout de base.
- **Jeu démo (P4)** : seed d'une mission `is_demo=1` réaliste (≈6-8 interviews +
  tout le contenu de restitution) — c'est la matière que la 1ère page démo donne
  à explorer. Dépendance : la 1ère page est plus convaincante avec P4 en place,
  mais la **plomberie** (flag + cookie + filtrage + bandeau) est livrable et
  testable **avant** le seed.

## 4. Découpage proposé

| Palier | Contenu | Dépend de | Coût | Risque |
| --- | --- | --- | --- | --- |
| **P5a-1 — 1ère page + cloisonnement** | choix démo/réel sur `/`, cookie mode, `Mission.is_demo` (migration additive), filtrage listings + tag création, bandeau MODE DÉMO | mécanisme tranché (§5) | Moyen | Faible-Moyen |
| **P5a-2 — jeu démo (= P4)** | seed d'une mission démo complète (`is_demo=1`) | P5a-1 | Faible | Faible |
| **P5b — cinématique** | storyboard scripté de la génération (`refonte-deck` §6.2) | P5a, deck cible | Élevé | Élevé (sous-cadrage séparé) |

## 5. Arbitrages à trancher AVANT code

1. **Mécanisme d'isolation** — le seul vrai choix structurant :
   - **(A) Flag `Mission.is_demo` same-DB** — fidèle au vrai VSCode1 (§1.1),
     léger, réversible, migration additive, aucun changement de lancement,
     compatible avec une cinématique qui lit les mêmes routes. **Contre** :
     isolation logique (un bug de filtre pourrait mélanger) — mitigé par le
     défaut 0 et des tests de filtrage.
     → **recommandé** (correspond à « comme pour VSCode1 », coût/risque le plus bas).
   - **(B) Base démo séparée `data/demo/app.db`** — décision écrite §6.1,
     isolation **physique** totale, réinitialisation = supprimer un fichier.
     **Contre** : bascule au lancement (2 process/ports ou variable), UX de
     changement de mode moins fluide (pas un simple lien), plus lourd, ne
     correspond pas au mécanisme démo réel de VSCode1.
2. **Intégration de la 1ère page** : `/` = choix démo/réel puis redirection vers
   l'entrée 3-choix (recommandé, calque VSCode1) — à confirmer.
3. **Édition en démo** : la mission démo est-elle **explorable + rejouable**
   (modifiable, réinitialisable par re-seed) ou **lecture seule** ? (VSCode1 :
   modifiable, isolée par le flag.)

---

*Lié : `docs/reflexions/refonte-deck-restitution.md` §6 (cadrage parent, à
rectifier sur le point §6.1) ; sources VSCode1 `app/src/{mode,db,server}.js`,
`app/src/public/{index.html,env-banner.js}`, `app/scripts/seed-demo.js`.*
