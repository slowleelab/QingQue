"""API 请求模型输入验证测试 (P3-7)"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lumio.shared.models import ChatRequest, ChatSendRequest


class TestChatRequestValidation:
    """P3-7 整改: 限制 message 长度防 DoS, 限制 session_id/customer_id 长度防注入."""

    def test_normal_message_passes(self) -> None:
        req = ChatRequest(message="信用卡年费怎么减免")
        assert req.message == "信用卡年费怎么减免"

    def test_message_too_long_rejected(self) -> None:
        """单条消息 > 2000 字符应被 Pydantic 拒绝 (防 Redis Stream + LLM 资源耗尽)."""
        long_msg = "a" * 2001
        with pytest.raises(ValidationError) as exc_info:
            ChatRequest(message=long_msg)
        # Pydantic 错误含 'max_length' 或 'at most 2000'
        err_text = str(exc_info.value)
        assert "2000" in err_text or "max_length" in err_text.lower()

    def test_message_at_limit_passes(self) -> None:
        """恰好 2000 字符边界值应通过 (off-by-one 防回归)."""
        req = ChatRequest(message="a" * 2000)
        assert len(req.message) == 2000

    def test_session_id_too_long_rejected(self) -> None:
        """session_id > 128 字符应被拒绝 (Redis key 注入防护)."""
        with pytest.raises(ValidationError):
            ChatRequest(message="test", session_id="s" * 129)

    def test_customer_id_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatRequest(message="test", customer_id="c" * 129)

    def test_empty_message_still_rejected_by_router(self) -> None:
        """Pydantic 层面不强制 non-empty (允许 router 业务校验决定) — 这里只确认类型约束生效."""
        # 空字符串是合法 str, 业务层做 .strip() 后判空
        req = ChatRequest(message="")
        assert req.message == ""

    def test_chat_send_request_inherits_max_length(self) -> None:
        """ChatSendRequest 继承 ChatRequest, 应同样受 max_length 约束."""
        with pytest.raises(ValidationError):
            ChatSendRequest(message="x" * 2001)
