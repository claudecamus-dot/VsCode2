# Revue adversariale du chantier D1 — « la mission disparue n'est pas un refus »

**Date** : 2026-09-02 · **Cible** : le diff NON COMMITÉ au-dessus de `f2a0520`
(`app/routers/interviews.py`, `app/services/mission_backups.py`, `app/static/app.css`,
`app/templates/interviews/record.html`, `record_libre.html`, `tests/test_mission_backups.py`).

**Dispositif** : deux relecteurs `bmad-revue` en **contextes séparés**, lancés en parallèle
dans un même message, angles disjoints (backend / frontend). C'est la première fois que
l'indépendance des couches est réellement tenue : les trois revues précédentes signalaient
toutes la même limite — un porteur unique sans outil `Agent` jouait les deux angles en
séquence dans un seul contexte.

**Verdict brut** : 23 constats — **1 bloquant, 9 majeurs, 13 mineurs**. Aucun doublon.
Deux paires se corroborent d'un bout à l'autre (`D1-F5` ↔ `ID-REUSE-LEAK`).

**Arbitrage utilisateur du 2026-09-02** : traiter le bloquant + les 9 majeurs. Les 13
mineurs restent **non arbitrés** et sont consignés ici pour cela.

---

## Le fil rouge : le bandeau fait trois promesses, le code n'en tient aucune entièrement

Le chantier D1 remplace un 404 par une écriture + un bandeau qui dit à l'utilisateur où
son audio a été rangé. Les trois affirmations de ce bandeau ont été vérifiées une par une,
et chacune est fausse dans un cas atteignable.

| Promesse | Mesure | Constat |
| --- | --- | --- |
| « rangé dans Audio sans mission » | 23 noms sur 37 s'écrivent hors de l'inventaire et sortent en 404 | `D1-B1-BIS` |
| « tu peux l'écouter » | 3 tranches sur 4 gardent l'URL par mission, qui rend 404 | `D1-F1` |
| « le rattacher à une autre mission » | L'écran n'expose que *Télécharger* et *Supprimer* | `D1-F4` |

---

## BLOQUANT

### D1-F1 — la reconstruction des lignes est inerte sur les lignes qu'elle doit réparer

`record_libre.html:1436-1442` (reconstruction) + `:1408-1412` (`urlAudio`).

`fichiersOrphelins` ne reçoit que le fichier de la réponse **courante**. Toutes les tranches
uploadées avant la suppression sont donc rebâties avec la même URL par mission, qui rend
404. Le correctif `D1-M3` (passage du drapeau global à une carte par fichier) a cassé D1.

**Scénario** : entretien libre de 70 min, rotation toutes les 20 min. Tranches 1-2-3
uploadées mission vivante ; « Nettoyer les brouillons vides » supprime la mission à la
65ᵉ minute ; tranche 4 remonte `mission_absente:true`. Le bandeau annonce que l'audio est
écoutable — à l'écran, seul le lecteur de la tranche 4 fonctionne. 60 min d'audio sur 70
sont déclarées accessibles et ne le sont pas sur cette page.

**Preuve** — harnais Node sur les fonctions RÉELLES extraites du template :
```
tranche 1  <audio src=/missions/7/interviews/record/backup/7_t1.webm>
tranche 2  <audio src=/missions/7/interviews/record/backup/7_t2.webm>
tranche 3  <audio src=/missions/7/interviews/record/backup/7_t3.webm>
tranche 4  <audio src=/missions/audio-orphelin/7_t4.webm>
LIGNES POINTANT ENCORE SUR LA ROUTE PAR MISSION (404) : 3/4
```

**Aggravant** : le bloc fait `audioSegmentListEl.innerHTML = ''` puis reconstruit des
`<audio>` neufs — il coupe une écoute en cours (ce que le commentaire l.1248-1251 dit
vouloir éviter) pour un résultat identique sur 3 lignes sur 4.

**Atténuation mesurée** : les fichiers restent joignables depuis `/missions/audio-orphelin`
(`lister_orphelins_globaux` les liste). Ce n'est donc pas une perte de données — c'est une
promesse fausse et des lecteurs morts sur la page.

---

## MAJEURS — arbitrés, à traiter

### D1-B1-BIS — la route d'écriture n'applique jamais le filtre que la lecture vient d'assouplir

`interviews.py:1839-1848` (écriture) vs `mission_backups.py:71-86` (lecture).

Le correctif D1-B1 a rendu le filtre de lecture permissif ; la route d'écriture, elle,
recopie l'extension du client sans la confronter à `est_media`. 23 extensions produisent un
fichier écrit, annoncé `mission_absente: true`, absent de l'inventaire et refusé en 404.
C'est C1/D1-B1 refabriqué de l'autre côté du tuyau.

**Preuve** — E2E, 37 noms postés sur base jetable : `nb KO = 23 / 37`.

### B4-SIBLING — le garde-fou B4 n'est posé que sur un des deux chemins

`interviews.py:1603-1604` (`transcribe_file`, non touché). Le chemin jumeau porte les deux
mêmes lignes sans garde. Combiné à `est_media`, un fichier en cours de transcription
devient visible **et supprimable** dans l'inventaire global.

**Contrefactuel mesuré** : `ancien filtre = False` / `est_media = True` — avant le diff le
fichier était invisible, après il est destructible en un clic, pendant que le job le lit.

### B4-TEST-CI — le test du garde-fou B4 est non discriminant sur le système qui sert de gate

`tests/test_mission_backups.py:1027-1080` et `.github/workflows/ci.yml:15`
(`runs-on: ubuntu-latest`). Sous POSIX le point final est conservé, donc les deux
assertions passent **avec ou sans** le garde-fou. Retirer `interviews.py:1846-1847`
laisserait la CI verte.

Sous-constat : le message d'échec de l'assertion 1 est factuellement faux — `is_file()`
rend `True` parce que Windows retire le point des deux côtés.

### FLAG-STALE — le drapeau décrit l'état d'avant, la décision porte sur l'état d'après

`interviews.py:1822-1824` et `:1860`. `mission_absente` est un instantané pris avant
l'écriture, alors que la seule chose qu'il décide (quelle URL le client utilisera) dépend
de l'état d'après. Mission vivante à l'arrivée puis supprimée une seconde plus tard : le
client détient une URL qui rend 404, sans bandeau ni repli.

### D1-F2 — le verrou porte sur un bouton, son frère poste vers la même route

`record_libre.html:253-257`. `#rec-submit-force` (« Enregistrer quand même ») est un
`type="submit"` sans `formaction`, donc il poste vers la route qui rend 404, et aucune
ligne de JS ne le touche. C'est le défaut `D1-M1` lui-même, sur le chemin frère.

### D1-F3 — la branche import ne signale jamais `mission_absente`

`record_libre.html:1758-1766`, `record.html:1514-1524`. Ni le serveur ne l'émet sur
`/audio/transcribe-file/status`, ni le front ne l'appelle en fin d'import. Une mission
supprimée pendant un import de 1 h 30 laisse le bouton armé : le clic rend 404 et détruit
la transcription — exactement le scénario que D1 déclare fermer.

### D1-F4 — le bandeau promet un geste qui n'existe pas sur l'écran visé

`record.html:120-129`, `record_libre.html:138-147`. « le rattacher à une autre mission » :
l'écran « Audio sans mission » n'expose que `GET /audio-orphelin`, `GET
/audio-orphelin/{f}` et `POST /audio-orphelin/{f}/delete`. Le seul bouton d'action de la
ligne est la suppression définitive de la seule copie, sous une promesse de récupération.

### D1-F5 — le drapeau est réversible, le bandeau ne l'est pas

`record_libre.html:1419-1425`, `record.html:1194-1200`. `missionAbsente = false` réarme le
bouton pendant que le bandeau continue d'affirmer « ne peut plus être enregistré ».

**Preuve** — harnais Node :
```
tranche 4 : mission supprimee      bandeau = true | disabled = true
tranche 5 : mission_absente=false  bandeau = true | disabled = false
```

### ID-REUSE-LEAK — l'audio d'un client atterrit dans l'onglet Backup d'un autre

`interviews.py:1824` / `mission_backups.py:166-186`. La garde `_anterieur_a_la_mission` est
structurellement aveugle à une tranche écrite **après** la création de la mission qui a
hérité du numéro.

**Preuve** — E2E : `mission 1 supprimee ; nouvelle mission -> id=1 (reutilise ? True)` ;
`present dans l'onglet Backup de la mission du CLIENT B ? True` ; `GET backup cote client B
-> 200`.

**Préexistant** (comportement identique avant le diff). Signalé parce qu'il borne la
promesse du bandeau : l'audio peut être ailleurs, chez quelqu'un d'autre.

---

## MINEURS — NON ARBITRÉS, consignés

| id | Fichier | Constat |
| --- | --- | --- |
| `MP4-STOWAWAY` | `mission_backups.py:45` | `.mp4` passe de `audio/mp4` à `video/mp4` : passager clandestin, non nécessaire à D1-B1, contredit le docstring, 0 test. Rayon mesuré : 0 fichier concerné aujourd'hui |
| `CMT-8EXT` | `mission_backups.py:56-59` | Le commentaire dit « les 8 extensions ci-dessus » en désignant un dictionnaire que le même diff porte à 15 entrées |
| `MID-NEG` | `interviews.py:1824` | En retirant le 404, le diff ouvre la route à `mission_id <= 0` que le jumeau refuse en 400. L'écran affiche alors une raison **fausse** (« mission n° 0 supprimée ») |
| `PARTIAL-WRITE` | `interviews.py:1853-1859` | Sur échec en cours d'écriture, le fichier tronqué reste sur disque et le client ne reçoit jamais son nom. Préexistant |
| `TEST-HYGIENE` | `tests/test_mission_backups.py` | 4 fragilités : `status_code` hors du `try` (2×), `'target="_blank"' in source` correct par accident, `cursor: not-allowed` décorrélé de son sélecteur, tests de template dans un module de service |
| `CHURN` | `interviews.py:15,49-88,1716,2377` | Le diff mêle isort + `timezone.utc` → `UTC` à un correctif de sûreté. C'est `C15` à nouveau |
| `D1-F6` | `record_libre.html:567-575` | « Enregistrement terminé — prêt à envoyer. » s'affiche alors que le bouton vient d'être verrouillé |
| `D1-F7` | les deux templates | Le paragraphe « Les lignes deja construites… » est présent **deux fois de suite**, dans les deux templates, en deux rédactions qui se contredisent |
| `D1-F8` | `record_libre.html:2111-2142` | Ni « Recommencer » ni « Démarrer » ne remettent `missionAbsente`/`fichiersOrphelins` à zéro |
| `D1-F9` | `record_libre.html:207-210` | Le bandeau ordonne de cliquer un bouton qui vit dans `#tab-transcription`, masqué dès qu'on est sur l'onglet Répartition |
| `D1-F10` | `app.css:118-122` | `.btn:disabled:hover` ne neutralise que `border-color` : `.btn-danger:hover` continue de peindre le fond. Aucun bouton danger désactivé aujourd'hui |
| `D1-F11` | `app.css:118-119` vs `:127` | `.btn:disabled` (0,2,0) l'emporte sur `button.htmx-request` (0,1,1) : le curseur `wait` d'htmx est désarmé. Cosmétique |
| `D1-F12` | `record_libre.html:1262` vs `:1404` | `urlAudio` (hissée) déréférence une `var` déclarée 146 lignes plus bas. Sans effet aujourd'hui ; le premier appel de niveau supérieur lèverait un `TypeError` fatal qui couperait l'IIFE |

---

## Vérifications réelles jouées

- **pytest complet** : `1 failed, 683 passed in 393.15s`. L'échec est
  `test_export_pptx_renders_cleanly_in_a_real_engine` (LibreOffice, code 1, stdout et
  stderr vides). Rejoué seul : `1 passed in 17.66s`. Verrou de profil pendant les 6 min de
  suite ; le code PPTX n'est pas touché par le diff (diffstat vide).
- **Rendu navigateur réel** (Chrome headless — Edge échoue en `LoadEnclaveImageW 577`) sur
  la page RÉELLE servie par le serveur de dev : `.btn:disabled` résout en
  `opacity = 0.5`, `cursor = not-allowed`, `filter = grayscale(0.65)` ; `window.onerror
  captures = (aucune)`. La règle CSS ajoutée par le chantier est **vérifiée**, pas supposée.
- **Limite** : la sonde extérieure ne voit pas `signalerMissionAbsente` ni `urlAudio` — le
  script de page est une IIFE. Le comportement du bandeau est donc établi par harnais Node
  sur code extrait, pas par exécution navigateur.
- **Mutations** : les tests d'`est_media` sont discriminants (4 mutations injectées, 4
  détectées). Ceux du garde-fou B4 ne le sont pas sur Linux (`B4-TEST-CI`).

## Incident de séance — cause corrigée

Une commande shell terminée hors de la racine a verrouillé le shell de la session **et de
tous ses sous-agents** : 7 des 8 hooks de `.claude/settings.json` étaient déclarés en
chemin relatif, seul `remind_veille_agentic.py` utilisait `$CLAUDE_PROJECT_DIR`. Les 7 ont
été alignés sur ce motif (arbitrage utilisateur du 2026-09-02) ; le shell est reparti sans
redémarrage, `49 passed` sur les trois fichiers-contrat du dispositif.

Pendant le blocage, le hook `PostToolUse` `log_usage.py` a échoué : **l'usage des agents de
cette séance est partiellement absent de l'étage 1**.

---

# Seconde passe — les correctifs relus, et ce qu'ils avaient cassé

Les 10 constats arbitrés ont été appliqués, puis **relus par un troisième relecteur en
contexte frais**. Il a rendu **2 bloquants, 2 majeurs, 7 mineurs** — tous sur mes propres
correctifs. Septième itération d'affilée où un correctif introduit un défaut ; septième
fois que le gate l'attrape **avant** le commit.

## Les deux bloquants, et pourquoi ils étaient vrais

### D2-B2 — la prémisse du correctif D1-F1 était fausse

J'avais remplacé la carte par fichier (`fichiersOrphelins`) par le seul drapeau
`missionAbsente`, en argumentant que les deux routes sont mutuellement exclusives : mission
présente → route par mission pour tous les fichiers ; mission partie → route orpheline pour
tous. **C'est faux dans exactement le cas que D1-M3 invoquait** : la réutilisation
d'identifiant SQLite.

Mesuré : mission n° 2 supprimée, une nouvelle mission reçoit le n° 2, et l'ancienne tranche
rend `404` sur `get_record_backup` **et** `200` sur `get_audio_orphelin` — pendant que
`db.get(Mission, 2)` retrouve bien « une » mission. Mon drapeau repassait donc à `false` :
bandeau rétracté, bouton ré-armé, et le clic écrivait l'entretien d'un client dans la
mission d'un autre.

**Correctif** : le drapeau devient **monotone**. « La mission est revenue » n'existe pas —
ce que l'identifiant retrouve est une AUTRE mission, ce que le module affirme déjà ailleurs
(`_anterieur_a_la_mission`, cas 3 de `_raison_orphelin`). Ce que D1-F5 reprochait au drapeau
collant n'était pas d'être collant, c'était l'**incohérence** entre un bandeau figé et un
bouton ré-armé ; monotone, les deux disent la même chose.

### D2-B1 — le bandeau envoyait vers un écran où le fichier n'est pas

Pendant un import, `lister_orphelins_globaux` retient le fichier hors de l'inventaire tant
que le job n'est pas terminé (garde A5 : ne pas supprimer un fichier en cours de lecture).
Le bandeau ajouté par `D1-F3` envoyait donc l'utilisateur vers « Audio sans mission » où la
liste est vide et l'URL directe rend 404 — pendant 40 min à 1 h 30, et la fenêtre rouvre à
chaque « ↻ Relancer la transcription ».

**Correctif** : la promesse n'était pas fausse, elle était **prématurée** — le fichier
redevient listé dès que le job passe `done`/`failed` (vérifié dans le code de l'inventaire).
On le dit, avec une phrase qui ne s'affiche que dans cet état, plutôt que d'affaiblir A5.

## Les deux majeurs

| id | Constat | Traitement |
| --- | --- | --- |
| `D2-M1` | `error` sur un `<audio>` ne dit pas « 404 », il dit « je n'ai pas su jouer ça » — mesuré en navigateur : un conteneur non décodé et une route qui refuse rendent le **même** code (4). Le repli réécrivait donc le lien « Télécharger », qui fonctionnait, vers une URL en 404 : on cassait le dernier recours pour cause de codec manquant | Sonde `HEAD` via `recFetch` avant de basculer — on ne dégrade que vers du **prouvé**. Réseau muet, on ne touche à rien |
| `D2-M2` | Mon test de template laissait passer **cinq** mutations qui défont le correctif, dont la suppression pure et simple de `repointerLignesAudio` | Regex tolérante aux sauts de ligne pour l'URL en dur ; sites d'appel comptés **hors définition** ; ordre du gate vérifié ; `repointerLignesAudio` et le verrou explicitement assertés |

## Mineurs traités (les trois qui portaient sur mon propre code)

- `D2-m2` — en levant le 404, D1 avait rouvert la route aux `mission_id ≤ 0` que le jumeau
  refuse en 400. L'écran d'administration affichait alors une raison **fausse**. Garde-fou
  posé : c'est le seul cas de cette route où refuser ne détruit rien.
- `D2-m3` — `mission_id_du_fichier` acceptait `-5_`, `+5_`, `« 5_ »` et `007_` que les deux
  autres lecteurs du même préfixe refusent. Aligné, forme canonique comprise.
- `D2-m6` — `.mp4` remis en `audio/mp4` : le passage à `video/mp4` était un passager
  clandestin, sans rapport avec le filtre qu'il accompagnait, sans test, et contredisant le
  docstring de sa propre fonction.

## Mineurs NON traités, non arbitrés

`D2-m1` (la justification écrite de `repointerLignesAudio` reste inexacte : réaffecter `src`
coupe l'écoute autant que l'ancien `innerHTML = ''`), `D2-m4` (la docstring de `est_media`
laisse croire à un filtre média alors que `.exe`, `.pdf`, `.png` passent — l'asymétrie des
coûts est assumée, c'est la formulation qui trompe), `D2-m5` (l'attribut `accept` des deux
écrans ne propose pas `.audio`, `.opus`, `.mkv`… que la route d'import produit pourtant),
`D2-m7` (isort et `timezone.utc → UTC` mêlés au diff), plus les 13 mineurs de la première
passe.

## Vérifications de la seconde passe

- **Suite complète** : `693 passed`, 0 échec, 0 skip (deux passages consécutifs).
- **Mutations** : 10 sur 10 détectées à la première passe ; à la seconde, 4 mutations
  d'abord non détectées — dont **deux dues à mon propre encodage** (`replace(..., 1)`
  frappait le jumeau `transcribe_file`, et une mutation « déplacer » écrite comme un
  « dupliquer »). Une fois correctement encodées, toutes détectées, après ajout de deux
  tests qui manquaient (`D2-m2`, `D2-m3`).
- **Limite assumée** : injecter `if (true) return;` dans le corps du repli laisse le test
  vert. Un test de template par présence de chaîne ne peut pas voir du code mort injecté ;
  ce qu'il garde, c'est la disparition du mécanisme, qui est le mode de régression réel.
- **Navigateur réel** (sonde injectée DANS l'IIFE, sur la page servie par le serveur de
  dev) : import en cours → bandeau + avertissement d'attente + bouton verrouillé ; import
  terminé → avertissement retiré ; faux retour de mission → bandeau maintenu, bouton
  toujours verrouillé, `urlAudio` toujours sur l'orphelin. Identique sur les deux écrans.

## Un défaut de la suite de tests, préexistant et mesuré

Des tests de `test_mission_backups.py` échouent par intermittence en exécution partielle,
jamais les mêmes, et repassent seuls — le conftest documente déjà la cause (résidus dans
`RECORDINGS_DIR`, « un échec de ce genre ne se reproduit pas en isolant le test »). Mesuré
pour savoir si mes ajouts y contribuaient : **sans mes tests, c'est pire** — 1 à 3 échecs à
chacun des 4 passages, contre 0 à 1 avec. La suite complète, elle, est verte de bout en
bout. Le défaut préexiste et n'est pas traité ici.
