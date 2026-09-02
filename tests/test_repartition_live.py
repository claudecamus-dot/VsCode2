"""Aperçu « Répartition » des deux écrans d'enregistrement — exécution RÉELLE du JS.

Ce fichier comble le trou de couverture central de l'onglet qui se remplit PENDANT
la transcription : jusqu'ici, `renderRepartition`, `pollRepartition` et le vidage
de l'onglet n'étaient couverts par AUCUNE exécution. Les seuls signaux existants
(`tests/test_record_segment_jobs.py`) cherchent des sous-chaînes dans le HTML rendu
par Jinja — ils restent verts si la fonction affiche n'importe quoi, ou n'est jamais
appelée. Le chemin SERVEUR, lui, est déjà prouvé par exécution dans
`tests/test_interview_segment_jobs.py` : ce qui manquait est la moitié CLIENT.

**Les gestionnaires sont EXÉCUTÉS, pas grep-és.** Une première version de ce fichier
cherchait l'appel de vidage par expression régulière dans le corps du gestionnaire.
La revue adversariale du 2026-09-02 l'a mise en défaut en quatre coups : mettre
l'appel sous `if (false)`, le commenter en `/* */`, le différer d'un `setTimeout`,
ou placer un `return` avant — chacune de ces neutralisations laissait la suite
verte. Le gestionnaire est donc désormais extrait du template puis EXÉCUTÉ sous
node, dans un bac à sable où toute variable d'écran inconnue est une doublure
tolérante : ce qui est vérifié est l'ÉTAT DE L'ONGLET après le clic, pas la
présence d'une ligne de code.

Les quatre défauts trouvés en revue et corrigés le 2026-09-02 sont verrouillés ici :

- **F9/EC6** — « Recommencer » ne vidait pas l'onglet : les tours de l'entretien
  abandonné restaient affichés, et restaient exportables en PDF.
- **La réponse en vol** — une réponse partie AVANT le clic arrivait après et
  réaffichait la session jetée, contournant le vidage par la porte de derrière.
- **Le chemin frère** — « Démarrer » jette la session tout autant que
  « Recommencer » (après un arrêt, les deux boutons sont visibles) et ne vidait
  rien non plus.
- **Le libellé de progression** de l'écran structuré, qui n'était réécrit que
  lorsque le serveur renvoyait un total non nul.

Aucun accès base : ce module ne touche ni `DB_PATH` ni `SessionLocal`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
STRUCTURE = RACINE / "app" / "templates" / "interviews" / "record.html"
LES_DEUX = [LIBRE, STRUCTURE]


# --------------------------------------------------------------------------- #
# Extraction du JS réellement écrit dans l'écran
# --------------------------------------------------------------------------- #
def _sans_commentaires(contenu: str) -> str:
    """Retire les commentaires `//` de fin de ligne, en épargnant les `://` des
    URL — appliqué avant tout comptage d'accolades, sans quoi une accolade en
    commentaire fausse les bornes du bloc extrait."""
    sorties = []
    for ligne in contenu.splitlines():
        pos, i = None, ligne.find("//")
        while i >= 0:
            if i == 0 or ligne[i - 1] != ":":
                pos = i
                break
            i = ligne.find("//", i + 1)
        sorties.append(ligne if pos is None else ligne[:pos])
    return "\n".join(sorties)


def _bloc(contenu: str, depart: int) -> str:
    """Bloc `{...}` dont l'accolade ouvrante suit `depart`, par comptage."""
    debut = contenu.index("{", depart)
    profondeur, i = 0, debut
    while i < len(contenu):
        if contenu[i] == "{":
            profondeur += 1
        elif contenu[i] == "}":
            profondeur -= 1
            if profondeur == 0:
                break
        i += 1
    assert profondeur == 0, "bloc non refermé — l'extraction a dérivé"
    return contenu[debut : i + 1]


def _fonction(contenu: str, nom: str) -> str:
    """Déclaration `function <nom>(...) {...}` telle qu'écrite dans l'écran.

    La recherche exige que le nom soit suivi d'une parenthèse ou d'une espace :
    sans quoi `renderRepartition` matcherait un hypothétique
    `renderRepartitionStatus` déclaré plus haut, et le test exercerait la
    mauvaise fonction en restant vert (constat de revue du 2026-09-02)."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(nom), contenu)
    assert m, "fonction `%s` introuvable — l'écran a changé de forme" % nom
    depart = m.start()
    entete = contenu[depart : contenu.index("{", depart)]
    return entete + _bloc(contenu, depart)


def _corps_du_gestionnaire(contenu: str, ancre: str) -> str:
    """Le gestionnaire de clic attaché par `ancre`, prêt à être EXÉCUTÉ.

    Le corps est enveloppé dans une fonction immédiatement appelée plutôt que
    joué comme un bloc nu : ces gestionnaires portent des `return` de premier
    niveau (« si un enregistrement tourne déjà, ne rien faire »), et un `return`
    hors fonction est une erreur de syntaxe. L'enveloppe reproduit exactement ce
    qu'est le gestionnaire dans l'écran — le corps d'une fonction."""
    depart = contenu.index(ancre)
    return "(function () %s)();" % _bloc(contenu, depart)


def _corps_du_demarrage(contenu: str) -> str:
    """La remise à zéro jouée au clic sur « Démarrer l'enregistrement ».

    Elle ne se trouve PAS dans le corps synchrone du gestionnaire : celui-ci ne
    fait que des gardes puis appelle l'acquisition de la source audio, et toute
    la remise à zéro vit dans la suite (`.then`) de cette promesse. Exécuter le
    corps synchrone ne prouverait donc rien — il ne touche jamais à l'onglet.
    C'est ce callback-là qui jette la session précédente, c'est lui qu'on
    exécute."""
    depart = contenu.index("startBtn.addEventListener")
    suite = contenu.index("then(function (", depart)
    return "(function (s) %s)({});" % _bloc(contenu, suite)


def _source(ecran: Path) -> str:
    return _sans_commentaires(ecran.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Bac à sable node : DOM en doublure + toute variable d'écran inconnue tolérée
# --------------------------------------------------------------------------- #
_SANDBOX = r"""
const vm = require('vm');

function Element(id) {
  this.id = id; this.innerHTML = ''; this.textContent = '';
  this.hidden = false; this.disabled = false; this.value = '';
  this._classes = new Set();
  const self = this;
  this.classList = {
    add: function (c) { self._classes.add(c); },
    remove: function (c) { self._classes.delete(c); },
    contains: function (c) { return self._classes.has(c); },
    toggle: function (c, on) { if (on) self._classes.add(c); else self._classes.delete(c); }
  };
}

const elements = {};
function el(id) {
  if (!elements[id]) elements[id] = new Element(id);
  return elements[id];
}
%(elements)s

const blocsQA = %(blocsQA)s;

const document = {
  getElementById: function (id) { return elements[id] || null; },
  querySelectorAll: function (sel) {
    return /\.rec-rep-qa\s*$/.test(sel) ? blocsQA : [];
  },
  querySelector: function () { return null; },
  createElement: function () { return new Element('cree'); },
  body: { appendChild: function () {}, removeChild: function () {} }
};

// Toute variable d'écran non fournie ici est une doublure tolérante : elle se
// laisse lire, appeler, assigner et enchaîner sans jamais lever. Le corps d'un
// gestionnaire touche des dizaines de variables sans rapport avec l'onglet ;
// les stuber une par une rendrait le test faux dès le prochain ajout.
const doublure = new Proxy(function () {}, {
  get: function (t, p) {
    if (p === Symbol.toPrimitive || p === 'toString' || p === 'valueOf') {
      return function () { return ''; };
    }
    if (p === 'length') return 0;
    return doublure;
  },
  set: function () { return true; },
  apply: function () { return doublure; },
  construct: function () { return doublure; },
  has: function () { return true; }
});

const reel = { document: document, console: console, JSON: JSON, Array: Array,
               String: String, Object: Object, Math: Math, Promise: Promise,
               setTimeout: setTimeout, encodeURIComponent: encodeURIComponent };
const bac = new Proxy(reel, {
  has: function () { return true; },
  get: function (t, p) { return (p in t) ? t[p] : doublure; },
  set: function (t, p, v) { t[p] = v; return true; }
});
vm.createContext(bac);
function jouer(code) { vm.runInContext(code, bac); }

%(scenario)s
"""


def _node(script: str) -> dict:
    if shutil.which("node") is None:
        # ÉCHEC, pas skip : ces tests sont la SEULE preuve par exécution de
        # l'aperçu. Un skip rendrait la suite verte sans rien avoir vérifié du
        # comportement que l'utilisateur voit à l'écran.
        pytest.fail(
            "node est requis pour exécuter réellement le JS de l'aperçu "
            "Répartition (https://nodejs.org). Un skip rendrait la suite verte "
            "sans la moindre preuve d'exécution."
        )
    res = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert res.returncode == 0, res.stderr[:3000]
    return json.loads(res.stdout.strip())


def _bac(scenario: str, elements: str = "", blocs_qa: str = "[]") -> dict:
    return _node(_SANDBOX % {"scenario": scenario, "elements": elements,
                             "blocsQA": blocs_qa})


_ELEMENTS_LIBRE = (
    "el('rec-repartition'); el('rec-repartition-status'); el('rep-pdf');"
)
_ELEMENTS_STRUCTURE = (
    "el('rec-repartition'); el('rec-repartition-status'); el('rec-rep-progress');"
)

# Les blocs question/réponse de l'écran structuré, tels que le template les rend.
_BLOCS_QA = (
    "[{getAttribute: function () { return '12'; },"
    "  querySelector: function () { return el('rep-12'); }},"
    " {getAttribute: function () { return '13'; },"
    "  querySelector: function () { return el('rep-13'); }}]"
)


def _prelude_libre() -> str:
    """Les fonctions d'aperçu de l'écran libre, chargées dans le bac à sable."""
    src = _source(LIBRE)
    return (
        "jouer(%s);" % json.dumps(
            "var latestRepartitionTurns = [];\n"
            + _fonction(src, "escHtml") + "\n"
            + _fonction(src, "renderRepartition") + "\n"
        )
    )


def _appel_rendu(turns: list) -> str:
    """Un appel `renderRepartition(...)` joué dans le bac à sable. Le code est
    sérialisé d'un bloc plutôt que composé à la main : la première version
    cassait dès qu'un tour contenait une apostrophe — or les verbatims français
    en sont pleins."""
    return "jouer(%s);" % json.dumps("renderRepartition(%s);" % json.dumps(turns))


def _etat_libre() -> str:
    return (
        "console.log(JSON.stringify({"
        "  html: el('rec-repartition').innerHTML,"
        "  statusHidden: el('rec-repartition-status').hidden,"
        "  pdfDisabled: el('rep-pdf').disabled,"
        "  memorises: bac.latestRepartitionTurns ? bac.latestRepartitionTurns.length : -1"
        "}));"
    )


# --------------------------------------------------------------------------- #
# 1. Le rendu de l'onglet — écran libre
# --------------------------------------------------------------------------- #
def test_l_apercu_affiche_les_tours_dans_l_ordre_avec_leurs_sections() -> None:
    """Le rendu réel de l'onglet : ordre chronologique conservé, titre de section
    sorti en en-tête, question et remarque jointes derrière l'interlocuteur.
    C'est ce que voit l'utilisateur pendant qu'il parle."""
    turns = [
        {"interlocuteur": "Consultant", "question": "Quel est votre rôle ?",
         "remarque": None, "section_title": "Présentation"},
        {"interlocuteur": "Marie", "question": None,
         "remarque": "Je pilote le service client.", "section_title": None},
        {"interlocuteur": "Marie", "question": None,
         "remarque": "Nous manquons d'outils.", "section_title": "Difficultés"},
    ]
    etat = _bac(
        _prelude_libre()
        + _appel_rendu(turns)
        + _etat_libre(),
        _ELEMENTS_LIBRE,
    )
    html = etat["html"]

    assert etat["statusHidden"] is True
    assert etat["pdfDisabled"] is False
    assert etat["memorises"] == 3

    assert html.index("Quel est votre rôle") < html.index("Je pilote le service client")
    assert html.index("Je pilote le service client") < html.index("Nous manquons d'outils")
    assert html.count('class="rec-rep-section"') == 2
    assert html.index("Difficultés") < html.index("Nous manquons d'outils")
    assert 'class="rec-rep-q"' in html and 'class="rec-rep-r"' in html


@pytest.mark.parametrize("champ", ["interlocuteur", "remarque", "section_title"])
def test_l_apercu_echappe_le_html_de_chaque_champ_affiche(champ: str) -> None:
    """La transcription vient de Whisper puis d'un modèle local : elle peut
    contenir n'importe quels caractères. Chaque champ rendu est testé
    séparément — n'en couvrir que deux laissait retirer l'échappement du
    troisième sans rougir (constat de revue du 2026-09-02)."""
    tour = {"interlocuteur": "Marie", "question": None,
            "remarque": "propos", "section_title": None}
    tour[champ] = '<script>x</script> & "z"'
    etat = _bac(
        _prelude_libre()
        + _appel_rendu([tour])
        + _etat_libre(),
        _ELEMENTS_LIBRE,
    )
    html = etat["html"]

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html and "&quot;" in html


def test_l_apercu_vide_remet_le_bandeau_et_ferme_l_export() -> None:
    """Zéro tour rend l'onglet à son état initial, export PDF refermé compris."""
    etat = _bac(
        _prelude_libre() + _appel_rendu([]) + _etat_libre(),
        _ELEMENTS_LIBRE,
    )
    assert etat["html"] == ""
    assert etat["statusHidden"] is False
    assert etat["pdfDisabled"] is True
    assert etat["memorises"] == 0


def test_vider_apres_avoir_affiche_efface_aussi_la_memoire_de_l_export() -> None:
    """La séquence RÉELLE, dans un même contexte : l'onglet se remplit pendant
    l'entretien, puis on le vide. Les tests qui partent d'un contexte neuf ne
    prouvent rien ici — le défaut visé est justement que le conteneur se vide
    pendant que `latestRepartitionTurns` garde les propos de la session jetée,
    laissant « Télécharger la répartition (PDF) » actif sur un onglet en
    apparence vide (constat de revue du 2026-09-02)."""
    turns = [{"interlocuteur": "Marie", "question": None,
              "remarque": "Propos de la session jetee", "section_title": None}]
    etat = _bac(
        _prelude_libre()
        + _appel_rendu(turns)
        + _appel_rendu([])
        + _etat_libre(),
        _ELEMENTS_LIBRE,
    )
    assert etat["html"] == ""
    assert etat["memorises"] == 0, (
        "l'onglet paraît vide mais l'export PDF détient encore les propos de la "
        "session jetée"
    )
    assert etat["pdfDisabled"] is True


# --------------------------------------------------------------------------- #
# 2. Les deux gestionnaires qui jettent une session — EXÉCUTÉS
# --------------------------------------------------------------------------- #
def _corps_qui_jette(contenu: str, quoi: str) -> str:
    """Le code réellement joué quand l'utilisateur jette une session, selon le
    bouton : « Recommencer » agit dans son gestionnaire, « Démarrer » dans la
    suite de l'acquisition audio."""
    if quoi == "Recommencer":
        return _corps_du_gestionnaire(contenu, "resetBtn.addEventListener")
    return _corps_du_demarrage(contenu)


@pytest.mark.parametrize("quoi", ["Recommencer", "Démarrer"])
def test_jeter_la_session_vide_l_apercu_ecran_libre(quoi: str) -> None:
    """Les DEUX boutons qui jettent une session doivent vider l'onglet.

    Après un arrêt d'enregistrement, « Démarrer » et « Recommencer » sont tous
    deux visibles : relancer un entretien abandonne le précédent exactement
    comme « Recommencer ». Sans vidage, les tours de l'interviewé précédent
    restent affichés et exportables en PDF sous la nouvelle identité.

    Le gestionnaire est EXÉCUTÉ : mettre l'appel sous `if (false)`, le
    commenter, le différer ou le faire précéder d'un `return` fait rougir ce
    test, ce qu'une recherche de motif dans la source ne faisait pas."""
    src = _source(LIBRE)
    turns = [{"interlocuteur": "Marie", "question": None,
              "remarque": "Propos de la session jetee", "section_title": None}]
    corps = _corps_qui_jette(src, quoi)
    etat = _bac(
        _prelude_libre()
        + "jouer(%s);" % json.dumps("renderRepartition(%s);" % json.dumps(turns))
        + "jouer(%s);" % json.dumps(corps)
        + _etat_libre(),
        _ELEMENTS_LIBRE,
    )

    assert "Propos de la session jetee" not in etat["html"], (
        "« %s » n'a pas vidé l'onglet Répartition : les tours de la session "
        "jetée restent affichés" % quoi
    )
    assert etat["html"] == ""
    assert etat["memorises"] == 0, (
        "« %s » a vidé l'affichage mais pas la mémoire de l'export : le bouton "
        "PDF exporterait encore l'entretien abandonné" % quoi
    )
    assert etat["pdfDisabled"] is True


def test_jeter_la_session_vide_l_apercu_ecran_structure() -> None:
    """Même exigence sur le chemin frère, celui de l'entretien structuré : ses
    réponses par question et sa ligne de progression doivent disparaître.

    Cet écran a longtemps été le seul couvert par une recherche de motif, donc
    le seul où trois neutralisations de la garde passaient inaperçues — il est
    désormais exécuté lui aussi."""
    src = _source(STRUCTURE)
    reponses = {"12": {"text": "Reponse de la session jetee", "verbatims": []}}
    etats = {}
    for quoi in ("Recommencer", "Démarrer"):
        corps = _corps_qui_jette(src, quoi)
        etats[quoi] = _bac(
            "jouer(%s);" % json.dumps(_fonction(src, "renderRepartition"))
            + "jouer(%s);" % json.dumps(
                "renderRepartition(%s, 3, 5);" % json.dumps(reponses))
            + "jouer(%s);" % json.dumps(corps)
            + "console.log(JSON.stringify({"
              "  bloc: el('rep-12').textContent,"
              "  progressTexte: el('rec-rep-progress').textContent,"
              "  progressHidden: el('rec-rep-progress').hidden,"
              "  statusHidden: el('rec-repartition-status').hidden}));",
            _ELEMENTS_STRUCTURE, _BLOCS_QA,
        )
    for quoi, etat in etats.items():
        assert "Reponse de la session jetee" not in etat["bloc"], (
            "« %s » laisse la réponse de la session jetée dans son bloc" % quoi
        )
        assert etat["bloc"] == "—"
        assert etat["progressTexte"] == "", (
            "« %s » laisse le libellé de progression de la session jetée dans "
            "le nœud : il n'était réécrit que lorsque le serveur renvoyait un "
            "total non nul" % quoi
        )
        assert etat["progressHidden"] is True
        assert etat["statusHidden"] is False


def test_le_libelle_de_progression_est_efface_meme_sans_total() -> None:
    """Le libellé ne doit jamais survivre à un rendu, quel que soit ce que le
    serveur renvoie. La version d'avant ne le réécrivait que sous `total > 0` :
    un total absent laissait à l'écran le décompte de la session précédente."""
    src = _source(STRUCTURE)
    reponses = {"12": {"text": "Reponse precedente", "verbatims": []}}
    etat = _bac(
        "jouer(%s);" % json.dumps(_fonction(src, "renderRepartition"))
        + "jouer(%s);" % json.dumps("renderRepartition(%s, 3, 5);" % json.dumps(reponses))
        + "jouer('renderRepartition({}, undefined, undefined);');"
        + "console.log(JSON.stringify({"
          "  progressTexte: el('rec-rep-progress').textContent,"
          "  progressHidden: el('rec-rep-progress').hidden}));",
        _ELEMENTS_STRUCTURE, _BLOCS_QA,
    )
    assert etat["progressTexte"] == ""
    assert etat["progressHidden"] is True


# --------------------------------------------------------------------------- #
# 3. La course : une réponse partie avant que la session soit jetée
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "jeton_apres, situation",
    [("''", "Recommencer (aucune session en cours)"),
     ("'session-B'", "Démarrer (une nouvelle session a déjà commencé)")],
)
def test_une_reponse_en_vol_ne_repeuple_pas_un_apercu_remis_a_zero(
    jeton_apres: str, situation: str
) -> None:
    """Le vidage ne suffit pas seul : le poll interroge le serveur toutes les
    cinq secondes, et une réponse partie AVANT le clic arrive après.

    Les DEUX suites sont éprouvées, car elles diffèrent : après
    « Recommencer » le jeton est vide, après « Démarrer » il vaut celui d'une
    NOUVELLE session. Une garde qui accepterait tout jeton non vide passerait le
    premier cas et laisserait le second cassé (constat de revue du 2026-09-02)."""
    src = _source(LIBRE)
    turns = [{"interlocuteur": "Marie", "question": None,
              "remarque": "Propos de la session jetee", "section_title": None}]
    scenario = (
        "jouer(%s);" % json.dumps(
            "var latestRepartitionTurns = [];\n"
            "var NET_TIMEOUT_MS = 1000;\n"
            "var sessionToken = 'session-A';\n"
            "var reglerReponse;\n"
            "function recFetch() {"
            "  return new Promise(function (res) { reglerReponse = res; });\n"
            "}\n"
            + _fonction(src, "escHtml") + "\n"
            + _fonction(src, "renderRepartition") + "\n"
            + _fonction(src, "pollRepartition") + "\n"
            "pollRepartition();\n"
            "sessionToken = " + jeton_apres + ";\n"
            "renderRepartition([]);\n"
            "reglerReponse({ ok: true, json: function () {"
            "  return Promise.resolve({ turns: " + json.dumps(turns) + " });"
            "} });\n"
        )
        + "setTimeout(function () { " + _etat_libre() + " }, 50);"
    )
    etat = _bac(scenario, _ELEMENTS_LIBRE)

    assert "Propos de la session jetee" not in etat["html"], (
        "une réponse partie avant « %s » a réaffiché les tours de la session "
        "jetée — le vidage est contourné par la course" % situation
    )
    assert etat["html"] == ""
    assert etat["memorises"] == 0
    assert etat["pdfDisabled"] is True


def test_une_reponse_de_la_session_courante_s_affiche_bien() -> None:
    """Contrepartie indispensable : la garde ne doit pas éteindre l'aperçu. Sans
    ce test, « rien ne s'affiche jamais » passerait aussi le test de la course."""
    src = _source(LIBRE)
    turns = [{"interlocuteur": "Marie", "question": None,
              "remarque": "Propos de la session courante", "section_title": None}]
    scenario = (
        "jouer(%s);" % json.dumps(
            "var latestRepartitionTurns = [];\n"
            "var NET_TIMEOUT_MS = 1000;\n"
            "var sessionToken = 'session-A';\n"
            "var reglerReponse;\n"
            "function recFetch() {"
            "  return new Promise(function (res) { reglerReponse = res; });\n"
            "}\n"
            + _fonction(src, "escHtml") + "\n"
            + _fonction(src, "renderRepartition") + "\n"
            + _fonction(src, "pollRepartition") + "\n"
            "pollRepartition();\n"
            "reglerReponse({ ok: true, json: function () {"
            "  return Promise.resolve({ turns: " + json.dumps(turns) + " });"
            "} });\n"
        )
        + "setTimeout(function () { " + _etat_libre() + " }, 50);"
    )
    etat = _bac(scenario, _ELEMENTS_LIBRE)

    assert "Propos de la session courante" in etat["html"]
    assert etat["memorises"] == 1
    assert etat["pdfDisabled"] is False


# --------------------------------------------------------------------------- #
# 4. Câblage : les deux écrans, mêmes garanties
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ecran", LES_DEUX, ids=lambda p: p.name)
def test_les_deux_ecrans_gardent_le_jeton_au_retour_du_poll(ecran: Path) -> None:
    """Le mode d'échec récurrent de ces deux fichiers est « un correctif posé
    sur un chemin, oublié sur son frère ». Trois moitiés à tenir, car en
    vérifier une seule ne prouve rien : le jeton doit être FIGÉ avant le départ,
    la requête doit partir avec CE jeton figé, et le rendu doit le re-vérifier.

    Le troisième point ferme la neutralisation par masquage : redéclarer un
    `sessionToken` local dans la fonction rendait la comparaison toujours vraie
    tout en laissant les deux premières vérifications satisfaites."""
    contenu = _source(ecran)
    corps = _bloc(contenu, contenu.index("function pollRepartition"))

    assert re.search(r"var\s+tokenAuDepart\s*=\s*sessionToken\s*;", corps), (
        f"{ecran.name} : `pollRepartition` ne fige plus le jeton au départ de "
        "la requête — il ne peut plus détecter qu'il a changé pendant le vol"
    )
    assert "encodeURIComponent(tokenAuDepart)" in corps, (
        f"{ecran.name} : la requête part avec `sessionToken` plutôt qu'avec le "
        "jeton figé — la comparaison au retour ne veut alors plus rien dire"
    )
    assert corps.count("var sessionToken") == 0, (
        f"{ecran.name} : un `sessionToken` local masque celui de l'écran — la "
        "garde se compare à elle-même et laisse passer la réponse périmée"
    )
    rendu = corps[corps.index(".then", corps.index(".then") + 1):]
    assert re.search(r"sessionToken\s*===\s*tokenAuDepart", rendu), (
        f"{ecran.name} : la réponse est rendue sans re-vérifier le jeton — une "
        "réponse partie avant « Recommencer » réaffiche la session jetée"
    )


@pytest.mark.parametrize("ecran", LES_DEUX, ids=lambda p: p.name)
def test_l_apercu_est_rafraichi_toutes_les_cinq_secondes(ecran: Path) -> None:
    """L'aperçu ne vaut que s'il est réellement rafraîchi, et à la bonne
    cadence : la valeur est verrouillée, pas seulement la présence du timer.
    Sans le chiffre, porter l'intervalle à une heure laissait la suite verte
    alors que le « fil de l'eau » n'en était plus un."""
    contenu = _source(ecran)

    assert re.search(r"setInterval\(\s*pollRepartition\s*,\s*5000\s*\)", contenu), (
        f"{ecran.name} : l'aperçu Répartition n'est plus rafraîchi toutes les "
        "5 s — l'onglet cesserait de suivre la transcription"
    )


@pytest.mark.parametrize("ecran", LES_DEUX, ids=lambda p: p.name)
def test_le_poll_ne_part_pas_sans_session(ecran: Path) -> None:
    """Sans cette sortie anticipée, la page interroge la route toutes les cinq
    secondes dès son ouverture, jeton vide, avant tout enregistrement."""
    contenu = _source(ecran)
    corps = _bloc(contenu, contenu.index("function pollRepartition"))

    assert re.search(r"if\s*\(\s*!\s*sessionToken\s*\)\s*return", corps), (
        f"{ecran.name} : `pollRepartition` interroge le serveur même sans "
        "session en cours"
    )
