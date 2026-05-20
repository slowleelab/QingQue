"""话后小结生成器

AG_REVIEWING 阶段调用 LLM 自动生成话后小结，供坐席审核确认。
对应设计文档 §3.7 话后小结。
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from smartcs.shared.models import CallSummary, SentimentLabel

if TYPE_CHECKING:
    from smartcs.services.common.llm import LLMClient
    from smartcs.services.common.session import SessionManager

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT = """你是银行信用卡客服的话后小结助手。根据对话记录生成结构化小结，要求：
1. 客户需求：一句话概括客户来电目的
2. 问题分类：从 [账单查询, 交易查询, 额度查询, 分期咨询, 积分查询, 挂失, 投诉, 转人工, 闲聊, 其他] 中选择
3. 解决方案：坐席提供的解决方案摘要
4. 解决状态：已解决 / 部分解决 / 未解决 / 转接
5. 客户情绪：正面 / 中性 / 负面 / 愤怒
6. 关键信息：提取卡号后四位、金额、日期等关键实体（如有）

请严格按以下 JSON 格式输出：
{"customer_demand": "...", "problem_category": "...", "solution_provided": "...", "resolution_status": "...", "sentiment": "...", "key_info": {...}}"""


async def generate_call_summary(
    session_id: str,
    session_manager: SessionManager,
    llm_client: LLMClient | None = None,
) -> CallSummary:
    """生成话后小结

    Args:
        session_id: 会话 ID
        session_manager: 会话管理器
        llm_client: LLM 客户端，None 时返回空小结

    Returns:
        CallSummary 对象
    """
    # 加载对话历史
    turns = await session_manager.get_history(session_id, limit=30)
    if not turns:
        return _empty_summary(session_id)

    # 构造对话文本
    conversation = _format_conversation(turns)

    # LLM 不可用时返回基于模板的小结
    if llm_client is None:
        logger.info("LLM 不可用，返回模板小结: session=%s", session_id)
        return _template_summary(session_id, turns)

    try:
        response = await llm_client.chat(
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": f"对话记录：\n{conversation}"},
            ],
            json_mode=True,
            timeout=5.0,
        )
        return _parse_llm_response(session_id, response)
    except Exception as e:
        logger.warning("LLM 小结生成失败: session=%s error=%s，降级到模板", session_id, e)
        return _template_summary(session_id, turns)


def _format_conversation(turns: list) -> str:
    """格式化对话历史为文本"""
    lines = []
    for turn in turns:
        speaker = {"customer": "客户", "agent": "坐席", "bot": "机器人"}.get(turn.speaker, turn.speaker)
        lines.append(f"[{speaker}] {turn.content}")
    return "\n".join(lines)


def _parse_llm_response(session_id: str, response: str) -> CallSummary:
    """解析 LLM JSON 响应为 CallSummary"""
    import json

    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return _empty_summary(session_id)

    sentiment_map = {
        "正面": SentimentLabel.POSITIVE,
        "中性": SentimentLabel.NEUTRAL,
        "负面": SentimentLabel.NEGATIVE,
        "愤怒": SentimentLabel.ANGRY,
    }

    return CallSummary(
        summary_id=uuid.uuid4().hex,
        session_id=session_id,
        customer_demand=data.get("customer_demand", ""),
        problem_category=data.get("problem_category", "其他"),
        solution_provided=data.get("solution_provided", ""),
        resolution_status=data.get("resolution_status", ""),
        sentiment=sentiment_map.get(data.get("sentiment", ""), SentimentLabel.NEUTRAL),
        key_info=data.get("key_info", {}),
    )


def _template_summary(session_id: str, turns: list) -> CallSummary:
    """基于模板生成小结（LLM 不可用时的降级方案）"""
    customer_turns = [t for t in turns if t.speaker == "customer"]
    demand = customer_turns[0].content[:100] if customer_turns else ""

    return CallSummary(
        summary_id=uuid.uuid4().hex,
        session_id=session_id,
        customer_demand=demand,
        problem_category="其他",
        solution_provided="",
        resolution_status="",
        sentiment=SentimentLabel.NEUTRAL,
    )


def _empty_summary(session_id: str) -> CallSummary:
    """空小结"""
    return CallSummary(
        summary_id=uuid.uuid4().hex,
        session_id=session_id,
    )
