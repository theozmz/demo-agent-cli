"""Configuration system — Pydantic models loaded from harness.toml and env vars."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import logging
import tomllib
from pydantic import BaseModel, Field

from harness.core.errors import ConfigValidationError


class LlmConfig(BaseModel):
    """LLM provider configuration — secrets in harness.local.toml (git-ignored)."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6-20250514"
    fallback_model: str = "claude-haiku-3-5-20251001"
    expensive_model: str = ""  # For reviews/architecture tasks (e.g., Opus)
    api_key: str = ""
    api_base: str = ""
    max_tokens: int = 8192
    temperature: float = 0.0


class LoopConfig(BaseModel):
    """Agentic loop configuration."""

    engine: Literal["native", "langgraph"] = "native"
    mode: Literal["standard", "pair_coding", "multi_agent"] = "standard"
    max_turns: int = Field(default=500, ge=1, le=500)
    compaction_threshold: float = Field(default=0.80, ge=0.5, le=0.95)
    enable_tool_intent_nudge: bool = False
    human_approval: bool = True
    max_review_iterations: int = Field(default=5, ge=1, le=20)
    # Autonomous mode selection (ComplexityGate)
    auto_mode: bool = True
    auto_mode_threshold: float = Field(default=0.6, ge=0.4, le=0.95)
    auto_mode_llm_fallback: bool = False


class SandboxConfig(BaseModel):
    """Sandbox configuration."""

    runtime: str = "docker"


class RepoMapConfig(BaseModel):
    """RepoMap configuration."""

    enabled: bool = False
    max_map_tokens: int = 2000


class CacheConfig(BaseModel):
    """Prompt cache configuration."""

    warm_enabled: bool = False
    warm_interval_seconds: int = 240


class ObservabilityConfig(BaseModel):
    """Observability configuration."""

    backend: Literal["harness", "langfuse", "none"] = "none"

    # Langfuse connection (used when backend == "langfuse")
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # Evaluation LLM (separate from agent LLM — use cost-effective model)
    eval_llm_provider: str = "openai"
    eval_llm_model: str = "gpt-4o-mini"
    eval_llm_api_key: str = ""
    eval_llm_api_base: str = ""


class Config(BaseModel):
    """Root configuration — loaded from harness.toml."""

    llm: LlmConfig = Field(default_factory=LlmConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    repomap: RepoMapConfig = Field(default_factory=RepoMapConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        """Load config from harness.toml, with optional env-var overrides.

        If harness.local.toml exists alongside harness.toml, it is deep-merged
        on top — local sections/keys override the base file.  This lets you keep
        secrets (api_key, api_base) out of version control.
        """
        if path is None:
            path = cls._find_config()
        data: dict = {}
        if path and Path(path).exists():
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                raise ConfigValidationError(f"Invalid TOML in {path}: {e}") from e
        # Deep-merge harness.local.toml when present alongside the base config
        if path:
            local_path = Path(path).with_name("harness.local.toml")
            if local_path.exists():
                try:
                    with open(local_path, "rb") as f:
                        local_data = tomllib.load(f)
                except tomllib.TOMLDecodeError as e:
                    raise ConfigValidationError(f"Invalid TOML in {local_path}: {e}") from e
                cls._deep_merge(data, local_data)
        # Warn about unknown config keys
        _log = logging.getLogger(__name__)
        for key in data:
            if key not in cls.model_fields:
                _log.warning("Unknown config section [%s] — ignored", key)
        config = cls(**{k: v for k, v in data.items() if k in cls.model_fields})
        config._apply_env_overrides()
        return config

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """Recursively merge *override* into *base* in-place."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._deep_merge(base[key], value)
            else:
                base[key] = value

    def _apply_env_overrides(self):
        """Minimal env-var overrides for model / provider only.

        api_key and api_base are read from harness.toml / harness.local.toml.
        """
        if os.environ.get("HARNESS_MODEL"):
            self.llm.model = os.environ["HARNESS_MODEL"]
        if os.environ.get("HARNESS_PROVIDER"):
            self.llm.provider = os.environ["HARNESS_PROVIDER"]

    @staticmethod
    def _find_config() -> str | None:
        """Search for harness.toml in cwd, then ~/.harness/."""
        cwd = Path.cwd()
        candidates = [
            cwd / "harness.toml",
            Path.home() / ".harness" / "harness.toml",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return None
