"""Story 2.1 -- l'alerte « aucune parole detectee » est impossible a manquer.

AC2.1.1-AC2.1.3 sont verifies par EXECUTION du JS de l'ecran (bac a sable node
de test_tranches_et_reprise_execution), jamais par `.index()` sur le template.
Le rendu reel (fixed, role=alert, couleur) est joue dans un vrai navigateur
dans tests/test_e2e_premiers_clics.py. Aucun son : un bip serait capte par le micro.
"""
from __future__ import annotations

import json
import re

try:
    from test_tranches_et_reprise_execution import LIBRE, _bac, _js
    from test_repartition_live import _bloc, _corps_du_gestionnaire, _fonction, _sans_commentaires
except ImportError:
    from tests.test_tranches_et_reprise_execution import LIBRE, _bac, _js
    from tests.test_repartition_live import _bloc, _corps_du_gestionnaire, _fonction, _sans_commentaires

PREFIXE = "⚠ Micro muet — "
PARTAGE = "⚠ Partage perdu — "
TITRE = "Entretien libre — Interview-to-Deck"


def _scenario(corps: str, *, segment_ms: int = 60000, remote: bool = False,
              tab_actif: bool = False, demo: int = 0) -> dict:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    code = "".join(_js(_fonction(src, f)) for f in
                   ("majTitre", "afficherAlerte", "noteNoSpeech", "resetNoSpeech"))
    stop = _corps_du_gestionnaire(src, "stopBtn.addEventListener('click'")
    perdu = "(function () %s)();" % _bloc(src, src.index("tabLost: function"))
    ligne = re.search(r"if \(!lostId\) resetNoSpeech\(\);", src)
    assert ligne, "garde `if (!lostId) resetNoSpeech()` introuvable"
    return _bac(code + r"""
Object.assign(bac, {
  noSpeechStreak: 0, NOSPEECH_ALERT_AFTER: 2, titreOrigine: null, noSpeechActive: false,
  partagePerdu: false, alerteKind: null,
  NOSPEECH_TITLE_PREFIX: %(pref)s, PARTAGE_TITLE_PREFIX: %(part)s,
  SEGMENT_MS: %(seg)d, recordingActive: true,
  noSpeechBannerEl: el('b'), noSpeechTextEl: el('t'), noSpeechFixBtn: el('f'),
  noSpeechDurationEl: el('d'),
  remoteModeEl: { checked: %(remote)s },
  refreshSource: function () {},
  i2dAudioSource: { release: function () {}, supported: function () { return true; },
                    tabActive: function () { return %(tab)s; } }
});
el('b').style = {};
let ecritures = 0, texteAnnonce = '';
Object.defineProperty(el('t'), 'textContent', { configurable: true,
  get: function () { return texteAnnonce; }, set: function (v) { ecritures++; texteAnnonce = v; } });
bac.document.querySelector = function () {
  return %(demo)d ? { getBoundingClientRect: function () { return { height: %(demo)d }; } } : null;
};
jouer('document.title = ' + %(titre)s);
let out = {};
const titre = () => jouer('document.title');
const stopper = () => jouer(%(stop)s);
const partage_perdu = () => jouer(%(perdu)s);
const muet = (n) => { for (let k = 0; k < (n || 1); k++) jouer('noteNoSpeech()'); };
const parole = (lostId) => jouer('var lostId = ' + (lostId || 0) + '; ' + %(ligne)s);
%(corps)s
console.log(JSON.stringify(out));
""" % {"pref": json.dumps(PREFIXE), "part": json.dumps(PARTAGE), "seg": segment_ms,
       "remote": "true" if remote else "false", "tab": "true" if tab_actif else "false",
       "demo": demo, "titre": json.dumps(json.dumps(TITRE)), "stop": json.dumps(stop),
       "perdu": json.dumps(perdu), "ligne": json.dumps(ligne.group(0)), "corps": corps})


def test_alerte_masquee_au_chargement_et_au_1er_segment_muet() -> None:
    r = _scenario("""
out.charge = { cache: el('b').hidden, titre: titre() };
muet(1);
out.un = { cache: el('b').hidden, titre: titre() };
""")
    assert r["charge"] == {"cache": True, "titre": TITRE}
    assert r["un"] == {"cache": True, "titre": TITRE}


def test_alerte_au_2e_segment_muet_duree_mise_a_jour_et_titre_prefixe() -> None:
    r = _scenario("""
muet(2);
out.a2 = { cache: el('b').hidden, titre: titre(), texte: el('t').textContent, duree: el('d').textContent };
muet(1);
out.a3 = { texte: el('t').textContent, duree: el('d').textContent };
muet(2);
out.a5 = { duree: el('d').textContent, titre: titre() };
out.ecritures = ecritures;
""")
    assert r["a2"]["cache"] is False and r["a2"]["titre"] == PREFIXE + TITRE   # AC2.1.1, AC2.1.3
    assert "~2 min (2 segments)" in r["a2"]["duree"]
    assert "~3 min (3 segments)" in r["a3"]["duree"]                          # AC2.1.2
    assert "~5 min (5 segments)" in r["a5"]["duree"] and r["a5"]["titre"] == PREFIXE + TITRE
    assert r["a3"]["texte"] == r["a2"]["texte"]   # B : le noeud role=alert n'est PAS reecrit
    assert r["ecritures"] == 1, r


def test_duree_suit_SEGMENT_MS_avec_plancher_a_1_min() -> None:
    r = _scenario("muet(2); out = el('d').textContent;", segment_ms=69000)
    assert "~2 min (2 segments)" in r                 # 2 x 69 s = 2,3 min
    r = _scenario("muet(3); out = el('d').textContent;", segment_ms=90000)
    assert "~5 min (3 segments)" in r                 # 3 x 90 s = 4,5 min -> 5
    r = _scenario("muet(2); out = el('d').textContent;", segment_ms=10000)
    assert "~1 min (2 segments)" in r                 # plancher Math.max(1, ...)


def test_message_actionnable_selon_le_mode_distance() -> None:
    r = _scenario("muet(2); out = { f: el('f').hidden, t: el('t').textContent };", remote=True)
    assert r["f"] is False and "Partager aussi l'audio" in r["t"]
    r = _scenario("muet(2); out = { f: el('f').hidden, t: el('t').textContent };", remote=False)
    assert r["f"] is True and "Vérifie le micro" in r["t"]
    r = _scenario("muet(2); out = { f: el('f').hidden };", remote=True, tab_actif=True)
    assert r["f"] is True


def test_alerte_ne_disparait_qua_le_retour_de_la_parole_et_restaure_le_titre() -> None:
    r = _scenario("""
muet(3);
out.muet = { cache: el('b').hidden, titre: titre() };
parole();
out.parole = { cache: el('b').hidden, titre: titre() };
""")
    assert r["muet"]["cache"] is False
    assert r["parole"] == {"cache": True, "titre": TITRE}


def test_relance_d_un_vieux_segment_ne_leve_pas_l_alerte() -> None:
    r = _scenario("""
muet(3);
parole(7);                       // succes d'une relance (lostId = 7)
out.relance = { cache: el('b').hidden, titre: titre() };
parole(0);                       // vrai segment courant
out.courant = { cache: el('b').hidden, titre: titre() };
""")
    assert r["relance"] == {"cache": False, "titre": PREFIXE + TITRE}
    assert r["courant"] == {"cache": True, "titre": TITRE}


def test_arret_vide_bandeau_compteur_et_titre() -> None:
    r = _scenario("""
muet(2);
out.avant = titre();
stopper();
out.apres = { titre: titre(), cache: el('b').hidden, serie: jouer('noSpeechStreak') };
""")
    assert r["avant"] == PREFIXE + TITRE
    assert r["apres"] == {"titre": TITRE, "cache": True, "serie": 0}


def test_segment_muet_tardif_apres_arret_ne_reaffiche_rien() -> None:
    r = _scenario("""
muet(2); stopper();
muet(3);
out = { titre: titre(), cache: el('b').hidden, serie: jouer('noSpeechStreak') };
""")
    assert r == {"titre": TITRE, "cache": True, "serie": 0}


def test_partage_perdu_a_son_propre_prefixe_et_survit_au_retour_de_la_parole() -> None:
    r = _scenario("""
partage_perdu();
out.perdu = { cache: el('b').hidden, titre: titre(), texte: el('t').textContent };
muet(2);                              // micro muet EN PLUS : un seul prefixe, partage prime
out.les_deux = { titre: titre(), texte: el('t').textContent, duree: el('d').textContent };
parole();                             // la parole revient, le partage reste perdu
out.parole = { cache: el('b').hidden, titre: titre() };
stopper();
out.arret = { cache: el('b').hidden, titre: titre() };
""")
    assert r["perdu"]["cache"] is False and r["perdu"]["titre"] == PARTAGE + TITRE
    assert "partage du son" in r["perdu"]["texte"]
    assert r["les_deux"]["titre"] == PARTAGE + TITRE                 # pas d'empilement
    assert r["les_deux"]["texte"] == r["perdu"]["texte"] and "~2 min" in r["les_deux"]["duree"]
    assert r["parole"] == {"cache": False, "titre": PARTAGE + TITRE}
    assert r["arret"] == {"cache": True, "titre": TITRE}


def test_micro_muet_puis_partage_perdu_change_de_prefixe() -> None:
    r = _scenario("""
muet(2); out.muet = titre();
partage_perdu(); out.perdu = titre();
""")
    assert r["muet"] == PREFIXE + TITRE and r["perdu"] == PARTAGE + TITRE


def test_alerte_se_place_sous_le_bandeau_demo() -> None:
    assert _scenario("muet(2); out = el('b').style.top;", demo=26) == "26px"
    assert _scenario("muet(2); out = el('b').style.top;") == "0px"
