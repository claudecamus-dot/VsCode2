"""`call_ai_json` doit recopier les drapeaux de l'`AIError` qu'il ré-emballe.

Le test voisin `test_drapeau_transitoire_traverse_le_reemballage...` n'appelle
jamais `call_ai_json` : supprimer `wrapped.transitoire = exc.transitoire` le
laissait vert, alors que le retry de tranche (`getattr(exc, "transitoire")`)
ne se déclencherait plus en production. Ici l'appelant du fournisseur est
remplacé par un faux (pas de réseau, indépendant de la coupe Ollama)."""
from __future__ import annotations

import pytest

from app.services import ai_common


class _ErreurAppelant(ai_common.AIError):
    """Classe d'erreur fournie par l'appelant (`error_cls`)."""


def _erreur(*, transitoire: bool = False, timeout: bool = False) -> ai_common.AIError:
    exc = ai_common.AIError("panne simulee")
    exc.transitoire = transitoire
    exc.timeout = timeout
    return exc


def _appel_avec_faux(monkeypatch: pytest.MonkeyPatch, exc: ai_common.AIError) -> _ErreurAppelant:
    def faux_appelant(*args, **kwargs):
        raise exc

    monkeypatch.setattr(ai_common, "active_provider", lambda: "ollama")
    monkeypatch.setattr(ai_common, "_SDK_LOADERS", {"ollama": lambda: object()})
    monkeypatch.setitem(ai_common._CALLERS, "ollama", faux_appelant)
    with pytest.raises(_ErreurAppelant) as info:
        ai_common.call_ai_json("sys", "prompt", {}, "\nJSON.", error_cls=_ErreurAppelant)
    return info.value


def test_call_ai_json_conserve_le_drapeau_transitoire_vrai(monkeypatch) -> None:
    levee = _appel_avec_faux(monkeypatch, _erreur(transitoire=True))
    assert type(levee) is _ErreurAppelant
    assert levee.transitoire is True


def test_call_ai_json_conserve_le_drapeau_timeout(monkeypatch) -> None:
    levee = _appel_avec_faux(monkeypatch, _erreur(timeout=True))
    assert type(levee) is _ErreurAppelant
    assert levee.timeout is True
