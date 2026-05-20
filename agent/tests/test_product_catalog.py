from __future__ import annotations

import pytest

from smartcs.services.assist.product_catalog import Product, ProductCatalog
from smartcs.shared.models import IntentLabel


@pytest.fixture
def catalog():
    return ProductCatalog()


def test_catalog_has_products(catalog):
    assert len(catalog.products) > 0


def test_match_by_intent_installment(catalog):
    results = catalog.match(intent=IntentLabel.INSTALLMENT_INQUIRY, top_k=2)
    assert len(results) > 0
    assert all(isinstance(p, Product) for p in results)


def test_match_returns_empty_for_no_match(catalog):
    results = catalog.match(intent=IntentLabel.CHITCHAT, top_k=2)
    assert results == []
