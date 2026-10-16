"""`app/static/autosave.js` — EXÉCUTION RÉELLE sous Node (bac à sable DOM minimal).

Le VRAI fichier est exécuté ; on simule `htmx:responseError` avec
`xhr.status` = 404 / 401 / 500 / 0 et on lit ce que l'utilisateur verrait :
texte, lien de reconnexion, persistance au-delà des 3 s, attributs live.
Aucun accès base.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from test_repartition_live import _node
except ImportError:  # `tests` importable comme paquet
    from tests.test_repartition_live import _node

SCRIPT_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "autosave.js"

_BAC = r"""
function Classes(el) { this.s = new Set(); }
Classes.prototype.add = function (n) { this.s.add(n); };
Classes.prototype.remove = function (n) { this.s.delete(n); };
Classes.prototype.contains = function (n) { return this.s.has(n); };
Classes.prototype.toggle = function (n, on) { if (on) this.s.add(n); else this.s.delete(n); };

function Elt(tag) {
  this.tag = tag; this.attrs = {}; this.children = []; this._texte = '';
  this.classList = new Classes(); this.listeners = {};
}
Object.defineProperty(Elt.prototype, 'textContent', {
  get: function () { return this._texte + this.children.map(function (c) { return c.textContent; }).join(''); },
  set: function (v) { this._texte = v; this.children = []; }
});
Object.defineProperty(Elt.prototype, 'className', {
  set: function (v) { var self = this; v.split(' ').forEach(function (c) { self.classList.add(c); }); }
});
Elt.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Elt.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
Elt.prototype.appendChild = function (c) { this.children.push(c); return c; };
Elt.prototype.querySelector = function (sel) {
  var cls = sel.replace(/^\./, '');
  for (var i = 0; i < this.children.length; i++) {
    if (this.children[i].classList.contains(cls)) return this.children[i];
  }
  return null;
};
Elt.prototype.querySelectorAll = function (sel) {
  var cls = sel.replace(/^\./, ''), res = [];
  this.children.forEach(function (c) {
    if (c.classList.contains(cls)) res.push(c);
    res = res.concat(c.querySelectorAll(sel));
  });
  return res;
};
Elt.prototype.addEventListener = function (n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); };

var body = new Elt('body');
var slotInitial = new Elt('span'); slotInitial.classList.add('saved');
var cible404 = new Elt('span'); cible404.classList.add('saved');
var cible401 = new Elt('span'); cible401.classList.add('saved');
var cible500 = new Elt('span'); cible500.classList.add('saved');
var cible0 = new Elt('span'); cible0.classList.add('saved');
var tous = [slotInitial, cible404, cible401, cible500, cible0];
var statusInd = new Elt('span'); statusInd.classList.add('status-ind');

var leurre = new Elt('span'); leurre.classList.add('autre');  // jamais .saved
var document = {
  body: body,
  createElement: function (t) { return new Elt(t); },
  // Le sélecteur est RESPECTÉ (classe simple « .x ») : un leurre sans .saved
  // ne doit jamais devenir région live.
  querySelectorAll: function (sel) {
    var cls = sel.replace(/^\./, '');
    return tous.concat([leurre]).filter(function (e) { return e.classList.contains(cls); });
  }
};
var minuteries = [];
var setTimeout = function (f, d) { minuteries.push({ f: f, d: d }); return minuteries.length; };
var clearTimeout = function (id) { if (minuteries[id - 1]) minuteries[id - 1].f = null; };

%(code)s

function emettre(nom, detail) { (body.listeners[nom] || []).forEach(function (f) { f({ detail: detail }); }); }
// N'exécute que les minuteries dont le délai est <= 3000 ms (le « 3 s » est asserté).
function fin3s() { minuteries.forEach(function (m) { if (m.f && m.d <= 3000) { var f = m.f; m.f = null; f(); } }); }
function etat(el) {
  var a = el.children.filter(function (c) { return c.tag === 'a'; })[0];
  return {
    texte: el.textContent, erreur: el.classList.contains('error'),
    role: el.getAttribute('role'), live: el.getAttribute('aria-live'),
    lien: a ? { href: a.getAttribute('href'), target: a.getAttribute('target'),
                rel: a.getAttribute('rel'), texte: a.textContent } : null
  };
}

var out = { init: tous.map(function (e) { return { role: e.getAttribute('role'), live: e.getAttribute('aria-live') }; }) };
emettre('htmx:responseError', { target: cible404, xhr: { status: 404 } });
emettre('htmx:responseError', { target: cible401, xhr: { status: 401 } });
emettre('htmx:responseError', { target: cible500, xhr: { status: 500 } });
emettre('htmx:responseError', { target: cible0, xhr: { status: 0 } });
out.avant = { s404: etat(cible404), s401: etat(cible401), s500: etat(cible500), s0: etat(cible0) };
fin3s();
out.apres = { s404: etat(cible404), s401: etat(cible401), s500: etat(cible500), s0: etat(cible0) };
// Cible .status-ind : l'enfant dédié reçoit les attributs live AVANT remplissage.
emettre('htmx:responseError', { target: statusInd, xhr: { status: 404 } });
var enfant = statusInd.querySelector('.autosave-err');
out.statusInd = enfant ? etat(enfant) : null;
// Sans xhr (robustesse) : message générique, pas d'exception.
emettre('htmx:responseError', { target: cible500 });
out.sansXhr = etat(cible500);
out.leurre = { role: leurre.getAttribute('role'), live: leurre.getAttribute('aria-live') };
out.delais = minuteries.map(function (m) { return m.d; });

// 500 PUIS 404 sur le même slot : la minuterie de 3 s du 500 ne doit pas
// effacer le message persistant du 404 (clearTimeout + timers.delete).
var cibleSeq = new Elt('span'); cibleSeq.classList.add('saved');
emettre('htmx:responseError', { target: cibleSeq, xhr: { status: 500 } });
emettre('htmx:responseError', { target: cibleSeq, xhr: { status: 404 } });
fin3s();
out.seq500404 = etat(cibleSeq);

// status en CHAÎNE « 404 » (non strict) : message générique, pas le persistant.
var cibleChaine = new Elt('span'); cibleChaine.classList.add('saved');
emettre('htmx:responseError', { target: cibleChaine, xhr: { status: '404' } });
out.statutChaine = etat(cibleChaine);

// Cibles sans classList / sans .saved : aucune exception, aucun effet.
var erreurs = [];
try { emettre('htmx:responseError', { target: {}, xhr: { status: 404 } }); } catch (e) { erreurs.push(String(e)); }
var autre = new Elt('span');
try { emettre('htmx:responseError', { target: autre, xhr: { status: 404 } }); } catch (e) { erreurs.push(String(e)); }
out.cibleHorsSlot = { erreurs: erreurs, texte: autre.textContent };

// Succès 2xx APRÈS un message persistant, SANS htmx:afterSwap (204 / réponse
// vide / swap none) : le message de CE slot doit disparaître.
function persistantPuisSucces(statut, avecSwap) {
  var s = new Elt('span'); s.classList.add('saved');
  emettre('htmx:responseError', { target: s, xhr: { status: 404 } });
  emettre('htmx:afterRequest', { target: s, successful: true, failed: false, xhr: { status: statut } });
  return etat(s);
}
out.succes200 = persistantPuisSucces(200);
out.succes204 = persistantPuisSucces(204);
// Un échec (successful=false) ne l'efface pas ; un autre slot n'est pas touché.
var sEchec = new Elt('span'); sEchec.classList.add('saved');
var sAutre = new Elt('span'); sAutre.classList.add('saved');
emettre('htmx:responseError', { target: sEchec, xhr: { status: 404 } });
emettre('htmx:responseError', { target: sAutre, xhr: { status: 401 } });
emettre('htmx:afterRequest', { target: sEchec, successful: false, failed: true, xhr: { status: 404 } });
out.echecGarde = etat(sEchec);
emettre('htmx:afterRequest', { target: sEchec, successful: true, failed: false, xhr: { status: 200 } });
out.autreIntact = etat(sAutre);
// Le « ✓ » posé par un swap réussi n'est pas effacé par l'afterRequest suivant.
var sOk = new Elt('span'); sOk.classList.add('saved');
emettre('htmx:responseError', { target: sOk, xhr: { status: 404 } });
sOk.textContent = '✓ enregistré';
emettre('htmx:afterSwap', { target: sOk });
emettre('htmx:afterRequest', { target: sOk, successful: true, failed: false, xhr: { status: 200 } });
out.checkGarde = etat(sOk);
// Cible .status-ind : l'enfant .autosave-err persistant est effacé aussi.
var ind = new Elt('span'); ind.classList.add('status-ind');
emettre('htmx:responseError', { target: ind, xhr: { status: 404 } });
emettre('htmx:afterRequest', { target: ind, successful: true, failed: false, xhr: { status: 200 } });
out.succesStatusInd = etat(ind.querySelector('.autosave-err'));

// 9a : slots INSÉRÉS par un swap (afterSwap / load) deviennent live ; .status-ind non.
var conteneur = new Elt('div');
var neuf = new Elt('span'); neuf.classList.add('saved');
var neufInd = new Elt('span'); neufInd.classList.add('saved'); neufInd.classList.add('status-ind');
conteneur.appendChild(neuf); conteneur.appendChild(neufInd);
emettre('htmx:afterSwap', { target: conteneur });
var neuf2 = new Elt('span'); neuf2.classList.add('saved');
var cont2 = new Elt('div'); cont2.appendChild(neuf2);
(body.listeners['htmx:load'] || []).forEach(function (f) { f({ target: cont2, detail: {} }); });
var neuf3 = new Elt('span'); neuf3.classList.add('saved');
emettre('htmx:afterSwap', { target: neuf3 });
out.swapLive = { desc: etat(neuf), ind: etat(neufInd), load: etat(neuf2), direct: etat(neuf3) };

// 9b : un échec transitoire (sendError / 500 / validation) ne remplace pas un persistant.
function persistantPuis(nom, detail, evenement) {
  var s = new Elt('span'); s.classList.add('saved');
  emettre('htmx:responseError', { target: s, xhr: { status: 404 } });
  emettre(evenement || nom, detail(s));
  fin3s();
  return etat(s);
}
out.p404sendError = persistantPuis('htmx:sendError', function (s) { return { target: s }; });
out.p404r500 = persistantPuis('htmx:responseError', function (s) { return { target: s, xhr: { status: 500 } }; });
var f = new Elt('form'); var sf = new Elt('span'); sf.classList.add('saved'); f.appendChild(sf);
emettre('htmx:responseError', { target: sf, xhr: { status: 401 } });
(body.listeners['htmx:validation:halted'] || []).forEach(function (g) { g({ target: f, detail: {} }); });
fin3s();
out.p401halted = etat(sf);

// 9c : le succès d'un AUTRE élément ne l'efface pas ; celui de l'élément en erreur, si.
var eltA = {}, eltB = {};
var s9 = new Elt('span'); s9.classList.add('saved');
emettre('htmx:responseError', { target: s9, elt: eltA, xhr: { status: 401 } });
emettre('htmx:afterRequest', { target: s9, elt: eltB, successful: true, failed: false, xhr: { status: 200 } });
out.autreElt = etat(s9);
emettre('htmx:afterRequest', { target: s9, elt: eltA, successful: true, failed: false, xhr: { status: 200 } });
out.memeElt = etat(s9);
// 9e (R1) : l'élément en erreur est REMPLACÉ (outerHTML) : le succès suivant
// vient d'un elt neuf ; la source n'étant plus connectée, le message est effacé.
var eltV = { isConnected: true }, eltN = { isConnected: true };
var s10 = new Elt('span'); s10.classList.add('saved');
emettre('htmx:responseError', { target: s10, elt: eltV, xhr: { status: 404 } });
emettre('htmx:afterRequest', { target: s10, elt: eltN, successful: true, failed: false, xhr: { status: 200 } });
out.remplaceAvant = etat(s10);   // source encore connectée : message gardé
eltV.isConnected = false;
emettre('htmx:afterRequest', { target: s10, elt: eltN, successful: true, failed: false, xhr: { status: 200 } });
out.remplaceApres = etat(s10);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def sortie() -> dict:
    # Le code est encapsulé dans une IIFE par le fichier lui-même ; `var`
    # du bac à sable sont globaux au script Node (commonjs : portée module).
    return _node(_BAC % {"code": SCRIPT_JS.read_text(encoding="utf-8")})


def test_404_message_explicite_et_persistant(sortie: dict) -> None:
    msg = "⚠ fiche introuvable — rien n'est enregistré. Copiez votre texte avant de quitter."
    assert sortie["avant"]["s404"]["texte"] == msg
    assert sortie["avant"]["s404"]["erreur"] is True
    assert sortie["avant"]["s404"]["lien"] is None
    assert sortie["apres"]["s404"]["texte"] == msg, "le message 404 a été effacé au bout de 3 s"
    assert sortie["apres"]["s404"]["erreur"] is True


def test_401_lien_de_reconnexion_dans_un_nouvel_onglet_et_persistant(sortie: dict) -> None:
    av, ap = sortie["avant"]["s401"], sortie["apres"]["s401"]
    assert av["texte"] == "⚠ session expirée — reconnectez-vous"
    lien = av["lien"]
    assert lien is not None, "aucun lien de reconnexion"
    assert lien["href"] == "/connexion"
    assert lien["target"] == "_blank"
    assert "noopener" in lien["rel"]
    assert lien["texte"] == "reconnectez-vous"
    assert ap["texte"] == av["texte"] and ap["lien"] == lien, "le message 401 a été effacé"


@pytest.mark.parametrize("cle", ["s500", "s0"])
def test_autres_echecs_message_actuel_conserve_et_efface(sortie: dict, cle: str) -> None:
    assert sortie["avant"][cle]["texte"] == "⚠ échec de l'enregistrement"
    assert sortie["avant"][cle]["lien"] is None
    assert sortie["apres"][cle]["texte"] == "", "l'effacement à 3 s a disparu pour les autres échecs"
    assert sortie["apres"][cle]["erreur"] is False
    assert sortie["sansXhr"]["texte"] == "⚠ échec de l'enregistrement"


def test_slots_de_statut_sont_des_regions_live(sortie: dict) -> None:
    assert all(i == {"role": "status", "live": "polite"} for i in sortie["init"]), sortie["init"]
    enfant = sortie["statusInd"]
    assert enfant is not None and enfant["role"] == "status" and enfant["live"] == "polite"
    assert enfant["texte"].startswith("⚠ fiche introuvable")


def test_selecteur_respecte_seul_le_slot_saved_devient_live(sortie: dict) -> None:
    assert sortie["leurre"] == {"role": None, "live": None}


def test_minuteries_toutes_a_3000_ms(sortie: dict) -> None:
    assert sortie["delais"] and set(sortie["delais"]) == {3000}, sortie["delais"]


def test_500_puis_404_meme_slot_le_message_persistant_survit(sortie: dict) -> None:
    s = sortie["seq500404"]
    assert s["texte"].startswith("⚠ fiche introuvable") and s["erreur"] is True, s


def test_statut_en_chaine_donne_le_message_generique(sortie: dict) -> None:
    # Comportement actuel documenté : comparaison stricte (htmx fournit un nombre).
    assert sortie["statutChaine"]["texte"] == "⚠ échec de l'enregistrement"


def test_cible_sans_classlist_ou_hors_slot_est_ignoree(sortie: dict) -> None:
    assert sortie["cibleHorsSlot"] == {"erreurs": [], "texte": ""}


@pytest.mark.parametrize("cle", ["succes200", "succes204", "succesStatusInd"])
def test_succes_2xx_sans_afterswap_efface_le_message_persistant(sortie: dict, cle: str) -> None:
    assert sortie[cle]["texte"] == "" and sortie[cle]["erreur"] is False, sortie[cle]


def test_succes_n_efface_ni_un_echec_ni_un_autre_slot_ni_le_coche(sortie: dict) -> None:
    assert sortie["echecGarde"]["texte"].startswith("⚠ fiche introuvable")
    assert sortie["autreIntact"]["texte"].startswith("⚠ session expirée")
    assert sortie["checkGarde"]["texte"] == "✓ enregistré"


def test_9a_slots_inseres_par_un_swap_sont_live_sauf_status_ind(sortie: dict) -> None:
    sw = sortie["swapLive"]
    live = {"role": "status", "live": "polite"}
    for cle in ("desc", "load", "direct"):
        assert {"role": sw[cle]["role"], "live": sw[cle]["live"]} == live, (cle, sw[cle])
    assert sw["ind"]["role"] is None and sw["ind"]["live"] is None, sw["ind"]


@pytest.mark.parametrize("cle", ["p404sendError", "p404r500"])
def test_9b_echec_transitoire_ne_remplace_pas_un_persistant(sortie: dict, cle: str) -> None:
    assert sortie[cle]["texte"].startswith("⚠ fiche introuvable") and sortie[cle]["erreur"] is True, sortie[cle]


def test_9b_validation_halted_ne_remplace_pas_un_persistant(sortie: dict) -> None:
    assert sortie["p401halted"]["texte"].startswith("⚠ session expirée"), sortie["p401halted"]


def test_9c_succes_d_un_autre_element_n_efface_pas_le_message(sortie: dict) -> None:
    assert sortie["autreElt"]["texte"].startswith("⚠ session expirée"), sortie["autreElt"]
    assert sortie["memeElt"]["texte"] == "" and sortie["memeElt"]["erreur"] is False


def test_9d_serveur_sans_parametre_de_retour_lien_inchange() -> None:
    # /connexion n'expose aucun paramètre de retour (app/auth.py) : le lien reste /connexion.
    auth = (SCRIPT_JS.parent.parent / "auth.py").read_text(encoding="utf-8")
    assert "next" not in auth.lower().replace("call_next", "")


def test_9e_source_remplacee_par_un_swap_le_succes_suivant_efface_le_message(sortie: dict) -> None:
    # R1 : source encore connectée -> message gardé ; source détachée -> effacé.
    assert sortie["remplaceAvant"]["texte"].startswith("⚠ fiche introuvable"), sortie["remplaceAvant"]
    assert sortie["remplaceApres"]["texte"] == "" and sortie["remplaceApres"]["erreur"] is False, sortie["remplaceApres"]
