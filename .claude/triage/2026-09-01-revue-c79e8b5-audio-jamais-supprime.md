# Triage — revue du commit `c79e8b5` (2026-09-01)

Run d'orchestration `/orchestre` — fan-out de 2 relecteurs adversariaux sur le commit
**déjà poussé** `c79e8b5` « Audio d'entretien : plus aucune suppression automatique » :

- `bmad-revue` → `bmad-code-review` (opus), qui a lui-même cascadé sur
  `bmad-review-adversarial-general` et `bmad-review-edge-case-hunter` ;
- `bmad-revue` → `bmad-review-edge-case-hunter` (opus), périmètre frontend et chemins
  d'erreur.

Puis consolidation en session principale : dédoublonnage, arbitrage des sévérités
divergentes, et re-vérification personnelle des constats graves.

**Statut global (mis à jour le 2026-09-01) : 8 constats arbitrés et corrigés, 13 MINEUR encore `differe`.** La revue était le livrable initial ; les correctifs ont fait l'objet d'un mandat séparé et d'un arbitrage utilisateur (R4). Le détail de ce qui a été fait est en fin de fichier, section « Mise à jour du 2026-09-01 ».

**Preuve** : `VERIFIE-CONSO` = re-vérifié en consolidation par lecture ou exécution
directe ; `VERIFIE-AGENT` = établi par le sous-agent, non recoupé ; `SUPPOSE` = demande
une session navigateur ou une mesure runtime.

## Le fait marquant

**Le commit `c79e8b5` introduit lui-même 6 des 7 constats MAJEUR.** C'est la quatrième
itération consécutive où un correctif introduit un défaut sur ce chantier — et la
première fois que la revue se tient APRÈS le push, donc trop tard pour bloquer. Le
correctif d'EC-1 (le compteur), lui, est jugé **sain et complet par les deux relecteurs
indépendamment** : le problème n'est pas la qualité moyenne du commit, il est concentré
sur EC-3 et sur le rattachement de l'import, deux ajouts qui touchent à des états de page
et à un champ unique sans reprendre les gardes existantes.

## Répartition

| Sévérité | Nombre |
| --- | --- |
| BLOQUANT | **1** |
| MAJEUR | **7** |
| MINEUR | **13** |
| | **21** |

(29 constats bruts rendus par les deux relecteurs → 21 après dédoublonnage de 4 paires et
requalification de 4 sévérités.)

---

## BLOQUANT

| id | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- |
| C1 | La règle rend indestructibles les fichiers dont la mission n'existe plus — 75,8 Mo déjà dans ce cas sur l'installation réelle | `app/services/mission_backups.py:131` + `app/routers/missions.py:251` | VERIFIE-CONSO (mesuré) | **corrigé** — écran global + cascade |

**Ce que j'ai mesuré moi-même**, en lecture seule sur `data/recordings` (29 fichiers,
318,8 Mo) croisé avec `SELECT id FROM missions` en `mode=ro` :

| catégorie | fichiers | taille |
| --- | --- | --- |
| Préfixe d'une mission supprimée (8, 10, 11) | 7 | 57,4 Mo |
| Sans préfixe (`import_1785351999_f938b8c0.webm`, 29/07) | 1 | 18,4 Mo |
| **Inatteignables par l'onglet Backup** | **8** | **75,8 Mo = 24 % du répertoire** |

`lister_backups` exige un objet `Mission` et globbe `{mission.id}_*` : dès que la mission
est supprimée, plus aucun écran ne liste ses fichiers. Le commentaire posé par le commit
en `interviews.py:1568` — « c'est [le préfixe] qui rend la suppression par l'utilisateur
possible » — est donc **faux pour cette population**, et le commit retire au même moment
la purge à 7 jours qui la bornait.

**Nuance d'honnêteté** : ces 8 fichiers étaient déjà orphelins avant `c79e8b5`. Ce que le
commit change, c'est qu'il supprime le dernier mécanisme qui pouvait les faire
disparaître, et qu'il fonde sa règle sur une garantie d'atteignabilité qui ne tient pas.

---

## MAJEUR

| id | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- |
| C2 | Aucune migration des imports d'avant le commit : `import_*` n'a pas de préfixe, donc n'est atteignable par aucun écran, et n'est plus purgé | `app/services/audio_file_jobs.py:327` | VERIFIE-CONSO (le fichier de 18,4 Mo existe) | **corrigé** — écran global |
| C3 | Réutilisation d'id SQLite : une nouvelle mission adopte, écoute et peut supprimer l'audio d'entretien de la précédente | `app/services/mission_backups.py:88` et `:131` | VERIFIE-AGENT (rejoué en base jetable) | **corrigé** — garde de chronologie |
| C4 | Mode libre : l'import est ajouté aux tranches micro — « Relancer la transcription » transcrit deux fois le même entretien | `record_libre.html:1535-1548` → `interviews.py:2273, 2335` | VERIFIE-AGENT (lecture croisée du chemin complet) | **corrigé** — refus + rattachement |
| C8 | Le test structurel ne détecte PAS la réintroduction d'un helper de suppression — sa docstring promet plus fort que ce qu'il fait | `tests/test_audio_jamais_supprime.py:73-80` | VERIFIE-CONSO (détecteur relu ligne à ligne) | **corrigé** — détecteur AST |
| IMPORT-1 | Mode guidé : l'import écrase la référence au backup micro, qui bascule en orphelin — la retranscription rejouera l'import, pas la réunion | `record.html:1371` vs `:1186` | VERIFIE-CONSO (exécuté) | **corrigé** — refus + rattachement |
| EC3-1 | Le message d'EC-3 dit « le texte reste complet » ET « ré-importe-le » : suivre l'instruction duplique la transcription | `record.html:1164-1168`, `record_libre.html:1316-1324` | VERIFIE-CONSO | **corrigé** — message réécrit |
| EC3-2 | Après abandon, « Enregistrer » est actif et `beforeunload` muet : le geste suivant détruit la copie locale que le message dit de télécharger | `record.html:446-463` et `:954-959` | VERIFIE-CONSO | **corrigé** — verrou + acquittement |

### Détail des trois constats que j'ai recoupés personnellement

**C8 — le test est plus faible que sa promesse.** Le détecteur exige `RECORDINGS_DIR`
dans les 12 lignes au-dessus ou sur la ligne. Quatre formes réalistes passent au vert,
dont la plus naturelle : `def _oublier(chemin): chemin.unlink(missing_ok=True)` appelée
avec `RECORDINGS_DIR / job.filename` depuis un autre endroit. La regex ne couvre par
ailleurs ni `shutil.move` / `.rename` / `os.replace` (déplacement hors du répertoire) ni
la troncature (`open(..., "wb")`). C'est le seul garde-fou durable de la règle produit :
tant qu'il est contournable, l'invariant tient par la vigilance, pas par le test.

**IMPORT-1 — vérifié par lecture des trois sites.** `grep` sur `record.html` : le champ
`rec-backup-path` est unique (pas d'`audio_segments` sur cet écran), écrit en `:1186`
(backup micro réussi) et en `:1371` (import terminé, ligne AJOUTÉE par le commit), sans
aucune garde. Le clic d'import n'est gardé que par `fileImportDone`, `fileRetryInFlight`,
`files.length` et `recordingActive` — rien sur un backup existant.

**EC3-2 — vérifié par lecture des trois prédicats.** Ni le `lost` d'`updateSubmitState`
ni le `beforeunload` ne connaissent `urlBackupLocal` / `backupPerdusLocaux`. Après
`settle()`, `pendingBackups` vaut 0 : le bouton est actif, la fermeture d'onglet est
silencieuse. Le commentaire de `record.html:449-453` documente pourtant cette exacte
classe de défaut, déjà corrigée pour `lostSegments` le 2026-07-29 — la leçon n'a pas été
appliquée au chemin frère qu'ajoute ce commit.

---

## MINEUR

| id | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- |
| C5 | Échec d'écriture EN COURS d'upload : le fichier partiel porte déjà le préfixe et n'est plus supprimé — indiscernable d'un import valide | `app/routers/interviews.py:1611-1617` | VERIFIE-AGENT | differe |
| C6 | `record.html` ne révoque pas `urlBackupLocal` sur « Recommencer » : le blob de l'entretien entier reste en mémoire pour la vie de la page | `record.html:1584-1608` vs `record_libre.html:1257-1264` | VERIFIE-AGENT | **corrigé** — effet du point de reset partagé |
| C9 | Les tests de rattachement sont des recherches de chaîne à l'échelle du fichier : verts sur du code commenté ou mort | `tests/test_audio_jamais_supprime.py:196-207` | VERIFIE-AGENT (mutations exécutées) | differe |
| C10 | La fenêtre `[:4000]` déborde sur la route suivante — fragilité que le test frère corrige explicitement deux tests plus haut | `tests/test_audio_jamais_supprime.py:172-181` | VERIFIE-AGENT (fenêtre calculée) | differe |
| C11 | La docstring annonce un invariant « échec de création de job » qui n'est testé nulle part | `tests/test_audio_jamais_supprime.py:22-23` | VERIFIE-AGENT (grep) | differe |
| C12 | `updateSubmitState` depuis une génération périmée écrase le message d'état — ce que le commentaire du correctif nie explicitement | `record.html:897`, `record_libre.html:968` | VERIFIE-AGENT | differe |
| C13 | Le répertoire nettoyé par les tests, `%TEMP%\recordings`, n'est pas nommé pour ce projet : efface le contenu tiers et celui d'une seconde copie de travail | `tests/conftest.py:44` | VERIFIE-AGENT (marqueurs déposés/relevés) | differe |
| C14 | `db.commit()` sans modification dans la branche 409 : le commit en a retiré les deux seules écritures | `app/routers/interviews.py:1691` | VERIFIE-AGENT | differe |
| C15 | Bruit de fichiers générés dans un commit de code : `docs/wiki.html` et `agents-supervision.md` n'y apportent qu'un horodatage de scan | commit `c79e8b5` | VERIFIE-CONSO (diff relu) | **constat sur MA façon de faire** |
| IMPORT-2 | Ré-importer une tranche perdue lui donne une position déjà prise, et le bandeau « NON TRANSMISE » ne s'éteint jamais | `record_libre.html:1543-1545`, `:1308-1325` | VERIFIE-AGENT | differe |
| EC3-3 | `recuperables` compare des LONGUEURS : une seule tranche non gardée en local fait annoncer « PERDUES » pour toutes, lien de téléchargement affiché juste en dessous | `record_libre.html:1315` | VERIFIE-AGENT | differe |
| EC3-4 | `offrirBackupEnLocal` révoque AVANT d'affecter : si `createObjectURL` jette, le lecteur et le lien pointent sur une URL morte | `record.html:1106-1107` | VERIFIE-AGENT (déclencheur SUPPOSE) | differe |
| SRV-1 | `mission_id` et `session_token` ne sont pas recoupés : un POST peut attribuer l'audio à une autre mission existante | `app/routers/interviews.py:1580-1600` | VERIFIE-AGENT | differe — **portée réduite** |

**Arbitrage de consolidation sur SRV-1** : le second relecteur a établi par `grep` que
l'application n'a **aucune authentification** (`login` / `auth` / `current_user` → 0
résultat dans `app/`). Un « POST forgé » n'est donc pas un modèle de menace ici, et le
commit n'introduit aucune régression de sécurité. Ce qui reste vrai et utile : un onglet
ouvert sur une autre mission produit un fichier mal attribué. Ramené de MAJEUR à MINEUR.

---

## Fausses pistes fermées (ne pas les rouvrir)

Le brief du premier relecteur listait trois soupçons sur le préfixe. Les trois sont
**infondés**, et c'est démontré :

- **Collision `1_*` / `12_*`** : `lister_backups` globbe `f"{mission.id}_" + "*"` et
  `appartient_a_mission` compare `startswith(f"{mission_id}_")` — le séparateur est
  inclus. Déjà couvert par `test_un_import_orphelin_est_visible_dans_l_onglet_backup`.
- **Traversée de chemin par le suffixe** : `suffix` est filtré aux alphanumériques et au
  point, puis repart du dernier point — ni `/` ni `\` ne survivent.
- **Collision avec la convention des backups micro** : `{mission}_{ts}_{hex}.webm` contre
  `{mission}_import_{ts}_{hex}{suffix}` — le jeton `import_` les sépare, et aucun code du
  dépôt ne re-découpe un nom de fichier (`split("_")` → 0 résultat dans `app/`).

## Ce que les deux relecteurs jugent SAIN, indépendamment

- **Le correctif EC-1 du compteur** : 2 incréments / 2 décréments par écran, tous dans un
  `.finally` inconditionnel, aucun reset qui remette le compteur à zéro, plancher
  `Math.max(0, …)` en simple ceinture. Symétrique et complet sur les deux écrans. Le
  blocage à vie décrit dans le message de commit est bien fermé.
- **L'ordre des validations de `transcribe_file`** : les trois refus précèdent tous
  l'écriture, verrouillé par le comportement (`test_import_sans_jeton_refuse_SANS_rien_ecrire`).
- **La règle « plus aucune suppression automatique » elle-même** : un seul `unlink`
  d'audio dans tout `app/`, celui de `delete_record_backup`. Aucun chemin frère oublié.
- **La chaîne du préfixe de bout en bout** : écriture → `lister_backups` → 
  `appartient_a_mission` → `delete_record_backup`, vérifiée par exécution.

## Limites de cette revue

- **Aucune vérification en navigateur.** Les 7 constats JS sont établis par lecture
  croisée des branches, pas par exécution. C4 et EC3-2 mériteraient un rejeu réel.
- **L'indépendance des couches n'a pas pu être tenue chez le second relecteur** : son
  contexte n'avait pas l'outil de lancement de sous-agents, donc les couches Blind Hunter
  et Edge Case Hunter ont été jouées en séquence dans la même session, après lecture du
  diff. Un angle mort partagé reste possible. Le premier relecteur, lui, était bien
  indépendant — c'est ce qui rend le recoupement des deux flux significatif.
- **C5 non provoqué** (disque saturé), **EC3-4 non provoqué** (`createObjectURL` qui jette).
- Le chiffre de 318,8 Mo est un instantané, pas une vitesse d'accumulation.


---

## Mise à jour du 2026-09-01 — application des 8 constats arbitrés

⚠ **Ce fichier ne se met pas à jour tout seul.** Toute reprise de ce sujet part de la
colonne « statut » ci-dessus, pas de la mémoire de la séance.

**Arbitrage utilisateur** : « les 8 bloquant+majeurs », avec deux choix de forme —
C1/C2 en « écran global + cascade », C4/IMPORT-1 en « refus + rattacher sans transcrire ».
Les 13 MINEUR restent `differe` : ils n'ont pas été arbitrés, donc rien n'y a été touché.

### Ce qui a été fait, constat par constat

| Constat | Correctif | Où |
| --- | --- | --- |
| C1 | Écran « audio sans mission » (`/missions/audio-orphelin`) : liste, écoute, raison de l'orphelinat, suppression. Seul point d'entrée : un bouton sur la liste des missions, visible uniquement s'il y a des orphelins | `mission_backups.lister_orphelins_globaux`, `missions.py`, `templates/missions/audio_orphelin.html` |
| C2 | Le même écran couvre les imports d'avant le commit : un fichier SANS préfixe de mission y apparaît avec la raison « nom sans préfixe (import d'avant le 2026-09-01) » | `lister_orphelins_globaux` |
| C3 | `appartient_a_mission` refuse un fichier antérieur à la création de la mission — une mission qui hérite d'un id SQLite recyclé n'adopte plus l'audio de la précédente. L'ordre des tests compte : la référence explicite l'emporte AVANT la garde de chronologie | `mission_backups.py` |
| C4 | Le mode libre refuse « Transcrire ce fichier » dès qu'une tranche existe, et propose « Rattacher sans transcrire » | `record_libre.html` |
| C8 | Détecteur AST verbe-orienté avec liste d'exceptions nommées (fonction + raison), remplaçant la fenêtre de 12 lignes autour de `RECORDINGS_DIR`. Couvre `shutil.move`, `os.replace`, `Path.rename`, la troncature et l'ouverture en écriture ; discrimine `str.replace`/`datetime.replace` par la forme de l'appel. Un test dédié prouve que le détecteur voit ces verbes ET ne crie pas sur les homonymes | `tests/test_audio_jamais_supprime.py` |
| IMPORT-1 | Le mode guidé refuse l'import transcrivant quand un backup est déjà rattaché, et offre le même « Rattacher sans transcrire » | `record.html` |
| EC3-1 | Le message d'EC-3 envoie désormais vers « Rattacher sans transcrire » et dit explicitement de ne PAS passer par « Transcrire ce fichier ». Le commentaire du code portait la même erreur : corrigé aussi | les deux écrans |
| EC3-2 | Une copie locale non acquittée gèle « Enregistrer l'entretien » et arme `beforeunload`. Sortie explicite par un bouton d'acquittement — sans lui le verrou serait une impasse. L'acquittement se réarme sur une tranche neuve et se remet à zéro sur « Recommencer » | les deux écrans |
| C6 (MINEUR, effet de bord) | Le point de remise à zéro partagé (`oublierBackupLocal`) révoque le blob sur « Recommencer » et sur un nouveau démarrage | `record.html` |

### Ce que ces correctifs ont eux-mêmes introduit, et qui a été rattrapé

Deux impasses créées puis fermées dans la même séance — elles sont notées parce qu'elles
sont le mode d'échec récurrent de ce chantier (un correctif qui introduit son défaut) :

1. le verrou EC3-2 n'avait pas de sortie : qui acceptait de perdre la tranche ne pouvait
   plus enregistrer son entretien. D'où le bouton d'acquittement ;
2. l'état d'acquittement survivait à « Recommencer », donc l'entretien SUIVANT partait
   avec son bouton d'enregistrement grisé. D'où la remise à zéro dans `clearBackupList`
   et dans `oublierBackupLocal`.

### Ce qui reste ouvert

- Les **13 constats MINEUR** ci-dessus, tous en `differe`. Trois d'entre eux (C9, C10,
  C11) portent sur les tests de ce même fichier et n'ont PAS été traités par la
  réécriture C8, qui n'a touché qu'au détecteur structurel.
- **C13** (le nettoyage de `%TEMP%\recordings` par `conftest.py`) mérite d'être remonté :
  c'est le seul MINEUR qui peut détruire des données hors de ce dépôt.


---

## Seconde revue adversariale — sur les correctifs eux-mêmes (2026-09-01, avant commit)

Cette fois la revue est passée **avant** le commit, pas après. C'est la différence
mesurée par le superviseur le même jour : sur ce chantier, les quatre cycles précédents
ont chacun introduit un défaut, et le seul attrapé à temps est celui où la re-revue était
une étape du plan.

Elle a rendu **0 bloquant, 7 majeurs, 7 mineurs, 1 point à arbitrer**. Les 7 majeurs
portaient tous sur les correctifs de la journée. Tous sont traités.

| id | Ce que la revue a trouvé | Traitement |
| --- | --- | --- |
| A1 | **EC3-1 n'était pas corrigé sur `record.html`** : j'avais réécrit le commentaire (L1132) et laissé la chaîne que l'utilisateur lit (L1242), qui disait toujours « ré-importe-le avec Importer un fichier audio ». Le constat était déclaré « corrigé — les deux écrans » | Chaîne réécrite : elle envoie vers « Rattacher sans transcrire » et dit explicitement de ne PAS transcrire |
| A2 | **IMPORT-1 rouvert par le bouton qui le corrige** : « Rattacher » n'avait pas la garde `fileImportDone`. Rattacher pendant un import donnait rattachement de A puis écrasement par B à la fin de l'import | Garde ajoutée, alignée sur celle du mode libre qui l'avait déjà |
| A3 | **Le détecteur C8 était aveugle aux alias d'import** : `from os import remove`, `import shutil as sh`, `open(chemin, mode)` avec mode calculé, `shutil.copyfile`. Un arbre jetable portant 6 suppressions rendait `{}` | Résolution des imports (`_alias_des_imports`) ; mode d'ouverture indécidable traité comme destructeur ; `copyfile`/`copy2`/`copy` ajoutés |
| A4 | **Le bypass que C8 nommait mot pour mot n'était pas fermé** : un helper qui reçoit son chemin en argument, dans un fichier au nom neutre, restait invisible. Le filtre était passé de « 12 lignes » à « corps de la fonction » — plus large, même mode d'échec | Les verbes jamais anodins (`unlink`, `rmtree`, `shutil.move`…) ne passent plus par l'heuristique du tout : ils se justifient un par un dans `_EXCEPTIONS`. Ils sont 6 dans tout `app/`, l'exigence ne coûte rien |
| A5 | **Un fichier en cours de transcription était proposable à la suppression** : le jeu de références ignorait `AudioFileJob` | Les jobs non terminés retiennent leur fichier — **bornés par `is_audio_file_job_stale`** : sans cette borne, un import de juillet resté à `running` retenait 18,4 Mo pour toujours, c'est-à-dire C1 refabriqué par son propre remède (mesuré sur l'installation réelle) |
| A6 | `delete_mission` effaçait l'audio **avant** `db.commit()` : un commit en échec (« database is locked », réaliste ici) détruisait l'audio en laissant la mission | Ordre inversé. Il ne peut plus rater que dans le sens réparable — un unlink en échec laisse un orphelin, que le nouvel écran rattrape |
| A7 | Le rattachement réussi recopiait la révocation en ligne au lieu d'appeler `oublierBackupLocal()` : le bouton d'acquittement restait affiché après « ✓ fichier rattaché », et le cliquer conduisait l'utilisateur à **supprimer le backup qu'on venait de ranger** | Passage par le point unique. C'est le « point unique » que ce même diff introduisait, et que cette branche contournait |
| B1 | Mode libre : le geste CORRECT (rattacher) ne levait pas le gel d'EC3-2 — seule issue, acquitter, donc affirmer un fait déjà accompli | `noterRattachementLocal()`. Il ne révoque **jamais un blob au jugé** : une seule copie en attente se lève sans ambiguïté, plusieurs restent affichées. Sur-avertir ne coûte rien, sous-avertir détruit de l'audio |
| B2 | `int(prefixe)` après `prefixe.isdigit()` : `« ² ».isdigit()` est vrai, `int(« ² »)` lève — 500 sur la liste des missions | Garde `isascii()` |
| B3 | `glob("*")` sans filtre : un `.gitkeep` était listé comme audio supprimable | Filtre sur `_MEDIA_AUDIO` |

Restent **non traités et assumés** : B4 (suffixe déduit réductible à `.`), B5
(`appartient_a_mission(mission=None)` désarme la garde — aucun appelant actuel), B6
(l'inventaire ignore le mode démo), B7 (`/missions` mesuré à 10,1 ms).

### Le point à arbitrer — D1

`save_record_backup` renvoie désormais 404 si la mission n'existe plus. La revue objecte
que l'alternative au refus n'est pas « un orphelin » mais « une perte sèche » : un
enregistrement libre en cours dont le brouillon est emporté par « Nettoyer N brouillons
vides » dans un autre onglet voyait avant son fichier atterrir sur disque — désormais
joignable par l'écran global. Depuis le correctif, il est refusé net et la tranche
n'existe plus que comme blob de navigateur. **Mesure : les 7 brouillons réels sont tous
`_draft_vide=True`, dont les missions 7 et 14 qui portent de l'audio sur disque.**

Ce n'est pas un bug, c'est un choix de produit : accepter l'écriture d'un audio sans
mission, ou refuser. **Non tranché, non touché.**

### Ce que la revue a attaqué sans rien trouver

Elle le dit nommément, et c'est utile : l'ordre de `appartient_a_mission`, le fuseau
d'`_epoch_creation` (dérive de 2 h réellement fermée dans les deux sens), la garde C3 sur
données réelles (aucun fichier référencé listé comme orphelin, les 4 cas d'id réattribué
sont de vrais recyclages), l'impossibilité pour la cascade d'emporter l'audio d'une autre
mission, la traversée de chemin, la course affichage/suppression, l'ordre des routes, les
compteurs de génération, la règle du décrément hors garde, l'absence d'impasse EC3-2, et
l'absence de XSS stocké.

### Limite de cette revue

**Aucune exécution navigateur** : A1, A2, A7 et B1 sont établis par lecture croisée et
`grep`, pas par clic — je les ai re-vérifiés moi-même de la même façon avant de corriger.
Et les couches n'étaient pas indépendantes : le porteur `bmad-revue` n'a pas l'outil
`Agent`, donc les deux angles ont été joués en séquence dans un seul contexte. C'est
exactement le constat n° 2 du superviseur du même jour.
