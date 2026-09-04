# Revue bmad-code-review — déblocage enregistrement + redécoupage dégressif (2026-09-04)

Contexte : reprise d'un chantier laissé non commité par une session précédente
(demande utilisateur 2026-09-04 : l'extraction IA ne bloque plus l'enregistrement
d'un entretien) + `call_par_troncons_degressifs` (redécoupage sur timeout Ollama).
Revue adversariale lancée avant commit (P3, seuil >5 fichiers produit + JS de
`record_libre.html`). Rapport complet dans la transcription de session du
2026-09-04 (sous-agent `bmad-revue`, 3 couches : Blind Hunter, Edge Case Hunter,
Acceptance Auditor + vérifications propres).

| id | sév. | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- | --- |
| F1 | bloquant | Chemin structuré : extraction rendant `{}` sans lever → revue muette, zéro bandeau | app/routers/interviews.py:414-422,453-461 | reproduit (2 cas HTTP) | corrige (garde commune après le if/else + test `test_record_interview_extraction_vide_sans_exception_signale_quand_meme`) |
| F2 | majeur | Le signal `tranches_manquantes` ne survit pas à un F5 (voyage en query string, jamais persisté) | app/routers/interviews.py:1275,2664 ; missions.py:166 | reproduit (GET avec/sans param) | corrige (arbitrage : persister en base — colonne `Interview.tranches_manquantes`, migration additive `db.py`, tous les points de lecture basculés sur la DB, query string retirée) |
| F3 | majeur | Jobs détruits même en échec structuré → ré-envoi repart en synchrone sur transcription ENTIÈRE ; plus d'export PDF sur l'écran d'arrivée | app/routers/interviews.py:456 ; import_review.html:57-60 | lecture de code | corrige (arbitrage : ne pas supprimer les jobs si échec partiel — `if not manquantes: delete_segment_jobs(...)`, pas de nouvelle UI PDF, scope structuré uniquement comme arbitré) |
| F4 | majeur | Redécoupage dégressif peut multiplier par 3-4 le pire cas (jusqu'à 6-8 appels × 300s) que `RECUP_TRANCHES_MAX` bornait | app/services/ai_common.py:256-299 | mesuré (nb d'appels), durée = hypothèse | corrige (arbitrage : `_PROFONDEUR_REDECOUPAGE_MAX` 2→1, pire cas 3 appels au lieu de 6-8) |
| F5 | majeur | `_finalize_libre_turns` calcule `tranches_manquantes` puis le jette (contexte template incomplet) | app/routers/interviews.py (autour de 765-793) | reproduit (POST record-libre) | corrige (réutilise le bloc `{% if error %}` existant + assertions renforcées sur le test existant) |
| F6 | majeur | Bandeau renvoie vers un bouton non rendu (entretien sans audio) ; aucune route ne ré-extrait les tours depuis `raw_transcript` seul | libre_detail.html:44 ; finaliser.html:21-22 ; _retranscrire_button.html:12 | reproduit | differe (dégradable en mineur — dépend de l'arbitrage F2/F3) |
| F7 | majeur | Compteur trompeur : « 1 tranche » affiché même quand 100% du contenu est perdu (chemin synchrone) | app/routers/interviews.py:421,694 | lecture de code + test existant | differe (décision de wording produit, pas un bug ponctuel — voir arbitrage) |
| F8 | majeur | Entretien à 0 tour casse l'invariant documenté de `_creer_interview_libre` ; bloque le nettoyage groupé des brouillons vides | interviews.py:1216-1218 ; missions.py:29-39 | lecture de code | corrige (arbitrage : `_interview_vide` + `_draft_vide` généralisé, docstring de `_creer_interview_libre` mise à jour) |
| F9 | mineur | Bandeau structuré sans levier actionnable si >3 tranches KO (fenêtre `tentees` ≤3) | interviews.py:438,442 | lecture de code | differe |
| F10 | mineur | `call_par_troncons_degressifs` : découpage impair produit 3 morceaux (pas 2), un résidu d'1 mot part en appel IA complet | ai_common.py:289 | mesuré | corrige (`_split_en_deux_moities` + test `test_troncons_degressifs_sur_compte_impair_rend_deux_moities_pas_trois`) |
| F11 | mineur | `?tranches_manquantes=abc` (ou valeur non entière) rend 422 sur un GET de lecture | interviews.py:2642 ; missions.py:135 | lecture de code | corrige (`_parse_tranches_manquantes` dans les 2 routeurs + tests) |
| F12 | mineur | Drapeau `timeout` réservé à Ollama (OpenAI/Mistral tombent dans le générique, jamais redécoupés) | ai_common.py:537-538 | lecture de code | ecarte (fournisseur par défaut non concerné) |
| F13 | mineur | Timeout pendant une relance qualité perd le `best` déjà obtenu (préexistant) + redécoupage inutile (neuf) | interview_libre_extract_ai.py:262-276 | lecture de code | ecarte (préexistant, hors périmètre) |
| F14 | mineur | Branche `missions/finaliser.html` avec bandeau livrée sans test (P2) | missions/finaliser.html:16-24 | absence de test | corrige (`test_finaliser_affiche_le_bandeau_tranches_manquantes`, couvre aussi F11) |
| F15 | mineur | Deux assertions faibles (passaient déjà sur le code d'avant) + une assertion auto-réalisatrice | tests/test_interview_libre.py:1396-1397 ; test_interview_segment_jobs.py:820 | lecture de test | partiel (assertions renforcées sur test_interview_libre.py:1396-1397 en corrigeant F5 ; l'assertion auto-réalisatrice de test_interview_segment_jobs.py:820 reste — dépend de F6) |
| F16 | mineur | Code mort : paramètres inutilisés de `_extraire_tours_libre` ; import `unescape` inutilisé | interviews.py:648-650 ; test_interview_segment_jobs.py:31 | lecture de code | corrige (signature réduite à 4 paramètres, 2 sites d'appel mis à jour ; import retiré) |
| F17 | mineur | Docstring périmée référençant « Enregistrer quand même » (mécanisme supprimé) | interviews.py:2151-2153 | lecture de code | corrige |
| F18 | decision | Passagers clandestins R2 : .claude/settings.json, .claude/orchestration/log_run.py, docs/wiki.html, docs/wiki/technical/agents-supervision.md modifiés hors périmètre fonctionnel | (racine du diff) | git status | traite (commits séparés — voir plan de commit) |

## État après la passe de correctifs (2026-09-04, même session)

12 findings corrigés (F1, F2, F3, F4, F5, F8, F10, F11, F14, F16, F17 + F15
partiellement) avec tests de régression ajoutés à chaque fois, suite complète
re-vérifiée verte (hors les 12 échecs dispositif préexistants + flakiness
Windows connue de `test_mission_backups.py`, sans rapport). F2/F3/F4/F8 ont
été présentés à l'utilisateur pour arbitrage (AskUserQuestion) avant tout
correctif — appliqués selon l'arbitrage reçu, pas auto-appliqués. F2 a
notamment nécessité une migration additive réelle (`db.py`), vérifiée sur la
vraie base de dev (`data/app.db`, lecture seule, colonne confirmée présente).
F6/F9/F15 (résiduel) restent différés : mineurs, ou dont la cause profonde a
disparu avec la persistance de F2 (le bandeau de `capture.html`/`libre_detail.html`
est maintenant toujours vrai, F6 sur l'absence du bouton « Relancer la
transcription » sans audio reste néanmoins un gap réel sur le chemin
synchrone-sans-audio, non retraité ce soir).

Triage du rapport lui-même : 5 `decision_needed` (F2,F3,F4,F8,F18) — arbitrage
utilisateur requis avant d'agir (portée produit/perf, pas des bugs évidents) ;
11 `patch` (F1,F5,F6,F7,F9,F10,F11,F14,F15,F16,F17) — corrigeables sans arbitrage ;
2 `defer` (F12,F13) — hors périmètre de ce diff.

Décision de cette session : appliquer F1 (bloquant), F5, F7, F10, F11, F14, F16, F17
maintenant (correctifs sûrs et cadrés, Phase B revue-increment) ; présenter F2/F3/F4/F8
à l'utilisateur pour arbitrage (portée produit) ; F6/F9/F15 différés et notés ici plutôt
que traités à la hâte ; F12/F13/F18 traités comme indiqué ci-dessus.
