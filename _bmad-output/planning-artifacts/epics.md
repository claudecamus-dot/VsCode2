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

**Statut : à faire.**

**Acceptance Criteria [4 critères]:**

- AC2.1.1 — **Given** un enregistrement libre en cours, **When** le serveur rejette `no_speech` le 2e segment consécutif (seuil actuel `NOSPEECH_ALERT_AFTER`), **Then** un bandeau d'alerte rouge, distinct des bandeaux informatifs, s'affiche épinglé en haut de l'écran d'enregistrement (visible sans défiler), annoncé aux lecteurs d'écran (`role="alert"`), avec la durée muette et l'action corrective (bouton de partage de l'onglet en mode distance, comme aujourd'hui).
- AC2.1.2 — **Given** l'alerte affichée, **When** de nouveaux segments restent muets, **Then** la durée affichée se met à jour à chaque segment ; **And** le bandeau ne disparaît que lorsqu'un segment porte de nouveau de la parole.
- AC2.1.3 — **Given** l'alerte affichée et l'onglet de l'app en arrière-plan, **Then** le titre de l'onglet est préfixé par « ⚠ Micro muet — » ; **And** le titre d'origine est restauré au retour de la parole ou à l'arrêt de l'enregistrement.
- AC2.1.4 — **Given** des réponses `no_speech` simulées, **When** on rejoue le parcours dans un vrai navigateur (`tests/test_e2e_premiers_clics.py` ou harnais d'exécution JS), **Then** AC2.1.1 à AC2.1.3 sont vérifiés par exécution réelle, pas par recherche de texte dans le template.
