# Triage — revue de l'enregistrement libre audio (2026-08-31)

Run d'orchestration `/orchestre` — playbook `revue-design-parallele`, 2 angles en fan-out
(backend `general-purpose` opus, frontend `bmad-revue` → `bmad-review-edge-case-hunter` opus),
puis consolidation en session principale.

**Statut global : aucun correctif appliqué.** Le checkpoint du playbook est explicite — la
revue est le livrable, les correctifs sont un mandat séparé. Tous les constats sont donc
`differe` tant que l'utilisateur n'a pas arbitré ce qui se corrige.

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
| B1 | Le *reduce* de la synthèse ignore les axes de la mission et vide toute la répartition | `app/services/interview_libre_extract_ai.py:480` | VERIFIE-CONSO | differe |
| B2 | Une tranche d'extraction en échec disparaît sans avertissement, sans log et sans trace | `app/routers/interviews.py:674` | VERIFIE-CONSO | differe |
| F1 | Aucun timeout sur aucun `fetch` : un upload sans réponse gèle « Enregistrer » à vie | `app/templates/interviews/record_libre.html:950` | VERIFIE-CONSO | differe |
| F2 | Un 422 non-`no_speech` gèle l'enregistrement, et son rejeu reproduit le même 422 | `app/templates/interviews/record_libre.html:985` | VERIFIE-CONSO | differe |
| F3 | Sauvegarde audio : une tranche dont l'upload échoue est détruite, puis comptée pour rien | `app/templates/interviews/record_libre.html:1065` | VERIFIE-AGENT | differe |

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
| B3 | Récupération synchrone non bornée à l'arrêt : un POST peut lancer 20+ extractions Ollama en ligne | `app/routers/interviews.py:674` | VERIFIE-CONSO | differe |
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
| F9 | « Recommencer » ne vide pas la Répartition : tours de l'entretien jeté affichés ET exportables | `app/templates/interviews/record_libre.html:1503` | VERIFIE-CONSO | differe |
| F10 | `record.html` : `uploadBackup` sans garde de génération — l'audio d'un autre interviewé | `app/templates/interviews/record.html:944` | VERIFIE-CONSO | differe |
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
