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


class TestLocalConfig:
    """Test harness.local.toml deep-merge behaviour."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_toml(dir_: Path, name: str, content: str) -> Path:
        p = dir_ / name
        p.write_text(content, encoding="utf-8")
        return p

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------

    def test_local_config_merges(self, tmp_path: Path):
        """harness.local.toml values override the matching keys in the base file."""
        self._write_toml(tmp_path, "harness.toml", """
[llm]
model = "base-model"
provider = "base-provider"
api_key = ""

[loop]
max_turns = 10
""")
        self._write_toml(tmp_path, "harness.local.toml", """
[llm]
model = "local-model"
api_key = "sk-local-secret"
""")

        config = Config.load(str(tmp_path / "harness.toml"))

        # overridden by local
        assert config.llm.model == "local-model"
        assert config.llm.api_key == "sk-local-secret"
        # NOT in local — keeps base value
        assert config.llm.provider == "base-provider"
        # section NOT in local — untouched
        assert config.loop.max_turns == 10

    def test_local_config_adds_new_section(self, tmp_path: Path):
        """A section that only exists in the local file is added."""
        self._write_toml(tmp_path, "harness.toml", """
[loop]
max_turns = 5
""")
        self._write_toml(tmp_path, "harness.local.toml", """
[llm]
model = "local-only-model"
provider = "local-provider"
""")

        config = Config.load(str(tmp_path / "harness.toml"))

        assert config.llm.model == "local-only-model"
        assert config.llm.provider == "local-provider"
        assert config.loop.max_turns == 5

    def test_local_config_partial_override(self, tmp_path: Path):
        """A local file that only sets api_key leaves other [llm] keys alone."""
        self._write_toml(tmp_path, "harness.toml", """
[llm]
model = "base-model"
provider = "anthropic"
api_key = ""
api_base = "https://api.example.com"
""")
        self._write_toml(tmp_path, "harness.local.toml", """
[llm]
api_key = "sk-override-key"
""")

        config = Config.load(str(tmp_path / "harness.toml"))

        assert config.llm.api_key == "sk-override-key"
        assert config.llm.model == "base-model"
        assert config.llm.provider == "anthropic"
        assert config.llm.api_base == "https://api.example.com"

    def test_local_config_file_missing(self, tmp_path: Path):
        """When harness.local.toml does not exist, load still succeeds."""
        self._write_toml(tmp_path, "harness.toml", """
[llm]
model = "only-base"
[loop]
max_turns = 7
""")
        # deliberately do NOT create harness.local.toml

        config = Config.load(str(tmp_path / "harness.toml"))

        assert config.llm.model == "only-base"
        assert config.loop.max_turns == 7

    def test_local_config_empty_file(self, tmp_path: Path):
        """An empty (or whitespace-only) local file does not break loading."""
        self._write_toml(tmp_path, "harness.toml", """
[llm]
model = "base-model"
""")
        self._write_toml(tmp_path, "harness.local.toml", "\n")

        config = Config.load(str(tmp_path / "harness.toml"))

        assert config.llm.model == "base-model"

    def test_local_config_overrides_then_env(self, tmp_path: Path):
        """Env-var overrides still win over harness.local.toml values."""
        self._write_toml(tmp_path, "harness.toml", """
[llm]
model = "base-model"
""")
        self._write_toml(tmp_path, "harness.local.toml", """
[llm]
model = "local-model"
""")

        os.environ["HARNESS_MODEL"] = "env-model"
        try:
            config = Config.load(str(tmp_path / "harness.toml"))
            assert config.llm.model == "env-model"
        finally:
            del os.environ["HARNESS_MODEL"]

    def test_deep_merge_nested(self):
        """_deep_merge recurses into nested dicts instead of replacing them."""
        base = {
            "llm": {"model": "base", "api_key": ""},
            "loop": {"max_turns": 10},
        }
        override = {
            "llm": {"api_key": "sk-123"},
        }

        Config._deep_merge(base, override)

        # llm subtree was merged, not replaced
        assert base["llm"]["model"] == "base"
        assert base["llm"]["api_key"] == "sk-123"
        # loop subtree was not touched
        assert base["loop"]["max_turns"] == 10

    def test_load_resolves_local_alongside_cwd_config(self, tmp_path: Path, monkeypatch):
        """When harness.toml is found via _find_config, the sibling local file
        is also picked up automatically."""
        self._write_toml(tmp_path, "harness.toml", """
[llm]
model = "base-model"
api_key = ""
""")
        self._write_toml(tmp_path, "harness.local.toml", """
[llm]
api_key = "sk-from-local"
""")

        # monkey-patch _find_config to return our tmp_path file
        target = str(tmp_path / "harness.toml")
        monkeypatch.setattr(Config, "_find_config", lambda: target)

        config = Config.load()
        assert config.llm.api_key == "sk-from-local"
        assert config.llm.model == "base-model"
