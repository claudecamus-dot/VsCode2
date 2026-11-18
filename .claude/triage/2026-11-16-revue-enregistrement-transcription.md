# Revue approfondie — enregistrement + transcription (2026-11-16)

Demande utilisateur du 2026-11-16 : « relance une revue approfondie de tous les travaux
enregistrement + transcription ». 4 relecteurs Opus (`bmad-revue` → `bmad-code-review`),
lecture seule, sur `main` = `feat/libre-async` @ `fc2dd2f`. Angles : A client navigateur,
B routes serveur, C moteur de transcription, D exploitation/tests.

Statut de chaque constat : `ouvert` tant qu'il n'est ni arbitré ni corrigé. Rien n'est
corrigé sans arbitrage utilisateur (R4). Niveau de preuve : CONSTAT (code lu/exécuté) ou
HYPOTHÈSE.

## D — exploitation, observabilité, tests (rendu 2026-11-16)

| id | gravité | où | constat | preuve | correctif proposé | effort | statut |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D-1 | majeur | app/main.py:16,125-203 | Aucune config logging dans app/ : tous les `logger.info` du domaine sont perdus ; les erreurs sortent sans horodatage (lastResort) | CONSTAT (reproduit en processus) | RotatingFileHandler `data/logs/app.log` (asctime, level, name, thread) + `faulthandler.enable` ; test caplog + handler présent | S | ouvert |
| D-2 | majeur | tests/conftest.py:106,258 | Verrou de base de test jugé périmé après 45 min même si le détenteur vit → un 2e pytest le vole pendant une suite de 2 h 35 → WinError 32 | CONSTAT (lecture) | âge seulement si PID/démarrage inconnu, ou rafraîchir l'horodatage | S | ouvert |
| D-3 | majeur | config pytest absente | Pas de `faulthandler_timeout` en local (la CI l'a) ; en `-q` les noms d'échecs n'existent qu'au résumé final | CONSTAT ; lien avec 9 h 59 = HYPOTHÈSE | `addopts = -o faulthandler_timeout=300` + écriture au fil de l'eau des nodeid en échec | S | ouvert |
| D-4 | majeur | tests/e2e_serveur.py:26,50,79-91 | Log uvicorn relu seulement si le démarrage échoue, jamais sur un e2e rouge ; `--log-level warning` ; délai 60 s fixe | CONSTAT (lecture) | joindre la fin du log à l'échec, log-level info, délai réglable | S | ouvert |
| D-5 | moyen | tests/e2e_serveur.py:68,77 | Mot de passe lu dans `os.environ` au lieu de `env`, dans un `except` muet → 60 s puis faux diagnostic | CONSTAT (lecture) | lire `env.get(...)` avant la boucle, échec explicite | S | ouvert |
| D-6 | moyen | scripts/serveur-dev.ps1:120 | `-like "*parent_pid=$id*"` = match par préfixe (le correctif « non-chiffre ou fin » de la l.177 non reporté) → la purge peut tuer le worker d'un autre serveur | CONSTAT (lecture, 2 couches) | reprendre le `-match` borné de la l.177 dans `Get-DescendantsProcessus` | S | ouvert |
| D-7 | moyen | scripts/serveur-dev.ps1:56-59,78-81,311-319,425,683 | Grâce de 900 s fondée sur « Whisper tient le port fermé », caduque depuis 1a34437 → un worker périmé épargné 15 min | CONSTAT | grâce ~120 s, messages réécrits ; `-CheckOnly` en hook post-merge | S | ouvert |
| D-8 | moyen | scripts/serveur-dev.ps1:377-386,462-463,509 | Timeout d'`Invoke-PythonBorne` sous charge → « mot de passe absent » ou « périmé » → `-KeepIfFresh` peut purger un serveur sain chargé | CONSTAT (diagnostic faux) ; lien avec les morts de 8020 = HYPOTHÈSE | état « indéterminé » sans purge, afficher le stderr | S | ouvert |
| D-9 | moyen | scripts/serveur-dev.ps1:679-686 | Un processus déjà mort (ex. SchemaEnRetard) affiche « démarré lentement » ; aucune trace horodatée de lancement/mort | CONSTAT (branche) ; cause des morts = HYPOTHÈSE | branche ExitCode + fin du .err ; `serveur-dev.history.log` horodaté | S | ouvert |
| D-10 | moyen | audio_transcribe.py:444-469 ; tests/test_audio_jamais_supprime.py:22-23,128-145 ; tests/test_interview_segment_jobs.py:830 | Garanties sans test : (a) job fichier pendant un chargement Whisper attend sans borne (décision utilisateur en attente) ; (b) « audio survit au succès/échec du job » non testé, détecteur AST aveugle à `Path.open`/`os.open` ; (c) `<=` passe avec 0 appel | CONSTAT (b,c cités par les couches) | (a) après arbitrage ; (b) test comportemental + détecteur étendu ; (c) `==` | M | ouvert |
| D-11 | léger | test_audio_jamais_supprime.py:522 ; test_segment_jobs_unicite.py:93 ; test_interview_segment_jobs.py:~1180 | Harnais fragiles : découpe `[:4000]`, `raising=False` qui désactive un patch sans bruit, sessions sans `with` | CONSTAT (couches) | borner au décorateur suivant, retirer `raising=False`, `with SessionLocal()` | S | ouvert |
| D-12 | léger | .github/workflows/ci.yml:15 | CI Linux/3.12 seulement : verrou ctypes, serveur-dev.ps1, ACL jamais exercés | CONSTAT | job matrice windows-latest/3.14 limité (`not e2e` + dispositif) | M | ouvert |

Fait infirmé par D : les ERROR/exception atterrissent bien dans le `.err` (lastResort), seules
les INFO sont perdues. Hypothèse non vérifiée : index unique partiel posé sans dédoublonnage
(app/db.py:223-228).

## A — client navigateur (rendu 2026-11-16, STATUT partiel : non croisé avec les triages antérieurs, doublons possibles)

| id | gravité | où | constat | preuve | correctif proposé | statut |
| --- | --- | --- | --- | --- | --- | --- |
| A-1 | à corriger | record_libre.html:3128-3152 | Bannière de reprise du brouillon visible après Démarrer, « Reprendre » sans garde `recordingActive` → écrase texte, jeton de session, positions et tranches de la session vivante | CONSTAT (lecture) | masquer au démarrage + sortir du gestionnaire si `recordingActive` ou si `transcriptSoFar` est non vide ; test exécuté | ouvert |
| A-2 | à corriger | record_libre.html:1772 | `resetNoSpeech(false, lostId)` (story 2.1, 2e série) exécuté par aucun test : tous les harnais d'`uploadSegment` le stubbent → une régression vers `resetNoSpeech()` passe la suite | CONSTAT (couche VG) | test exécutant `uploadSegment` avec les vrais `rendreAlerte`/`resetNoSpeech` (2×422 no_speech, 200 lostId, 200 courant ; tabLost puis 200) | ouvert |
| A-3 | à corriger | record_libre.html:877-898 | Mode démo, scroll 0 : `body.paddingTop` pousse le demo-banner sticky sous l'alerte (z 10000) qui le recouvre, bande vide au-dessus ; l'e2e ne vérifie que `top == 30` | HYPOTHÈSE (2 couches, non rendu) | marge sur `main` ou alerte en top 0 au-dessus ; e2e `elementFromPoint` sur le demo-banner | ouvert |
| A-4 | à arbitrer | record_libre.html:1054 | Re-partage réussi efface l'alerte « muet » (écart à la lettre d'AC2.1.2) ; si l'onglet re-partagé est lui aussi muet (case audio non cochée — le cas de l'entretien 23), l'alerte disparaît ~2 min | CONSTAT | décision par défaut de l'orchestrateur le 2026-11-16 (signalée, non contestée) à reconfirmer ; sinon ne plus effacer au re-partage | ouvert |
| A-5 | à corriger | record_libre.html:3043-3061,3110 | `setInterval(pollRepartition, 5000)` avec délai 120 s et sans garde « en vol » → requêtes chevauchées, aperçu qui perd/duplique des tours | CONSTAT (absence de garde) / HYPOTHÈSE (effet) | drapeau `repPollEnVol` | ouvert |
| A-6 | à corriger | rec_audio_source.js | Stop pendant que le sélecteur d'onglet est ouvert puis résolution tardive → micro/AudioContext réacquis après l'arrêt | HYPOTHÈSE | jeton de génération | ouvert |
| A-7 | à corriger | record_libre.html:1761-1775 | 2xx avec texte vide → segment perdu bloquant au lieu d'un équivalent no_speech | HYPOTHÈSE | traiter comme no_speech | ouvert |
| A-8 | à corriger | record_segment_wait.html:89-100 ; libre_segment_wait.html:94-105 | Poll sans borne, sans traitement du 401 ni de `total == 0` | HYPOTHÈSE (2 couches) | borne de sortie + export/copie proposés | corrigé (d810f1d, branche fix/s33-navigateur) ; seuils 20 min / 6 échecs / 3 réponses vides validés par l'utilisateur le 2026-11-16 |
| A-9 | à corriger | rec_retry.js | 408/429 non classés « relance » → segment perdu bloquant ; lien avec « réseau ×12 » non établi | HYPOTHÈSE | classer en relance | ouvert |
| A-10 | mineur | record.html:3872-3892,5369-5401 | Mode guidé : `noteNoSpeech` sans garde `recordingActive`, ancienne alerte (périmètre de la story 2.1 ?) | HYPOTHÈSE | aligner sur record_libre | reporté le 2026-11-17 (hors Story 3.3, mineur : à reprendre dans une story dédiée) |
| A-11 | mineur | rec_draft.js:163-188 | `savedAt` absent → « NaN/NaN », jamais périmé | CONSTAT (Blind Hunter) | garde | corrigé (branche fix/s33-navigateur, revue finale 2026-11-17) |
| A-12 | mineur | rec_draft.js:40 | Une seule clé par mission : deux onglets s'écrasent | CONSTAT | clé par session | reporté le 2026-11-17 (hors Story 3.3, mineur : à reprendre dans une story dédiée) |
| A-13 | mineur | rec_draft.js:142 vs 153 | Ordre des paramètres incohérent `marquerEnvoye` / `effacerSiEnvoye` | CONSTAT | aligner | reporté le 2026-11-17 (hors Story 3.3, mineur : à reprendre dans une story dédiée) |
| A-14 | mineur | record_libre.html:549-556 | Brouillon sans identité saisie ni segments perdus relançables | CONSTAT | étendre le brouillon | reporté le 2026-11-17 (hors Story 3.3, mineur : à reprendre dans une story dédiée) |
| A-15 | mineur | record_libre.html:3146 | `nextBackupPosition = length` : réutilisation de numéro si positions restaurées non contiguës | HYPOTHÈSE | max(position)+1 | corrigé (branche fix/s33-navigateur, revue finale 2026-11-17) |
| A-16 | mineur | rec_retry.js:24-33 ; record_libre.html:1616-1622 ; rec_fetch.js:83-93 | Libellés « (1/1) » vs « (2/3) », commentaires périmés, `STATUT_ECHEC_MOTIFS` incomplet | CONSTAT | harmoniser | reporté le 2026-11-17 (hors Story 3.3, mineur : à reprendre dans une story dédiée) |
| A-17 | mineur | autosave.js:168-183 | 403/413/422 et `sendError` effacés au bout de 3 s alors que rien n'est enregistré ; `htmx:timeout` non écouté | CONSTAT | garder l'erreur affichée | reporté le 2026-11-17 (hors Story 3.3, mineur : à reprendre dans une story dédiée) |
| A-18 | mineur | rec_fetch.js:55 | Le minuteur écrase le `signal` fourni par l'appelant | CONSTAT | `AbortSignal.any` ou chaînage | corrigé (branche fix/s33-navigateur, revue finale 2026-11-17) |
| A-19 | mineur | rec_audio_source.js:66-73 | Échec de `resume()` ou piste `muted` sans rappel → enregistrement silencieux possible | CONSTAT | prévenir l'utilisateur | reporté le 2026-11-17 (hors Story 3.3, mineur : à reprendre dans une story dédiée) |
| A-20 | mineur | AC2.1.1 | Durée muette en `aria-live` poli hors de la région `role="alert"` (conforme à l'arbitrage B du 2026-11-16) | CONSTAT | aucun (arbitré) | arbitré |

## B — routes serveur (rendu 2026-11-16, STATUT partiel : base non lue, CSRF non vérifié route par route, non croisé avec les triages antérieurs)

| id | gravité | où | constat | preuve | correctif proposé | statut |
| --- | --- | --- | --- | --- | --- | --- |
| B-1 | bloquant (selon B) | interviews_libre.py:285-290 ; structuration_libre.py:543-560 | Une seule tranche en échec + audio présent → tout l'entretien en `a_transcrire` malgré du texte, `tranches_manquantes` à 0, aucun bandeau ; `peut_relancer` refuse cet état ; docstring (~248-250) annonce un avertissement que le code ne pose pas ; comportement figé par un test | CONSTAT (lecture) | **déjà arbitré le 2026-11-16 : garder la règle**. Reste hors arbitrage : docstring fausse, absence d'avertissement visible, et absence de sortie quand la retranscription ne produit aucun tour | arbitré (règle) / ouvert (avertissement, sortie) |
| B-2 | à corriger | interviews_segment_jobs.py:255-259 | `next_since = max(position+1)` des tranches finies : une tranche finie hors ordre fait sauter la précédente dans l'aperçu live (l'entretien sauvegardé n'est pas touché) | CONSTAT (3 couches) | ne pas dépasser la plus petite position non terminée | ouvert |
| B-3 | à corriger | interviews_audio.py:343 vs 393,502 | Jeton stocké tronqué à 64 car. mais comparé entier → 404 sur status/retry si jeton > 64 | CONSTAT (lecture) | `normaliser_jeton` à l'écriture et aux deux comparaisons | ouvert |
| B-4 | à corriger | interviews_backup.py:264-277 ; interviews_retranscription.py:199-207 ; interviews_libre.py:232-241 | Supprimer un segment de backup renumérote les positions et efface le « trou » → confirmation de retranscription conclut « complet » | HYPOTHÈSE forte | ne plus renuméroter / position d'origine distincte | ouvert |
| B-5 | à corriger | interview_segment_jobs.py (`segment_jobs_status*`) ; models.py | Lectures non filtrées sur `kind` (`libre_turns` / `answers`) → compteurs mélangés | HYPOTHÈSE | filtrer sur `kind` | ouvert |
| B-6 | à corriger | interview_segment_jobs.py (~574) | Récupération synchrone d'un job encore `running` → `done`, puis la tâche d'origine réécrit `failed` | HYPOTHÈSE | écriture conditionnelle (statut/version) | ouvert |
| B-7 | à corriger | structuration_libre.py:339-351 | Relancer un entretien « fait » partiel supprime tous les tours, corrections manuelles comprises, sans avertissement | HYPOTHÈSE (2 couches) | `garde_edition` / confirmation | ouvert |
| B-8 | à corriger | interviews_segment_jobs.py:84,114 | Jeton fait d'espaces → normalisé vide, lignes orphelines partagées | HYPOTHÈSE | 400 | ouvert |
| B-9 | à corriger | interviews_libre.py (`record_libre_confirm`) | `_creer_interview_libre` sans `segment_token` → double POST = deux entretiens | HYPOTHÈSE | passer le jeton (index unique) | ouvert |
| B-10 | à corriger | mission_backups.py:618 | `int(prefixe)` au lieu de `mission_id_du_fichier` → fichier « 007_x » invisible | HYPOTHÈSE | utiliser la fonction | ouvert |
| B-11 | à corriger | mission_backups.py:192-233 | `references_audio_sures` sans garde `_anterieur_a_la_mission` → après réutilisation d'id SQLite, rattachement d'un ancien fichier | HYPOTHÈSE | ajouter la garde | ouvert |
| B-12 | à corriger | structuration_libre.py (`_traiter`) | Exception hors `try` tue le fil worker, ids restés dans `_en_vol` → « déjà en file » jusqu'au redémarrage | HYPOTHÈSE | `try/finally` | ouvert |
| B-13 | mineur | divers (voir rapport) | remarque perdue (`question or remarque`), dédup par sous-chaîne qui écarte « Oui », `segment_tail` non repris, faux 410 au retry de retranscription, fuite de compteur, tri `position or 0`, `SEGMENT_JOB_STALE_AFTER_S` non borné | HYPOTHÈSE | au cas par cas | ouvert |
| B-14 | mineur | divers | Code mort et docstrings périmées | CONSTAT | nettoyage | ouvert |

## Synthèse et lots proposés (à arbitrer)

Doublons : C-5 = D-1. Recoupements : A-4 et B-1 touchent des arbitrages du 2026-11-16 ; C-3
rejoint la décision utilisateur en attente sur l'attente `_get_model` sans borne.

- **Lot 1 — observabilité (préalable à tout diagnostic)** : D-1, D-3, D-4, D-5, D-9. Effort S.
- **Lot 2 — transcription qui peut ne jamais avancer** : C-1, C-2, C-3, C-6, C-7. Story à rédiger.
- **Lot 3 — perte de données côté navigateur** : A-1, A-2, A-5, A-7, A-9. Story à rédiger.
- **Lot 4 — intégrité serveur** : B-2, B-3, B-4, B-9, B-11, B-12, puis vérification des hypothèses B-5 à B-8. Story à rédiger.
- **Lot 5 — qualité de transcription** : C-4 (hallucinations sur silence). Story à rédiger.
- **Lot 6 — outillage dev/tests** : D-2, D-6, D-7, D-8, D-10, D-11, D-12.
- **Mineurs** : A-10 à A-19, B-13, B-14, C-8 à C-11 — au fil des lots qui touchent les mêmes fichiers.

## C — moteur de transcription (rendu 2026-11-16, STATUT partiel : templates non lus, file Ollama non couverte)

| id | gravité | où | constat | preuve | correctif proposé | statut |
| --- | --- | --- | --- | --- | --- | --- |
| C-1 | élevé | audio_transcribe.py:207-214,579-595 ; main.py:157-158 | La borne de 900 s inclut le chargement du modèle : sous charge (12-15 min), le 1er bloc de chaque worker dépasse 900 s → échec ; la relance tue les workers donc repaie le chargement → peut ne jamais avancer (le cas de l'entretien 23) | HYPOTHÈSE (chiffres du code, timing non mesuré) | échéance démarrée quand le worker signale « modèle prêt », ou borne propre au 1er bloc ; test avec faux modèle dont le constructeur dort | ouvert |
| C-2 | moyen | audio_transcribe.py:909-926 vs 706 | `_sequentiel_borne` (0 worker, modèle chargé) attend 900 s alors que l'attente légitime de `_MODEL_LOCK` derrière le direct va jusqu'à 1800 s → échec non réessayable au message « mémoire » faux ; le fil non tuable transcrit ensuite un bloc jeté | CONSTAT (reproduit à échelle réduite) | horloge démarrée à l'obtention du verrou, Event d'abandon après `acquire`, message distinct | ouvert |
| C-3 | moyen | audio_transcribe.py:1244,1287-1290,709 | Réserve « attente sans borne » réévaluée : restent sans borne ni battement le job d'un seul bloc et le repli séquentiel final après casse du pool — qui charge ~1,6 Gio dans le serveur sans garde mémoire juste après un BrokenProcessPool (typiquement un manque de mémoire) | CONSTAT (lecture) / impact HYPOTHÈSE | repli final via `_sequentiel_borne` + garde mémoire ; `_ATTENTE_CHARGEMENT_MAX` côté fond pour un « occupé » réessayable | ouvert (rejoint la décision utilisateur en attente) |
| C-4 | moyen | audio_transcribe.py:606,711,1359 | Hallucinations sur silence non filtrées : `condition_on_previous_text` par défaut, aucun filtre no_speech_prob/avg_logprob/compression_ratio, aucune liste de phrases connues → « Merci d'avoir regardé cette vidéo ! » ×7 conservé ; un bloc d'hallucinations neutralise probablement l'alerte « aucune parole » | CONSTAT (code absent) / effet sur l'alerte HYPOTHÈSE | `condition_on_previous_text=False`, filtre par segment, liste de phrases, repli des répétitions | ouvert |
| C-5 | moyen | app/ (aucune config logging) | = D-1 (INFO perdus, WARNING+ non horodatés dans le .err) | CONSTAT (reproduit) | voir D-1 | doublon de D-1 |
| C-6 | moyen-faible | audio_file_jobs.py:126-186 (appelé en 552) | `_extraire_tours` écrit sans contrôle de propriétaire → un fantôme pendant l'extraction re-date le job et crée un segment job en double à la même position → tours dupliqués | CONSTAT (lecture) | contrôle de propriétaire avant chaque insertion et battement | ouvert |
| C-7 | faible | audio_file_jobs.py:453-458,473 | `TranscriptionMemoireInsuffisanteError` jamais testée au niveau job : retirée du tuple, elle serait avalée comme « tranche illisible » → tranches perdues | HYPOTHÈSE (mutation non jouée) | test au niveau job | ouvert |
| C-8 | faible | audio_transcribe.py:958 vs 978-999 | Mémoire mesurée avant l'attente illimitée des permis → lecture périmée | CONSTAT | remesurer après les permis | ouvert |
| C-9 | faible | audio_transcribe.py:196-203,816-817 | Modèles `.en`/`distil-*`/`turbo` estimés à 3,2 Gio ; repli Linux SC_AVPHYS_PAGES = mémoire libre, pas disponible → 0 worker | CONSTAT | table complétée, MemAvailable | ouvert |
| C-10 | faible | audio_transcribe.py:677-679,925 | En LIVE, le repli final appelle `_get_model` sans borne (contextvar non copiée dans le Thread) ; message « Relancez — blocs conservés » inadapté à un segment | CONSTAT (déclencheur rare) | copier le contexte, message dédié | ouvert |
| C-11 | info | ai_common.py:681 | Docstring « silencieuse en cas d'échec » alors que la fonction lève (l'appelant attrape) | CONSTAT | corriger la doc | ouvert |

Note C : `epics.md` affiche encore « Statut : à faire » pour les stories 2.1 et 2.2, fusionnées
et validées par l'utilisateur le 2026-11-16 — à mettre à jour.

## Arbitrages utilisateur du 2026-11-17 (revue finale des stories 3.1-3.4)

- Filtre d'hallucinations (3.2) : la règle « phrase de silence répétée ≥ 2 fois dans un bloc → écartée » est **conservée** telle quelle (plus agressive que RUN_REPETITION_MIN = 3, choix assumé).
- AC3.2.1 seconde clause (réutiliser les workers Whisper déjà chargés à la relance d'un job) et AC3.2.4 (bloc fait d'une hallucination répétée hors liste, confiance moyenne → « aucune parole ») : **à corriger avant fusion**.
- B-2 (tranche en échec récupérée après le curseur, absente de l'aperçu live) : **à corriger** (client record_libre.html + curseur serveur), sur la branche d'intégration.
- CLAUDE.md : mise à jour du paragraphe du site 8020 (grâce 120 s, plafond « actif » 1800 s, codes de sortie 4 et 5) **acceptée**, après fusion.

## Clôture du 2026-11-17 : stories 3.1 à 3.4 fusionnées dans main (non poussé)

Fusions locales sur main : 3.4 (4deed34 + reliquats 7770651), 3.3 (58baa5b), 3.2 (c9dbebc + arrêt du pool garé 153a78c), 3.1 (0b01e80 + journal des workers et CLAUDE.md 542c2f1), B-2 (29fb726), clics P5 (3756668). Chaque story : revue finale bmad-code-review, tours de correction avec preuve P1, re-revue des correctifs.

Restes ouverts (non bloquants, à reprendre dans une story dédiée) :
- 3.4 F4 : supprimer la DERNIÈRE partie d'un entretien ne laisse aucun trou détectable (aucune colonne ne porte le nombre de tranches attendu ; une marque dans `audio_segments` toucherait 75 lectures dans 19 fichiers) — à arbitrer, proposition : colonne dédiée.
- 3.4 : B-14 non fait ; tri `position or 0` (interviews_backup.py, mission_backups.py) ; B-5 non appliqué à `segment_jobs_status` complet.
- 3.3 : en mode libre (route historique `POST /record-libre`), le signal « transcription incomplète » s'affiche sur la revue mais n'est pas persisté dans l'entretien créé (lacune antérieure) ; `record_segment_wait.html` sans clic e2e de sortie.
- 3.2 : pool chaud repris seulement à taille EXACTE — rechargements entre fichiers de durées différentes (performance, pas correction) ; B1-B3/B5 de la re-revue du pool (attente sans battement, gardes mémoire du direct qui ignorent le pool garé).
- 3.1 : purge `-KeepIfFresh` par âge du worker (arbitrage utilisateur) ; serveur non identifiable avec certitude → « indéterminé » sans purge (le lancement manuel purge) ; tests de minutage instables sous forte charge (test_audio_fond_busy_non_rejoue, test_audio_permis_fond_attente, test CPU de test_serveur_dev_script).
- B-2 : positions sans ligne (POST de segment échoué) redemandées à chaque sondage, borné à 500 ; tranche ré-extraite après réception non rafraîchie dans l'aperçu.
- A-10, A-12, A-13, A-14, A-16, A-17, A-19 : reportés (voir tableau A).

## Clôture du 2026-11-18 : Story 3.5 et suite

- 3.4 F4 : traité par la Story 3.5 (colonne `parties_supprimees`), fusionnée 8b92378, poussée, CI #79 verte, validée à l'écran par l'utilisateur le 2026-11-17.
- Stories 3.1 à 3.4 poussées le 2026-11-17, CI verte.
- Restes 3.5 : confirmation de suppression trop large pour un fichier orphelin ; revue de retranscription et bandeau de finalisation muets sur les parties supprimées ; vraie concurrence non testée.
- Arbitrage utilisateur du 2026-11-18 (« finalise les travaux de l'entretien libre ») : TOUS les restes ouverts ci-dessus sont repris en stories 3.6 (lacunes visibles), 3.7 (A-12 à A-19 sauf A-10, reste B-2), 3.8 (B-5, B-14, tris par position), 3.9 (pool chaud, battements, tests de minutage) — voir `epics.md`. A-10 (mode guidé) reste reporté, hors entretien libre.
