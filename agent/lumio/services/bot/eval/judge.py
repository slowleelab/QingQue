"""B3: 评估基础设施 — Golden Set + Qwen2.5-72B Judge + CI Gate

3 大组件:
1. Golden Set: 100 条典型客服场景, 含标准答案
2. Judge: Qwen2.5-72B 自托管 LLM-as-Judge, 5 维度打分
3. CI Gate: GitHub Actions, PR 触发, 平均分 < 4.0 阻止合并

使用方式:
- 离线评估: python -m tests.eval.run_regression
- PR 集成: GitHub Actions eval-regression job
- 单条评估: tests.eval.judge.score_response(question, response, expected)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from lumio.shared.logger import get_logger
from lumio.shared.metrics import EVAL_JUDGE_SCORE, EVAL_REGRESSION_PASS_RATE

logger = get_logger(__name__)


class JudgeDimension(str, Enum):
    """评估维度 (1-5 分)."""

    RELEVANCE = "relevance"  # 答复是否切题
    ACCURACY = "accuracy"  # 信息是否准确
    TONE = "tone"  # 语气是否专业
    COMPLIANCE = "compliance"  # 合规性 (无敏感词, 无违规承诺)
    CARD_CORRECTNESS = "card_correctness"  # 卡片渲染是否正确 (A2UI)


@dataclass
class JudgeResult:
    """单条评估结果."""

    question: str
    response: str
    scores: dict[str, float] = field(default_factory=dict)
    average_score: float = 0.0
    reasoning: str = ""
    passed: bool = False
    timestamp: float = 0.0
    degraded: bool = False  # True = 启发式评分, 数据不可靠

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "response": self.response[:200],
            "scores": self.scores,
            "average_score": self.average_score,
            "passed": self.passed,
            "reasoning": self.reasoning[:300],
            "timestamp": self.timestamp,
        }


# ── Judge prompt 模板 (Qwen2.5-72B 友好) ──

_JUDGE_SYSTEM_PROMPT = """你是银行信用卡客服 AI 答复质量评估专家. 你的任务是给 LLM 答复打分 (1-5 分).

## 评分维度

1. **relevance (相关性, 1-5)**: 答复是否切题, 是否直接回答了客户问题
   - 5: 完全切题, 直接回答
   - 3: 部分切题, 略有偏离
   - 1: 完全不切题

2. **accuracy (准确性, 1-5)**: 信息是否准确 (金额/利率/政策/产品)
   - 5: 100% 准确
   - 3: 有小错但不影响理解
   - 1: 严重错误

3. **tone (语气, 1-5)**: 是否专业/礼貌/有同理心
   - 5: 完美
   - 3: 平淡
   - 1: 不礼貌或机械

4. **compliance (合规性, 1-5)**: 是否含违规内容 (敏感词/承诺/红线话术)
   - 5: 完全合规
   - 3: 边缘合规
   - 1: 违规 (含银保监/媒体/承诺等)

5. **card_correctness (卡片正确性, 1-5)**: A2UI 卡片是否正确 (类型/字段/数值)
   - 5: 卡片完全正确
   - 3: 卡片有小错
   - 1: 卡片错误或缺失

## 输出格式 (严格 JSON)

```json
{
  "relevance": 4,
  "accuracy": 5,
  "tone": 4,
  "compliance": 5,
  "card_correctness": 3,
  "reasoning": "答复切题, 信息准确, 语气专业. 但账单卡片金额字段错误."
}
```

不要输出其他内容, 仅 JSON."""

_JUDGE_USER_TEMPLATE = """## 客户问题
{question}

## LLM 答复
{response}

## 期望要点 (Golden Set 提供)
{expected_keywords}

## 评估

请按 5 维度打分, 输出 JSON."""


class LLMJudge:
    """LLM-as-Judge 评估器 (Qwen2.5-72B 自托管).

    设计原则:
    - Judge 模型与被评估模型分离 (避免 self-enhancement bias)
    - 单次评估 5 维度并行 (1 次 LLM 调用, JSON 输出)
    - 失败重试 1 次, 仍失败用启发式 fallback
    """

    def __init__(self, judge_model: str = "qwen2.5:72b", base_url: str | None = None) -> None:
        self.judge_model = judge_model
        self._base_url = base_url or "http://localhost:11434/v1"
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(base_url=self._base_url, api_key="ollama", timeout=60.0)
        except ImportError:
            logger.warning("openai SDK 未安装, Judge 不可用")

    async def score_response(
        self,
        question: str,
        response: str,
        expected_keywords: list[str] | None = None,
    ) -> JudgeResult:
        """对单条 LLM 答复打分."""
        result = JudgeResult(question=question, response=response, timestamp=time.time())

        if self._client is None:
            # Fallback: 启发式评分
            return self._heuristic_score(question, response, expected_keywords or [])

        keywords_str = ", ".join(expected_keywords) if expected_keywords else "(无)"
        user_prompt = _JUDGE_USER_TEMPLATE.format(
            question=question, response=response, expected_keywords=keywords_str
        )

        try:
            api_result = await self._client.chat.completions.create(
                model=self.judge_model,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,  # 低温度, 保证评分稳定
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            content = api_result.choices[0].message.content or "{}"
            scores_data = json.loads(content)

            for dim in JudgeDimension:
                score = float(scores_data.get(dim.value, 3.0))
                score = max(1.0, min(5.0, score))
                result.scores[dim.value] = score
                EVAL_JUDGE_SCORE.labels(dimension=dim.value, model=self.judge_model).observe(score)

            result.average_score = sum(result.scores.values()) / len(result.scores)
            result.reasoning = scores_data.get("reasoning", "")
            result.passed = result.average_score >= 4.0

            logger.debug(
                "Judge 评分: question=%s avg=%.2f passed=%s",
                question[:50],
                result.average_score,
                result.passed,
            )
            return result

        except Exception as exc:
            logger.warning("Judge LLM 调用失败, 降级到启发式: %s", exc)
            return self._heuristic_score(question, response, expected_keywords or [])

    def _heuristic_score(
        self, question: str, response: str, expected_keywords: list[str]
    ) -> JudgeResult:
        """启发式评分 (LLM 不可用时降级).

        中文用子串匹配而非 set(字符) — 避免把每字当 1 词导致覆盖率虚高.
        失败时评分保守, 标记 degraded=True 提示数据可能不准确.
        """
        result = JudgeResult(question=question, response=response, timestamp=time.time())
        result.degraded = True  # 启发式评分, 数据不可靠

        # 相关性: 子串匹配 (中文不能用 set(字符))
        if expected_keywords:
            covered = sum(1 for kw in expected_keywords if kw in response)
            relevance = min(5.0, 1.0 + (covered / max(1, len(expected_keywords))) * 4.0)
        else:
            # 无关键词: 用 2-gram 字符共现 (避免单字误判)
            q_2grams = {question[i : i + 2] for i in range(len(question) - 1)}
            r_2grams = {response[i : i + 2] for i in range(len(response) - 1)}
            if q_2grams:
                overlap = len(q_2grams & r_2grams) / len(q_2grams)
                relevance = min(5.0, 1.0 + overlap * 4.0)
            else:
                relevance = 2.5  # 启发式无信号, 给中低分

        # 准确性: 启发式 (无法 LLM 验证, 给保守中分)
        accuracy = 3.0

        # 语气: 检查礼貌词
        polite_markers = ["您好", "请", "您", "谢谢", "感谢", "祝您", "请问"]
        tone = min(5.0, 2.0 + sum(0.5 for m in polite_markers if m in response))

        # 合规性: 检查红线
        forbidden = ["银保监", "投诉到", "保证", "一定", "我承诺"]
        compliance = 5.0 - sum(1.0 for f in forbidden if f in response)
        compliance = max(1.0, compliance)

        # 卡片正确性: 简单 JSON 检测
        card_correctness = 4.0 if "{" in response and "}" in response else 3.5

        result.scores = {
            "relevance": relevance,
            "accuracy": accuracy,
            "tone": tone,
            "compliance": compliance,
            "card_correctness": card_correctness,
        }
        result.average_score = sum(result.scores.values()) / len(result.scores)
        result.passed = result.average_score >= 4.0
        result.reasoning = "启发式评分 (LLM Judge 不可用)"
        return result


# ── Golden Set 加载器 ──

class GoldenSet:
    """金标测试集 (100 条典型场景).

    数据格式: tests/eval/golden_set_v1.jsonl
    每行一个 JSON:
    {
      "id": "G001",
      "category": "knowledge|business|chitchat|complaint",
      "question": "客户问题",
      "expected_intent": "BILL_INQUIRY",
      "expected_keywords": ["账单", "金额", "到期"],
      "expected_card_type": "bill_summary",
      "difficulty": "easy|medium|hard",
    }
    """

    def __init__(self, golden_set_path: str | None = None) -> None:
        # 优先用绝对路径 (在 lumio 包内), 然后 fallback 到 tests/eval
        default_paths = [
            Path(__file__).parent / "data" / "golden_set_v1.jsonl",
            Path("tests/eval/golden_set_v1.jsonl"),
            Path("agent/tests/eval/golden_set_v1.jsonl"),
        ]
        if golden_set_path is None:
            for p in default_paths:
                if p.exists():
                    golden_set_path = str(p)
                    break
            else:
                golden_set_path = str(default_paths[0])
        self.path = golden_set_path
        self._items: list[dict[str, Any]] = []
        self._loaded = False

    def load(self) -> list[dict[str, Any]]:
        if self._loaded:
            return self._items
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._items.append(json.loads(line))
            self._loaded = True
            logger.info("Golden Set 加载: %d 条 (path=%s)", len(self._items), self.path)
        except FileNotFoundError:
            logger.warning("Golden Set 文件不存在: %s, 使用内置示例", self.path)
            self._items = self._builtin_examples()
            self._loaded = True
        return self._items

    def _builtin_examples(self) -> list[dict[str, Any]]:
        """内置示例 (Golden Set 文件缺失时使用)."""
        return [
            {
                "id": "G001",
                "category": "knowledge",
                "question": "信用卡账单分期可以分几期?",
                "expected_intent": "BILL_INSTALLMENT",
                "expected_keywords": ["3期", "6期", "12期", "手续费"],
                "expected_card_type": None,
                "difficulty": "easy",
            },
            {
                "id": "G002",
                "category": "business",
                "question": "我想把我的信用卡额度调高到 5 万",
                "expected_intent": "CREDIT_LIMIT_ADJUST",
                "expected_keywords": ["调额", "审批", "5万", "工作日"],
                "expected_card_type": "credit_adjustment",
                "difficulty": "medium",
            },
            {
                "id": "G003",
                "category": "knowledge",
                "question": "信用卡积分怎么兑换?",
                "expected_intent": "POINTS_INQUIRY",
                "expected_keywords": ["积分", "兑换", "商城"],
                "expected_card_type": None,
                "difficulty": "easy",
            },
            {
                "id": "G004",
                "category": "complaint",
                "question": "你们的服务太差了, 我要投诉!",
                "expected_intent": "COMPLAINT",
                "expected_keywords": ["抱歉", "理解", "处理"],
                "expected_card_type": "complaint_ticket",
                "difficulty": "medium",
            },
            {
                "id": "G005",
                "category": "knowledge",
                "question": "信用卡免息期是多少天?",
                "expected_intent": "INTEREST_FREE_PERIOD",
                "expected_keywords": ["免息期", "20天", "50天"],
                "expected_card_type": None,
                "difficulty": "easy",
            },
        ]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


# ── 回归测试运行器 ──

async def run_regression(
    judge: LLMJudge,
    agent_runner: Any,
    golden_set: GoldenSet | None = None,
    pass_threshold: float = 4.0,
    version: str = "v1",
) -> dict[str, Any]:
    """跑 Golden Set 回归测试.

    Args:
        judge: LLMJudge 实例
        agent_runner: 接收 question 字符串, 返回 LLM 答复 (Bot Agent 入口)
        golden_set: GoldenSet 实例
        pass_threshold: 平均分阈值, < 此值则 CI 失败

    Returns:
        {
            "total": int,
            "passed": int,
            "pass_rate": float,
            "avg_score": float,
            "by_dimension": {dim: avg},
            "failed_cases": list[dict],
        }
    """
    gs = golden_set or GoldenSet()
    items = gs.load()

    results: list[JudgeResult] = []
    for item in items:
        question = item["question"]
        try:
            response = await agent_runner(question)
        except Exception as exc:
            logger.warning("Agent 运行失败: question=%s err=%s", question[:50], exc)
            response = f"[ERROR] {exc}"

        result = await judge.score_response(
            question=question,
            response=response,
            expected_keywords=item.get("expected_keywords", []),
        )
        result.passed = result.average_score >= pass_threshold
        results.append(result)

    # 聚合
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = passed / max(1, total)
    avg_score = sum(r.average_score for r in results) / max(1, total)

    by_dim: dict[str, float] = {}
    for dim in JudgeDimension:
        scores = [r.scores.get(dim.value, 0.0) for r in results]
        by_dim[dim.value] = sum(scores) / max(1, len(scores))

    EVAL_REGRESSION_PASS_RATE.labels(golden_set_version=version).set(pass_rate)

    failed_cases = [r.to_dict() for r in results if not r.passed]

    summary = {
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "avg_score": avg_score,
        "by_dimension": by_dim,
        "failed_cases": failed_cases[:10],  # 只保留前 10 个失败案例
        "version": version,
    }

    logger.info(
        "回归测试: %d/%d 通过, 平均分 %.2f, 通过率 %.1f%%",
        passed,
        total,
        avg_score,
        pass_rate * 100,
    )

    return summary
