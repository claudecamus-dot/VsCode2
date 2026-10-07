# Triage — code-review-crew sur `main..feat/libre-async` (2026-10-06)

Branche : 9 commits (9c6c339 → 411302d). Salle : Vex (sécurité), Grumbal (adversaire),
Boundary (cas limites), Yui (artisan), Dana (pragmatique). Arbitrage utilisateur :
« correctif ciblé ce soir » (F1-F6), le reste en dette.

| Id | Sév. | Voix | Constat | Décision |
| --- | --- | --- | --- | --- |
| F1 | bloquant | Grumbal, Vex, Boundary, Dana | `_attendre_les_tranches` dort jusqu'à 30 min dans le pool anyio partagé, session DB ouverte ; sémaphore Ollama sans timeout → site figé | corrige d405099 |
| F2 | majeur | Grumbal | Attente coupée au 1er job échoué/stale ; recover retraite des tranches vivantes ; stale mesuré pendant l'attente du verrou | corrige (ronde 1, d405099..dbe2cd8) |
| F3 | majeur | Boundary, Grumbal | Écriture finale non gardée par `en_cours` ; entretien supprimé pendant la tâche → tours orphelins | corrige (ronde 1) |
| F4 | majeur | Boundary, Dana, Grumbal | États figés : `a_traiter` orphelin, `en_cours` sans relance, poll infini sur 404, `fait` à 0 tour sans bouton, relance refusée muette | corrige (ronde 1) |
| F5 | mineur | Boundary, Vex | `session_token` vide/espaces contourne l'index ; segments audio gardent des clés arbitraires ; `:` accepté | corrige (ronde 1) |
| F6 | mineur | Grumbal | `delete_segment_jobs` hors try ; IntegrityError re-levée en 500 | corrige dbe2cd8 |
| D1 | majeur (dette) | Yui | Statuts en littéraux dispersés (constante `STRUCTURATION_STATUTS` inutilisée) | differe — dette |
| D2 | majeur (dette) | Yui | Service ajouté à l'agrégat routeurs pour les monkeypatchs de tests ; shims de ré-export | differe — dette |
| D3 | mineur (dette) | Yui | Cycle de vie de job dupliqué vs `audio_file_jobs.py` | differe — dette |
| D4 | majeur (dette) | Vex | Relance sans plafond de tentatives | differe — dette (file dédupliquée de F1 évite la rafale) |
| D5 | mineur | Grumbal | `_tours_vides` : identité en littéral, dérive possible de `_build_identity` | differe — dette |
| D6 | mineur | Vex | Deux entretiens peuvent référencer le même fichier audio — suppression à vérifier | ouvert — à vérifier |
| D7 | mineur | rédacteur | m5 non signalé sur l'écran finaliser (mission brouillon) | differe — dette |
| H1 | hors lot | Charpentier, Dana | Concurrence Whisper (8 workers) pendant l'enregistrement | differe — hors lot |

## Ronde 1 appliquée (411302d..dbe2cd8) — contre-revue Grumbal + Boundary

F1-F6 corrigés (d405099, d818498, 554262b, 8fa1295, 56c59c6, dbe2cd8), P1 tenues.
Boundary : ses cas fermés. Grumbal : nouveaux points → ronde 2 (arbitrée « ronde courte ») :

| Id | Sév. | Constat | Décision |
| --- | --- | --- | --- |
| G1 | majeur | Blocage de tête de file : un entretien qui attend une tranche figée retient la file jusqu'à 30 min | corrige 5446b60 |
| G2 | mineur | Relance refusée à tort juste après un échec (registre libéré après l'écriture du statut) | corrige 7a156f8 |
| G3 | mineur | Entretien supprimé pendant l'attente → appel IA complet gaspillé ; `db.get` None non gardé | corrige ebd133e |
| G4 | mineur | Suppression : jobs commités avant l'entretien (pas la même transaction) | corrige b5647f8 |
| G5 | mineur | `segment_token` non tronqué à 64 comme les jobs | corrige a9f7537 |
| D8 | majeur (dette) | Hypothèse mono-processus : deux serveurs sur la même base peuvent doubler une structuration ou réconcilier à tort (Grumbal 2/3, Boundary 4) | differe — dette documentée dans structuration_libre.py |
| D9 | mineur (dette) | Fixture autouse synchrone dans `tests/conftest.py` masque le vrai worker hors tests dédiés et e2e | differe — dette |

Suites : baseline `main` 1514 passed / 1 échec d'environnement
(`test_serveur_dev_script::test_sans_mot_de_passe_le_script_refuse_avant_toute_purge`,
venv absent du snapshot). Branche sur 7601f99 : 1590 passed, 2 échecs dus aux mutations P1 sur disque pendant la suite (18/18 verts en rejeu isolé).

## Ronde 3 (7601f99) — relecture Grumbal de la ronde 2 + 2 relectures bmad-revue du correctif

| Id | Sév. | Constat | Décision |
| --- | --- | --- | --- |
| R1 | bloquant | Boucle chaude du worker : `_file.empty()` faux dès 2 entretiens en attente, aucun sommeil | corrige 7601f99 (test P1) |
| R2 | majeur | Exception de `_tranches_en_vol` laisse `en_cours` figé | corrige 7601f99 (test P1) |
| R3 | majeur | `_prendre` lève après son commit `en_cours` (échéance encore None) → figé | corrige 7601f99 (test P1) |
| R4 | mineur | Échéances consommées pendant le `_finir` d'un autre entretien | differe — dette |
| R5 | mineur | `relancer` → `_liberer` sans marque | differe — dette |
| R6 | mineur | `_marquer_echec` inconditionnel peut basculer un `en_cours` orphelin (le rend relançable) | ecarte — comportement voulu, mono-processus (D8) |
