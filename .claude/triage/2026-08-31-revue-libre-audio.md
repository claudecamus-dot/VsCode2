# Triage — revue de l'enregistrement libre audio (2026-08-31)

Run d'orchestration `/orchestre` — playbook `revue-design-parallele`, 2 angles en fan-out
(backend `general-purpose` opus, frontend `bmad-revue` → `bmad-review-edge-case-hunter` opus),
puis consolidation en session principale.

**Statut global : aucun correctif appliqué.** Le checkpoint du playbook est explicite — la
revue est le livrable, les correctifs sont un mandat séparé. Tous les constats sont donc
`differe` tant que l'utilisateur n'a pas arbitré ce qui se corrige.

> **Mise à jour 2026-08-31 (reprise, session vscode2-78).** Une session ultérieure a
> appliqué, avec arbitrage utilisateur, des correctifs pour B1, B2+B3, F1, F2, F10 —
> commit `2bf44b7` (commité et poussé par la session pair vscode5-supervision pendant la
> vérification). Vérifié à la reprise : suite complète **630 passed** (un premier run à
> 120 erreurs = verrou Windows transitoire sur la base de test, non reproduit), rendu réel
> des 2 écrans OK (serveur frais prouvé, empreinte `314b4b7d49f06174`), **gate R3** tenu
> (revue adversariale du correctif, POST-commit faute d'avoir pu bloquer) → **12 constats
> nouveaux (0 bloquant, 6 MAJEUR, 6 MINEUR)**, § « Revue du correctif » plus bas.
> Statuts par constat mis à jour dans les tables. Les 12 nouveaux constats sont `differe`
> en attente d'arbitrage.

> **Mise à jour 2026-09-01 (session vscode2-79).** Commit `c79e8b5` — règle produit
> « l'audio ne se supprime QUE par une action de l'utilisateur sur le site » — ferme
> **EC-1**, **EC-3** et **F3** (= EC-1), qui étaient encore `differe` dans ce fichier
> alors que le code les traitait : le triage était périmé de plusieurs heures. Le
> `differe` non arbitré passe donc de 14 à **12 constats**. Vérification du commit :
> suite complète **653 passed** (relance intégrale après correctif d'une régression de
> `test_mission_backups`), CI GitHub Actions run #35 **success** sur `c79e8b5`.
> ⚠️ Ce fichier ne se met pas à jour tout seul : un constat fermé par un commit doit
> l'être ICI dans le même mouvement, sinon le backlog ment sur ce qui reste à faire.

> **Mise à jour 2026-09-02 (run `/orchestre` « finalise l'entretien libre »).** **F9** et
> **EC-6** (le même défaut) sont **corrigés** : « Recommencer » vide désormais l'onglet
> Répartition (`renderRepartition([])`), et `pollRepartition` fige son jeton au départ pour
> qu'une réponse en vol ne réaffiche pas la session jetée — une porte de derrière que le
> seul vidage laissait ouverte, et qui existait AUSSI sur le chemin frère `record.html`
> (corrigé du même coup). Le trou de test qui rendait ces défauts invisibles est comblé par
> `tests/test_repartition_live.py`, qui EXÉCUTE réellement le JS extrait du template sous
> node : 9 tests, dont 3 mutations vérifiées rouges sur le code d'avant. Suite complète
> **724 passed, 0 skipped**.
>
> **F14** (poll jamais arrêté) reste ouvert et est requalifié : le `setInterval` doit
> continuer après l'arrêt de l'enregistrement, puisque les jobs d'extraction se terminent
> en fond APRÈS le clic sur Stop — l'arrêter viderait l'aperçu de sa raison d'être. Ce qui
> était réellement dangereux dans F14, c'est la réponse en vol, désormais gardée.

**Preuve** : `VERIFIE-CONSO` = re-vérifié en consolidation par lecture ou exécution directe
(je ne reprends pas un rapport d'agent à mon compte sans le recouper) ; `VERIFIE-AGENT` =
lu par le sous-agent, non recoupé ; `SUPPOSE` = demande une session navigateur ou une
mesure runtime pour être confirmé.

Périmètre lu : `audio_transcribe.py`, `audio_file_jobs.py`, `interview_segment_jobs.py`,
`interview_libre_extract_ai.py`, `ai_common.py`, routes libre/record/segments/retranscription
de `interviews.py`, `db.py`, `main.py` ; `record_libre.html`, `record.html`,
`rec_audio_source.js`, `busy.js`, `autosave.js`, les 11 écrans `libre_*`/`record_*`.

Vérification lancée : `pytest tests/test_libre_enregistrement_direct.py
tests/test_record_segment_jobs.py tests/test_regen_ui.py tests/test_turns_display.py
tests/test_source_audio_distance.py -q` → **90 passed**. Aucun de ces tests n'exécute de
JS : ce sont des assertions de présence de chaînes dans le HTML rendu. La suite complète
est verte (620) — elle ne couvre aucun des constats ci-dessous.

---

## Synthèse

| Sévérité | Backend | Frontend | Total |
| --- | --- | --- | --- |
| BLOQUANT | 2 | 3 | **5** |
| MAJEUR | 8 | 8 | **16** |
| MINEUR | 7 | 6 | **13** |
| | | | **34** |

Répartition après arbitrage de consolidation (B7 dégradé de MAJEUR à MINEUR, cf. plus bas).

**Une cause racine domine et explique 3 constats sur les 5 bloquants/majeurs les plus
graves : un correctif a été posé sur un chemin et pas sur ses chemins frères.** C'est le
mode d'échec déjà consigné en mémoire projet (`apply-the-lesson-to-sibling-paths`). Il se
manifeste ici trois fois : la récupération plafonnée et l'avertissement de perte partielle
(posés le 2026-07-31 sur la retranscription seule, absents du chemin nominal), la garde de
génération d'`uploadBackup` (posée sur `record_libre.html`, absente de `record.html`), et
`renderRepartition` au reset (posé sur `record.html`, absent de `record_libre.html`).

---

## BLOQUANT

| id | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- |
| B1 | Le *reduce* de la synthèse ignore les axes de la mission et vide toute la répartition | `app/services/interview_libre_extract_ai.py:480` | VERIFIE-CONSO | **corrige** (2bf44b7, COMPLET — revue R3) |
| B2 | Une tranche d'extraction en échec disparaît sans avertissement, sans log et sans trace | `app/routers/interviews.py:674` | VERIFIE-CONSO | **corrige** (2bf44b7, COMPLET sur le chemin visé ; réserve = R3-M3) |
| F1 | Aucun timeout sur aucun `fetch` : un upload sans réponse gèle « Enregistrer » à vie | `app/templates/interviews/record_libre.html:950` | VERIFIE-CONSO | **partiel** (2bf44b7 : 2 écrans câblés ; restent R3-M2/M5/M6) |
| F2 | Un 422 non-`no_speech` gèle l'enregistrement, et son rejeu reproduit le même 422 | `app/templates/interviews/record_libre.html:985` | VERIFIE-CONSO | **corrige côté serveur, partiel côté client** (2bf44b7 ; R3-m5/m6) |
| F3 | Sauvegarde audio : une tranche dont l'upload échoue est détruite, puis comptée pour rien | `app/templates/interviews/record_libre.html:1065` | VERIFIE-AGENT | **partiel** (2bf44b7 : « comptée pour rien » traité ; « détruite » subsiste, R3-m4) |

### B1 — le *reduce* ignore les axes
Le *map* a ses variantes dynamiques (`_synthese_system(axes)`, `_synthese_schema(axes)`,
`_synthese_json_hint(axes)`, lignes 396-407). Le *reduce* (ligne 480) est resté sur les
constantes statiques `_REDUCE_SYSTEM` / `_SYNTHESE_SCHEMA` / `_SYNTHESE_JSON_HINT`, câblées
sur les 5 clés historiques, puis relit le résultat avec `repartition_keys(axes)` (ligne 491).
Migration des axes du 2026-07-27 non propagée au *reduce*.

Reproduit en consolidation, faux `call_ai_json` obéissant au hint reçu :

```
axes personnalisés, 1 tronçon  (pas de reduce) -> {'outillage_donnees': 'contenu…', 'gouvernance': 'contenu…'}  0 clé vide
axes personnalisés, 3 tronçons (avec reduce)   -> {'outillage_donnees': '', 'gouvernance': ''}                  2 clés vides / 2
```

Conséquence : mission à axes renommés + entretien de plus de 40 tours (donc tout entretien
réel) → « Régénérer l'analyse » tourne plusieurs minutes et rend une répartition
**intégralement vide**, sans erreur. Elle alimente ensuite la synthèse globale et le deck.
Les 5 axes par défaut ne sont pas touchés : les clés coïncident.
Test : `tests/test_interview_libre.py:417` ne teste le *reduce* qu'avec `axes=None`.

### B2 — perte partielle silencieuse sur le chemin NOMINAL
`recover_stalled_or_failed_jobs` a trois appelants ; seul le chemin libre direct n'a
**aucun** des deux garde-fous :

| appelant | borne de récupération | détection de perte partielle |
| --- | --- | --- |
| `interviews.py:420` (mode paramétré) | absente | **oui** — `still_ko` bloque avec un message actionnable (l.424) |
| `interviews.py:674` (**mode libre direct, nominal**) | **absente** | **aucune** |
| `interviews.py:2280` (retranscription) | **oui** — `[:RECUP_TRANCHES_MAX]` = 3 | **oui** — `attendues > abouties` (l.2287) |

Le plafond et l'avertissement ont été posés le 2026-07-31 (commit `d36aef6`), sur la
retranscription seule. Le commentaire de la ligne 2275 décrit le risque encore vivant en
674 : « 20 × (timeout + relance) dans un seul POST, soit des heures de requête bloquée ».
Puis la ligne 698 `delete_segment_jobs` détruit les jobs porteurs du texte manquant. Aucun
log : `run_segment_job` ne journalise rien sur la branche `_EXTRACT_ERRORS`.
Conséquence : entretien créé `status="done"` avec 15 min de propos manquants au milieu,
sans message, sans trace. Test : `test_interview_segment_jobs.py` couvre « tous les jobs
échouent » (l.552) et « un job échoué est récupéré » (l.502), jamais l'échec **partiel**.

### F1 — aucun timeout de requête
`grep -c "AbortController\|AbortSignal\|\.abort("` = **0** sur `record_libre.html`,
`record.html` et `rec_audio_source.js`. Une promesse jamais réglée ne décrémente ni
`pendingSegments`, ni `pendingBackups`, ni `pendingSegmentJobSubmits` ; `updateSubmitState()`
garde `busy=true` définitivement. Aucun de ces compteurs n'est remis à zéro par `startBtn`
ni `resetBtn` : le gel survit aux sessions suivantes de la même page. Seule sortie offerte à
l'écran : « Recommencer », qui détruit la transcription.

### F2 — le 422 déterministe bloque sans issue
Contrat serveur : `interviews.py:1417` pose `code: "no_speech"` **uniquement** sur
`NoSpeechError` ; `:1419` renvoie un 422 nu pour tout autre `TranscriptionError`.
Client : `record_libre.html:985-988` fait `blocking = !muet`, et `retryOrGiveUp` ne se
déclenche que sur erreur réseau ou `status >= 500` — un 422 ne le déclenche jamais. Le rejeu
manuel renvoie les mêmes octets à la même route : un 422 déterministe (blob de taille 0,
faster-whisper indisponible) se réinjecte en boucle comme bloquant. Aucun bouton
« abandonner ce segment », aucun plafond de tentatives, aucune dérogation.

---

## MAJEUR

| id | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- |
| B3 | Récupération synchrone non bornée à l'arrêt : un POST peut lancer 20+ extractions Ollama en ligne | `app/routers/interviews.py:674` | VERIFIE-CONSO | **partiel** (2bf44b7 : plafonné en libre ; chemin frère `:420` NON plafonné = R3-M1) |
| B4 | L'étape 2 découpe en NOMBRE DE TOURS et ignore `OLLAMA_CHUNK_MAX_WORDS` | `app/services/interview_libre_extract_ai.py:58` | VERIFIE-AGENT | differe |
| B5 | L'appel de fusion n'a aucune borne de taille : `num_ctx` tronque le prompt en silence | `app/services/interview_libre_extract_ai.py:463` | VERIFIE-AGENT | differe |
| B6 | Un redémarrage serveur n'est jamais rattrapé : 45 min à 3 h d'attente muette | `app/main.py:49` | VERIFIE-AGENT | differe |
| B8 | La coercition de type fait DISPARAÎTRE un tour entier au lieu d'aplatir la valeur | `app/services/interview_libre_extract_ai.py:73` | VERIFIE-AGENT | differe |
| B9 | Les blocs sans parole sont retirés du texte retranscrit sans marqueur ni décompte | `app/routers/interviews.py:2256` | VERIFIE-AGENT | differe |
| B10 | Le premier poll complet détruit à la fois la transcription en base et le fichier audio | `app/routers/interviews.py:1599` | VERIFIE-AGENT | differe |
| B11 | Une tranche audio en échec est marquée traitée : la relance ne la rejoue jamais | `app/services/audio_file_jobs.py:238` | VERIFIE-AGENT | differe |
| F4 | La perte du micro en cours d'enregistrement n'est ni détectée ni signalée | `app/static/rec_audio_source.js:247` | VERIFIE-AGENT | differe |
| F5 | Quitter l'écran avec une transcription non enregistrée ne demande aucune confirmation | `app/templates/interviews/record_libre.html:890` | VERIFIE-AGENT | differe |
| F6 | `libre_segment_wait.html` retient la seule copie de l'entretien sans garde-fou de sortie | `app/templates/interviews/libre_segment_wait.html:27` | VERIFIE-AGENT | differe |
| F7 | Une exception au démarrage laisse l'écran mort : ni démarrer, ni arrêter, ni enregistrer | `app/templates/interviews/record_libre.html:1372` | VERIFIE-AGENT | differe |
| F8 | La rotation n'a aucun filet : la transcription peut s'arrêter en silence | `app/templates/interviews/record_libre.html:479` | VERIFIE-AGENT | differe |
| F9 | « Recommencer » ne vide pas la Répartition : tours de l'entretien jeté affichés ET exportables | `app/templates/interviews/record_libre.html:1503` | VERIFIE-CONSO | **corrigé le 2026-09-02** — `renderRepartition([])` dans le gestionnaire, + garde de jeton contre la réponse en vol ; verrouillé par `tests/test_repartition_live.py` (mutations vérifiées) |
| F10 | `record.html` : `uploadBackup` sans garde de génération — l'audio d'un autre interviewé | `app/templates/interviews/record.html:944` | VERIFIE-CONSO | **corrige** (2bf44b7, COMPLET — parité stricte avec `uploadSegment`) |
| F11 | Les alertes de source audio ne sortent jamais de l'onglet, alors que l'entretien est ailleurs | `app/templates/interviews/record_libre.html:551` | VERIFIE-AGENT | differe |

Notes de consolidation :

- **B3 partage la cause racine de B2** (tableau des trois appelants ci-dessus) : un seul
  correctif sur `interviews.py:674` traite les deux, plus le chemin frère `:420` pour la borne.
- **F9 et F10 sont symétriques** : chacun des deux écrans porte une garde que l'autre n'a
  pas. Le nom du compteur de génération diffère (`recGeneration` dans `record.html`,
  `backupGeneration` dans `record_libre.html`), ce qui explique que les relectures croisées
  ne les aient pas rapprochés. Vérifié en consolidation : `record.html:1359` appelle
  `renderRepartition({}, 0, 0)` dans son reset, le reset de `record_libre.html:1503-1533` ne
  l'appelle jamais ; inversement `record_libre.html:1070/1083` porte la garde `gen` dans
  `uploadBackup`, `record.html:944-974` ne l'a pas.
- **F4, F11, B9 forment une même famille** : « l'audio perd une face de l'entretien et rien
  ne le dit ». C'est exactement le cas réel de la mission 16. F11 est le plus retors : le
  mode « à distance » consiste à travailler dans un AUTRE onglet, or l'alerte ne sort jamais
  de l'onglet d'enregistrement (aucun `document.title`, `Notification` ni `visibilitychange`
  dans tout le périmètre).

---

## MINEUR

| id | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- |
| B7 | Deux jobs de même position dupliquent leur contenu dans le tour de table | `app/routers/interviews.py:807` | VERIFIE-CONSO | differe — **dégradé** |
| B12 | Le paramètre `schema` de tous les appels IA est mort : rien ne contraint la réponse | `app/services/ai_common.py:462` | VERIFIE-AGENT | differe |
| B13 | « Abandonner » pendant une retranscription laisse des tranches orphelines | `app/routers/interviews.py:2188` | VERIFIE-AGENT | differe |
| B14 | `_transcribe_parallel` découpe en durée/n_workers, pas en tronçons de 30 s | `app/services/audio_transcribe.py:264` | VERIFIE-AGENT | differe |
| B15 | `interview_segment_jobs.mission_id` ajouté sans REFERENCES : orphelins adoptés | `app/db.py:86` | VERIFIE-AGENT | differe |
| B16 | Une tranche silencieuse déclenche un avertissement que la relance ne lèvera jamais | `app/routers/interviews.py:2287` | VERIFIE-AGENT | differe |
| B17 | SQLite sans WAL avec un job de fond qui commite à chaque bloc et des polls à 3 s | `app/db.py:40` | SUPPOSE | differe |
| F12 | Fenêtre de course à l'arrêt : un enregistreur arrêté par sa rotation échappe au comptage | `app/templates/interviews/record_libre.html:1477` | SUPPOSE | differe |
| F13 | Un `<input type="date">` partiel replié dans `<details>` bloque l'enregistrement sans message | `app/templates/interviews/_identite_block.html:38` | SUPPOSE | differe |
| F14 | L'aperçu Répartition est reconstruit intégralement toutes les 5 s, sans jamais s'arrêter | `app/templates/interviews/record_libre.html:1570` | VERIFIE-AGENT | differe |
| F15 | Gel croisé non réversible des trois formulaires de l'écran de retranscription | `app/templates/interviews/libre_retranscription.html:85` | VERIFIE-AGENT | differe |
| F16 | `libre_detail.html` : le formulaire d'édition n'a aucune garde anti-double-soumission | `app/templates/interviews/libre_detail.html:68` | VERIFIE-AGENT | differe |
| F17 | Une transcription obtenue par import ne peut pas être effacée : « Recommencer » jamais révélé | `app/templates/interviews/record_libre.html:174` | VERIFIE-AGENT | differe |

### B7 — arbitrage de consolidation : MAJEUR → MINEUR
Le sous-agent backend a prouvé par exécution que `merge_segment_turns` sur trois jobs de
positions `[0, 1, 1]` rend `['bloc A', 'bloc B', 'bloc B']`, et a laissé en SUPPOSÉ le fait
que le client puisse resoumettre — hors de son périmètre. Recoupé côté navigateur :
`record_libre.html:393` fait `var pos = segmentJobPosition++` **avant** le `fetch` et ne
rejoue jamais (le commentaire assume explicitement la position sautée) ; la machinerie de
rejeu `SEGMENT_RETRY_DELAYS_MS` / `retryOrGiveUp` appartient à `uploadSegment`, sur une autre
route, qui ne crée aucun job. **Aucun chemin vivant ne produit deux jobs de même position.**
Le défaut reste réel — absence de contrainte d'unicité `(session_token, position)` — mais
latent : à traiter comme durcissement, pas comme incident.

---

## Chemins parcourus et jugés couverts (ne pas re-revoir)

Verrou synchrone de double-démarrage ; double-clic sur Arrêter ; `start()`/`stop()` sur un
enregistreur déjà dans cet état ; mutex `partageEnCours` de `shareTab` ; plafond de la liste
des segments perdus ; positions de tranche et de job minées d'avance ; `coverShift` recalant
les submits en vol ; compteur `lostRetryBlocking` ; garde de génération re-testée au réveil
du `setTimeout` de retry ; héritage de `genArg` ; `fileRetryInFlight` ; plancher
`Math.max(0, …)` ; ordre d'arrivée des segments (sérialisé par le verrou Whisper serveur) ;
`[hidden] { display: none !important }` (`app.css:44`) ; `tojson` en attribut ; `escHtml` ;
attente bloquée côté serveur (rattrapée par `stale → any_failed`) ; 404 terminal du poll de
retranscription.

## Limites de cette revue

1. **Aucun chemin n'a été rejoué dans un navigateur.** Les items `SUPPOSE` demandent une
   session Chrome/Edge réelle (débranchement de périphérique, fenêtre de course à l'arrêt,
   refus de soumission sur `<details>` replié).
2. **Aucun appel Ollama réel.** B1 est reproduit avec un faux `call_ai_json` ; B4 et B5
   s'appuient sur des temps consignés dans le code (mesure du 2026-07-19), non remesurés.
3. **Pas de HTMX dans le parcours d'enregistrement** : `hx-*` n'existe que dans
   `capture.html` / `_saved.html` / `_verbatims.html`, hors périmètre — les cas « swap qui
   détruit un écouteur » ne se matérialisent pas ici, et `autosave.js` y est inerte.
4. **Pas de fonction pause/reprise** dans ces deux écrans : les cas correspondants sont sans
   objet.
5. **Non instrumenté** : comportement réel des timers (`rotationTimer` 60 s, poll 5 s) sous
   le throttling d'onglet en arrière-plan de Chrome — usage pourtant nominal en entretien à
   distance.
6. **Revue projet au sens large écartée** par arbitrage utilisateur au moment de composer le
   plan : pas d'`audit-technique` (robustesse/perf/risque/sécurité transverses), pas de revue
   du commit `1c58e7f`.

---

## Revue du correctif (gate R3 sur `2bf44b7`, 2026-08-31)

Revue adversariale du correctif lui-même (bmad-code-review via sous-agent opus, layers en
séquence — l'outil Agent n'était pas exposé au porteur, couche « aveugle » affaiblie),
POST-commit : `2bf44b7` a été commité/poussé par la session pair pendant la revue, le
checkpoint bloquant du playbook n'a pas pu bloquer. `node --check` OK sur `rec_fetch.js` ;
lecture seule, aucun pytest (suite 630 verte jouée par la session appelante).

**12 constats, 0 bloquant.**

> **Mise à jour — les 12 sont TRAITÉS** (arbitrage utilisateur « traite les 12 R3 »,
> 2026-08-31 soir). Suite complète **640 passed** (0 failed, 0 skipped, 0 error ; +10
> tests), rendu réel des 2 écrans re-vérifié sur serveur frais prouvé (empreinte
> `4df17b5ff12743e9` servie == disque) et contenu SERVI mesuré : 8 `recFetch`,
> **0 `fetch` nu**, politique `recTimeout` présente sur les deux écrans.
> R3-m6 s'est avéré **déjà couvert** par `strip_segment_markers` (le motif
> `⚠ ?\[segment[^\]\n]*\]` prend aussi le marqueur d'abandon) : constat SUPPOSE réfuté
> par lecture, verrouillé par un test de non-régression plutôt que par un correctif.

| id | sév. | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- | --- |
| R3-M1 | MAJEUR | B3 non appliqué au chemin frère : `recover_stalled_or_failed_jobs` sans plafond en mode paramétré (prescrit par la note de consolidation) | `app/routers/interviews.py:420` | VERIFIE-AGENT | **corrige** |
| R3-M2 | MAJEUR | F1 absent des 3 écrans frères du même parcours ; poll en chaîne `setTimeout` qui meurt définitivement sur connexion morte, écran détenant la seule copie de l'entretien ; `test_record_reseau.ECRANS` verrouille le trou | `libre_segment_wait.html:58`, `record_segment_wait.html:55`, `libre_retranscription.html:100` | VERIFIE-AGENT | **corrige** (+ `capture.html`) |
| R3-M3 | MAJEUR | Plafond de récupération = préfixe fixe (les 3 mêmes tranches à chaque envoi) + asymétrie de filtre `a_recuperer` (sans `text.strip()`) vs `still_ko` — blocage possible à l'infini avec message promettant un progrès | `app/routers/interviews.py:690-691` | VERIFIE-AGENT (mécanisme) | **corrige** |
| R3-M4 | MAJEUR | Le clamp `Math.min` sur `sliceEndNow` est un no-op dès qu'un segment est arrivé pendant le vol du POST — sur-comptage de `coveredLen` intact, fenêtre élargie 120× par `NET_TIMEOUT_MS` | `record_libre.html:446`, `record.html:388` | VERIFIE-AGENT | **corrige** |
| R3-M5 | MAJEUR | Le gel n'est pas supprimé, il est ramené à ~45 min (3 tentatives × 900 s, `pendingSegments` tenu), sans affichage d'échéance | `rec_fetch.js` + `record_libre.html:284`, `record.html:260` | VERIFIE-AGENT (arithmétique) | **corrige** |
| R3-M6 | MAJEUR | L'abort client ne coupe pas le travail Whisper serveur et `retryOrGiveUp` renvoie les mêmes octets — emballement possible si transcription plus lente que le temps réel, aucune idempotence serveur | `rec_fetch.js:36-45` + `record_libre.html:1015` | SUPPOSE | **corrige** (plus de rejeu auto) |
| R3-m1 | MINEUR | Les 3 tests qui exécutent réellement `recFetch` sont `skip` sans node — contredit le contrat « un skipped n'est pas un passed » (0f8ca53) en local | `tests/test_record_reseau.py:147` | VERIFIE-AGENT | **corrige** (`pytest.fail`) |
| R3-m2 | MINEUR | Le garde anti-`fetch`-nu exige un littéral mono-ligne : `fetch(url, …)` ou multi-ligne passe sans détection | `tests/test_record_reseau.py:55` | VERIFIE-AGENT | **corrige** |
| R3-m3 | MINEUR | 3ᵉ valeur de `SEGMENT_RETRY_DELAYS_MS` morte (`attempt + 1 < length` = 3 tentatives, indice 2 jamais lu) — recopiée dans les deux `uploadBackup` neufs | `record_libre.html:1017`, `record.html:1044` | VERIFIE-AGENT | **corrige** |
| R3-m4 | MINEUR | `beforeunload` ne couvre ni `pendingBackups` ni `backupPerdus` — fermer l'onglet pendant les ~30 min de reprise de sauvegarde ne demande rien | `record_libre.html:977-982` | VERIFIE-AGENT | **corrige** |
| R3-m5 | MINEUR | `markLostSegmentAbandoned` sort en silence si marqueur introuvable alors que le segment est déjà retiré du bandeau — la perte redevient silencieuse | `record_libre.html:875`, `record.html:811` | VERIFIE-AGENT (atteignabilité SUPPOSE) | **corrige** |
| R3-m6 | MINEUR | Le marqueur `⚠ [segment N abandonné…]` part dans `segment_tail` donc dans le prompt d'extraction — restituable comme tour de parole, non testé | `record_libre.html:873`, `record.html:809` | SUPPOSE | **sans objet** — déjà couvert, test ajouté |

### Ce qui a été fait pour chacun

- **R3-M1 + R3-M3** — helper partagé `_fenetre_recuperation(jobs, deja_abouti)` près de
  `RECUP_TRANCHES_MAX`, appliqué aux **trois** appelants de
  `recover_stalled_or_failed_jobs` (paramétré `:420`, libre `:690`, retranscription
  `:2360`). Il porte les deux garanties : même filtre de matière que `still_ko` (une
  tranche sans texte ne consomme plus un créneau à chaque envoi) et tri
  `(error is not None, position)` — les tranches jamais tentées passent avant les échecs
  déjà constatés, donc plus de préfixe fixe qui affame les tranches 4..N. Limite assumée
  et documentée : quand TOUTES les tranches restantes portent une erreur, la fenêtre
  redevient stable (pas de compteur de tentatives en base) — c'est le cas que couvre la
  porte de sortie « Enregistrer quand même ».
- **R3-M4** — le couple `sliceEnd` figé + `coverShift` est remplacé par **`flightSliceEnd`**,
  frontière de la tranche en vol maintenue en coordonnées COURANTES : toute substitution
  de marqueur avant cette frontière la décale du même delta (que le marqueur soit avant
  `coveredLen` ou DANS la tranche — c'est précisément le cas que l'ancien code ratait),
  et la résolution avance `coveredLen` exactement jusqu'à elle. Remise à `-1` en
  `.finally`. Une seule frontière suffit : la garde de ré-entrance de `submitSegmentJob`
  ne laisse qu'un POST de tranche en vol.
- **R3-M5 + R3-M6** — `rec_fetch.js` étiquette ses rejets de délai (`err.recTimeout = true`,
  testable en programme, contrairement au message traduisible) ; les quatre
  `retryOrGiveUp` prennent un paramètre `timedOut` et **ne relancent plus automatiquement
  sur délai** : le segment part directement au bandeau (relance manuelle ou abandon
  possibles), la tranche de sauvegarde est déclarée perdue. Rejouer les mêmes octets après
  une fenêtre entière aggravait la file d'un serveur déjà noyé — l'abort client n'annule
  pas le travail Whisper en cours — et prolongeait le gel à ~45 min. Les messages
  affichent désormais le rang de tentative (« 2/3 »).
- **R3-M2** — `recFetch` sur les polls de `libre_segment_wait.html`,
  `record_segment_wait.html`, `libre_retranscription.html`, **plus `capture.html`**
  (chemin frère trouvé en appliquant la leçon `apply-the-lesson-to-sibling-paths` :
  transcription de notes, même gel). Leur `.catch` réarmait déjà le `setTimeout` : borner
  la promesse suffit à ramener le silence infini dans le chemin d'erreur ordinaire.
  `test_record_reseau.ECRANS` couvre maintenant les **6** écrans.
- **R3-m1** — `node` absent devient un `pytest.fail` explicite, plus un `skip` : ces trois
  tests sont la seule preuve par EXÉCUTION du helper réseau.
- **R3-m2** — la regex du garde accepte n'importe quel premier argument (`fetch(url, …)`,
  multi-ligne, concaténation) et n'exempte plus que les mentions après `//`.
- **R3-m3** — `SEGMENT_RETRY_DELAYS_MS = [2000, 6000]` avec `attempt < length` : 3
  tentatives, comme avant, mais la lecture ne ment plus.
- **R3-m4** — `beforeunload` des deux écrans couvre `pendingBackups`.
- **R3-m5** — marqueur introuvable : le constat d'abandon est **ajouté en fin de
  transcription** au lieu d'un `return` muet. Au pire une note surnuméraire visible et
  effaçable ; jamais une perte silencieuse.
- **R3-m6** — **réfuté par lecture** : `strip_segment_markers` (`ai_common.py`) matche
  `⚠ ?\[segment[^\]\n]*\]`, donc déjà le marqueur d'abandon, et il est appliqué à l'entrée
  des DEUX extracteurs. Aucun correctif — un test de non-régression fige le comportement
  (`test_strip_segment_markers_retire_le_marqueur_d_abandon`).

**Tests ajoutés (+10, suite à 640)** : plafond du chemin paramétré
(`test_record_plafonne_la_recuperation_synchrone`, échoue sur le code d'avant — 6 appels
au lieu de 3), fenêtre anti-famine et fenêtre ignorant les tranches abouties (unitaires
sur `_fenetre_recuperation`), marqueur d'abandon assaini, politique « pas de relance
automatique sur délai » sur les deux écrans, preuve par exécution node de l'étiquette
`recTimeout` (deux scénarios), et les 4 écrans frères ajoutés au garde anti-`fetch`-nu.

Vérifié et RAS par la même revue : map-reduce axé complet (5 points de contact),
survie des jobs sur le chemin bloquant, contrat corps-de-réponse de `rec_fetch.js`
(prouvé par exécution node à 3 bouchons), parité stricte de la garde de génération
F10, `_parse_repartition` préservant les axes arbitraires.


---

## Revue du correctif R3 (gate R4, 2026-09-01) — AVANT commit cette fois

Le traitement des 12 constats R3 (§ précédent) avait été fait, vérifié et
documenté par la session du 2026-08-31 soir, mais **ni commité ni journalisé** :
16 fichiers retrouvés dans l'arbre de travail à la reprise, signalés par le hook
`arbre_sale()`. La reprise du 2026-09-01 a donc rejoué le gate `dev-verifie` à
mi-course — et cette fois **avant** le commit, contrairement à la revue R3 qui
n'avait pu se tenir que POST-commit.

**État re-vérifié à la reprise, par mesure et non sur parole** : suite complète
**640 passed** (386,92 s, 0 failed / 0 skipped / 0 error) ; contenu SERVI des
2 écrans porteur de 8 `recFetch` et **0 `fetch` nu** ; serveur prouvé frais
(`rec_fetch.js` servi == disque, `b7429909ab2d9f76`) ; les 2 écrans rendus et
regardés ; `docs/wiki*` = churn du scan SessionStart (compteurs et dates).
`test_rec_fetch_charge_sans_defer` confirme que le piège du chargement différé
est verrouillé par un test, pas seulement par un commentaire.

**Fan-out de 2 relecteurs adversariaux en contexte frais** (backend
`bmad-code-review`, frontend `bmad-review-edge-case-hunter`, opus, sur le diff
non commité) → **18 constats, 0 bloquant**. Trois sont des défauts **introduits
par le correctif R3 lui-même** — c'est le cycle que ce gate existe pour casser.

### Arbitré et APPLIQUÉ le 2026-09-01 (arbitrage utilisateur : « les 3 introduits, puis commit »)

| id | sév. | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- | --- |
| N1 | MAJEUR | Le plafond porté au chemin frère sans le message qui le rend tenable : ⌈N/3⌉ pages IDENTIQUES, et la promesse « seules les tranches en échec seront retraitées » devenue littéralement fausse | `app/routers/interviews.py:425` → `:431` | VERIFIE-CONSO (exécuté) | **corrige** |
| N2 | MAJEUR | Corollaire du plafond : `job_error` pris sur `still_ko` resurface l'erreur d'une tranche NON retentée à cet envoi — « Ollama saturé » affiché alors qu'Ollama répond | `app/routers/interviews.py:432` | VERIFIE-AGENT (exécuté) | **corrige** (les DEUX chemins) |
| EC-2 | MAJEUR | `replaceLostMarker` décale `flightSliceEnd` mais ne pose de job que pour `pos < coveredLen` : une parole récupérée DANS la tranche en vol n'entre ni dans un job ni dans le reliquat | `record_libre.html:860`, `record.html:791` | VERIFIE-CONSO (exécuté) | **corrige** |
| EC-4 | MAJEUR | Le test qui fige « pas de relance auto sur délai » cherche `timedOut` à l'échelle du FICHIER : débrancher la politique d'UN des deux `retryOrGiveUp` le laisse vert | `tests/test_record_reseau.py:87` | VERIFIE-AGENT (mutation) | **corrige** |

**Ce qui a été fait.**

- **N1 + N2** — `tentees` nommée sur le chemin paramétré, `recuperees` compté,
  et les deux branches du message portées depuis le chemin libre (progrès
  chiffré « N récupérées, il en reste M sur T » / pas de progrès). `job_error`
  est désormais pris sur `tentees` et non sur `still_ko` — **sur les deux
  chemins** : le libre portait le même défaut, et ne corriger que le chemin
  signalé aurait rejoué à l'identique la leçon `apply-the-lesson-to-sibling-paths`
  que ces constats dénoncent.
- **EC-2** — `dejaCouvert` / `dansTrancheEnVol` calculés AVANT toute mutation
  (un delta négatif ferait repasser `coveredLen` sous `pos`), puis
  `if (dejaCouvert || dansTrancheEnVol) postRecoveredJob(text);` tandis que
  `coveredLen += delta` reste conditionné à `dejaCouvert` seul. Les deux écrans.
- **EC-4** — assertion PAR SITE D'APPEL : extraction du corps de chaque
  `retryOrGiveUp` par comptage d'accolades, commentaires de ligne retirés, puis
  `timedOut` exigé avant le `setTimeout` de relance ; plus une assertion que le
  nombre de `retryOrGiveUp` vaut exactement 2 (un site ajouté ne peut plus
  échapper à la politique).

**Tests ajoutés (+4 cas)** et **tous prouvés discriminants par mutation** —
chaque correctif rétabli à sa version d'avant fait virer au rouge exactement le
test correspondant, et rien d'autre :

```
mutation N1/N2 -> test_record_dit_ou_on_en_est_quand_le_plafond_bloque   FAILED
                  test_record_ne_resurface_pas_une_erreur_perimee         FAILED
mutation EC-2  -> test_la_parole_recuperee_dans_la_tranche_en_vol_part_en_job
                  FAILED sur les DEUX écrans
mutation EC-4  -> test_le_delai_maximal_ne_declenche_pas_de_relance_automatique
                  FAILED sur record_libre.html, VERT sur record.html (seul
                  record_libre était muté) — c'est la sensibilité par site
                  d'appel que l'ancienne version n'avait pas
```

### Re-revue des correctifs R4 — le gate attrape le cycle une fois de plus

Le playbook exige « correctifs appliqués **puis RELUS** — la revue n'a validé que le
code d'avant ». Les 4 correctifs ci-dessus sont donc repartis en revue adversariale
en contexte frais (`bmad-code-review`, opus, harnais node rejouant le VRAI code
extrait des templates). **7 constats — dont deux défauts introduits par les
correctifs eux-mêmes.** C'est la troisième itération consécutive où un correctif
introduit un défaut, et la première où il est attrapé AVANT le commit.

| id | sév. | titre | preuve | statut |
| --- | --- | --- | --- | --- |
| F1 | HAUTE | **EC-2 crée un DOUBLON** : quand le POST de tranche en vol ÉCHOUE, son `.catch` n'avance pas `coveredLen` — la parole récupérée reste AUSSI dans le reliquat et repart une seconde fois. Or c'est le même incident réseau qui a produit le segment perdu : le cas est fréquent | EXÉCUTÉ | **corrige** |
| F2 | MOYENNE | **EC-4 reste contournable** : `assert "err.recTimeout" in contenu` est satisfaite par les 4 lignes de COMMENTAIRE qui le mentionnent — débrancher le câblage des DEUX sites laissait le test vert. La règle « on teste le CODE, pas les commentaires » n'était pas appliquée à cette assertion | EXÉCUTÉ (mutation) | **corrige** |
| F3 | MOYENNE | `postRecoveredJob` fuit `pendingSegmentJobSubmits` à travers un « Recommencer » (= EC-1), et EC-2 y routait un cas nouveau et plus fréquent | EXÉCUTÉ | **corrige** (c79e8b5, avec EC-1) |
| F4 | BASSE | La branche « ça progresse » avale l'erreur FRAÎCHE des tranches qui viennent d'échouer : sur 24 tranches à 1 récupérée par envoi, le levier actionnable (« augmente OLLAMA_TIMEOUT ») n'apparaît jamais | LU | **corrige** |
| F5 | BASSE | Le préfixe « Les tranches déjà réparties sont conservées. » redit la fin de la branche de progrès, et l'affirme au PREMIER envoi alors qu'aucune tranche n'a jamais abouti | EXÉCUTÉ (rendu) | **corrige** |
| F6 | BASSE | `_corps_de_fonction` comptait les accolades AVANT de retirer les commentaires : une accolade en commentaire faussait les bornes → test rouge sur du code correct | EXÉCUTÉ | **corrige** |
| F7 | BASSE | Les assertions structurelles restent satisfiables par du code qui ne tient pas la politique (jeton alternatif, simple mention) | EXÉCUTÉ | **corrige** (partiellement — cf. limite) |

**Ce qui a été fait.**

- **F1** — file d'attente `recuperesEnAttente`. La couverture d'une parole récupérée
  n'est PAS connue au moment de la substitution : elle dépend de l'issue du POST en
  vol. On met donc en attente au lieu de trancher. `.then` (après la garde `gen` et
  après l'avancée de `coveredLen`) → `forEach(postRecoveredJob)` : la parole vient de
  sortir du reliquat, il lui faut son job. `.catch` → rien : elle y reste et s'y fait
  extraire une fois. `.finally` → remise à `[]`. Le cas `dejaCouvert`, dont la
  couverture est ACQUISE, part toujours immédiatement.
- **F2** — assertion de CÂBLAGE ajoutée, sur le texte commentaires retirés :
  `len(re.findall(r"retryOrGiveUp\\([^)]*recTimeout", sans_commentaires)) == 2`. Les
  deux moitiés sont désormais tenues : le câblage (chaque `.catch` étiquette le rejet)
  et l'appelé (chaque `retryOrGiveUp` se BRANCHE sur `timedOut`, une mention ne suffit
  plus).
- **F4 + F5** — `job_error` (pris sur `tentees`, donc frais) calculé pour LES DEUX
  branches, sur les DEUX chemins ; préfixe redondant supprimé.
- **F6** — `_sans_commentaires()` extrait, appliqué AVANT tout comptage d'accolades,
  et épargnant les `://` des URL. `_corps_de_fonction` exige désormais un texte déjà
  nettoyé.

**Re-prouvé par mutation** — les 4 contournements que la re-revue avait démontrés
virent maintenant au rouge :

```
cablage retire des 2 sites d'appel   -> ROUGE sur les 2 ecrans  (restait VERT)
soumission immediate (le doublon)    -> ROUGE sur les 2 ecrans
politique remplacee par une mention  -> ROUGE                   (restait VERT)
branche de progres sans erreur       -> ROUGE
```

Suite complète : **645 passed** (0 failed / 0 skipped / 0 error), contenu servi des
2 écrans re-vérifié (4 occurrences de `recuperesEnAttente`, 2 câblages `recTimeout`,
0 `fetch` nu), écrans re-rendus et regardés.

**Limite assumée (F7).** Les tests de ces deux écrans restent STRUCTURELS : ils figent
le mécanisme, pas le comportement. Le comportement — 0 perte ET 0 doublon sur les deux
issues du POST — n'est prouvé que par le harnais node des relecteurs, pas par la suite.
Le patron `_node` existe pourtant déjà dans `tests/test_record_reseau.py` pour
`rec_fetch.js` : porter ces scénarios en test exécuté est le durcissement à faire, et
c'est la seule chose qui attraperait un futur défaut de logique plutôt que de forme.

### Non arbitré — `differe` (12 constats)

Pré-existants, ou rendus plus atteignables par le correctif R3, ou durcissement.
Aucun n'est appliqué : ils attendent un arbitrage utilisateur.

| id | sév. | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- | --- |
| EC-1 | MAJEUR | `postRecoveredJob` : `.finally` sort AVANT le décrément de `pendingSegmentJobSubmits` si la génération a changé — « Enregistrer » grisé pour la vie de la page, et plus aucun job soumis ensuite | `record_libre.html:926`, `record.html:859` | VERIFIE-CONSO (exécuté) | **corrige** (c79e8b5, 2026-09-01, les DEUX écrans — garde de génération retirée du chemin de décrément) |
| EC-3 | MAJEUR | Sur délai, `uploadSegment` garde le blob (bandeau, relance, abandon) ; `uploadBackup` le JETTE sans issue — sur `record.html`, sans rotation, ce blob est l'audio ENTIER de l'entretien | `record.html:1069`, `record_libre.html:1240` | VERIFIE-CONSO | **corrige** (c79e8b5, 2026-09-01 : blob rendu à l'utilisateur — `offrirBackupEnLocal` côté `record`, `backupPerdusLocaux` côté libre) |
| N3 | MINEUR | La docstring de `_fenetre_recuperation` invoque « Enregistrer quand même » comme mitigation, porte de sortie qui n'existe QUE sur les chemins libres | `app/routers/interviews.py:2045` | VERIFIE-AGENT (exécuté) | differe |
| N4 | MINEUR | Le test du marqueur d'abandon recopie le littéral au lieu de le dériver des templates : une réécriture en `⚠️` (sélecteur de variation) le laisse vert et réinjecte le marqueur dans le prompt | `tests/test_no_speech_et_marqueurs.py:136` | VERIFIE-AGENT | differe |
| N5 | MINEUR | Rien ne verrouille la CONVERGENCE du chemin paramétré (N envois → écran de revue) ni la limite assumée de la fenêtre | `tests/test_record_segment_jobs.py:445` | VERIFIE-AGENT (exécuté) | differe |
| N6 | MINEUR | `deja_abouti` diverge entre les 3 appelants (`status == "done"` / `bool(turns_result)` / `turns_result is not None`) sous une docstring qui annonce des garanties « partagées » | `app/routers/interviews.py:426, 703, 2395` | VERIFIE-AGENT (exécuté) | differe |
| N7 | MINEUR | `j.error is not None` dans le tri, `if j.error` partout ailleurs : un `error=""` est classé « déjà en échec » d'un côté, « sans erreur » de l'autre | `app/routers/interviews.py:2048` | VERIFIE-AGENT (exécuté) | differe |
| N8 | MINEUR | `.text.strip()` est neuf sur le chemin retranscription et la colonne est NULLable au niveau SQLite (ajoutée par `ALTER TABLE`) → `AttributeError` = 500. Non atteignable aujourd'hui | `app/routers/interviews.py:2047` + `app/db.py:79` | VERIFIE-AGENT | differe |
| EC-5 | MINEUR | `pendingBackups` dans `beforeunload` fait apparaître la confirmation de sortie sur « Télécharger le PDF » et sur la porte de sortie, deux boutons non gatés par `updateSubmitState` | `record_libre.html:918`, `record.html:918` | SUPPOSE | differe |
| EC-6 | MINEUR | `record_libre.html` ne rappelle pas `renderRepartition` au reset ET `pollRepartition` sort sur `!sessionToken` : les tours de l'entretien ABANDONNÉ restent affichés et **exportables en PDF** définitivement (= F9, aggravé) | `record_libre.html:1697` | VERIFIE-CONSO | **corrigé le 2026-09-02** (= F9) |
| EC-7 | MINEUR | `flightSliceEnd` n'est remis à `-1` par aucun reset alors que `coveredLen` l'est — l'invariant ne tient que par la garde de ré-entrance, que EC-1 casse précisément | `record_libre.html:1583`, `record.html:1394` | VERIFIE-AGENT | differe |
| EC-8 | MINEUR | Le garde anti-`fetch`-nu laisse passer `window.fetch(` et tout appel précédé d'un `//` littéral sur la même ligne | `tests/test_record_reseau.py:63` | VERIFIE-AGENT (exécuté) | differe |
| EC-9 | MINEUR | La justification écrite du repli de `markLostSegmentAbandoned` est fausse : le textarea est `readonly`, le marqueur ne peut pas être « édité à la main » | `record_libre.html:884` vs `:171` | VERIFIE-AGENT | differe |
| EC-10 | MINEUR | `beforeunload` couvre `pendingBackups` mais pas `pendingSegments` : après « Arrêter », la dernière minute de texte encore en vol part en silence | `record_libre.html:1000`, `record.html:918` | VERIFIE-AGENT | differe |

### Vérifié et RAS par cette revue (ne pas re-revoir)

Câblage du helper aux **3** appelants (grep exhaustif : pas de 4e site d'appel,
aucune récupération inline ailleurs) ; parité ensembliste du filtre de matière
avec `still_ko` sur les deux chemins ; le helper ne mute pas son entrée et son
tri est stable ; cas limites du helper (liste vide, tous aboutis, moins de 3
candidats, tranche sans texte) ; **R3-m6 vrai ET exhaustif** —
`strip_segment_markers` est appelé DANS `extract_turns_from_text` et DANS
`extract_answers_from_text`, donc structurellement inévitable sur les 6 sites
d'appel ; le repli de R3-m5 réinjecte la même variable, donc le même format ;
`ai_common.py` ne porte que des commentaires dans ce diff ; le re-POST
navigateur est possible après `_record_error` (`identity` transporte
`transcript`/`session_token`/`segment_tail`) ; arithmétique R3-m3 = bien
3 tentatives ; équilibre des compteurs sur les 4 branches de délai ;
`flightSliceEnd` — la justification « une seule frontière suffit » est VRAIE sur
les deux écrans, et ses cas limites (marqueur après la frontière, delta négatif,
deux substitutions dans un même vol, POST résolu après reset, rotation) sont
corrects ; les 4 écrans frères réarment bien leur `setTimeout` dans le `.catch` ;
**aucune asymétrie `record.html` / `record_libre.html` introduite par ce diff**.

### Limites de cette revue

1. Aucun chemin rejoué dans un navigateur : EC-5 (boîte `beforeunload` sur un
   POST à réponse `Content-Disposition`) et la fréquence réelle de la fenêtre de
   course d'EC-2 restent SUPPOSÉS. Le mécanisme d'EC-2, lui, est prouvé par
   exécution du JS extrait.
2. Aucun appel Ollama réel : les saturations sont simulées par monkeypatch, les
   temps proviennent des commentaires du code, non remesurés.
3. Le chemin retranscription (`:2392`) n'a pas été exercé de bout en bout —
   N6 et N8 y sont établis par lecture + exécution du helper isolé.
4. Les 27 constats `differe` et 4 `partiels` de la revue initiale (§ plus haut)
   n'ont pas été re-touchés : ils restent ouverts.
