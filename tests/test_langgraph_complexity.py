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


# ===================================================================
# TestChineseComplexity — 中文输入复杂度路由
# ===================================================================
class TestChineseComplexity:
    @pytest.fixture
    def assessor(self):
        return ComplexityAssessor()

    def test_cn_simple_rename(self, assessor):
        result = assessor.assess("重命名 getCwd 函数为 getCurrentWorkingDirectory")
        assert result.tier == ComplexityTier.SIMPLE
        assert result.recommended_model == "cheap"

    def test_cn_simple_fix_typo(self, assessor):
        result = assessor.assess("修正 README 中的拼写错误")
        assert result.tier == ComplexityTier.SIMPLE

    def test_cn_simple_add_type_hints(self, assessor):
        result = assessor.assess("给 utils.py 添加类型注解")
        assert result.tier == ComplexityTier.SIMPLE

    def test_cn_simple_single_function(self, assessor):
        result = assessor.assess("写一个判断质数的函数")
        # No CN keyword match → defaults to INTEGRATION
        assert result.confidence <= 0.55

    def test_cn_integration_api(self, assessor):
        result = assessor.assess("添加用户注册的 REST API 端点，连接数据库")
        assert result.tier == ComplexityTier.INTEGRATION
        assert result.recommended_model == "default"

    def test_cn_integration_refactor(self, assessor):
        result = assessor.assess("跨多个模块重构认证逻辑")
        assert result.tier == ComplexityTier.INTEGRATION

    def test_cn_integration_migrate(self, assessor):
        result = assessor.assess("将数据库查询从原始 SQL 迁移到 ORM")
        assert result.tier == ComplexityTier.INTEGRATION

    def test_cn_architecture_design(self, assessor):
        result = assessor.assess("设计微服务架构并实现用户认证系统")
        assert result.tier == ComplexityTier.ARCHITECTURE
        assert result.recommended_model == "expensive"

    def test_cn_architecture_rbac(self, assessor):
        result = assessor.assess("实现基于角色的访问控制系统（RBAC）")
        assert result.tier == ComplexityTier.ARCHITECTURE

    def test_cn_architecture_oauth(self, assessor):
        result = assessor.assess("添加 OAuth 2.0 认证和 JWT 令牌刷新机制")
        assert result.tier == ComplexityTier.ARCHITECTURE

    def test_cn_architecture_security(self, assessor):
        result = assessor.assess("设计高可用分布式系统架构，包含容灾和性能优化方案")
        assert result.tier == ComplexityTier.ARCHITECTURE

    def test_mixed_lang_refactor_auth(self, assessor):
        result = assessor.assess("重构 authentication 模块并添加 OAuth 2.0 认证")
        assert result.tier == ComplexityTier.ARCHITECTURE

    def test_cn_no_match_defaults_integration(self, assessor):
        result = assessor.assess("做一些事情")
        assert result.tier == ComplexityTier.INTEGRATION
        assert result.confidence == 0.5

    def test_cn_confidence_between_zero_and_one(self, assessor):
        result = assessor.assess("重命名函数并修正文档中的拼写错误")
        assert 0.0 <= result.confidence <= 1.0
