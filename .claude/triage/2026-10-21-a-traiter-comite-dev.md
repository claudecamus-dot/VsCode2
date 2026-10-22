# À traiter — entrant du comité augmenté de dev (2026-10-21)

Sources : salle `code-review-crew` pre-push (5 voix, tours 1 et 2, tableau commun de la séance),
`2026-10-20-code-review-crew-lots-19-oct.md` (dette ouverte), revues Opus de la fusion.
État de départ : `feat/libre-async` à `24d494e` (fusion de `feat/transcription-fiable`), base réelle migrée.
La suite complète (`C:\tmp\pt_full_merge_20261021.log`) donne les échecs réels : **à ajouter ici dès
qu'elle est finie** (section « Échecs de la suite »). Le comité ne démarre qu'après elle.

Règles : un fichier = un propriétaire (`verifier_partition.py`) ; chaque correctif de bug livre son test,
prouvé rouge sur l'ancien code par `scripts/preuve_p1.py` EN COPIE ; revue adversariale d'un modèle
différent AVANT commit ; relire les correctifs eux-mêmes (une ronde de correction répète le défaut qu'elle corrige).

## A. À corriger avant tout push (arbitrés « oui » dans la salle, à confirmer)

| Id | Constat | Fichiers | Test attendu |
| --- | --- | --- | --- |
| AD-6 | `TranscriptionFondBusyError` (audio_transcribe.py:563) est recréée en `TranscriptionError(ECHEC_TRANSCRIPTION)` par `except Exception` de `_transcrire_bloc_sequentiel` (l.744-748) → `_est_reessayable` (audio_file_jobs.py:228-233) la juge réessayable : 3 × 1800 s au lieu de 1800 s. Relever l'exception avant le wrap. | audio_transcribe.py | via `iter_transcribe_blocks` ET via le job : le verrou de fond occupé n'est pas rejoué |
| SP-1 | Critère « un réessai réussi = compté et visible » non tenu hors direct : `rec_retry.js:116-117` n'affiche rien si `statut` = complet ; `libre_detail.html` ne montre aucun bilan ; l'import classique crée le job sans `interview_id` (interviews_audio.py:281-287). Proposition : message « N bloc(s) relancé(s) puis réussi(s) » même en complet ; `bilan_audio` passé à la fiche ; poser `interview_id` au rattachement. | rec_retry.js, interviews_saisie.py, interviews_audio.py, libre_detail.html | e2e ou route : fiche d'un entretien retranscrit avec `blocs_rejoues` non vide affiche le compte |
| SH-1 | Garde de lecture des noms audio plus faible que la garde d'écriture : `_noms_audio` (interviews_retranscription.py:126-145) ne rejette pas `:` (sous Windows `Path(dossier) / "D:x.webm"` sort du dossier). Base réelle propre (25 références). **Décision à prendre** : rejet de `:` seul (sans risque pour l'ancien) ou `_reference_sure` complet. | interviews_retranscription.py | `:` refusé à la lecture |
| CR-20 | Pas de source unique du nombre d'essais par bloc : plafond réel (2+1)×3 = 9 ; pauses 2 s dupliquées ; commentaires « 3 × 1800 s par bloc » faux. | audio_file_jobs.py, audio_transcribe.py | test de contrat figeant le produit |
| AD-3 | Le job rejoue un `ECHEC_TRANSCRIPTION` déjà épuisé au niveau bas (9 tentatives pour un bloc déterministe). **À arbitrer** : ne pas rejouer, ou garder pour les échecs transitoires. | audio_file_jobs.py | selon l'arbitrage |

## B. Défauts mineurs établis ou probables

| Id | Constat | Fichiers |
| --- | --- | --- |
| EH-1 | `max(1, …)` résiduel dans `_etat_audio` (interviews_libre.py:238-239) ≠ branche voisine `max(0, …)` : compte de la bannière faux (cas : toutes positions hors 0..999) | interviews_libre.py |
| EH-5 | `pendingSegments--` non borné (record_libre.html:1430) alors que les autres sont `Math.max(0, …)` | record_libre.html |
| EH-2 | job `running` d'avant la migration (completude NULL) repris après redémarrage : écart de durée jamais signalé (`non_evaluable`) | audio_file_jobs.py |
| AD-2 | attente de permis du FOND sans échéance (audio_transcribe.py:693-694) ; un FOND bloqué > 10 800 s peut être doublé par un clic « Reprendre ». **À arbitrer** : échéance, ou battement (`_battement`) pendant l'attente | audio_transcribe.py |
| AD-1 | `relancer_tranches_en_fond` occupe un thread jusqu'à 1800 s (BackgroundTasks synchrones) — sans effet pour un seul utilisateur | tranches_echec.py |
| TOCTOU | `_worker_vivant()` / `reserver_tranches()` (interviews_libre.py:142,157) → au pire un calcul IA en double ; piste : réservation sous condition SQL | interviews_libre.py, tranches_echec.py |
| done×2 | double `done()` possible si `uploadSegment` lève après avoir lancé son envoi (record_libre.html ≈ l.1372-1385, 1633-1637) | record_libre.html |
| m1 | `interviews_libre.py:334` `existant.tranches_manquantes = tranches` réécrit à l'identique (code mort) | interviews_libre.py |
| m2 | rejeu de niveau job : `TranscriptionBusyError` ne compte pas (fait) ; fenêtre « rejeu non commité perdu en cas de coupure » (information insuffisante) | audio_file_jobs.py |
| SH-2 | commentaires « route non authentifiée » périmés (interviews_audio.py:108-112, 247) | interviews_audio.py |
| EH-3 | `date.today()` suit le fuseau du serveur (templating.py:52-57) | templating.py |
| EH-7 | entrée de dédoublonnage dont le thread meurt : reste jusqu'à `TTL_VOL_S` (3600 s) | segment_dedupe.py |
| SH-1b / SH-2b | cache de dédoublonnage et jeton de tranches non cloisonnés (sans effet à compte unique) | segment_dedupe.py, interview_segment_jobs.py |

## C. Hygiène et lisibilité (aucun comportement)

| Id | Constat |
| --- | --- |
| CR-21 | commentaire périmé `SEGMENT_RETRY_DELAYS_MS` (audio_transcribe.py:184 ; tests/test_record_reseau.py:254,299) |
| CR-22 | `resultat_recent` et `PriorityLock.locked()` n'ont pour appelants que des tests ; `REGISTRE.vider` est utilisé (conftest) → à garder |
| CR-23 | `en_fond()` ≡ `avec_priorite(FOND)` (ai_common.py:503-518) ; « maintenant UTC naïf » écrit 3 fois (`_maintenant`, `_utcnow`, inline) ; `_is_stale` importé privé |
| CR-24 | 7 fichiers de test nommés d'après un lot (94 tests) : `test_concurrence_whisper_w7`, `test_lot3_w5_serveur_donnees`, `test_lot4_w9_{fiche,serveur,tranches}`, `test_mineurs_w10`, `test_conftest_vider_recordings_w10` → renommer par comportement (`git mv`, contenu intact) |
| CR-25 | doublures de test dupliquées : faux pools (4 variantes), `FakeSegment` (3), `FakeResponse` (4 dans un fichier) → module partagé |
| CR-26 | tests qui lisent une chaîne source (≈ 20 assertions `in html`) : vérifier qu'aucune ne porte sur de l'ordre d'exécution JS (interdit par CLAUDE.md) |
| CR-27 | JS inline dupliqué entre `record_libre.html` (~3000 l.) et `record.html` (~2000 l.) : extraire `prochaineTentative(nature, attempt)` dans `rec_retry.js` (remplace 4 copies) et `afficherCompletude` |
| AD-5 | 3 commentaires de tests citant encore `OLLAMA-COUPE-TEMPORAIRE` (navigateur_cdp.py:798, test_e2e_parcours_donnees.py:182, test_e2e_parcours_synthese.py:17) |
| Textes | « segment » / « tranche » / « bloc » pour la même chose ; « rejoué » → « relancé » ; alerte de complétude trop technique ; apostrophes ’ et ' mêlées (record.html:872) — **choix de vocabulaire à valider** |
| Tests déclarés manquants | le `confirm()` des boutons de relance n'est pas exercé (entretien de départ sans tours) ; refus pendant une structuration `en_cours` avec worker vivant non amorçable depuis la base |

## D. Décisions de l'utilisateur à obtenir (ne pas deviner)

1. SH-1 : `:` seul ou `_reference_sure` complet.
2. AD-3 : ne plus rejouer un échec épuisé, ou garder.
3. AD-2 : échéance du FOND, ou battement pendant l'attente.
4. Vocabulaire affiché (segment/tranche/bloc ; rejoué/relancé).
5. Hygiène du push : sorties runtime de `.claude/supervision/` dans les commits `a59d234` et `727fabe` ; `ci.yml` (timeout 20 min contre une suite de 30 min à 2 h 35, déclenchée seulement par une pull request).

## Échecs de la suite (à remplir à la fin du run)

Suite complète sur la fusion `24d494e` (`C:\tmp\merge-fiab`, config par défaut, `pytest -q`) : **8 échecs, 2078 passés, 2 skipped, 2 xfailed, 3 warnings en 13300 s (3 h 41)**, log `C:\tmp\pt_full_merge_20261021.log`. Machine fortement chargée pendant tout le run (agents et navigateurs en parallèle). Les 3 échecs de l'avant-fusion (`test_mission_backups`, `enchainer…`, `transcrit_la_parole`) sont verts.

| # | Test | Statut |
| --- | --- | --- |
| 1 | `test_e2e_parcours_synthese::test_telecharger_la_fiche_et_la_transcription_en_pdf` | **test périmé, corrigé** (branche `dev/sp1-ad6-sh1`) : la fiche s'ouvre sur l'Aperçu depuis `0f9fd4d` (défaut du 2026-10-19), le lien du PDF est dans l'onglet Transcription caché → le test clique l'onglet d'abord. Passe seul. À arbitrer : le lien PDF doit-il aussi figurer dans l'Aperçu ? |
| 2-6 | `test_e2e_premiers_clics::{test_parcours_premiers_clics, test_apercu_risques_et_maturite_suit_la_saisie_sans_rechargement, test_deck_exemple_televerser_puis_retirer, test_onglet_perime_axe_variante_rapprochee_et_axe_disparu_refuse, test_relancer_la_transcription_audio_depuis_la_fiche}` | **aléa de charge** : aucun ne se reproduit seul (40-70 s chacun), tous verts avant la fusion (19 passés). Machine saturée pendant 3 h 41. Pas d'étape précise identifiée (sections d'échec du log non lues). |
| 7-8 | `test_no_speech_et_marqueurs::test_un_segment_refuse_en_422_garde_son_audio_et_son_bouton_de_relance[record]` et `[record-libre]` | **test périmé, corrigé** (même branche) : le comportement produit est conservé (audio gardé, bouton de relance gardé, pas de relance automatique) ; la nature du refus est décidée par `RETRY_PLAN.classer` (rec_retry.js:60-65) depuis la fusion, l'assertion cherchait l'ancienne chaîne `result.data.code === 'no_speech'`. Le classement est figé à l'exécution par le test du plan partagé. 33 passés avec `test_navigateur_enregistrer_handlers.py`. |

## TODO — reprise en nouvelle session (état au 2026-10-22)

État : `feat/libre-async` poussée à `5e2eaa5` (AD-6 `cf2f03d`, SH-1 `b961f66`, SP-1 `4b536e1`, tests périmés `441984a`). Site 8020 frais. Run « livrer SP-1… » = `en-attente-validation`.

1. **Validation écran de SP-1 par l'utilisateur** (puis `log_run.py --solde "2026-10-21T21:20:05" succes "..."`) : fiche avec blocs relancés puis réussis ; fiche dont la dernière transcription a échoué ; enregistrement micro avec coupure réseau de 10 s ; coupure longue + relance des segments ; enregistrement Teams avec segments en échec ; relance de transcription depuis la fiche.
2. **Décisions utilisateur** (section D) : SH-1 (`:` seul ou `_reference_sure`), AD-3, AD-2, vocabulaire, hygiène du push (sorties runtime `.claude/supervision/` dans `a59d234`/`727fabe`, timeout 20 min de `ci.yml`), lien PDF de la transcription dans l'Aperçu, `interview_id` posé au rattachement de l'import classique.
3. **Comité de dev** sur les sections B et C (sans décision requise) : un fichier = un propriétaire, P1 par `scripts/preuve_p1.py` en copie, revue d'un modèle différent avant commit, relire les correctifs eux-mêmes.
4. **Reliquat non commité** : sorties runtime `.claude/supervision/*` et `docs/wiki*` ; décider de les committer ou de les ignorer (lié au point 2).
5. **Hub VSCode5** : prévenir la session `vscode5-supervision-projets-11` quand VSCode2 est au repos ; propagation `.claude/` en commit séparé ; rejouer `tests/test_agent_*.py` et les deux tests de contrat du dispositif avant.
6. **Worktrees** : décider du sort de `cd/V2pd`, `cd/VSCode2`, `fl/F-vscode2` et des 5 `.claude/worktrees/agent-*` (non touchés).
7. **Runs `en-cours` anciens** à solder avec accord : 2026-10-07, 20261015T151932-321de1, 20261016T174224-49c587, 20261019T104700-e6d266.
8. **Vérifier la CI GitHub** après push (rappel : `ci.yml` ne part que sur PR / push main).

## Journal d'actions — 2026-10-22

- **Mise à jour de Claude Code** : `claude update` a échoué (« Unable to fetch latest version from npm registry »), mais `npm view @anthropic-ai/claude-code version` répond `2.1.308` (registre `https://registry.npmjs.org/`, aucun proxy configuré) = version installée (`claude --version` → 2.1.308). Rien à mettre à jour. À relancer si l'échec de `claude update` se reproduit.
- **Salle `atelier-dev` (4 voix, tour 1) sur les défauts B/C** : partition de base A `interviews_libre.py` (EH-1, m1) / B `record_libre.html` + `record.html` (EH-5, 4 sites) / C `audio_file_jobs.py` + `audio_transcribe.py` (EH-2, CR-20, CR-21 app) / D tests et commentaires seuls (AD-5, SH-2, `test_record_reseau.py`). En attente de l'utilisateur : EH-1 (le `max(1,…)` est-il voulu ? un test existe déjà : `test_lot4_w9_serveur.py:274`), EH-2 (`non_evaluable` voulu ?), EH-3 (fuseau cible). Aucun fichier de code modifié.

## Traitement du 2026-10-22 (après la salle atelier-dev)

- **Branche cible de la propagation hub** : `feat/libre-async`. `main` est un ancêtre (0 commit que la branche n'a pas, 60 de moins) ; propager ailleurs créerait une divergence du dispositif. Commit limité à `.claude/`, sans les sorties runtime. Aucun push (hors allowlist).
- **Fermés par commit** : m1, SH-2, CR-21, AD-5, partie commentaire de CR-20 (commit `chore: drop a dead write and fix stale comments`, 134 tests rejoués verts).
- **EH-1 : sans suite.** Le `max(1, …)` est voulu : `tests/test_lot4_w9_serveur.py:274` fige `etat([-3]) == (True, 1)` (un segment reçu mais inexploitable laisse la position 0 manquante). Passer à `max(0, …)` déclarerait l'audio complet alors qu'aucune référence valide n'existe.
- **Restent ouverts** : EH-5 (4 sites `pendingSegments--`, preuve par le harnais JS site par site), source unique du plafond d'essais de CR-20 (constante dérivée dans `audio_transcribe.py`, aucun renommage des constantes patchées), EH-2 (`non_evaluable` semble voulu : décision utilisateur), EH-3 (fuseau cible : décision utilisateur), AD-1/2/3, TOCTOU, done×2, EH-7, CR-22 à CR-27, section D.

## Traitement du 2026-10-22 (suite) — fast-forward de `feat/libre-async` sur `4c0c93e`

Intégré par `integration/2026-10-22` (10 branches de travail + 3 de correction de la salle `code-review-crew`), **non poussé** (49 commits d'avance sur `origin`). Rejeu de l'intégration : 599 passés (tests touchés + voisins), 230 passés (JS + invariants, après la dernière ronde), e2e navigateur seuls 60 passés / 2 xfail attendus (51 min, aucun autre pytest en parallèle).

| Item | Décision | Commit(s) | Preuve |
|---|---|---|---|
| EH-5 (`uploadSegment`, `record.html`) | **réel**, `settle()` idempotent + garde du `.catch` | 371a649 | test node rouge/vert, P1 tenue |
| `uploadBackup` (`record.html` et `record_libre.html`) | **réel** : re-POST d'une tranche confirmée = position en double (libre) ; drapeaux `settled`/`acked`, gate libéré, message de perte | b553f3f, 5834dd7, 8e3cd6f, 3956b8c, 292f68d | tests node, P1 tenue |
| Revue de la salle (J1-J4, J1b, J2b) | blob de 0 octet non posté + avertissement visible ; `settle()` isolé ; message « NON SAUVEGARDÉ » seulement si la copie locale a échoué ; message si `maybeSubmitFinalJob` lève | bbef536, 7ffe8de, ad8ca90, b5672a6, f9bf185, 2408088, deb8e15 | tests node, P1 (mutations de texte pour J1b/J2b) |
| CR-20 | `plafond_essais_bloc(BLOC_RETRIES)` recalculé à l'appel (constante figée supprimée) | 5330fe7, 2015074 | invariant (pas de P1, aucun changement de comportement) |
| EH-2 | **défaut d'orchestrateur : `non_evaluable` voulu**, code inchangé, test + commentaire | 9894a67 | P1 tenue (mutation « fabrique 0.0 ») |
| AD-3 | **défaut d'orchestrateur : comportement inchangé**, test de contrat. Piste non appliquée : sous-classe `TranscriptionDeterministeError` | 4928751 | contrat |
| AD-2 | **défaut d'orchestrateur : battement** pendant l'attente du permis FOND (ContextVar `BATTEMENT_ATTENTE`, 60 s) ; rollback si le commit du battement échoue ; test de bout en bout par `run_audio_file_job` ; l'attente séquentielle (`audio_transcribe.py` ~l.591) n'a pas de battement : 1800 s < seuil de péremption 10 800 s | 80eebcf, cab0952, 98704c5, 6941ace, 9bd510d | P1 tenue |
| Fichier de 0 octet (trou n°2) | **défaut d'orchestrateur : 422**, porté par `ecrire_audio_borne(refuser_vide=True)` (aucun `unlink` dans la route, la règle « audio jamais supprimé » et `_EXCEPTIONS` restent intactes) | 8143458, 3043fc3, 1043438 | P1 tenue ; `test_audio_jamais_supprime` vert |
| Tests des trous du comité | CSRF des POST audio, upload coupé à mi-flux, référence audio absente, suppression verrouillée (409), câblage des 4 réconciliations du lifespan, `retranscrire_confirmer` (atomicité, jobs de tranche conservés, doublon de jeton plus court) | 45e6b06, c2790c8, 2979e73, 59b5218, 545a4df | P1 tenue (suppression verrouillée et référence absente : mutation manuelle annoncée, `preuve_p1.py` refuse les fichiers CRLF) |
| Test instable `test_mission_backups` | **non reproduit** (8 puis 10 runs verts) : durcissement seul (nettoyage des `{mid}_*`), cause non établie | 310daac | 10 runs verts |
| EH-1, EH-7, TOCTOU, AD-1, SH-1b | **sans effet / voulu** (voir la salle) | — | lecture, test existant `test_lot4_w9_serveur.py:274` pour EH-1 |

**Restent ouverts** (rien d'appliqué) : EH-3 (fuseau cible à choisir, 3 occurrences de `date.today()`) ; SH-1 (`:` seul ou `_reference_sure`) ; SH-2b (jeton de tranches, filtré par mission à `interviews_libre.py:309`, classé sans suite) ; vocabulaire segment/tranche/bloc ; lien PDF dans l'Aperçu ; hygiène du push (timeout 20 min de `ci.yml`) ; garde de propriété de `retranscrire_confirmer` (texte du formulaire, ancien `raw_transcript` écrasé sans copie) ; **8 erreurs `ruff check app/` préexistantes** sur `feat/libre-async` (imports non triés, dont `app/main.py`, `app/templating.py`) : le gate CI échoue déjà ; CR-22 à CR-27 ; validation écran de SP-1 par l'utilisateur ; rafraîchissement de la preuve manuelle de `preuve_p1.py` sur fichiers CRLF (à signaler au hub, fait).
**Constats de méthode** : la salle a pris ~67 min par voix pour 12 prévues (cause non établie) ; le rejeu e2e groupé en parallèle donnait 13 échecs de charge, seul 0 échec ; le premier rejeu d'intégration n'incluait pas `test_audio_jamais_supprime`, qui a révélé le défaut de l'`unlink` ; chaque ronde de correction du JS a réintroduit un défaut de la classe corrigée (5 rondes).
