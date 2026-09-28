---
title: Restitution défendable — spec de cadrage (BROUILLON)
status: brouillon — rien n'est engagé, arbitrage utilisateur attendu
date: 2026-09-28
methode: bmad-spec (mode headless/express) — écart assumé : fichier unique au lieu du dossier SPEC.md + .memlog.md, seule écriture autorisée par le brief
sources:
  - app/services/synthese_ai.py
  - app/services/analyse_import.py
  - app/models.py
  - app/services/pptx_export/slides_trajectoire.py
  - tests/test_mission_trame_flow.py (_FILLED_ANALYSIS)
---

# Restitution défendable — spec de cadrage (BROUILLON)

## ARBITRAGE UTILISATEUR — 2026-09-28

**I1 et I3 : acceptés tels que spécifiés ci-dessous.**

**I2 : redéfini par l'utilisateur.** Texte de l'arbitrage : « les recommandations
proviennent de la synthèse de l'agrégation de toutes les interviews faites pour
lesquelles on aura trouvé un **consensus** ou des **écarts importants** permettant de
proposer des améliorations ».

Ce que cela change par rapport au premier cadrage :

- **L'unité de provenance n'est plus l'entretien, c'est le CONSTAT** — qualifié
  `consensus` ou `écart`, issu de l'agrégation de TOUS les entretiens. La chaîne
  devient `reco → constat(s) → entretiens qui le portent`, au lieu de
  `reco → entretiens`. **A1 est donc tranché par une option (d)** que la table
  ci-dessous ne proposait pas.
- **Les convergences/divergences cessent d'être du code mort optionnel** : elles
  sont le cœur de I2. **A3 passe de « laisser en sommeil » à « instruire dans
  I2 »** — soit en rebranchant `generate_theme_synthesis` et la table `syntheses`,
  soit en les remplaçant par le modèle de constat ci-dessous (à trancher à
  l'implémentation, sur pièces).
- **I1 devient un prérequis de I2, non un incrément indépendant** : qualifier un
  consensus suppose de savoir combien d'interviewés portent le constat. L'ordre de
  livraison est donc I1 → I2 → I3.

**Reste à arbitrer sur I2 redéfini** (trois questions nouvelles, non couvertes par
A1-A9) :

| # | Question | Options | Recommandation |
|---|---|---|---|
| B1 | Où vivent les constats | (a) nouvelle table `mission_constats` ; (b) réanimer `syntheses` (convergences/divergences, par thème) | (a) : `syntheses` est par thème, or un constat transverse à la mission n'a pas de thème. Réanimer une table morte au mauvais grain coûte plus qu'une table neuve |
| B2 | Qui qualifie consensus vs écart | (a) l'IA seule ; (b) l'IA propose, le comptage déterministe de I1 s'affiche à côté, le consultant tranche | (b) : « consensus » est un jugement sémantique, mais il ne doit jamais s'afficher sans le décompte qui le soutient ou le contredit |
| B3 | Grain d'un constat | (a) par axe d'étude ; (b) par thème de trame ; (c) transverse à la mission | (a) : l'axe est le seul grain commun aux entretiens structurés ET libres (la trame n'existe pas en mode libre) |

## À ARBITRER (en tête)

> Table du premier cadrage. **A1, A3 et A4 sont amendés par l'arbitrage ci-dessus** ;
> les autres lignes restent valides.

| # | Question | Options | Recommandation (la plus réversible) |
|---|---|---|---|
| A1 | Unité de provenance d'une reco | (a) liste d'`Interview.id` ; (b) liste de verbatims (`quote` + interviewé) ; (c) les deux | (a) : table d'association seule, sans toucher `recommendations` ; (b) s'ajoute ensuite sans rien casser |
| A2 | Qui établit la provenance | (a) l'IA la déclare dans `RECO_SCHEMA` ; (b) calcul déterministe après coup (correspondance texte) ; (c) l'IA propose, le consultant valide | (c) : l'IA propose des id, le code rejette ceux qui ne sont pas dans la mission, le consultant corrige. Jamais d'id inventé montré tel quel |
| A3 | Sort de `generate_theme_synthesis` + table `syntheses` (morts) | (a) rebrancher (incrément 3) ; (b) supprimer ; (c) laisser en l'état | (c) jusqu'à la décision sur l'incrément 3. Supprimer une table exige une migration, c'est moins réversible |
| A4 | Comptage « X sur N » : sur quoi compter | (a) interviewés qui ont répondu à la question/au thème (déterministe, disponible) ; (b) interviewés qui *soutiennent* le constat (sémantique, IA) | (a) d'abord. (b) est un jugement IA : il ne se présente pas comme un comptage |
| A5 | Horizon d'une reco | (a) énuméré (court / moyen / long terme) ; (b) date libre ; (c) trimestre | (a), en colonne nullable. Aucune valeur imposée aux recos existantes |
| A6 | Porteur | (a) garder `acteurs` texte + ajouter `porteur` texte ; (b) référence vers une entité Personne | (a) : aucune entité nouvelle |
| A7 | KPI rattaché à une reco | (a) FK nullable `MissionKpi.recommendation_id` ; (b) garder le rattachement à l'axe (`_snap_axe`) | Reporter (hors incrément 1-2). (a) si on le fait, avec l'axe conservé en repli |
| A8 | Contrat d'import Markdown : puces nouvelles | (a) puces facultatives (`- Sources :`, `- Horizon :`, `- Porteur :`) ignorées si absentes ; (b) puces obligatoires | (a) : `_FILLED_ANALYSIS` reste valide sans modification |
| A9 | Deck : où montrer la provenance | (a) mention sur la fiche reco ; (b) annexe « matrice constat × interviewés » ; (c) les deux | (a) d'abord (une ligne sur `_slide_recommendation`) |

## Constats re-vérifiés sur pièces (2026-09-28)

1. **Traçabilité : CONFIRMÉ.** `synthese_ai._global_material_blocks` (l.185) concatène par thème et par question des lignes `interviewé (rôle) : réponse`, plus les verbatims avec leur auteur. Le LLM reçoit donc les attributions, mais le croisement est entièrement délégué au modèle : aucun comptage n'est produit côté code. `RECO_SCHEMA` (l.524) ne contient que title, objectif, acteurs, valeur, complexite, proposition_valeur, plan_actions et resultats_attendus. `models.Recommendation` (l.882) n'a aucun champ de provenance.
2. **Convergences/divergences : CONFIRMÉ, avec un complément.** `generate_theme_synthesis` (synthese_ai.py l.94) n'a aucun appelant dans `app/` ni dans `tests/`. Complément : la table `models.Synthesis` (`syntheses`, l.398, colonnes `convergences`/`divergences`) est morte elle aussi. Seule la relation `Theme.synthesis` (l.207) la cite, et aucun template ne lit `convergences`.
3. **Plan d'amélioration incomplet : CONFIRMÉ.** `Recommendation` n'a ni horizon, ni échéance, ni dépendance, et `acteurs` est un `String(300)` libre. `MissionKpi` (l.721) porte `axe` en texte, recalé via `_snap_axe` (l.938), et aucun lien vers une reco. La priorisation se limite à `valeur`/`complexite` (1-5), bornés par `_clamp_score`.
4. **« L'import associe les rubriques par mots-clés figés sur les 5 axes » : INFIRMÉ en partie.** `analyse_import._match_global_field` (l.~90) cherche D'ABORD le libellé exact des axes de la mission. `GLOBAL_FIELD_KEYWORDS` (l.41) ne sert qu'en repli pour les 5 axes par défaut. C'est hors sujet ici, mais le contrat reste un parseur par préfixes : les puces de reco suivent `RECO_FIELD_PREFIXES`, et une puce inconnue est ignorée (à confirmer lors de l'implémentation : Information insuffisante sur le traitement exact d'un libellé non reconnu).

## Pourquoi

En restitution, le client conteste un constat par la question « qui a dit ça ? ». Aujourd'hui, le consultant ne peut y répondre qu'en relisant les entretiens. L'outil produit une affirmation du LLM, sans poids (combien de personnes) ni source (qui). Une reco qu'on ne peut pas remonter jusqu'à des entretiens se défend mal. Une reco sans horizon ni porteur ne constitue pas un plan.

## Incréments (chacun livrable seul)

### I1 — Couverture déterministe par thème (le plus petit utile)
- **Visible** : sur l'écran de synthèse et dans le deck, chaque thème affiche « N interviewés sur M ont répondu ». Le calcul se fait par code, sans IA.
- **Change** : nouveau calcul à partir de la matière déjà assemblée pour `_global_material_blocks` (les `by_question` par thème). Aucune écriture en base : la valeur est calculée à la volée.
- **Schéma/DB** : aucun, donc pas de migration.
- **Prompts IA** : aucun changement. En option, injecter « (N/M interviewés) » dans l'en-tête de bloc `=== THÈME ===`. À ARBITRER, parce que cela change la sortie IA.
- **Deck** : une mention de couverture sur les slides de synthèse par axe/thème (`slides_diagnostic.py`, slide exacte : Information insuffisante, non lue).
- **Import Markdown** : non touché.
- **Risques** : en mode libre (`material_libre`), il n'y a ni thème ni question. Il faut alors une définition de couverture par axe (répartition non vide), à spécifier. Le dénominateur M doit exclure les entretiens non transcrits.

### I2 — Constats qualifiés (consensus / écart) et recos qui s'y adossent

> **Version arbitrée du 2026-09-28** (voir en tête). Remplace le I2 initial, qui
> rattachait une reco directement à des entretiens.

- **Visible** : l'écran de synthèse gagne, par axe, une liste de **constats**, chacun
  qualifié `consensus` (les interviewés convergent) ou `écart` (ils divergent
  sensiblement), avec le décompte déterministe de I1 affiché à côté
  (« 5 interviewés sur 7 ») et la liste des personnes qui le portent. Chaque
  recommandation **cite le ou les constats qui la motivent** ; le deck affiche, sur la
  fiche reco, « Part de : `<constat>` — consensus, 5/7 ».
- **Schéma/DB** : nouvelle table `mission_constats(id, mission_id FK CASCADE, axe_key,
  type ∈ {consensus, ecart}, libelle, edite)` (B1, B3) et deux tables d'association :
  `constat_interviews(constat_id FK CASCADE, interview_id FK CASCADE)` pour les
  personnes qui portent le constat, et `recommendation_constats(recommendation_id FK
  CASCADE, constat_id FK CASCADE)` pour le lien de motivation. Migration par
  `APP_DB_MIGRATE=1`. Déclarer de vraies `REFERENCES … ON DELETE CASCADE` : une colonne
  FK ajoutée sans elle laisse des orphelins qu'un id SQLite réutilisé réadopte.
- **Prompts IA** : deux étapes distinctes, pas une seule.
  1. `generate_global_synthesis` (ou un appel dédié) renvoie, en plus des axes, une
     liste de constats `{axe, type, libelle, interviewes[]}`. Les entretiens cités hors
     mission sont rejetés à la lecture (A2) ; il faut donc exposer un identifiant stable
     par entretien dans `_global_material_blocks`, qui n'affiche aujourd'hui que le nom.
  2. `RECO_SCHEMA` gagne `constats` (tableau d'identifiants de constats de la mission),
     et le prompt de `_build_reco_prompt` part désormais des constats plutôt que du seul
     texte de synthèse. Un identifiant de constat inconnu est rejeté, jamais affiché tel
     quel.
  À mesurer sur un vrai appel Ollama (llama3.1:8b) avant de rendre un champ obligatoire :
  un modèle local peut renvoyer une liste vide et vider la sortie.
- **Décompte vs jugement (B2)** : le `type` est un jugement du modèle ; le décompte
  « X sur N » vient du code (I1). Les deux s'affichent ensemble, et **un constat dont le
  décompte contredit la qualification est signalé à l'écran** plutôt que corrigé en
  silence — c'est exactement ce que le consultant doit pouvoir trancher.
- **Deck** : ligne « Part de : … » sur `slides_trajectoire._slide_recommendation`
  (l.288) ; en option, une slide « consensus / écarts » par axe dans
  `slides_diagnostic.py`. Géométrie à revérifier au rendu réel (pptx-verify, invariant
  `test_deck_qualite.py`) — jamais sur la seule foi de python-pptx.
- **Import Markdown** : nouvelle rubrique facultative `## CONSTATS` / `### <axe>` /
  `- [consensus|écart] <libellé> (Nom1, Nom2)`, et puce facultative
  `- Constats : <libellés>` sous une reco (A8). `_FILLED_ANALYSIS` doit continuer de
  passer **sans modification** : c'est le contrat de non-régression de l'import.
- **Garde d'édition** : `Recommendation.edite` et le nouveau `Constat.edite` sont
  respectés — une régénération ne doit pas écraser un constat requalifié à la main.
- **Sort du code mort (A3)** : `generate_theme_synthesis` et la table `syntheses`
  (convergences/divergences, par thème) couvrent une intention proche au mauvais grain.
  À l'implémentation, choisir explicitement : les réanimer, ou les supprimer au profit de
  `mission_constats`. Ne pas laisser coexister deux mécanismes de convergence.
- **Risques** : homonymes à la résolution par nom à l'import ; identifiants de constats
  hallucinés par le modèle ; coût d'un appel IA supplémentaire sur une chaîne déjà
  longue ; et le risque produit principal — **un « consensus » affirmé sur 2 interviewés
  sur 9**, que la règle de décompte ci-dessus existe précisément pour rendre visible.

### I3 — Plan d'amélioration : horizon + porteur (+ convergences/divergences, optionnel)
- **Visible** : chaque fiche reco porte un horizon (A5) et un porteur (A6). Le deck en tire une slide « feuille de route » par horizon.
- **Schéma/DB** : `Recommendation.horizon` (nullable) et `Recommendation.porteur` (texte, défaut ""). Une migration est nécessaire.
- **Prompts IA** : ajouter `horizon` et `porteur` à `RECO_SCHEMA`, avec normalisation hors énumération → null, sans rejet.
- **Deck** : les fiches de `_slide_recommendation` gagnent ces champs. Nouvelle slide dans `slides_trajectoire.py`, à dessiner d'abord par le rendu (deck-design-library).
- **Import Markdown** : puces facultatives `- Horizon :` et `- Porteur :` à ajouter à `RECO_FIELD_PREFIXES`. Attention à l'ordre des préfixes (cf. le commentaire « proposition de valeur » avant « valeur »).
- **Sous-lot convergences/divergences** : rebrancher `generate_theme_synthesis` + table `syntheses` (A3) sur l'écran de synthèse, ou les supprimer. Ce sous-lot est livrable séparément.
- **Risques** : l'horizon attribué par l'IA a une valeur faible, il faut prévoir la saisie consultant.

## Hors périmètre
- Séquencement et dépendances entre recos (graphe) : il faut d'abord I3.
- Rattachement KPI → reco (A7).
- Comptage sémantique « X sur N *soutiennent* » (A4-b).
- Refonte de la priorisation au-delà de valeur × complexité.
- Capture, transcription amont, auth, `.claude/`.
- Suppression de `GLOBAL_FIELD_KEYWORDS`, qui reste en repli volontaire.

## Contraintes
- Le contrat d'import Markdown ne se casse pas : tout champ nouveau est facultatif à l'import (`tests/test_mission_trame_flow.py::_FILLED_ANALYSIS` inchangé).
- `data/app.db` ne migre qu'avec `APP_DB_MIGRATE=1`.
- La garde `edite` des recos/axes est préservée par toute régénération.
- Un chiffre affiché au client (couverture) est calculé par code, jamais rédigé par le LLM.

## Signal de succès
- I1 : sur une mission réelle, chaque thème du deck porte un « N/M » égal au décompte SQL des entretiens ayant au moins une réponse sur ce thème.
- I2 : 100 % des recos générées ont des sources ⊂ entretiens de la mission. Un import Markdown avec `- Sources :` les restaure, sans elles il passe toujours.
- I3 : le deck exporté contient une feuille de route par horizon, rendue et regardée (pptx-verify).

## Hypothèses (non confirmées)
- Le LLM local (llama3.1:8b par défaut) sait restituer des id d'entretien fiables : non mesuré.
- Les noms d'interviewés sont uniques au sein d'une mission : non vérifié.
