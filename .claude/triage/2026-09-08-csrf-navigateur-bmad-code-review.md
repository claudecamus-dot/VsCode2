# Revue bmad-code-review — 403 CSRF sur les formulaires + couche « test utilisateur » (2026-09-08)

Contexte : signalement utilisateur du 2026-09-08 — 403 « Origine non autorisée »
sur Supprimer, Démarrer et le changement de mode depuis un vrai navigateur, suite
de 760 tests verte. Cause : `Referrer-Policy: no-referrer` fait envoyer
`Origin: null` sur les POST de formulaire, que la garde CSRF refuse. Correctif :
`same-origin` ; journal des refus ; pilote CDP (`tests/navigateur_cdp.py`) et
parcours « premiers clics » (`tests/test_e2e_premiers_clics.py`) ; règle P5.

Revue adversariale jouée sur le diff FIGÉ (P3) par le sous-agent `bmad-revue`,
skill `bmad-code-review`, 4 couches lancées à 20:31 : acceptance-auditor et
edge-case-hunter ont rendu (21:49 / 21:12) ; **blind-hunter et verification-gap
ont été interrompus** (« Request interrupted by user », 21:41) et n'ont rien
rendu. La séance s'est arrêtée avant le triage ; les deux rapports ont été
récupérés depuis les transcripts des sous-agents le soir même (séance
suivante), c'est ce tableau. Une seconde revue sur le diff final (le pilote a
été réécrit depuis) est jouée avant le commit — voir « Reste ouvert ».

| id | sév. | titre | fichier | statut |
| --- | --- | --- | --- | --- |
| F1 | bloquant | La liste blanche du test accepte `no-referrer-when-downgrade` / `origin-when-cross-origin`, qui fuient l'URL (ids de mission et d'entretien) à un tiers | tests/test_csrf.py | corrigé (`== "same-origin"`, raisons en commentaire) |
| F2 | bloquant | Le WARNING CSRF est un amplificateur de journal sans borne (≤16 Kio attaquant par ligne, fichier jamais tourné, écriture synchrone dans la boucle) | app/csrf.py | corrigé (régulateur : 1 ligne / 5 s, refus tus comptés et annoncés, valeurs tronquées à 200 ; tests `test_le_journal_des_refus_est_regule_et_tronque`) |
| F3 | majeur | Le WARNING partait sur un logger sans handler (`logging.lastResort`) : sans niveau ni nom, sur un autre flux que le serveur | app/csrf.py | corrigé (logger `uvicorn.error`) — l'horodatage reste celui du format d'uvicorn (aucun), hors périmètre |
| F4 | bloquant | Navigateur + boucle asyncio fuités si le constructeur échoue après `Popen` | tests/navigateur_cdp.py | corrigé (`try/except BaseException → fermer()`) — et la cause réelle mesurée derrière : le lanceur `msedge.exe` sort en 1,6 s, `kill()` ne tuait jamais le navigateur ; 51 orphelins comptés → `Browser.close`, pid réel via `SystemInfo.getProcessInfo`, arbre, puis par profil |
| F5 | majeur | `websockets.connect` et `ws.close()` sans délai | tests/navigateur_cdp.py | corrigé (`wait_for`) |
| F6 | majeur | `evaluer` rend `None` en silence (`undefined`) | tests/navigateur_cdp.py | corrigé (`RuntimeError` explicite) |
| F7 | mineur | Réponse de `handleJavaScriptDialog` jetée, erreur invisible | tests/navigateur_cdp.py | corrigé (`erreurs_cdp`, asserté par `_sans_erreur`) |
| F8 | non prouvé | Enfants Chromium survivant au `kill()` | tests/navigateur_cdp.py | corrigé — prouvé : renderers, GPU, crashpad survivaient ; fermeture par `Browser.close` + arbre + profil, 0 résiduel mesuré après 3 tests |
| F9 | majeur | `asyncio.sleep(0.3)` « le temps que les scripts démarrent » sur chaque navigation | tests/navigateur_cdp.py | corrigé (les scripts ont tourné avant `load` ; drainage des évènements jusqu'au silence) |
| F10 | majeur | `port_libre()` TOCTOU ; `_cible()` s'attache au premier CDP qui répond | tests/navigateur_cdp.py | corrigé côté navigateur (`--remote-debugging-port=0` + `DevToolsActivePort` du profil : on s'attache à CELUI qu'on a lancé) ; differe côté uvicorn (`port_libre` conservé, un échec de bind est une erreur explicite de la fixture) |
| F11 | mineur | `journal.close()` sautable si `proc.wait` lève | tests/test_e2e_premiers_clics.py | corrigé (`try/finally`) |
| F12 | mineur | « uvicorn n'a pas démarré » rendu pour un serveur qui répond mal | tests/test_e2e_premiers_clics.py | corrigé (3 causes distinguées, `HTTPError` capturé) |
| F13 | mineur | Échec de lancement sans cause (stderr jeté) | tests/navigateur_cdp.py | corrigé (`navigateur.log` dans le profil, état du lanceur dans l'erreur) — c'est ce journal qui a permis de trouver N1 |
| F14 | bloquant | « changement de mode » nommé cassé dans le correctif, sans aucun test ; le parcours « premiers clics » commençait à `/missions/new` | tests/test_e2e_premiers_clics.py | corrigé (`test_choisir_le_mode_puis_demarrer` : `/` → `/mode/reel` → `/entretiens/structure/nouveau` → `/entretiens/libre/nouveau`) |
| F15 | majeur | `_sans_erreur` lit `reponses`/`exceptions` AVANT le drainage : état périmé | tests/test_e2e_premiers_clics.py | corrigé (`drainer()` en tête ; `Network.loadingFailed` comptés aussi) |
| F16 | mineur | « clic RÉEL » surévalué : `element.click()` passe sur un élément invisible ou recouvert | tests/navigateur_cdp.py | corrigé (`Input.dispatchMouseEvent` aux coordonnées, taille non nulle et `elementFromPoint` vérifiés) |
| F17 | majeur | Les deux tests non-e2e sont des tautologies sur la constante | tests/test_csrf.py, tests/test_entetes_securite.py | traite — assumé : le garde durable est le parcours navigateur, désormais obligatoire en CI (E2) |
| E1 | mineur | `E2E_NAVIGATEUR` sur un chemin faux = skip silencieux | tests/navigateur_cdp.py | corrigé (`RuntimeError`) |
| E2 | majeur | La CI saute le module si aucun Chromium n'est trouvé : suite verte sans parcours | .github/workflows/ci.yml | corrigé (`E2E_OBLIGATOIRE=1` → erreur de collecte) — **à vérifier sur le premier run CI après push, non mesuré ici** |
| E3 | mineur | Exclusion favicon par suffixe d'URL (query string) | tests/navigateur_cdp.py | corrigé (`urlsplit().path`) |
| E4 | mineur | uvicorn de test hérite de tout `os.environ` | tests/test_e2e_premiers_clics.py | partiel — `WHISPER_WARM_UP=0` posé (le warm-up chargeait `medium`, ≈1,5 Go, à chaque serveur de test ; interrupteur ajouté dans `audio_transcribe.warm_up`, 3 tests) ; le reste de l'environnement reste hérité (PATH, venv) |
| E5 | mineur | Clic sans navigation = `TimeoutError` nu | tests/navigateur_cdp.py | corrigé (`AssertionError` avec le sélecteur) |
| E6 | mineur | `Page.navigate` avec `errorText` traité comme une page | tests/navigateur_cdp.py | corrigé |
| E7 | mineur | Dialogue ouvert hors d'une lecture de socket (setTimeout, beforeunload) | tests/navigateur_cdp.py | differe — non observé sur ce parcours ; le drainage systématique réduit la fenêtre |
| N1 | bloquant | (hors revue, trouvé à la mise au point) Lancé depuis un processus ÉLEVÉ, Edge se relance dé-élevé via le shell et meurt dès que la session Windows est verrouillée : « CDP injoignable » | tests/navigateur_cdp.py | corrigé (`--do-not-de-elevate`, sans effet hors élévation) |
| N2 | majeur | (hors revue) Profil de plus de ~150 caractères : Edge sort en 1,6 s, journal vide ; `tmp_path` fait 178 caractères sur ce poste | tests/navigateur_cdp.py, tests/test_e2e_premiers_clics.py | corrigé (profil court sous le temporaire système ; garde `_LONGUEUR_MAX_PROFIL` = 140, échec immédiat avec la raison) |

## Seconde revue — sur le diff final, avant commit (2026-09-09)

`bmad-revue` / `bmad-code-review`, 3 couches aveugles cette fois toutes rendues
(blind-hunter, edge-case-hunter, verification-gap ; acceptance-auditor sans
spec). Le relecteur a lui-même rejoué les 21 tests du périmètre (verts) et
**re-prouvé P1** (3 échecs avec `no-referrer` remis par un `sitecustomize` hors
dépôt). Bilan brut : 0 bloquant, 5 majeurs, 11 mineurs (4 non prouvés), 3 rejetés.

| id | sév. | titre | fichier | statut |
| --- | --- | --- | --- | --- |
| A1 | majeur | Le CHEMIN de la requête échappait à `_tronquer` : ligne de 6 082 car. mesurée sur un POST au chemin de 6 000 | app/csrf.py | corrigé (méthode et chemin tronqués ; cas chemin-long ajouté au test du régulateur) |
| A2 | majeur | `warm_up_ollama()` bloquait le serveur e2e jusqu'à `OLLAMA_TIMEOUT` (300 s) avant sa première requête — la leçon `WHISPER_WARM_UP` non appliquée au warm-up frère | app/services/ai_common.py, tests/e2e_serveur.py | corrigé (`OLLAMA_WARM_UP=0`, 3 tests `test_ollama_warm_up.py`, posé par `serveur_uvicorn`) |
| A3 | majeur | `importorskip("websockets")` AVANT la garde `E2E_OBLIGATOIRE` : skip silencieux possible en CI | tests/test_e2e_*.py | corrigé (garde d'abord ; `websockets` absent = erreur sous E2E_OBLIGATOIRE) |
| A4 | majeur | `_tuer_par_profil` matchait son propre powershell (le motif est dans sa ligne de commande) et se tuait au milieu du pipeline | tests/navigateur_cdp.py | corrigé (`$_.ProcessId -ne $PID`) |
| A5 | majeur | 554 lignes de pilote sans aucun test propre : jamais exécuté sur un poste sans Chromium | tests/test_navigateur_cdp.py | corrigé (5 tests sans navigateur : E2E_NAVIGATEUR absent = erreur, profil trop long refusé sans lancement, exemptions favicon des deux listes, nettoyage sur exécutable absent) |
| A6 | mineur | Exemption `_CHEMINS_NAVIGATEUR` appliquée aux 4xx mais pas aux échecs réseau | tests/navigateur_cdp.py | corrigé |
| A7 | mineur | Diagnostic de démarrage d'uvicorn : branche « n'a pas répondu » injoignable (`kill()` posait le code avant le test) | tests/e2e_serveur.py | corrigé (code relevé AVANT `kill()`, `TimeoutExpired` absorbé) — factorisé pour les deux modules e2e |
| A8 | mineur | Profil temporaire fuité si le constructeur du navigateur échoue | tests/test_e2e_*.py | corrigé (`mkdtemp` avant le `try`, `rmtree` dans le `finally`) |
| A9 | mineur | Régulateur global : un flot de refus tiers étouffe le 403 de l'utilisateur | app/csrf.py | ecarte — réguler par clé (origine, chemin) rouvrirait l'amplification (une ligne par chemin varié) ; l'adresse client est ajoutée à la ligne pour distinguer les deux cas |
| A10 | mineur | Sélecteur « Supprimer la mission » commun à 4 formulaires | tests/test_e2e_premiers_clics.py | corrigé (`form[action^='/missions/'][action$='/delete']`) |
| A11 | mineur | `WHISPER_WARM_UP`, `E2E_NAVIGATEUR`, `E2E_OBLIGATOIRE` non documentés | .env.example | corrigé (+ `OLLAMA_WARM_UP`) |
| A12 | mineur | Docstring du régulateur contredite par le test (`reinitialiser()` vs `dernier`) | app/csrf.py | corrigé (docstring : `reinitialiser()` remet les deux à zéro ; l'intervalle s'exerce par l'horloge) |
| A13 | mineur (non prouvé) | `_drainer` sort au 1er silence de 0,2 s : une réponse tardive après la dernière assertion n'est jamais comptée | tests/navigateur_cdp.py | differe — à démontrer par une route lente instrumentée avant de complexifier |
| A14 | mineur (non prouvé) | Test du régulateur dépendant de l'horloge réelle | tests/test_csrf.py | corrigé (horloge injectée par `monkeypatch` sur `csrf.time.monotonic`) |
| A15 | mineur (non prouvé) | `_cible_page` prenait la 1re cible `page` sans regarder laquelle | tests/navigateur_cdp.py | corrigé (l'onglet `about:blank` d'abord) |
| A16 | mineur (non mesuré) | Navigateur sur `ubuntu-latest` affirmé, jamais vérifié ; `E2E_OBLIGATOIRE` = erreur de collecte pour toute la suite | .github/workflows/ci.yml | partiel — étape `google-chrome --version` ajoutée (échec nommé avant pytest) ; le 1er run CI après push reste à regarder |

Rejetés par le relecteur (ne pas ré-instruire) : « `same-origin` régresse la
confidentialité » (assumé, borné par la CSP, documenté) ; TOCTOU `port_libre`
côté uvicorn (déjà différé, F10) ; autres sondes Chromium à exempter (non observé).

## Reste ouvert après ce chantier

- **E2 n'est pas mesuré** : l'e2e obligatoire en CI n'a pas encore tourné sur
  `ubuntu-latest` (Chrome présent sur l'image, `--no-sandbox` posé). Regarder le
  premier run après le push — leçon du 2026-08 : une suite verte en local a
  caché une CI rouge pendant 6 runs.
- **Chrome sous élévation** : `chrome.exe` élevé n'écrit jamais son profil ici
  (lanceur vivant, dossier vide, même avec `--do-not-de-elevate`) ; Edge reste
  le premier candidat sur Windows. Non creusé.
