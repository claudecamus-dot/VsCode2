# Référentiel de critères — pratiques de la flotte

Établi le 2026-07-23 sur investigation des référentiels faisant autorité (sources en fin
de section). C'est la **cible d'amélioration** du dispositif : chaque domaine liste ses
critères de référence, ce que la flotte **mesure déjà** (scan déterministe ou audit), et
les **écarts à outiller** — qui alimenteront les prochains findings du superviseur.

Légende : ✅ mesuré par le scan déterministe · 🔍 couvert par l'audit qualitatif ·
⬜ non mesuré aujourd'hui (cible d'amélioration).

**Ce document et la table `CRAFT_PRATIQUES` de `scripts/scan_projets.py` mesurent deux
choses différentes, et c'est ce qui les a fait diverger** (finding
`referentiel:deux-sources-qui-se-contredisent`, revue du 2026-08-31) : ici on répond
« ce critère est-il OUTILLÉ ? » (✅/🔍/⬜) ; là-bas on répond « quel est l'ÉTAT de la
flotte sur ce critère ? » (ok/moyen/absent). Deux axes orthogonaux qui se contredisent
dès que l'un est mis à jour sans l'autre. Règle : **un ✅ posé ici nomme la fonction qui
le mesure** — sans nom de fonction, il n'est pas gagné.

---

## 1. Pratiques de développement — référentiel : DORA capabilities

Les capacités techniques du programme DORA (Google Cloud), corrélées empiriquement à la
performance de livraison (déploiement fréquent, lead time court, faible taux d'échec).

| Critère | Mesure flotte |
| --- | --- |
| Gestion de version pour tout (code, config, scripts) | ✅ (repo git + cadence du dernier commit + **dette non commitée par `git status --porcelain` sur les 6 dépôts**, outillée le 2026-07-30 : la ligne annonçait cette mesure depuis le 2026-07-23 sans qu'elle existe ailleurs que sur le hub) |
| Linter/analyse statique configuré et exécuté | ✅ (dimension pratiques+rules : ruff/ESLint) |
| Intégration continue (build+tests à chaque push) | ✅ (workflow `.github/workflows/` présent — **mesuré le 2026-07-30 : 5/6**, seul VSCode3 n'en a pas. La mention « seule VSCode1 l'a » datait du 2026-07-23 et était fausse depuis : VSCode, VSCode2, VSCode4 et le hub en ont acquis une entre-temps) |
| Automatisation du déploiement | ⬜ (aucun projet n'a de déploiement outillé — pertinence à évaluer, projets locaux) |
| Trunk-based development (branches courtes, < 3 actives) | ✅ `git_etat()` (`scan_projets.py:159`, comptage `git branch` l. 184-189 ; seuil DORA < 3 appliqué au rendu) — outillé le 2026-07-30 après 7 jours de ⬜ « à ajouter au scan », **mesuré : 6/6 dépôts à une seule branche `main`** |
| Revue de code systématique avant merge/commit | ✅ (dimension revue : agent reviewer/hook pré-commit) |
| Dépendances épinglées / build reproductible | 🔍 (audit risque technique — constat : VSCode2 tout en `>=`, lockfile OK sur VSCode1) |
| Documentation du code à jour (voir § 3) | ✅ partiel |
| Rules/conventions explicites (CLAUDE.md, conventions.md) | ✅ |
| Discipline de contexte/tokens documentée (`/compact` cadré, sous-agents d'exploration, lecture ciblée) | ✅ (titre de section dans CLAUDE.md/conventions — adopté le 2026-07-30 depuis la veille du 2026-07-24 ; mesuré : 5/6, seul le hub ne l'a pas écrite) |
| — critère `/clear` (pas seulement `/compact`) dans cette même section, après deux corrections ratées consécutives | ✅ `discipline_clear_critere()` (`scan_projets.py`, extension de `discipline_tokens` à son contenu) — adopté le 2026-09-04 depuis la veille du 2026-09-02 (guide « Coder avec Claude », ch. 7) ; informatif seul, n'entre pas dans le score composé `pratiques` pour ne pas rouvrir sans arbitrage le seuil de niveau des 6 projets déjà mesurés. Non encore re-mesuré flotte-wide (hors périmètre de cette adoption, scopée hub) |

| Aucune étape de vérification en CI ne se termine par `\|\| true` ni `continue-on-error: true` — **sauf** si une garde du dépôt barre vraiment à sa place | ⬜ (mesurable à froid par grep sur `.github/workflows/*.yml` — **non outillé dans le scan ce jour** : le critère brut donnerait un faux positif sur VSCode4 et sur le hub, où le `\|\| true` est délibérément informatif et adossé à `tests/test_lint_baseline.py`. Un détecteur honnête doit croiser les deux, il reste à écrire. Adopté le 2026-09-19) |
| Tout workflow qui lance des tests publie un artefact d'exécution (`actions/upload-artifact` + `if-no-files-found: error`) — un vert sans artefact publié compte comme non exécuté | ⬜ (mesurable à froid par grep sur `.github/workflows/*.yml` — non outillé ce jour. Mesuré le 2026-09-19 : **0 occurrence sur les 8 dépôts** ; appliqué au hub le jour même, donc 1/8 désormais) |
| Baseline de lint versionnée qui barre dans les DEUX SENS (hausse = régression, baisse = dette payée non inscrite) | ✅ au hub et sur VSCode4 (`tests/test_lint_baseline.py`) — ⬜ ailleurs, et **non mesuré par le scan** : c'est la présence d'un test nommé, pas une propriété du dépôt. Porté de VSCode4 au hub le 2026-09-19 |
| Famille `PT` (flake8-pytest-style) dans le `select` ruff | ⬜ (mesurable à froid — lecture de `[tool.ruff.lint].select` dans chaque `pyproject.toml` ; **non outillé** : adoption scopée au hub, les 3 autres `pyproject.toml` n'ont pas été touchés. Portée honnête : **aucune** règle PT n'attrape nos gardes vertes par construction, qui sont sémantiques ; PT couvre la classe triviale, pas la nôtre) |
| Filet de sécurité atteint depuis un point d'entrée réel (`vulture --min-confidence 100`, whitelist **générée** depuis `settings.json`) | ⬜ — **délibérément non outillé** : instruction faite au hub le 2026-09-19 (VSCode2 étant occupé), taux de faux positifs mesuré **5/9 = 56 %** à `--min-confidence 60`, et **0 remontée** à `--min-confidence 100` (donc 0 % de FP mais 0 % d'utilité comme gate). Un critère à 56 % de bruit ne se câble pas ; l'outil reste utile **à la main**, il a trouvé le jour même 2 vraies morts dans `canon/log_run.py` |
| Configuration par variables d'environnement, jamais en dur dans le code (Twelve-Factor, facteur III) — la séparation configuration / secrets est ce qui compte | ⬜ **non outillée** : un grep à froid de motifs (`localhost:`, `http://`, chemins absolus) produirait des faux positifs en nombre, la trouvaille le dit elle-même. Adopté le 2026-09-26. **Action proposée NON appliquée** : retirer les `.env.dev`/`.env.preprod`/`.env.prod` de VSCode1 — ils y sont versionnés **à dessein** (`app/.gitignore` l. 15-29 : paramétrage sans secret, secrets dans des `.env.*.local` ignorés, `.env.example` présent, garde-fou `scripts/test-env-sans-secret.js` dans `npm test`) : c'est déjà la séparation configuration / secrets |
| Petits changements : commits sous 400 lignes changées (Google eng-practices, « Small CLs ») | ✅ `commits_hors_gabarit()` (`git log --numstat` sur les 20 derniers commits, seuil > 400 lignes ajoutées + supprimées), affiché dans Pratiques + rules, hors notation. **Mesuré le 2026-09-26 : 1 à 3 commits hors gabarit sur 20 dans chacun des 8 dépôts.** Étape `decoupage-atomique` ajoutée au playbook `evolution-flotte` ; seuil documenté dans les `conventions.md` de VSCode1 (`b8c2d6e`), VSCode2 (`47aaed7`), VSCode3 (`2a31808`), VSCode4 (`a671774`) |
| Typage statique : `[tool.mypy]` ou `[tool.pyright]` avec une cible non vide | ✅ `typage_statique()` (lecture de `pyproject.toml`), affiché, hors notation. **Mesuré le 2026-09-26 : 0/8 avant, 1/8 après** (le hub : `[tool.mypy]` sur `scripts/` et `.claude/dispositif/canon/`, `ignore_missing_imports`, **pas une gate** — première passe `py -m mypy` : 48 erreurs dans 5 fichiers, non corrigées). VSCode4 reste à évaluer |

**Écarts à outiller** : fraîcheur des dépendances (épinglage + versions vulnérables
connues), temps de lead (commit→livrable). *La détection trunk-based figurait encore ici
alors qu'elle est outillée depuis le 2026-07-30 — retiré le 2026-08-31 : ce document se
contredisait à deux lignes d'intervalle.*

## 2. Pratiques de test — référentiels : pyramide de tests + ISO/IEC 25010

Pyramide (beaucoup d'unitaires rapides, moins d'intégration, peu d'e2e — mais des e2e
RÉELS sur le livrable) + les caractéristiques qualité ISO 25010 comme axes de test.

| Critère | Mesure flotte |
| --- | --- |
| Tests unitaires présents sur la logique métier | ✅ (compte de fichiers de test) |
| Couverture mesurée (pas forcément gatée) | ✅ (coverage configuré — fait sur VSCode1 84,7 % / VSCode2 ~38 %) |
| Tests fonctionnels sur l'artefact RÉEL (rendu, PDF re-parsé, navigateur) | ✅ (marqueurs puppeteer/pymupdf/Presentation/TestClient) |
| Chemin critique couvert (le cœur qui fait la valeur) | 🔍 (audit — constat : calcul de scores VSCode1 non testé, export PPT hors `npm test`) |
| Tests d'erreur/cas limites (pas seulement le chemin heureux) | 🔍 (audit robustesse) |
| Tests en CI (pas seulement en local) | ✅ (CI VSCode1) |
| Seuil de couverture gaté (une fois la mesure stabilisée) | ⬜ (décision différée volontairement : mesurer d'abord) |
| Tests de non-régression sur bug corrigé | ⬜ (non détectable automatiquement — discipline à documenter dans conventions) |
| **P5 — Tout clic utilisateur corrigé est rejoué dans un navigateur RÉEL** (test e2e qui clique comme l'utilisateur, pas un appel d'API qui contourne l'UI) | ⬜ — aucune fonction du scan ne le mesure : le marqueur puppeteer/playwright du critère « artefact réel » prouve la présence d'un e2e, pas qu'un clic corrigé y soit rejoué. Mesurable par relecture seulement. Référence : `VSCode2/CLAUDE.md` règle P5 + `VSCode2/tests/test_e2e_premiers_clics.py`. Source : finding `flotte:regle-p5-clic-navigateur-reel-a-generaliser`, reprise acceptée le 2026-09-28 |
| **Inventaire de routes figé** : un test rapide gèle la surface publique (méthode, chemin) du serveur ; rouge si une route disparaît ou apparaît sans mise à jour consciente | ⬜ — non mesuré par le scan (présence d'un test nommé, pas une propriété lue à froid). Présent sur VSCode2 (`tests/test_route_inventory.py`, `app.openapi()`) et au hub depuis le 2026-09-28 (`tests/test_serve_wiki_routes.py`, serveur `http.server` réel sur port éphémère). Source : finding `flotte:inventaire-de-routes-fige-a-generaliser` |

| Garde-fou livré avec son test → **mutation de sa propre source**, au moins un mutant tué, documenté dans le commit | ⬜ — **non outillable par le scan, et c'est définitif** : la mutation exige une EXÉCUTION, aucune lecture à froid ne peut la constater. Critère de **revue** : inscrit comme étape de la skill `revue-increment` (§ 3) le 2026-09-19, relisible après coup par le marqueur « mutant tué » dans les notes de run. Motif : 4 gardes vertes et contournables le 2026-09-09, 6 gardes vertes par construction le 2026-09-19 |

**Écarts à outiller** : part unitaire/intégration/e2e (forme de la pyramide), couverture
du chemin critique identifié par projet, tendance de couverture (snapshots).

## 3. Pratiques de documentation — référentiel : Diátaxis

Quatre besoins distincts → quatre formes : **tutorial** (apprentissage), **how-to**
(objectif), **référence** (information), **explication** (compréhension). Qualité
fonctionnelle (exactitude, complétude) ET qualité profonde (répond au besoin réel).

| Critère | Mesure flotte |
| --- | --- |
| Porte d'entrée : README avec install/usage (= how-to minimal) | ✅ (dimension documentation) |
| Référence technique à jour (stack, architecture, conventions) | ✅ partiel (wiki technical présent sur 3 projets) |
| Rules d'agent (CLAUDE.md) présentes et opérantes | ✅ |
| Explication (docs de conception, pourquoi des choix) | ✅ partiel (docs/reflexions ici ; non mesuré ailleurs) |
| Doc générée jamais éditée à la main (marquée comme telle) | ✅ (pratique en place ici — wiki généré) |
| Exactitude : la doc correspond au code réel (pas de doc morte) | ✅ (dimension documentation — chemins cités dans les blocs de code de README.md/CLAUDE.md vérifiés sur disque, 0 token, dégrade la pastille sur écart) |
| Les 4 formes Diátaxis distinguées (pas un fourre-tout) | ✅ (dimension documentation — `diataxis_formes()` cherche les marqueurs usuels des 4 formes dans les titres Markdown de README.md/CLAUDE.md et les noms de fichiers sous `docs/`, 0 token ; sous-champ additif `documentation.diataxis`, ne dégrade jamais la pastille présence+exactitude) |
| `AGENTS.md` à la racine (standard ouvert Linux Foundation, à côté ou en lieu de CLAUDE.md) | ✅ `agents_md_present()`, **mesure affichée, non imposée** — l'action adoptée est « évaluer par projet », aucun fichier créé. Mesuré le 2026-09-26 : **1/8** (VScode6-learning-sprintIA) |
| Décisions d'architecture tracées en ADR (Nygard : titre, statut, contexte, décision, conséquences) sous `docs/adr/` ou `docs/decisions/` | ✅ `adr_presents()` (compte les fichiers portant les 4 sections), **mesure affichée**, aucune action généralisée : arbitrage projet par projet. Mesuré le 2026-09-26 : **0/8** |

**Écarts à outiller** : datation des sections (fraîcheur).

## 4. Cadrage produit — référentiels : 4 risques de Cagan + Opportunity Solution Tree (Torres)

Cagan (*Inspired*) : toute discovery doit adresser 4 risques — **valeur** (en veulent-ils ?),
**utilisabilité** (savent-ils s'en servir ?), **faisabilité** (peut-on le construire ?),
**viabilité** (est-ce soutenable ?). Torres : arbre outcome → opportunités (besoins) →
solutions, pour relier chaque solution à un besoin réel.

| Critère | Mesure flotte |
| --- | --- |
| Persona / utilisateur cible nommé | ✅ (marqueur persona) |
| Why / problème à résoudre explicite | ✅ (marqueur why/pourquoi) |
| Besoins / pain points formalisés | ✅ (marqueur besoins) |
| Proposition de valeur écrite | ✅ (marqueur valeur) |
| Product brief ou PRD structuré (BMAD ou autre) | ✅ (artefact product-brief détecté) |
| Les 4 risques de Cagan adressés explicitement | ⬜ (analyse qualitative — remédiation `bmad-prfaq`/`bmad-forge-idea`) |
| Lien outcome → solution (chaque feature rattachée à un besoin) | ⬜ (non mesuré — pertinent surtout sur VSCode1/VSCode2) |
| Mesure de succès définie (comment on saura que ça marche) | ⬜ (ajoutable au détecteur : marqueur « mesure de succès / KPI ») |
| Non-objectifs explicites (ce que le produit ne fait pas) | ⬜ (ajoutable au détecteur) |

**Écarts à outiller** : marqueurs « mesure de succès » et « non-objectifs » dans le scan ;
audit produit qualitatif (les 4 risques) via `bmad-agent-pm`/`bmad-prfaq` sur demande.

## 5. Sécurité — référentiels : OWASP ASVS 5.0 (~350 exigences, 17 chapitres) + SAMM (3 niveaux de maturité)

ASVS pour le **quoi vérifier** dans l'application ; SAMM pour la **maturité du
processus**. Adapté à l'échelle de la flotte (projets locaux mono-utilisateur, mais
manipulant des données réelles — PII d'interviews et de répondants).

| Critère (sous-ensemble ASVS pertinent flotte) | Mesure flotte |
| --- | --- |
| Secrets jamais commités (.env gitigné, placeholders) | ✅ proxy + 🔍 audit (VSCode1 versionne `.env.dev`/`.env.preprod`/`.env.prod` **à dessein** — paramétrage sans secret, secrets dans des `.env.*.local` ignorés, garde-fou `scripts/test-env-sans-secret.js` dans `npm test` : pas un écart, requalifié le 2026-09-26) |
| Pas d'injection de commande (subprocess en liste, jamais shell=True) | 🔍 audit (6/6 sains) |
| Pas d'eval/pickle/désérialisation non sûre | 🔍 audit (6/6 sains) |
| Entrées utilisateur validées (taille, type, chemin assaini) | 🔍 audit (constats : upload audio sans cap VSCode2, JSON→500 VSCode) |
| SQL paramétré (jamais de concaténation d'entrée) | 🔍 audit (VSCode1/VSCode2 sains) |
| **Authentification là où des données personnelles sont exposées** | 🔍 audit — **constat majeur : API VSCode1 sans aucune auth avec PII** |
| Exposition réseau minimale (bind localhost par défaut) | 🔍 audit — **constat : VSCode écoute 0.0.0.0 en exécutant PowerShell** |
| Garde-fous d'agent (deny rules, guard git destructif) | ✅ proxy |
| Dépendances sans vulnérabilité connue (audit npm/pip) | ✅ **proxy** `audit_dependances_ci()` : présence de `pip-audit`, `npm audit` ou `safety check` dans `.github/workflows/*.yml` — mesure la présence de l'audit, **pas** l'absence de vulnérabilité (les étapes ajoutées sont non bloquantes). Mesuré le 2026-09-26 : **4/8** (VSCode1 `npm audit`, ajouté `ea5e96e` ; VSCode2, VSCode3 déjà ; VSCode4 `pip-audit`, ajouté `e0a9b4e`, puis Pillow 12.2.0 → 12.3.0 `564d3cf` : 13 vulnérabilités → 0) |
| Modélisation de menace même légère (SAMM L1) | ⬜ (à faire une fois par projet à données réelles) |

**Écarts à outiller** : suivi des
2 constats majeurs (auth VSCode1, bind VSCode) comme findings.

## 6. Pratiques data — référentiel : DAMA-DMBOK (dimensions qualité) + cycle de vie

Nouveau domaine (aucune mesure aujourd'hui). Dimensions qualité DAMA : complétude,
validité, exactitude, cohérence, unicité, fraîcheur. Pertinent surtout pour VSCode1
(répondants/PII, SQLite) et VSCode2 (interviews/verbatims clients, SQLite).

| Critère | Mesure flotte |
| --- | --- |
| Inventaire des données sensibles (où sont les PII ?) | ⬜ — VSCode1 : répondants nominatifs ; VSCode2 : verbatims d'interviews clients |
| Sauvegarde outillée et testée (backup + restore) | ⬜ (VSCode1 a backup-db.js/restore — non vérifié régulièrement) |
| Isolation dev/réel (la base réelle jamais écrasée par les tests) | ✅ partiel (conventions VSCode2 : APP_DB_PATH isolé par conftest) |
| Rétention/purge définies (combien de temps garde-t-on les verbatims ?) | ⬜ |
| Migrations de schéma outillées (pas de DDL artisanal) | 🔍 audit (constat : migrations f-string VSCode2) |
| Qualité des données mesurée (complétude/validité sur les tables clés) | ⬜ |
| Anonymisation/pseudonymisation quand possible (exports, demos) | ⬜ (seed-demo existe sur VSCode1/VSCode2 — à vérifier) |

**Écarts à outiller** : détecteur data au scan (présence backup/restore, seed-demo,
isolation de test), puis audit data qualitatif sur VSCode1/VSCode2 (les 2 projets à PII).

---

## 7. Pratiques agentic — référentiels : docs providers (Anthropic/Claude Code, OpenAI, Mistral, GitHub)

Le domaine propre à cette flotte : des projets **développés avec des agents**. La cible
vient des documentations officielles des providers (pas d'un standard figé — elles
bougent vite, d'où l'alimentation continue par le volet 2 de `veille-agentic`).

| Critère | Mesure flotte |
| --- | --- |
| Contexte projet versionné (CLAUDE.md / règles d'agent par projet) | ✅ (dimension « Pratiques + rules » du scan) |
| Skills packagées pour les workflows récurrents (au lieu de prompts répétés) | ✅ (inventaire skills du scan + catalogue orchestrateur) |
| Garde-fous outillés : hooks destructifs, deny rules, permissions explicites | ✅ (dimension « Sécurité (proxy) » : guard git, deny rules) |
| Supervision de l'usage réel des agents (transcripts → métriques → diagnostic) | ✅ (dispositif étage 1+2 de ce hub — canon partagé) |
| Vérification réelle des livrables d'agent (rendu regardé, pas confiance aveugle) | ✅ (dimension « Test fonctionnel / rendu réel » + pptx-verify) |
| Boucle humaine sur les actions irréversibles (propose → arbitre → applique) | ✅ (arbitrages.json + checkpoints des playbooks) |
| Mémoire projet persistante entretenue (faits durables hors contexte de session) | ⬜ (mémoires présentes côté hub — non mesuré par projet) |
| Sous-agents scopés pour l'exploration volumineuse (contexte principal préservé) | ⬜ (règle du catalogue orchestrateur — usage non mesuré à froid) |
| Revue en contexte frais protégée du fork (le relecteur n'hérite pas du contexte de l'implémenteur) | ⬜ (règle adoptée 2026-08-31 et écrite dans les playbooks `evolution-flotte` et `export-ppt-verifie`, mais **aucun détecteur** : `grep -i fork` ne rend rien dans `scan_projets.py` ni `scan_transcripts.py`. Le ✅ posé le jour même était faux — finding `referentiel:deux-sources-qui-se-contredisent`) |
| TTL de cache prompt déclaré sur les sous-agents ré-invoqués dans une même séance (`experimental.cacheTtl`) | ⬜ (mesurable à froid par grep sur `.claude/agents/*.md` — critère adopté 2026-08-31, activation suspendue à une mesure `/cost` réelle) |
| Diagnostic confronté aux 3 catégories MAST (spécification/design, mésalignement inter-agents, vérification/terminaison), angle mort MAST nommé s'il n'apparaît depuis N cycles | ⬜ (mesurable par relecture seulement, pas à froid — MAST, arXiv:2503.13657, veille 2026-09-08) |
| Faux succès auto-déclarés détectés (« succès » sans marqueur de vérification dans les notes du run) | ✅ `succes_sans_marqueur()` (`scan_projets.py`) — affiché dans le bloc `CHIFFRES-MESURES:REPRISES` de CLAUDE.md (Confident Closing, arXiv:2606.09863, veille 2026-09-08) |
| Un fan-out dont un sous-agent est en échec/non-rendu n'est jamais journalisé succès | ✅ `log_run.py` refuse `resultat: succes` si une étape du `plan` porte `etat: echec`/`etat: non-rendu` (OrchestraBench, arXiv:2608.05263, veille 2026-09-08) |
| Sous-agent DÉCLARÉ sur disque et observé au moins une fois (jointure `.claude/agents/*.md` × agents observés) | ✅ `agents_declares_non_instrumentes()` (`scripts/scan_projets.py`) — adopté et outillé le 2026-09-19. **Ne signale que le zéro, jamais le « peu »** : aucune fenêtre d'observation n'est requise pour un ensemble vide, et la veille n'a trouvé aucune source publiée pour en justifier une. Les porteurs de `.claude/agents-en-sommeil/` sont exclus (décision tracée ≠ panne). Mesuré au hub le 2026-09-19 : 1 écart, `agent-securite` — `utilisateur-produit`, qui avait motivé la règle, a été invoqué entre-temps |
| **[RÈGLE NÉGATIVE]** Une quittance de validation produite par un agent simulant l'utilisateur **ne vaut pas** quittance : le nom en sortie doit être celui d'une **personne** | ✅ `est_identite_non_humaine()` (`.claude/dispositif/canon/log_run.py`) — adopté le 2026-09-19. Source : le taux de succès d'un agent varie **jusqu'à 9 points de pourcentage** selon le LLM qui joue l'utilisateur, avec miscalibration systématique (« Lost in Simulation »). Mettre l'agent AU PLAN ne répare rien : il répond à « qui a été simulé en train d'ouvrir l'artefact », pas à « qui l'a ouvert ». La garde refuse les identités qui **s'annoncent** non humaines — elle ne fait pas l'état civil |
| **[RÈGLE NÉGATIVE]** Un jugement de **préférence** sur une réponse d'IA (la sienne ou celle d'un pair) **ne vaut pas** critère de qualité | ⬜ — **délibérément non outillé**, aucun détecteur à froid n'est possible. Adopté le 2026-09-19. Source : `compar:IA` (arXiv:2602.06669), arène LLM de l'État français (Ministère de la Culture), **seule source francophone sérieuse** trouvée sur la qualité des échanges humain↔IA. Elle documente quatre confondants qui font préférer une réponse **sans qu'elle soit meilleure** : la **longueur**, le **format**, le **préfixe** et la **sycophantie**. Portée ici : une revue de salle, un vote entre variantes ou un « cette version est mieux » ne closent aucun critère — seule une mesure le fait. **Régime humain→IA, PAS orchestrateur→sous-agent** : l'entrée est classée *non-trouvaille* côté correctif, elle ne corrige aucun des 6 cas qui coûtent cher à cette flotte ; la règle négative est le seul résidu qui valait d'être gravé |
| Hooks de RAPPEL (fail-open, sortie 0) distingués des hooks de GATE (sortie 2 bloquante) | ⬜ — **et le gate n'est PAS câblé** : l'entrée de veille exige une preuve avant de câbler. Le 2026-09-19, un hook Stop jetable écrit **hors du dépôt** rend bien 2 sur le cas nominal et 0 quand `stop_hook_active` est vrai (la réponse publiée à « un hook bloquant fera boucler l'agent ») — mais **le blocage au niveau du harnais n'a pas été observé** : l'entrée reste classée « annoncé ». Rien n'a été câblé dans `settings.json` |
| Compte rendu de salle : désaccord(s) documenté(s) distinct de la synthèse finale, ou absence explicitement interrogée | ⬜ (mesurable par relecture seulement — Deliberative Illusion, arXiv:2606.03032, veille 2026-09-08) |
| Sous-agent long : jalon intermédiaire journalisable rendu avant la conclusion finale | ⬜ (mesurable par relecture seulement — Beyond the Leaderboard, arXiv:2607.05775, veille 2026-09-08) |
| Fiabilité mesurée en répétition (success^k) : au moins N≥3 exécutions du même cas dans le jeu de référence avec un taux de succès^k calculé, pas une seule passe | **instrument futur** — ⬜ aujourd'hui : `evaluer_constats.py` ne rejoue jamais un même cas (`ablation_mono_agent_jamais_faite=true`, aucun champ `success^k` dans `runs.jsonl`). Nécessite une option `--repeter N` à ajouter au script (hors périmètre de cet incrément — action corrective non appliquée, voir reste-à-faire). Source : ReliabilityBench, ACL 2026 Findings (revue par pairs) + arXiv:2601.06112 (préprint, écart 96,9%→88,1% sous perturbation, CITATION non reproduite localement) |
| Gate de lancement multi-salles : Δ-succès mesuré > seuil ET Δ-coût ≤ budget ET Δ-P95 ≤ budget ET pas de hausse du taux de salle-muette, sinon repli sur single-agent+outils | **instrument futur** — ⬜ aujourd'hui : `p95_salle_min` et `seuil_non_convergent_min` sont mesurés, mais aucune comparaison mono-agent vs 4-salles à budget égal n'existe (ablation jamais faite). Nécessite une instrumentation dédiée (hors périmètre de cet incrément — action corrective non appliquée, voir reste-à-faire ; à court terme, repli conseillé sur 2-3 salles tant que l'ablation n'existe pas). Source : arXiv:2512.08296 (préprint v3, labo évaluant sa propre architecture — juge et partie) + arXiv:2607.27942 (préprint), tous deux non relus par les pairs à ce jour |
| Au moins 1 cas d'injection composée (2 éléments bénins isolés, malveillants une fois routés — indirecte, différée, agent distant compromis, mémoire empoisonnée) présent dans le jeu de référence sécurité | Présence d'un tel cas : **mesurable à froid dès aujourd'hui** par comptage dans le jeu de référence (aucun cas actuellement — vérifié : `tests/` n'en contient pas, § 18 de la grille 7 bis). Le **scoring outillé** (`evaluer_constats.py --securite`) reste, lui, un **instrument futur** — pas encore écrit (hors périmètre de cet incrément — action corrective non appliquée, voir reste-à-faire). Source : ACL 2026 Findings/long (S18/S19, revus par pairs) + arXiv:2602.16901 (préprint) |
| **Clôture à jeton aléatoire** autour de tout contenu client ou non authentifié passé à un agent (le jeton est tiré à chaque appel, après l'écriture du contenu : une injonction embarquée ne peut pas refermer le bloc ni se faire passer pour une consigne). LIMITE (revue du 2026-09-28, même principe que `wiki:injonction-semantique-lisible-apres-neutralisation`) : la clôture ferme la STRUCTURE, pas la lisibilité sémantique — le contenu enfermé reste un texte que le modèle lit ; l'implémentation de référence la double d'une consigne anti-injonction explicite (`openhub_agents.py:172-174`), les deux vont ensemble | ⬜ — **présence non mesurée à froid** (aucun détecteur ; à relever par `audit-technique`/`agent-securite` dès qu'un projet passe du contenu client à un agent). Implémentation de référence de la flotte : `VSCode2/app/services/openhub_agents.py:158-178` (`_prompt_avec_contexte_non_fiable`, `uuid4` tronqué à 12 hex), correctif du constat `securite:critique` du 2026-09-04 (injection indirecte vers agent autonome). Pendant côté produit de la clause `PROVENANCE` des briefs. Source : veille du 2026-09-28 (étude OpenHub), finding `flotte:pattern-cloture-jeton-contenu-client`, arbitrage utilisateur du 2026-09-28 |
| **Repli simulé propre** quand une CLI externe dont dépend le produit est absente : message clair (quoi installer, où) et mode dégradé au lieu d'un plantage | ⬜ — mesurable par relecture ou par un test qui retire la CLI du `PATH` ; aucun détecteur à froid. Référence : `VSCode2/app/services/openhub_agents.py:251-257` (réponse « Agent simulé » + consigne d'installer `opencode` dans le `PATH`). Source : même trouvaille, arbitrage du 2026-09-28 |
| **Frontmatter d'agent déclaratif** (`id`/`label`/`description`/`mode` côté `.opencode/agents/*.md` de VSCode2) | ⬜ **déjà couvert par le frontmatter .claude/agents** (constat de LECTURE manuelle, aucune fonction de scan ne le mesure — la règle du ✅ de ce fichier exige un nom de fonction) (`name`/`description`/`tools`, plus `model` — lu sur `.claude/agents/bmad-revue.md` le 2026-09-28) : `name` tient lieu d'`id`, `mode` est implicite (tout porteur de `.claude/agents/` est un sous-agent) ; seul `label` n'a pas d'équivalent, et il ne sert qu'à l'affichage. **Rien à ajouter, aucun nouveau format** — arbitrage du 2026-09-28 |

**Alimentation** : le volet 2 de `veille-agentic` (docs providers) propose des entrées
`pratique` avec `regle_proposee` (candidate à devenir un critère ci-dessus) et
`action_corrective` (correctif flotte arbitrable). Une pratique passée `adopte` par
l'utilisateur est intégrée ici, et au scan si mesurable à froid.

## 7 ter. Structure d'un agent — référentiel : gabarit de mandat à 9 blocs

Structure validée par l'utilisateur le 2026-09-29 ; raisons, sources (avec leur nature :
doc officielle, billet, papier relu, préprint), limites, squelette et exemple rempli dans
[`docs/reflexions/gabarit-agent.md`](../../reflexions/gabarit-agent.md). Un critère de
PRÉSENCE mesure le texte du mandat, pas la qualité du comportement de l'agent ; la preuve
du comportement (3 à 5 briefs de référence rejoués) est proposée à part, non outillée.

| Critère | Mesure flotte |
| --- | --- |
| 1. Rôle : 1 à 2 phrases fonctionnelles (qui, pour qui, ce qu'il produit) | ⬜ (exige un jugement : la qualité d'un rôle ne se lit pas à froid) |
| 2. Objectif et fin : condition d'arrêt écrite, budget de temps, pas de `maxTurns` | ✅ `structure_mandats_agents()` via `blocs_mandat()` (critère B11, noté, part des 6 blocs lisibles à froid) — nature : titre de section d'arrêt/budget et absence de `maxTurns` dans l'en-tête ; présence, pas justesse de la condition |
| 3. Contexte et motifs : le pourquoi de chaque contrainte | ⬜ (exige un jugement) |
| 4. Outils : chaque outil de `tools:` décrit dans le corps ; ligne « Écriture : » si Edit/Write | ✅ `structure_mandats_agents()` via `blocs_mandat()` (critère B11, noté, part des 6 blocs lisibles à froid) — nature : chaque nom de `tools:` cité dans le corps, ligne « Écriture : » si Edit/Write ; la citation n'est pas une description juste |
| 5. Manière de travailler : consigne générale, sans plan pas à pas | ⬜ (exige un jugement : distinguer consigne et plan figé) |
| 6. Ton et format dits en positif | ✅ `structure_mandats_agents()` via `blocs_mandat()` (critère B11, noté, part des 6 blocs lisibles à froid) — nature : ligne ou titre commençant par « Ton » ; « dit en positif » exige un jugement, non noté |
| 7. Interdits courts et motivés | ✅ `structure_mandats_agents()` via `blocs_mandat()` (critère B11, noté, part des 6 blocs lisibles à froid) — nature : titre de section d'interdits ; « courts et motivés » exige un jugement, non noté |
| 8. Exemple de départ (première action ou brief type) | ✅ `structure_mandats_agents()` via `blocs_mandat()` (critère B11, noté, part des 6 blocs lisibles à froid) — nature : titre « Exemple » ou « Première action » ; la pertinence de l'exemple exige un jugement |
| 9. Rappel final : contrat de sortie et provenance en fin de texte | ✅ `structure_mandats_agents()` via `blocs_mandat()` (critère B11, noté, part des 6 blocs lisibles à froid) — nature : titre de contrat de sortie ET mention de provenance dans le dernier tiers du texte |

## 7 bis. Grille de maturité agentic — domaines, avec ce que le hub mesure

Source : **grille d'audit et standard de maturité agentic fournis par l'utilisateur le
2026-09-20**. Ce paragraphe ne duplique pas le § 7 (critères outillés, un par ligne) :
il répond à la question du commanditaire — *sur les domaines du standard, où en est ce
hub, et par quelle preuve ?* Chaque ligne a été **vérifiée dans le dépôt avant d'être
écrite** ; une preuve non retrouvée serait marquée comme telle plutôt que reconduite.

Marqueurs : ✅ mesuré à froid · 🔍 audité (relecture, pas de détecteur) · ⬜ non mesuré ·
N/A avec sa raison.

| Domaine du standard | État | Preuve dans ce dépôt |
| --- | --- | --- |
| § 4 Design des agents | ✅ | Gabarit de brief du § 2 ter d'`agent-orchestrator` : slots `UNCERTAINTY` et `QUALITY CRITERIA` + bloc de fin obligatoire (commit `e0369bb`) |
| § 9 Orchestration | ✅ | Fan-out plafonné à **≤ 4 sous-agents** (table des modes, `agent-orchestrator` § « Parallèle ») ; salle non convergente au-delà de **5× le p95 mesuré** refusée par `guard_convergence_salles.py` sur `convergence.py` ; terminaison étayée (`guard_terminaison_etayee.py`, commit `cada8a1`) ; `log_run.py` REFUSE un run `succes` dont une étape porte `etat: echec` ou `non-rendu` |
| § 11 Sorties structurées | ✅ | Bloc `STATUT / COMMIT / FAITS INFIRMÉS / INFORMATION INSUFFISANTE / NON FERMÉ` en dernières lignes de tout rendu de salle, exigé **par `agent_type`** (`general-purpose`) et non par heuristique de texte |
| § 12 Évaluation | ✅ partiel | `evaluation/jeu_reference.jsonl` — **235 cas** (`wc -l`, 2026-09-20), vocabulaire fermé `CLASSES` dans `evaluer_constats.py` (commit `cc15e1e`). Partiel assumé et écrit dans le script lui-même : « ni l'utilité d'un constat fondé, ni les **faux négatifs** » — un constat jamais émis n'est pas mesurable |
| § 13 Quality gates | ✅ | **16 entrées de hooks** dans `settings.json` sur 6 événements, dont **5 bloquantes** (`guard_destructive_git`, `guard_export_genere`, `guard_salle_skills`, `guard_service`, `guard_terminaison_etayee`) ; mutation testing appliqué aux gardes elles-mêmes (`TestEpreuveParMutation`) |
| § 15 Observabilité | ✅ partiel | `log_usage.py` apparie l'`agent_id` (session × horodatage) et enregistre le `modele` demandé et le `modele_resolu`. Les jetons des sous-agents d'arrière-plan restent un chantier — non clos ici |
| § 25 Taxonomie des erreurs | ✅ | Vocabulaire fermé `CLASSES = ("fonde-et-corrige", "non-fonde", "deja-resolu", "refuse-par-choix")` (`evaluer_constats.py`) + relecture **MAST** en 3 catégories imposée avant écriture du diagnostic (`agent-supervisor` § 3 ter) |
| § 18 Sécurité agentic | 🔍 | Skill et sous-agent `agent-securite`, classification **OWASP ASI01-10**. **Aucun test d'injection** : `tests/` n'en contient pas un seul (vérifié le 2026-09-20) — l'audit est une relecture, pas une épreuve |
| § 14 KPI | ✅ partiel | Mesuré par script sur `runs.jsonl` le 2026-09-20 : **167 succès sur 197 runs**, **121 reprises soit 0,61 par run**. L'acceptation humaine (quittance qui ne vaut que sous un nom de **personne**, `est_identite_non_humaine()`) a été instaurée la veille — trop récente pour avoir une série |
| § 8 Model routing | ⬜ | La politique est **écrite** (§ modèle d'`agent-orchestrator` : fan-out mécanique en haiku, revue en sonnet…) et `log_usage.py` a commencé à enregistrer `modele`/`modele_resolu` — mais aucun croisement modèle × tâche × reprises n'est encore rendu |
| § 23 Gouvernance | ⬜ → ✅ | **Devient ✅ avec le volet A de ce chantier** : `write_diagnostic.py` pose désormais `owner` et `echeance` (`vu_le` + `SEUIL_ECHEANCE_FINDING_JOURS`) sur tout constat écrit ou reconduit, et `point_du_jour.py` NOMME au démarrage ceux dont l'échéance est passée. Avant ce jour : aucun des 19 constats ne portait de propriétaire ni de date, et 11 attendaient dans cet état |
| § 3 Valeur métier | ⬜ | Le sous-agent `utilisateur-produit` existe et est invoqué, mais **aucun KPI métier** n'est journalisé : le dispositif mesure des runs, pas de la valeur livrée |
| § 6 RAG · § 7 Mémoire · § 16 Scalabilité · § 21 Résilience fournisseur | N/A | La flotte **n'est pas une usine d'agents en production** : c'est du développement *avec* des agents. Pas de corpus servi, pas de SLA, pas de charge à absorber. Voir la réserve ci-dessous — un N/A de domaine ne vaut pas N/A de toutes ses clauses |

### Réserve sur les N/A (issue de la table ronde du 2026-09-20)

Un domaine classé N/A ne rend pas sans objet **chacune** de ses clauses. Deux résidus
sont explicitement retenus, et un N/A posé sans les relire serait une sortie par le haut :

- **§ 8.3 — « ne pas agrandir la fenêtre de contexte comme substitut à un bon
  retrieval »** décrit exactement la *discipline de gestion des tokens* du hub (lire le
  bon étage d'abord, `runs.jsonl` par la fin, sous-agent pour toute sortie volumineuse).
  La clause reste **applicable détachée du RAG** : ce n'est pas du retrieval, c'est la
  même erreur de raisonnement.
- **§ 18 — attaques composées multi-agent (S18/S19, ACL 2026)** ne sont **PAS** sans
  objet pour un orchestrateur **en étoile à briefs en texte libre** : un brief est du
  texte non contraint qui traverse plusieurs agents. Le point est **à instruire**, il
  n'est ni mesuré ni écarté.

### Ce qui n'est PAS adopté de la grille (règle négative)

Le **barème 0-4 par domaine et le score /100** de la grille fournie ne sont **pas
adoptés**. Raison : un niveau attribué à dire d'expert est un **jugement de préférence**,
et un jugement de préférence ne vaut pas critère — règle déjà inscrite au § 7 sur la
source `compar:IA` (arXiv:2602.06669, quatre confondants : longueur, format, préfixe,
sycophantie). Un score global agrège en outre des domaines N/A avec des domaines mesurés
et produit un chiffre qu'aucune commande ne reproduit, ce que R6 interdit. Ce qui est
retenu de la grille, c'est sa **structure de preuve** — preuve, risque, impact,
recommandation, propriétaire, échéance — dont le volet A rend les deux derniers champs
effectifs.

---

## Référentiel d'évaluation v3 (kit « Évaluation des pratiques »)

Barème 3 arbitré le 2026-09-29 (« ils sont notés ») : B gagne B11, structure des mandats
d'agents (§ 7 ter) ; rien d'autre ne change, une note v2 se recalcule sans B11
(`evaluation_agentic.globale_sans_ajouts`). Choix d'UN critère composite gradué plutôt
que 6 critères binaires : 6 critères auraient pesé 6/16 = 37,5 % de B sur une seule
pratique, un critère pèse 1/11 = 9 %. Sans définition d'agent : « non applicable »,
hors moyenne. Mesure de PRÉSENCE du texte, pas du comportement de l'agent.
Arbitré le 2026-09-28 (barème v2). 23 critères par projet, en deux référentiels,
chacun détecté par une fonction de `scripts/detection_generique.py` (C1 : le ✅ nomme
cette fonction) ; les textes pédagogiques complets (définition, ce qu'on regarde,
pourquoi, ce que la note permet / ne permet pas de conclure) sont les docstrings de ces
fonctions, rendus dans l'onglet « Référentiel » de `docs/evaluation-agentic.html`.
Liste générée depuis `evaluation_agentic.BAREMES` le 2026-09-28, pas écrite à la main ;
renumérotée le 2026-09-29 dans l'ordre de lecture arbitré (groupes de
`detection_generique.GROUPES`, codes attribués dans cet ordre — lot 9).

### A — Pratiques de développement (13)

| Groupe | Code | Critère | Mesure | Source publique |
| --- | --- | --- | --- | --- |
| Besoin et conception | A1 | Backlog présent et user stories bien formées | ✅ `epics_us_bien_formees()` | Scrum Guide 2020, « Product Backlog » ; B. Wake, 2003 (grille INVEST) |
| Besoin et conception | A2 | Critères d'acceptation | ✅ `criteres_acceptance()` | D. North, « Introducing BDD », 2006 |
| Besoin et conception | A3 | Décisions de conception tracées | ✅ `decisions_conception_tracees()` | M. Nygard, « Documenting Architecture Decisions », 2011 ; adr.github.io |
| Développement | A4 | Traçabilité de la demande au livrable | ✅ `tracabilite_demande_livrable()` | Conventional Commits, pied « Refs » ; GitHub/GitLab, « closing keywords » |
| Développement | A5 | Code documenté | ✅ `code_documente()` | PEP 257 |
| Développement | A6 | Analyseur statique configuré | ✅ `linter_configure()` | Google, « Software Engineering at Google », chap. 20 |
| Développement | A7 | Revue avant intégration par une autre personne | ✅ `revue_avant_integration()` | Google Engineering Practices, « Code Review » |
| Développement | A8 | Hygiène de sécurité de base | ✅ `securite_base()` | OWASP Top 10, A07 ; GitHub, « secret scanning » |
| Tests et livraison | A9 | Tests automatisés | ✅ `tests_automatises()` | Google, « Software Engineering at Google », chap. 11 |
| Tests et livraison | A10 | Tests et code évoluent ensemble | ✅ `co_evolution_tests()` | K. Beck, « Test-Driven Development by Example » ; DORA, test automation |
| Tests et livraison | A11 | Mesure de couverture configurée | ✅ `couverture_configuree()` | documentation coverage.py, Istanbul, JaCoCo |
| Tests et livraison | A12 | Résultat final exercé par le canal de l'utilisateur | ✅ `test_artefact_reel()` | M. Fowler, « TestPyramid » ; « Broad Stack Test » |
| Tests et livraison | A13 | Intégration et livraison continues | ✅ `integration_continue()` | DORA, « Continuous integration », « Continuous delivery » |

### B — Pratiques agentic (11)

| Groupe | Code | Critère | Mesure | Source publique |
| --- | --- | --- | --- | --- |
| Cadrer | B1 | Cadre agentic versionné | ✅ `cadre_agentic_versionne()` | Anthropic, « Claude Code best practices » ; agents.md |
| Cadrer | B2 | Gestion des prompts comme des artefacts — note fondée sur des déclarations | ✅ `gestion_prompts()` | OpenAI, « Evals » ; Anthropic, « Create strong empirical evaluations » |
| Cadrer | B3 | Politique de modèle et d'effort déclarée — note fondée sur des déclarations | ✅ `politique_modele_effort()` | Anthropic, « Choosing a model » |
| Borner | B4 | Garde-fous et réversibilité du travail des agents | ✅ `garde_fous_agentic()` | OWASP Top 10 for Agentic Applications, « excessive agency » ; Anthropic, « Claude Code security » |
| Borner | B5 | Niveau d'orchestration agentic (échelle à 5 niveaux) — note fondée sur des déclarations | ✅ `niveau_orchestration()` | Anthropic, « Building effective agents », 2024 |
| Exécuter | B6 | Solution agentic maîtrisée (critère conditionnel : « non applicable » sans dépendance LLM) | ✅ `solution_agentic_maitrisee()` | OWASP Top 10 for LLM Applications, LLM01, LLM05, LLM10 |
| Exécuter | B7 | Blocages du dispositif agentic tracés | ✅ `blocages_traces()` | Google SRE Book, chap. 15, « Postmortem Culture » |
| Livrer et valider | B8 | Conformité des résultats à la demande — note fondée sur des déclarations | ✅ `conformite_resultats()` | DORA, « change failure rate », par analogie |
| Livrer et valider | B9 | Validation humaine tracée après livraison | ✅ `validation_humaine_tracee()` | Scrum Guide 2020, « Definition of Done » ; ISO/IEC 25010, adéquation fonctionnelle |
| Améliorer | B10 | Amélioration continue du cadre agentic | ✅ `amelioration_continue()` | Anthropic, « Claude Code best practices » : faire évoluer CLAUDE.md ; Scrum Guide 2020, « Sprint Retrospective » |
| Structurer les agents | B11 | Structure des mandats d'agents (critère conditionnel et gradué : « non applicable » sans définition d'agent ; mesure la présence du texte, pas le comportement) | ✅ `structure_mandats_agents()` | Anthropic, « Prompting best practices » ; Liu et al., « Lost in the Middle », TACL 2024 |

Hors notation, relevant d'un audit : qualités I/N/V/E d'INVEST (A1), maîtrise réelle
d'une solution embarquant un LLM (B6). Les anciens axes propres au hub (reprises, refus
de gardes, salles, tokens) sortent du référentiel : bloc « dispositif » informatif, jamais
noté.

---

## Sources

- DORA capabilities : [dora.dev/capabilities](https://dora.dev/capabilities/) (continuous delivery, test automation, trunk-based…)
- OWASP ASVS 5.0 : [owasp.org/www-project-application-security-verification-standard](https://owasp.org/www-project-application-security-verification-standard/) · SAMM : [devguide.owasp.org…/samm](https://devguide.owasp.org/en/11-security-gap-analysis/01-guides/01-samm/)
- Diátaxis : [diataxis.fr](https://diataxis.fr/) (tutorials / how-to / reference / explanation + qualité fonctionnelle vs profonde)
- Cagan, 4 risques de discovery (*Inspired*) · Torres, Opportunity Solution Tree : [productcompass.pm](https://www.productcompass.pm/p/what-exactly-is-product-discovery)
- DAMA-DMBOK dimensions qualité : [dama.org](https://dama.org/learning-resources/dama-data-management-body-of-knowledge-dmbok/) · [DDQ research paper (DAMA-NL)](https://dama-nl.org/wp-content/uploads/2020/09/DDQ-Dimensions-of-Data-Quality-Research-Paper-version-1.2-d.d.-3-Sept-2020.pdf)
- Pratiques agentic : [docs Claude Code](https://code.claude.com/docs) · [Anthropic — building effective agents](https://www.anthropic.com/research/building-effective-agents) · [OpenAI platform — agents](https://platform.openai.com/docs/guides/agents) · [docs Mistral](https://docs.mistral.ai/) — surveillées par le volet 2 de `veille-agentic`

- **Non-trouvaille inscrite quand même** : `compar:IA` (arXiv:2602.06669) — PDF ouvert le 2026-09-19, **sections visibles seulement**. Jeux de données publics de prompts francophones et de préférences appariées, classement Bradley-Terry. Aucune action corrective : la seule chose transposable est la règle négative du § 7 ci-dessus
- Trouvailles de veille adoptées le 2026-09-19 : « Lost in Simulation » (utilisateurs simulés par LLM, jusqu'à 9 pts d'écart) · mutation testing scopée (mutmut `--paths-to-mutate`, Stryker `thresholds.break`) · hook `Stop`/`SubagentStop` à code 2 et `stop_hook_active` (docs Claude Code) · `actions/upload-artifact` + `if-no-files-found: error` · ruff `PT` (flake8-pytest-style) · `vulture` · agent déclaré non instrumenté
- **Constat, pas règle** : `copybara` (Google) est la référence attendue pour « correctif appliqué à la copie générée au lieu de la source ». Lecture faite le 2026-09-19, **il ne documente pas de garde de divergence comparable à `export_agentic.py --check`** : le dispositif maison est déjà en avance, aucune règle n'est à inscrire. `OpenFastTrace` (traçabilité exigence↔test cassant la CI) reste pertinent pour les dépôts porteurs de vraies user stories (VSCode1/VSCode2), **sans objet pour le hub qui n'en a pas** — non instruit, dépendance Java 17 à mettre en regard d'un coût d'entrée estimé ~2 h

## Gouvernance de ce référentiel

Ce document est la **cible** ; le scan et les audits sont la **mesure** ; l'écart entre
les deux alimente les findings du superviseur (`pratique-*`), arbitrés puis appliqués via
`evolution-flotte`. Réviser ce référentiel quand la veille (`veille-agentic`) détecte une
évolution des sources (ex. ASVS 5.x, révision DMBOK).
