"""AgenticLoop — the core agent loop (query → LLM → tools → observe → repeat).

Implements the credit-assignment framework from "Who Gets the Credit?"
— each LoopEvent carries a SignalGranularity tag (G0–G3) so downstream
analysis can attribute outcomes to prompt (P), structural (S), and
memory (M) context dimensions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, TYPE_CHECKING

from harness.llm.types import ChatMessage, ToolCall
from harness.llm.client import LlmClient
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.tool import ToolContext
from harness.core.loop_delegate import (
    LoopDelegate, LoopContext, LoopOutcome, LoopSignal, TextAction,
)
from harness.core.context import ContextGatherer
from harness.core.compaction import CompactionEngine, CompactionStrategy, TruncationTracker
from harness.core.errors import MaxTurnsReachedError

if TYPE_CHECKING:
    from harness.logging.task_logger import TaskLogger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credit-assignment framework enums (from Li et al., 2026)
# ---------------------------------------------------------------------------

class SignalGranularity(Enum):
    """Feedback signal granularity — the credit-assignment taxonomy axis.

    G0 — Outcome-only scalar (pass/fail, exit code).
    G1 — Process-level textual diagnostic (error messages, tool outputs).
    G2 — Component-attributed signal (which tool/validator failed).
    G3 — Cross-dimensional harness signal (compaction, retry, safety, memory hit).
    """
    G0 = "G0"
    G1 = "G1"
    G2 = "G2"
    G3 = "G3"


class AttributionDimension(Enum):
    """Which context dimension a signal primarily informs.

    P — Prompt context (semantic control: instructions, exemplars).
    S — Structural context (orchestration: roles, workflows, tools).
    M — Memory context (runtime state: persistence, retrieval, compaction).
    """
    PROMPT = "P"
    STRUCTURAL = "S"
    MEMORY = "M"

# Retry config for transient LLM failures
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 3.0, 7.0]  # seconds, exponential-ish

# Recognised transient exception types / messages
_TRANSIENT_PATTERNS = (
    "timeout", "timed out", "connection", "rate limit", "rate exceeded",
    "server error", "service unavailable", "too many requests",
    "internal server error", "bad gateway", "gateway timeout",
    "overloaded", "capacity", "throttle",
)


# ---------------------------------------------------------------------------
# LoopEvent — real-time progress events for CLI display
# ---------------------------------------------------------------------------
@dataclass
class LoopEvent:
    """Emitted by AgenticLoop.run() for real-time CLI feedback.

    Each event carries a ``signal_granularity`` tag (G0–G3) and a
    primary ``attribution`` dimension (P/S/M) for credit-assignment
    analysis per Li et al. (2026).
    """

    kind: str  # thinking | tool_call | tool_result | text | retry | done | compact
    iteration: int = 0

    # tool_call / tool_result
    tool_name: str = ""
    tool_input: dict[str, Any] | None = None
    tool_output: str = ""
    tool_error: bool = False

    # text / done
    content: str = ""

    # retry
    retry_attempt: int = 0
    retry_error: str = ""

    # final outcome (kind="done")
    outcome: LoopOutcome | None = None

    # Credit-assignment framework (Li et al., 2026)
    signal_granularity: SignalGranularity = SignalGranularity.G0
    attribution: AttributionDimension = AttributionDimension.STRUCTURAL


# ---------------------------------------------------------------------------
# LoopConfig
# ---------------------------------------------------------------------------
@dataclass
class LoopConfig:
    """Configuration for the agent loop."""

    max_turns: int = 500
    compaction_threshold: float = 0.80
    enable_tool_intent_nudge: bool = False


# ---------------------------------------------------------------------------
# ChatDelegate
# ---------------------------------------------------------------------------
class ChatDelegate(LoopDelegate):
    """Standard interactive chat delegate — calls LLM, executes tools, returns text."""

    def __init__(
        self,
        llm: LlmClient,
        tool_executor: ToolExecutor,
        gatherer: ContextGatherer,
        task_logger: "TaskLogger | None" = None,
    ):
        self._llm = llm
        self._tools = tool_executor
        self._gatherer = gatherer
        self._signal: LoopSignal = LoopSignal.NONE
        self._task_logger = task_logger
        self._session_id: str = ""
        self._workspace_root: str = ""
        # Progress callback — set by AgenticLoop before calling call_llm
        self._on_event: Callable[[LoopEvent], None] | None = None

    async def check_signals(self) -> LoopSignal:
        return self._signal

    async def before_llm_call(self, ctx: LoopContext, iteration: int) -> LoopOutcome | None:
        return None

    async def call_llm(self, ctx: LoopContext, iteration: int) -> "LlmResponse":  # type: ignore[override]
        tools = ctx.tool_registry.get_schemas() if ctx.tool_registry and not ctx.force_text else None
        last_error: Exception | None = None

        from harness.observability import get_backend, NoopBackend

        _obs_backend = get_backend()
        _gen = None
        if not isinstance(_obs_backend, NoopBackend):
            model = getattr(self._llm, "model", "")
            _gen = _obs_backend.create_trace(name="llm_call").generation(
                name="llm_call",
                model=model,
                input={
                    "messages_count": len(ctx.messages),
                    "has_tools": tools is not None and len(tools) > 0 if tools else False,
                    "iteration": iteration,
                },
                model_parameters={
                    "temperature": getattr(self._llm, "temperature", 0.0),
                    "max_tokens": getattr(self._llm, "max_tokens", 0),
                },
            )

        for attempt in range(_MAX_RETRIES):
            llm_start = time.monotonic()
            try:
                response = await self._llm.generate(
                    messages=ctx.messages,
                    tools=tools,
                    system_prompt=ctx.system_prompt,
                )
                duration_ms = (time.monotonic() - llm_start) * 1000
                usage = response.usage
                if _gen is not None and usage:
                    _gen.end(
                        output={"response_type": "tool_calls" if response.tool_calls else "text"},
                        usage={
                            "input": usage.input_tokens,
                            "output": usage.output_tokens,
                            "total": usage.input_tokens + usage.output_tokens,
                        },
                    )
                elif _gen is not None:
                    _gen.end(output={"response_type": "tool_calls" if response.tool_calls else "text"})
                if self._task_logger:
                    self._task_logger.log_llm_call(
                        model=getattr(self._llm, "model", ""),
                        provider=getattr(self._llm, "provider", ""),
                        messages_count=len(ctx.messages),
                        has_tools=tools is not None and len(tools) > 0,
                        response_type="tool_calls" if response.tool_calls else "text",
                        tokens_input=usage.input_tokens if usage else 0,
                        tokens_output=usage.output_tokens if usage else 0,
                        duration_ms=duration_ms,
                        iteration=iteration,
                    )
                return response

            except Exception as exc:
                duration_ms = (time.monotonic() - llm_start) * 1000
                err_msg = str(exc).lower()
                is_transient = any(p in err_msg for p in _TRANSIENT_PATTERNS)

                if self._task_logger:
                    self._task_logger.log_llm_call(
                        model=getattr(self._llm, "model", ""),
                        messages_count=len(ctx.messages),
                        response_type="error",
                        duration_ms=duration_ms,
                        iteration=iteration,
                        error=str(exc),
                    )

                if not is_transient or attempt >= _MAX_RETRIES - 1:
                    if _gen is not None:
                        _gen.end(output={"error": str(exc)}, model="")
                    raise

                last_error = exc
                backoff = _RETRY_BACKOFF[attempt]
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, backoff, exc,
                )
                if self._on_event:
                    self._on_event(LoopEvent(
                        kind="retry",
                        iteration=iteration,
                        retry_attempt=attempt + 1,
                        retry_error=str(exc)[:200],
                        signal_granularity=SignalGranularity.G3,
                        attribution=AttributionDimension.MEMORY,
                    ))
                if self._task_logger:
                    self._task_logger.log_attribution(
                        dimension="M", granularity="G3",
                        event_kind="retry", iteration=iteration,
                        detail=f"LLM retry {attempt+1}/{_MAX_RETRIES}: {str(exc)[:100]}",
                    )
                await asyncio.sleep(backoff)

        # Should not reach here, but just in case
        if _gen is not None:
            _gen.end(output={"error": "all retries exhausted"}, model="")
        if last_error:
            raise last_error
        raise RuntimeError("LLM call failed after retries")

    async def handle_text_response(self, text: str, ctx: LoopContext) -> TextAction:
        return TextAction.RETURN

    async def execute_tool_calls(
        self, tool_calls: list[ToolCall], ctx: LoopContext
    ) -> LoopOutcome | None:
        if not ctx.tool_registry:
            return None

        tool_ctx = ToolContext(
            cwd=ctx.cwd,
            session_id=self._session_id,
            turn_id="",
            workspace_root=self._workspace_root,
            task_logger=self._task_logger,
            subagent_depth=ctx.subagent_depth,
        )
        for tc in tool_calls:
            # Notify: tool call starting — G2: component-attributed
            if self._on_event:
                self._on_event(LoopEvent(
                    kind="tool_call",
                    tool_name=tc.name,
                    tool_input=tc.input,
                    signal_granularity=SignalGranularity.G2,
                    attribution=AttributionDimension.STRUCTURAL,
                ))

            try:
                output = await self._tools.execute(tc.name, tc.input, tool_ctx)
            except Exception as e:
                if self._task_logger:
                    self._task_logger.log_error(
                        source="tool", message=str(e), tool_name=tc.name,
                    )
                ctx.messages.append(ChatMessage.tool_result(
                    tool_call_id=tc.id, content=str(e), name=tc.name, is_error=True,
                ))
                if self._on_event:
                    self._on_event(LoopEvent(
                        kind="tool_result",
                        tool_name=tc.name,
                        tool_output=f"Error: {e}",
                        tool_error=True,
                        signal_granularity=SignalGranularity.G2,
                        attribution=AttributionDimension.STRUCTURAL,
                    ))
                if self._task_logger:
                    self._task_logger.log_attribution(
                        dimension="S", granularity="G2",
                        event_kind="tool_result", tool_name=tc.name,
                        iteration=ctx.iteration,
                        detail=f"Tool error: {str(e)[:100]}",
                    )
                continue

            # Notify: tool result (truncated for display)
            display_output = output.content or "(no output)"
            ctx.messages.append(ChatMessage.tool_result(
                tool_call_id=tc.id,
                content=output.content,
                name=tc.name,
                is_error=output.is_error,
            ))
            if self._on_event:
                # G1 for read tools (process-level text), G2 for write/exec
                gran = SignalGranularity.G2 if tc.name in ("file_write", "file_edit", "bash_exec") else SignalGranularity.G1
                self._on_event(LoopEvent(
                    kind="tool_result",
                    tool_name=tc.name,
                    tool_output=display_output,
                    tool_error=output.is_error,
                    signal_granularity=gran,
                    attribution=AttributionDimension.STRUCTURAL,
                ))
            if self._task_logger:
                gran_str = "G2" if tc.name in ("file_write", "file_edit", "bash_exec") else "G1"
                self._task_logger.log_attribution(
                    dimension="S", granularity=gran_str,
                    event_kind="tool_result", tool_name=tc.name,
                    iteration=ctx.iteration,
                    detail="Tool executed successfully",
                )

        return None

    async def after_iteration(self, iteration: int, ctx: LoopContext):
        pass


# ---------------------------------------------------------------------------
# AgenticLoop
# ---------------------------------------------------------------------------
class AgenticLoop:
    """Core agentic loop — the main query → LLM → tools → observe cycle.

    Supports a progress callback (``on_event``) so the CLI can display
    real-time feedback during execution.
    """

    def __init__(self, delegate: LoopDelegate, ctx: LoopContext, config: LoopConfig):
        self.delegate = delegate
        self.ctx = ctx
        self.config = config
        self._compaction_engine = CompactionEngine()
        self._truncation_tracker = TruncationTracker()

    # ------------------------------------------------------------------
    # Compaction helpers
    # ------------------------------------------------------------------

    def _estimate_tokens(self) -> int:
        """Estimate current token usage across all messages."""
        if self.ctx.llm:
            return self.ctx.llm.estimate_tokens(self.ctx.messages)
        total = sum(len(m.content or "") for m in self.ctx.messages)
        return int(total * 0.35)

    def _compaction_needed(self) -> bool:
        """Check whether compaction should be applied.

        Uses the engine's evaluate() so threshold comparisons are correct.
        """
        if self._truncation_tracker.exhausted:
            logger.warning("Truncation tracker exhausted — skipping compaction check")
            return False
        tokens = self._estimate_tokens()
        strategy = self._compaction_engine.evaluate(self.ctx.messages, tokens)
        return strategy != CompactionStrategy.NONE

    def _auto_compact(self, on_event: Callable[[LoopEvent], None] | None = None) -> None:
        tokens = self._estimate_tokens()
        result = self._compaction_engine.compact(self.ctx.messages, tokens)
        if result.strategy.value != "none":
            self.ctx.messages = result.messages
            self._truncation_tracker.record()
            logger.info(
                "Compaction: %s — %d → ~%d tokens",
                result.strategy.value,
                result.tokens_before,
                result.tokens_after,
            )
            # G3: cross-dimensional harness signal — compaction reflects
            # token budget pressure, which involves P (what to keep),
            # S (which tool results to stub), and M (how much state)
            if on_event:
                on_event(LoopEvent(
                    kind="compact",
                    content=result.strategy.value,
                    signal_granularity=SignalGranularity.G3,
                    attribution=AttributionDimension.MEMORY,
                ))
            # Log to TaskLogger for persistent credit-assignment analysis
            if self.delegate and hasattr(self.delegate, '_task_logger') and self.delegate._task_logger:
                self.delegate._task_logger.log_compaction(
                    strategy=result.strategy.value,
                    tokens_before=result.tokens_before,
                    tokens_after=result.tokens_after,
                    truncated_count=result.truncated_count,
                )
        else:
            self._truncation_tracker.reset()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(
        self, on_event: Callable[[LoopEvent], None] | None = None
    ) -> LoopOutcome:
        """Execute the agent loop until completion or max turns.

        Args:
            on_event: Optional callback for real-time progress events.
        """
        start_time = time.monotonic()
        iteration = 0
        final_text = ""

        # Wire progress callback into the delegate
        if hasattr(self.delegate, '_on_event'):
            self.delegate._on_event = on_event

        # Create session-level langfuse trace
        from harness.observability import get_backend, NoopBackend

        _obs_backend = get_backend()
        _trace = None
        if not isinstance(_obs_backend, NoopBackend):
            session_id = getattr(self.delegate, '_session_id', '')
            first_msg = self.ctx.messages[0].content if self.ctx.messages else ""
            engine = getattr(self.config, 'engine', 'native')
            _trace = _obs_backend.create_trace(
                name="agent_session",
                session_id=session_id,
                input={"user_prompt": first_msg[:500] if first_msg else ""},
                tags=["agent_loop", str(engine)],
            )

        def _end_trace(outcome: LoopOutcome) -> None:
            if _trace is not None:
                _trace.end(output={
                    "outcome": outcome.kind,
                    "turns": outcome.turns,
                    "duration_ms": outcome.duration_ms,
                })

        for iteration in range(1, self.config.max_turns + 1):
            # 1. Check signals
            signal = await self.delegate.check_signals()
            if signal == LoopSignal.STOP:
                outcome = LoopOutcome(
                    kind="stopped", content=final_text,
                    duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
                )
                if on_event:
                    on_event(LoopEvent(
                        kind="done", outcome=outcome,
                        signal_granularity=SignalGranularity.G0,
                        attribution=AttributionDimension.STRUCTURAL,
                    ))
                _end_trace(outcome)
                return outcome

            # 2. Pre-LLM hook
            early = await self.delegate.before_llm_call(self.ctx, iteration)
            if early:
                if on_event:
                    on_event(LoopEvent(
                        kind="done", outcome=early,
                        signal_granularity=SignalGranularity.G0,
                        attribution=AttributionDimension.STRUCTURAL,
                    ))
                _end_trace(early)
                return early

            # 3. Call LLM — G1: process-level textual signal
            if on_event:
                on_event(LoopEvent(
                    kind="thinking", iteration=iteration,
                    signal_granularity=SignalGranularity.G1,
                    attribution=AttributionDimension.PROMPT,
                ))
            try:
                response = await self.delegate.call_llm(self.ctx, iteration)
            except Exception as e:
                logger.error(f"LLM call failed at iteration {iteration}: {e}")
                outcome = LoopOutcome(
                    kind="error", content=str(e),
                    duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
                )
                if on_event:
                    on_event(LoopEvent(
                        kind="done", outcome=outcome,
                        signal_granularity=SignalGranularity.G0,
                        attribution=AttributionDimension.STRUCTURAL,
                    ))
                _end_trace(outcome)
                return outcome

            # 4. Parse response
            if response.tool_calls:
                self.ctx.messages.append(ChatMessage.assistant(
                    content=response.text or "", tool_calls=response.tool_calls,
                ))
                outcome = await self.delegate.execute_tool_calls(response.tool_calls, self.ctx)
                if outcome:
                    if on_event:
                        on_event(LoopEvent(
                            kind="done", outcome=outcome,
                            signal_granularity=SignalGranularity.G0,
                            attribution=AttributionDimension.STRUCTURAL,
                        ))
                    _end_trace(outcome)
                    return outcome
            else:
                final_text = response.text or ""
                action = await self.delegate.handle_text_response(final_text, self.ctx)
                if action == TextAction.RETURN:
                    usage = response.usage
                    tokens_used = (usage.input_tokens + usage.output_tokens) if usage else 0
                    outcome = LoopOutcome(
                        kind="completed", content=final_text,
                        tokens_used=tokens_used,
                        duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
                    )
                    if on_event:
                        on_event(LoopEvent(
                            kind="done", outcome=outcome,
                            signal_granularity=SignalGranularity.G0,
                            attribution=AttributionDimension.PROMPT,
                        ))
                    _end_trace(outcome)
                    return outcome

            # 5. Post-iteration
            await self.delegate.after_iteration(iteration, self.ctx)

            # 6. Compaction check — emits G3 cross-dimensional harness signal
            if self._compaction_needed():
                self._auto_compact(on_event)

        outcome = LoopOutcome(
            kind="max_turns", content=final_text,
            duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
        )
        if on_event:
            on_event(LoopEvent(
                kind="done", outcome=outcome,
                signal_granularity=SignalGranularity.G0,
                attribution=AttributionDimension.STRUCTURAL,
            ))
        _end_trace(outcome)
        return outcome
