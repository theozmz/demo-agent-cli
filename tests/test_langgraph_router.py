"""Tests for LangGraph model router."""

import pytest
from harness.langgraph.router import LangGraphModelRouter
from harness.langgraph.complexity import ComplexityTier


class TestLangGraphModelRouter:
    """Verify model routing by role and complexity."""

    def setup_method(self):
        self.router = LangGraphModelRouter(llm_config=None)

    # ------------------------------------------------------------------
    # Role-based routing
    # ------------------------------------------------------------------

    def test_controller_uses_default(self):
        tier = self.router.tier_for_role("controller")
        assert tier == "default"

    def test_coder_uses_default(self):
        tier = self.router.tier_for_role("coder")
        assert tier == "default"

    def test_reviewer_uses_expensive(self):
        tier = self.router.tier_for_role("reviewer")
        assert tier == "expensive"

    def test_spec_reviewer_uses_expensive(self):
        tier = self.router.tier_for_role("spec_reviewer")
        assert tier == "expensive"

    def test_code_quality_reviewer_uses_expensive(self):
        tier = self.router.tier_for_role("code_quality_reviewer")
        assert tier == "expensive"

    # ------------------------------------------------------------------
    # Complexity-based routing for implementers
    # ------------------------------------------------------------------

    def test_simple_implementer_uses_cheap(self):
        model = self.router.route_for_implementer(ComplexityTier.SIMPLE)
        assert "haiku" in model.lower()

    def test_integration_implementer_uses_default(self):
        model = self.router.route_for_implementer(ComplexityTier.INTEGRATION)
        assert "sonnet" in model.lower()

    def test_architecture_implementer_uses_expensive(self):
        model = self.router.route_for_implementer(ComplexityTier.ARCHITECTURE)
        assert "opus" in model.lower()

    # ------------------------------------------------------------------
    # Unknown role
    # ------------------------------------------------------------------

    def test_unknown_role_uses_default(self):
        tier = self.router.tier_for_role("nonexistent_role")
        assert tier == "default"


class TestLangGraphModelRouterWithConfig:
    """Verify model routing with actual config."""

    def test_with_custom_models(self):
        from harness.config.config import LlmConfig

        config = LlmConfig(
            provider="anthropic",
            model="custom-sonnet",
            fallback_model="custom-haiku",
            expensive_model="custom-opus",
        )
        router = LangGraphModelRouter(llm_config=config)

        assert router.route_for("controller") == "custom-sonnet"
        assert router.route_for("reviewer") == "custom-opus"
        assert router.route_for_implementer(ComplexityTier.SIMPLE) == "custom-haiku"
        assert router.route_for_implementer(ComplexityTier.INTEGRATION) == "custom-sonnet"
        assert router.route_for_implementer(ComplexityTier.ARCHITECTURE) == "custom-opus"
