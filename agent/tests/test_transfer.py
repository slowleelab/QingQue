"""转人工判断逻辑单元测试"""

from __future__ import annotations

from lumio.services.common.transfer import TransferChecker
from lumio.shared.models import (
    IntentLabel,
    IntentResult,
    SentimentLabel,
    SessionState,
    TransferTriggerLevel,
)


def _make_session(**overrides) -> SessionState:
    """构造测试用 SessionState"""
    defaults = {
        "session_id": "test-session",
        "low_confidence_streak": 0,
        "confidence_history": [],
    }
    defaults.update(overrides)
    return SessionState(**defaults)


# ── L1 关键词触发 ──


def test_l1_transfer_keyword() -> None:
    """命中转人工关键词应触发 L1"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.5)

    triggered, level, reason = checker.check("我要转人工", intent)
    assert triggered is True
    assert level == TransferTriggerLevel.L1
    assert "KEYWORD_HIT" in reason or "转人工" in reason


def test_l1_sensitive_keyword() -> None:
    """命中敏感词应触发 L1"""
    checker = TransferChecker(sensitive_keywords=["炸弹", "自杀"])
    intent = IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.5)

    triggered, level, reason = checker.check("炸弹", intent)
    assert triggered is True
    assert level == TransferTriggerLevel.L1
    assert "SENSITIVE_HIT" in reason


def test_l1_no_trigger() -> None:
    """不命中关键词不应触发 L1"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.9)

    triggered, level, reason = checker.check("查账单", intent)
    assert triggered is False
    assert level is None


# ── L2 语义触发 ──


def test_l2_complaint_intent() -> None:
    """投诉意图应触发 L2"""
    # 使用不含 L1 关键词的文本，避免 L1 先命中
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.COMPLAINT, primary_confidence=0.9)

    triggered, level, reason = checker.check("你们服务太差了", intent)
    assert triggered is True
    assert level == TransferTriggerLevel.L2
    assert "COMPLAINT" in reason


def test_l2_transfer_agent_intent() -> None:
    """transfer_agent 意图应触发 L2"""
    # 使用不含 L1 关键词的文本
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.TRANSFER_AGENT, primary_confidence=0.9)

    triggered, level, reason = checker.check("帮我接一下客服", intent)
    assert triggered is True
    assert level == TransferTriggerLevel.L2


def test_l2_negative_sentiment_high_confidence() -> None:
    """负面情感 + 高置信度应触发 L2"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.85)

    triggered, level, reason = checker.check("账单不对", intent, sentiment=SentimentLabel.NEGATIVE)
    assert triggered is True
    assert level == TransferTriggerLevel.L2


def test_l2_negative_sentiment_low_confidence_no_trigger() -> None:
    """负面情感 + 低置信度不应触发 L2"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.5)

    triggered, level, reason = checker.check("这个怎么回事", intent, sentiment=SentimentLabel.NEGATIVE)
    assert triggered is False


# ── L3 累计触发 ──


def test_l3_low_confidence_streak() -> None:
    """连续低置信度应触发 L3"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.3)
    session = _make_session(low_confidence_streak=3)

    triggered, level, reason = checker.check("随便什么", intent, session=session)
    assert triggered is True
    assert level == TransferTriggerLevel.L3
    assert "LOW_CONFIDENCE_STREAK" in reason


def test_l3_repeated_fallback() -> None:
    """最近5轮中3轮低置信度应触发 L3"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.2)
    session = _make_session(confidence_history=[0.8, 0.1, 0.2, 0.9, 0.15])

    triggered, level, reason = checker.check("还不懂", intent, session=session)
    assert triggered is True
    assert level == TransferTriggerLevel.L3


def test_l3_no_trigger_short_history() -> None:
    """历史不足5轮不应触发 L3"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.2)
    session = _make_session(confidence_history=[0.1, 0.2])

    triggered, level, reason = checker.check("不懂", intent, session=session)
    assert triggered is False


# ── 优先级 ──


def test_l1_takes_priority_over_l2() -> None:
    """L1 应优先于 L2"""
    checker = TransferChecker(sensitive_keywords=["投诉"])
    intent = IntentResult(primary_intent=IntentLabel.COMPLAINT, primary_confidence=0.9)

    triggered, level, reason = checker.check("我要投诉", intent)
    assert triggered is True
    # L1 命中敏感词"投诉"，应在 L2 之前
    assert level == TransferTriggerLevel.L1


# ── 无触发 ──


def test_no_transfer_normal_conversation() -> None:
    """正常对话不应触发转人工"""
    checker = TransferChecker()
    intent = IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.9)
    session = _make_session(low_confidence_streak=0, confidence_history=[0.9, 0.8, 0.85])

    triggered, level, reason = checker.check("查一下账单", intent, sentiment=SentimentLabel.NEUTRAL, session=session)
    assert triggered is False


# ── P3-8: config 路径应相对源文件, 而非 cwd ──


def test_p3_8_config_path_uses_source_file_not_cwd(tmp_path, monkeypatch) -> None:
    """P3-8 整改: 改用 __file__ 相对路径后, 即便 cwd 切到任意位置也应能加载 config.

    修复前: Path("config/transfer_keywords.txt") 在 /tmp/ 起 uvicorn 会静默 fail,
    回退到 _DEFAULT_TRANSFER_KEYWORDS, SRE 改了配置文件但生产不生效.
    """
    # 切到任意 cwd
    monkeypatch.chdir(tmp_path)
    # 改 LUMIO_ENVIRONMENT 为 development 避免 production 模式禁测试
    monkeypatch.setenv("LUMIO_ENVIRONMENT", "development")
    monkeypatch.setenv("LUMIO_JWT_SECRET", "x" * 32)

    # 清除 lru_cache (config 缓存)
    from lumio.shared.config import get_settings

    get_settings.cache_clear()

    checker = TransferChecker()
    # 配置文件独有 '找领导' (默认 _DEFAULT_TRANSFER_KEYWORDS 不含)
    # 如果走了相对源文件路径, '找领导' 应触发转人工
    # 如果走了 cwd 相对 (broken), 应 fallback 到默认列表, 不会触发
    assert "找领导" in checker._transfer_keywords, f"配置文件未加载, 当前关键词: {sorted(checker._transfer_keywords)}"

    intent = IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.9)
    session = _make_session()
    triggered, _, _ = checker.check("我要找领导", intent, sentiment=SentimentLabel.NEUTRAL, session=session)
    assert triggered is True, "配置文件独有的 '找领导' 应触发 L1 转人工"
