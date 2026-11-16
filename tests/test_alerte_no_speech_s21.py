"""Story 2.1 -- l'alerte « aucune parole detectee » est impossible a manquer.

AC2.1.1-AC2.1.3 sont verifies par EXECUTION du JS de l'ecran (bac a sable node
de test_tranches_et_reprise_execution), jamais par `.index()` sur le template.
Le rendu reel (sticky, role=alert, couleur) est joue dans un vrai navigateur
dans tests/test_e2e_premiers_clics.py. Aucun son : un bip serait capte par le micro.
"""
from __future__ import annotations

import json

try:
    from test_tranches_et_reprise_execution import LIBRE, _bac, _js
    from test_repartition_live import _bloc, _corps_du_gestionnaire, _fonction, _sans_commentaires
except ImportError:
    from tests.test_tranches_et_reprise_execution import LIBRE, _bac, _js
    from tests.test_repartition_live import _bloc, _corps_du_gestionnaire, _fonction, _sans_commentaires

PREFIXE = "\u26a0 Micro muet \u2014 "
TITRE = "Entretien libre \u2014 Interview-to-Deck"


def _scenario(corps: str) -> dict:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    code = "".join(_js(_fonction(src, f)) for f in
                   ("marquerTitreMuet", "restaurerTitre", "noteNoSpeech", "resetNoSpeech"))
    stop = _corps_du_gestionnaire(src, "stopBtn.addEventListener('click'")
    return _bac(code + r"""
Object.assign(bac, {
  noSpeechStreak: 0, NOSPEECH_ALERT_AFTER: 2, NOSPEECH_TITLE_PREFIX: %(pref)s, titreOrigine: null,
  SEGMENT_MS: 60000, recordingActive: true,
  noSpeechBannerEl: el('b'), noSpeechTextEl: el('t'), noSpeechFixBtn: el('f'),
  remoteModeEl: { checked: false },
  i2dAudioSource: { release: function () {}, supported: function () { return true; }, tabActive: function () { return false; } }
});
jouer('document.title = ' + %(titre)s);
const out = {};
const titre = () => jouer('document.title');
const stopper = () => jouer(%(stop)s);
%(corps)s
console.log(JSON.stringify(out));
""" % {"pref": json.dumps(PREFIXE), "titre": json.dumps(json.dumps(TITRE)),
       "stop": json.dumps(stop), "corps": corps})


def test_alerte_au_2e_segment_muet_duree_mise_a_jour_et_titre_prefixe() -> None:
    r = _scenario(r"""
jouer('noteNoSpeech()');
out.apres1 = { cache: el('b').hidden, titre: titre() };
jouer('noteNoSpeech()');
out.apres2 = { cache: el('b').hidden, titre: titre(), texte: el('t').textContent };
jouer('noteNoSpeech()');
out.apres3 = { cache: el('b').hidden, titre: titre(), texte: el('t').textContent };
jouer('noteNoSpeech()'); jouer('noteNoSpeech()');
out.apres5 = { texte: el('t').textContent, titre: titre() };
""")
    assert r["apres1"] == {"cache": True, "titre": TITRE}            # sous le seuil : rien
    assert r["apres2"]["cache"] is False                              # AC2.1.1
    assert r["apres2"]["titre"] == PREFIXE + TITRE                    # AC2.1.3
    assert "2 derniers segments (~2 min)" in r["apres2"]["texte"]
    assert "3 derniers segments (~3 min)" in r["apres3"]["texte"]     # AC2.1.2 : a chaque segment
    assert "5 derniers segments (~5 min)" in r["apres5"]["texte"]
    assert r["apres5"]["titre"] == PREFIXE + TITRE                    # prefixe pose UNE fois


def test_alerte_ne_disparait_qua_le_retour_de_la_parole_et_restaure_le_titre() -> None:
    r = _scenario(r"""
jouer('noteNoSpeech(); noteNoSpeech(); noteNoSpeech()');
out.muet = { cache: el('b').hidden, titre: titre() };
jouer('resetNoSpeech()');
out.parole = { cache: el('b').hidden, titre: titre() };
""")
    assert r["muet"]["cache"] is False
    assert r["parole"] == {"cache": True, "titre": TITRE}


def test_arret_de_l_enregistrement_restaure_le_titre_d_origine() -> None:
    r = _scenario(r"""
jouer('noteNoSpeech(); noteNoSpeech()');
out.avant = titre();
stopper();
out.apres = titre();
""")
    assert r["avant"] == PREFIXE + TITRE
    assert r["apres"] == TITRE
