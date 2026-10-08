# Triage — lot « transcription fiable » (2026-10-08)

Critère utilisateur : « une transcription de la totalité de l'audio sans segment failed
du 1er coup » ; un retry réussi est un échec masqué (compté, exposé), les retries restent
le filet. Branche `feat/transcription-fiable`, worktree isolé depuis 32b5035 (la branche
`feat/libre-async` et ses fichiers non commités ne sont pas touchés). Salles :
`atelier-idees` (cadrage), `atelier-dev` (partition, preuve). Revue du commit 1 : une
passe adversariale `bmad-code-review` en contexte unique, sans exécution.

## Décisions de salle (unanimes après deux tours)

Aucun bloc sauté ni vide persisté ; 3 tentatives au total par bloc (pool compris), palier
suivant jamais le même ; définitifs : TranscriptionError (dont NoSpeechError), OSError,
décodage ; épuisement → job `failed`, reprise par `start_index` ; retry avant le `yield` ;
signature du générateur inchangée, `stats` additif. Décision utilisateur « go » du
2026-10-08 sur D1 : colonne JSON `completude` (migration `APP_DB_MIGRATE=1` au moment de
la fusion) et `code` serveur sur la dictée.

## Commit 1 — retry par bloc

| Id | Sév. | Constat | Décision |
| --- | --- | --- | --- |
| R1 | majeur | Le test « erreur non liée au pool » affirmait qu'une erreur déterministe n'est pas rejouée ; le code la rejoue (1 à 2 pools relancés avant l'échec) | corrigé : test renommé et docstring réécrite ; coût assumé et écrit |
| R2 | mineur | `shutdown(wait=True)` attend la fenêtre de futures d'un pool en échec non-Broken, puis le palier suivant les retranscrit (jusqu'à 2×n blocs, minutes) | ouvert — hypothèse non mesurée ; piste `cancel_futures=True` dans `_drain_parallel` |
| R3 | mineur | `BrokenProcessPool` imputé au premier bloc non rendu, pas forcément au fautif | corrigé : commentaire d'attribution approximative, borne tenue |
| R4 | mineur | Le test du journal n'affirmait pas l'absence de chemin/texte | corrigé : gabarit strict (`re.fullmatch`) |
| R5 | mineur | `stats` non branché sur les appelants ; `blocs_rejoues` remis à zéro à la reprise ; `duree_decodee_s` absent si le décodage échoue | prévu commit 2 (cumul côté job, lecture `.get`) |
| R6 | mineur | `ConnectionResetError`/`BrokenPipeError` d'un pipe de pool classés définitifs (OSError) | ouvert — pas de régression, hypothèse |
| R7 | dette | Direct (`transcribe_audio`, `_transcribe_parallel`) sans retry par bloc | hors lot — nommé |

Preuve P1 par `scripts/preuve_p1.py` : pool (ligne ~696), repli séquentiel (~721) et
bloc unique (~684), chacune « PREUVE P1 TENUE », fichier restauré octet pour octet.

## NON TRAITÉ (lot 1, décisions de périmètre)

- Écart retranscription (tranche illisible → `done`) contre fichier unique (→ `failed`) :
  signalement via le bilan de complétude, alignement du comportement renvoyé à un ADR.
- Persistance serveur des segments du direct : lot 2. Journal d'échec côté serveur dans
  `transcribe_segment` : dans le lot.
- `capture.html` (dictée de notes) : sans retry client, dette.
- Durée de conteneur absente sur les `.webm` de `MediaRecorder` : le bilan dira « non
  vérifiable » ; fréquence non mesurée.
