# Playbook `dev-verifie` — implémentation vérifiée de bout en bout

Le workflow de dev quotidien du projet, rendu structurel : implémenter, tester, **vérifier
en réel** (pas seulement pytest vert — mémoires `feedback_pptx_tests_need_real_render_check`
et discipline `revue-increment`), puis boucle de definition-of-done avant tout commit.
Précédent : c'est la pratique effective de tous les incréments livrés du projet (statut
`eprouve`).

Les étapes de vérification réelle sont **conditionnelles au type de fichiers touchés**
(table des vérifications obligatoires de la skill) : ne garder à l'instanciation que
celles dont la condition s'applique, ne jamais retirer `pytest` ni `revue-increment`.

Frontière avec `export-ppt-verifie` : un changement de code qui *touche* l'export PPT au
passage reste ici (l'étape `verification-pptx` couvre) ; quand le **livrable est le deck
lui-même** (layout, contenu, visuel), préférer `export-ppt-verifie` qui déroule la chaîne
PPT complète (cadres photo, polish, passe design).

```json
{
  "nom": "dev-verifie",
  "description": "Implémentation d'une feature/correction avec tests, vérification réelle adaptée aux fichiers touchés, et revue-increment avant commit.",
  "statut": "eprouve",
  "source": "manuel",
  "declencheurs": [
    "implémente/corrige/ajoute une fonctionnalité dans app/",
    "changement de template Jinja, CSS, JS",
    "changement de l'export PPT (pptx_export.py, pptx_deck.py)",
    "fin d'incrément, préparation d'un commit de code produit"
  ],
  "etapes": [
    {
      "id": "cadrage",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "fichiers concernés lus, appelants des fonctions/champs partagés grep-és avant modification"
      },
      "checkpoint": false
    },
    {
      "id": "implementation",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "chaque exigence EXPLICITE de la demande (points numérotés, contraintes) cochée une à une contre le diff — pas seulement « ça compile/passe » ; toute exigence réinterprétée ou écartée signalée, jamais silencieuse ; style du fichier environnant respecté (pas de linter configuré)"
      },
      "checkpoint": false
    },
    {
      "id": "tests",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "verdict lu sur la ligne de synthèse RÉELLE de pytest (N passed / 0 failed / 0 error) — jamais sur un résumé filtré du proxy rtk (déjà mal reporté un run) ni un [100%] de sortie tronquée ; compter les points, pas le code retour (bruit de teardown Windows connu) ; en cas de doute, relancer via rtk proxy pytest ou rediriger toute la sortie dans un fichier",
        "commande": "pytest -q"
      },
      "checkpoint": false
    },
    {
      "id": "verification-ui",
      "agent": "run-dev-server",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "SI template Jinja/CSS/JS touché : screenshot de la page modifiée pris et regardé"
      },
      "checkpoint": false
    },
    {
      "id": "verification-pptx",
      "agent": "pptx-verify",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "SI pptx_export.py/pptx_deck.py touché : export réel rendu en images et inspecté (python-pptx est un parseur tolérant)"
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
        "critere": "boucle revue + application des correctifs + re-vérification réelle exécutée en entier"
      },
      "checkpoint": "avant tout commit — action difficilement réversible, proposer, ne pas exécuter unilatéralement"
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel"
}
```
