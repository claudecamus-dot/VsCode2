# Triage — contre-revue de la coupe Ollama temporaire (2026-10-13)

Salle code-review-crew, 5 lentilles. Arbitrage utilisateur : « ok continue ».
La coupe (`OLLAMA-COUPE-TEMPORAIRE`, `TOUR-DE-TABLE-COUPE-TEMPORAIRE`) est VOULUE : aucun fichier sous `app/` modifié par ce lot.

| Id | Constat | Sort |
| --- | --- | --- |
| A | conftest : « 14 tests » alors que la liste en compte 15 ; un test renommé sortait de la liste en silence ; pas de procédure de réactivation | corrigé (compte, garde `pytest.UsageError` si fichier collecté mais nom absent, bloc RÉACTIVATION). Garde inactif si coupe inactive, fichier non collecté, ou sélection par node-id `::` |
| B | `..._garde_transitoire_faux` ne garde rien ; `transitoire is False` tautologique | corrigé : test et assertion supprimés ; P1 tenue (outil) sur `wrapped.timeout = exc.timeout` (l.643) |
| C | e2e retranscrire : assertion d'URL non discriminante, `mid` inutilisé, docstring trop promesse | corrigé : assertion d'URL retirée, `mid` retiré, docstring exacte (pas de 403 + 1 AudioFileJob) ; test rejoué vert. Pas de nouvelle mutation (déjà prouvée) |
| D | `_extract_with_retries` sans test de bout en bout avec réglages extrêmes | corrigé : test paramétré inf/nan/1e9 ; P1 tenue (outil) en retirant le plafond `min(_RETRY_DELAY_MAX_S, ...)`. Le garde `isfinite` n'est pas isolé par ce test (le plafond couvre inf/nan) : mutation non jouée |
| E | Seuil stale 45 min contre 5 appels + 600 s d'attente | consigné sans action : défaut préexistant, dormant pendant la coupe |
| F | CLAUDE.md : chiffre « 1592 tests / 29 min 32 s » périmé | corrigé : marqué non remesuré ; 3 lignes sur la coupe ajoutées |
| G (V1, V2, E3, G3, cas tordus) | constats mineurs | sans action |

## Non prouvé
- T3 : aucune mutation jouée.
- e2e « 18 passed, 1 skipped en 575 s » : non rejoué par la session (seul le test C l'a été).
- Suite complète (~30 min) : non rejouée.
- CI : non lue.
