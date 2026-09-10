# Revue bmad-code-review — audit hub (bornes/rollback/garde atomique) + correctif de course get_or_create_* (2026-09-09)

Contexte : le hub de supervision (VScode5) a poussé 4 commits sur VSCode2
(`d9a66f7`, `47c526e`, `2b44b71`, `49529c5`) traitant l'audit-technique du
2026-09-04 (troisième borne anti-zip-bomb, plafond RAM sur 2 routes audio,
`db.rollback()` dans 2 tâches de fond, garde atomique anti-double-lancement de
la synthèse globale) — sans P3 (revue avant commit, un sous-agent ne peut pas
la jouer sur son propre travail) ni P5 complète (les tests de bout en bout
démarrent leur propre uvicorn, interdit à un sous-agent). En reprenant la main
sur `app/`, cette session a trouvé et corrigé une course supplémentaire
laissée hors périmètre par le hub (`get_or_create_global_synthesis`/`swot`/
`executive_summary`), puis fait jouer `bmad-code-review` (4 couches aveugles,
`bmad-revue`) sur les deux périmètres — le diff déjà commité ET le correctif
non commité.

Suite complète rejouée avant la revue : 923 passed, 0 failed, 0 skipped
(navigateur réel compris — P5 satisfaite sur le chemin nominal des 2 routes
audio et de la synthèse globale, pas sur le rejeu d'un 413 ni d'un double-clic).

| id | sév. | titre | fichier:ligne | statut |
| --- | --- | --- | --- | --- |
| F1 | bloquant | `MAX_ZIP_ENTREES` contournable en falsifiant 4 octets de l'EOCD (le compte déclaré, pas `size_cd`, que `zipfile` lit réellement) | app/uploads.py:176-198 | corrigé (borne sur `size_cd // 46`) |
| F2 | bloquant | La garde atomique de `generate_global` est défaite par l'acquittement d'erreur (écriture ORM inconditionnelle `idle` + commit, AVANT l'UPDATE conditionnel) — double-clic après échec relance deux jobs | app/routers/synthese.py:197-210 | corrigé (acquittement fusionné dans l'UPDATE conditionnel) |
| F3 | majeur | `db.begin_nested()` COMMIT la 1ère écriture d'une session SQLite (pysqlite en mode autocommit implicite, pas de listener `connect`/`begin`) — le correctif de course transforme un `db.add()` révocable en écriture durable, y compris depuis un GET | app/services/synthese_ecriture.py:66-68 ; app/db.py (absence de listener) | corrigé — PAS par les listeners `connect`/`begin` d'abord posés : vérifiés isolément OK, mais mesurés ENSUITE comme dangereux à l'échelle app (toute session qui ne fait QUE LIRE garde une transaction ouverte jusqu'à son commit — a fait échouer `test_synthese_globale_concurrence.py` par `database is locked`, un test préexistant sans rapport). `db.py` restauré à l'original ; `_get_or_create_1_1` réécrit sur `INSERT … ON CONFLICT DO NOTHING` (upsert natif SQLite), qui ne requiert ni SAVEPOINT ni changement de app/db.py |
| F4 | majeur | `except IntegrityError` suppose la course d'unicité ; toute autre violation (FK sur mission supprimée) prend le même chemin, relit `None`, l'appelant lève `AttributeError` — 500 trompeur | app/services/synthese_ecriture.py:69-75 | corrigé — devenu sans objet avec le pivot F3 : l'upsert ne lève jamais d'`IntegrityError` pour le cas courant (`ON CONFLICT DO NOTHING`), donc plus de `except` qui suppose quoi que ce soit ; une vraie violation d'intégrité ailleurs continue de lever normalement, à l'INSERT lui-même |
| F5 | majeur | 413 audio incohérent avec l'écran : `capture.html` détruit le blob sans recours ; `record*.html` le classe bloquant alors qu'une relance renverra le même 413 | app/templates/interviews/capture.html:146-150 ; record.html:1146-1161 ; record_libre.html:1284-1305 | differe — JS + parcours navigateur, hors du lot bloquant du jour ; à traiter avec F16 |
| F6 | mineur | `db.flush()` de la prise de jeton devenu inerte, commentaire décrit un code disparu | app/routers/synthese.py:237-243 | corrigé (commentaire réécrit) |
| F7 | mineur | Test de course : rien n'assure que le concurrent est réellement parti ; assertion tautologique | tests/test_synthese_ecriture_concurrence.py:150-165 | corrigé (`assert concurrent_lance[0]`, assertion tautologique retirée) |
| F8 | mineur | `_T` non lié, `fabrique` non annotée | app/services/synthese_ecriture.py:36,55 | corrigé |
| F9 | mineur | `MAX_ZIP_ENTREES` seule borne sans surcharge d'environnement | app/uploads.py:87 | corrigé (`_entier_env`) |
| F10 | mineur | Test du plafond audio couple deux réglages indépendants (`>` MAX_UPLOAD_BYTES) | tests/test_uploads_bornes.py | corrigé (assertion contre l'artefact mesuré) |
| F11 | mineur | Branche zip64 de `_entrees_declarees` jamais exercée (tests s'arrêtent à 20 000, sous la sentinelle 0xFFFF) | app/uploads.py:192-198 | differe — effort de forger une archive zip64 non trivial, branche sûre par construction (repli au-dessus du plafond) |
| F12 | mineur | Entrée d'arbitrage `VSCode2:audit-2026-09-04` factuellement fausse sur 2 points déjà fermés (`app/main.py:87,90`) ; audit local `.claude/audits/VSCode2.json` non resynchronisé (daté 2026-09-03) | .claude/supervision/arbitrages.json ; .claude/audits/VSCode2.json | traite — signalé au hub par message (2026-09-09), jamais édité localement (canon hub) |
| F13 | mineur | Clôture partielle : 2 routes audio en écriture streaming disque restent sans plafond, non authentifiées | app/routers/interviews.py:1671,1961 | ecarte — hors périmètre assumé (`app/uploads.py:14-18`), signalé au hub |
| F14 | mineur | `_get_or_create_answer` porte le même lire-puis-écrire que la course corrigée, sur la table la plus écrite (autosave 2 onglets) | app/routers/interviews.py:141-151 | differe — signalé au hub, hors périmètre du jour |
| F15 | mineur | `db.refresh(global_synthesis)` probablement redondant (UPDATE ORM-enabled synchronise déjà l'attribut) | app/routers/synthese.py:253-257 | ecarte — non prouvé par le relecteur, gain marginal, laissé en ceinture-et-bretelles |
| F16 | mineur | Commit `47c526e` non scopé (4 sujets) ; P5 non rejouée sur les 3 routes POST touchées | commit 47c526e | traite — code déjà poussé, rien à défaire ; le clic 413 en navigateur reste à faire (cf. F5 différé) |

## Second passage de revue — sur les correctifs eux-mêmes (2026-09-09)

Les correctifs de F1/F2 et le pivot de F3 n'avaient été revus par personne :
`bmad-revue` / `bmad-code-review` rejoué dessus, 4 couches. Il a trouvé une
faute de PROCESSUS grave et un contournement complet de mon propre correctif.

| id | sév. | titre | fichier:ligne | statut |
| --- | --- | --- | --- | --- |
| B1 | bloquant | **Le correctif F2 n'était pas dans l'arbre** : `app/routers/synthese.py` était byte-identique à HEAD. Restauré depuis HEAD pour prouver P1 sur un test, jamais remis — et tout ce qui a suivi a donc été testé contre le code NON corrigé | app/routers/synthese.py | corrigé (fichier restauré depuis la copie de travail, vérifié : `_acquitter_erreur` présent, 40 définitions vs 39 à HEAD, +34 lignes) |
| B2 | bloquant | Conséquence de B1 : la course F2 était toujours exploitable, double lancement reproduit par le relecteur | app/routers/synthese.py | corrigé avec B1 — 9 tests de concurrence verts après restauration |
| B3 | bloquant | Conséquence de B1 : le test de non-régression F2 passait sur le code non corrigé (le relecteur l'a mesuré contre l'arbre réel, pas contre le correctif) | tests/test_synthese_globale_concurrence.py | corrigé avec B1 — le point d'entrelacement (`_total_answer_count`) précède bien la seule écriture du chemin testé, vérifié sur le code restauré |
| B4 | bloquant | **Fantôme zip64** : la garde ne consultait le zip64 que sur SENTINELLE, alors que `zipfile._EndRecData64` suit le locator dès qu'il est présent. Archive à locator+EOCD64 valides + fin 32 bits menteuse SANS sentinelle → garde lit `(1, 46)`, `zipfile` indexe 20 000 entrées | app/uploads.py:231 | corrigé (zip64 consulté dès qu'un locator valide précède, `max()` des deux jeux de valeurs ; reproduit avant/après) |
| M1 | majeur | Le correctif F1 ne shippait AUCUN test de régression (P1) — `_zip_innombrable()` est honnête et passait déjà avec l'ancienne borne | tests/test_uploads_bornes.py | corrigé (3 tests : compte falsifié, fantôme zip64, zip64 légitime — les 3 rouges sur HEAD, le fantôme rouge aussi sur la version pré-B4) |
| M2 | majeur | Trois clôtures fausses dans ce fichier de triage (F2/F6/F7 marqués corrigés alors que le code ne l'était pas) | ce fichier | corrigé (ce tableau ; les statuts ci-dessus ont été re-vérifiés dans l'arbre, pas déclarés) |
| m1 | moyen | Message d'erreur inversé : `size_cd // 46` est un MAJORANT, le message disait « contient au moins N » | app/uploads.py:274 | corrigé (« peut indexer jusqu'à N ») |
| m3 | moyen | `assert MAX_ZIP_ENTREES >= 4000` rougissait dès qu'on utilisait la surcharge d'environnement que F9 venait d'ajouter | tests/test_uploads_bornes.py:189 | corrigé (assertion retirée ; les 2 vrais `.pptx` restent la garde) |
| m4 | moyen | Le `db.commit()` du helper change le contrat transactionnel (un `rollback()` postérieur ne défait plus la création), docstring muette | app/services/synthese_ecriture.py:82 | corrigé (contrat documenté ; les 10 sites d'appel vérifiés un par un par le relecteur : aucun n'a d'écriture en attente avant l'appel) |
| m5 | moyen | Le 3e test du nouveau fichier n'exerce pas la course de création qu'il annonçait | tests/test_synthese_ecriture_concurrence.py | corrigé (renommé et redocumenté en test de fumée « ni 500 ni doublon », avec ce qu'il ne prouve pas) |
| m7 | mineur | Assertion tautologique F7 toujours présente | tests/test_synthese_ecriture_concurrence.py:171 | corrigé (retirée) |
| m8 | mineur | Le test audio calculait sa marge sur le 2e artefact, pas le plus gros cité par sa docstring | tests/test_uploads_bornes.py | corrigé (27,64 Mio, le plus gros mesuré) |
| m9 | mineur | La suppression de `MAX_AUDIO > MAX_UPLOAD` (F10) laissait l'inversion des deux plafonds sans garde | tests/test_uploads_bornes.py | corrigé (invariant porté sur les DÉFAUTS via `_mo_env`, sans recoupler les valeurs courantes) |
| m10 | mineur | `_entier_env` et la surcharge F9 n'étaient exercés par aucun test | app/uploads.py:76-83 | corrigé (5 cas paramétrés : valide, illisible, 0, négatif, absent) |
| m2 | moyen | Calibration périmée : le plafond effectif est ~1700-2400 entrées réelles (noms longs), pas 4000 ; marge réelle ×5,7 et non ×10 sur le vrai template client | app/uploads.py:107-113 | differe — le chiffre du plafond reste sûr et surchargeable ; le commentaire de calibration mérite une réécriture au prochain passage |
| m6 | mineur | `db.flush()` inerte + commentaire décrivant un code disparu | app/routers/synthese.py:237-243 | corrigé (retiré et commentaire réécrit — c'était le F6 annoncé, effectivement perdu avec B1 puis restauré) |
| h1 | mineur (hypothèse) | Le helper peut rendre `None` si la mission est supprimée entre le commit et la relecture, sous une annotation `-> T` | app/services/synthese_ecriture.py:87-88 | differe — non reproduit ; l'ancien code avait la même forme d'échec |
| h2 | mineur | `mission.id is None` lève désormais `IntegrityError NOT NULL` là où l'ancien `db.add()` laissait SQLAlchemy résoudre la FK au flush | app/services/synthese_ecriture.py:77-81 | ecarte — aucun appelant ne passe une mission non flushée (les 10 vérifiés) |

## Reste ouvert après ce chantier

- **F5/F16** : le 413 audio n'est vérifié qu'au niveau route (TestClient), jamais
  en navigateur réel ni côté traitement JS du blob perdu. Un prochain chantier
  doit : classer le 413 non-bloquant (ou proposer un téléchargement local sur
  `capture.html`), et étendre `tests/test_e2e_premiers_clics.py` ou un module
  dédié avec un clip audio dépassant `MAX_AUDIO_UPLOAD_BYTES` abaissé pour le test.
- **F13/F14** : deux races/gardes manquantes de la même famille que celles
  corrigées aujourd'hui, sur des chemins différents (écriture disque non
  plafonnée ; autosave de réponse). Signalées au hub, non arbitrées.
- **m2** : réécrire le commentaire de calibration de `MAX_ZIP_ENTREES` sur la
  grandeur réellement appliquée (`size_cd // 46`, pas le compte d'entrées), et
  ajouter un test « gros document légitime à chemins longs passe ».
- **La leçon de B1**, qui vaut au-delà de ce chantier : restaurer un fichier à
  sa version HEAD pour prouver un P1 crée une fenêtre où tout ce qu'on teste
  ensuite porte sur le code d'AVANT. Le geste doit être immédiatement suivi de
  sa restauration, et le `git diff --stat` du périmètre relu AVANT de conclure
  quoi que ce soit — c'est une revue extérieure qui l'a vu, pas moi.
