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

import re
from dataclasses import dataclass, field
from enum import Enum


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
    ]
    ARCHITECTURE_PATTERNS: list[str] = [
        r"\b(design|architecture|security|critical|auth\w+)\b",
        r"\b(data\s+model|schema\s+change|deployment|performance)\b",
        r"\b(concurrency|locking|transaction|race\s+condition)\b",
        r"\b(access\s+control|permission|real.time|notification\s+system)\b",
        r"\b(oauth|sso|encrypt|decrypt|token|jwt|rbac)\b",
    ]

    # Thresholds
    CONFIDENCE_THRESHOLD: float = 0.7
    DEFAULT_CONFIDENCE: float = 0.5

    def __init__(self, confidence_threshold: float = 0.7):
        self.CONFIDENCE_THRESHOLD = confidence_threshold
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

        # LLM pass would go here — requires LlmClient injection.
        # For now, return heuristic with a note.
        heuristic.reasoning += " (LLM pass skipped: no client configured)"
        return heuristic

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
            ComplexityTier.SIMPLE: len(self._simple_re.findall(combined)),
            ComplexityTier.INTEGRATION: len(self._integration_re.findall(combined)),
            ComplexityTier.ARCHITECTURE: len(self._architecture_re.findall(combined)),
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
