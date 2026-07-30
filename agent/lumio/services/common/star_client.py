"""star-connection HTTP 客户端"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class StarConnectionClient:
    """star-connection HTTP 客户端"""

    def __init__(self, base_url: str = "http://localhost:8080") -> None:
        self._base_url = base_url.rstrip("/")

    def build_transfer_request(
        self,
        session_id: str,
        customer_id: str | None = None,
        transfer_reason: str = "",
        transfer_summary: str = "",
        history: list[dict[str, str]] | None = None,
        intent: str = "",
        sentiment: str = "",
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "customer_id": customer_id or "",
            "transfer_reason": transfer_reason,
            "transfer_summary": transfer_summary,
            "history": history or [],
            "intent": intent,
            "sentiment": sentiment,
        }

    async def create_session(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/sessions — create agent session on star-connection"""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self._base_url}/api/sessions", json=data)
            if resp.status_code != 200:
                logger.error("create_session failed: %s %s", resp.status_code, resp.text)
                raise RuntimeError(f"star-connection returned {resp.status_code}")
            return resp.json()
