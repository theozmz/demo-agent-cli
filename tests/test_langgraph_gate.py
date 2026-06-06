"""Tests for ComplexityGate — autonomous mode selection."""

import pytest
from harness.langgraph.gate import (
    ComplexityGate,
    ModeSelection,
    create_complexity_gate,
)
from harness.langgraph.complexity import ComplexityTier


class TestComplexityGate:
    """Verify autonomous mode selection based on task complexity."""

    def setup_method(self):
        self.gate = ComplexityGate(enabled=True, confidence_threshold=0.5)

    # ------------------------------------------------------------------
    # Mode selection by complexity tier
    # ------------------------------------------------------------------

    def test_simple_task_stays_native(self):
        """Simple tasks should stay in native standard mode."""
        result = self.gate.assess_and_select(
            "fix a typo in README.md",
            current_engine="native",
            current_mode="standard",
        )
        assert result.engine == "native"
        assert result.mode == "standard"
        assert result.auto_triggered is False

    def test_simple_task_rename(self):
        """Rename tasks are simple — no multi-agent needed."""
        result = self.gate.assess_and_select(
            "rename the getCwd function to get_current_working_directory",
            current_engine="native",
            current_mode="standard",
        )
        assert result.engine == "native"

    def test_integration_task_triggers_pair_coding(self):
        """Integration tasks trigger langgraph pair_coding."""
        result = self.gate.assess_and_select(
            "add a REST API endpoint for user profiles with database integration",
            current_engine="native",
            current_mode="standard",
        )
        assert result.engine == "langgraph"
        assert result.mode == "pair_coding"
        assert result.auto_triggered is True

    def test_architecture_task_triggers_multi_agent(self):
        """Architecture tasks trigger the full multi-agent pipeline."""
        result = self.gate.assess_and_select(
            "design and implement the authentication and authorization system "
            "for our critical payment platform with proper security patterns",
            current_engine="native",
            current_mode="standard",
        )
        assert result.engine == "langgraph"
        assert result.mode == "multi_agent"
        assert result.auto_triggered is True

    def test_refactor_triggers_pair_coding(self):
        """Refactoring is an integration-level task."""
        result = self.gate.assess_and_select(
            "refactor the database layer across multiple modules",
            current_engine="native",
            current_mode="standard",
        )
        assert result.engine == "langgraph"
        assert result.mode == "pair_coding"

    # ------------------------------------------------------------------
    # Confidence threshold
    # ------------------------------------------------------------------

    def test_low_confidence_falls_back(self):
        """When confidence is below threshold, fall back to configured mode."""
        gate = ComplexityGate(enabled=True, confidence_threshold=0.95)
        result = gate.assess_and_select(
            "do something vague and unspecified",
            current_engine="native",
            current_mode="standard",
        )
        # With high threshold, low-confidence assessments fall back
        assert result.auto_triggered is False

    def test_high_confidence_triggers(self):
        """With low threshold, even moderate confidence triggers."""
        gate = ComplexityGate(enabled=True, confidence_threshold=0.4)
        result = gate.assess_and_select(
            "refactor the database schema and API endpoints",
            current_engine="native",
            current_mode="standard",
        )
        # With low threshold, should auto-select
        assert result.auto_triggered is True

    # ------------------------------------------------------------------
    # Disabled gate
    # ------------------------------------------------------------------

    def test_disabled_gate_uses_configured(self):
        """When disabled, the gate should not change anything."""
        gate = ComplexityGate(enabled=False)
        result = gate.assess_and_select(
            "design a new microservices architecture for the platform",
            current_engine="native",
            current_mode="standard",
        )
        assert result.engine == "native"
        assert result.mode == "standard"
        assert result.auto_triggered is False

    # ------------------------------------------------------------------
    # Explicit overrides
    # ------------------------------------------------------------------

    def test_forced_mode_bypasses_gate(self):
        """When user explicitly sets mode, gate should not override."""
        result = self.gate.assess_and_select(
            "design a new architecture",
            current_engine="langgraph",
            current_mode="pair_coding",
            force_mode="pair_coding",
        )
        assert result.mode == "pair_coding"
        assert result.auto_triggered is False

    def test_forced_engine_and_mode_bypasses_gate(self):
        """When both are forced, gate is completely bypassed."""
        result = self.gate.assess_and_select(
            "design a new architecture",
            current_engine="native",
            current_mode="standard",
            force_engine="langgraph",
            force_mode="multi_agent",
        )
        assert result.engine == "langgraph"
        assert result.mode == "multi_agent"
        assert result.auto_triggered is False

    # ------------------------------------------------------------------
    # Quick checks
    # ------------------------------------------------------------------

    def test_is_complex(self):
        """is_complex should return True for integration/architecture tasks."""
        assert self.gate.is_complex("fix typo") is False
        assert self.gate.is_complex("add API endpoint for user search with DB") is True
        assert self.gate.is_complex("design the new auth architecture") is True

    def test_should_use_multi_agent(self):
        """should_use_multi_agent only true for architecture tasks."""
        assert self.gate.should_use_multi_agent("fix typo") is False
        assert self.gate.should_use_multi_agent("add API endpoint") is False
        assert self.gate.should_use_multi_agent("design the new security architecture") is True

    # ------------------------------------------------------------------
    # ModeSelection dataclass
    # ------------------------------------------------------------------

    def test_mode_selection_fields(self):
        from harness.langgraph.complexity import ComplexityAssessment
        assessment = ComplexityAssessment(
            tier=ComplexityTier.ARCHITECTURE,
            confidence=0.9,
            reasoning="clear architecture task",
            recommended_model="expensive",
            estimated_file_count=10,
            estimated_tool_calls=30,
        )
        sel = ModeSelection(
            engine="langgraph",
            mode="multi_agent",
            complexity=assessment,
            reasoning="Complexity: architecture. Auto-selected.",
            auto_triggered=True,
        )
        assert sel.engine == "langgraph"
        assert sel.mode == "multi_agent"
        assert sel.auto_triggered is True
        assert sel.complexity.tier == ComplexityTier.ARCHITECTURE


class TestCreateComplexityGate:
    """Verify factory function from config."""

    def test_default_gate(self):
        gate = create_complexity_gate(config=None)
        assert gate.enabled is True
        assert gate.confidence_threshold == 0.6

    def test_from_config(self):
        from harness.config.config import Config, LoopConfig

        config = Config(
            loop=LoopConfig(
                auto_mode=False,
                auto_mode_threshold=0.8,
                auto_mode_llm_fallback=True,
            )
        )
        gate = create_complexity_gate(config)
        assert gate.enabled is False
        assert gate.confidence_threshold == 0.8
        assert gate.use_llm_fallback is True


class TestTaskComplexityScenarios:
    """End-to-end: verify real-world tasks route correctly."""

    @pytest.mark.parametrize("task,expected_mode", [
        ("fix typo in README", "standard"),
        ("rename variable x to user_count", "standard"),
        ("format code in utils.py", "standard"),
        ("add type hints to the helper module", "standard"),
        ("add a new REST endpoint for order history", "pair_coding"),
        ("integrate payment gateway with order system", "pair_coding"),
        ("refactor auth module across 5 files", "pair_coding"),
        ("migrate from SQLite to PostgreSQL", "pair_coding"),
        ("design and implement OAuth2 authentication flow", "multi_agent"),
        ("build a real-time notification system with websockets", "multi_agent"),
        ("architect the microservices deployment strategy", "multi_agent"),
        ("implement role-based access control across all endpoints", "multi_agent"),
    ])
    def test_routing(self, task, expected_mode):
        gate = ComplexityGate(enabled=True, confidence_threshold=0.5)
        result = gate.assess_and_select(
            task,
            current_engine="native",
            current_mode="standard",
        )
        # Architecture tasks should always trigger multi_agent
        # Integration → pair_coding
        # Simple → standard
        assert result.mode == expected_mode, (
            f"Task '{task}' should route to '{expected_mode}', "
            f"got '{result.mode}' (tier={result.complexity.tier.value}, "
            f"confidence={result.complexity.confidence:.0%})"
        )
