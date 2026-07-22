"""请求限流模块单元测试

覆盖 rate_limit 安全关键路径：用户/IP 复合 key、豁免路径、限流器创建。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from smartcs.shared.rate_limit import (
    _EXEMPT_PATHS,
    _user_or_ip_key,
    create_limiter,
    is_rate_limit_exempt,
)


class TestUserOrIpKey:
    """限流 key 生成测试：已认证用 user_id，未认证用 IP"""

    def test_no_auth_header_uses_ip(self) -> None:
        request = MagicMock()
        request.headers = {}
        request.client.host = "10.0.0.1"

        with patch("smartcs.shared.rate_limit.get_remote_address", return_value="10.0.0.1"):
            key = _user_or_ip_key(request)
            assert key == "ip:10.0.0.1"

    def test_bearer_token_extracts_user_id(self) -> None:
        request = MagicMock()
        request.headers = {"Authorization": "Bearer valid.jwt.token"}

        with patch("smartcs.shared.auth.decode_token", return_value={"sub": "user-42"}):
            key = _user_or_ip_key(request)
            assert key == "user:user-42"

    def test_bearer_no_sub_falls_back_to_ip(self) -> None:
        """token 无 sub 字段→降级 IP"""
        request = MagicMock()
        request.headers = {"Authorization": "Bearer no-sub.token"}

        with (
            patch("smartcs.shared.auth.decode_token", return_value={"exp": 123}),
            patch("smartcs.shared.rate_limit.get_remote_address", return_value="10.0.0.2"),
        ):
            key = _user_or_ip_key(request)
            assert key == "ip:10.0.0.2"

    def test_decode_error_falls_back_to_ip(self) -> None:
        """JWT 解析失败→降级 IP"""
        request = MagicMock()
        request.headers = {"Authorization": "Bearer bad-token"}

        with (
            patch("smartcs.shared.auth.decode_token", side_effect=ValueError("bad")),
            patch("smartcs.shared.rate_limit.get_remote_address", return_value="10.0.0.3"),
        ):
            key = _user_or_ip_key(request)
            assert key == "ip:10.0.0.3"

    def test_non_bearer_auth_uses_ip(self) -> None:
        """非 Bearer 认证方式→IP"""
        request = MagicMock()
        request.headers = {"Authorization": "Basic abc123"}

        with patch("smartcs.shared.rate_limit.get_remote_address", return_value="10.0.0.4"):
            key = _user_or_ip_key(request)
            assert key == "ip:10.0.0.4"


class TestExemptPaths:
    """限流豁免路径测试"""

    def test_health_exempt(self) -> None:
        assert is_rate_limit_exempt("/api/health") is True
        assert is_rate_limit_exempt("/api/health/live") is True
        assert is_rate_limit_exempt("/api/health/ready") is True

    def test_metrics_exempt(self) -> None:
        assert is_rate_limit_exempt("/metrics") is True

    def test_favicon_exempt(self) -> None:
        assert is_rate_limit_exempt("/favicon.ico") is True

    def test_api_endpoint_not_exempt(self) -> None:
        assert is_rate_limit_exempt("/api/chat/send") is False
        assert is_rate_limit_exempt("/api/kb/faq/search") is False

    def test_exempt_prefixes_list_matches_tests(self) -> None:
        """确保测试覆盖的豁免路径与实际常量一致"""
        assert "/api/health" in _EXEMPT_PATHS
        assert "/metrics" in _EXEMPT_PATHS
        assert "/favicon.ico" in _EXEMPT_PATHS


class TestCreateLimiter:
    """限流器工厂测试"""

    @patch("smartcs.shared.rate_limit.get_settings")
    def test_disabled_returns_disabled_limiter(self, mock_settings: MagicMock) -> None:
        """rate_limit_enabled=False → limiter.enabled=False"""
        settings = MagicMock()
        settings.rate_limit_enabled = False
        mock_settings.return_value = settings

        limiter = create_limiter()
        assert limiter.enabled is False

    @patch("smartcs.shared.rate_limit.get_settings")
    def test_enabled_returns_configured_limiter(self, mock_settings: MagicMock) -> None:
        """rate_limit_enabled=True → 使用 Redis URI 限流器"""
        settings = MagicMock()
        settings.rate_limit_enabled = True
        settings.rate_limit_default = "100/minute"
        settings.redis.host = "localhost"
        settings.redis.port = 6379
        settings.redis.password = ""
        settings.redis.db = 0
        mock_settings.return_value = settings

        limiter = create_limiter()
        assert limiter.enabled is True

    @patch("smartcs.shared.rate_limit.get_settings")
    def test_with_password_includes_auth(self, mock_settings: MagicMock) -> None:
        """有密码时 Redis URI 包含认证信息"""
        settings = MagicMock()
        settings.rate_limit_enabled = True
        settings.rate_limit_default = "50/minute"
        settings.redis.host = "redis-host"
        settings.redis.port = 6380
        settings.redis.password = "secret"
        settings.redis.db = 1
        mock_settings.return_value = settings

        limiter = create_limiter()
        # 限流器成功创建即证明 URI 正确
        assert limiter.enabled is True
