# Revue bmad-code-review — 6 constats du hub (unicité, fuites, reprise, lint) — 2026-09-10

Contexte : le hub VScode5 a fusionné 5 findings dans le diagnostic local sur
mandat utilisateur (« transmets aux projets dans leur TODO les problèmes de
niveau moyen »). 20 constats annoncés sur 4 dimensions d'audit. **Vérification
sur le code réel : 15 réellement ouverts**, 3 étant déjà fermés au moment de la
transmission et 2 l'ayant été par `c8450c2` le matin même — l'audit du hub avait
été rendu sur un code antérieur à ses propres commits.

Arbitrage utilisateur : je garde tout (le hub ne touche pas à `app/`), les 5
helpers `pptx_deck` sont retirés, le gate ruff devient bloquant.

Commits : `87880e3` (lint), `2629ea0` (pptx), `7355e29` (constats d'audit),
`36069df` (CI). Suite finale **960 passed**, export PPT réel rendu et inspecté.

## Ce qui a été fermé

| dimension | constats | statut |
| --- | --- | --- |
| robustesse | 4 | 3 déjà fermés (vérifiés), 1 traité — unicité des tranches |
| sécurité | 2 | 1 déjà fermé (vérifié), 1 traité — messages fixes sur 10 sites |
| risque technique | 5 | 1 déjà faux (vérifié), 4 traités |
| performance | 4 | **aucun** — voir « Reste ouvert » |

## Quatre rondes de revue adversariale sur MES correctifs

Le fait marquant de ce chantier n'est pas ce que le hub avait trouvé, mais ce que
la revue a trouvé dans mes propres correctifs : **3 bloquants et 13 majeurs**.

| ronde | bloquants | ce qu'ils étaient |
| --- | --- | --- |
| 1 | 2 | Le gate CI invoquait un `ruff` que la CI n'installe pas ; ma route « idempotente » en SELECT-puis-INSERT transformait une course en 500 **avec perte de 5 min d'entretien** — pire que le défaut qu'elle corrigeait |
| 2 | 1 | Mon 409 « soumettez le complément à la suivante » est illisible par le client (`if (!res.ok) throw`) : il relançait sur `P+1` avec le préfixe, et `merge_segment_turns` concatène sans dédoublonner — je troquais un doublon bloqué par la contrainte contre un doublon qu'elle ne voit pas |
| 3 | 0 | 4 majeurs, dont une garde posée sur 1 chemin d'écriture sur 3, un `persistDraft()` avant les mutations (fragment de parole rescapé écrasé), un `turns_result` effacé avant qu'un nouveau existe |
| 4 | 0 | 1 majeur : mes correctifs avaient rompu une équivalence dont 3 consommateurs en aval dépendaient — une tranche re-soumise était prise pour aboutie, ni relancée ni signalée |

Deux de mes affirmations écrites dans le code étaient **factuellement fausses** et
ont été rectifiées : « dernier résidu du constat message d'exception brut » (5
sites frères restaient ouverts) et « la relance JS crée une seconde tranche à la
même position » (elle mine `P+1`, vérifié dans `record_libre.html`).

Deux incidents de méthode, à ma charge :
- une découpe de source **par index de lignes** a décapité un `@router.get` : la
  route `segment-jobs/status` rendait 404. Seule la **suite complète** l'a vu, le
  fichier de test isolé ne l'appelle pas ;
- j'ai édité `app/` **pendant** qu'une suite complète tournait, ce qui a fabriqué
  2 échecs fantômes (`empreinte_code()` hache les mtimes).

## Reste ouvert

| id | sujet | pourquoi ouvert |
| --- | --- | --- |
| O1 | **4 constats de performance** : N+1 systémique (zéro `selectinload` dans tout `app/`), `_chunk_blocks` sans borne de bloc surdimensionné, `list_missions` sans pagination + `lister_orphelins_globaux` à chaque affichage, fetch photo en série dans une route synchrone | non traités, hors du lot du jour |
| O2 | `.claude/skills/pptx-deck/` garde les 5 helpers retirés et les **documente** (`SKILL.md:56,62`) | la copie se déclare source de vérité du hub (« corriger ICI, jamais dans une copie de projet ») ; divergence passée de ~23 à ~157 lignes. À porter au canon puis resynchroniser — **pas ici** |
| O3 | `ruff` n'était installé dans **aucun** requirements ; `\|\| true` avalait le « No module named ruff » | corrigé ici, mais **à vérifier sur les 4 autres cibles** : même câblage probable, donc même angle mort depuis le 2026-07-23 |
| O4 | `ai_common` (`_friendly`, `AIError(f"Erreur Ollama : …")`) et les 2 sites `openhub_agents` interpolent encore un texte système rendu à l'écran | hors périmètre de l'audit du 09/09 — **non traités, pas clos** |
| O5 | Le doublon **inter-positions** que produit la relance JS n'est traité nulle part : `merge_segment_turns` concatène sans dédoublonner | la contrainte d'unicité ne le voit pas. Nommé dans `models.py`, non corrigé |
| O6 | `_get_or_create_answer` porte le même lire-puis-écrire, sur la table la plus écrite du produit | différé explicitement (arbitrage du jour) : le même motif vient de coûter 4 rondes de revue sur un chemin moins chaud |
| O7 | `tests/test_e2e_premiers_clics.py` non étendu alors que `POST /interviews/segment-jobs` est réécrite | P5 partiellement honoré : le parcours navigateur qui exerce cette route (`test_e2e_enregistrement_libre.py`) passe, mais le clic touché n'y est pas ajouté |
| O8 | Une ligne peut désormais être `pending` en portant un `turns_result` périmé | c'est le prix de ne pas jeter un appel IA déjà payé. Les 2 prédicats de finalisation sont alignés sur `status == "done"` et un test les épingle — mais l'invariant implicite « porte un résultat = a abouti » n'existe plus, et tout code futur doit le savoir |
| O9 | CI **non vérifiée** après le push de `36069df` | `git credential fill` attend une saisie interactive sur ce poste ; le hook `check_ci_after_push.py` n'a rien remonté. C'est le premier push avec un gate lint BLOQUANT : à regarder |

## Blocage levé au passage (hors constats du hub)

`websockets` 16.0 coupait la session CDP en vol (ping 20 s sans pong) :
`test_le_tour_de_table_se_rejoue_autant_de_fois_que_necessaire` échouait **2 fois
sur 2**. Avec `ping_interval=None`, 6 passed — et la suite complète passe de
22 min à 9 min. Commit `902dc0b`.

**Le mécanisme reste non prouvé** et c'est écrit dans le code : un dépassement de
keepalive poserait `close_sent` et produirait un autre message que celui observé.
Deux pistes ouvertes y sont nommées (durée d'échec supérieure au succès ;
`max_queue=16` mettant le transport en pause). Trancher demanderait un run avec
`logging.getLogger("websockets")` en DEBUG.
