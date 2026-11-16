# Epics — VSCode2 (Interview-to-Deck)

Amorçage story-obligatoire, 2026-10-28, à compléter avec le PO. Contenu tiré de `README.md`, `CLAUDE.md` et de l'arborescence `tests/` ; aucune fonctionnalité inventée. Tout ce qui est marqué « à confirmer par le PO » est une hypothèse.

## Epic 1 : Socle existant et travaux en cours

Le projet transforme des entretiens (enregistrés ou importés) en un deck PPT de restitution (FastAPI + Jinja2 + HTMX). Livrable principal : l'export `.pptx` de `app/services/pptx_export/`.

### Story 1.1: Garder la suite de tests du livrable verte

En tant que mainteneur du projet,
je veux que la suite pytest passe sur l'arbre courant,
afin de pouvoir modifier `app/` sans casser l'export PPT ni les routes.

**Statut : à vérifier** (la suite compte plus de 1900 tests ; `CLAUDE.md` signale 8 échecs et 3 erreurs au run du 2026-10-20 sur un arbre antérieur, et une durée non expliquée de 2 h 35 ; aucun run sur l'état final). Cette story n'a pas été exécutée ici : « non exécuté ».

**Acceptance Criteria [2 critères]:** (sources : `CLAUDE.md` section Commandes ; à confirmer par le PO)

- AC1.1.1 — **Given** le venv du projet et `APP_DB_PATH` pointant une base de test jetable (jamais `data/app.db`), **When** on lance `.venv/Scripts/python.exe -m pytest -q`, **Then** le code de sortie est 0, sans échec ni erreur. Non exécuté.
- AC1.1.2 — **Given** une édition de `app/X.py`, **When** on rejoue `.venv/Scripts/python.exe -m pytest tests/test_route_inventory.py -q` (qui fige la table méthode/chemin via `app.openapi()`), **Then** il passe. Non exécuté.

### Story 1.2: Protéger l'application par authentification fermée par défaut

En tant que responsable du déploiement,
je veux que toute route non publique rende 503 sans `APP_AUTH_PASSWORD`,
afin qu'aucune donnée d'entretien ne soit servie sans authentification.

**Statut : fait selon `CLAUDE.md`** (depuis le 2026-09-19 ; `app/auth.py`, test `tests/test_auth.py`) ; non revérifié ici.

**Acceptance Criteria [2 critères]:** (sources : `CLAUDE.md`, `tests/test_auth.py`)

- AC1.2.1 — **Given** `APP_AUTH_PASSWORD` absent, **When** on appelle une route autre que `/connexion` ou `/static/**`, **Then** la réponse est 503.
- AC1.2.2 — **Given** des credentials absents, **When** on appelle une route protégée, **Then** la réponse est 401 ; commande : `.venv/Scripts/python.exe -m pytest tests/test_auth.py -q` avec `APP_DB_PATH` posé. Non exécuté.

### Story 1.3: Documenter et lever les anomalies de durée de la suite complète

En tant que mainteneur du projet,
je veux connaître la durée réelle et les échecs de la suite sur l'état final,
afin de disposer d'une base fiable avant de livrer un incrément.

**Statut : ouvert** (`CLAUDE.md` note la durée de 29 min 32 s mesurée le 2026-10-07, puis 9 h 59 min le 2026-10-14 et 2 h 35 le 2026-10-20, causes inconnues, « NON rejouée sur l'état final »). À confirmer par le PO : priorité et propriétaire.

**Acceptance Criteria [2 critères]:** (source : `CLAUDE.md` section Commandes)

- AC1.3.1 — **Given** l'état final de l'arbre, **When** on rejoue la suite complète avec chronométrage, **Then** durée, nombre de tests passés, échecs, erreurs et skips sont consignés dans `CLAUDE.md` avec la date. Non exécuté.
- AC1.3.2 — **Given** les 8 échecs et 3 erreurs signalés au 2026-10-20, **When** la suite est rejouée, **Then** chacun est corrigé, ou documenté comme connu avec sa cause. Non exécuté.

## Epic 2 : Fiabilité de l'enregistrement libre

### Story 2.1: Rendre impossible à manquer l'alerte « aucune parole détectée »

En tant que consultant qui enregistre un entretien libre (souvent à distance, sur Meet ou Teams),
je veux être alerté de façon impossible à manquer quand le micro ne capte plus de parole,
afin de corriger la source audio pendant l'entretien au lieu de découvrir après coup que l'essentiel est muet.

**Origine :** mission 31 « atelier kiro », 2026-11-06 : 10 min de parole captées sur ~104 min enregistrées (diagnostic du 2026-11-16 : le micro n'entendait pas le casque). Le bandeau existant (`record_libre.html`, `noteNoSpeech`) était informatif et seulement visible sur la page. Validée par l'utilisateur le 2026-11-16, **sans signal sonore** (un bip pourrait être entendu en visio et capté par le micro).

**Statut : fait** (fusionné dans main 6e15dfc, validé à l'écran par l'utilisateur le 2026-11-16).

**Acceptance Criteria [4 critères]:**

- AC2.1.1 — **Given** un enregistrement libre en cours, **When** le serveur rejette `no_speech` le 2e segment consécutif (seuil actuel `NOSPEECH_ALERT_AFTER`), **Then** un bandeau d'alerte rouge, distinct des bandeaux informatifs, s'affiche épinglé en haut de l'écran d'enregistrement (visible sans défiler), annoncé aux lecteurs d'écran (`role="alert"`), avec la durée muette et l'action corrective (bouton de partage de l'onglet en mode distance, comme aujourd'hui).
- AC2.1.2 — **Given** l'alerte affichée, **When** de nouveaux segments restent muets, **Then** la durée affichée se met à jour à chaque segment ; **And** le bandeau ne disparaît que lorsqu'un segment porte de nouveau de la parole.
- AC2.1.3 — **Given** l'alerte affichée et l'onglet de l'app en arrière-plan, **Then** le titre de l'onglet est préfixé par « ⚠ Micro muet — » ; **And** le titre d'origine est restauré au retour de la parole ou à l'arrêt de l'enregistrement.
- AC2.1.4 — **Given** des réponses `no_speech` simulées, **When** on rejoue le parcours dans un vrai navigateur (`tests/test_e2e_premiers_clics.py` ou harnais d'exécution JS), **Then** AC2.1.1 à AC2.1.3 sont vérifiés par exécution réelle, pas par recherche de texte dans le template.

### Story 2.2: Transcrire un long audio sans saturer la mémoire ni rester figé

En tant que consultant qui relance la transcription d'un long entretien (retranscription ou import),
je veux que la transcription avance ou échoue avec un message clair,
afin de ne jamais attendre des heures un écran « 0 passage » pendant que la machine sature.

**Origine :** 2026-11-16, retranscription de l'entretien 23 (mission 31, ~105 min) : 8 processus de transcription ont chacun chargé le modèle Whisper medium (~1,6 Go privé chacun) sur une machine déjà chargée ; mémoire pleine, pagination continue, 0 bloc en 45 min, aucune erreur avant la péremption de 3 h. Le modèle est rechargé à chaque bloc d'une minute (`audio_transcribe.py`, `_transcribe_pcm_chunk`), le nombre de processus ne dépend que des cœurs (`MAX_PARALLEL_WORKERS`), et le parent attend chaque résultat sans délai (`futures.pop(index).result()`). Approche validée par l'utilisateur le 2026-11-16.

**Statut : fait** (fusionné dans main fc2dd2f ; AC2.2.4 validé par l'utilisateur le 2026-11-16 : retranscription réelle de l'entretien 23 aboutie).

**Acceptance Criteria [4 critères]:**

- AC2.2.1 — **Given** une transcription parallèle (`_drain_parallel`), **When** un processus de transcription traite plusieurs blocs, **Then** il charge le modèle une seule fois (au démarrage du processus), jamais à chaque bloc.
- AC2.2.2 — **Given** une mémoire disponible insuffisante pour N chargements du modèle, **When** le pool démarre, **Then** le nombre de processus est réduit à ce que la mémoire disponible permet (au moins 1, repli séquentiel si besoin), via une mesure de mémoire injectable pour les tests.
- AC2.2.3 — **Given** un bloc qui ne rend aucun résultat dans un délai borné, **When** le délai expire, **Then** le travail échoue proprement (statut `failed`, message lisible à l'écran proposant la relance) ou se replie, au lieu d'attendre la péremption de 3 h ; **And** le travail signale qu'il est vivant pendant l'attente tant qu'il progresse.
- AC2.2.4 — **Given** les correctifs, **When** on rejoue la retranscription réelle de l'entretien 23 sur le serveur 8020, **Then** elle avance (blocs > 0) sans saturer la mémoire, et l'utilisateur valide le résultat à l'écran.

## Epic 3 : Fiabilité de bout en bout de l'enregistrement et de la transcription

Source : revue approfondie du 2026-11-16, `.claude/triage/2026-11-16-revue-enregistrement-transcription.md` (ids A-n, B-n, C-n, D-n). Lots lancés par l'utilisateur le 2026-11-16 (« lance les travaux de tous ces lots »).

### Story 3.1: Rendre les échecs visibles et la suite de tests diagnostiquable

En tant que mainteneur, je veux que l'application et la suite de tests laissent une trace datée de ce qui se passe, afin de diagnostiquer un job figé, une chute du serveur ou un test bloqué sans deviner.

**Statut : à faire.** Constats : D-1 (=C-5), D-2, D-3, D-4, D-5, D-6, D-7, D-8, D-9, D-12.

- AC3.1.1 — **Given** le serveur démarré, **When** un module `app.*` journalise en INFO ou plus, **Then** la ligne est écrite horodatée (date, niveau, logger, fil) dans un fichier de log de l'app, avec rotation ; `faulthandler` est activé.
- AC3.1.2 — **Given** une suite pytest locale, **When** un test bloque ou échoue, **Then** un délai par test produit une trace (comme en CI) et le nodeid de chaque échec est écrit au fil de l'eau dans un fichier, pas seulement au résumé final.
- AC3.1.3 — **Given** un pytest de plus de 45 min, **When** un 2e pytest démarre, **Then** il ne vole pas la base de test d'un détenteur vivant.
- AC3.1.4 — **Given** un test e2e rouge, **Then** la fin du journal du serveur de test est jointe à l'échec ; le mot de passe de test est lu depuis l'environnement passé au serveur, avec un échec explicite s'il manque.
- AC3.1.5 — **Given** `scripts/serveur-dev.ps1`, **Then** la recherche des descendants est bornée (pas de match par préfixe de PID), la grâce de démarrage est ramenée à une valeur cohérente avec le port ouvert d'emblée, un délai de sonde dépassé donne un état « indéterminé » sans purge, et chaque lancement/mort est tracé horodaté.
- AC3.1.6 — **Given** la CI, **Then** un job Windows limité (hors e2e) exerce les tests du dispositif et du verrou de base.

### Story 3.2: Une transcription longue avance toujours ou échoue clairement, sans hallucinations sur le silence

En tant que consultant, je veux qu'une transcription de fichier ou une retranscription progresse sur une machine chargée, et que le silence ne produise pas de faux texte, afin d'obtenir une transcription fiable sans relances inutiles.

**Statut : à faire.** Constats : C-1, C-2, C-3, C-4, C-6, C-7, C-8, C-9, C-10, C-11, D-10(a).

- AC3.2.1 — **Given** un worker qui charge son modèle lentement, **When** le chargement dépasse la borne d'un bloc, **Then** le délai du bloc ne court qu'une fois le modèle prêt (le chargement a sa propre borne, généreuse), et une relance ne repaie pas un chargement déjà fait quand c'est évitable.
- AC3.2.2 — **Given** le repli séquentiel, le job d'un seul bloc et le repli après casse du pool, **Then** chacun est borné, signale qu'il vit, passe par la garde mémoire, et l'attente du verrou ne compte pas dans le délai du bloc ; un chargement en cours renvoie un « occupé » réessayable plutôt qu'une attente sans fin.
- AC3.2.3 — **Given** l'extraction des tours après transcription, **Then** une exécution supplantée n'écrit rien (contrôle de propriétaire avant chaque écriture et battement).
- AC3.2.4 — **Given** de l'audio silencieux ou quasi silencieux, **Then** les hallucinations connues (phrases types, répétitions, segments à forte probabilité de non-parole) sont écartées, et un bloc qui n'en contient que compte comme « aucune parole ».
- AC3.2.5 — **Given** `TranscriptionMemoireInsuffisanteError` au niveau job, **Then** un test prouve qu'elle n'est jamais avalée comme tranche illisible.

### Story 3.3: Ne jamais perdre la session en cours côté navigateur

En tant que consultant en entretien, je veux qu'aucun clic ni aucune réponse serveur inattendue ne fasse perdre la session en cours, afin de ne pas perdre l'entretien.

**Statut : à faire.** Constats : A-1, A-2, A-3, A-4, A-5, A-6, A-7, A-8, A-9, A-10 à A-19.

- AC3.3.1 — **Given** un enregistrement en cours ou un texte déjà présent, **Then** la reprise d'un brouillon est impossible (bannière masquée au démarrage, gestionnaire gardé).
- AC3.3.2 — **Given** le vrai chemin d'envoi des segments (422 no_speech, relance d'un vieux segment, segment courant, partage perdu), **Then** un test exécuté prouve le comportement de l'alerte sans stub de `resetNoSpeech`.
- AC3.3.3 — **Given** un repartage d'onglet réussi, **Then** l'alerte « aucune parole » reste affichée jusqu'à ce qu'un segment porte de nouveau de la parole (décision par défaut de l'orchestrateur du 2026-11-16, prudente : l'onglet repartagé peut être muet) ; en mode démo l'alerte ne recouvre pas le bandeau démo.
- AC3.3.4 — **Given** un serveur lent, **Then** une seule requête d'aperçu est en vol à la fois ; une réponse 2xx vide est traitée comme « aucune parole » ; 408/429 sont relancés ; les pages d'attente ont une borne de sortie avec export/copie proposés.
- AC3.3.5 — Les défauts mineurs A-10 à A-19 sont corrigés quand ils touchent les mêmes fichiers, chacun avec un test quand c'est un comportement.

### Story 3.4: Garantir l'intégrité des entretiens côté serveur

En tant que consultant, je veux que l'enregistrement, la structuration et les sauvegardes ne perdent ni ne dupliquent rien, et qu'un entretien bloqué affiche pourquoi et comment en sortir.

**Statut : à faire.** Constats : B-1 (avertissement et sortie, la règle de blocage est conservée par arbitrage du 2026-11-16), B-2 à B-14, D-10(b,c), D-11.

- AC3.4.1 — **Given** un entretien libre bloqué `a_transcrire` avec du texte, **Then** un avertissement visible dit pourquoi et propose une sortie (relancer les tranches, ou structurer le texte disponible après confirmation), et la docstring dit vrai.
- AC3.4.2 — **Given** des tranches finies hors ordre, **Then** l'aperçu live n'en saute aucune.
- AC3.4.3 — **Given** un jeton de session long, d'espaces seuls ou un double POST de confirmation, **Then** pas de 404 inattendu, pas d'orphelins, pas de doublon d'entretien.
- AC3.4.4 — **Given** la suppression d'un segment de sauvegarde, **Then** un trou de positions reste détectable ; un fichier d'une mission supprimée n'est jamais rattaché après réutilisation d'id.
- AC3.4.5 — **Given** une exception dans le fil de structuration ou une récupération concurrente d'un job, **Then** pas d'id bloqué « déjà en file », pas d'écrasement d'un statut `done` par `failed` ; relancer une structuration ne détruit pas des corrections manuelles sans avertissement.
- AC3.4.6 — Chaque hypothèse B-5 à B-13 est vérifiée : corrigée avec son test si réelle, sinon documentée comme infirmée.
