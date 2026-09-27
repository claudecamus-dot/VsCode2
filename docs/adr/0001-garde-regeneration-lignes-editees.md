# ADR 0001 — Garde serveur contre l'écrasement des lignes éditées à la main

- Date : 2026-09-27
- Statut : accepté (arbitrage utilisateur du 2026-09-26, salle atelier-dev, constat G)
- Périmètre : **les HUIT surfaces régénérables** d'une mission. Étendu le
  2026-09-27 (arbitrage utilisateur
  `VSCode2:garde-regeneration-limitee-a-3-surfaces-sur-8`, soulevé
  indépendamment par deux lentilles de revue au titre de « appliquer la leçon aux
  chemins frères ») des trois listes de suivi initiales — indicateurs
  (`mission_kpis`), matrice risques-contrôles (`mission_risks`), grille de
  maturité (`mission_maturites`) — aux cinq autres : difficultés
  (`mission_difficulties`), axes et recommandations (`recommendation_axes`,
  `recommendations`), SWOT (`mission_swots`), executive summary
  (`mission_executive_summaries`) et synthèse globale (`global_syntheses`).
  **Aucune surface n'est exclue.**

## Contexte

Tout ce que l'IA produit sur une mission s'affine ensuite à la main, par autosave
htmx : les trois listes de suivi, ligne par ligne (`POST /kpis/{id}/field`,
`/risques/{id}/field`, `/maturites/{id}/field`), mais aussi les difficultés, les
axes et recommandations, les quadrants de la SWOT, l'executive summary et la
synthèse globale elle-même. Une nouvelle génération REMPLACE intégralement :
`apply_kpis_result` / `apply_risks_result` / `apply_maturite_result` /
`apply_difficulties_result` réaffectent la collection, ce qui supprime les
anciennes lignes (delete-orphan) et en crée de neuves ;
`apply_recommendations_result` supprime puis recrée l'arbre ;
`apply_swot_result` / `apply_executive_summary_result` /
`apply_global_synthesis_result` réécrivent les champs en place.

La seule protection était un `confirm()` JavaScript sur le bouton
« Régénérer ». Il ne protège rien côté serveur : une requête directe, un double
envoi, un onglet resté ouvert ou un navigateur sans JS écrasent le travail du
consultant sans que rien ne l'arrête. Le travail perdu n'est pas récupérable :
aucune version antérieure des lignes n'est conservée.

## Décision

Une couture unique — `app/services/garde_edition.py` : un registre `SURFACES` des
huit surfaces et une fonction `garde_regeneration(mission, *, surface, confirmer)`.
`surface` est un mot-clé OBLIGATOIRE et doit être une clé du registre : une
neuvième surface branchée dessus lève un `KeyError` au lieu d'être servie sans
garde en silence. Pas de paramètre optionnel, pas de mode dégradé.

**TROIS formes de marqueur, parce que les surfaces n'ont pas la même forme.**
L'uniformité aurait coûté une colonne redondante :

| Surface | Marqueur | Migration |
| --- | --- | --- |
| `mission_kpis`, `mission_risks`, `mission_maturites`, `mission_difficulties` | `edite` par ligne | oui (additive) |
| `recommendation_axes` + `recommendations` | `edite` aux DEUX niveaux, compte agrégé sur l'arbre | oui (additive) |
| `global_syntheses`, `mission_swots`, `mission_executive_summaries` | `status == "edited"`, **déjà existant** | **aucune** |

1. **Listes de lignes** — un drapeau par ligne, `edite BOOLEAN DEFAULT 0`.
2. **Arbre axes / recommandations** — le même drapeau, sur les DEUX niveaux :
   l'intitulé d'un axe (`POST /recommandations/axes/{id}/field`) et les champs
   d'une recommandation (`POST /recommandations/{id}/field`) sont deux autosaves
   distincts, et ne marquer que la recommandation aurait laissé un axe renommé à
   la main se faire écraser en silence. Le compte est agrégé sur l'arbre, qui est
   ce que le consultant voit comme « ses recommandations ».
3. **Enregistrements uniques** (synthèse globale, SWOT, executive summary) —
   **aucune colonne nouvelle.** Ces trois modèles portent déjà
   `status ∈ {empty, generated, edited}`, posé à `edited` par leur autosave et
   remis à `generated` par `apply_*_result` : le marqueur existe, il se remet à
   zéro seul, exactement comme `edite`. En ajouter un second aurait dupliqué un
   état (correction minimale, R1). Effet de bord utile : un contenu SAISI à la
   main sans génération préalable (`empty` -> `edited`) est protégé lui aussi, ce
   qu'un drapeau posé par la génération n'aurait pas couvert.

Pour les cinq surfaces de type 1 et 2, le drapeau est un `edite BOOLEAN DEFAULT 0`
par ligne.

- Il est posé à VRAI par les routes d'autosave d'`app/routers/synthese.py`
  (`save_kpi_field`, `save_risk_field`, `save_maturite_field`,
  `save_difficulty_field`, `set_difficulty_verbatim`, `save_recommendation_field`,
  `save_axis_field`), APRÈS validation du champ — un champ refusé en 400 ne marque rien — et
  **seulement si la valeur change réellement** (`_ecrire_et_marquer`). Les
  autosaves se déclenchent aussi sur `blur`, et le modificateur `changed` de
  `hx-trigger` ne porte que sur `keyup` : entrer dans un champ et en sortir sans
  rien taper poste la valeur inchangée. Sans cette condition, parcourir l'écran au
  clavier annonçait « 8 lignes éditées à la main seront remplacées » — un
  avertissement qui crie pour rien n'est plus lu le jour où il a raison. La même
  condition vaut pour le `status = "edited"` des trois enregistrements uniques
  (`_marquer_statut_edite`), qui était posé à CHAQUE autosave avant le 2026-09-27,
  changement ou pas. Lier un verbatim à une difficulté marque la ligne au même
  titre qu'un libellé retouché : c'est du travail à la main, et la régénération le
  perd (les difficultés neuves naissent sans lien).
- Il retombe à FAUX **sans aucun code** à la régénération : `apply_*_result`
  construit des objets neufs, qui naissent avec le défaut du modèle. Rien à
  ajouter dans `app/services/synthese_ecriture.py`, et un test le prouve
  (`test_regeneration_remet_le_drapeau_edite_a_faux_sans_code`) — c'est ce fait
  qui rend la décision aussi petite.
- La garde est appelée par les HUIT routes de génération : `_generate_liste_view`
  (`app/routers/export.py`) pour les trois listes de suivi, puis directement dans
  `generate_swot_view`, `generate_executive_summary_view`,
  `generate_difficulties_view` (export.py), `generate_recommendations_view` et
  `generate_global` (synthese.py). S'il existe au moins un élément édité et que le
  formulaire ne porte pas `confirmer=1`, **rien n'est généré** : l'écran revient
  avec le message et un bouton qui repose la même demande avec `confirmer=1`.
- **Code HTTP : 400 pour sept surfaces, 200 pour la synthèse globale.** Les sept
  premières postent un formulaire de page entière, dont le navigateur rend le
  corps quel que soit le statut — le 400 est donc le même code que les uploads
  refusés depuis l'harmonisation du 2026-09-27. La synthèse globale, elle, est un
  fragment HTMX (`hx-post` + `hx-swap="outerHTML"` dans `_global_panel.html`), et
  htmx 2.0.3 n'échange RIEN sur un 4xx (`responseHandling` par défaut :
  `{code:"[45]..", swap:false, error:true}`, lisible dans
  `app/static/htmx.min.js`). Un 400 y laisserait le consultant devant un écran
  inchangé, sans la demande de confirmation : **une garde dont le refus est
  invisible est un bug**, et c'est la seule raison de la divergence. Un test en
  VRAI NAVIGATEUR la tient
  (`test_garde_synthese_globale_rend_sa_confirmation_dans_le_fragment_htmx`) : un
  TestClient, qui lit le corps d'un 400 sans se soucier d'htmx, ne l'aurait pas vu.
  Sur cette route, la garde s'interpose avant la prise de jeton atomique : aucun
  `generation_status` touché, aucune tâche de fond lancée.
- La garde passe **après la précondition IA**. Sans synthèse globale, la
  génération est de toute façon impossible : demander de confirmer le
  remplacement d'une ligne éditée pour répondre ensuite « générez d'abord la
  synthèse globale » ferait valider une action qui n'aura pas lieu.
- La couture **exige** une surface déclarée dans `SURFACES`
  (`app/services/garde_edition.py`) et lit `.edite` / `.status` en accès direct.
  Pas de paramètre optionnel, pas de `getattr` avec défaut : une neuvième surface
  branchée dessus lève un `KeyError` au lieu d'être servie sans garde, et une
  faute de frappe sur l'attribut ne se déguise pas en « aucune ligne éditée ». Le
  mode de défaillance de la garde n'est pas « éteinte ». Un test de contrat fige
  la signature (`surface` mot-clé sans défaut) et la liste des huit clés.

Le `confirm()` JavaScript (et le `hx-confirm` du panneau de synthèse globale) est
CONSERVÉ : il couvre aussi le cas d'un contenu généré NON édité, que la garde
serveur laisse passer par construction. La double confirmation ne concerne que ce
qui a réellement été touché à la main.

### Deuxième perte, symétrique : un résultat IA entièrement vide

La garde protège du remplacement par un résultat VOULU. Elle ne protégeait pas
du remplacement par un résultat VIDE : les listes refusaient déjà d'écraser une
liste affinée par une génération sans item (`_generate_liste_view`), mais
`apply_swot_result`, `apply_executive_summary_result` et
`apply_global_synthesis_result` écrivaient sans condition et reposaient
`status = "generated"`. Or `_clean_global` / `_clean_swot`
(`services/synthese_ai.py`) rendent TOUJOURS chaque clé, `""` comprise : un
modèle muet effaçait donc le texte écrit à la main **et** désarmait la garde
pour la fois suivante. Défaut préexistant à ce lot, corrigé avec lui (arbitrage
utilisateur du 2026-09-27) : le laisser ouvert sur les surfaces mêmes qu'on
vient de protéger serait incohérent.

**Règle retenue : vide = TOUS les champs blancs.** Pas « au moins un champ
blanc » : une SWOT sans menaces ou un executive summary sans `key_message` sont
des générations légitimes, et les refuser bloquerait le produit bien plus
souvent que le défaut corrigé. C'est la règle des listes (« aucun item »)
transposée à un enregistrement multi-champs, et c'était déjà celle que l'import
d'analyse externe appliquait localement (`any(... .strip())` dans
`routers/export.py`) — cette copie a été supprimée au profit de la règle unique,
portée par `_resultat_entierement_vide` dans `services/synthese_ecriture.py`.

Les trois `apply_*` rendent désormais un booléen « quelque chose a été écrit ».
Sur False, **rien n'est écrit et `status` n'est pas touché** — ne pas reposer le
statut est la moitié du correctif, sans quoi la garde se désarmerait en silence.
Le message : SWOT et executive summary le rendent dans leur réponse, comme les
listes ; la synthèse globale est produite par un job de fond
(`services/global_synthesis_job.py`) qui n'a aucune réponse HTTP — elle emprunte
donc le canal existant `generation_status = "error"` / `generation_error`, que le
panneau affiche déjà (poll `/synthese/globale/status` et rechargement de page).

## Migration

Additive, par le dictionnaire `additions` de `_add_missing_columns`
(`app/db.py`) : `ALTER TABLE … ADD COLUMN edite BOOLEAN DEFAULT 0` au démarrage,
sans réécriture de table ni perte de données, sur SIX tables — `mission_kpis`,
`mission_risks`, `mission_maturites` (2026-09-27, lot initial), puis
`mission_difficulties`, `recommendation_axes`, `recommendations` (extension du
même jour). Les trois enregistrements uniques n'exigent AUCUNE migration : ils
réutilisent leur colonne `status`.

**Limite assumée — à lire avant de s'étonner :** les lignes éditées AVANT cette
migration se relisent à `edite = 0`. (Elle ne vaut PAS pour les trois
enregistrements uniques : leur `status` existait déjà, donc une synthèse, une SWOT
ou un executive summary édités de longue date sont protégés dès la mise à jour.) La colonne n'existait pas quand elles ont
été modifiées, et rien en base ne permet de le reconstituer (cf. « Écarté »).
La garde protège donc les éditions POSTÉRIEURES à la migration. Sur une base
existante, la première régénération après mise à jour peut encore écraser des
lignes éditées de longue date — seul le `confirm()` JavaScript s'y oppose.

## Écarté

- **Horodatage (`updated_at` comparé à la date de génération).** Les tables de
  lignes (indicateurs, risques, maturité, difficultés, recommandations) n'ont
  aucune colonne de date : il faudrait l'ajouter ET la remplir, ce
  qui ne dirait toujours rien des lignes déjà modifiées. Même limite que le
  drapeau, pour plus de code.
- **Empreinte du contenu généré, comparée au contenu courant.** Détecterait les
  éditions antérieures, mais suppose de conserver le résultat de génération de
  chaque ligne, donc une table ou une colonne de plus, une empreinte à maintenir
  à chaque écriture et un faux positif dès qu'une normalisation change. Trop
  lourd pour ce que ça ajoute.
- **Fusion (garder les lignes éditées, remplacer les autres).** Rend un état
  imprévisible pour le consultant : liste mi-ancienne mi-neuve, positions et
  couvertures incohérentes, sans qu'il puisse savoir ce qui vient d'où. Un
  remplacement franc, annoncé et confirmé, est plus honnête.
- **Une colonne `edite` sur les trois enregistrements uniques, pour l'uniformité.**
  Elle aurait dupliqué le `status` empty|generated|edited déjà porté par ces
  modèles, déjà posé à `edited` par l'autosave et déjà remis à `generated` par la
  génération : deux états à maintenir d'accord, pour une garde identique. La
  diversité des trois formes est assumée — elle suit la forme des données.
- **Naissance à `edite=1` d'une ligne ajoutée à la main.** Sans objet : il
  n'existe aujourd'hui aucune route d'ajout de ligne (limite connue « pas de
  bouton ajouter »). À reposer le jour où elle existera.
