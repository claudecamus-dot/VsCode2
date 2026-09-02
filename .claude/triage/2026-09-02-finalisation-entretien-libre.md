# Triage — finalisation de l'entretien libre (2026-09-02)

Run `/orchestre` — playbook `dev-verifie`. Demande utilisateur en six points :
finaliser l'entretien libre, prouver qu'il est opérationnel, sans régression, avec
l'enregistrement audio présent par tranche, la transcription opérationnelle, et la
répartition Q/R au fil de l'eau qui fonctionne et qui est testée.

**Cadrage (R1)** : trois analyses en fan-out sur l'état RÉEL du code (audio par tranche,
transcription, répartition Q/R), croisées avec les 53 constats encore ouverts des trois
fichiers de triage précédents — dont les statuts écrits étaient connus pour être périmés,
donc re-vérifiés sur le code lui-même.

**Preuve** : `MESURE` = commande jouée, chiffre reproductible. `RENDU` = artefact réel
obtenu et regardé. `LECTURE` = établi par lecture de code, non rejoué.

---

## Ce qui a été prouvé opérationnel, en réel

Vérifications faites sur le serveur de développement lancé sur un port vierge, contre la
base de développement, avec Ollama et faster-whisper réellement sollicités.

| Exigence | Vérification | Résultat |
| --- | --- | --- |
| Audio présent par tranche | 3 tranches postées sur la route de sauvegarde, puis inventaire de mission et ré-écoute de chacune | MESURE — 3/3 listées, HTTP 200, 44 233 octets rendus intacts |
| Tranches sur la fiche d'entretien | Entretien enregistré avec ses trois tranches, fiche rechargée | MESURE — 3 lecteurs audio, libellé « par tranche de 20 min », les 3 noms présents |
| Transcription | Fichier audio porteur de vraie parole importé par la route d'import, statut poursuivi jusqu'à `done` | MESURE — texte transcrit exact, bloc par bloc |
| Répartition Q/R au fil de l'eau | 2 tranches de texte soumises successivement pendant que la précédente était traitée | MESURE — 2 tours après la 1ʳᵉ tranche, 4 après la 2ᵉ : la fusion est bien incrémentale |
| Export PDF de l'onglet Répartition | Route d'export appelée, PDF passé au vérificateur du projet | MESURE — verdict OK, 0 bloquant, une seule abscisse gauche, polices embarquées |
| Écran d'enregistrement libre | Capture de la page servie par le serveur | RENDU — les deux onglets présents, mise en page intacte |
| Non-régression | Suite complète avant et après | MESURE — 715 passed avant, 734 passed après (715 + 19 tests ajoutés), 0 failed, 0 skipped |

---

## BLOQUANT

Aucun.

---

## MAJEUR

| id | titre | fichier | preuve | statut |
| --- | --- | --- | --- | --- |
| L1 | Le modèle par défaut ne restitue AUCUNE question : l'onglet « Répartition (Q/R) » n'a jamais de Q | `app/services/ai_common.py` (`SYNTHESE_MODEL`) | MESURE | **ouvert — arbitrage utilisateur requis** |
| L2 | « Recommencer » ne vidait pas l'onglet Répartition : tours de la session jetée affichés ET exportables en PDF | `app/templates/interviews/record_libre.html` | MESURE (mutation) | **corrigé** |
| L3 | Une réponse de poll partie avant « Recommencer » réaffichait la session jetée — le vidage contourné par la course | `record_libre.html` + `record.html` | MESURE (mutation) | **corrigé sur les deux écrans** |
| L4 | Le bouton « Démarrer » jette la session tout autant que « Recommencer », sans vider l'onglet — le même défaut par son chemin frère | `record_libre.html` + `record.html` | LECTURE (revue adversariale) | **corrigé sur les deux écrans** |

### L1 — le détail, parce que c'est lui qui décide de « la répartition Q/R fonctionne bien »

Mesure du 2026-09-02 sur le chemin réel (`extract_turns_from_text`, retries compris),
trois tranches d'entretien typées, CPU local :

| modèle | durée totale | couverture moyenne du texte | tours portant une question |
| --- | --- | --- | --- |
| `qwen2.5:3b-instruct` (défaut actuel) | 166 s | 79 % | **0** |
| `llama3.1:8b` | 269 s | **100 %** | **9** |

Détail par cas — le premier est un dialogue question/réponse, le deuxième un monologue
(qui ne contient légitimement aucune question), le troisième un enchaînement dense de
questions courtes :

| cas | `qwen2.5:3b` | `llama3.1:8b` |
| --- | --- | --- |
| dialogue Q/R | 77 s · 45 % · 0 question | 80 s · 100 % · 4 questions |
| monologue | 40 s · 93 % · 0 question | 85 s · 100 % · 0 question |
| Q/R dense | 49 s · 100 % · 0 question | 105 s · 100 % · 5 questions |

Deux faits, distincts, tous deux mesurés :

1. **Aucune question n'est jamais étiquetée comme telle** par le modèle par défaut, sur
   les trois cas. Même quand tout le texte est conservé (cas Q/R dense, 100 %), les
   questions du consultant partent dans le champ `remarque`. L'onglet affiche donc bien
   du contenu, mais la structure question/réponse qu'il annonce est absente.
2. **Sur un dialogue, plus de la moitié du texte disparaît** (45 % conservé). La garde
   anti-perte du code se déclenche sous 35 % : 45 % passe juste au-dessus, sans relance.

L'arbitrage historique avait choisi ce modèle pour sa vitesse, sur un cas de monologue.
La mesure ci-dessus montre que l'écart de vitesse s'efface sur le cas qui compte : 77 s
contre 80 s sur le dialogue, parce que le modèle rapide y déclenche ses relances.

**Ce point n'a pas été corrigé** : changer `SYNTHESE_MODEL` affecte tous les usages IA du
produit, pas seulement la répartition — synthèse globale, extraction structurée,
recommandations. C'est une décision produit, elle revient à l'utilisateur (R4).

---

## MINEUR

| id | titre | fichier | preuve | statut |
| --- | --- | --- | --- | --- |
| L5 | Le libellé de progression n'était réécrit que sous `total > 0` : un `total` absent laissait celui de la session précédente | `record.html` | LECTURE (harnais de la revue) | **corrigé** |
| L6 | La boucle d'affichage repartait de `turns` brut alors que la normalisation portait sur la variable mémorisée | `record_libre.html` | LECTURE (revue) | **corrigé** |
| L7 | Une dizaine de commentaires annonçaient une rotation de 30 min là où le code tourne à 20 min | `record_libre.html`, `app/models.py` | MESURE (`BACKUP_SEGMENT_MS = 20 * 60000`) | **corrigé** |
| L8 | Les jobs restés `pending`/`running` ne sont pas repris au redémarrage du serveur | `app/main.py` | LECTURE | **ouvert, requalifié — pas de perte** |

### L8 — requalification

Signalé comme majeur par l'analyse de la transcription, il l'est moins qu'il n'en a l'air :
la finalisation de l'entretien retraite tout job non terminé, un par un. Un redémarrage
serveur en cours d'entretien ne perd donc aucun contenu ; il retarde son extraction
jusqu'au clic sur « Enregistrer », où elle redevient synchrone. C'est une dégradation de
confort, pas une perte. Le corriger (reprise au démarrage) est un choix de conception avec
un coût processeur au lancement — laissé à l'arbitrage plutôt qu'appliqué.

---

## Le trou de test comblé

Avant ce run, `renderRepartition`, `pollRepartition` et le vidage de l'onglet n'étaient
couverts par **aucune exécution** : les seuls signaux cherchaient des sous-chaînes dans le
HTML rendu, et restaient verts si la fonction affichait n'importe quoi. C'est exactement ce
qui a laissé L2 et L3 vivre dans le code.

`tests/test_repartition_live.py` (19 tests) extrait les fonctions et les gestionnaires du
template, et les **exécute réellement sous node** : ordre des tours, sections, échappement
de chaque champ affiché, état vide, vidage par les DEUX boutons qui jettent une session et
sur les DEUX écrans, course de la réponse en vol dans ses deux formes (jeton vide après
« Recommencer », jeton d'une nouvelle session après « Démarrer »), sa contrepartie (une
réponse légitime doit toujours s'afficher), la cadence du poll et sa sortie sans session.

### La première version de ces tests était trop faible — et la revue l'a prouvé

La revue adversariale a mis en défaut la première mouture en douze coups : le test du
vidage cherchait l'appel par expression régulière, donc mettre cet appel sous `if (false)`,
le commenter, le différer d'un `setTimeout` ou le faire précéder d'un `return` laissait la
suite verte. L'écran structuré, lui, n'avait aucun test d'exécution : masquer sa garde par
une variable locale la rendait constamment vraie sans rien casser.

Le fichier a donc été refondu : les gestionnaires sont **extraits du template puis
EXÉCUTÉS** sous node, dans un bac à sable où toute variable d'écran inconnue est une
doublure tolérante. Ce qui est vérifié est l'état de l'onglet après le clic, pas la
présence d'une ligne de code. Le gestionnaire de « Démarrer » a demandé une correction de
plus : sa remise à zéro ne vit pas dans le corps synchrone du gestionnaire mais dans la
suite de l'acquisition du micro — exécuter le corps synchrone n'aurait rien prouvé.

Les douze mutations ont été rejouées sur la version refondue : **douze détectées, zéro
faux négatif.**

| mutation | verdict |
| --- | --- |
| vidage sous `if (false)` | rouge |
| vidage commenté en bloc | rouge |
| vidage différé par `setTimeout` | rouge |
| `return` avant le vidage | rouge |
| garde masquée par une variable locale (structuré) | rouge |
| garde évaluée puis ignorée (structuré) | rouge |
| jeton figé après le départ de la requête (structuré) | rouge |
| garde acceptant tout jeton non vide (libre) | rouge |
| mémoire de l'export non vidée | rouge |
| cadence du poll portée à une heure (les deux écrans) | rouge |
| échappement d'un titre de section retiré | rouge |

---

## Ce qui reste ouvert, et pourquoi

- **L1** — arbitrage du modèle : c'est la seule chose qui empêche de dire que la
  répartition Q/R « fonctionne bien ». Attend une décision.
- **L8** — reprise des jobs au démarrage : choix de conception, sans perte de données.
- Les **51 constats** des trois triages précédents restent ouverts hors de ce périmètre
  (audio orphelin, mission absente, rattachement) ; deux d'entre eux, F9 et EC-6, ont été
  fermés par ce run et marqués comme tels dans leur fichier d'origine.

## Incident d'orchestration à retenir

Les deux relecteurs adversariaux ont été lancés en parallèle **pendant** que la session
principale éditait encore les mêmes templates. L'un d'eux a testé des mutations et a
restauré les fichiers à leur version d'origine, effaçant les correctifs en cours. Ils ont
été reconstruits depuis des copies hors dépôt, sans perte. La règle du dispositif — deux
écrivains ne travaillent jamais sur les mêmes fichiers en parallèle — vaut aussi quand le
second « écrivain » est un relecteur censé ne rien modifier : une revue qui teste des
mutations écrit.
