"""Model routing based on agent role and task complexity.

Maps each agent role to the appropriate model tier:
- Controller: default (Sonnet) — planning and coordination
- Implementers: complexity-based — cheap for simple, default for integration
- Reviewers: ALWAYS expensive (Opus) — defect cost > token cost
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness.langgraph.complexity import ComplexityTier

if TYPE_CHECKING:
    from harness.config.config import LlmConfig


class LangGraphModelRouter:
    """Routes model selection by agent role and task complexity.

    Key principle: Reviewers always use the most capable model.
    The cost of a missed defect far exceeds the token cost of review.

    Usage:
        router = LangGraphModelRouter(llm_config)
        model = router.route_for("spec_reviewer")
        model = router.route_for_implementer(ComplexityTier.INTEGRATION)
    """

    # Role → model tier mapping
    ROLE_MODEL_MAP: dict[str, str] = {
        "controller": "default",
        "coder": "default",
        "reviewer": "expensive",
        "spec_reviewer": "expensive",
        "code_quality_reviewer": "expensive",
    }

    # Complexity tier → model tier for implementers
    COMPLEXITY_MODEL_MAP: dict[ComplexityTier, str] = {
        ComplexityTier.SIMPLE: "cheap",
        ComplexityTier.INTEGRATION: "default",
        ComplexityTier.ARCHITECTURE: "expensive",
    }

    def __init__(self, llm_config: "LlmConfig | None" = None):
        self._llm_config = llm_config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route_for(self, role: str) -> str:
        """Get the model name for a given agent role.

        Args:
            role: One of "controller", "coder", "reviewer",
                  "spec_reviewer", "code_quality_reviewer".

        Returns:
            The concrete model name to use (e.g., "claude-sonnet-4-6-20250514").
        """
        tier = self.ROLE_MODEL_MAP.get(role, "default")
        return self._resolve_model(tier)

    def route_for_implementer(self, complexity: ComplexityTier) -> str:
        """Get the model name for an implementer based on task complexity.

        Args:
            complexity: The assessed complexity tier.

        Returns:
            The concrete model name to use.
        """
        tier = self.COMPLEXITY_MODEL_MAP.get(complexity, "default")
        return self._resolve_model(tier)

    def tier_for_role(self, role: str) -> str:
        """Get the model tier (not concrete model) for a role."""
        return self.ROLE_MODEL_MAP.get(role, "default")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_model(self, tier: str) -> str:
        """Resolve a model tier to a concrete model name.

        Uses LlmConfig when available; falls back to sensible defaults.
        """
        if self._llm_config is None:
            return self._default_model(tier)

        match tier:
            case "cheap":
                return self._llm_config.fallback_model or "claude-haiku-3-5-20251001"
            case "default":
                return self._llm_config.model or "claude-sonnet-4-6-20250514"
            case "expensive":
                return (
                    getattr(self._llm_config, "expensive_model", "")
                    or self._llm_config.model
                    or "claude-opus-4-8-20250514"
                )
        return self._llm_config.model or "claude-sonnet-4-6-20250514"

    @staticmethod
    def _default_model(tier: str) -> str:
        """Fallback model names when no LlmConfig is available."""
        return {
            "cheap": "claude-haiku-3-5-20251001",
            "default": "claude-sonnet-4-6-20250514",
            "expensive": "claude-opus-4-8-20250514",
        }.get(tier, "claude-sonnet-4-6-20250514")
