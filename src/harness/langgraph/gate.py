"""ComplexityGate — autonomous pre-flight complexity assessment and mode selection.

The gate sits BEFORE the main agent loop. It assesses the user's task complexity
and auto-selects the engine + collaboration mode without any user configuration.

Design principle (MetaGPT-inspired):
- The agent autonomously evaluates "how complex is this task?"
- Based on complexity, it self-triggers the appropriate collaboration topology
- No user needs to know about "pair_coding" vs "multi_agent" modes

Trigger thresholds:
- SIMPLE → native standard (no multi-agent overhead needed)
- INTEGRATION → langgraph pair_coding (coder + reviewer, no human approval)
- ARCHITECTURE → langgraph multi_agent (controller + implementers + two-stage review)

Config controlled via harness.toml:
```toml
[loop]
auto_mode = true              # enable autonomous mode selection
auto_mode_threshold = 0.6     # complexity confidence threshold
```
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness.langgraph.complexity import ComplexityAssessor, ComplexityTier, ComplexityAssessment

if TYPE_CHECKING:
    from harness.config.config import Config
    from harness.llm.client import LlmClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mode selection result
# ---------------------------------------------------------------------------

@dataclass
class ModeSelection:
    """Result of autonomous mode selection by the ComplexityGate."""
    engine: str           # "native" | "langgraph"
    mode: str             # "standard" | "pair_coding" | "multi_agent"
    complexity: ComplexityAssessment
    reasoning: str
    auto_triggered: bool  # True if the gate changed the mode


# ---------------------------------------------------------------------------
# ComplexityGate
# ---------------------------------------------------------------------------

class ComplexityGate:
    """Pre-flight gate that assesses task complexity and selects agent mode.

    Inspired by MetaGPT's role self-selection: the agent evaluates the task
    and decides what kind of collaboration topology it needs.

    Modes by complexity tier:
    ┌──────────────┬──────────┬──────────────────────────────────────┐
    │ Tier         │ Engine   │ Mode       │ Rationale              │
    ├──────────────┼──────────┼────────────┼──────────────────────────┤
    │ SIMPLE       │ native   │ standard   │ Single agent, low overhead│
    │ INTEGRATION  │ langgraph│ pair_coding│ AI review, no human needed│
    │ ARCHITECTURE │ langgraph│ multi_agent│ Full team: plan+impl+review│
    └──────────────┴──────────┴────────────┴──────────────────────────┘
    """

    # Thresholds for triggering multi-agent modes
    # These reflect the complexity tier at which each mode activates
    MODE_THRESHOLDS: dict[ComplexityTier, tuple[str, str]] = {
        ComplexityTier.SIMPLE: ("native", "standard"),
        ComplexityTier.INTEGRATION: ("langgraph", "pair_coding"),
        ComplexityTier.ARCHITECTURE: ("langgraph", "multi_agent"),
    }

    def __init__(
        self,
        assessor: ComplexityAssessor | None = None,
        *,
        enabled: bool = True,
        confidence_threshold: float = 0.6,
        use_llm_fallback: bool = False,
    ):
        self._assessor = assessor or ComplexityAssessor()
        self.enabled = enabled
        self.confidence_threshold = confidence_threshold
        self.use_llm_fallback = use_llm_fallback

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess_and_select(
        self,
        task: str,
        *,
        current_engine: str = "native",
        current_mode: str = "standard",
        force_engine: str = "",
        force_mode: str = "",
    ) -> ModeSelection:
        """Assess task complexity and select the optimal agent mode.

        This is THE entry point for autonomous mode triggering.

        Args:
            task: The user's task description.
            current_engine: Currently configured engine (from config).
            current_mode: Currently configured mode (from config).
            force_engine: If non-empty, bypass auto-selection for engine.
            force_mode: If non-empty, bypass auto-selection for mode.

        Returns:
            ModeSelection with the selected engine, mode, and reasoning.
        """
        # If both engine and mode are explicitly forced, skip assessment
        if force_engine and force_mode:
            return ModeSelection(
                engine=force_engine,
                mode=force_mode,
                complexity=self._assessor.assess(task),
                reasoning="Engine and mode explicitly set by user — skipping auto-selection.",
                auto_triggered=False,
            )

        # If only mode is forced but engine is langgraph, skip
        if force_mode and current_engine == "langgraph":
            return ModeSelection(
                engine=current_engine,
                mode=force_mode,
                complexity=self._assessor.assess(task),
                reasoning="Mode explicitly set by user — skipping auto-selection.",
                auto_triggered=False,
            )

        if not self.enabled:
            return ModeSelection(
                engine=current_engine,
                mode=current_mode,
                complexity=self._assessor.assess(task),
                reasoning="Auto-mode disabled — using configured engine/mode.",
                auto_triggered=False,
            )

        # ---- Autonomous assessment ----
        assessment = self._assessor.assess(
            task,
            use_llm=self.use_llm_fallback,
        )

        # Determine if confidence is high enough to trust the assessment
        if assessment.confidence < self.confidence_threshold:
            logger.info(
                "Complexity confidence (%.2f) below threshold (%.2f) — "
                "defaulting to configured mode: %s/%s",
                assessment.confidence, self.confidence_threshold,
                current_engine, current_mode,
            )
            return ModeSelection(
                engine=current_engine,
                mode=current_mode,
                complexity=assessment,
                reasoning=(
                    f"Complexity assessment confidence too low "
                    f"({assessment.confidence:.0%} < {self.confidence_threshold:.0%}). "
                    f"Falling back to configured mode '{current_mode}'."
                ),
                auto_triggered=False,
            )

        # Select engine + mode based on complexity tier
        selected_engine, selected_mode = self.MODE_THRESHOLDS.get(
            assessment.tier, ("native", "standard")
        )

        # Check if we're changing anything
        auto_triggered = (
            selected_engine != current_engine
            or selected_mode != current_mode
        )

        reasoning = (
            f"Complexity: {assessment.tier.value} "
            f"(confidence: {assessment.confidence:.0%}). "
            f"Auto-selected: engine={selected_engine}, mode={selected_mode}. "
            f"{assessment.reasoning}"
        )

        if auto_triggered:
            logger.info(
                "AUTO-TRIGGER: task complexity '%s' → engine=%s mode=%s "
                "(was engine=%s mode=%s)",
                assessment.tier.value, selected_engine, selected_mode,
                current_engine, current_mode,
            )
        else:
            logger.debug(
                "No change: task complexity '%s' matches configured mode %s",
                assessment.tier.value, current_mode,
            )

        return ModeSelection(
            engine=selected_engine,
            mode=selected_mode,
            complexity=assessment,
            reasoning=reasoning,
            auto_triggered=auto_triggered,
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def is_complex(self, task: str) -> bool:
        """Quick check: is this task complex enough for multi-agent?"""
        assessment = self._assessor.assess(task)
        return assessment.tier in (
            ComplexityTier.INTEGRATION,
            ComplexityTier.ARCHITECTURE,
        )

    def should_use_multi_agent(self, task: str) -> bool:
        """Quick check: should we use the full multi-agent pipeline?"""
        assessment = self._assessor.assess(task)
        return assessment.tier == ComplexityTier.ARCHITECTURE


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_complexity_gate(config: "Config | None" = None) -> ComplexityGate:
    """Create a ComplexityGate from config.

    Reads [loop] auto_mode and auto_mode_threshold from config.
    Falls back to sensible defaults when config is unavailable.
    """
    enabled = True
    threshold = 0.6
    use_llm = False

    if config is not None:
        enabled = getattr(config.loop, "auto_mode", True)
        threshold = getattr(config.loop, "auto_mode_threshold", 0.6)
        use_llm = getattr(config.loop, "auto_mode_llm_fallback", False)

    return ComplexityGate(
        enabled=enabled,
        confidence_threshold=threshold,
        use_llm_fallback=use_llm,
    )
