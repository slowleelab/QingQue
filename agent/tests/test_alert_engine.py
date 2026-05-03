from __future__ import annotations

import pytest
from smartcs.services.assist.alert_engine import AlertEngine
from smartcs.shared.models import SentimentLabel, AlertLevel, AlertCategory


@pytest.fixture
def engine():
    return AlertEngine()


def test_load_rules_from_memory(engine):
    engine.load_from_memory()
    assert len(engine._rules) > 0


def test_compliance_check_hits_keyword(engine):
    engine.load_from_memory()
    alerts = engine.check_compliance("这是非法套现渠道，包过")
    assert len(alerts) > 0
    assert alerts[0]["level"] != AlertLevel.INFO.value


def test_compliance_check_clean_text(engine):
    engine.load_from_memory()
    alerts = engine.check_compliance("您好，我想查询一下我的账单")
    assert alerts == []


def test_sentiment_alert_angry(engine):
    result = engine.check_sentiment(SentimentLabel.ANGRY)
    assert len(result) > 0
    assert result[0]["category"] == "emotion"


def test_sentiment_alert_neutral(engine):
    result = engine.check_sentiment(SentimentLabel.NEUTRAL)
    assert result == []


def test_trend_escalation(engine):
    history = [
        SentimentLabel.NEUTRAL,
        SentimentLabel.NEGATIVE,
        SentimentLabel.NEGATIVE,
        SentimentLabel.NEGATIVE,
    ]
    result = engine.check_sentiment_trend(history, window=3)
    assert len(result) > 0


def test_trend_no_escalation(engine):
    result = engine.check_sentiment_trend(
        [SentimentLabel.NEUTRAL, SentimentLabel.NEGATIVE],
        window=3,
    )
    assert result == []
