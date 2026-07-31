from __future__ import annotations

from lumio.services.common.chat_client import ChatSvcClient


def test_client_has_base_url():
    c = ChatSvcClient(base_url="http://localhost:8080")
    assert c._base_url == "http://localhost:8080"


def test_build_transfer_request():
    c = ChatSvcClient()
    req = c.build_transfer_request(
        session_id="sess-001",
        customer_id="cust-001",
        transfer_reason="complaint",
        transfer_summary="test",
        history=[{"role": "customer", "content": "hi"}],
        intent="complaint",
        sentiment="angry",
    )
    assert req["session_id"] == "sess-001"
    assert req["transfer_reason"] == "complaint"
    assert len(req["history"]) == 1
