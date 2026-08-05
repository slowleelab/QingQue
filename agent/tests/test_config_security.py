"""P0-2 整改测试: JWT 占位密钥在所有环境都被拦截 (prod 强拒, dev 警)

覆盖 agent/lumio/shared/config.py:_validate_production_security 的 P0-2 行为:
- prod + 默认占位密钥 → 抛 ValueError
- prod + 短密钥 (< 32) → 抛 ValueError
- prod + 正常密钥 (>= 32) → 启动成功
- dev + 占位密钥 → 不抛 (兼容 dev flow), 但日志 WARNING
- dev + <CHANGE_ME> 占位符 → 同样不抛但 WARNING
"""

from __future__ import annotations

import logging

import pytest

from lumio.shared.config import Settings


class TestJWTValidator:
    """P0-2: JWT 占位密钥永远不能被静默接受"""

    def test_prod_default_secret_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """prod 环境 + 历史 dev 默认密钥 → 启动失败 (P0-2 核心)"""
        monkeypatch.setenv("LUMIO_ENVIRONMENT", "production")
        monkeypatch.setenv("LUMIO_JWT_SECRET", "lumio-dev-secret-change-in-production")
        with pytest.raises(Exception, match="禁止占位密钥|生产环境必须设置"):
            Settings()

    def test_prod_short_secret_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """prod + 弱密钥 (< 32 字符) → 启动失败"""
        monkeypatch.setenv("LUMIO_ENVIRONMENT", "production")
        monkeypatch.setenv("LUMIO_JWT_SECRET", "too_short")
        with pytest.raises(Exception, match="长度必须 >= 32"):
            Settings()

    def test_prod_valid_secret_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """prod + 32+ 字符密钥 + 全部外部凭据 → 启动成功"""
        monkeypatch.setenv("LUMIO_ENVIRONMENT", "production")
        monkeypatch.setenv("LUMIO_JWT_SECRET", "a" * 32)
        # P0-5 第三轮修复: 生产环境还要求 LLM/MinIO/ES/Redis 凭据非默认
        monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
        monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
        monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
        monkeypatch.setenv("ES_USERNAME", "es-user")
        monkeypatch.setenv("ES_PASSWORD", "es-pass")
        monkeypatch.setenv("REDIS_PASSWORD", "redis-pass")
        cfg = Settings()
        assert cfg.environment == "production"
        assert cfg.jwt_secret == "a" * 32

    def test_dev_default_secret_warns_not_blocks(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """dev + 占位密钥 → 不抛 (兼容 dev flow), 但日志 WARNING"""
        monkeypatch.setenv("LUMIO_ENVIRONMENT", "development")
        monkeypatch.setenv("LUMIO_JWT_SECRET", "lumio-dev-secret-change-in-production")
        with caplog.at_level(logging.WARNING, logger="lumio.shared.config"):
            cfg = Settings()  # 不应抛
        assert cfg.environment == "development"
        # WARNING 包含 "占位"
        assert any("占位" in rec.message for rec in caplog.records), (
            f"应记录占位密钥 WARNING, 实际日志: {[r.message for r in caplog.records]}"
        )

    def test_dev_change_me_placeholder_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """dev + P0-1 新增的 <CHANGE_ME> 占位符 → 同样不抛但 WARNING"""
        monkeypatch.setenv("LUMIO_ENVIRONMENT", "development")
        monkeypatch.setenv("LUMIO_JWT_SECRET", "<CHANGE_ME>")
        with caplog.at_level(logging.WARNING, logger="lumio.shared.config"):
            cfg = Settings()
        assert cfg.environment == "development"
        assert any("占位" in rec.message for rec in caplog.records)

    def test_dev_short_secret_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dev + 短但非占位的密钥 → 启动成功 (长度检查仅在 prod 强制)"""
        monkeypatch.setenv("LUMIO_ENVIRONMENT", "development")
        monkeypatch.setenv("LUMIO_JWT_SECRET", "dev-only-short-but-not-placeholder")
        cfg = Settings()
        assert cfg.environment == "development"
