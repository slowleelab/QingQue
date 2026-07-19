"""配置测试"""

from app.config import Settings


def test_settings_defaults():
    """测试默认配置"""
    settings = Settings()
    assert settings.service_name == "knowledge-platform"
    assert settings.database.host == "localhost"
    assert settings.database.port == 5433
    assert settings.elasticsearch.index_prefix == "kp"
    assert settings.elasticsearch.chunks_index == "kp_kb_chunks"
    assert settings.kafka.ingest_topic == "kp.ingest.request"
    assert settings.kafka.dlq_topic == "kp.ingest.dlq"
    assert settings.kafka.max_retries == 3
    assert settings.rag.embedding_dim == 1024
    assert settings.rag.embedding_model_version == "bge-m3-v1"
    assert settings.langfuse.enabled is False


def test_chunks_index_property():
    """测试 ES 索引名拼接"""
    settings = Settings()
    assert settings.elasticsearch.chunks_index == f"{settings.elasticsearch.index_prefix}_kb_chunks"
