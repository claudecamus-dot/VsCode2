"""Exception contract of the narrowed excepts of app/services/pptx_deck.py.

An EXPECTED error yields the fallback; an UNEXPECTED one (RuntimeError) propagates."""

import pytest

from app.services import pptx_deck as D


class _Ombre:
    def __init__(self, exc):
        self._exc = exc

    @property
    def inherit(self):
        return True

    @inherit.setter
    def inherit(self, v):
        raise self._exc("boom")


class _FormeOmbre:
    def __init__(self, exc):
        self.shadow = _Ombre(exc)


class _FormeGeo:
    def __init__(self, exc):
        self._exc = exc

    @property
    def left(self):
        raise self._exc("boom")


class _Adj:
    def __init__(self, exc):
        self._exc = exc

    def __setitem__(self, i, v):
        raise self._exc("boom")


class _Prs:
    def __init__(self, exc):
        self._exc = exc

    @property
    def slide_masters(self):
        raise self._exc("boom")


@pytest.mark.parametrize("exc", [AttributeError, NotImplementedError])
def test_no_shadow_expected_falls_back(exc):
    D._no_shadow(_FormeOmbre(exc))


def test_no_shadow_unexpected_propagates():
    with pytest.raises(RuntimeError):
        D._no_shadow(_FormeOmbre(RuntimeError))


@pytest.mark.parametrize("exc", [AttributeError, NotImplementedError, ValueError, TypeError])
def test_bornes_in_expected_returns_none(exc):
    assert D._bornes_in(_FormeGeo(exc)) is None


def test_bornes_in_unexpected_propagates():
    with pytest.raises(RuntimeError):
        D._bornes_in(_FormeGeo(RuntimeError))


@pytest.mark.parametrize("exc", [IndexError, AttributeError, KeyError])
def test_police_marque_expected_returns_none(exc):
    assert D.police_marque(_Prs(exc)) is None


def test_police_marque_unexpected_propagates():
    with pytest.raises(RuntimeError):
        D.police_marque(_Prs(RuntimeError))


def test_xfrm_groupe_unexpected_propagates():
    class _El:
        def find(self, *_):
            raise RuntimeError("boom")

    class _Shp:
        _element = _El()

    with pytest.raises(RuntimeError):
        D._xfrm_de_groupe_non_transforme(_Shp())


def test_xfrm_groupe_expected_false():
    class _Shp:
        pass  # no _element -> AttributeError

    assert D._xfrm_de_groupe_non_transforme(_Shp()) is False
