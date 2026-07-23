# Playbook `export-ppt-verifie` — travaux sur le deck de restitution, vérifiés au rendu réel

La chaîne PPT complète du projet, rendue structurelle : produire ou faire évoluer le deck
de restitution (via l'export `pptx_export.py`/`pptx_deck.py` ou un deck construit
directement), enrichir si pertinent (cadres photo du template, qualité rédactionnelle),
puis **toujours** vérifier au rendu réel — python-pptx est un parseur tolérant, un fichier
qui parse peut ne pas s'ouvrir dans PowerPoint (mémoire
`feedback_pptx_tests_need_real_render_check`).

Précédent (statut `eprouve`) : la colonne vertébrale génération → vérification rendu est la
pratique effective du projet — paire `pptx-deck` + `pptx-verify` jouée le 2026-07-03,
rejouée tout au long des refontes deck (2026-07-18 → 2026-07-22). Les trois skills
d'enrichissement (`pptx-framed-image`, `slide-text-polish`, `restitution-deck-design`),
longtemps `jamais utilisées`, ont **toutes été mises en œuvre le 2026-07-22** (cadres photo
des têtes de chapitre, lint copy, passe design). **`restitution-deck-design` (le
« ppt-designer ») n'est plus conditionnelle auto-jugée mais OBLIGATOIRE** dès que le diff
touche un layout/composant/couleur (diagnostic superviseur 2026-07-22 : conditionnelle
auto-jugée = jamais lancée → design ad hoc, qualité insuffisante — cf. étape design-review).

Frontière avec `dev-verifie` : si la demande est un changement de code générique (routes,
services, templates web), c'est `dev-verifie` qui s'applique — ce playbook-ci est la
version spécialisée quand le **livrable est le deck lui-même** (layout, contenu, visuel).
Les deux partagent l'obligation `pptx-verify` et la terminaison `revue-increment`.

**Itération de design ≠ reprise** (diagnostic superviseur 2026-07-23 : 7 runs / 7 succès /
7 « reprises » — 100 % des runs comptaient la boucle de rendu attendue comme anomalie, la
stat ne portait plus aucun signal) : la boucle **rendu de contrôle → liste de défauts →
correction → re-rendu** est l'étape NOMINALE de ce playbook, bornée à **2 itérations**
au-delà du rendu initial ; à la 3ᵉ, escalade utilisateur avec l'état réel (règle de
non-convergence, mémoire `feedback-non-convergence-user-is-oracle`). Dans le journal
(`log_run.py`), le champ `reprises` ne compte QUE ce qui sort de ce budget ou relève d'un
imprévu (étape en échec de contrat, environnement, exigence découverte) — jamais les
itérations de la boucle nominale.

```json
{
  "nom": "export-ppt-verifie",
  "description": "Production ou évolution du deck PPT de restitution : génération, enrichissements conditionnels (cadres photo, polish rédactionnel, passe design), vérification au rendu réel obligatoire, revue-increment avant commit.",
  "statut": "eprouve",
  "source": "manuel",
  "declencheurs": [
    "génère/améliore/corrige le deck PPT de restitution d'une mission",
    "changement de layout, de constantes ou de slide dans pptx_export.py / pptx_deck.py",
    "remplir les cadres photo (« ici mettre une Photo ») d'un template client",
    "qualité rédactionnelle / design des slides du deck"
  ],
  "etapes": [
    {
      "id": "cadrage",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "données de la mission identifiées (synthèse globale, axes, recommandations), template client ou deck vierge choisi, FIELD_SHAPE relu si les constantes de layout bougent (parité aperçu web / PPT). SI la demande référence un deck/charte externe (VSCode3/4, template client) : RENDRE 2-3 slides de la référence (pptx-verify) et en extraire les motifs concrets AVANT d'implémenter — INTERDIT d'affirmer une conformité « charte VSCodeN » de mémoire (rétro 2026-07-22 : barre d'accent ajoutée « charte VSCode4 » 816ab02 puis retirée « VSCode4 n'en a pas » 09c7ba3, + sommaire/numéro/encarts corrigés seulement après avoir enfin rendu VSCode4 — l'essentiel des reprises deck venait de là)."
      },
      "checkpoint": false
    },
    {
      "id": "generation",
      "agent": "pptx-deck",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "export .pptx produit sans exception, verifier_geometrie() passé (appelé par build_presentation), pytest -k \"pptx or export\" vert"
      },
      "checkpoint": false
    },
    {
      "id": "cadres-photo",
      "agent": "pptx-framed-image",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "SI le template client porte des cadres photo (prstGeom round2DiagRect, « ici mettre une Photo ») : image insérée épousant la forme exacte du cadre (skill jamais utilisée à ce jour — prudence, contrôler à l'étape verification-rendu)"
      },
      "checkpoint": false
    },
    {
      "id": "polish-texte",
      "agent": "slide-text-polish",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "SI le contenu textuel des slides a été produit ou retouché : slide_lint passé sur {title, bullets}, findings bloquants corrigés (skill jamais utilisée à ce jour — prudence)"
      },
      "checkpoint": false
    },
    {
      "id": "verification-rendu",
      "agent": "pptx-verify",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "export réel rendu en images et inspecté visuellement (valeurs alignées, panneaux ni vides ni étirés, pas de collision avec le chrome du template) — jamais retirée à l'instanciation, quelle que soit la taille du changement. BOUCLE NOMINALE (diagnostic superviseur 2026-07-23) : rendu → liste de défauts → correction → re-rendu est le déroulé ATTENDU de cette étape, ≤ 2 itérations au-delà du rendu initial puis escalade utilisateur — ces itérations ne se journalisent PAS en reprises. CHECKLIST EXIGENCES PERSISTANTES (plan d'amélioration 2026-07-22) : tenir la liste des éléments explicitement demandés par l'utilisateur aux tours précédents (ex. numéro de chapitre) et VÉRIFIER leur présence au rendu à chaque itération — une contrainte de gabarit se résout en dessinant l'élément, jamais en l'omettant (le numéro de chapitre a été écarté 3 fois ainsi)."
      },
      "checkpoint": false
    },
    {
      "id": "design-review",
      "agent": "restitution-deck-design",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "OBLIGATOIRE dès que le diff touche un layout / composant / couleur de slide (seuil OBJECTIF, pas un auto-jugement « ça a l'air pauvre » — c'était le défaut : conditionnelle auto-jugée = jamais lancée, diagnostic superviseur 2026-07-22). Lancer restitution-deck-design et appliquer sa review checklist au rendu réel (hiérarchie 1 headline/slide, rythme d'espacement unique, couleur=sens non décorative, alignement exact, cohérence de composant — un même composant identique partout via un helper pptx_deck, retenue : pas d'aplat criard là où un accent suffit), corriger, puis retour à verification-rendu."
      },
      "checkpoint": false
    },
    {
      "id": "revue-increment",
      "agent": "revue-increment",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "SI du code produit a été modifié (pptx_export.py, pptx_deck.py, FIELD_SHAPE) : boucle revue + correctifs + re-vérification exécutée en entier"
      },
      "checkpoint": "avant tout commit — action difficilement réversible, proposer, ne pas exécuter unilatéralement"
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel. Les itérations de la boucle nominale rendre→corriger→re-rendre (≤ 2 au-delà du rendu initial, cf. verification-rendu) sont le déroulé attendu, PAS des reprises — le champ reprises du journal ne compte que ce qui sort de ce budget ou d'un imprévu (diagnostic superviseur 2026-07-23 : 7/7 runs à reprises=100 %, stat sans signal)"
}
```
