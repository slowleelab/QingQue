from __future__ import annotations

from smartcs.services.common.star_client import StarConnectionClient


def test_client_has_base_url():
    c = StarConnectionClient(base_url="http://localhost:8080")
    assert c._base_url == "http://localhost:8080"

def test_build_transfer_request():
    c = StarConnectionClient()
    req = c.build_transfer_request(
        session_id="sess-001", customer_id="cust-001",
        transfer_reason="complaint", transfer_summary="test",
        history=[{"role":"customer","content":"hi"}],
        intent="complaint", sentiment="angry",
    )
    assert req["session_id"] == "sess-001"
    assert req["transfer_reason"] == "complaint"
    assert len(req["history"]) == 1
