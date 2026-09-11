# Cadrage — les trois gardes du 2026-09-10 (Périmètre, plafond de lot, verrou pytest)

Ce document est le cadrage du chantier *et* le registre de triage de la revue
adversariale du 2026-09-10. Il ne se clôt pas en conversation : chaque ligne de
la section 4 se coche ici.

**Statut au 2026-09-11 : les 6 findings de gravité HAUTE sont corrigés**
(T1 à T6), chacun avec son test de régression prouvé échouant sur le code
d'avant (P1). Périmètre arbitré par l'utilisateur : « les 6 hauts, le reste
différé au registre », livré en trois commits scopés.
**Statut au 2026-09-11, fin de chantier : TOUS les points sont clos.** T1–T6
(hauts), T7–T16 (moyens et bas), T17–T19 (différés motivés), les rondes 2 et 3
de revue, et T18 — le seul qui exigeait d'écrire dans un autre dépôt — porté
dans VSCode3 (`f50f111`). Ce fichier reste la trace du chantier : il dit ce qui
a été corrigé, ce qui a été écarté et pourquoi, et ce que chaque ronde de revue
a coûté. Ne pas le supprimer.

## 1. Le problème d'origine

Une séance du 2026-09-10 a fait tourner **quatre rondes** de revue adversariale
sur des correctifs fraîchement écrits ; elles y ont trouvé **3 bloquants et
13 majeurs**. Le superviseur a refusé d'en conclure « ajoutons une 5e ronde » :
la revue avait parfaitement fonctionné, et c'est précisément le problème — elle
a fait, à 4× le prix et APRÈS coup, un travail mécanique qui coûtait un `grep`
AVANT d'écrire.

Deux causes ont été nommées, chacune mesurée :

1. **Le périmètre n'est jamais énuméré avant d'écrire.** Forme commune de la
   majorité des défauts du jour : « garde posée sur 1 chemin d'écriture sur 3 »,
   « équivalence rompue dont 3 consommateurs dépendaient », « dernier résidu »
   démenti par 5 sites frères. La leçon existait déjà en mémoire — elle ne tient
   pas, parce qu'elle dépend d'une vigilance et non d'une commande.
2. **Le lot mélange les sujets.** Un commit de 16 fichiers / 1841 insertions
   portant 4 dimensions, le lendemain d'un triage relevant déjà « commit non
   scopé ». Le profil 2 → 1 → 0 → 0 bloquants ne dit pas « la revue a convergé »,
   il dit « le lot était trop gros pour être revu en un passage ».

## 2. Ce qui a été décidé (arbitrages utilisateur du 2026-09-10)

| # | Garde | Intention | Régime |
| --- | --- | --- | --- |
| G1 | **Périmètre** | Avertir quand un commit pose une garde alors que sa forme NUE subsiste chez des fichiers frères | Non bloquante, **opt-in** |
| G2 | **Plafond de lot** | Avertir au-delà d'un nombre de fichiers jugé non revuable en un passage | Non bloquante, **opt-in** |
| G3 | **Verrou pytest** | Refuser VITE un second pytest sur la base partagée, au lieu d'une cascade `WinError 32` | Actif, deux portes de sortie |
| G4 | **Empreinte par contenu** | `empreinte_code()` hache le CONTENU et non le `mtime_ns` | Actif |

## 3. Les invariants — ce qu'aucun correctif ne doit casser

- **I1 — Rien ne s'hérite.** Le hook est publié verbatim dans cinq dépôts.
  VSCode1/3/4 n'ont rien arbitré : ils ne doivent voir NI G1 NI G2. L'opt-in est
  porté par la configuration JSON locale, jamais par une valeur en dur.
- **I2 — Aucune garde ne bloque.** Un garde-fou qui empêche de livrer se fait
  débrancher la semaine suivante. Un garde-fou muet ne sert à rien non plus
  (leçon du hook voisin, muet sept semaines).
- **I3 — Un garde-fou neuf ne désarme jamais un garde-fou existant.** Les trois
  avertissements préexistants survivent à toute panne des nouveaux (fail-open).
- **I4 — Le verrou ne survit jamais à son propriétaire.** Un verrou orphelin est
  pire que pas de verrou : il faut alors le supprimer à la main, et personne ne
  sait où il est.
- **I5 — Le spécifique va dans le JSON, le générique dans le `.py`.** C'est le
  contrat écrit en tête du fichier lui-même.
- **I6 — P1 tient.** Tout test de régression doit ÉCHOUER sur le code d'avant.

## 4. Registre de triage — revue adversariale du 2026-09-10 (2e ronde)

Trois couches : Blind Hunter, Edge Case Hunter, Verification Gap.
Acceptance Auditor écarté (`review_mode = no-spec`).
Vérifications faites au code par le triage, pas sur parole.

### À corriger — gravité haute

- [x] **T1 (corrigé 2026-09-11) — Le verrou se fait VOLER dans la course qu'il prétend fermer.**
  `tests/conftest.py:141-152`. Deux runners démarrant sans verrou existant
  passent tous deux `_VERROU.exists()`. L'un gagne `open(..., "x")` ; l'autre
  attrape `FileExistsError` et **écrase sans relire**. Les deux suites tournent :
  la cascade `WinError 32` revient. Au passage, `_rendre_le_verrou` du gagnant ne
  nettoie plus rien (PID différent) → verrou orphelin 45 min, ce qui casse aussi
  **I4**. Le commentaire affirme « le perdant retombe sur le chemin de refus en
  relisant le fichier du gagnant » : **c'est faux**, il n'y a aucune relecture.
  *Correctif* : dans la branche `FileExistsError`, relire et ré-appliquer la
  décision vivant/périmé (boucle courte), jamais écrire d'autorité.

- [x] **T2 (corrigé 2026-09-11) — La garde Périmètre désarme elle-même, et ce commit-ci le prouve.**
  `.claude/hooks/warn_verif_before_commit.py:697-699`. `ligne_presente` cherche
  `perimetre:` dans `message_commit + tout le diff ajouté`. Or le diff ajoute
  `_LIGNE_PERIMETRE = "Périmètre:"` (ligne 486). Tout commit touchant ce hook —
  celui-ci compris — éteint la garde pour l'ensemble du lot. N'importe quel
  commentaire français « périmètre : » dans un fichier sans rapport fait pareil.
  *Correctif* : ancrer la recherche au message de commit, et si l'annotation
  en code est conservée, l'ancrer en début de ligne ET dans un fichier du
  périmètre surveillé.

- [x] **T3 (corrigé 2026-09-11) — La garde Périmètre est aveugle au cas qui l'a fait naître.**
  `.claude/hooks/warn_verif_before_commit.py:589-599`. `_freres_nus` exclut tous
  les fichiers touchés par le commit. Or le défaut fondateur — « garde posée sur
  1 chemin d'écriture sur 3 » — vit typiquement dans le MÊME fichier. La garde se
  tait exactement là où on l'a construite pour parler.
  *Correctif* : compter les occurrences résiduelles À L'INTÉRIEUR des fichiers
  touchés plutôt que de les exclure en bloc.

- [x] **T4 (corrigé 2026-09-11) — Le test de régression de B1 passe sur le code NON corrigé (viole I6/P1).**
  `tests/test_hooks_discipline.py:434`. Le test n'assert que `returncode == 0` et
  un contexte non vide — satisfait par l'avertissement « vérif réelle »
  préexistant, quel que soit le retour de `_diff_ajoute`. Retirer
  `encoding="utf-8"` laisse le test VERT pendant que la garde devient muette sur
  tout diff portant un emoji à sélecteur de variante (des centaines de fichiers ici).
  *Correctif* : combiner la fixture emoji avec `_depot_avec_frere_nu` et asserter
  que le frère est NOMMÉ.

- [x] **T5 (corrigé 2026-09-11) — L'opt-in est asserté sur deux constantes, pas sur le chargeur qui décide (viole I1).**
  `tests/test_hooks_discipline.py:390`. Le test lit `_DEFAULT_PERIMETRE_ENABLED`
  et `_DEFAULT_PLAFOND_LOT` sans jamais appeler `_load_gardes_config`. Faire de
  `cfg.get("perimetre_enabled", True)` le défaut laisse le test vert pendant que
  les quatre dépôts frères héritent des deux signaux — le défaut exact que M11
  visait.
  *Correctif* : pointer `_config_path` sur un JSON temporaire SANS les clés et
  asserter `(False, 0)`.

- [x] **T6 (corrigé 2026-09-11) — La moitié PRODUCTRICE du verrou n'est exercée par aucun test.**
  `tests/test_conftest_verrou.py`. Les trois tests en sous-processus écrivent
  eux-mêmes le fichier de verrou avant de lancer l'enfant ; aucun n'assert que
  conftest en a créé un, ni qu'il a été RENDU. Supprimer tout le bloc de création
  (ou le `atexit.register`) laisse les quatre tests verts et la garde inerte.
  *Correctif* : un test qui lance un enfant sur base isolée, vérifie que le
  verrou existe pendant, et a disparu après.

### À corriger — gravité moyenne

- [x] **T7 (corrigé 2026-09-11) — `_staged_files` et `_diff_ajoute` ne s'accordent pas sur `-am`.**
  Hook `:302` teste `("-a", "--all")`, `:522` teste `("-a", "--all", "-am")`. Pour
  `git commit -am` sans rien de stagé, `files` est vide et `main()` sort avant
  toute garde. Le chemin `git commit -a` n'est couvert par aucun test.

- [x] **T8 (corrigé 2026-09-11) — Le plafond de lot aurait raté le commit qui le motive.**
  Hook `:704` ne compte que `watched` (`app/`), pas les tests, `.claude/` ni docs
  — soit exactement la forme d'un lot « 4 sujets ». Ce diff-ci : 9 fichiers,
  1 seul sous `app/`, aucun avertissement.

- [x] **T9 (corrigé 2026-09-11) — Deux constantes mortes, dont une fausse source de vérité.**
  Hook `:477` `_MOTS_D_EXHAUSTIVITE` et `:501` `_PLAFOND_FICHIERS_LOT = 6`, non
  référencées. La seconde duplique en dur le seuil que `_load_gardes_config` lit
  dans le JSON : qui l'édite ne change rien.

- [x] **T10 (déjà livré dans 0ad0ad6) — Le message de refus du verrou nomme la MAUVAISE base.**
  `tests/conftest.py:128` interpole `_TEST_DB` alors que le verrou dérive de
  `APP_DB_PATH` (correctif B4). Le runner qui a suivi la porte de sortie annoncée
  est celui à qui le message ment.

- [x] **T11 (corrigé 2026-09-11) — `empreinte_code` : deux affirmations fausses dans sa propre justification.**
  `app/main.py:66-69` annonce un appel « par `/__fraicheur` » : vérifié,
  `fraicheur()` retourne `EMPREINTE_AU_CHARGEMENT` et n'appelle jamais
  `empreinte_code()`. Le vrai second appelant est `scripts/serveur-dev.ps1`.
  L'ouverture « Le CONTENU, plus le `mtime_ns` » dit l'inverse du code (le mtime a
  été REMPLACÉ). Et la docstring de `fraicheur()` annonce encore « des mtimes
  hashés » alors que la route expose désormais un sha256 du contenu.

- [x] **T12 (corrigé 2026-09-11) — Un test mute un fichier source suivi.**
  `tests/test_regen_ui.py:347-361` écrit un marqueur dans `app/models.py`. La
  restauration est en `finally`, mais une interruption laisse le marqueur dans un
  module réel — et fait recharger le serveur `--reload` en cours. Ce mode de
  défaillance est déjà consigné en mémoire sur ce dépôt.

- [x] **T13 (corrigé 2026-09-11) — `_PAIRES_DE_GARDE` est spécifique à VSCode2, dans un fichier publié verbatim dans 5 dépôts (viole I5).**
  `lire_upload_borne`, `ecrire_audio_borne`, `verifier_zip_borne` n'existent
  qu'ici. Le travail fait à côté sur l'opt-in respectait le contrat ; ce tableau
  l'enfreint. Deux paires partagent en outre la forme nue `await file.read()` :
  un commit posant les deux bornes imprime deux blocs identiques.

### Gravité basse

- [x] **T14 (corrigé 2026-09-11)** — Verrou non idempotent pour son propre PID (`if pid == os.getpid(): return`) ; PID recyclé par Windows non départagé ; workers `pytest-xdist` refusés les uns par les autres.
- [x] **T15 (corrigé 2026-09-11)** — `PYTHON = .venv/Scripts/python.exe` + `skipif` : 3 tests sur 4 se taisent hors Windows. `sys.executable` est le bon interpréteur par construction.
- [x] **T16 (déjà livré dans 0ad0ad6)** — `sys.path.insert(0, tests/)` jamais retiré (`monkeypatch.syspath_prepend`).

### Différés, avec leur raison

- [x] **T17 — Branche « fichier illisible » d'`empreinte_code` non testée.** Différé : garde-fou étroit sur un chemin de diagnostic dev ; rendre un fichier illisible de façon portable coûte plus que le risque. À rouvrir si l'empreinte passe sur un chemin utilisateur.
- [x] **T18 — Le hook est édité LOCALEMENT alors que sa source vit au hub.**
  **PORTÉ le 2026-09-11** dans VSCode3, la source déclarée par
  `export_agentic.GENERIQUE` — commit `f50f111`, scopé à ce seul fichier,
  504 → 861 lignes. **Non poussé** : VSCode3 avait 2 commits d'avance qui ne sont
  pas les nôtres, et publier le travail d'une autre session ne se décide pas ici.
  La propagation du kit vers les cinq dépôts reste à la main du hub.

  Vérifié IN SITU avant le commit, ce qui est le seul contrôle qui compte pour la
  flotte : avec la configuration réelle de VSCode3
  (`watched_prefixes: ["docs/cadrage-ppt/"]`, aucune des trois clés neuves), les
  deux gardes sont **inertes** — `perimetre_enabled` False, `plafond_lot` 0,
  `paires_de_garde` vide — et l'avertissement dont VSCode3 dépend part toujours
  en citant son propre périmètre. L'invariant I1 n'est donc pas une intention :
  il est mesuré chez la cible.

  Le hook était portable tel quel grâce à T13 : tout le spécifique était déjà
  passé en configuration. Ne restaient que des commentaires d'historique et un
  `_DEFAULT_WATCHED_PREFIXES = ("app/",)` préexistant.

  Deux choses ont retardé ce portage, et elles valent d'être notées : le hub
  avait annoncé le prendre (double écriture évitée en le prévenant), et
  l'écriture inter-dépôts a d'abord été **refusée par les permissions** — elle
  n'a été faite qu'après arbitrage explicite de l'utilisateur. Une session tierce
  travaillait chez VSCode3 sur un chantier sans recoupement ; elle a été
  prévenue avant l'écriture, pas après.
  Mesure faite avant l'envoi : VSCode2 porte 817 lignes, VSCode3 (la source
  déclarée par l'en-tête du fichier, via `export_agentic.GENERIQUE`) 504 —
  375 lignes d'écart, et **aucune** des deux gardes n'y existe. Ni
  `export_agentic` ni `scripts/scan_projets.py` ne sont présents sur cette
  machine : le hub n'y est pas, donc la publication ne peut pas se faire d'ici.
  Message envoyé à la session `vscode5-supervision-projets-e5` avec les trois
  éléments à canoniser (garde Périmètre, plafond de lot, et `_commit_prend_tout`
  — ce dernier vaut pour les cinq dépôts indépendamment des gardes) et les trois
  invariants à tenir (défauts neutres, paires en configuration, non bloquantes).
  **À rouvrir à chaque synchronisation du canon tant que la réponse n'est pas là.**
- [x] **T19 — `test_les_gardes_restent_NON_BLOQUANTES` est satisfait par un hook totalement muet.** Différé : ses tests frères couvrent l'avertissement ; note, pas trou.

## 4 bis. Ronde 2 — ce que les correctifs de T7-T16 avaient cassé

Revue rejouée sur le diff des correctifs eux-mêmes, le 2026-09-11. Elle a
trouvé **trois affirmations fausses et deux correctifs inopérants**, tous
introduits par la ronde précédente. Le motif est constant sur ce dépôt : c'est
la correction d'une revue qui fabrique le défaut suivant.

- [x] **R2-1 — T8 ne marchait pas, et son commentaire affirmait le contraire.**
  Le plafond de lot était évalué APRÈS la sortie « rien sous un périmètre
  surveillé » : un lot de docs et de tests — la forme même qu'on veut découper —
  passait sans un mot. Mesuré : 10 fichiers sous `docs/`, zéro avertissement.
  Le plafond est remonté avant cette sortie, `main()` a désormais deux sorties
  et un `_emettre()` commun.
- [x] **R2-2 — La garde se déclenchait sur le fichier qui DÉCLARE les formes
  gardées.** Le diff était lu en entier, or le JSON de configuration contient
  les six littéraux : committer cette configuration produisait cinq blocs
  fantômes — sur le commit même qui introduisait la clé. Le diff est maintenant
  restreint aux chemins surveillés (`git diff -- <prefixes>`).
- [x] **R2-3 — La preuve de sensibilité au CONTENU avait été perdue.** En
  passant d'une mutation de `app/models.py` (T12) à la création d'un fichier
  neuf, le test restait vert même en supprimant `h.update(p.read_bytes())` :
  l'ajout d'un chemin suffisait à bouger l'empreinte. Le test réécrit
  maintenant le fichier jetable EN PLACE, à liste de chemins constante.
- [x] **R2-4 — `_commit_prend_tout` se trompait dans les deux sens.** `-uall` et
  `-Sabc` étaient pris pour `--all` (`u` et `S` manquaient à
  `_COURTS_AVEC_VALEUR`), et une valeur séparée commençant par un tiret
  (`-m "-analyse du lot"`) était relue comme un groupe de drapeaux.
- [x] **R2-5 — La sortie xdist ouvrait un trou.** `PYTEST_XDIST_WORKER` s'hérite
  par l'environnement : un pytest lancé depuis un worker tournait donc SANS
  verrou. La garde exige désormais que le module `xdist` soit réellement chargé.
- [x] **R2-6 — Deux justifications fausses.** « `lire_upload_borne` est un
  préfixe de `lire_upload_audio_borne` » : `in` vaut `False`. Et « trois tests
  sur quatre se taisaient » : `git show HEAD` donne 6 `def test_` et 4 `skipif`.
- [x] **R2-7 — Un résidu de test rendait le test rouge à perpétuité**, au lieu
  d'être simplement supprimé ; une entrée de configuration réduite à un espace
  passait la validation et aurait fait parler la garde à chaque commit.

Et une leçon de méthode, payée ici : **le premier test de R2-2 passait pour une
mauvaise raison**. Il ne mettait aucun fichier surveillé dans le commit, donc le
hook sortait avant d'évaluer la moindre garde. C'est la preuve P1 — le jouer
contre le code d'avant — qui l'a révélé : le test passait AUSSI sans le
correctif. Un test qui ne peut pas échouer ne prouve rien, et seule la mutation
le dit.

## 4 ter. Ronde 3 — l'outil anti-récidive portait le défaut qu'il traque

`scripts/preuve_p1.py` a été écrit pour que le défaut des rondes 1 et 2 ne
puisse plus revenir : il outille la preuve P1, dont la version artisanale avait
produit deux faux négatifs et laissé passer un test qui passait des deux côtés.
Sa revue a rendu 31 constats. Le motif s'est répété une troisième fois : l'outil
contre les affirmations fausses en portait trois, et deux de ses tests passaient
pour une mauvaise raison.

**Corrigé :**

- [x] **R3-1 — Un code pytest non nul n'est pas un test rouge.** Une mutation
  cassant la syntaxe fait sortir pytest en 2 (collecte impossible) ; la première
  version le comptait comme « rouge » et certifiait donc une PREUVE TENUE à
  partir d'une erreur de syntaxe. Idem pour le code 5 (aucun test collecté :
  chemin faux ou `-k` sans correspondance). Seuls 0 et 1 sont des verdicts ; tout
  le reste rend un code d'OUTILLAGE distinct.
- [x] **R3-2 — Un test VOISIN qui tombe faisait conclure.** Quand un sélecteur
  est donné, l'outil exige désormais que l'échec porte sur lui.
- [x] **R3-3 — Une restauration ratée rendait 0.** Le verdict est maintenant
  écrasé par le code 6 : un dépôt resté sur le code d'avant avec un tampon de
  conformité serait le défaut n°3 aggravé.
- [x] **R3-4 — Corruption de fins de ligne.** Lecture/écriture en OCTETS : sur
  Windows, `write_text` réécrivait un fichier LF en CRLF, et le rouge aurait pu
  venir de là.
- [x] **R3-5 — Cible hors du dépôt, bloc AVANT vide, marqueur multi-ligne en
  ligne de commande, séparateur `---` ambigu** : tous refusés (le séparateur est
  devenu `---PREUVE-P1---`). Le mode « suppression du marqueur » a été retiré
  plutôt que documenté.
- [x] **R3-6 — Édition concurrente** entre la lecture et la mutation : détectée
  (code 8) au lieu d'être silencieusement écrasée. Les temporaires sont nettoyés.
- [x] **R3-7 — Trois affirmations fausses** dans mes propres commentaires, et une
  règle CLAUDE.md inapplicable (« jamais à la main » alors que l'outil ne traite
  qu'un marqueur d'un seul fichier). Corrigées, la règle dit maintenant son
  périmètre.
- [x] **R3-8 — Deux tests passaient pour une mauvaise raison** : celui de la
  restauration serait passé sur une erreur d'usage survenue avant toute écriture,
  celui du diagnostic n'assertait qu'un texte générique.
- [x] **R3-9 — La garde anti-sync passait sur un retrait PARTIEL** de clé, et
  levait un `AttributeError` opaque au lieu d'émettre ses instructions T18.

**Les quatre derniers, traités le 2026-09-11 sur demande utilisateur :**

- [x] **R3-10 — Un processus TUÉ pendant la fenêtre mutée laissait le fichier
  muté.** Le `finally` couvre les sorties normales et les exceptions, pas un
  `taskkill`. Une **sentinelle** (`%TEMP%/preuve_p1_interrompue.json`, chemin
  fixe donc retrouvable) est posée AVANT la mutation et retirée seulement après
  restauration vérifiée. La preuve suivante la voit et REFUSE de partir (code 9)
  en nommant la sauvegarde ; `--restaurer` remet le fichier. Elle ne restaure
  jamais d'elle-même : elle prévient d'abord. Une sentinelle périmée — la
  restauration avait eu lieu, seule la trace restait — est retirée sans bloquer.
- [x] **R3-11 — Un marqueur non aligné sur des lignes entières** pouvait tomber
  dans un commentaire ou au milieu d'une ligne et muter ailleurs que voulu.
  Refusé (code 3), avec `--fragment` pour l'assumer. L'outil imprime en plus les
  lignes exactes qu'il va muter, avec leur numéro.
- [x] **R3-12 — La garde anti-sync ne vérifiait pas que `main()` APPELLE encore
  les gardes** : un canon gardant les noms sans les câbler passait. Elle fait
  maintenant parler le hook pour de vrai, une fois par garde. Prouvé : débrancher
  l'appel du plafond la fait rougir.
- [x] **R3-13 — Le code 5 (mutation sans effet) n'était épinglé par aucun test.**

**Et un cinquième, trouvé en UTILISANT l'outil — pas en le relisant :**

- [x] **R3-14 — Un marqueur multi-lignes ne pouvait pas fonctionner sur ce
  dépôt.** L'arbre de travail est entièrement en CRLF (`core.autocrlf=true`) et
  l'outil lit en octets, sans traduction. Un marqueur tapé en LF ne correspondait
  donc jamais : « apparaît 0 fois », soit **exactement le faux négatif que
  l'outil existe pour empêcher**. Les marqueurs sont désormais réécrits aux fins
  de ligne du fichier cible, et `\r` compte comme fin de ligne dans le contrôle
  d'alignement.
  Les quatre preuves jouées jusque-là n'avaient tenu que parce que leurs
  marqueurs faisaient une seule ligne. Aucune revue ne l'avait vu ; c'est la
  première tentative de mutation multi-lignes sur un fichier réel qui l'a sorti.
  La leçon est la même que celle de tout ce chantier, appliquée à l'outillage :
  **un outil de vérification se vérifie en l'utilisant sur un cas réel**, pas en
  le relisant.

## 5. Ce que cette revue dit du chantier lui-même

Le chantier construit un outil contre « la garde posée sur un chemin, ses frères
oubliés » — et T2, T3, T5, T6 sont **exactement ce défaut, dans l'outil**. La
garde se désarme elle-même, ignore le cas fondateur, et ses tests valident des
constantes plutôt que le chemin qui décide.

Ce n'est pas un argument contre les gardes : c'est la démonstration que la revue
adversariale reste le gate, y compris — surtout — sur le code qui prétend la
rendre moins nécessaire. Le nombre de rondes ne baissera pas parce qu'on aura
écrit un hook ; il baissera quand le périmètre sera énuméré avant d'écrire.
