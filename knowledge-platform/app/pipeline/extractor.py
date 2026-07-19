"""LLM 结构化抽取器

从文档正文自动抽取：
- 关键词（keywords）
- 摘要（summary）
- 实体（entities: 产品名/金额/日期/监管文号等）
- FAQ 问答对（仅 doc_type=faq 时）

替代人工 YAML frontmatter，符合生产级要求。
使用 OpenAI 兼容 API（vLLM / Ollama），Structured Output 保证输出格式。
Langfuse 追踪 LLM 调用成本/延迟/质量。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)

# Langfuse 实例（可选，enabled=False 时不初始化）
_langfuse = None
_langfuse_enabled = False

try:
    settings = get_settings()
    if settings.langfuse.enabled:
        from langfuse import Langfuse

        _langfuse = Langfuse(
            host=settings.langfuse.host,
            public_key=settings.langfuse.public_key,
            secret_key=settings.langfuse.secret_key,
        )
        _langfuse_enabled = True
except Exception:
    pass


@dataclass
class ExtractionResult:
    """LLM 抽取结果"""

    keywords: list[str] = field(default_factory=list)
    summary: str = ""
    entities: list[dict] = field(default_factory=list)
    faq_pairs: list[dict] = field(default_factory=list)


_SYSTEM_PROMPT = """你是一个银行信用卡知识文档分析专家。请从给定文档正文中抽取结构化信息。

要求：
1. keywords: 提取 5-15 个核心关键词，涵盖产品名、业务类型、关键条件
2. summary: 用 1-3 句话概括文档核心内容
3. entities: 提取命名实体，每项格式 {"type": "产品名|金额|日期|监管文号|费率|其他", "value": "实体值"}
4. faq_pairs: 仅当文档是 FAQ 类型时提取问答对，格式 {"question": "问题", "answer": "答案摘要"}

严格以 JSON 格式输出，不要输出其他内容。"""


class LLMExtractor:
    """LLM 结构化抽取器

    使用 OpenAI 兼容 API，通过 system prompt 约束输出为 JSON。
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.llm.base_url).rstrip("/")
        self._api_key = api_key or settings.llm.api_key
        self._model = model or settings.llm.primary_model
        self._temperature = temperature if temperature is not None else settings.llm.temperature
        self._max_tokens = max_tokens or settings.llm.max_tokens
        self._timeout = timeout or settings.llm.timeout_seconds

    async def extract(self, content: str, doc_type: str = "", title: str = "") -> ExtractionResult:
        """从文档正文抽取结构化信息

        Args:
            content: 文档正文（清洗后）
            doc_type: 文档类型（faq/费率/章程等）
            title: 文档标题

        Returns:
            ExtractionResult 抽取结果
        """
        # 截断超长文本，避免 token 超限
        max_chars = 8000
        truncated = content[:max_chars]
        if len(content) > max_chars:
            truncated += "\n...(文档已截断)"

        user_prompt = f"文档标题: {title}\n文档类型: {doc_type}\n\n文档正文:\n{truncated}"

        # Langfuse 追踪
        trace = None
        generation = None
        if _langfuse_enabled and _langfuse:
            trace = _langfuse.trace(name="llm_extract", metadata={"title": title, "doc_type": doc_type})
            generation = trace.generation(
                name="extract",
                model=self._model,
                input=user_prompt,
            )

        try:
            t0 = time.perf_counter()
            response_text = await self._call_llm(user_prompt)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            result = self._parse_response(response_text)

            if generation:
                generation.end(
                    output=response_text,
                    usage_details={"latency_ms": latency_ms},
                    metadata={"keywords": len(result.keywords), "entities": len(result.entities)},
                )

            logger.info(
                "LLM 抽取完成",
                title=title,
                keywords=len(result.keywords),
                entities=len(result.entities),
                faq=len(result.faq_pairs),
                latency_ms=latency_ms,
            )
            return result
        except Exception:
            if generation:
                generation.end(level="ERROR")
            logger.exception("LLM 抽取失败", title=title)
            return ExtractionResult()

    async def _call_llm(self, user_prompt: str) -> str:
        """调用 LLM API"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": self._temperature,
                    "max_tokens": self._max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_response(text: str) -> ExtractionResult:
        """解析 LLM JSON 响应"""
        # 尝试提取 JSON（LLM 可能包裹在 markdown 代码块中）
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉首尾的 ``` 行
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试找到第一个 { 和最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    logger.warning("LLM 响应解析失败，返回空结果")
                    return ExtractionResult()
            else:
                logger.warning("LLM 响应中未找到 JSON，返回空结果")
                return ExtractionResult()

        return ExtractionResult(
            keywords=data.get("keywords", []),
            summary=data.get("summary", ""),
            entities=data.get("entities", []),
            faq_pairs=data.get("faq_pairs", []),
        )
