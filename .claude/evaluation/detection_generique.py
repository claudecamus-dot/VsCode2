"""Generic, project-agnostic detection of engineering and agentic practices (stdlib only).

Two referentials: A (13 development practices) and B (10 agentic practices).

Frozen output contract::

    detecter(chemin) -> {critere: {"etat": "mesure" | "non mesure" | "non applicable",
                                   "signaux": [str, ...],
                                   "preuve": {"fonction": str, "fichier": str}}}

Reading rule: "mesure" + non-empty signals = practice present; "mesure" + empty
signals = practice absent; "non mesure" = the detector could not conclude (never
a guessed state); "non applicable" = the criterion does not concern this project.
Ratio-based criteria add an informative "ratio" key (niveau_orchestration adds "niveau", criteres_acceptance adds
"lien_ac_test"). No score is computed here; timestamps are the caller's business.

Development criteria walk the tree bounded in depth and skip tooling / template
directories (agent configuration, generated method kits, dependencies, VCS data,
template folders): their files describe practices in general, not this project's.
Method kits are recognised by a KNOWN directory prefix only (PREFIXES_KIT, today
``_bmad``); a kit installed under another name is read as the project's own files.
Agentic criteria read the agent configuration only at its conventional root
locations, and inside a skill folder only its SKILL.md (never its bundled assets).
"""
import ast
import json
import os
import re
import subprocess
import sys

PROFONDEUR_MAX = 6
MAX_FICHIERS = 20000
MAX_OCTETS_TEXTE = 512 * 1024
MAX_OCTETS_JOURNAL = 8 * 1024 * 1024
MAX_COMMITS = 500

DOSSIERS_EXCLUS = {
    ".git", ".claude", "node_modules", ".venv", "venv", "env", "__pycache__",
    "site-packages", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    "template", "templates", "_templates", "skeleton", "boilerplate", "vendor",
}
# Method-kit directories ship generic templates, not the project's own artefacts.
# PREFIXES_KIT lists the KNOWN kit / template directory prefixes; a directory whose
# name starts with one is skipped, except its OUTPUT directory (KIT_SORTIE), which
# holds the project's real planning artefacts and is kept. LIMIT: only these
# prefixes are recognised — a method kit installed under another directory name
# is read as the project's own files (extend the tuple to cover it).
PREFIXES_KIT = ("_bmad",)
KIT_SORTIE = {"_bmad-output"}

EXT_CODE = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".java", ".kt",
            ".go", ".rb", ".cs", ".php", ".rs", ".swift", ".scala", ".c", ".cpp",
            ".h", ".vue", ".svelte", ".gs"}
RE_TEST = re.compile(r"(^test_.+|.+_test\.[a-z]+$|.+\.(spec|test)\.[a-z]+$|.+Tests?\.(java|kt|cs)$)")
# Ratio criteria from git history (co_evolution_tests, tracabilite_demande_livrable): estimated thresholds, not measured
# ones — placed inside the empty gap of a bimodal distribution seen on 8 repos
# (0.00 vs 0.60-0.87); no published threshold exists.
SEUIL_PRESENT, SEUIL_ABSENT = 0.5, 0.2  # estimated thresholds (see above), not published ones
SEUIL_DOC_ABSENT = 0.1  # estimated threshold, not a published one
SEUIL_US = 0.5  # « at least half » of the stories / runs: estimated threshold, not a published one
SEUIL_RYTHME = 3  # commits on the agentic frame in 90 days (estimated threshold)

_CACHE = {}


def _exclu(nom):
    """True for tooling / template directories (DOSSIERS_EXCLUS, known kit prefixes
    such as ``_bmad`` in PREFIXES_KIT) — never for a kit's output directory."""
    if nom in DOSSIERS_EXCLUS:
        return True
    return nom.startswith(PREFIXES_KIT) and nom not in KIT_SORTIE


def _fichiers(racine):
    """Relative posix paths of the project's own files (bounded, excluded dirs pruned)."""
    out = []
    racine = os.path.abspath(racine)
    for d, dirs, files in os.walk(racine):
        rel = os.path.relpath(d, racine)
        prof = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = [x for x in dirs if not _exclu(x)] if prof < PROFONDEUR_MAX else []
        for f in files:
            out.append(f if rel == "." else (rel.replace(os.sep, "/") + "/" + f))
            if len(out) >= MAX_FICHIERS:
                return out
    return out


def _fichiers_agentic(racine):
    """Agent configuration files: root .claude/ tree, skills reduced to their SKILL.md."""
    out = []
    base = os.path.join(racine, ".claude")
    if not os.path.isdir(base):
        return out
    for d, dirs, files in os.walk(base):
        rel = os.path.relpath(d, racine).replace(os.sep, "/")
        prof = rel.count("/") + 1
        if rel == ".claude/skills":
            for s in sorted(dirs):
                if os.path.isfile(os.path.join(d, s, "SKILL.md")):
                    out.append(f"{rel}/{s}/SKILL.md")
            dirs[:] = []
            continue
        dirs[:] = [x for x in dirs if not _exclu(x)] if prof < PROFONDEUR_MAX else []
        out.extend(f"{rel}/{f}" for f in files)
        if len(out) >= MAX_FICHIERS:
            break
    return out


def _lire(racine, rel, limite=MAX_OCTETS_TEXTE):
    try:
        p = os.path.join(racine, rel)
        if os.path.getsize(p) > limite:
            return ""
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _git(racine, *args):
    """Run git; decode UTF-8 with replacement (never crash on a foreign byte)."""
    try:
        r = subprocess.run(["git", "-C", racine, *args], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _a_git(racine):
    return os.path.exists(os.path.join(racine, ".git"))


def _commits(racine):
    """Last commits as dicts {h, ae, an, parents, corps}; None when unreadable.

    `ae`/`an` are git's MAILMAP-resolved author email/name (%aE/%aN): with a
    `.mailmap`, two addresses of one person are one identity."""
    cle = ("commits", racine)
    if cle not in _CACHE:
        log = _git(racine, "log", "-n", str(MAX_COMMITS), "--format=%H%x1f%aE%x1f%aN%x1f%P%x1f%B%x1e")
        if log is None:
            _CACHE[cle] = None
        else:
            out = []
            for bloc in log.split("\x1e"):
                parts = bloc.strip("\n").split("\x1f", 4)
                if len(parts) == 5:
                    out.append({"h": parts[0].strip(), "ae": parts[1].strip().lower(),
                                "an": parts[2].strip().lower(),
                                "parents": parts[3].split(), "corps": parts[4]})
            _CACHE[cle] = out
    return _CACHE[cle]


def _suivis(racine):
    cle = ("suivis", racine)
    if cle not in _CACHE:
        s = _git(racine, "ls-files")
        _CACHE[cle] = None if s is None else set(s.splitlines())
    return _CACHE[cle]


def _res(fonction, etat, signaux=(), fichier="", **extra):
    d = {"etat": etat, "signaux": list(signaux),
         "preuve": {"fonction": fonction.__name__, "fichier": fichier}}
    d.update(extra)
    return d


def _est_test(rel):
    return bool(RE_TEST.match(rel.rsplit("/", 1)[-1]))


def _est_code(rel):
    return os.path.splitext(rel)[1].lower() in EXT_CODE


def _trailers(corps, noms):
    """Emails of the given trailers (e.g. Reviewed-by) in a commit body."""
    alternatives = "|".join(noms)
    rx = re.compile(rf"^(?:{alternatives}):.*?<([^>]+)>", re.I | re.M)
    return [m.lower() for m in rx.findall(corps)]


def _mailmap(racine, emails):
    """{email: canonical email} through the repo's `.mailmap` (`git check-mailmap`).

    Without a .mailmap (or if git fails) every address maps to itself: two
    addresses of one person then count as two — a stated limit, not a guess."""
    emails = sorted({e for e in emails if e})
    if not emails:
        return {}
    sortie = _git(racine, "check-mailmap", *[f"<{e}>" for e in emails])
    lignes = (sortie or "").splitlines()
    if len(lignes) != len(emails):
        return {e: e for e in emails}
    out = {}
    for e, ligne in zip(emails, lignes, strict=True):
        m = re.search(r"<([^>]*)>\s*$", ligne)
        out[e] = m.group(1).strip().lower() if m else e
    return out


# ------------------------------------------------------------------ ecosystems
# A tool-specific criterion only concludes « absent » for an ecosystem whose usual
# tools it knows (its COMPLETE set). For any other ecosystem it reports what it
# recognises (present) and otherwise « non mesure » with the reason — never an
# absence that would only mean « tool not listed here ».
MANIFESTES = {
    "pyproject.toml": "python", "setup.py": "python", "setup.cfg": "python", "Pipfile": "python",
    "package.json": "javascript", "pom.xml": "jvm", "build.gradle": "jvm",
    "build.gradle.kts": "jvm", "build.sbt": "jvm", "Gemfile": "ruby", "composer.json": "php",
    "go.mod": "go", "Cargo.toml": "rust", "mix.exs": "elixir", "Package.swift": "swift",
    "CMakeLists.txt": "c/c++", "stack.yaml": "haskell", "pubspec.yaml": "dart",
    "project.clj": "clojure", "deps.edn": "clojure", "rebar.config": "erlang",
    "build.zig": "zig",
}
EXT_ECOSYSTEME = {
    ".py": "python", ".js": "javascript", ".ts": "javascript", ".tsx": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".vue": "javascript",
    ".svelte": "javascript", ".gs": "javascript", ".java": "jvm", ".kt": "jvm",
    ".scala": "jvm", ".cs": "dotnet", ".rb": "ruby", ".php": "php", ".go": "go",
    ".rs": "rust", ".ex": "elixir", ".exs": "elixir", ".swift": "swift", ".c": "c/c++",
    ".cpp": "c/c++", ".h": "c/c++", ".hs": "haskell", ".dart": "dart", ".clj": "clojure",
    ".erl": "erlang", ".zig": "zig",
}


def _ecosysteme_dominant(racine, fichiers):
    """Ecosystem with the most source files (build manifests count when there is no
    source file at all); None when the repository holds neither."""
    cle = ("ecosysteme", racine)
    if cle not in _CACHE:
        compte, manif = {}, []
        for f in fichiers:
            nom = f.rsplit("/", 1)[-1]
            eco = EXT_ECOSYSTEME.get(os.path.splitext(nom)[1].lower())
            if eco:
                compte[eco] = compte.get(eco, 0) + 1
            if nom in MANIFESTES or re.match(r"requirements.*\.txt$", nom):
                manif.append(MANIFESTES.get(nom, "python"))
            elif nom.endswith((".csproj", ".sln")):
                manif.append("dotnet")
            elif nom.endswith(".cabal"):
                manif.append("haskell")
        for eco in manif:  # a manifest weighs one file, and breaks ties
            compte[eco] = compte.get(eco, 0) + 1
        _CACHE[cle] = max(sorted(compte), key=lambda e: compte[e]) if compte else None
    return _CACHE[cle]


def _absent_ou_non_mesure(fonction, racine, fichiers, complets):
    """« absent » when the dominant ecosystem is fully known to the criterion (or the
    repository has no code at all), otherwise « non mesure » with the reason."""
    eco = _ecosysteme_dominant(racine, fichiers)
    if eco is None or eco in complets:
        return _res(fonction, "mesure")
    return _res(fonction, "non mesure", [
        f"écosystème dominant « {eco} » : ses outils ne sont reconnus que par leur "
        "présence, leur absence ne se conclut pas"])


FICHIERS_CI = re.compile(r"^(\.github/workflows/[^/]+\.ya?ml|\.gitlab-ci\.yml|azure-pipelines\.yml|"
                         r"Jenkinsfile|\.circleci/config\.yml|bitbucket-pipelines\.yml|Makefile|"
                         r"justfile|Taskfile\.ya?ml)$")


# ====================================================================== A
# ------------------------------------------------------------------ tests_automatises
def tests_automatises(racine, fichiers):
    """Tests automatisés.

    Définition : le dépôt contient des fichiers de tests exécutables par une machine.
    Signaux exacts : fichiers nommés test_*, *_test.*, *.spec.*, *.test.*, *Test.java
    (et *Tests.java, .kt, .cs, .php, .swift), *_spec.rb ; en Rust, fichiers .rs sous
    tests/ ou attribut #[test] dans un fichier .rs ; en complément, script « test »
    réel dans package.json (le gabarit npm « no test specified » ne compte pas).
    Pourquoi : un test automatisé rejoue une vérification à coût nul à chaque
    changement (Google, « Software Engineering at Google », chap. 11).
    Permet de conclure : des tests existent dans le code du projet.
    Ne permet pas de conclure : qu'ils passent, ni qu'ils testent l'essentiel. Un
    script de test npm déclaré sans fichier de test reconnu : non mesuré (les tests
    peuvent être nommés autrement), jamais « absent ». « Absent » n'est conclu que si
    l'écosystème dominant du dépôt est Python, JavaScript/TypeScript, Java/Kotlin,
    .NET, Ruby, PHP, Go, Rust ou Elixir (conventions de nommage connues) ; pour un
    autre écosystème, aucun test reconnu donne « non mesuré ».
    """
    t = [f for f in fichiers if _est_test_etendu(racine, f)]
    npm = _script_test_npm(racine, fichiers)
    sig = [f"{len(t)} fichier(s) de test"] if t else []
    if npm:
        sig.append(f"script de test npm déclaré ({npm})")
    if npm and not t:
        return _res(tests_automatises, "non mesure",
                    [f"script de test déclaré dans {npm} mais aucun fichier de test reconnu"], npm)
    if not sig:
        return _absent_ou_non_mesure(tests_automatises, racine, fichiers, COMPLETS_TESTS)
    return _res(tests_automatises, "mesure", sig, t[0] if t else "")


COMPLETS_TESTS = {"python", "javascript", "jvm", "dotnet", "ruby", "php", "go", "rust", "elixir"}
RE_TEST_ETENDU = re.compile(r"(.+_spec\.rb$|.+Tests?\.(php|swift)$)")
RE_TEST_RUST = re.compile(r"#\[test\]")


def _est_test_etendu(racine, rel):
    """_est_test plus the conventions of other ecosystems (RSpec, PHPUnit, XCTest,
    Rust integration tests under tests/ and inline #[test] functions)."""
    if _est_test(rel) or RE_TEST_ETENDU.match(rel.rsplit("/", 1)[-1]):
        return True
    if rel.endswith(".rs"):
        return "/tests/" in "/" + rel or bool(RE_TEST_RUST.search(_lire(racine, rel)))
    return False


def _script_test_npm(racine, fichiers):
    """Path of the first package.json declaring a REAL ``scripts.test`` (not the npm placeholder)."""
    for f in fichiers:
        if f.rsplit("/", 1)[-1] != "package.json":
            continue
        try:
            test = ((json.loads(_lire(racine, f) or "{}") or {}).get("scripts") or {}).get("test")
        except (ValueError, AttributeError):
            continue
        if isinstance(test, str) and test.strip() and "no test specified" not in test:
            return f
    return ""


# ------------------------------------------------------------------ couverture_configuree
RE_COUV = re.compile(r"pytest-cov|--cov\b|\[tool\.coverage|\[coverage:|\bc8\b|\bnyc\b|--coverage|"
                     r"collectCoverage|jacoco|coverlet|simplecov|excoveralls", re.I)
FICHIERS_COUV = {".coveragerc", ".nycrc", ".nycrc.json", ".c8rc", ".c8rc.json",
                 "tarpaulin.toml", ".tarpaulin.toml"}
CONFIGS_COUV = {"pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "package.json",
                "pom.xml", "build.gradle", "build.gradle.kts", "jest.config.js",
                "vitest.config.ts", "vitest.config.js", "Gemfile", "mix.exs"}


def couverture_configuree(racine, fichiers):
    """Mesure de couverture configurée.

    Définition : l'outillage qui mesure quelle part du code les tests exécutent est branché.
    Signaux exacts : .coveragerc/.nycrc/.c8rc/tarpaulin.toml, ou pytest-cov, --cov,
    [tool.coverage], c8, nyc, --coverage, collectCoverage, jacoco, coverlet, simplecov
    dans un fichier de configuration ou de dépendances (dont *.csproj) ; excoveralls
    dans mix.exs ; dans la chaîne d'intégration ou un Makefile : go test -cover /
    -coverprofile, cargo tarpaulin, cargo llvm-cov, grcov.
    Pourquoi : sans mesure, les zones jamais testées restent invisibles
    (documentation coverage.py, Istanbul, JaCoCo).
    Permet de conclure : l'équipe peut mesurer sa couverture.
    Ne permet pas de conclure : le taux obtenu, ni qu'il est suivi. « Absent » n'est
    conclu que si l'écosystème dominant est Python, JavaScript/TypeScript, Java/Kotlin
    ou Ruby ; ailleurs (Go, Rust, Elixir, .NET…), aucun signal donne « non mesuré ».
    """
    for f in fichiers:
        nom = f.rsplit("/", 1)[-1]
        if nom in FICHIERS_COUV:
            return _res(couverture_configuree, "mesure", [f"fichier {nom}"], f)
        if nom in CONFIGS_COUV or re.match(r"requirements.*\.txt$", nom) or nom.endswith(".csproj"):
            m = RE_COUV.search(_lire(racine, f))
            if m:
                return _res(couverture_configuree, "mesure", [f"{m.group(0)} dans {nom}"], f)
        if FICHIERS_CI.match(f):
            m = RE_COUV_CI.search(_lire(racine, f))
            if m:
                return _res(couverture_configuree, "mesure", [f"{m.group(0)} dans {f}"], f)
    return _absent_ou_non_mesure(couverture_configuree, racine, fichiers, COMPLETS_COUV)


COMPLETS_COUV = {"python", "javascript", "jvm", "ruby"}
RE_COUV_CI = re.compile(r"-coverprofile|go test\b[^\n]*\s-cover\b|cargo[ -]tarpaulin|cargo llvm-cov|grcov")


# ------------------------------------------------------------------ test_artefact_reel
RE_E2E = re.compile(r"playwright|selenium|cypress|puppeteer|webdriver|TestClient\(|"
                    r"requests\.(get|post)\(|httpx\.|supertest|Presentation\(|fitz\.open|"
                    r"load_workbook\(|pdfplumber|docx\.Document\(|subprocess\.run\(|"
                    r"httptest\.|exec\.Command\(|assert_cmd|std::process::Command|reqwest::", re.I)


def test_artefact_reel(racine, fichiers):
    """Résultat final exercé par le canal de l'utilisateur.

    Définition : au moins un test exerce le résultat final par le même canal que
    l'utilisateur — page servie et pilotée, appel HTTP, document produit puis rouvert,
    commande lancée comme en production — quel que soit l'outil de test.
    Signaux exacts : dossier e2e/ ou, dans un fichier de test, pilotage de navigateur
    (playwright, selenium, cypress, puppeteer, webdriver), client HTTP (TestClient,
    requests, httpx, supertest), réouverture d'un document (pptx, pdf, xlsx, docx) ou
    lancement d'une commande (subprocess.run) ; en Go, httptest. ou exec.Command( ;
    en Rust, assert_cmd, std::process::Command ou reqwest::.
    Pourquoi : un test unitaire vert ne garantit pas que l'assemblage fonctionne
    (M. Fowler, « TestPyramid » ; « Broad Stack Test »).
    Permet de conclure : un chemin de test de bout en bout existe.
    Ne permet pas de conclure : qu'il est exécuté régulièrement, qu'il passe, ni
    qu'un humain a regardé le rendu — la vérification visuelle humaine n'est pas couverte.
    « Absent » n'est conclu que si l'écosystème dominant est Python ou
    JavaScript/TypeScript ; ailleurs, aucun signal donne « non mesuré ».
    """
    for f in fichiers:
        if "/e2e/" in "/" + f and _est_code(f):
            return _res(test_artefact_reel, "mesure", ["dossier e2e"], f)
    for f in fichiers:
        if _est_code(f) and _est_test_etendu(racine, f):
            m = RE_E2E.search(_lire(racine, f))
            if m:
                return _res(test_artefact_reel, "mesure", [f"{m.group(0)} dans un test"], f)
    return _absent_ou_non_mesure(test_artefact_reel, racine, fichiers, COMPLETS_E2E)


COMPLETS_E2E = {"python", "javascript"}


# ------------------------------------------------------------------ co_evolution_tests
def co_evolution_tests(racine, fichiers):
    """Tests et code évoluent ensemble.

    Définition : part des commits des 90 derniers jours qui modifient du code
    source ET touchent aussi des tests.
    Signaux exacts : git log --since=90.days --name-only ; dénominateur = commits
    touchant du code source hors tests ; présent si ratio >= 0,5, absent si < 0,2
    (seuils estimés, non publiés).
    Pourquoi : des tests écrits avec le code suivent son évolution au lieu de
    dériver (K. Beck, « Test-Driven Development by Example » ; DORA, test automation).
    Permet de conclure : l'habitude de livrer code et tests dans le même geste.
    Ne permet pas de conclure : que les tests sont écrits AVANT le code (TDD),
    ni leur qualité. Entre les seuils, sans historique git, clone partiel ou
    moins de 10 commits de code : non mesuré.
    """
    if not _a_git(racine):
        return _res(co_evolution_tests, "non mesure", ["pas d'historique git"])
    if (_git(racine, "rev-parse", "--is-shallow-repository") or "").strip() == "true":
        return _res(co_evolution_tests, "non mesure", ["clone partiel"])
    log = _git(racine, "log", "--since=90.days", "--name-only", "--format=%x1e")
    if log is None:
        return _res(co_evolution_tests, "non mesure", ["git log illisible"])
    code = avec = 0
    for bloc in log.split("\x1e"):
        chemins = [ligne.strip() for ligne in bloc.splitlines() if ligne.strip()]
        chemins = [c for c in chemins if not any(_exclu(p) for p in c.split("/")[:-1])]
        tests = [c for c in chemins if _est_test(c) or "/tests/" in "/" + c or c.startswith("test/")]
        src = [c for c in chemins if _est_code(c) and c not in tests]
        if src:
            code += 1
            if tests:
                avec += 1
    if code < 10:
        return _res(co_evolution_tests, "non mesure", [f"{code} commit(s) de code sur 90 j (< 10)"])
    ratio = avec / code
    info = f"{avec}/{code} commits de code touchent aussi des tests"
    if ratio >= SEUIL_PRESENT:
        return _res(co_evolution_tests, "mesure", [info], ratio=ratio)
    if ratio < SEUIL_ABSENT:
        return _res(co_evolution_tests, "mesure", [], ratio=ratio)
    return _res(co_evolution_tests, "non mesure", [info + " (zone intermédiaire)"], ratio=ratio)


# ------------------------------------------------------------------ code_documente
def code_documente(racine, fichiers):
    """Code documenté.

    Définition : part des fonctions publiques Python dotées d'une docstring non triviale
    (au moins 10 caractères).
    Signaux exacts : analyse syntaxique (module ast) des fichiers .py hors tests ;
    fonction publique = nom sans « _ » initial, au niveau module ou dans une classe
    publique. Absent sous 0,1 (seuil estimé), présent au-delà ; ratio affiché à titre
    informatif.
    Pourquoi : la docstring est la documentation que l'outillage retrouve (PEP 257).
    Permet de conclure : une habitude de documenter l'interface du code.
    Ne permet pas de conclure : la justesse des docstrings, ni la documentation hors
    Python. Sans fonction publique Python analysable : non mesuré.
    """
    total = doc = 0
    for f in fichiers:
        if not f.endswith(".py") or _est_test(f) or f.startswith("tests/"):
            continue
        try:
            arbre = ast.parse(_lire(racine, f))
        except (SyntaxError, ValueError):
            continue
        noeuds = list(arbre.body)
        for n in arbre.body:
            if isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
                noeuds.extend(n.body)
        for n in noeuds:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_"):
                total += 1
                if len((ast.get_docstring(n) or "").strip()) >= 10:
                    doc += 1
    if not total:
        return _res(code_documente, "non mesure", ["aucune fonction publique Python analysable"])
    ratio = doc / total
    if ratio < SEUIL_DOC_ABSENT:
        return _res(code_documente, "mesure", [], ratio=ratio)
    return _res(code_documente, "mesure", [f"{doc}/{total} fonctions publiques documentées"],
                ratio=ratio)


# ------------------------------------------------------------------ integration_continue
RE_CHANGELOG = re.compile(r"^(CHANGELOG|CHANGES|HISTORY|RELEASE[_-]?NOTES)(\.[a-z]+)?$", re.I)


def integration_continue(racine, fichiers):
    """Intégration et livraison continues.

    Définition : une chaîne automatique rejoue les vérifications à chaque envoi, et
    les livraisons sont repérées (versions étiquetées, notes de version).
    Signaux exacts : .github/workflows/*.yml, .gitlab-ci.yml, azure-pipelines.yml,
    Jenkinsfile, .circleci/config.yml, bitbucket-pipelines.yml ; étiquettes git
    (git tag) ; dossier releases/ ; fichier CHANGELOG*, CHANGES*, HISTORY*, RELEASE_NOTES*.
    Pourquoi : intégration et livraison continues sont des capacités prédictives de la
    performance de livraison (DORA, « Continuous integration », « Continuous delivery »).
    Permet de conclure : une chaîne d'intégration est déclarée, des livraisons sont tracées.
    Ne permet pas de conclure : que la chaîne tourne, qu'elle est verte, ni la
    fréquence réelle de livraison.
    """
    sig, fichier = [], ""
    for f in fichiers:
        if (f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))) or f in {
                ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile",
                ".circleci/config.yml", "bitbucket-pipelines.yml"}:
            sig.append(f"intégration continue : {f}")
            fichier = f
            break
    if _a_git(racine):
        tags = [t for t in (_git(racine, "tag", "--list") or "").splitlines() if t.strip()]
        if tags:
            sig.append(f"{len(tags)} version(s) étiquetée(s)")
    if any(f.startswith("releases/") for f in fichiers):
        sig.append("dossier releases/")
    for f in fichiers:
        if RE_CHANGELOG.match(f.rsplit("/", 1)[-1]) and "/" not in f:
            sig.append(f"notes de version : {f}")
            fichier = fichier or f
            break
    return _res(integration_continue, "mesure", sig, fichier)


# ------------------------------------------------------------------ linter_configure
RE_LINT_NOM = re.compile(r"^(\.eslintrc.*|eslint\.config\..*|\.flake8|\.?ruff\.toml|\.pylintrc|"
                         r"pylintrc|\.prettierrc.*|biome\.json|\.golangci\.(ya?ml|toml|json)|checkstyle\.xml|"
                         r"\.rubocop\.yml|\.stylelintrc.*|\.markdownlint.*|\.?clippy\.toml|\.?rustfmt\.toml|"
                         r"\.credo\.exs|phpcs\.xml(\.dist)?|phpstan\.neon(\.dist)?|\.php-cs-fixer(\.dist)?\.php)$")
RE_LINT_CFG = re.compile(r"\[tool\.(ruff|pylint|flake8|black|isort)|\[flake8\]|\"eslint\"|\"lint\"\s*:")


def linter_configure(racine, fichiers):
    """Analyseur statique configuré.

    Définition : un outil de style et de défauts courants (linter) est configuré.
    Signaux exacts : fichiers .eslintrc*, eslint.config.*, .flake8, ruff.toml, .pylintrc,
    .prettierrc*, biome.json, .golangci.yml/.yaml/.toml/.json, checkstyle.xml,
    .rubocop.yml, clippy.toml, rustfmt.toml, .credo.exs, phpcs.xml, phpstan.neon,
    .php-cs-fixer.php, ou sections [tool.ruff]/[tool.pylint]/[flake8] ou script
    « lint » dans la configuration ; dans la chaîne d'intégration ou un Makefile :
    cargo clippy, cargo fmt, golangci-lint, mix credo.
    Pourquoi : il attrape mécaniquement une classe de défauts avant la revue
    (Google, « Software Engineering at Google », chap. 20).
    Permet de conclure : l'outil est configuré.
    Ne permet pas de conclure : qu'il est lancé, ni que le code le respecte.
    « Absent » n'est conclu que si l'écosystème dominant est Python,
    JavaScript/TypeScript ou Ruby ; ailleurs (Go, Rust, Elixir, Java…), aucun signal
    donne « non mesuré ».
    """
    for f in fichiers:
        if RE_LINT_NOM.match(f.rsplit("/", 1)[-1]):
            return _res(linter_configure, "mesure", [f"fichier {f}"], f)
    for f in fichiers:
        if f.rsplit("/", 1)[-1] in {"pyproject.toml", "setup.cfg", "tox.ini", "package.json"}:
            m = RE_LINT_CFG.search(_lire(racine, f))
            if m:
                return _res(linter_configure, "mesure", [f"{m.group(0)} dans {f}"], f)
        if FICHIERS_CI.match(f):
            m = RE_LINT_CI.search(_lire(racine, f))
            if m:
                return _res(linter_configure, "mesure", [f"{m.group(0)} dans {f}"], f)
    return _absent_ou_non_mesure(linter_configure, racine, fichiers, COMPLETS_LINT)


COMPLETS_LINT = {"python", "javascript", "ruby"}
RE_LINT_CI = re.compile(r"cargo clippy|cargo fmt|golangci-lint|mix credo")


# ------------------------------------------------------------------ revue_avant_integration
def revue_avant_integration(racine, fichiers):
    """Revue avant intégration par une autre personne.

    Définition : avant d'entrer dans la branche principale, un changement est approuvé
    par un compte DIFFÉRENT de son auteur, et la trace en reste dans le dépôt.
    Signaux exacts : fichier CODEOWNERS désignant au moins un relecteur qui n'est PAS
    parmi les auteurs des 500 derniers commits, un CODEOWNERS qui ne nomme que les
    auteurs ne comptant pas, et une équipe @org/equipe seule (membres illisibles hors
    forge) laissant le critère non mesuré faute d'autre signal ; dans ces
    commits, mention « Reviewed-by: » d'une adresse différente de celle de l'auteur ;
    commit de fusion dont l'auteur diffère de l'auteur de la branche fusionnée.
    Les identités sont résolues par le `.mailmap` du dépôt s'il existe (formes %aE/%aN
    de git, `git check-mailmap`) : deux adresses d'une même personne y sont une seule
    identité. Limite : sans .mailmap, deux adresses d'une même personne comptent pour
    deux personnes.
    Une co-signature par un outil d'IA (Co-Authored-By) ne compte jamais ici : elle dit
    qui écrit, pas qui approuve.
    Pourquoi : la revue par un pair diffuse la connaissance et attrape des défauts
    (Google Engineering Practices, « Code Review »).
    Permet de conclure : une approbation par un tiers est tracée dans git.
    Ne permet pas de conclure : la qualité des revues, ni une revue faite hors git
    (outil de forge non répliqué dans l'historique).
    """
    codeowners = next((f for f in fichiers if f.rsplit("/", 1)[-1] == "CODEOWNERS"), "")
    if not _a_git(racine):
        # Without history, the owners cannot be compared with the authors.
        return _res(revue_avant_integration, "non mesure", ["pas d'historique git"])
    cs = _commits(racine)
    if cs is None:
        return _res(revue_avant_integration, "non mesure", ["git log illisible"])
    sig, fichier, raisons = [], "", []
    trailers = {c["h"]: _trailers(c["corps"], ["Reviewed-by"]) for c in cs}
    owners = _owners(_lire(racine, codeowners)) if codeowners else []
    carte = _mailmap(racine, [e for es in trailers.values() for e in es]
                     + [o for o in owners if not o.startswith("@")])
    emails_auteurs = {c["ae"] for c in cs}
    noms_auteurs = {c["an"].replace(" ", "") for c in cs} | {e.split("@")[0] for e in emails_auteurs}
    if codeowners:
        tiers, equipes = [], []
        for o in owners:
            if o.startswith("@") and "/" in o:
                equipes.append(o)          # team membership is not readable offline
            elif o.startswith("@"):
                if o[1:] not in noms_auteurs:
                    tiers.append(o)
            elif carte.get(o, o) not in emails_auteurs:
                tiers.append(o)
        if tiers:
            sig.append(f"relecteurs désignés hors auteurs (CODEOWNERS : {len(tiers)})")
            fichier = codeowners
        elif equipes:
            raisons.append("CODEOWNERS ne désigne que des équipes (membres non lisibles)")
    revues = sum(1 for c in cs if any(carte.get(e, e) != c["ae"] for e in trailers[c["h"]]))
    auteurs = {c["h"]: c["ae"] for c in cs}
    fusions = sum(1 for c in cs if len(c["parents"]) > 1
                  and auteurs.get(c["parents"][1], c["ae"]) != c["ae"])
    if revues:
        sig.append(f"{revues} commit(s) relus par un tiers (Reviewed-by)")
    if fusions:
        sig.append(f"{fusions} fusion(s) par un autre compte que l'auteur")
    if not sig and raisons:
        return _res(revue_avant_integration, "non mesure", raisons, codeowners)
    return _res(revue_avant_integration, "mesure", sig, fichier)


def _owners(texte):
    """Owners listed in a CODEOWNERS file (@user, @org/team, email), lower-cased."""
    out = []
    for ligne in texte.splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if not ligne or (ligne.startswith("[") and ligne.endswith("]")):
            continue
        out += [t.lower() for t in ligne.split()[1:] if "@" in t]
    return out


# ------------------------------------------------------------------ epics_us_bien_formees / criteres_acceptance
RE_NOM_BACKLOG = re.compile(r"(epic|stor(y|ies)|backlog|roadmap|prd|user[-_ ]?stor)", re.I)
RE_ITEM = re.compile(r"^\s*(#{1,6}\s*(epic|story|us[-\s]?\d|user story|feature)|[-*]\s*\[[ xX]\])", re.I | re.M)
# Bounded on LETTERS (not \b, which does not split on "_"): « product_vision »
# matches, « projets-supervision » does not.
RE_NOM_CADRAGE = re.compile(r"(?<![a-z])(prd|brief|vision|persona|cadrage)(?![a-z])", re.I)
RE_CADRAGE = re.compile(r"persona|probl[eè]me|problem statement|proposition de valeur|value proposition|"
                        r"utilisateurs? cibles?|target users?", re.I)
RE_TITRE = re.compile(r"^#{1,6}\s+(.*)$", re.M)
RE_TITRE_US = re.compile(r"\b(story|user story|us[-\s]?\d+)\b", re.I)
RE_ROLE = re.compile(r"\b(en tant qu[e']|as an? )", re.I)
RE_AC = re.compile(r"acceptance criteria|crit[eè]res? d.acceptation|\bgiven\b|[ée]tant donn[ée]", re.I)
RE_DEP = re.compile(r"d[ée]pend|depends on|dependenc|pr[ée]requis|prerequisite|blocked by", re.I)
RE_REF_AC = re.compile(r"\b(story|us)[\s-]?\d+(\.\d+)?|\bAC[\s-]?\d+", re.I)


def _markdown(fichiers):
    return [f for f in fichiers if f.lower().endswith((".md", ".markdown", ".txt"))]


def _stories(racine, fichiers):
    """(file, block text) for each user story found in the project's own markdown."""
    cle = ("stories", racine)
    if cle in _CACHE:
        return _CACHE[cle]
    out = []
    for f in _markdown(fichiers):
        txt = _lire(racine, f)
        titres = list(RE_TITRE.finditer(txt))
        for i, m in enumerate(titres):
            fin = titres[i + 1].start() if i + 1 < len(titres) else len(txt)
            bloc = txt[m.end():fin]
            if RE_TITRE_US.search(m.group(1)) and (RE_ROLE.search(bloc) or len(bloc.strip()) > 20):
                out.append((f, bloc))
    _CACHE[cle] = out
    return out


def epics_us_bien_formees(racine, fichiers):
    """Backlog présent et user stories bien formées.

    Définition : le projet tient, dans ses propres fichiers (jamais dans des gabarits
    d'outillage), un backlog d'épiques et de user stories dont la forme est exploitable :
    rôle énoncé, critères d'acceptation attachés, dépendance déclarée. Le cadrage produit
    (persona, problème, valeur) est relevé comme signal complémentaire.
    Signaux exacts : fichier nommé epic/story/backlog/roadmap/prd avec au moins 2
    éléments (titre « Epic/Story/US n » ou case à cocher) ; fichier prd/brief/vision/
    persona portant au moins 2 marqueurs de cadrage ; pour chaque story, trois contrôles
    de forme (« En tant que… »/« As a… », critères d'acceptation, dépendance), chacun
    retenu s'il couvre au moins la moitié des stories. Présent si rôle ET critères sont
    retenus ; la dépendance est un signal, pas une condition.
    Pourquoi : un besoin écrit et découpé rend le travail discutable et priorisable
    (Scrum Guide 2020, « Product Backlog ») ; la grille INVEST (B. Wake, 2003) décrit
    une story exploitable.
    Permet de conclure : un backlog existe et ses stories ont une forme exploitable.
    Ne permet pas de conclure : les qualités I/N/V/E d'INVEST (indépendance,
    négociabilité, valeur, estimabilité), qui demandent une lecture humaine ; ni que le
    backlog est à jour ou bien priorisé. Les gabarits d'un kit de méthode ne sont
    écartés que si le kit est installé sous un nom de dossier connu (aujourd'hui
    _bmad) ; sous un autre nom, ils seraient lus comme le backlog du projet.
    Backlog sans story analysable : non mesuré.
    """
    sig, fichier = [], ""
    for f in _markdown(fichiers):
        nom = f.rsplit("/", 1)[-1]
        if RE_NOM_BACKLOG.search(nom):
            n = len(RE_ITEM.findall(_lire(racine, f)))
            if n >= 2:
                sig.append(f"backlog {f} ({n} éléments)")
                fichier = fichier or f
        if RE_NOM_CADRAGE.search(nom):
            marques = {m.group(0).lower() for m in RE_CADRAGE.finditer(_lire(racine, f))}
            if len(marques) >= 2:
                sig.append(f"cadrage produit {f}")
                fichier = fichier or f
    st = _stories(racine, fichiers)
    if not st:
        if sig:
            return _res(epics_us_bien_formees, "non mesure", sig + ["aucune user story analysable"], fichier)
        return _res(epics_us_bien_formees, "mesure")
    n = len(st)
    retenus = {}
    for nom, rx in (("role", RE_ROLE), ("criteres", RE_AC), ("dependance", RE_DEP)):
        k = sum(1 for _, b in st if rx.search(b))
        if k / n >= SEUIL_US:
            retenus[nom] = f"{nom} {k}/{n}"
    if "role" in retenus and "criteres" in retenus:
        return _res(epics_us_bien_formees, "mesure", sig + list(retenus.values()), fichier or st[0][0])
    return _res(epics_us_bien_formees, "mesure", [], st[0][0])


def criteres_acceptance(racine, fichiers):
    """Critères d'acceptation.

    Définition : part des user stories qui portent des critères d'acceptation, et
    lien de ces critères vers les tests.
    Signaux exacts : section « Acceptance Criteria » / « Critères d'acceptation », ou
    formulation Given/When/Then (y compris des « **Given** » seuls, sans When/Then).
    Présent si au moins la moitié des stories en portent. Signal complémentaire :
    fichiers de test qui citent une story ou un critère (« Story 1.2 », « US-3 », « AC2 »).
    Pourquoi : un critère écrit avant le développement dit quand la story est finie
    (D. North, « Introducing BDD », 2006).
    Permet de conclure : l'habitude d'écrire les attendus, et s'ils sont reliés aux tests.
    Ne permet pas de conclure : que ces critères sont effectivement vérifiés ou tenus.
    Sans story trouvée : non mesuré.
    """
    st = _stories(racine, fichiers)
    if not st:
        return _res(criteres_acceptance, "non mesure", ["aucune user story trouvée"])
    k = sum(1 for _, b in st if RE_AC.search(b))
    ratio = k / len(st)
    liens = sum(1 for f in fichiers if _est_test(f) and _est_code(f) and RE_REF_AC.search(_lire(racine, f)))
    sig = []
    if ratio >= SEUIL_US:
        sig.append(f"{k}/{len(st)} stories avec critères")
        if liens:
            sig.append(f"{liens} fichier(s) de test citent une story ou un AC")
    return _res(criteres_acceptance, "mesure", sig, st[0][0], ratio=ratio, lien_ac_test=liens)


# ------------------------------------------------------------------ securite_base
RE_SECRET = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|"
                       r"ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{32,}")


def securite_base(racine, fichiers):
    """Hygiène de sécurité de base.

    Définition : les fichiers de secrets locaux sont exclus du suivi git et aucun
    secret reconnaissable n'est versionné.
    Signaux exacts : .gitignore couvrant .env ; aucun motif de clé (AWS AKIA…, clé
    privée PEM, jeton GitHub ghp_, Slack xox*, sk-…) dans les fichiers suivis.
    Pourquoi : un secret committé reste dans l'historique (OWASP Top 10, A07 ;
    GitHub, « secret scanning »).
    Permet de conclure : l'hygiène minimale est en place.
    Ne permet pas de conclure : l'absence de toute faille, ni de secrets à un format
    non reconnu. Sans historique git : non mesuré.
    """
    if not _a_git(racine):
        return _res(securite_base, "non mesure", ["pas d'historique git"])
    suivis = _suivis(racine)
    if suivis is None:
        return _res(securite_base, "non mesure", ["git ls-files illisible"])
    for f in sorted(suivis)[:MAX_FICHIERS]:
        if RE_SECRET.search(_lire(racine, f)):
            return _res(securite_base, "mesure", [], f)
    ign = _lire(racine, ".gitignore")
    if re.search(r"^\s*(\*\*/)?\.env(\*|\.\*)?\s*$", ign, re.M):
        return _res(securite_base, "mesure", [".env exclu par .gitignore", "aucun secret reconnu"],
                    ".gitignore")
    return _res(securite_base, "mesure", [], ".gitignore")


# ------------------------------------------------------------------ decisions_conception_tracees
RE_DECISION = re.compile(r"(^|/)(adr|adrs|decisions?)/[^/]+\.md$|(^|/)(architecture|ARCHITECTURE)[^/]*\.md$|"
                         r"(^|/)docs?/architecture/[^/]+\.md$|decision[^/]*\.md$", re.I)
RE_DATE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b|^\s*\**date\**\s*:", re.I | re.M)


def decisions_conception_tracees(racine, fichiers):
    """Décisions de conception tracées.

    Définition : les décisions d'architecture sont écrites, datées et versionnées avec
    le code (registre de décisions dit « ADR », ou document d'architecture).
    Signaux exacts : fichiers .md dans un dossier adr/, adrs/, decisions/, doc(s)/
    architecture/, ou nommés architecture*.md / *decision*.md, suivis par git ; une date
    (AAAA-MM-JJ ou « Date: ») dans le fichier ; date de dernière révision (git log).
    Pourquoi : une décision non écrite se re-débat et se perd avec les personnes
    (M. Nygard, « Documenting Architecture Decisions », 2011 ; adr.github.io).
    Permet de conclure : des décisions sont tracées et quand elles ont été révisées.
    Ne permet pas de conclure : que les décisions sont bonnes, ni qu'elles sont
    appliquées dans le code. Sans historique git : non mesuré.
    """
    if not _a_git(racine):
        return _res(decisions_conception_tracees, "non mesure", ["pas d'historique git"])
    suivis = _suivis(racine) or set()
    docs = [f for f in fichiers if RE_DECISION.search(f) and f in suivis]
    dates = [f for f in docs if RE_DATE.search(_lire(racine, f))]
    if not dates:
        return _res(decisions_conception_tracees, "mesure", [], docs[0] if docs else "")
    sig = [f"{len(docs)} document(s) de décision versionné(s), {len(dates)} daté(s)"]
    dern = (_git(racine, "log", "-1", "--format=%cs", "--", *docs[:50]) or "").strip()
    if dern:
        sig.append(f"dernière révision {dern}")
    return _res(decisions_conception_tracees, "mesure", sig, dates[0])


# ------------------------------------------------------------------ tracabilite_demande_livrable
RE_REF_TICKET = re.compile(r"(?<![\w/&])#\d+\b|\b(?:US|story|issue|ticket)[\s#-]?\d+(?:\.\d+)?\b", re.I)
RE_REF_JIRA = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
FAUX_TICKETS = {"UTF", "SHA", "ISO", "RFC", "HTTP", "MD", "X"}
# A non-empty « Refs: <id> » trailer LINE (footer, not free text) — same format as the
# hub hook .claude/hooks/warn_commit_sans_ref.py; a test feeds both the same messages.
RE_REFS = re.compile(r"^\s*Refs\s*:\s*\S", re.I | re.M)


def _cite_ticket(corps):
    texte = "\n".join(ligne for ligne in corps.splitlines()
                      if not re.match(r"^[\w-]+-by:", ligne.strip(), re.I))
    if RE_REFS.search(texte) or RE_REF_TICKET.search(texte):
        return True
    return any(m.group(1) not in FAUX_TICKETS for m in RE_REF_JIRA.finditer(texte))


def tracabilite_demande_livrable(racine, fichiers):
    """Traçabilité de la demande au livrable.

    Définition : les commits citent la demande qu'ils servent (user story, ticket,
    issue), ce qui permet de remonter d'une ligne de code au besoin.
    Signaux exacts : dans les 500 derniers messages de commit (lignes de signature
    exclues), une référence « #123 », « US-4 », « story 1.2 », « issue 7 », « ticket 9 »
    ou « PROJ-42 » (hors UTF-8, SHA-256…), ou une ligne de pied « Refs: <id> » non
    vide (« Refs: VScode5:<slug> », « Refs: run 2026-09-29T10:12 ») ; présent si
    au moins la moitié des commits en portent, absent sous 20 % (seuils estimés, non publiés).
    Pourquoi : lier le changement à sa demande rend l'impact et l'origine vérifiables
    (Conventional Commits, pied « Refs » ; GitHub/GitLab, « closing keywords »).
    Permet de conclure : l'habitude de relier le code à une demande tracée.
    Ne permet pas de conclure : que la demande citée existe ou est satisfaite.
    Entre les seuils, sans historique git ou avec moins de 10 commits : non mesuré.
    """
    if not _a_git(racine):
        return _res(tracabilite_demande_livrable, "non mesure", ["pas d'historique git"])
    cs = _commits(racine)
    if cs is None:
        return _res(tracabilite_demande_livrable, "non mesure", ["git log illisible"])
    if len(cs) < 10:
        return _res(tracabilite_demande_livrable, "non mesure", [f"{len(cs)} commit(s) (< 10)"])
    k = sum(1 for c in cs if _cite_ticket(c["corps"]))
    ratio = k / len(cs)
    info = f"{k}/{len(cs)} commits citent une demande"
    if ratio >= SEUIL_PRESENT:
        return _res(tracabilite_demande_livrable, "mesure", [info], ratio=ratio)
    if ratio < SEUIL_ABSENT:
        return _res(tracabilite_demande_livrable, "mesure", [], ratio=ratio)
    return _res(tracabilite_demande_livrable, "non mesure", [info + " (zone intermédiaire)"], ratio=ratio)


# ====================================================================== B
RE_CADRE = re.compile(r"^(CLAUDE\.md|AGENTS\.md|GEMINI\.md|\.cursorrules|\.windsurfrules|"
                      r"\.github/copilot-instructions\.md|\.github/(agents|instructions)/[^/]+\.md|"
                      r"\.claude/(agents|rules|commands)/[^/]+\.md|\.claude/settings\.json|"
                      r"\.claude/skills/[^/]+/SKILL\.md|\.cursor/rules/[^/]+)$")
RE_MEMOIRE = re.compile(r"^(MEMORY\.md|\.claude/(memory|context)/[^/]+\.md|docs/(context|memory)[^/]*\.md)$")
RE_AGENT_DEF = re.compile(r"^(\.claude/agents|\.github/agents|agents)/[^/]+\.md$")
RE_LLM = re.compile(r"(?<![\w-])(anthropic|@anthropic-ai/sdk|openai|langchain[\w-]*|llama[-_]index|"
                    r"mistralai|google-generativeai|google-genai|ollama|litellm|crewai|autogen|"
                    r"semantic-kernel|@ai-sdk/[\w-]+)(?![\w-])", re.I)
CONFIGS_DEP = {"requirements.txt", "pyproject.toml", "package.json", "Pipfile", "setup.py",
               "setup.cfg", "go.mod", "pom.xml", "build.gradle", "Gemfile"}
PROTECTIONS_LLM = (
    ("délai d'attente", re.compile(r"\btimeout\s*[=:]", re.I)),
    ("plafond de sortie", re.compile(r"max_tokens|max_output_tokens|maxTokens", re.I)),
    ("sortie validée", re.compile(r"pydantic|jsonschema|response_format|json_schema|tool_choice|zod", re.I)),
    ("filtrage des entrées", re.compile(r"moderation|guardrail|sanitiz|prompt.?injection|allowlist", re.I)),
)
RE_DESTRUCTIF = re.compile(r"reset\s+--hard|push\s+(--force|-f)\b|clean\s+-[a-z]*f|branch\s+-D|"
                           r"checkout\s+--\s|rm\s+-rf", re.I)
RE_IA_COSIGN = re.compile(r"^Co-Authored-By:.*(claude|copilot|gpt|anthropic|openai|cursor|gemini)", re.I | re.M)
RE_PROMPT = re.compile(r"(^|/)prompts?/[^/]+\.(md|txt|ya?ml|json|j2|jinja2?)$|\.prompt(\.md)?$|\.prompty$", re.I)
# Eval-SUITE shapes only, word-bounded: a dir evals/, eval_x.py, x_eval.py, x.eval.yaml,
# a promptfoo config. Never « evaluation_*.py » (an evaluation engine) or « evaluate ».
RE_EVAL = re.compile(r"(^|/)evals?/|(^|/)(evals?([_.-][^/]*)?|[^/]*[_-]evals?)\.(py|json|ya?ml|jsonl)$|"
                     r"(^|/)[^/]+\.evals?\.[^/]+$|(^|/)promptfooconfig\.(ya?ml|json|js)$", re.I)
RE_DOC_PROMPTS = re.compile(r"(^|/)(readme|index)(\.[^/]*)?$", re.I)
RE_JOURNAL_NOM = re.compile(r"(runs?|journal|executions?|history|log)[^/]*\.jsonl$", re.I)
CLES_STATUT = ("resultat", "result", "statut", "status", "etat", "outcome")
CLES_REPRISES = ("reprises", "retries", "iterations", "attempts")
RE_ECHEC = re.compile(r"^(echec|échec|ko|fail(ed|ure)?|error|erreur|bloque|bloqué|blocked|abandon(ne|né)?)$", re.I)
RE_ATTENTE = re.compile(r"attente|pending|awaiting|en-cours|in-progress", re.I)
RE_MODELE = re.compile(r"^\s*(model|modele|effort|reasoning_effort)\s*:\s*[\"']?([\w.\-]+)", re.I | re.M)
RE_COUT = re.compile(r"(usage|cost|couts?|budget|tokens?)[^/]*\.(jsonl|json|csv)$", re.I)


def _cadre(racine):
    """Frame files (agent instructions + context/memory) at their conventional locations."""
    cle = ("cadre", racine)
    if cle not in _CACHE:
        cand = set(_fichiers_agentic(racine))
        for f in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules", ".windsurfrules", "MEMORY.md"):
            if os.path.isfile(os.path.join(racine, f)):
                cand.add(f)
        for sous in (".github/agents", ".github/instructions", ".cursor/rules", "agents", "docs"):
            p = os.path.join(racine, sous)
            if os.path.isdir(p):
                cand.update(f"{sous}/{x}" for x in os.listdir(p) if os.path.isfile(os.path.join(p, x)))
        if os.path.isfile(os.path.join(racine, ".github/copilot-instructions.md")):
            cand.add(".github/copilot-instructions.md")
        _CACHE[cle] = (sorted(f for f in cand if RE_CADRE.match(f)),
                       sorted(f for f in cand if RE_MEMOIRE.match(f)),
                       sorted(cand))
    return _CACHE[cle]


def _journal(racine, fichiers):
    """(file, records) of the first execution journal carrying a status field."""
    cle = ("journal", racine)
    if cle in _CACHE:
        return _CACHE[cle]
    res = (None, [])
    cand = [f for f in _cadre(racine)[2] + list(fichiers) if RE_JOURNAL_NOM.search(f.rsplit("/", 1)[-1])]
    for f in cand:
        recs = []
        for ligne in _lire(racine, f, MAX_OCTETS_JOURNAL).splitlines():
            try:
                r = json.loads(ligne)
            except ValueError:
                continue
            if isinstance(r, dict) and any(k in r for k in CLES_STATUT):
                recs.append(r)
        if recs:
            res = (f, recs)
            break
    _CACHE[cle] = res
    return res


def _statut(r):
    for k in CLES_STATUT:
        if isinstance(r.get(k), str):
            return r[k].strip()
    return ""


# ------------------------------------------------------------------ cadre_agentic_versionne
def cadre_agentic_versionne(racine, fichiers):
    """Cadre agentic versionné.

    Définition : les instructions données aux agents d'IA (règles du projet, définitions
    d'agents, autorisations, contexte et mémoire transmis) sont des fichiers suivis par
    git, relus et modifiés comme du code.
    Signaux exacts : fichiers suivis parmi CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules,
    .windsurfrules, .github/copilot-instructions.md, .claude/agents|rules|commands/*.md,
    .claude/settings.json, .claude/skills/*/SKILL.md, .cursor/rules/* ; contexte ou
    mémoire : MEMORY.md, .claude/memory|context/*.md, docs/context*|memory*.md.
    Pourquoi : un cadre versionné est partagé par l'équipe et son évolution est
    traçable (Anthropic, « Claude Code best practices » ; agents.md).
    Permet de conclure : le cadre existe et vit dans le dépôt.
    Ne permet pas de conclure : qu'il est pertinent, lu ou respecté par les agents.
    Sans historique git : non mesuré.
    """
    if not _a_git(racine):
        return _res(cadre_agentic_versionne, "non mesure", ["pas d'historique git"])
    suivis = _suivis(racine) or set()
    cadre, memoire, _ = _cadre(racine)
    c = [f for f in cadre if f in suivis]
    m = [f for f in memoire if f in suivis]
    sig = []
    if c:
        sig.append(f"{len(c)} fichier(s) de cadre versionné(s)")
    if m:
        sig.append(f"{len(m)} fichier(s) de contexte/mémoire versionné(s)")
    return _res(cadre_agentic_versionne, "mesure", sig, (c or m or [""])[0])


# ------------------------------------------------------------------ garde_fous_agentic
def garde_fous_agentic(racine, fichiers):
    """Garde-fous et réversibilité du travail des agents.

    Définition : des règles empêchent un agent de faire des actions destructrices, et le
    travail produit reste réversible ; la part écrite par une IA est identifiable.
    Signaux exacts : .claude/settings.json déclarant des interdictions (deny) ou des
    crochets (hooks) ; script de crochet (.claude/hooks, .githooks, .husky) bloquant une
    commande git destructrice (reset --hard, push --force, clean -f, branch -D) ;
    .pre-commit-config.yaml ; règles de protection de branche (.github/rulesets/*,
    .github/settings.yml avec « protection ») ; commits d'annulation (« Revert ») dans
    l'historique ; commits co-signés par une IA (Co-Authored-By).
    Pourquoi : un agent autonome doit être borné et ses erreurs rattrapables
    (OWASP Top 10 for Agentic Applications, « excessive agency » ; Anthropic, « Claude
    Code security »).
    Permet de conclure : des barrières déclarées et des traces de réversibilité.
    Ne permet pas de conclure : que les barrières tiennent ; l'absence de poussée forcée
    n'est pas observable depuis le dépôt (réécriture d'historique invisible localement).
    Ni garde-fou déclaré ni historique git : non mesuré.
    """
    sig, fichier = [], ""
    reglage = _lire(racine, ".claude/settings.json")
    if re.search(r'"(deny|hooks)"', reglage):
        sig.append("interdictions ou crochets déclarés (.claude/settings.json)")
        fichier = ".claude/settings.json"
    _, _, agentic = _cadre(racine)
    crochets = [f for f in agentic if f.startswith(".claude/hooks/")]
    crochets += [f for f in fichiers if f.startswith((".githooks/", ".husky/"))]
    gardes = [f for f in crochets if RE_DESTRUCTIF.search(_lire(racine, f))]
    if gardes:
        sig.append(f"{len(gardes)} garde(s) contre une commande git destructrice")
        fichier = fichier or gardes[0]
    if ".pre-commit-config.yaml" in fichiers:
        sig.append("contrôles avant commit (.pre-commit-config.yaml)")
    if any(f.startswith(".github/rulesets/") for f in fichiers) or \
            "protection" in _lire(racine, ".github/settings.yml"):
        sig.append("protection de branche déclarée")
    if _a_git(racine):
        cs = _commits(racine) or []
        rev = sum(1 for c in cs if c["corps"].startswith("Revert "))
        ia = sum(1 for c in cs if RE_IA_COSIGN.search(c["corps"]))
        if rev:
            sig.append(f"{rev} commit(s) d'annulation (Revert)")
        if ia:
            sig.append(f"{ia} commit(s) co-signés par une IA (part écrite identifiable)")
    elif not sig:
        return _res(garde_fous_agentic, "non mesure", ["pas d'historique git ni garde-fou déclaré"])
    return _res(garde_fous_agentic, "mesure", sig, fichier)


# ------------------------------------------------------------------ solution_agentic_maitrisee
def solution_agentic_maitrisee(racine, fichiers):
    """Solution agentic maîtrisée (critère conditionnel).

    Définition : quand le PRODUIT embarque lui-même un modèle de langage ou des agents,
    les appels sont protégés : délai d'attente, plafond de sortie, sortie validée,
    entrées filtrées.
    Signaux exacts : d'abord une dépendance LLM déclarée (anthropic, openai, langchain,
    llama-index, mistralai, google-genai, ollama, litellm, crewai, autogen…) dans un
    fichier de dépendances ; sinon « non applicable ». Ensuite, dans le code hors tests :
    timeout=, max_tokens, validation (pydantic, jsonschema, response_format, zod),
    filtrage (moderation, guardrail, sanitize, prompt injection) ; présent si au moins
    2 des 4 protections apparaissent.
    Pourquoi : un appel de modèle non borné est une source de coûts, de pannes et
    d'injections (OWASP Top 10 for LLM Applications, LLM01, LLM05, LLM10).
    Permet de conclure : des protections sont présentes dans le code.
    Ne permet pas de conclure : qu'elles couvrent chaque appel ni qu'elles suffisent —
    ce critère relève d'un audit de code ; la détection automatique n'est qu'un repérage.
    """
    dep, fdep = None, ""
    for f in fichiers:
        nom = f.rsplit("/", 1)[-1]
        if nom in CONFIGS_DEP or re.match(r"requirements.*\.txt$", nom):
            m = RE_LLM.search(_lire(racine, f))
            if m:
                dep, fdep = m.group(1), f
                break
    if not dep:
        return _res(solution_agentic_maitrisee, "non applicable", ["aucune dépendance LLM déclarée"])
    trouve = set()
    for f in fichiers:
        if _est_code(f) and not _est_test(f):
            txt = _lire(racine, f)
            trouve.update(nom for nom, rx in PROTECTIONS_LLM if rx.search(txt))
    if len(trouve) < 2:
        return _res(solution_agentic_maitrisee, "mesure", [], fdep)
    return _res(solution_agentic_maitrisee, "mesure",
                [f"dépendance LLM : {dep}",
                 f"protections {len(trouve)}/4 : " + ", ".join(sorted(trouve))], fdep)


# ------------------------------------------------------------------ amelioration_continue
def amelioration_continue(racine, fichiers):
    """Amélioration continue du cadre agentic.

    Définition : le cadre donné aux agents est révisé à un rythme régulier, signe que
    les leçons des erreurs y sont reversées (ce critère mesure le rythme, pas la présence
    du cadre).
    Signaux exacts : nombre de commits des 90 derniers jours touchant un fichier de cadre
    ou de mémoire ; présent à partir de 3 (seuil estimé), absent à 0 ; traces de
    rétrospective (fichier dont le nom contient retro) en signal complémentaire.
    Pourquoi : un cadre figé dérive de la pratique réelle (Anthropic, « Claude Code best
    practices » : faire évoluer CLAUDE.md ; Scrum Guide 2020, « Sprint Retrospective »).
    Permet de conclure : le cadre vit.
    Ne permet pas de conclure : que les révisions l'améliorent.
    Sans git, sans cadre ou avec 1 à 2 révisions : non mesuré.
    """
    if not _a_git(racine):
        return _res(amelioration_continue, "non mesure", ["pas d'historique git"])
    cadre, memoire, _ = _cadre(racine)
    if not cadre and not memoire:
        return _res(amelioration_continue, "non mesure", ["aucun cadre agentic"])
    log = _git(racine, "log", "--since=90.days", "--name-only", "--format=%x1e")
    if log is None:
        return _res(amelioration_continue, "non mesure", ["git log illisible"])
    n = sum(1 for b in log.split("\x1e")
            if any(RE_CADRE.match(x.strip()) or RE_MEMOIRE.match(x.strip()) for x in b.splitlines()))
    retro = [f for f in fichiers if "retro" in f.rsplit("/", 1)[-1].lower()]
    if n >= SEUIL_RYTHME:
        sig = [f"{n} révision(s) du cadre sur 90 j"]
        if retro:
            sig.append(f"{len(retro)} trace(s) de rétrospective")
        return _res(amelioration_continue, "mesure", sig, cadre[0] if cadre else memoire[0])
    if n == 0:
        return _res(amelioration_continue, "mesure", [], cadre[0] if cadre else memoire[0])
    return _res(amelioration_continue, "non mesure", [f"{n} révision(s) sur 90 j (zone intermédiaire)"])


# ------------------------------------------------------------------ gestion_prompts
def gestion_prompts(racine, fichiers):
    """Gestion des prompts comme des artefacts.

    Définition : les prompts utilisés sont des fichiers versionnés, révisés, et si
    possible évalués par un jeu d'essai.
    Signaux exacts : fichiers suivis dans un dossier prompts/ ou nommés *.prompt,
    *.prompt.md, *.prompty (un README ou index de documentation du dossier n'est pas
    un prompt) ; nombre de commits les ayant modifiés ; jeu d'évaluation en forme de
    suite (dossier evals/, fichier eval_*.py, *_eval.py, *.eval.yaml, configuration
    promptfoo) — jamais un moteur « evaluation_*.py ». Sans jeu d'évaluation, ce
    sous-signal est « non mesuré », jamais noté.
    Pourquoi : un prompt non versionné ne se compare pas d'une version à l'autre
    (OpenAI, « Evals » ; Anthropic, « Create strong empirical evaluations »).
    Permet de conclure : des prompts sont gérés comme du code — sur DÉCLARATION :
    le score repose sur des fichiers présents, pas sur une preuve d'usage.
    Ne permet pas de conclure : la qualité des prompts ni leurs résultats.
    Sans historique git : non mesuré.
    """
    if not _a_git(racine):
        return _res(gestion_prompts, "non mesure", ["pas d'historique git"])
    suivis = _suivis(racine) or set()
    prompts = [f for f in fichiers if RE_PROMPT.search(f) and f in suivis
               and not RE_DOC_PROMPTS.search(f)]
    if not prompts:
        return _res(gestion_prompts, "mesure")
    log = _git(racine, "log", "-n", str(MAX_COMMITS), "--format=%x1e", "--", *prompts[:50]) or ""
    n = log.count("\x1e")
    evals = [f for f in fichiers if RE_EVAL.search(f)]
    sig = [f"{len(prompts)} prompt(s) versionné(s)", f"{n} révision(s)",
           f"evals : {len(evals)} fichier(s)" if evals else "evals: non mesuré (aucun jeu d'évaluation)"]
    return _res(gestion_prompts, "mesure", sig, prompts[0])


# ------------------------------------------------------------------ niveau_orchestration
def niveau_orchestration(racine, fichiers):
    """Niveau d'orchestration agentic (échelle à 5 niveaux).

    Définition : jusqu'où le projet organise le travail de ses agents — 1 cadre
    d'instructions, 2 agents spécialisés définis, 3 orchestrateur déclaré, 4 procédures
    (playbooks) décrites, 5 journal d'exécution tenu. Le niveau est le dernier échelon
    atteint sans trou.
    Signaux exacts : 1 fichier de cadre (CLAUDE.md, AGENTS.md…) ; 2 définitions
    .claude/agents/*.md, .github/agents/*.md, agents/*.md ; 3 fichier ou skill dont le
    nom contient « orchestr » ; 4 dossier playbooks/ ou fichier *playbook* ou
    .claude/workflows/ ; 5 journal JSONL d'exécutions portant un statut.
    Pourquoi : la structuration du travail agentic conditionne sa reproductibilité
    (Anthropic, « Building effective agents », 2024).
    Permet de conclure : ce que le projet DÉCLARE. Avertissement : les niveaux 4 et 5
    lisent des déclarations, pas un comportement observé.
    Ne permet pas de conclure : que l'orchestration fonctionne ni qu'elle est utilisée.
    """
    cadre, _, agentic = _cadre(racine)
    tous = agentic + list(fichiers)
    niveaux = [
        ("cadre d'instructions", bool(cadre)),
        ("agents définis", any(RE_AGENT_DEF.match(f) for f in agentic)
         or any(RE_AGENT_DEF.match(f) for f in fichiers)),
        ("orchestrateur déclaré", any("orchestr" in f.lower() for f in tous)),
        ("procédures décrites", any(re.search(r"(^|/)playbooks?/|playbook|\.claude/workflows/", f, re.I)
                                    for f in tous)),
        ("journal d'exécution", _journal(racine, fichiers)[0] is not None),
    ]
    niveau = 0
    for _, ok in niveaux:
        if not ok:
            break
        niveau += 1
    sig = [f"niveau {i + 1} : {nom}" for i, (nom, _) in enumerate(niveaux[:niveau])]
    return _res(niveau_orchestration, "mesure", sig, cadre[0] if cadre else "", niveau=niveau)


# ------------------------------------------------------------------ blocages_traces
def blocages_traces(racine, fichiers):
    """Blocages des agents tracés.

    Définition : les exécutions d'agents sont journalisées avec leur issue, échecs
    compris, ce qui rend les blocages visibles.
    Signaux exacts : fichier JSONL nommé runs/journal/executions/history/log dont les
    lignes portent un champ de statut (resultat, result, status, statut, etat,
    outcome) ; part des exécutions en échec (echec, ko, failed, error, bloqué…),
    affichée à titre informatif.
    Pourquoi : on ne corrige que les blocages qu'on voit (Google SRE Book, chap. 15,
    « Postmortem Culture »).
    Permet de conclure : les blocages sont tracés, et leur fréquence.
    Ne permet pas de conclure : leurs causes. Sans journal d'exécution : non mesuré.
    """
    f, recs = _journal(racine, fichiers)
    if f is None:
        return _res(blocages_traces, "non mesure", ["aucun journal d'exécution"])
    k = sum(1 for r in recs if RE_ECHEC.match(_statut(r)))
    return _res(blocages_traces, "mesure",
                [f"journal {f} : {len(recs)} exécution(s), {k} en échec"], f, ratio=k / len(recs))


# ------------------------------------------------------------------ conformite_resultats
def conformite_resultats(racine, fichiers):
    """Conformité des résultats à la demande.

    Définition : part des demandes livrées du premier coup, sans reprise ; se distingue
    des blocages tracés : ici l'écart entre la demande et le résultat.
    Signaux exacts : dans le journal d'exécution, le nombre de reprises (reprises,
    retries, iterations, attempts) des exécutions closes ; les exécutions encore en
    attente de validation sont exclues — un aller-retour de validation n'est pas une
    reprise. Présent si au moins la moitié sont livrées du premier coup.
    Pourquoi : les reprises mesurent le travail refait (DORA, « change failure rate »,
    par analogie).
    Permet de conclure : ce que le journal DÉCLARE — le score repose sur des
    déclarations, pas sur une preuve.
    Ne permet pas de conclure : la satisfaction réelle du demandeur.
    Sans journal ou sans champ de reprises : non mesuré.
    """
    f, recs = _journal(racine, fichiers)
    if f is None:
        return _res(conformite_resultats, "non mesure", ["aucun journal d'exécution"])
    clos = []
    for r in recs:
        if RE_ATTENTE.search(_statut(r)):
            continue
        cle = next((k for k in CLES_REPRISES if isinstance(r.get(k), int)), None)
        if cle is not None:
            clos.append(r[cle])
    if not clos:
        return _res(conformite_resultats, "non mesure", ["aucun champ de reprises exploitable"], f)
    k = sum(1 for n in clos if n == 0)
    ratio = k / len(clos)
    sig = [f"{k}/{len(clos)} exécutions closes livrées du premier coup"] if ratio >= SEUIL_US else []
    return _res(conformite_resultats, "mesure", sig, f, ratio=ratio)


# ------------------------------------------------------------------ politique_modele_effort
def politique_modele_effort(racine, fichiers):
    """Politique de modèle et d'effort déclarée.

    Définition : le projet choisit le modèle (ou le niveau d'effort) selon la tâche,
    et suit ce que cela coûte.
    Signaux exacts : champ model:/effort: dans l'en-tête des définitions d'agents
    (.claude/agents/*.md, .github/agents/*.md, agents/*.md) et des SKILL.md, ou dans
    .claude/settings.json ; présent si au moins 2 valeurs différentes (politique
    différenciée) ; suivi de coût en signal complémentaire (fichier usage/cost/budget/
    tokens en .jsonl/.json/.csv).
    Pourquoi : un petit modèle suffit à beaucoup de tâches, pour un coût moindre
    (Anthropic, « Choosing a model »).
    Permet de conclure : une politique est DÉCLARÉE — le score repose sur des
    déclarations, pas sur une preuve d'application.
    Ne permet pas de conclure : que le choix est pertinent tâche par tâche.
    """
    _, _, agentic = _cadre(racine)
    defs = [f for f in agentic + list(fichiers)
            if RE_AGENT_DEF.match(f) or f.endswith("/SKILL.md") or f == ".claude/settings.json"]
    valeurs, fichier = set(), ""
    for f in defs:
        txt = _lire(racine, f)
        if f.endswith(".md"):
            m = re.match(r"^---\n(.*?)\n---", txt, re.S)
            txt = m.group(1) if m else ""
        else:
            txt = "\n".join(f"{k}: {v}" for k, v in re.findall(r'"(model|effort)"\s*:\s*"([^"]+)"', txt))
        for _, v in RE_MODELE.findall(txt):
            valeurs.add(v.lower())
            fichier = fichier or f
    if len(valeurs) < 2:
        return _res(politique_modele_effort, "mesure", [], fichier)
    sig = [f"{len(valeurs)} modèles/efforts déclarés : " + ", ".join(sorted(valeurs))]
    cout = [f for f in agentic + list(fichiers) if RE_COUT.search(f.rsplit("/", 1)[-1])]
    if cout:
        sig.append(f"suivi de coût : {cout[0]}")
    return _res(politique_modele_effort, "mesure", sig, fichier)


# ------------------------------------------------------------------ validation_humaine_tracee
def validation_humaine_tracee(racine, fichiers):
    """Validation humaine tracée après livraison.

    Définition : la recette d'un livrable est prononcée par une autre personne que son
    auteur, et la trace en est conservée.
    Signaux exacts : dans les 500 derniers commits, mention « Approved-by: »,
    « Accepted-by: », « Validated-by: » ou « Tested-by: » d'une adresse différente de
    l'auteur ; dans le journal d'exécution, une exécution passée d'un état « en attente
    de validation » à un état clos (champ « avant » conservé).
    Pourquoi : l'acceptation par le demandeur ferme la boucle de livraison (Scrum Guide
    2020, « Definition of Done » ; ISO/IEC 25010, adéquation fonctionnelle).
    Permet de conclure : des recettes par un tiers sont tracées.
    Ne permet pas de conclure : ce qui a été vérifié lors de la recette. Une forge qui
    garde les approbations hors de git ne laisse rien ici : sans trace, non mesuré.
    """
    sig = []
    if _a_git(racine):
        cs = _commits(racine) or []
        k = sum(1 for c in cs if any(e != c["ae"] for e in _trailers(
            c["corps"], ["Approved-by", "Accepted-by", "Validated-by", "Tested-by"])))
        if k:
            sig.append(f"{k} commit(s) recettés par un tiers")
    f, recs = _journal(racine, fichiers)
    lev = 0
    for r in recs:
        if RE_ATTENTE.search(_statut(r)):
            continue
        for v in r.values():
            if isinstance(v, dict) and RE_ATTENTE.search(str(v.get("avant", v.get("previous", "")))):
                lev += 1
                break
    if lev:
        sig.append(f"{lev} validation(s) levée(s) dans le journal")
    if not sig:
        return _res(validation_humaine_tracee, "non mesure", ["aucune trace de recette par un tiers"])
    return _res(validation_humaine_tracee, "mesure", sig, f or "")


# ------------------------------------------------------------------ structure_mandats_agents
# The 6 blocks of the reference template (docs/reflexions/gabarit-agent.md) that
# can be read cold, without judgement. Blocks 1 (role quality), 3 (reasons) and
# 5 (general guidance, no hand-written step plan) need a reader: not scored.
RE_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
RE_OUTILS = re.compile(r"^tools\s*:\s*(.+)$", re.M)
RE_TITRE = re.compile(r"^#{1,4}\s+(.+)$", re.M)
RE_BLOC_FIN = re.compile(r"condition d.arr[êe]t|\barr[êe]t\b|stop condition|when to stop|"
                         r"\bdone\b|fin de (la )?t[âa]che|\bbudget\b", re.I)
# « Ton, ... », « Ton et format », « Tone: » -- never the French possessive « Ton texte ».
RE_BLOC_TON = re.compile(r"^(#{1,4}\s+|\*\*)?(ton|tone)\s*(,|:|\*\*|et\b|and\b|$)",
                         re.I | re.M)
RE_BLOC_INTERDITS = re.compile(r"ne fais jamais|ne fait jamais|interdit|\bnever\b|"
                               r"\bdo not\b|\bdon.t\b|forbidden", re.I)
RE_BLOC_EXEMPLE = re.compile(r"exemple|example|premi[èe]re action|first action|brief type",
                             re.I)
RE_ECRITURE = re.compile(r"^\**\s*[ÉE]criture\s*:|^\**\s*Writes?\s*:", re.M)
RE_CONTRAT = re.compile(r"contrat de sortie|format de sortie|output contract|"
                        r"output format|\bsortie\b|\boutput\b|rappel", re.I)
RE_PROVENANCE = re.compile(r"provenance|non authentifi|untrusted|unauthenticated", re.I)
BLOCS_STRUCTURE = ("objectif et fin", "outils", "ton et format", "interdits",
                   "exemple de départ", "rappel final")
SEUILS_STRUCTURE = (0.25, 0.45, 0.65, 0.95)  # estimated thresholds, not published ones


def blocs_mandat(texte):
    """{block: bool} for the 6 cold-detectable blocks of one agent mandate."""
    m = RE_FRONT.match(texte)
    front = m.group(1) if m else ""
    corps = texte[m.end():] if m else texte
    titres = RE_TITRE.findall(corps)
    lo = RE_OUTILS.search(front)
    outils = [o.strip() for o in lo.group(1).split(",") if o.strip()] if lo else []
    decrits = bool(outils) and all(re.search(rf"(?<![\w-]){re.escape(o)}(?![\w-])", corps)
                                   for o in outils)
    if {"Edit", "Write"} & set(outils) and not RE_ECRITURE.search(corps):
        decrits = False
    tiers = len(corps) * 2 // 3
    pos_contrat = [mm.start() for mm in RE_TITRE.finditer(corps)
                   if RE_CONTRAT.search(mm.group(1))]
    pos_prov = [mm.start() for mm in RE_PROVENANCE.finditer(corps)]
    return {
        "objectif et fin": any(RE_BLOC_FIN.search(t) for t in titres)
        and "maxTurns" not in front,
        "outils": decrits,
        "ton et format": bool(RE_BLOC_TON.search(corps)),
        "interdits": any(RE_BLOC_INTERDITS.search(t) for t in titres),
        "exemple de départ": any(RE_BLOC_EXEMPLE.search(t) for t in titres),
        "rappel final": bool(pos_contrat) and max(pos_contrat) >= tiers
        and bool(pos_prov) and max(pos_prov) >= tiers,
    }


def structure_mandats_agents(racine, fichiers):
    """Structure des mandats d'agents (critère conditionnel, gradué).

    Définition : chaque définition d'agent suit le gabarit de référence à 9 blocs
    (docs/reflexions/gabarit-agent.md) ; les 6 blocs lisibles sans jugement sont
    présents : objectif et fin, outils décrits, ton et format, interdits, exemple de
    départ, rappel final.
    Signaux exacts : fichiers .claude/agents/*.md, .github/agents/*.md ou agents/*.md
    (hors README) ; sinon « non applicable ». Par mandat : titre de section « Condition
    d'arrêt », « budget » ou « stop » et pas de maxTurns dans l'en-tête ; chaque outil
    de la ligne tools: de l'en-tête cité dans le corps, plus une ligne « Écriture : »
    si Edit ou Write est accordé ; ligne ou titre commençant par « Ton » ; titre de
    section « Ce que tu ne fais jamais », « Interdits » ou « Never » ; titre « Exemple »
    ou « Première action » ; titre de contrat de sortie ET mention de provenance dans le
    dernier tiers du texte. La part moyenne de blocs présents fait la note.
    Pourquoi : un mandat structuré borne la tâche et place le vital en tête et en fin
    (Anthropic, « Prompting best practices » ; Anthropic, « Writing tools for agents » ;
    Liu et al., « Lost in the Middle », TACL 2024 ; Zheng et al., EMNLP Findings 2024).
    Permet de conclure : le texte des mandats porte les blocs de la structure de
    référence.
    Ne permet pas de conclure : que l'agent se comporte mieux — c'est une mesure de
    présence du texte, pas du comportement ; la qualité du rôle, les motifs et
    l'absence de plan pas à pas exigent une lecture et ne sont pas notés.
    """
    cand = set(fichiers) | set(_cadre(racine)[2])
    mandats = sorted(f for f in cand if RE_AGENT_DEF.match(f)
                     and not f.rsplit("/", 1)[-1].lower().startswith("readme"))
    if not mandats:
        return _res(structure_mandats_agents, "non applicable", ["aucune définition d'agent"])
    compte = dict.fromkeys(BLOCS_STRUCTURE, 0)
    for f in mandats:
        for bloc, ok in blocs_mandat(_lire(racine, f)).items():
            compte[bloc] += bool(ok)
    ratio = sum(compte.values()) / (len(BLOCS_STRUCTURE) * len(mandats))
    sig = [f"{bloc} : {n}/{len(mandats)} mandat(s)" for bloc, n in compte.items()]
    return _res(structure_mandats_agents, "mesure", sig, mandats[0], ratio=ratio)


# SINGLE source of the reading order: visible groups, then criteria inside each
# group. Public codes (tests_automatises..tracabilite_demande_livrable, cadre_agentic_versionne..validation_humaine_tracee) are numbered in THIS order by the page;
# every tab reads it, none re-sorts (reading order arbitrated 2026-09-29).
GROUPES = {
    "A": (
        ("Besoin et conception", ("epics_us_bien_formees", "criteres_acceptance",
                                  "decisions_conception_tracees")),
        ("Développement", ("tracabilite_demande_livrable", "code_documente",
                           "linter_configure", "revue_avant_integration",
                           "securite_base")),
        ("Tests et livraison", ("tests_automatises", "co_evolution_tests",
                                "couverture_configuree", "test_artefact_reel",
                                "integration_continue")),
    ),
    "B": (
        ("Cadrer", ("cadre_agentic_versionne", "gestion_prompts",
                    "politique_modele_effort")),
        ("Borner", ("garde_fous_agentic", "niveau_orchestration")),
        ("Exécuter", ("solution_agentic_maitrisee", "blocages_traces")),
        ("Livrer et valider", ("conformite_resultats", "validation_humaine_tracee")),
        ("Améliorer", ("amelioration_continue",)),
        ("Structurer les agents", ("structure_mandats_agents",)),
    ),
}
REFERENTIELS = {lettre: [nom for _, noms in groupes for nom in noms]
                for lettre, groupes in GROUPES.items()}
GROUPE_DE = {nom: titre for groupes in GROUPES.values()
             for titre, noms in groupes for nom in noms}
FONCTIONS = {nom: globals()[nom] for ref in REFERENTIELS.values() for nom in ref}


def detecter(chemin):
    """Run every detection on a repository path (read-only)."""
    racine = os.path.abspath(chemin)
    if not os.path.isdir(racine):
        return {k: _res(f, "non mesure", ["chemin introuvable"]) for k, f in FONCTIONS.items()}
    _CACHE.clear()
    fichiers = _fichiers(racine)
    out = {}
    for k, f in FONCTIONS.items():
        try:
            out[k] = f(racine, fichiers)
        except Exception as exc:  # any doubt degrades to "non mesure", never a guess
            out[k] = _res(f, "non mesure", [f"erreur {type(exc).__name__}"])
    _CACHE.clear()
    return out


if __name__ == "__main__":
    print(json.dumps(detecter(sys.argv[1] if len(sys.argv) > 1 else "."), ensure_ascii=False, indent=1))
