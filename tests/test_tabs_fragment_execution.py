"""Onglets : un fragment d'URL hors onglets ne rejette plus l'éditeur sur le
premier onglet — EXÉCUTION RÉELLE de `app/static/tabs.js` sous node.

Le lien « voir le deck d'exemple » ajouté le 2026-09-27 pointe vers l'ancre
`#deck-exemple`, qui ne désigne AUCUN onglet. `tabs.js` écoute `hashchange` et
retombait alors sur `tabs[0]` : le consultant qui cliquait ce lien depuis l'onglet
Indicateurs se retrouvait sur le premier onglet, sans rien avoir demandé.

Une assertion de présence de chaîne serait aveugle ici (le défaut est dans le
CHEMIN d'exécution d'un gestionnaire d'événement, pas dans le texte du fichier) :
le script est donc joué pour de vrai dans un bac à sable, et ce qui est vérifié
est l'ÉTAT après exécution — quel onglet porte la classe `active`.
"""
from __future__ import annotations

from pathlib import Path

try:  # `tests/` sur sys.path (mode d'import par défaut de pytest)
    from test_repartition_live import _node
except ImportError:  # `tests` importable comme paquet
    from tests.test_repartition_live import _node

RACINE = Path(__file__).resolve().parent.parent
TABS_JS = RACINE / "app" / "static" / "tabs.js"

# Bac à sable : deux onglets (« kpis », « risques ») et leurs panneaux, un hash
# pilotable, et `hashchange` déclenché à la main comme le fait le navigateur.
_SANDBOX = r"""
const vm = require('vm');

function Element(role, nom) {
  this.dataset = role === 'tab' ? {tab: nom} : {panel: nom};
  this.classes = new Set();
  const self = this;
  this.classList = {
    toggle: function (c, on) { if (on) self.classes.add(c); else self.classes.delete(c); },
  };
  this.addEventListener = function () {};
}
const onglets = [new Element('tab', 'kpis'), new Element('tab', 'risques')];
const panneaux = [new Element('panel', 'kpis'), new Element('panel', 'risques')];
const conteneur = {
  querySelectorAll: function (sel) {
    return sel === '.tab' ? onglets : panneaux;
  },
  querySelector: function (sel) {
    const m = /\.tab\[data-tab="(.*)"\]/.exec(sel);
    return m ? onglets.find(function (o) { return o.dataset.tab === m[1]; }) || null : null;
  },
};
const ecoutes = {};
const location = {hash: %(hash_initial)s};
const window = {
  addEventListener: function (ev, fn) { ecoutes[ev] = fn; },
};
const CSS = {escape: function (s) { return s; }};
const document = {
  querySelectorAll: function () { return [conteneur]; },
};
const contexte = {document: document, window: window, location: location, CSS: CSS,
                  console: console, require: require};
contexte.globalThis = contexte;
vm.createContext(contexte);
vm.runInContext(%(source)s, contexte);

function actif() {
  const o = onglets.find(function (x) { return x.classes.has('active'); });
  const p = panneaux.find(function (x) { return x.classes.has('active'); });
  return [o ? o.dataset.tab : null, p ? p.dataset.panel : null];
}
const etats = {apres_chargement: actif()};
%(scenario)s
console.log(JSON.stringify(etats));
"""


def _bac(scenario: str, hash_initial: str = "") -> dict:
    import json
    return _node(_SANDBOX % {
        "source": json.dumps(TABS_JS.read_text(encoding="utf-8")),
        "hash_initial": json.dumps(hash_initial),
        "scenario": scenario,
    })


def _naviguer(fragment: str) -> str:
    """Navigation hash-only, comme un clic sur un lien interne."""
    return (f"location.hash = {fragment!r};\n"
            "ecoutes['hashchange']();\n")


def test_un_fragment_hors_onglets_ne_change_pas_l_onglet_ouvert() -> None:
    """Le cas signalé : l'utilisateur est sur « risques », clique le lien vers
    l'ancre `#deck-exemple` — son onglet ne doit pas bouger."""
    etats = _bac(
        "onglets[0].classList.toggle('active', false);\n"
        "onglets[1].classList.toggle('active', true);\n"
        "panneaux[0].classList.toggle('active', false);\n"
        "panneaux[1].classList.toggle('active', true);\n"
        + _naviguer("#deck-exemple")
        + "etats.apres_ancre_hors_onglets = actif();\n"
    )
    assert etats["apres_chargement"] == ["kpis", "kpis"]
    assert etats["apres_ancre_hors_onglets"] == ["risques", "risques"]


def test_un_fragment_d_onglet_selectionne_toujours_cet_onglet() -> None:
    """Le comportement utile est intact : `#risques` sélectionne bien l'onglet
    Risques, au chargement comme sur une navigation hash-only."""
    etats = _bac(_naviguer("#risques") + "etats.apres_fragment_onglet = actif();\n")
    assert etats["apres_fragment_onglet"] == ["risques", "risques"]

    etats2 = _bac("", hash_initial="#risques")
    assert etats2["apres_chargement"] == ["risques", "risques"]


def test_un_fragment_inconnu_au_chargement_active_quand_meme_un_onglet() -> None:
    """Au CHARGEMENT, il faut un onglet actif : arriver directement sur
    `…/apercu#deck-exemple` ne doit pas laisser la page sans aucun panneau
    visible. La tolérance ne vaut que pour les navigations ultérieures."""
    etats = _bac("", hash_initial="#deck-exemple")
    assert etats["apres_chargement"] == ["kpis", "kpis"]


def test_le_retour_a_une_url_sans_fragment_revient_a_l_onglet_par_defaut() -> None:
    """Contrat de 2026-07-29 préservé : URL et onglet ne divergent pas. Sans
    fragment du tout, on revient à l'onglet par défaut."""
    etats = _bac(
        _naviguer("#risques")
        + "location.hash = ''; ecoutes['hashchange']();\n"
        + "etats.apres_retour = actif();\n"
    )
    assert etats["apres_retour"] == ["kpis", "kpis"]
