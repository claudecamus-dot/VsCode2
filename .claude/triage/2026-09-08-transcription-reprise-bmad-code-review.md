# Revue bmad-code-review — transcription : réconciliation, relance, reprise de session (2026-09-08)

Contexte : incident réel du matin (entretien de 2h, mission 19, session
`11af4f97-…`) — 3 tranches figées `running` 5h30, POST de tranche muet, aucune
persistance de la transcription (2h jamais enregistrées), zéro audio de secours.
Quatre correctifs arbitrés par l'utilisateur (D1 réconciliation au démarrage,
D2 relance + visibilité, D3 brouillon local + reprise, D4 instrumentation) et
récupération de la session en base (entretien 17). Revue adversariale jouée sur
le diff FIGÉ avant commit (P3) par le sous-agent `bmad-revue`, skill
`bmad-code-review` (3 couches parallèles : blind-hunter, edge-case-hunter,
verification-gap ; acceptance-auditor écartée faute de spec). Rapport complet
dans la transcription de session du 2026-09-08.

| id | sév. | titre | fichier | statut |
| --- | --- | --- | --- | --- |
| F1 | bloquant | « Démarrer » écrasait le brouillon proposé sans confirmation | record_libre.html (startBtn) | corrigé (confirm explicite si un brouillon `reprise` attend ; test `test_demarrer_n_ecrase_pas_un_brouillon_propose_sans_confirmation`) |
| F2 | majeur | Budget de relance jamais remis à zéro entre deux sessions | record_libre.html + record.html (start/reset) | corrigé (test paramétré sur les 2 écrans) |
| F3 | majeur | Réconciliation sans notion de propriétaire (2 serveurs sur la même base) | interview_segment_jobs.py | traite — accepté et documenté (arbitrage utilisateur : un serveur par base est le modèle ; docstring + trace INFO F11) |
| F4 | majeur | `persistDraft` jette le signal « stockage indisponible » | record_libre.html | corrigé (message unique non bloquant `#rec-draft-status` ; `persistDraft` exécuté sous node avec `sauver` → false) |
| F5 | majeur | Un `done` gardait l'erreur posée par la réconciliation | interview_segment_jobs.py (`run_segment_job`) | corrigé (`job.error = None` au succès) |
| F6 | majeur | Mode « discret » sans règle CSS | app.css | corrigé (`.notice.rec-draft-discret`, test) |
| F7 | majeur | `record.html` gardait le `.catch` muet | record.html | corrigé (arbitrage : porté ; tests paramétrés + exécutés sur les 2 écrans) |
| F8 | majeur | D2/D3 tenus par des présences de chaînes (4 mutations vertes) | tests/ | corrigé (arbitrage : `tests/test_tranches_et_reprise_execution.py` — JS extrait et EXÉCUTÉ sous node, bloc de reprise contre le vrai rec_draft.js ; les 4 mutations font rougir la suite) |
| F9 | mineur | 4e site de mutation du texte sans `persistDraft` | record_libre.html | corrigé |
| F10 | mineur | Sauvegarde audio de 0 octet journalisée comme écrite | interviews.py | corrigé (WARNING dédié) |
| F11 | mineur | Réconciliation sans trace | main.py | corrigé (INFO avec le nombre) |
| F12 | mineur | `job.error` posé par D1 place la tranche en fin de fenêtre de récupération | interviews.py `_fenetre_recuperation` | **écarté** — sans effet pratique après un redémarrage (lot homogène), documenté par la revue |
| F13 | mineur | Tests non hermétiques (base partagée à chemin fixe) | tests | corrigé (mission dédiée ; commentaire sur `>= 2`) — et cause réelle des 35 erreurs de la suite : `engine.dispose()` ajouté au `teardown_module` |
| F14 | mineur | Étage node en `skip` silencieux si node absent | tests | corrigé (échec explicite sous `CI`/`GITHUB_ACTIONS`) |
| F15 | mineur | `nextBackupPosition` non restauré | record_libre.html | corrigé |
| F16 | mineur | Journal borné par éviction des plus anciens | rec_draft.js | corrigé (20 premiers + 40 derniers) |
| F17 | mineur | En-tête pointant un fichier de test inexistant | rec_draft.js | corrigé |
| D-A | décision | Propos conservés 7 jours en local après envoi réussi | rec_draft.js | traite — arbitré : conservé (mode discret + « Ignorer » pour purger) |

## Reste ouvert après ce chantier

- **D4 — cause du zéro audio de secours en 2h : non déterminée.** Le journal
  serveur du matin était tronqué par un redémarrage (le lanceur écrasait le
  fichier — corrigé : rotation `.prev` dans `scripts/serveur-dev.ps1`) et l'état
  client est mort avec l'onglet (corrigé : journal d'évènements
  `backup-rotation/envoi/ok/ko` dans le brouillon local, trace INFO/WARNING
  serveur). La prochaine occurrence sera diagnosticable ; celle-ci ne l'est plus.
- **Tours de l'entretien 17 récupéré** : le début est en double (tranches 0, 4 et
  5 se recouvraient, chacune structurée par l'IA) — à nettoyer dans l'onglet
  Répartition, sur demande.
