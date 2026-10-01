# Évaluation des pratiques — mode d'emploi

Ce dossier (`.claude/evaluation/`) mesure, sans appeler aucun modèle d'IA et sans
dépendance à installer (Python 3.11+ et sa bibliothèque standard suffisent), les pratiques
de votre dépôt, puis produit une page HTML autonome : les notes, **et** pour chaque note
les critères qui la composent, ce qui a été trouvé, et ce qu'il faudrait faire.

| Fichier | Rôle |
| --- | --- |
| `detection_generique.py` | lit le dépôt (fichiers suivis, historique git) et dit, critère par critère, ce qu'il a trouvé ; ne calcule aucune note |
| `evaluation_agentic.py` | transforme ces constats en notes |
| `evaluation_page.py` | écrit la page `docs/evaluation-agentic.html` (CSS et JavaScript en ligne, aucune ressource externe) |
| `README.md` | ce fichier |

## Lancer

```
py .claude/evaluation/evaluation_page.py
```

(`python3` à la place de `py` hors Windows.) Options : `--racine <dépôt>` (par défaut, le
dépôt qui contient `.claude/`) et `--sortie <fichier.html>` (par défaut
`docs/evaluation-agentic.html`). Ouvrez ensuite la page dans un navigateur. Le dépôt
évalué est le vôtre : il apparaît comme l'unique projet de la page. La mesure lit un dépôt
git complet ; un clone partiel ou un dossier sans `.git` rend plusieurs critères « non
mesuré » (voir plus bas).

La page a trois onglets :

- **Résultats** — la note globale, puis les deux référentiels côte à côte : pour chaque
  critère, son code (A9…B9), sa question en une phrase et son état.
- **Écarts** — pour chaque critère qui n'est pas au maximum : ce que la mesure a trouvé,
  et une action concrète pour monter d'un niveau.
- **Référentiel** — la définition complète de chaque critère (voir ci-dessous) et un
  glossaire. Cliquer un critère depuis les deux autres onglets y mène directement.

Sans JavaScript, les onglets disparaissent et la page devient une page longue lisible ;
les liens vers un critère restent valides.

## Les deux référentiels (23 critères)

Chaque critère est détaillé dans l'onglet **Référentiel** selon cinq parties :
**1.** définition, **2.** ce qu'on regarde exactement dans le dépôt, **3.** pourquoi c'est
important, avec sa source publique, **4.** ce que la note permet de conclure, **5.** ce
qu'elle ne permet pas de conclure. Le tableau ci-dessous n'en est que la forme courte.

### A — Pratiques de développement (13)

| Groupe | Code | Critère | La question posée |
| --- | --- | --- | --- |
| Besoin et conception | A1 | Backlog présent et user stories bien formées | Le besoin est-il écrit en user stories exploitables (rôle, critères, dépendances) ? |
| Besoin et conception | A2 | Critères d'acceptation | Chaque story dit-elle à quoi on reconnaît qu'elle est finie ? |
| Besoin et conception | A3 | Décisions de conception tracées | Les choix d'architecture sont-ils écrits, datés et versionnés ? |
| Développement | A4 | Traçabilité de la demande au livrable | Peut-on remonter d'un changement de code à la demande qu'il sert ? |
| Développement | A5 | Code documenté | Les fonctions publiques du code disent-elles ce qu'elles font ? (Python) |
| Développement | A6 | Analyseur statique configuré | Un outil attrape-t-il mécaniquement les défauts courants avant la relecture ? |
| Développement | A7 | Revue avant intégration par une autre personne | Un changement est-il approuvé par une autre personne que son auteur avant d'entrer ? |
| Développement | A8 | Hygiène de sécurité de base | Les secrets restent-ils hors du dépôt ? |
| Tests et livraison | A9 | Tests automatisés | Le projet contient-il des tests qu'une machine peut rejouer ? |
| Tests et livraison | A10 | Tests et code évoluent ensemble | Quand le code change, les tests changent-ils avec lui ? (90 derniers jours) |
| Tests et livraison | A11 | Mesure de couverture configurée | L'équipe peut-elle mesurer quelle part du code ses tests exécutent ? |
| Tests et livraison | A12 | Résultat final exercé par le canal de l'utilisateur | Au moins un test vérifie-t-il le résultat final comme l'utilisateur le reçoit ? (la vérification visuelle par un humain n'est pas couverte) |
| Tests et livraison | A13 | Intégration et livraison continues | Une chaîne automatique vérifie-t-elle chaque envoi, et les livraisons sont-elles repérées ? |

### B — Pratiques agentic (11)

Ce référentiel regarde le travail confié à des assistants d'IA (agents).

| Groupe | Code | Critère | La question posée |
| --- | --- | --- | --- |
| Cadrer | B1 | Cadre agentic versionné | Les instructions données aux assistants d'IA sont-elles versionnées avec le code ? |
| Cadrer | B2 | Gestion des prompts comme des artefacts | Les prompts sont-ils gérés comme des fichiers versionnés, voire évalués ? |
| Cadrer | B3 | Politique de modèle et d'effort déclarée | Le projet choisit-il le modèle ou l'effort selon la tâche, et suit-il le coût ? |
| Borner | B4 | Garde-fous et réversibilité | Des barrières empêchent-elles un assistant de faire des dégâts, et son travail reste-t-il réversible ? |
| Borner | B5 | Niveau d'orchestration (échelle à 5 niveaux) | Jusqu'où le projet organise-t-il le travail de ses assistants ? |
| Exécuter | B6 | Solution agentic maîtrisée (conditionnel) | Si le produit appelle lui-même un modèle d'IA, ces appels sont-ils bornés et contrôlés ? |
| Exécuter | B7 | Blocages tracés | Les exécutions des assistants sont-elles tracées, et combien échouent ? |
| Livrer et valider | B8 | Conformité des résultats à la demande | Le travail confié aux assistants est-il livré du premier coup ? |
| Livrer et valider | B9 | Validation humaine tracée | La recette d'un livrable par une autre personne que son auteur est-elle tracée ? |
| Améliorer | B10 | Amélioration continue du cadre | Le cadre donné aux assistants est-il révisé régulièrement ? |
| Structurer les agents | B11 | Structure des mandats d'agents (conditionnel, gradué) | Les mandats des agents suivent-ils la structure de référence (fin écrite, outils décrits, ton, interdits, exemple, rappel final) ? Mesure la présence du texte, pas le comportement. |

**B2, B3, B5 et B8 portent la mention « note fondée sur des déclarations »** : ils lisent
ce que le projet déclare dans ses fichiers (un niveau, un modèle, un statut de journal),
pas une preuve que la pratique fonctionne.

## Écosystèmes reconnus

La détection ne connaît que les noms de fichiers et d'outils listés ici (relevés dans
le script de détection, rien d'autre n'est cherché). Elle repère d'abord **l'écosystème
dominant** du dépôt : celui qui compte le plus de fichiers source, un fichier de
construction (`pyproject.toml`, `requirements*.txt`, `package.json`, `pom.xml`,
`build.gradle`, `*.csproj`, `Gemfile`, `composer.json`, `go.mod`, `Cargo.toml`,
`mix.exs`, `Package.swift`, `CMakeLists.txt`, `stack.yaml`, `pubspec.yaml`…) comptant
pour un fichier.

**Absent ou non mesuré.** Pour les critères qui dépendent d'un outil (A9 tests, A11
couverture, A12 bout en bout, A6 analyseur statique), un critère sans signal n'est noté
**absent · 2/10** que si l'écosystème dominant fait partie de ceux dont la détection
connaît les outils usuels (liste par critère ci-dessous). Pour tout autre écosystème,
un outil reconnu donne « présent », mais aucun signal donne **non mesuré**, avec la
raison : la détection ne sanctionne pas une équipe parce qu'elle utilise un outil qui
n'est pas listé ici. Un dépôt sans aucun fichier source ni fichier de construction
garde « absent ». Autres cas de « non mesuré » : A5 sans code Python, A9 quand
`package.json` déclare un vrai `scripts.test` (pas le gabarit npm « no test
specified ») sans aucun fichier de test reconnu, et les critères qui lisent
l'historique git quand il manque.

- **A9 tests** — absence conclue pour Python, JavaScript/TypeScript, Java/Kotlin,
  .NET, Ruby, PHP, Go, Rust, Elixir : fichiers `test_*`, `*_test.*`, `*.spec.*`,
  `*.test.*`, `*Test.java` (et `*Tests.java`, `.kt`, `.cs`, `.php`, `.swift`),
  `*_spec.rb` ; en Rust, fichiers `.rs` sous `tests/` ou attribut `#[test]` ;
  `scripts.test` dans `package.json`.
- **Langages vus comme du code source** : `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.mjs`,
  `.cjs`, `.java`, `.kt`, `.go`, `.rb`, `.cs`, `.php`, `.rs`, `.swift`, `.scala`, `.c`,
  `.cpp`, `.h`, `.vue`, `.svelte`, `.gs`.
- **A11 couverture** — absence conclue pour Python, JavaScript/TypeScript, Java/Kotlin,
  Ruby : `.coveragerc`, `.nycrc`, `.c8rc`, `tarpaulin.toml`, ou `pytest-cov`, `--cov`,
  `[tool.coverage`, `c8`, `nyc`, `--coverage`, `collectCoverage`, `jacoco`, `coverlet`,
  `simplecov`, `excoveralls` dans `pyproject.toml`, `setup.cfg`, `tox.ini`,
  `pytest.ini`, `package.json`, `pom.xml`, `build.gradle`, `jest.config.js`,
  `vitest.config.ts`, `Gemfile`, `mix.exs`, `*.csproj` ou `requirements*.txt` ; dans la
  chaîne d'intégration ou un `Makefile` : `go test -cover`, `-coverprofile`,
  `cargo tarpaulin`, `cargo llvm-cov`, `grcov`.
- **A12 bout en bout** — absence conclue pour Python, JavaScript/TypeScript : dossier
  `e2e/`, ou dans un test `playwright`, `selenium`, `cypress`, `puppeteer`,
  `webdriver`, `TestClient(`, `requests.get(`, `httpx.`, `supertest`,
  `Presentation(`, `fitz.open`, `load_workbook(`, `pdfplumber`, `docx.Document(`,
  `subprocess.run(`, `httptest.`, `exec.Command(`, `assert_cmd`,
  `std::process::Command`, `reqwest::`.
- **A5 documentation** : Python uniquement (docstrings lues par le module `ast`).
- **A13 intégration continue** : `.github/workflows/*.yml`, `.gitlab-ci.yml`,
  `azure-pipelines.yml`, `Jenkinsfile`, `.circleci/config.yml`,
  `bitbucket-pipelines.yml` ; étiquettes git, dossier `releases/`, `CHANGELOG`.
- **A6 analyseur statique** — absence conclue pour Python, JavaScript/TypeScript,
  Ruby : `.eslintrc*`, `eslint.config.*`, `.flake8`, `ruff.toml`, `.pylintrc`,
  `.prettierrc*`, `biome.json`, `.golangci.yml` (ou `.toml`, `.json`),
  `checkstyle.xml`, `.rubocop.yml`, `.stylelintrc*`, `.markdownlint*`,
  `clippy.toml`, `rustfmt.toml`, `.credo.exs`, `phpcs.xml`, `phpstan.neon`,
  `.php-cs-fixer.php`, sections `[tool.ruff]`, `[tool.pylint]`, `[flake8]`, script
  `"lint"` ; dans la chaîne d'intégration ou un `Makefile` : `cargo clippy`,
  `cargo fmt`, `golangci-lint`, `mix credo`.
- **A8 secrets** : `.env` dans `.gitignore` ; motifs de clé `AKIA`, clé privée PEM,
  `ghp_`, `xox`, `sk-`.
- **B1 cadre des assistants** : `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`,
  `.windsurfrules`, `.github/copilot-instructions.md`, `.claude/settings.json`,
  `.cursor/rules/`.
- **B2 prompts** : fichiers suivis dans un dossier `prompts/` (hors `README` et `index`
  de documentation) ou nommés `*.prompt`, `*.prompt.md`, `*.prompty` ; jeu d'évaluation
  en forme de suite : dossier `evals/`, fichiers `eval_*.py`, `*_eval.py`,
  `*.eval.yaml`, `promptfooconfig.yaml`. Un moteur nommé `evaluation_*.py` n'est pas un
  jeu d'évaluation.
- **A4 traçabilité** : références `#123`, `US-4`, `story 1.2`, `issue 7`, `ticket 9`,
  `PROJ-42`, ou une ligne de pied `Refs:` suivie d'un identifiant non vide.
- **A7 revue** : un `CODEOWNERS` ne compte que s'il désigne au moins un relecteur qui
  n'est pas parmi les auteurs des 500 derniers commits ; une équipe `@org/equipe` seule
  laisse le critère « non mesuré ». Les adresses sont rapprochées par le `.mailmap` du
  dépôt s'il existe ; sans lui, deux adresses d'une même personne comptent pour deux
  personnes — ajoutez un `.mailmap` pour que vos propres approbations ne passent pas
  pour celles d'un tiers.
- **B6 appels de modèle** : dépendances `anthropic`, `openai`, `langchain`,
  `mistralai`, `google-genai`, `ollama`, `litellm`, `crewai`, `autogen`,
  `semantic-kernel`, `@ai-sdk/` dans `requirements.txt`, `pyproject.toml`,
  `package.json`, `Pipfile`, `go.mod`…

**Kits de méthode.** Les dossiers de gabarits d'un kit de méthode sont ignorés (leurs
fichiers décrivent une méthode, pas votre projet) — mais seul le préfixe de dossier
`_bmad` est reconnu aujourd'hui (son dossier de sortie `_bmad-output` reste lu). Un kit
installé sous un autre nom est lu comme vos propres fichiers et peut gonfler A1 et A2.

## Déclarer un dépôt sans produit

Un dépôt qui ne livre aucun produit (outillage, documentation, supervision) peut le
déclarer à sa racine dans un fichier `.evaluation.json` :

```
{"livre_un_produit": false}
```

A1 (backlog et user stories bien formées) et A2 (critères d'acceptation) deviennent
alors « non applicable » et sortent de la moyenne. Seule la valeur `false` exacte compte ;
un fichier absent ou illisible ne change rien. Les autres critères s'appliquent toujours.
Sur la page, ces deux critères portent alors la mention « note fondée sur des
déclarations » : c'est votre déclaration, pas une mesure, qui les écarte.

Une déclaration ne cache jamais une mesure : si la détection trouve malgré tout un
backlog réel dans le dépôt, A1 et A2 gardent leur note mesurée et la page affiche la
contradiction (« déclaré sans produit, mais un backlog est détecté »).

## Cas particuliers de la page

- Lancée depuis l'équipe qui a conçu cette mesure, la page montre **plusieurs projets**
  (un onglet par projet) ; chez vous, elle n'en montre qu'un : votre dépôt.
- Un bloc **« Dispositif (informatif) »** n'apparaît que lorsque cette équipe s'évalue
  elle-même : il décrit son propre outillage et n'est jamais noté.

## Lire une note

- La plupart des critères n'ont que **deux niveaux notés** : **présent (10/10)** si au
  moins un des signaux décrits est trouvé, **absent (2/10)** sinon.
- Deux critères ont une échelle : **B5** (niveaux d'orchestration 0 à 5 → 2, 4, 6, 8, 10 ;
  le niveau retenu est le dernier atteint sans sauter d'échelon) et **B7** (part
  d'exécutions en échec : 10 sous 5 %, puis 8, 6, 4 aux seuils 5 / 10 / 20 %, et 2 à
  partir de 35 % — seuils estimés, pas publiés).
- La note d'un référentiel est la moyenne de ses critères notés ; la **note globale** est
  la moyenne des deux référentiels. Tous les poids valent 1.
- Une note ne s'affiche jamais sans les critères qui la composent, ni sans sa date.

## Les états qui ne sont pas des notes

Aucun de ces états n'est un chiffre, et aucun n'entre dans une moyenne.

- **« non mesuré »** — le dépôt ne permet pas de conclure : pas d'historique git, clone
  partiel, trop peu de commits, pas de journal d'exécution, valeur entre deux seuils…
  Chaque critère dit dans sa partie 5 les cas où il est « non mesuré ». On ne devine
  jamais une note.
- **« non applicable »** — le critère ne concerne pas ce projet : B6 si le produit ne
  déclare aucune dépendance à un modèle d'IA (il n'a rien à maîtriser), et A1 / A2 si le
  dépôt se déclare sans produit (voir plus haut).
- **« périmé »** — la dernière mesure date de plus de **7 jours** ; la page affiche sa date
  au lieu d'un chiffre. Relancez la commande pour rafraîchir.
- **« jamais mesuré »** — aucune mesure n'existe encore.

## Verrou sécurité

Si un problème de sécurité de priorité 1 est signalé et reste ouvert (lu dans
`.claude/supervision/diagnostic.json` quand ce fichier existe), la note globale est
remplacée par **« bloquant »** : une moyenne ne doit pas masquer une faille. Sans ce
fichier, le verrou est simplement inactif ; A8 (hygiène de sécurité de base) reste mesuré
normalement.

Quand le verrou se ferme-t-il ? Chaque problème signalé peut recevoir une décision écrite
(fichier `.claude/supervision/arbitrages.json`). Le verrou ne se ferme que si la
**décision la plus récente** pour ce problème dit à la fois qu'il est **accepté** (elle
commence par « ACCEPTÉ » ou « ADOPTÉ ») **et que le correctif a été appliqué** (le mot
« APPLIQUÉ » figure dans sa première phrase, sans « non », « pas » ni « jamais » devant).
Accepter de corriger n'est pas avoir corrigé : « ACCEPTÉ, on le fera » laisse le verrou
ouvert. Restent aussi ouverts : un refus, une décision mise de côté, une acceptation
partielle ou reportée (« partiel », « non », « plus tard », « à appliquer » dans la
première phrase, quelle que soit la ponctuation), et toute décision illisible.

« La plus récente » se lit sur la date de la décision (champ `date`), pas sur sa place
dans le fichier. Une décision sans date compte comme plus ancienne que toute décision
datée ; à date égale, l'ordre du fichier départage.

Un problème encore **non prouvé** (marqué **[HYPOTHÈSE]** ou **[À VÉRIFIER]** sur la page)
bloque quand même, par prudence : la page l'étiquette pour que vous sachiez qu'il reste à
confirmer.

## Présence n'est pas exécution

La mesure constate qu'une chose **existe** dans le dépôt : un fichier de test, une chaîne
d'intégration, une règle, un journal. Elle ne dit pas que les tests passent, que la chaîne
est verte, ni que la règle est respectée. Une bonne note est une condition nécessaire, pas
une preuve de qualité.

## Ce qui demande un audit (non noté ici)

Certaines qualités exigent une lecture humaine du code ou des textes et ne sont donc pas
notées automatiquement :

- les qualités **I, N, V, E** de la grille INVEST (indépendance, négociabilité, valeur,
  estimabilité) des user stories — A1 n'en contrôle que la forme (rôle, critères
  d'acceptation, dépendance) ;
- la **maîtrise réelle** d'une solution qui embarque un modèle d'IA — B6 ne fait que
  repérer des protections (délai d'attente, plafond de sortie, validation, filtrage) ;
- la pertinence du choix de modèle tâche par tâche (B3), la qualité des prompts (B2) et
  des revues (A7).

## Rupture de série

Cette version est le **barème 3** (depuis le 2026-09-29) : critère B11 ajouté, rien
d'autre ne change. Une note qui a baissé peut n'avoir rien perdu : elle a été notée sur
davantage de critères ; la page affiche « v2 → v3 » pour chaque projet dont la note
bouge. Le barème v2 date du 2026-09-28 ; les notes v1 ne sont pas comparables et ne sont jamais recalculées ; la page le
rappelle en tête.
