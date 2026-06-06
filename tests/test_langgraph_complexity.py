"""Tests for autonomous complexity assessment."""

import pytest
from harness.langgraph.complexity import (
    ComplexityAssessor,
    ComplexityTier,
    ComplexityAssessment,
)


class TestComplexityAssessor:
    """Verify the two-pass complexity assessment logic."""

    def setup_method(self):
        self.assessor = ComplexityAssessor()

    # ------------------------------------------------------------------
    # Simple tasks
    # ------------------------------------------------------------------

    def test_simple_rename_task(self):
        result = self.assessor.assess("rename the getCwd function to get_current_working_directory")
        assert result.tier == ComplexityTier.SIMPLE
        assert result.recommended_model == "cheap"

    def test_simple_format_task(self):
        result = self.assessor.assess("format the code in src/utils.py with proper indentation")
        assert result.tier == ComplexityTier.SIMPLE

    def test_simple_fix_typo_task(self):
        result = self.assessor.assess("fix typo in the README documentation")
        assert result.tier == ComplexityTier.SIMPLE

    def test_simple_add_test_task(self):
        result = self.assessor.assess("add test for the login function in tests/test_auth.py")
        assert result.tier == ComplexityTier.SIMPLE

    # ------------------------------------------------------------------
    # Integration tasks
    # ------------------------------------------------------------------

    def test_integration_api_task(self):
        result = self.assessor.assess(
            "add a new REST API endpoint for user profile with database integration"
        )
        assert result.tier == ComplexityTier.INTEGRATION
        assert result.recommended_model == "default"

    def test_integration_refactor_task(self):
        result = self.assessor.assess("refactor the authentication module across multiple files")
        assert result.tier == ComplexityTier.INTEGRATION

    def test_integration_migrate_task(self):
        result = self.assessor.assess("migrate the database schema from v1 to v2")
        assert result.tier == ComplexityTier.INTEGRATION

    # ------------------------------------------------------------------
    # Architecture tasks
    # ------------------------------------------------------------------

    def test_architecture_design_task(self):
        result = self.assessor.assess("design the microservices architecture for the new platform")
        assert result.tier == ComplexityTier.ARCHITECTURE
        assert result.recommended_model == "expensive"

    def test_architecture_security_task(self):
        result = self.assessor.assess(
            "implement authentication and authorization for the critical payment system"
        )
        assert result.tier == ComplexityTier.ARCHITECTURE

    def test_architecture_performance_task(self):
        result = self.assessor.assess(
            "investigate and fix the race condition in the concurrent transaction handler"
        )
        assert result.tier == ComplexityTier.ARCHITECTURE

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def test_confidence_is_between_zero_and_one(self):
        result = self.assessor.assess("some random task description")
        assert 0.0 <= result.confidence <= 1.0

    def test_high_confidence_for_strong_match(self):
        result = self.assessor.assess(
            "rename the variable and format the file and fix typo in comments"
        )
        assert result.confidence > 0.5

    # ------------------------------------------------------------------
    # Unknown / no-match tasks
    # ------------------------------------------------------------------

    def test_default_to_integration_for_unknown(self):
        result = self.assessor.assess("do something vague")
        assert result.tier == ComplexityTier.INTEGRATION
        assert result.confidence == 0.5

    # ------------------------------------------------------------------
    # Batch assessment
    # ------------------------------------------------------------------

    def test_batch_assessment(self):
        tasks = [
            "fix typo in README",
            "add API endpoint for user search",
            "design the new authentication architecture",
        ]
        results = self.assessor.assess_batch(tasks)
        assert len(results) == 3
        assert results[0].tier == ComplexityTier.SIMPLE
        assert results[1].tier == ComplexityTier.INTEGRATION
        assert results[2].tier == ComplexityTier.ARCHITECTURE

    # ------------------------------------------------------------------
    # Model tier mapping
    # ------------------------------------------------------------------

    def test_model_mapping(self):
        assert ComplexityAssessor._model_for_tier(ComplexityTier.SIMPLE) == "cheap"
        assert ComplexityAssessor._model_for_tier(ComplexityTier.INTEGRATION) == "default"
        assert ComplexityAssessor._model_for_tier(ComplexityTier.ARCHITECTURE) == "expensive"

    # ------------------------------------------------------------------
    # Estimation helpers
    # ------------------------------------------------------------------

    def test_file_count_estimation(self):
        assert ComplexityAssessor._estimate_file_count(ComplexityTier.SIMPLE) == 2
        assert ComplexityAssessor._estimate_file_count(ComplexityTier.INTEGRATION) == 5
        assert ComplexityAssessor._estimate_file_count(ComplexityTier.ARCHITECTURE) == 10

    def test_tool_calls_estimation(self):
        assert ComplexityAssessor._estimate_tool_calls(ComplexityTier.SIMPLE) == 5
        assert ComplexityAssessor._estimate_tool_calls(ComplexityTier.INTEGRATION) == 15
        assert ComplexityAssessor._estimate_tool_calls(ComplexityTier.ARCHITECTURE) == 30


class TestComplexityAssessment:
    """Verify the ComplexityAssessment dataclass."""

    def test_fields(self):
        a = ComplexityAssessment(
            tier=ComplexityTier.SIMPLE,
            confidence=0.9,
            reasoning="clear match",
            recommended_model="cheap",
            estimated_file_count=2,
            estimated_tool_calls=5,
        )
        assert a.tier == ComplexityTier.SIMPLE
        assert a.confidence == 0.9
        assert a.recommended_model == "cheap"
