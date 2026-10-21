# Triage — salle `code-review-crew` (comité augmenté) sur les lots du 2026-10-19

Date : 2026-10-20/21. Périmètre : diff non commité de `feat/libre-async` (11 lots W1-W11 du 2026-10-19
regroupés dans l'arbre principal, base HEAD a59d234). Tableau commun des voix : scratchpad de la séance
(non versionné) ; ce fichier en est la trace durable.

## Corrigé dans cette séance (avec preuve)

| Id | Constat | Correctif | Preuve |
| --- | --- | --- | --- |
| EH-1 | `_drain_parallel` prenait les permis Whisper un par un : interblocage de deux imports longs, direct en 503 | prise tout-ou-rien sous verrou court, échéance unique pour le direct, direct démarre avec les permis libres (option B, arbitrage 2026-10-20), plafond borné, comptage par permis | `test_concurrence_whisper_w7.py` (P1 tenue par `preuve_p1.py`, 2 tests) ; 3 rondes de revue Opus ; 478 tests verts sur 29 fichiers citant `audio_transcribe` |
| BH-4 | `save_libre_detail` : un POST avec `turn_id` sans les autres listes `turn_*` vidait les tours | report du hunk w9 : le serveur ignore les `turn_*` | `test_libre_detail_edit_ignores_turns_and_repartition` (P1 tenue) |
| BH-1 | une structuration pouvait retraiter une tranche fraîchement réservée (double extraction IA) | report w9 : `_worker_vivant`, `_attendre_fin_structuration` | revue du report : fermé en pratique, fenêtre résiduelle ci-dessous |
| (perdu) | 11 hunks de w9 absents de l'arbre principal (w9 édité après le regroupement) | reportés, identiques à la référence | `diff -u --strip-trailing-cr` vide sur les 5 fichiers de code et les 3 tests |
| T2 | `test_l_enregistrement_libre_transcrit…` : la fiche s'ouvre sur Aperçu (comportement voulu), le test ne cliquait pas l'onglet Transcription | clic ajouté | 4 tests e2e verts |
| T1 | `test_enchainer_…_tranches_failed` figeait des statuts transitoires | assertion sur l'état stable (positions conservées, rien de `done`) — moins stricte, assumé | idem |
| P5 | aucun clic navigateur sur « Relancer cette tranche » / « Relancer toutes » | `tests/test_e2e_relance_tranches.py` | 2 passages verts, P1 tenue (route cassée → rouge) |
| conftest | la garde de `vider_recordings_de_test` ne nettoyait jamais le temporaire système (des dizaines de `*.db` voisins) → `test_mission_backups` rouge (2 tests) | la vérification des bases voisines ne s'applique plus à LA base de test du conftest | `test_vide_la_base_de_test_du_conftest_meme_avec_des_bases_voisines` (P1 tenue) ; 78 verts |

## Dette ouverte (non bloquante, arbitrée « consigner »)

- **Famine d'un gros job de fond** (EH-1 option B) : un flux de petits jobs peut doubler un job FOND qui veut N permis. Pas d'interblocage. FOND ne cède pas pendant `direct_actif()`.
- **Fenêtre d'une instruction** dans la restitution de permis partiels (`while acquis: release(); acquis -= 1`) : une exception asynchrone peut sur-libérer un permis ; piste : décrémenter avant de relâcher ou `BoundedSemaphore`.
- **Seuil de 3,0 s** de `test_live_busy_respecte_le_budget` large (détecte une attente ≥ 10× trop longue).
- **TOCTOU** `_worker_vivant()` / `reserver_tranches()` (`interviews_libre.py:142,157`) : au pire un calcul IA en double ; piste : réservation sous condition SQL `structuration_status != 'en_cours'`.
- **Double `done()`** possible dans `record_libre.html` (≈ l.1372-1385 et 1633-1637) si `uploadSegment` lève après avoir lancé son envoi ; piste : drapeau `doneOnce`, ou test dans le harnais d'exécution JS.
- **Thread bloqué jusqu'à 30 min** par `_attendre_fin_structuration` (pas de verrou ni session tenus).
- `interviews_libre.py:334` : `existant.tranches_manquantes = tranches` réécrit à l'identique (code mort).
- **SH-1 / SH-2** : cache de dédoublonnage des segments (`segment_dedupe.py`) et jeton des tranches non cloisonnés par mission/utilisateur ; sans conséquence tant qu'il n'y a qu'un mot de passe unique.
- **EH-2 à EH-6** : `_transcrire_dedoublonne` (seul `CancelledError` appelle `quitter`), échecs non mis en cache (rejeu d'un blob sans parole), `_noter_duree` n'alimente que le chemin court, `date.today()` suit le fuseau du serveur, commit de rattrapage échoué dans `relancer_tranches_en_fond`.
- **AD-2** : plafond « dur » de 90 s de `recover_stalled_or_failed_jobs` non borné pour la 1re tranche.
- **CR-1..CR-9** (artisan) : `en_fond()` duplique `avec_priorite(FOND)` (6 lignes), `_maintenant()` redéfini alors que `_utcnow` existe, `_is_stale` importé privé, utilitaires génériques enfouis dans le routeur, `est_abandonne()` à effet de bord, `PriorityLock.locked()` / `REGISTRE.vider` / `resultat_recent` sans appelant, tests nommés d'après les lots (`_w7`, `_w9`, `_w10`) à renommer d'après le comportement, `test_mineurs_w10.py` fourre-tout.
- **AD-5** : commentaires périmés citant `OLLAMA-COUPE-TEMPORAIRE` dans `tests/navigateur_cdp.py`, `test_e2e_parcours_donnees.py`, `test_e2e_parcours_synthese.py`.
- **Garde `vider_recordings_de_test`** (revue du correctif) : pour la base de test du conftest, seul un voisin nommé exactement `app.db` arrête le nettoyage ; une copie renommée (`app_backup.db`) dans un `TMP` mal posé passerait — piste : signature de contenu (table `interviews`) ouverte en lecture seule. Et quand la garde refuse (un `app.db` dans le temporaire), les résidus reviennent sans message : piste `warnings.warn`.
- **Lacunes de test déclarées** : le `confirm()` des boutons de relance n'est pas exercé (entretien de départ sans tours) ; le refus pendant une structuration `en_cours` avec worker vivant n'est pas amorçable depuis la base (registre en mémoire du processus).
- **Suite complète** : dernière exécution 1917 passés / 8 échecs / 3 erreurs en 2 h 35 min sur l'arbre AVANT les reports (pas rejouée depuis) ; durée ≫ 29 min 32 s mesurées le 2026-10-07 (cause inconnue, voir le run anormal du 2026-10-14).

## Retiré ou réfuté pendant la salle

AD-1 (relance audio n'appelle pas Ollama), AD-3 (`tranches_manquantes` compte bien les tranches `failed`), BH-2 (jeton aléatoire sans tranche : ni attente ni fausses tranches ; seul effet : un double POST sans `session_token` crée deux entretiens, assumé par la docstring), ET-1 (invariant couvert par `test_turns_display.py:496` et `:535`), ET-5, SP-3, SP-4 (la levée des skips de la coupe Ollama dans `conftest.py` voyage avec le lot), hypothèse SSRF Ollama (l'URL ne vient que de `OLLAMA_HOST`). CR-1 rétrogradé en mineur.

## Décisions de l'utilisateur (2026-10-20)

Ordre de fusion : **option A** (committer ce diff d'abord, puis fusionner `feat/transcription-fiable` — critère « un réessai réussi est compté et visible » livré là-bas, commits efeb088/835a2ef/1abc74d du worktree `C:\tmp\fiab`, conflits probables sur `audio_transcribe.py`, `record*.html`, `interviews_audio.py`, `interviews_saisie.py`, `interviews_retranscription.py`, `models.py`). EH-1 option B. Report de w9 : oui. Retrait du paragraphe « synthèse globale » de `libre_detail.html` : voulu, non reporté. AgentDiet : adopté (mesure à froid : part redondante médiane 1,5 %, pilote de filtre non justifié).
