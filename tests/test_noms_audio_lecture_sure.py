"""SH-1 : la garde de LECTURE des noms audio (`_noms_audio`/`_tranches_audio`)
rejette aussi `:` (lecteur Windows `D:x.webm`, flux ADS), comme `_reference_sure`
a l'ecriture — sans exiger le prefixe de mission (noms legacy `import_<ts>.webm`)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers import interviews_retranscription as rt


def _itw(segments=None, backup=None):
    return SimpleNamespace(audio_segments=segments, audio_backup_path=backup)


def _seg(nom, pos=0):
    return {"filename": nom, "position": pos}


@pytest.mark.parametrize("nom", ["D:x.webm", "C:evil.webm", "4_a.webm:ads", "x:y"])
def test_nom_avec_deux_points_refuse_dans_segments(nom):
    assert rt._noms_audio(_itw([_seg(nom)])) == []


@pytest.mark.parametrize("nom", ["D:x.webm", "C:evil.webm"])
def test_nom_avec_deux_points_refuse_dans_backup_seul(nom):
    assert rt._noms_audio(_itw([], backup=nom)) == []


def test_tranches_audio_n_ouvre_pas_un_nom_avec_deux_points(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "RECORDINGS_DIR", tmp_path)
    ouverts = []
    vrai = Path.is_file

    def espion(self):
        ouverts.append(str(self))
        return vrai(self)

    monkeypatch.setattr(Path, "is_file", espion)
    presents, absentes = rt._tranches_audio(_itw([_seg("D:x.webm")]))
    assert (presents, absentes) == ([], 0)
    assert not any("D:x.webm" in o for o in ouverts)


@pytest.mark.parametrize(
    "nom",
    ["4_a_recent.webm", "import_1785351999_f938b8c0.webm", "12_import_1792521817_2266db81.weba"],
)
def test_noms_legitimes_passent(nom):
    assert rt._noms_audio(_itw([_seg(nom)])) == [nom]
    assert rt._noms_audio(_itw([], backup=nom)) == [nom]


@pytest.mark.parametrize("nom", ["a/b.webm", "a\\b.webm", "..x.webm", "../x.webm"])
def test_rejets_existants_tiennent(nom):
    assert rt._noms_audio(_itw([_seg(nom)])) == []
