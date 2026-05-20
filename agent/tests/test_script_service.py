from __future__ import annotations

import pytest

from smartcs.services.assist.script_service import ScriptService
from smartcs.shared.models import IntentLabel


@pytest.fixture
def service():
    return ScriptService()


def test_load_scripts_from_memory(service):
    service.load_from_memory()
    assert len(service._scripts) > 0


def test_retrieve_by_intent_faq(service):
    service.load_from_memory()
    results = service.retrieve(intent=IntentLabel.FAQ, top_k=3)
    assert len(results) > 0
    for s in results:
        assert s["category"] == IntentLabel.FAQ.value


def test_retrieve_respects_top_k(service):
    service.load_from_memory()
    results = service.retrieve(intent=IntentLabel.INSTALLMENT_INQUIRY, top_k=1)
    assert len(results) <= 1


def test_retrieve_returns_empty_for_no_match(service):
    service.load_from_memory()
    results = service.retrieve(intent=IntentLabel.TRANSFER_AGENT, top_k=3)
    assert results == []


def test_resolve_variables(service):
    service.load_from_memory()
    script = service.retrieve(intent=IntentLabel.BILL_QUERY, top_k=1)[0]
    variables = {"customer_name": "王先生", "bill_amount": "3256.80", "due_date": "2026-05-15"}
    result = service.resolve_variables(script, variables)
    assert "王先生" in result
    assert "3256.80" in result
    assert "{" not in result


def test_resolve_variables_no_placeholders(service):
    result = service.resolve_variables({"content": "您好", "variables": []}, {"customer_name": "test"})
    assert result == "您好"
