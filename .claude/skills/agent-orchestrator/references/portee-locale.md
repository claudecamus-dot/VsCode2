# Portée sur ce projet (chapitre local, extrait de SKILL.md)


**Une skill maison prime sur une skill BMAD générique dès que le canal est celui du deck ou
de l'écran.** Ne pas plaquer un pattern générique là où le projet a déjà son outil.

**Vérifications obligatoires propres à ce dépôt** — elles s'ajoutent à celles du socle :

| Si le plan touche… | Alors le plan contient… |
| --- | --- |
| Template Jinja / CSS / JS | Screenshot via `run-dev-server` (pas seulement pytest) |
| Formulaire, route POST, middleware, en-tête HTTP, garde CSRF | Parcours **vrai navigateur** `tests/test_e2e_premiers_clics.py` joué et allongé du clic touché (demande utilisateur 2026-09-08 : « plus de test utilisateur » — 403 CSRF dès les premiers clics avec une suite verte) |
| `app/services/pptx_export/**` / `pptx_deck.py` | `pptx-verify` (rendu réel — python-pptx est un parseur tolérant). Cardinalité ou libellé rendus VARIABLES → rendre un cas **non par défaut** |
| `pptx_export/slides_diagnostic.py` (porte `_slide_swot`) | Skill `swot-matrix` chargée AVANT de dessiner (matrice 2×2 réelle, pas quatre cartes) |
| `pptx_export/slides_trajectoire.py` (porte `_slide_matrice_effort_valeur`) | Skill `priority-matrix` chargée AVANT de dessiner (matrice dessinée, jamais un scatter Excel natif) |
| Une slide dessinée ou retouchée, quelle qu'elle soit | Skill `deck-design-library` consultée AVANT (partir de l'intention pour choisir la forme) |
| Cadres photo / images encadrées (têtes de chapitre) | Skill `pptx-framed-image` (prstGeom cloné sur l'image, pas un arrondi PIL) |

`deck-design-review` et `slide-text-polish` pour la revue d'un deck, `pdf-quality` dès qu'un
PDF est produit.

**Génération ET mesure d'un PDF : `pdf-quality`, avec son paramètre arbitré.** Un PDF qui
se génère sans erreur n'est pas un PDF correct. La skill est installée ici et son
vérificateur se lance :

```bash
py .claude/skills/pdf-quality/scripts/pdf_verify.py <sortie.pdf> --retrait-citation-mm 3.53
```

**`--retrait-citation-mm 3.53` est arbitré et conservé** : sans lui, le vérificateur
signale à tort un bord gauche multiple sur les PDF de ce projet — un faux défaut bloquant
sur un PDF correct, qui coûte une chasse au bug inexistant.

**Conception** : `docs/reflexions/agent-orchestrateur.md`.

**Playbooks et cibles propres à ce dépôt** :

| Playbook | Pour | Statut |
| --- | --- | --- |
| `dev-verifie` | Implémentation/correction avec tests + vérif réelle + revue-increment avant commit | Éprouvé |
| `cycle-produit-bmad` | Cycle produit BMAD complet (généré depuis le CSV) — **sur demande explicite uniquement** | Jamais joué |

La cible **export de deck** est `app/services/pptx_export/**` et `pptx_deck.py` ;
`run-dev-server` sert à regarder un écran.

| Si le plan touche… | Alors le plan contient… |
| --- | --- |
| Fin d'incrément / avant commit | `revue-increment` en étape terminale |
| **Première écriture sous `app/`** | Le run est journalisé **avant** cette écriture : `py .claude/orchestration/log_run.py '{…, "resultat": "en-cours"}'`, puis `--solde` à la remise. Finding prio 5 du 2026-09-07 (arbitré le 2026-09-09) : deux chantiers `app/` en trois jours sans aucun run, l'un portant un bloquant trouvé trois jours après l'écriture — le gate ne protège que ce que le journal voit, et le rappel de reliquat ne déclenche rien. Un run `en-cours` rend le chantier visible du superviseur dès sa première ligne, pas à son commit. |

**Le piège de parallélisation, ici.** Deux sous-agents ne modifient jamais les mêmes
fichiers en parallèle — sur ce projet le piège classique est **deux agents sur
`app/routers/` ou sur `tests/`**. Si le plan l'exige : `isolation: "worktree"`, ou
sérialiser les étapes d'écriture.

**Et la session principale compte comme un écrivain.** Règle posée le 2026-09-02, sur
incident : deux relecteurs adversariaux ont été lancés en parallèle pendant que la
session principale éditait encore les templates qu'ils revoyaient. L'un d'eux a joué
`git checkout --` dessus pour mesurer le code d'avant — un besoin légitime de revue —
et les correctifs non commités ont disparu du disque. Ils ont été reconstruits depuis
des copies hors dépôt, mais l'incident n'a été vu que par hasard.

Deux enseignements, qui ne se déduisent pas de la règle ci-dessus :

- **Un relecteur écrit.** « Ne modifie aucun fichier » décrit son mandat, pas ce que
  ses outils peuvent faire : prouver qu'un test échoue sur le code d'avant passe par
  le disque si rien ne l'en empêche. Un agent porteur de `Bash` est un écrivain
  potentiel, quel que soit son rôle.
- **Le gate de revue se joue sur du code figé.** Lancer la revue puis continuer à
  éditer, c'est faire revoir une version qui n'existera plus. Poser les correctifs,
  s'arrêter, PUIS lancer les relecteurs — et n'éditer à nouveau qu'une fois leurs
  rapports rendus.

Un hook refuse désormais les commandes qui réécrivent l'arbre
(`.claude/hooks/guard_destructive_git.py`, testé dans `tests/test_hooks_discipline.py`),
et les mandats de `bmad-revue` et `agent-supervisor` disent quoi faire à la place.
**Ce correctif a une portée flotte** : le même hook et les mêmes mandats vivent sur
les autres projets, il est à remonter au hub VSCode5 pour propagation.

**Les cibles de ce dépôt**, pour qualifier une demande : cible **applicative**
(`app/routers/`, `app/services/`, un template Jinja, du CSS/JS) ; cible **export de deck**
(`app/services/pptx_export/**`, `pptx_deck.py`) ; cible **export PDF** (reportlab).
