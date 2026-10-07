# Triage — code-review-crew sur `main..feat/libre-async` (2026-10-06)

Branche : 9 commits (9c6c339 → 411302d). Salle : Vex (sécurité), Grumbal (adversaire),
Boundary (cas limites), Yui (artisan), Dana (pragmatique). Arbitrage utilisateur :
« correctif ciblé ce soir » (F1-F6), le reste en dette.

| Id | Sév. | Voix | Constat | Décision |
| --- | --- | --- | --- | --- |
| F1 | bloquant | Grumbal, Vex, Boundary, Dana | `_attendre_les_tranches` dort jusqu'à 30 min dans le pool anyio partagé, session DB ouverte ; sémaphore Ollama sans timeout → site figé | À corriger (worker dédié, file dédupliquée, acquire avec timeout) |
| F2 | majeur | Grumbal | Attente coupée au 1er job échoué/stale ; recover retraite des tranches vivantes ; stale mesuré pendant l'attente du verrou | À corriger |
| F3 | majeur | Boundary, Grumbal | Écriture finale non gardée par `en_cours` ; entretien supprimé pendant la tâche → tours orphelins | À corriger |
| F4 | majeur | Boundary, Dana, Grumbal | États figés : `a_traiter` orphelin, `en_cours` sans relance, poll infini sur 404, `fait` à 0 tour sans bouton, relance refusée muette | À corriger |
| F5 | mineur | Boundary, Vex | `session_token` vide/espaces contourne l'index ; segments audio gardent des clés arbitraires ; `:` accepté | À corriger |
| F6 | mineur | Grumbal | `delete_segment_jobs` hors try ; IntegrityError re-levée en 500 | À corriger |
| D1 | majeur (dette) | Yui | Statuts en littéraux dispersés (constante `STRUCTURATION_STATUTS` inutilisée) | Dette (sauf si F4 les touche) |
| D2 | majeur (dette) | Yui | Service ajouté à l'agrégat routeurs pour les monkeypatchs de tests ; shims de ré-export | Dette |
| D3 | mineur (dette) | Yui | Cycle de vie de job dupliqué vs `audio_file_jobs.py` | Dette |
| D4 | majeur (dette) | Vex | Relance sans plafond de tentatives | Dette (la file dédupliquée de F1 évite la rafale) |
| D5 | mineur | Grumbal | `_tours_vides` : identité en littéral, dérive possible de `_build_identity` | Dette |
| D6 | mineur | Vex | Deux entretiens peuvent référencer le même fichier audio — suppression à vérifier | À vérifier |
| D7 | mineur | rédacteur | m5 non signalé sur l'écran finaliser (mission brouillon) | Dette |
| H1 | hors lot | Charpentier, Dana | Concurrence Whisper (8 workers) pendant l'enregistrement | Hors lot |

## Ronde 1 appliquée (411302d..dbe2cd8) — contre-revue Grumbal + Boundary

F1-F6 corrigés (d405099, d818498, 554262b, 8fa1295, 56c59c6, dbe2cd8), P1 tenues.
Boundary : ses cas fermés. Grumbal : nouveaux points → ronde 2 (arbitrée « ronde courte ») :

| Id | Sév. | Constat | Décision |
| --- | --- | --- | --- |
| G1 | majeur | Blocage de tête de file : un entretien qui attend une tranche figée retient la file jusqu'à 30 min | Ronde 2 |
| G2 | mineur | Relance refusée à tort juste après un échec (registre libéré après l'écriture du statut) | Ronde 2 |
| G3 | mineur | Entretien supprimé pendant l'attente → appel IA complet gaspillé ; `db.get` None non gardé | Ronde 2 |
| G4 | mineur | Suppression : jobs commités avant l'entretien (pas la même transaction) | Ronde 2 |
| G5 | mineur | `segment_token` non tronqué à 64 comme les jobs | Ronde 2 |
| D8 | majeur (dette) | Hypothèse mono-processus : deux serveurs sur la même base peuvent doubler une structuration ou réconcilier à tort (Grumbal 2/3, Boundary 4) | Dette documentée |
| D9 | mineur (dette) | Fixture autouse synchrone dans `tests/conftest.py` masque le vrai worker hors tests dédiés et e2e | Dette |

Suites : baseline `main` 1514 passed / 1 échec d'environnement
(`test_serveur_dev_script::test_sans_mot_de_passe_le_script_refuse_avant_toute_purge`,
venv absent du snapshot). Branche : en cours.
