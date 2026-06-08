"""Autonomous task complexity assessment.

Two-pass strategy:
1. Heuristic pass (fast, no LLM): keyword + scope pattern matching
2. LLM pass (when heuristic confidence < threshold): cheap-model classification

Complexity tiers map to model selection:
- SIMPLE → cheap model (Haiku) — formatting, renaming, simple CRUD
- INTEGRATION → default model (Sonnet) — cross-module, API design
- ARCHITECTURE → expensive model (Opus) — design, security, critical paths
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.llm.client import LlmClient

logger = logging.getLogger(__name__)


class ComplexityTier(str, Enum):
    """Task complexity classification tiers."""
    SIMPLE = "simple"           # 1-2 files, straightforward logic
    INTEGRATION = "integration"  # Multiple files, cross-module coordination
    ARCHITECTURE = "architecture"  # Design decisions, security, critical paths


@dataclass
class ComplexityAssessment:
    """Result of complexity assessment for a task."""
    tier: ComplexityTier
    confidence: float            # 0.0 - 1.0
    reasoning: str
    recommended_model: str       # "cheap" | "default" | "expensive"
    estimated_file_count: int
    estimated_tool_calls: int


class ComplexityAssessor:
    """Two-pass complexity assessment for autonomous task routing.

    The heuristic pass uses keyword scoring across three dimensions.
    When confidence is below threshold, an LLM pass provides a more
    accurate classification using a cheap model.

    Usage:
        assessor = ComplexityAssessor()
        result = assessor.assess("implement user authentication")
        # result.tier == ComplexityTier.ARCHITECTURE
        # result.recommended_model == "expensive"
    """

    # Heuristic keyword patterns mapped to tiers
    SIMPLE_PATTERNS: list[str] = [
        r"\b(rename|format|add\s+comment|fix\s+\w+\s+typo|fix\s+typo|update\s+doc)\b",
        r"\b(add\s+test|single\s+function|one\s+file|simple\s+fix)\b",
        r"\b(print|logging|debug\s+log|add\s+type\s+hints?)\b",
        r"\b(basic\s+crud|boilerplate|scaffold)\b",
    ]
    INTEGRATION_PATTERNS: list[str] = [
        r"\b(coordinate|multiple\s+files?|api|interface|endpoint)\b",
        r"\b(refactor|migrate|integrate|cross.module|multi.module)\b",
        r"\b(database|schema|query|orm|model\s+relation)\b",
        r"\b(rest|graphql|websocket|microservice)\b",
        r"\b(translate|port|convert)\b",  # cross-language / cross-platform work
    ]
    ARCHITECTURE_PATTERNS: list[str] = [
        r"\b(design|architecture|security|critical|auth\w+)\b",
        r"\b(data\s+model|schema\s+change|deployment|performance)\b",
        r"\b(concurrency|locking|transaction|race\s+condition)\b",
        r"\b(access\s+control|permission|real.time|notification\s+system)\b",
        r"\b(oauth|sso|encrypt|decrypt|token|jwt|rbac)\b",
        r"\b(role.based|authorization|authentication)\b",
        r"\b(monolith|microservice.architecture|system.design)\b",
    ]

    # Chinese patterns — no \b anchors since CJK chars are not \w in Python re.
    # Substring matching is safe: Chinese task descriptions are dense, and the
    # scoring algorithm's max() + confidence threshold naturally handles noise.
    # Chinese patterns use short, independent keywords rather than compound phrases.
    # CJK text often mixes Chinese with English/code (e.g. "修正 README 中的拼写"),
    # so requiring adjacency (like "修正拼写") misses valid matches. Each keyword
    # stands alone as a signal for its tier.
    CN_SIMPLE_PATTERNS: list[str] = [
        r"重命名|格式化|加注释|修正|错别字|拼写|更新文档",
        r"添加测试|单个函数|单个文件|简单修复|小改动",
        r"打印|日志|调试|类型注解|类型标注",
        r"增删改查|模板代码|脚手架|样板代码|基础",
    ]
    CN_INTEGRATION_PATTERNS: list[str] = [
        r"多个文件|多文件|API|接口|端点|接口开发",
        r"重构|迁移|集成|跨模块|多模块|跨服务",
        r"数据库|数据模型|查询|ORM|模型关系|表结构",
        r"REST|GraphQL|WebSocket|微服务|消息队列",
        r"翻译|移植|转换|跨语言|跨平台",
    ]
    CN_ARCHITECTURE_PATTERNS: list[str] = [
        r"系统设计|架构设计|微服务架构|单体架构|分布式|高可用|容灾",
        r"认证系统|鉴权|权限系统|权限控制|权限管理|访问控制",
        r"数据模型|模式变更|架构变更|部署方案|性能优化|性能调优",
        r"并发|锁|事务|竞态条件|竞争条件|死锁",
        r"实时通知|通知系统|消息推送|消息队列架构",
        r"OAuth|单点登录|SSO|加密|解密|令牌|JWT|RBAC",
        r"基于角色|授权|身份验证|登录系统|关键路径",
        r"安全|架构|设计|认证",
    ]

    # Thresholds
    CONFIDENCE_THRESHOLD: float = 0.7
    DEFAULT_CONFIDENCE: float = 0.5

    def __init__(self, confidence_threshold: float = 0.7,
                 llm_client: "LlmClient | None" = None):
        self.CONFIDENCE_THRESHOLD = confidence_threshold
        self._llm = llm_client
        # Pre-compile regex patterns
        self._simple_re = re.compile(
            "|".join(self.SIMPLE_PATTERNS), re.IGNORECASE
        )
        self._integration_re = re.compile(
            "|".join(self.INTEGRATION_PATTERNS), re.IGNORECASE
        )
        self._architecture_re = re.compile(
            "|".join(self.ARCHITECTURE_PATTERNS), re.IGNORECASE
        )
        self._cn_simple_re = re.compile(
            "|".join(self.CN_SIMPLE_PATTERNS)
        )
        self._cn_integration_re = re.compile(
            "|".join(self.CN_INTEGRATION_PATTERNS)
        )
        self._cn_architecture_re = re.compile(
            "|".join(self.CN_ARCHITECTURE_PATTERNS)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(
        self,
        task: str,
        plan: str = "",
        use_llm: bool = False,
    ) -> ComplexityAssessment:
        """Assess task complexity.

        Args:
            task: The task description to assess.
            plan: Optional broader plan context for the task.
            use_llm: If True, fall back to LLM when heuristic confidence is low.

        Returns:
            ComplexityAssessment with tier, confidence, and model recommendation.
        """
        heuristic = self._heuristic_assess(task, plan)
        if heuristic.confidence >= self.CONFIDENCE_THRESHOLD or not use_llm:
            return heuristic

        if self._llm is None:
            heuristic.reasoning += " (LLM pass skipped: no client configured)"
            return heuristic

        try:
            return self._llm_assess(task, plan, heuristic)
        except Exception as exc:
            logger.warning("LLM complexity assessment failed: %s — using heuristic", exc)
            heuristic.reasoning += f" (LLM pass failed: {exc})"
            return heuristic

    def _llm_assess(
        self, task: str, plan: str, heuristic: ComplexityAssessment
    ) -> ComplexityAssessment:
        """LLM-based assessment — language-agnostic, works for any natural language.

        Called when the heuristic has low confidence. Uses a cheap model to
        classify the task into one of three tiers. The prompt is designed to
        work across languages since the LLM itself is multilingual.
        """
        import asyncio

        prompt = f"""Classify this software engineering task into exactly one complexity tier.

Task: {task}
{f"Context: {plan}" if plan else ""}

Tiers:
- simple: 1-2 files, rename/typo/format, single function, basic CRUD, boilerplate
- integration: multiple files, API/endpoint, refactor, migrate, database, REST/GraphQL
- architecture: design, security, auth/OAuth, concurrency, system architecture, RBAC

Respond with ONLY a JSON object, no other text:
{{"tier": "<tier>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}}"""

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        response = loop.run_until_complete(
            self._llm.generate(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a task complexity classifier. Output only JSON.",
                tools=None,
            )
        )

        text = (response.text or "").strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.split("```")[0].strip()

        data = json.loads(text)
        tier_str = data.get("tier", "integration").lower()
        tier_map = {
            "simple": ComplexityTier.SIMPLE,
            "integration": ComplexityTier.INTEGRATION,
            "architecture": ComplexityTier.ARCHITECTURE,
        }
        tier = tier_map.get(tier_str, ComplexityTier.INTEGRATION)
        confidence = float(data.get("confidence", 0.6))

        return ComplexityAssessment(
            tier=tier,
            confidence=min(max(confidence, 0.0), 1.0),
            reasoning=f"LLM: {data.get('reason', '')} (heuristic was: {heuristic.reasoning})",
            recommended_model=self._model_for_tier(tier),
            estimated_file_count=self._estimate_file_count(tier),
            estimated_tool_calls=self._estimate_tool_calls(tier),
        )

    def assess_batch(
        self,
        tasks: list[str],
        plan: str = "",
    ) -> list[ComplexityAssessment]:
        """Assess complexity for multiple tasks.

        Returns assessments in the same order as input tasks.
        """
        return [self.assess(task, plan) for task in tasks]

    # ------------------------------------------------------------------
    # Heuristic assessment
    # ------------------------------------------------------------------

    def _heuristic_assess(
        self, task: str, plan: str = ""
    ) -> ComplexityAssessment:
        """Keyword + pattern-based heuristic classification."""
        combined = f"{task} {plan}"

        scores = {
            ComplexityTier.SIMPLE: len(self._simple_re.findall(combined))
                                  + len(self._cn_simple_re.findall(combined)),
            ComplexityTier.INTEGRATION: len(self._integration_re.findall(combined))
                                      + len(self._cn_integration_re.findall(combined)),
            ComplexityTier.ARCHITECTURE: len(self._architecture_re.findall(combined))
                                       + len(self._cn_architecture_re.findall(combined)),
        }

        total = sum(scores.values())
        if total == 0:
            # No matches — default to INTEGRATION with low confidence
            return ComplexityAssessment(
                tier=ComplexityTier.INTEGRATION,
                confidence=self.DEFAULT_CONFIDENCE,
                reasoning="No keyword matches; defaulting to integration tier",
                recommended_model=self._model_for_tier(ComplexityTier.INTEGRATION),
                estimated_file_count=3,
                estimated_tool_calls=10,
            )

        tier = max(scores, key=scores.get)  # type: ignore[arg-type]
        confidence = min(scores[tier] / total, 1.0)

        # Boost confidence when one tier dominates
        runner_up = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
        if scores[tier] > runner_up * 2:
            confidence = min(confidence + 0.15, 1.0)

        return ComplexityAssessment(
            tier=tier,
            confidence=confidence,
            reasoning=(
                f"Heuristic scores: simple={scores[ComplexityTier.SIMPLE]}, "
                f"integration={scores[ComplexityTier.INTEGRATION]}, "
                f"architecture={scores[ComplexityTier.ARCHITECTURE]}"
            ),
            recommended_model=self._model_for_tier(tier),
            estimated_file_count=self._estimate_file_count(tier),
            estimated_tool_calls=self._estimate_tool_calls(tier),
        )

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    @staticmethod
    def _model_for_tier(tier: ComplexityTier) -> str:
        """Map complexity tier to recommended model category."""
        mapping = {
            ComplexityTier.SIMPLE: "cheap",
            ComplexityTier.INTEGRATION: "default",
            ComplexityTier.ARCHITECTURE: "expensive",
        }
        return mapping[tier]

    @staticmethod
    def _estimate_file_count(tier: ComplexityTier) -> int:
        """Estimate number of files affected by a task of this tier."""
        return {ComplexityTier.SIMPLE: 2, ComplexityTier.INTEGRATION: 5,
                ComplexityTier.ARCHITECTURE: 10}[tier]

    @staticmethod
    def _estimate_tool_calls(tier: ComplexityTier) -> int:
        """Estimate tool calls needed for a task of this tier."""
        return {ComplexityTier.SIMPLE: 5, ComplexityTier.INTEGRATION: 15,
                ComplexityTier.ARCHITECTURE: 30}[tier]
