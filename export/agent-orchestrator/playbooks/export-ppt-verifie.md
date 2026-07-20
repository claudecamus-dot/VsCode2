# Playbook `export-ppt-verifie` — travaux sur le deck de restitution, vérifiés au rendu réel

La chaîne PPT complète du projet, rendue structurelle : produire ou faire évoluer le deck
de restitution (via l'export `pptx_export.py`/`pptx_deck.py` ou un deck construit
directement), enrichir si pertinent (cadres photo du template, qualité rédactionnelle),
puis **toujours** vérifier au rendu réel — python-pptx est un parseur tolérant, un fichier
qui parse peut ne pas s'ouvrir dans PowerPoint (mémoire
`feedback_pptx_tests_need_real_render_check`).

Précédent (statut `eprouve`) : la colonne vertébrale génération → vérification rendu est la
pratique effective du projet — paire `pptx-deck` + `pptx-verify` jouée le 2026-07-03
(construction du deck puis rendu inspecté), `pptx-verify` rejoué le 2026-07-18. Les trois
étapes conditionnelles s'appuient en revanche sur des skills **jamais utilisées à ce jour**
(`pptx-framed-image`, `slide-text-polish`, `restitution-deck-design`) — conservées par
arbitrage utilisateur du 2026-07-18 (`.claude/supervision/arbitrages.json`) et reliées ici
pour exister dans le routage : les proposer avec prudence explicite et vérifier leur
résultat au rendu.

Frontière avec `dev-verifie` : si la demande est un changement de code générique (routes,
services, templates web), c'est `dev-verifie` qui s'applique — ce playbook-ci est la
version spécialisée quand le **livrable est le deck lui-même** (layout, contenu, visuel).
Les deux partagent l'obligation `pptx-verify` et la terminaison `revue-increment`.

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
        "critere": "données de la mission identifiées (synthèse globale, axes, recommandations), template client ou deck vierge choisi, FIELD_SHAPE relu si les constantes de layout bougent (parité aperçu web / PPT)"
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
        "critere": "export réel rendu en images et inspecté visuellement (valeurs alignées, panneaux ni vides ni étirés, pas de collision avec le chrome du template) — jamais retirée à l'instanciation, quelle que soit la taille du changement"
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
        "critere": "SI le rendu passe la géométrie mais reste visuellement pauvre (mur de boîtes, hiérarchie absente) : passe design appliquée puis retour à verification-rendu (skill jamais utilisée à ce jour — prudence)"
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
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel"
}
```
