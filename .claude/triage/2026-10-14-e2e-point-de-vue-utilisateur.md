# Triage — comité e2e « point de vue utilisateur » (2026-10-14)

Arbitrage utilisateur : fermer d'abord 4 trous. Un seul fichier ajouté : `tests/test_e2e_parcours_utilisateur.py` (les correctifs PA et PB modifient ensuite `app/main.py` et `app/static/autosave.js`, voir les sections PA et PB+PC ; le fichier de tests parcours seul est neuf pour le trou 1-4). Preuves par `scripts/preuve_p1.py` (restauration octet pour octet vérifiée).

## Trous fermés

| Id | Trou | Test | Preuve |
| --- | --- | --- | --- |
| 1 | Export + téléchargement du .pptx | `test_exporter_et_telecharger_le_pptx` : clic réel sur « Exporter en PPT », téléchargement par le NAVIGATEUR (`Browser.setDownloadBehavior`), octets `PK`, `Presentation()` ouvre, >= 1 slide. Synthèse semée, aucune IA, photos coupées par `PPTX_NO_PHOTO_FETCH` | P1 tenue : `content=buf.getvalue()` -> `b"MUTANT"` (export.py l.630) |
| 2 | Connexion par cookie | `test_le_cookie_de_session_garde_connecte_sur_plusieurs_ecrans` : sans Bearer, formulaire, cookie `app_session` présent et httpOnly, 4 écrans par clics (liste, mission, fiche, fil d'Ariane), puis cookie effacé -> formulaire sous 401 | P1 tenue : cookie posé sous un autre nom (auth.py l.248) |
| 3 | Fiche supprimée, onglet resté | `test_fiche_supprimee_ailleurs_action_de_l_ancien_onglet`, `test_mission_supprimee_ailleurs_clic_de_l_ancien_onglet` (caractérisation, comportement actuel observé le 2026-10-14, pas forcément voulu) | P1 tenue sur l'entretien : message d'autosave.js l.77 changé. Test « mission » : la mutation du message d'autosave.js n'a pas été jouée sur lui ; après inversion (PA), il a été vu ROUGE par retrait du lien dans `erreur.html` |
| 4 | Entretien guidé : répondre, recharger, relire, terminer | `test_entretien_guide_repondre_recharger_relire_terminer` | P1 tenue : `hx-trigger="never"` sur le textarea (capture.html l.298). Un 1er essai avec un commentaire `{# #}` dans la balise a été « non concluant » (outil), refait avec un marqueur simple |

### Constats REJOUÉS (écart avec la lecture du comité)
- Trou 3 : la lecture du comité (« JSON brut après action ») n'est vraie que pour une NAVIGATION (lien « Terminer », « + Entretien structuré » : l'écran montre `{"detail":"Entretien introuvable."}` / `{"detail":"Mission introuvable."}`, statut 404). Pour l'AUTOSAVE, `app/static/autosave.js` affiche « ⚠ échec de l'enregistrement » : pas silencieux, mais ne dit pas que la fiche n'existe plus ; la saisie reste dans le champ.
- Trou 2 : « renvoie vers /connexion » est inexact : cookie absent, la page protégée rend le formulaire sous 401 à la MÊME URL (pas de redirection).
- Le badge « Répondue » est rendu en majuscules (`text-transform`) : innerText = « RÉPONDUE » ; les assertions comparent sans casse.
- Le diagnostic du 1er test e2e (`test_les_deux_sessions_cdp_sont_sans_keepalive`) a été vert (66 s) ; l'erreur `E.` rapportée n'a pas été reproduite.

## Constats NON traités, consignés sans action (lecture du comité, NON REJOUÉ sauf mention)

### (a) Parcours sans e2e
Créer/éditer/importer une trame ; importer un entretien existant ; dictée ; recommandations ; résumé exécutif / difficultés / verbatims ; exports PDF et Markdown de la fiche ; sauvegarde du brouillon d'enregistrement ; SWOT et synthèse globale en nominal.

### (b) Assertions faibles des e2e existants (`test_e2e_premiers_clics.py`)
- `assert "⚠" in nav.texte()` générique (~l.341, 356, 423, 865).
- `assert "rechargez" not in nav.texte()` possiblement vacuant (~l.453).
- `_attendre_texte` lit `textContent`, qui inclut le texte masqué (~l.261-272) : VÉRIFIÉ à la lecture ; `nav.texte()` = `document.body.innerText` : VÉRIFIÉ (navigateur_cdp.py l.720-721).
- `!!document.getElementById('rec-start')` : présence DOM seulement (~l.173, 208) : VÉRIFIÉ.
- Texte « Missions » trop banal (~l.963) : VÉRIFIÉ (présent dans la barre de navigation).
- Test de capture PNG : teste l'outil, pas l'app (~l.970) : VÉRIFIÉ.
- Enregistrement libre : lit `.value` du textarea et seulement le premier mot (`test_e2e_enregistrement_libre.py` ~l.351) : non rejoué.

### (c) Fidélité du harnais
Base neuve non migrée ; seed par ORM plutôt que par l'UI ; navigateur headless nu (profil vierge, pas de cache ni d'extensions) ; pas de job long réaliste ; `skip` global si aucun navigateur, sauf `E2E_OBLIGATOIRE=1`. Non rejoué.

### (d) États limites non couverts
Session expirée sous HTMX (`app/auth.py` l.199-205 : une requête XHR sans `Accept: text/html` reçoit un JSON 401, l'autosave afficherait « échec » sans dire pourquoi : lecture, non rejoué) ; F5 en pleine transcription ; micro refusé ; double-clic ; état vide ; fichier audio trop gros. Non rejoué.

## Non prouvé
- Suite complète (~30 min) : non rejouée.
- Test « mission supprimée » : mutation d'autosave.js non jouée sur lui ; seule la mutation PA (lien de `erreur.html` retiré) l'a vu rouge.
- Téléchargement : le .pptx est écrit par le navigateur headless sur disque (`Browser.setDownloadBehavior`), pas un second GET.

## PD — assertions renforcées (redacteur-PD, 2026-10-14)

Périmètre : `tests/test_e2e_premiers_clics.py`, `tests/test_e2e_enregistrement_libre.py`. Aucun fichier `app/` modifié durablement (`git diff --stat -- app/` vide après chaque preuve ; restauration « octet pour octet » rapportée par `preuve_p1.py`).

| # | Assertion | Avant -> après | Preuve de rouge |
|---|-----------|----------------|-----------------|
| 1 | échec IA (6 sites : kpis, risques, maturité, garde KPI, garde onglet, SWOT) | `"⚠" in nav.texte()` -> `_assert_echec_ia_rendu` : un seul `.ai-error` VISIBLE, commence par « ⚠ », contient « Ollama est désactivé temporairement » (coupe) ou « Impossible de joindre Ollama sur http://127.0.0.1:9 » (réseau réel), et pas le texte de la garde | Mutant `apercu.html` l.20 (`⚠ {{ error }}` -> `⚠ Erreur`) : vert puis ROUGE sur `test_regenerer_apres_edition_passe_par_l_ecran_de_confirmation_serveur` (l'ancien « ⚠ » seul serait resté vert). Les 5 autres sites partagent le helper : non rejoués un par un. |
| 2 | `"rechargez" not in nav.texte()` | SUPPRIMÉE : « rechargez » n'existe plus sous `app/templates` (grep), lue avant le clic d'onglet donc panneaux masqués : ne pouvait pas échouer. Rien de réel n'était gardé ; la preuve du comportement reste `window.__meme_page` + `bulle` + `jauge`. | sans objet |
| 3 | `_attendre_texte` | `textContent` -> `innerText` (visible) | mutation non jouée. 18 passed : aucun des appels ne dépendait du texte masqué. |
| 4 | `rec-start` (l.173, 208) | présence DOM -> `offsetParent !== null && !disabled` | Mutant `record_libre.html` l.93 `style="display:none"` : ROUGE (`test_parcours_premiers_clics`). Mutant `disabled` : le test PASSE (le JS réactive le bouton avant la lecture) -> la partie `!disabled` n'est pas prouvée par ce test (PREUVE ÉCHOUÉE ; commentaire de `_REC_START_UTILISABLE` corrigé le 2026-10-15). |
| 5 | `"Missions" in nav.texte()` | -> `.page-head h1` visible == « Missions » | Mutant `missions/list.html` l.5 (`<h1>Liste</h1>`) : ROUGE (`test_le_formulaire_de_connexion_connecte`). |
| 6 | libre l.351 | `texte.split()[0] in .value` -> textarea VISIBLE + phrase entière (espaces normalisés) dans `.value` | NON VALIDÉE : le test est rouge en amont (voir ci-dessous), la ligne n'est jamais atteinte ; mutation non jouée. |

Rejeux : `test_e2e_premiers_clics.py` 18 passed, 1 skipped en 629 s. `test_e2e_enregistrement_libre.py` 3 failed en 361 s.

Rouges révélés (pas de correction de l'app) : les 3 tests de `test_e2e_enregistrement_libre.py` échouent à `_attendre(... "répartition Q/R")` (l.249, « toujours rien après 90s », erreurs HTTP [] exceptions []) : rouge attribué à la coupe `OLLAMA-COUPE-TEMPORAIRE` (aucun tour extrait), pour une MAUVAISE raison côté test (il suppose l'IA active) ; non rejoué sur un arbre HEAD vierge, donc attribution non prouvée.

PE : le rejeu `E.` et le blocage de ~50 min (cause inconnue, non reproduit) sont consignés sans correction ; je n'ai observé aucun blocage (le plus long run : 629 s, terminé).

## PD-bis — e2e enregistrement libre sous coupe

- Avec la coupe (`.venv`, repo principal, 2026-10-14) : 3 failed en 348 s, les 3 au même point
  (`_parler_et_attendre_le_tour_de_table`, `_attendre` « répartition Q/R (tours extraits par l'IA) :
  toujours rien après 90s », statut « Enregistrement terminé — prêt à envoyer », erreurs [] exceptions []).
  Pas de CSRF/serveur/navigateur : l'extraction IA est seule absente.
- Sans la coupe (worktree du commit b4cb05a, fichier de test racine copié, conftest ancien, `.venv` principal,
  `app.__file__` = worktree) : 3 passed en 154 s. Cause prouvée par test ; l'assertion renforcée (~l.351,
  phrase entière dans le textarea) est validée par ce vert (transcription faster-whisper réelle).
- Tuples ajoutés à `_TESTS_COUPES_OLLAMA` : les 3 tests de `test_e2e_enregistrement_libre.py` (15 -> 18).
- Ces 3 e2e étaient rouges depuis le commit 69d569b sans être listés : le rapport « 18 passed, 1 skipped »
  du 2026-10-13 ne portait que sur `test_e2e_premiers_clics.py`.
- Reste : le dossier `C:/tmp/sanscoupe_pdbis` n'a pu être supprimé (Permission denied) ; worktree
  désenregistré (`git worktree list` propre), dossier résiduel à supprimer à la main.

## PA — 404 lisible

- **Décision** (utilisateur, 2026-10-14) : handler central ; une NAVIGATION en 404 rend une page lisible, JSON/fetch/HTMX inchangés, un 404 reste un 404.
- **Périmètre** : `app/main.py` (+32 lignes : handler `StarletteHTTPException`, table fermée detail -> message, critère `_veut_du_html` importé de `app/auth.py` sans le modifier), `app/templates/erreur.html` (neuf), `tests/test_erreurs_html.py` (neuf), inversion des 2 tests de caractérisation « supprimée ailleurs » de `tests/test_e2e_parcours_utilisateur.py`. HTML seulement si 404 + pas de `HX-Request` + `Accept` contenant `text/html` ; sinon `http_exception_handler` (même réponse et en-têtes).
- **Preuves (preuve_p1.py, restauration octet pour octet)** : retrait du critère `_veut_du_html` -> rouge (cas json/étoile) ; retrait du critère `HX-Request` -> rouge (cas htmx) ; retrait du lien dans `erreur.html` -> les 2 e2e rouges.
- **Rejoués** : test_erreurs_html + test_route_inventory + test_auth : 38 passed ; 17 fichiers citant 404/detail/Accept + test_csrf : 594 passed, 5 skipped ; e2e parcours : 5 passed.
- **Limites** : 71 HTTPException dans 15 fichiers non toutes rejouées individuellement ; suite complète non lancée ; autres statuts (401, 403, 405, 413, 422, 503) volontairement inchangés ; routes `/api`/JSON non touchées ; un 404 de `/static/**` demandé avec `Accept: text/html` rendait aussi la page (corrigé le 2026-10-15, voir la section finale). Le texte de la page est vérifié en `innerText` (e2e) ; l'apostrophe est échappée `&#39;` dans le HTML source (rendu correct).

## PB+PC — autosave explicite

- **Décisions** (utilisateur, 2026-10-14) : PB, fiche supprimée (404) -> « ⚠ fiche introuvable — rien n'est enregistré. Copiez votre texte avant de quitter. », message PERSISTANT. PC, session expirée (401) -> « ⚠ session expirée — » + vrai lien `<a href="/connexion" target="_blank" rel="noopener">reconnectez-vous</a>`, persistant ; serveur inchangé, pas de HX-Redirect. Autres statuts / réseau : « ⚠ échec de l'enregistrement » inchangé (effacé à 3 s).
- **Périmètre** : `app/static/autosave.js` (statut lu via `evt.detail.xhr.status` ; lien bâti au DOM, pas d'innerHTML ; `showError(evt, message, opts)`), `tests/test_autosave_execution.py` (neuf, exécute le vrai fichier sous Node : 5 tests), 2 e2e ajoutés à `tests/test_e2e_parcours_utilisateur.py`.
- **Accessibilité** : `role="status" aria-live="polite"` posés DEPUIS LE JS (gabarits interdits) sur tous les `.saved` présents au chargement et sur l'enfant `.autosave-err` créé à l'erreur. À confirmer : le slot `#status-{qid}` des réponses de capture est un `.status-ind` (badge qui change souvent) NON rendu live par prudence (annonces bruyantes) ; seul son enfant `.autosave-err`, créé au moment de l'erreur, l'est — une région live créée avec son contenu n'est pas toujours annoncée par les lecteurs d'écran (non testé avec un lecteur). Un attribut dans le gabarit serait plus fiable.
- **Preuves (preuve_p1.py, restauration octet pour octet, harnais Node)** : (1) branche 404 mutée (`statut === 4040`) -> 2 tests rouges ; (2) retrait de `target="_blank"` du lien -> test 401 rouge ; (3) 404 non persistant (`persistant: false`) -> test 404 rouge.
- **Limites** : un 401 sous `fetch` hors autosave htmx n'est pas traité ; durée de session inconnue (information insuffisante) ; le lien ouvre /connexion dans un autre onglet mais la saisie de l'onglet expiré n'est enregistrée qu'après reconnexion ET nouvelle frappe ; 403 CSRF et 5xx gardent le message générique.

## Suite complète du 2026-10-14 — 14 non-verts

Mesuré par la session : `12 failed, 1639 passed, 18 skipped, 2 errors in 35992.25s (9:59:52)`, EXIT=1.
Rejeu de chacun SEUL avec la coupe (basetemp neuf), puis SANS la coupe (worktree `b4cb05a`, tests
inchangés depuis : `git diff --stat b4cb05a -- tests/<fichier>` vide ; `app.__file__` = worktree).

| test | raison avec coupe | vert sans coupe | verdict |
|---|---|---|---|
| test_libre_corrections_revue::test_edition_fiche_libre_signale_une_date_illisible | `role="status"` absent : l'avertissement de date est dans le bloc identité de libre_detail.html (l.48, zone TOUR-DE-TABLE-COUPE) | oui | COUPE (skip posé) |
| test_ollama_integration (4 tests) | `SynthesisAIError : Ollama est désactivé temporairement` (Ollama réel JOIGNABLE ici, donc non skippés par leur garde) | oui, 4 passed en 156 s (réel) | COUPE (skip posé) |
| test_ollama_warm_up::appelle_le_serveur_par_defaut, ::toute_autre_valeur_garde | 0 appel au lieu de 1 (`warm_up_ollama` retourne) | oui | COUPE (skip posé) |
| test_turns_display (4 tests : mêmes_regles, ordre_onglets, exports_pdf, corriger_identite) | onglets/champs/identité du Tour de table absents du HTML | oui | COUPE (skip posé) |
| test_serveur_dev_script::test_stop_purge_le_worker_muet… | PASSE seul (1 passed, 23 s) | non rejoué | AUTRE : non reproduit seul ; hypothèse non prouvée : relances du 8020 / charge pendant la suite |
| test_e2e_premiers_clics (2 ERROR de setup) | PASSENT seuls (70 s et 40 s) | non rejoués | AUTRE : instabilité de setup sous charge, hypothèse non prouvée |

`_TESTS_COUPES_OLLAMA` : 18 -> 29 entrées (11 ajoutées). `--collect-only` : 1671 collectés, aucun UsageError.
Rejeu des 4 fichiers : 31 passed, 11 skipped, 0 failed.

Durée anormale (≈10 h contre 29-49 min attendues) : cause INCONNUE. Pistes non prouvées : tests
Ollama réel/e2e navigateur bloqués sur timeouts, machine chargée ou en veille. La sortie ligne à ligne
n'a pas été analysée par durée (`--durations` non demandé).

Orphelins : processus non inspectés par ce rédacteur (aucun test interrompu). `C:/tmp/sanscoupe_pdbis`
existe encore (non touché). Worktree `C:/tmp/sanscoupe_suite` retiré.

Ouvert : cause des 10 h ; instabilité des 2 ERROR e2e et du test serveur-dev (à rejouer en suite
complète avec `--durations=25` pour les attraper en charge).

## Dernier tour de correctifs (arbitré 2026-10-15)

- **Handler 404 (`app/main.py`)** : garde `isinstance(exc.detail, str)` (un detail dict/liste retombe sur le handler PAR DÉFAUT, JSON 404 ; avant : 500 TypeError) et exclusion `/static/**` (reste JSON). Tests dans `tests/test_erreurs_html.py` (routes de test montées/retirées par fixture, aucune route permanente). `detail=None` devient « Not Found » (str) : page générique, 404. Preuve P1 par `preuve_p1.py` : garde isinstance retirée -> rouge (dict, liste) ; exclusion /static retirée -> rouge. `ruff check app/main.py` : propre après tri des imports.
- **Autosave** : un succès 2xx (`htmx:afterRequest`, `successful`) sans `afterSwap` laissait le message persistant 404/401 (prouvé ROUGE sur le code d'avant dans le harnais Node : 3 échecs). Corrigé par un `WeakSet` des slots persistants effacés au succès de CE slot. Preuves P1 : handler neutralisé -> 3 rouges ; `clearTimeout` retiré -> `test_500_puis_404_meme_slot...` rouge ; délai 3000 -> 5000 -> rouge. e2e navigateur non écrit (succès réel après erreur trop difficile à simuler) : le harnais Node est la seule preuve.
- **Harnais Node** : `querySelectorAll` respecte le sélecteur (leurre), les minuteries enregistrent leur délai (`fin3s` n'exécute que <= 3000 ms), séquence 500 puis 404, statut en chaîne `"404"` (documenté : message générique), cible sans classList / hors slot.
- **Honnêteté** : docstrings/commentaires de caractérisation corrigés (parcours), `_assert_echec_ia_rendu` (assertion vacuante remplacée par l'absence du texte de garde sur la page ; marqueur `OLLAMA-COUPE-TEMPORAIRE` posé), `"Missions"` du parcours renforcé (`.page-head h1` visible), `time.sleep(3.5)` remplacé par un polling, test « non authentifié » documenté (ne prouve que la précédence du middleware), chiffres de CLAUDE.md (29 entrées, durée non remesurée).
- **Non joué** : mutations des assertions e2e renforcées du parcours et de `_assert_echec_ia_rendu` (voir rapport de session pour les rejeux).

## Panne du site 8020 le 2026-10-15 : cause probable, correctifs, ce qui reste non prouvé

**Cause probable (HYPOTHÈSE, non prouvée).** La tâche planifiée `VSCode2-site-8020` relance `serveur-dev.ps1 -Port 8020 -KeepIfFresh` à l'ouverture de session puis toutes les 5 min. Ce mode ne conservait le serveur que s'il répondait ET était frais ; un serveur qui ne répond PAS ENCORE tombait dans « purge et relance ». Trois facteurs combinés :
1. Le démarrage est LONG : `lifespan` d'`app/main.py` (~l.114-163) enchaîne réconciliations de jobs, `audio_transcribe.warm_up()` (préchauffage Whisper) puis `warm_up_ollama()`. Mesuré par la session le 2026-10-15 : worker à 83 s de CPU, 1 Go de RAM, port fermé pendant >= 8 min sous charge. Le health-check de relance n'attendait que 30 s.
2. La décision de purger était prise puis exécutée bien plus tard : instance de 11:31:39, kill à 11:33:33 d'un serveur devenu sain à 11:32:37 (décision périmée).
3. Les preuves de mutation modifiaient `app/` EN PLACE : contenu servi « périmé » + rechargements `--reload` pendant la fenêtre mutée.

**Corrigé (fichiers : `scripts/preuve_p1.py`, `scripts/serveur-dev.ps1`).**
- `preuve_p1.py` : mutation sur une COPIE du dépôt par défaut (exclus : `.venv` 498 Mo, `.git`, `data` 554 Mo [base réelle], `.env` [secrets], `.opencode`, `_bmad*`, caches, `.claude/worktrees` 121 Mo ; gros fichiers >5 Mo en lien dur, dont `tests/exemple` 209 Mo ; gardé : app, tests, scripts, docs, `.claude` hors worktrees). Interpréteur = `.venv` du dépôt, cwd = copie, garde `app.__file__` sous la copie, copie supprimée avec retries. `--en-place` = ancien comportement. `tests/e2e_serveur.py` prend `cwd=parents[1]` = la copie : correct. Aucun chemin absolu codé en dur dans `tests/` (grep). Limite : sans `.git`, un test qui appellerait git sur le dépôt lui-même serait rouge (refus 4, jamais un faux vert).
- `serveur-dev.ps1` (-KeepIfFresh seulement) : (a) racine uvicorn jeune (< 900 s) OU CPU des process qui progresse sur 3 s, port fermé -> `exit 0` « rien purgé » ; (b) périmé mais `app/` modifié il y a < 120 s -> `exit 0` ; (c) attente de démarrage manuel 300 s, échec sans tuer le processus lancé ; (d) mutex `Global\VSCode2-serveur-dev-<port>` (KeepIfFresh : `exit 0` ; manuel : 60 s puis `exit 3`), relâché en `finally` ; (f) re-test juste avant le kill et entre les deux passes ; appels HTTP (`WebRequest` avec délais) et sous-processus python bornés (90 s, tué au-delà).
- Preuves P1 (mode copie) : (a) seuil de grâce neutralisé -> `test_decision_keepiffresh_table` rouge ; (c) `1..30` rétabli -> `test_attente_de_demarrage...` rouge. Preuve du mode copie : app/ inchangé (n, mtime max des sources, `git diff --stat`) avant/après.

**Instances « fantômes » (2 vivantes, 13/10 13:41 et 20:03, NON tuées).** Causes possibles de blocage, toutes dans l'ancien script : `WebClient.DownloadString/DownloadData` sans délai (serveur qui accepte sans répondre), `& $python -c` sans délai (import d'app bloqué sous charge), purge qui attend. Hypothèse, non prouvée : la limite `ExecutionTimeLimit` de la tâche (10 min) n'a pas tué ces powershell enfants du `conhost --headless`. À faire par l'utilisateur (tâche NON modifiée) : vérifier `ExecutionTimeLimit` et le comportement « arrêter la tâche » (la mort de l'hôte ne tue pas l'arbre) ; l'ancien script n'ayant pas de mutex, ces instances ne bloquent pas le nouveau.

**Proposition, non appliquée (hors périmètre, `app/main.py`)** : déplacer `audio_transcribe.warm_up()` et `warm_up_ollama()` du `lifespan` vers une tâche de fond lancée après le démarrage ; le port s'ouvrirait en quelques secondes et toute la fragilité ci-dessus disparaît. 

**Non prouvé** : la boucle purge/relance comme cause unique ; le comportement sous la vraie charge de 8020 (aucun test n'a touché 8020) ; que le délai de 3 s de mesure CPU suffise à détecter un worker lent (jamais vu en réel) ; risque résiduel : un verrou tenu par une instance bloquée fait sortir les suivantes en 0 sans agir (borné par les délais ci-dessus).

## Revue bmad-code-review lots A et C (2026-10-15) — findings OUVERTS, en attente d'arbitrage

Rapport du sous-agent bmad-revue, statut partiel : aucun bloquant, aucune mutation jouée, tout est HYPOTHÈSE sauf mention. `pytest` sur 5 fichiers rapides (erreurs_html, route_inventory, auth, autosave_execution, preuve_p1_copie) : 61 passed. `test_serveur_dev_script` et les e2e non rejoués.

| # | Sév. | Fichier | Finding |
|---|---|---|---|
| 1 | majeur | preuve_p1.py:170,184 | lien dur >5 Mo (tests/exemple) + `chmod` au nettoyage : un test qui écrit une fixture liée modifie l'original ; docstring « jamais écrit » trop forte |
| 2 | majeur | preuve_p1.py ~451 | `fichier_mut.unlink()` (protection du lien dur) sans test bout en bout |
| 3 | majeur | preuve_p1.py ~285-298 | garde `_import_vient_de` jamais exercée (dépôts factices sans `app/__init__.py`) ; `TimeoutExpired` non capturé |
| 4 | majeur | serveur-dev.ps1 | chemin « vieux mais actif » (CPU) non couvert hors fonction pure |
| 5 | mineur | serveur-dev.ps1:600 | commentaire/nom de test disent 120 s, valeur 300 s (CONSTAT) |
| 6 | mineur | test_serveur_dev_script.py | re-test avant kill gardé par `.index()` sur le texte source |
| 7 | mineur | serveur-dev.ps1 | `Kill()` sans arbre, échappement `\"`, `1..0`, `WaitOne` négatif, mutex Global\ non élevé |
| 8 | mineur | app/main.py erreur_http | hx-boost reçoit du JSON ; `exc.headers` perdus sur le chemin HTML ; rendu qui lève => 500 ; `q=0` ignoré |
| 9 | mineur | autosave.js | slots insérés par swap non `live` ; sendError remplace un message persistant ; 2xx d'un autre élément efface « session expirée » ; lien sans retour |
| 10 | mineur | test_erreurs_html.py, erreur.html | `_json_attendu` code mort ; anti-réflexion faible ; « Introuvable » en dur |
| 11 | mineur | serveur-dev.ps1 en-tête | énumération (d) -> (f) ; serveur jeune périmé épargné 900 s |

## Revue des correctifs G1-G4 (2026-10-15) — findings OUVERTS, en attente d'arbitrage

Les 11 findings de la 1re revue sont traités (rapports G1-G4). La relecture des correctifs (bmad-code-review) : aucun bloquant prouvé ; mutations échantillonnées G1 (garde st_nlink), G3 (`q=0`), G4 (garde transitoire/persistant) vues ROUGES. G2 (serveur-dev.ps1) non exécuté par le relecteur. Nouveaux findings, tous HYPOTHÈSES sauf mention :

| # | Sév. | Fichier | Finding |
|---|---|---|---|
| R1 | majeur | autosave.js (afterRequest, `source !== d.elt`) | élément en erreur remplacé par un swap outerHTML : le succès suivant arrive avec un `elt` neuf, le message 404/401 reste affiché à vie (le défaut que le correctif voulait réparer) ; piste : effacer si `source.isConnected` est faux |
| R2 | majeur | preuve_p1.py `_import_vient_de` dans `main()` (CONSTAT) | l'appel n'est couvert par aucun test bout en bout (mini-dépôts sans `app/__init__.py`) : supprimer l'appel laisse la suite verte |
| R3 | majeur | serveur-dev.ps1 `Invoke-RetestAvantKill`, passe 2 | si la passe 1 a tué et que la passe 2 voit un processus jeune respawné, sortie « demarrage » (0) : le serveur peut rester mort |
| R4 | majeur | serveur-dev.ps1 | catch du mutex limité à UnauthorizedAccessException, hors try/finally ; `-GraceDemarrageS`/`-StabiliteS` non bornés par Limiter-Entier |
| R5-R8 | mineur | main.py `_html_accepte` (seule la 1re entrée text/html compte), logger local, `--python` relatif, cible sous dossier exclu, `_interprete_du_depot` non testé, tests par recherche de texte, assertions fragiles | voir rapport |

Information insuffisante : portée du `taskkill /T` de `Invoke-PythonBorne` vis-à-vis du 8020 ; échec `test_check_only…` de G2 attribué à un uvicorn étranger sur 8099 (non vérifié).
Non rejoué à ce stade : 26 fichiers de non-régression de `app/main.py` (expirés à 595 s chez G3), `test_mission_trame_flow` (échec soffice chez G4), `test_serveur_dev_script` complet, e2e navigateur.

## Correctifs R1-R4 (2026-10-16, arbitré « OK applique ») — revue de contrôle

- R1 corrigé (`autosave.js`, garde `source.isConnected !== false`, test 9e rouge avant / vert après, P1 tenue). R2 test de bout en bout ajouté (P1 tenue). R3 `-ApresKill` (P1 tenue). R4 bornes `-GraceDemarrageS`/`-StabiliteS` (P1 tenue) + catch générique du mutex (test textuel seulement ; « mutex hors try/finally » jugé sans effet, non prouvé).
- Rejeu (un seul pytest, basetemp neuf) : erreurs_html, route_inventory, auth, csrf, autosave_execution, serveur_dev_script, preuve_p1_copie, preuve_p1, e2e_parcours, e2e_premiers_clics = **166 passed, 1 skipped en 811 s**. Les 26 fichiers de non-régression de `main.py` : NON rejoués ; suite complète : non rejouée.
- Revue bmad-code-review : statut PARTIEL (2 couches sur 3 non revenues, aucun test rejoué par le relecteur). Aucun bloquant. Findings OUVERTS (hypothèses, en attente d'arbitrage) :
  - S1 moyen, serveur-dev.ps1 ~l.528 : catch générique du mutex peut perdre la trace d'un mutex abandonné si PS enveloppe l'exception (`InnerException`) → message faux « autre instance ».
  - S2 moyen, Resolve-DecisionKeepIfFresh ~l.384 : verdict « demarrage » sans plafond pour un serveur ACTIF port fermé (antérieur à R3).
  - S3 faible : test du mutex et `test_la_passe_2_declare_apres_kill_au_retest` textuels. S4 faible : preuve_p1.py l.214 saute le contrôle d'import sans le dire sans `app/__init__.py`. S5 faible : autosave.js, source `null` si erreur sans `elt` (comportement d'avant).

## Traite tout (2026-10-16) — suite de la revue R1-R4

- Autosave : A (plusieurs sources par slot), C (repli `document.contains`), D (succès sans `elt`) corrigés, tests rouge->vert ; B (succès d'un tiers efface après détachement de la source) = DÉCISION de l'agent, sans test rouge avant, à confirmer par l'utilisateur (inconvénient : le texte non enregistré de l'élément détaché n'est plus signalé). Pas de P1 individuelle pour `test_A_erreur_sans_elt` et `test_B_source_detachee`.
- Scripts : S1 INFIRMÉ (mutex abandonné bien attrapé sous PS 5.1, mesuré) ; message d'ouverture de verrou désormais précis (`$script:erreurVerrou`) + test qui exécute le vrai bloc ; S3, S4 corrigés ; S5 documenté sans correction.
- **S2 ANNULÉ** : le plafond 2×Grace sur « actif » a été relu comme défaut répété (revue de contrôle) : `$AgeServeurS` est l'âge de la racine uvicorn = le reloader sous `--reload`, donc le plafond purgerait le 8020 en plein reload. Retiré, tests d'origine restaurés, commentaire posé. Le risque d'origine (site gelé qui brûle du CPU gardé sans fin) reste ouvert, NON corrigé ; piste : plafonner sur l'âge du worker le plus jeune. Hypothèse de revue, non reproduite.
- Rejeu : 145 passed 1 skipped (e2e navigateur inclus) avant retrait de S2, puis 40 passed (serveur-dev + preuve_p1_copie) après. Suite complète non rejouée.
