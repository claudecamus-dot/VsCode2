"""Tests de `paginer_items` (pptx_deck.py) — bin-packing pur, sans
dépendance à python-pptx/DB/HTTP, utilisé par la pagination auto de
l'export PPT (voir pptx_export.py)."""
from __future__ import annotations

from app.services.pptx_deck import paginer_items


def test_paginer_items_splits_when_capacity_exceeded() -> None:
    assert paginer_items([1, 1, 1, 1], lambda x: x, 2) == [[1, 1], [1, 1]]


def test_paginer_items_single_page_when_everything_fits() -> None:
    assert paginer_items([1, 1, 1], lambda x: x, 10) == [[1, 1, 1]]


def test_paginer_items_oversized_item_alone_on_its_page() -> None:
    assert paginer_items([3], lambda x: x, 2) == [[3]]
    assert paginer_items([1, 3, 1], lambda x: x, 2) == [[1], [3], [1]]


def test_paginer_items_empty_input_returns_one_empty_page() -> None:
    assert paginer_items([], lambda x: x, 2) == [[]]


def test_paginer_items_preserves_order_and_drops_nothing() -> None:
    items = list(range(10))
    pages = paginer_items(items, lambda x: 1, 3)
    assert [x for page in pages for x in page] == items
