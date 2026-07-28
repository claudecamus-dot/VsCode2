# Playbook `dev-verifie` — implémentation vérifiée de bout en bout

Le workflow de dev quotidien du projet, rendu structurel : implémenter, tester, **vérifier
en réel** (pas seulement pytest vert — mémoires `feedback_pptx_tests_need_real_render_check`
et discipline `revue-increment`), puis boucle de definition-of-done avant tout commit.
Précédent : c'est la pratique effective de tous les incréments livrés du projet (statut
`eprouve`).

Les étapes de vérification réelle sont **conditionnelles au type de fichiers touchés**
(table des vérifications obligatoires de la skill) : ne garder à l'instanciation que
celles dont la condition s'applique, ne jamais retirer `pytest` ni `revue-increment`.

**Deux étapes ajoutées le 2026-07-27** (arbitrage utilisateur sur le constat superviseur
prio 4 — le gate a sauté sur un diff de 12 fichiers produit, commité PUIS revu) :

- `autorisation-sous-agents` en tête : quand le harnais exige un accord explicite pour
  lancer des sous-agents, il se demande **une fois pour tout le run**, au début. C'est
  précisément au moment du gate — tard, avec le code déjà écrit et l'utilisateur en
  attente — que la question ne se pose plus et que la revue saute.
- `revue-adversariale` **avant** le commit, et non après : une revue jouée après un
  `git push` ne protège plus rien, elle documente. Elle est conditionnelle aux seuils de
  `revue-increment` (> 5 fichiers produit sous `app/`, JS de concurrence de
  `record*.html`, logique à risque) — en dessous, la revue inline suffit et l'étape saute.

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
      "id": "autorisation-sous-agents",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "SI le harnais exige un accord explicite pour lancer des sous-agents : accord demandé UNE FOIS, au début du run, pour l'ensemble du run (revue adversariale comprise) — jamais au moment du gate, où le code est déjà écrit et où la question ne se pose plus"
      },
      "checkpoint": false
    },
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
      "id": "revue-adversariale",
      "agent": "bmad-code-review",
      "mode": "parallele",
      "fan_out_max": 3,
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "SI seuil atteint (> 5 fichiers produit sous app/, JS de concurrence de record*.html, ou logique à risque : suppression/écrasement de données, migration, export irréversible) : chasseurs lancés SUR LE CODE PAS ENCORE COMMITÉ, triage clos APRÈS la fin de flux de CHAQUE chasseur (une 1re notification est provisoire), correctifs appliqués puis RELUS — la revue n'a validé que le code d'avant"
      },
      "checkpoint": "BLOQUANT : aucun commit de code produit tant que le triage n'est pas clos. Une revue jouée après le commit ne protège plus rien (constat superviseur prio 4 du 2026-07-27 : 12 fichiers produit poussés puis revus)"
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
    },
    {
      "id": "commit-et-journal",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "le run est journalisé (py .claude/orchestration/log_run.py) AU MOMENT DU COMMIT, pas à la fin de la séance — une séance longue enchaîne les demandes et ne se termine presque jamais par une action de clôture (constat superviseur prio 2 du 2026-07-27 : 4 commits, aucune ligne de journal)"
      },
      "checkpoint": false
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel"
}
```
