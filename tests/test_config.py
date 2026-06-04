"""Tests for the configuration system."""

import os
import tempfile
from pathlib import Path

from harness.config.config import Config, LlmConfig, LoopConfig


class TestConfig:
    """Test configuration loading and defaults."""

    def test_default_config(self):
        """Default config should have sensible values."""
        config = Config()
        assert config.llm.model == "claude-sonnet-4-6-20250514"
        assert config.llm.provider == "anthropic"
        assert config.loop.max_turns == 30
        assert config.loop.engine == "native"

    def test_load_from_toml(self):
        """Config should load from a TOML file."""
        toml_content = """
[llm]
model = "gpt-4o"
provider = "openai"

[loop]
max_turns = 10
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write(toml_content)
            f.flush()
            config = Config.load(f.name)
        os.unlink(f.name)

        assert config.llm.model == "gpt-4o"
        assert config.llm.provider == "openai"
        assert config.loop.max_turns == 10

    def test_env_override(self):
        """Environment variables should override config."""
        os.environ["HARNESS_MODEL"] = "claude-haiku-3-5"
        try:
            config = Config.load()
            assert config.llm.model == "claude-haiku-3-5"
        finally:
            del os.environ["HARNESS_MODEL"]

    def test_nested_models(self):
        """Nested config models should have correct defaults."""
        config = Config()
        assert config.sandbox.runtime == "docker"
        assert config.observability.backend == "none"
        assert config.repomap.enabled is False
