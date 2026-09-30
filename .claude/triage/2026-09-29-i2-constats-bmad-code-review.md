# Triage — I2 constats qualifiés (revues du 2026-09-29 et du 2026-09-30)

Sources : salle `code-review-crew` (4 voix opus, commits d2259b2..93a7bb3),
relecture des correctifs (opus), `bmad-code-review` tranche 4 (sonnet),
`bmad-code-review` lot des chantiers (sonnet).

| id | sév. | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- | --- |
| S1 | majeur | deck : consensus non étayé affiché sans signal | constats.py texte_fondee_sur | code lu | corrige 0bfa8f8, test + P1 |
| S2 | majeur | réimport `## CONSTATS` sans recos : liens reco-constat effacés par CASCADE | constats.py apply_constats_import | test rouge avant | corrige 0bfa8f8, test + P1 |
| S3 | moyen | docstring de test citant un plancher 2.0in inexistant | test_deck_qualite.py | grep | corrige 0bfa8f8 |
| S4 | mineur | largeur d'estimation de la note ≠ largeur de rendu | slides_trajectoire.py:330 | lu | corrige 0bfa8f8, sans test |
| S5 | mineur | « , 0/0 » au deck quand M = 0 | constats.py texte_fondee_sur | lu | corrige 0bfa8f8, test + P1 |
| S6 | majeur | écart porté par une majorité jamais signalé (spec B2) | synthese_material.py | spec l.118 | corrige 5206d07 (2N>M), test + P1 |
| S7 | mineur | parenthèse de commentaire lue comme porteurs | analyse_import.py _CONSTAT_RE | lu | partiel 5206d07 : parenthèse mixte « (hors licences, Alix) » toujours lue comme noms |
| S8 | mineur | libellé contenant « ; » coupé dans la puce Constats | constats.py lier_aux_constats | test | corrige 5206d07, test + P1 |
| S9 | mineur | lien par libellé tous axes confondus | constats.py lier_aux_constats | lu | differe : voulu et documenté, à arbitrer si gênant |
| S10 | mineur | journal horodaté de repli jamais relu (serveur-dev) | serveur-dev.ps1 | test | corrige 1a26df9, test + P1 |
| S11 | mineur | journaux horodatés jamais purgés | serveur-dev.ps1 | lu | differe |
| T1 | bloquant | recos IA : plan_actions en liste → AttributeError | synthese_ai.py generate_recommendations | Ollama réel | corrige 855d01e, test + P1 |
| T2 | majeur | régénération de synthèse écrase les constats importés | global_synthesis_job.py | lu | corrige 855d01e (option A arbitrée), test + P1 |
| T3 | mineur | titre de reco en liste rendu en puces | synthese_ai.py | lu | corrige 855d01e, test |
| T4 | mineur | ids « E12 (Dupont) » rejetés en bloc | synthese_ai.py _parse_ids | lu | corrige 5206d07, test + P1 |
| T5 | mineur | « Interview 3 et E5 » : le 3 disparaît sans être compté rejeté | synthese_ai.py _parse_ids | exécuté | differe |
| T6 | mineur | échec des constats relance toute la chaîne ; aucune borne sur un fournisseur bloqué | global_synthesis_job.py | lu | differe |
| T7 | mineur | génération synchrone de la synthèse sans constats | routers/synthese.py:546 | lu | differe : à confirmer |
| T8 | majeur | recos IA ne citent aucun constat (0/6, qwen2.5:3b) | synthese_ai.py RECO_JSON_HINT | Ollama réel | corrige 5206d07 : 6/6 mesuré |
| L1 | majeur | recollage « a ; b » silencieux quand a et b existent | constats.py _recoller | exécuté | corrige 5206d07, test + P1 |
| L2 | majeur | nom inconnu en minuscule avalé dans le libellé | constats.py _parenthese_commentaire | exécuté | corrige 5206d07, test + P1 ; « (de Villiers) » reste une limite |
| C1 | bloquant | CI : étape pytest bloquée (>1 h #48, borne 20 min #49) | .github/workflows/ci.yml | runs #48 #49 | ouvert : journal inaccessible sans authentification |
| C2 | majeur | aucun run CI entre le 2026-09-04 et le 2026-09-29 | .github/workflows/ci.yml | API runs | ouvert : pushes de I1/I2 jamais testés en CI |
| A1 | mineur | table `syntheses` orpheline après suppression d'A3 | models.py | grep | differe : migration destructive, arbitrage requis |
