"""AgenticLoop — the core agent loop (query → LLM → tools → observe → repeat)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness.llm.types import ChatMessage, ToolCall
from harness.llm.client import LlmClient
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.tool import ToolContext
from harness.core.loop_delegate import (
    LoopDelegate, LoopContext, LoopOutcome, LoopSignal, TextAction,
)
from harness.core.context import ContextGatherer
from harness.core.compaction import CompactionEngine, TruncationTracker
from harness.core.errors import MaxTurnsReachedError

if TYPE_CHECKING:
    from harness.logging.task_logger import TaskLogger

logger = logging.getLogger(__name__)


@dataclass
class LoopConfig:
    """Configuration for the agent loop."""

    max_turns: int = 30
    compaction_threshold: float = 0.80
    enable_tool_intent_nudge: bool = False


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
        # Set by the caller before each task
        self._session_id: str = ""
        self._workspace_root: str = ""

    async def check_signals(self) -> LoopSignal:
        return self._signal

    async def before_llm_call(self, ctx: LoopContext, iteration: int) -> LoopOutcome | None:
        return None

    async def call_llm(self, ctx: LoopContext, iteration: int) -> "LlmResponse":  # type: ignore[override]
        tools = ctx.tool_registry.get_schemas() if ctx.tool_registry and not ctx.force_text else None
        llm_start = time.monotonic()
        try:
            response = await self._llm.generate(
                messages=ctx.messages,
                tools=tools,
                system_prompt=ctx.system_prompt,
            )
            duration_ms = (time.monotonic() - llm_start) * 1000
            if self._task_logger:
                usage = response.usage
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
            if self._task_logger:
                self._task_logger.log_llm_call(
                    model=getattr(self._llm, "model", ""),
                    messages_count=len(ctx.messages),
                    response_type="error",
                    duration_ms=duration_ms,
                    iteration=iteration,
                    error=str(exc),
                )
            raise

    async def handle_text_response(self, text: str, ctx: LoopContext) -> TextAction:
        return TextAction.RETURN

    async def execute_tool_calls(self, tool_calls: list[ToolCall], ctx: LoopContext) -> LoopOutcome | None:
        if not ctx.tool_registry:
            return None

        tool_ctx = ToolContext(
            cwd=ctx.cwd,
            session_id=self._session_id,
            turn_id="",
            workspace_root=self._workspace_root,
            task_logger=self._task_logger,
        )
        for tc in tool_calls:
            try:
                output = await self._tools.execute(tc.name, tc.input, tool_ctx)
                ctx.messages.append(ChatMessage.tool_result(
                    tool_call_id=tc.id,
                    content=output.content,
                    name=tc.name,
                    is_error=output.is_error,
                ))
            except Exception as e:
                if self._task_logger:
                    self._task_logger.log_error(
                        source="tool",
                        message=str(e),
                        tool_name=tc.name,
                    )
                ctx.messages.append(ChatMessage.tool_result(
                    tool_call_id=tc.id,
                    content=str(e),
                    name=tc.name,
                    is_error=True,
                ))
        return None

    async def after_iteration(self, iteration: int, ctx: LoopContext):
        pass


class AgenticLoop:
    """
    Core agentic loop — the main query → LLM → tools → observe cycle.

    Yields messages in real-time for the CLI to render.
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
        # Fallback: rough char-based estimate
        total = sum(len(m.content or "") for m in self.ctx.messages)
        return int(total * 0.35)

    def _compaction_needed(self) -> bool:
        ratio = self._estimate_tokens() / self.config.compaction_threshold
        return ratio > self.config.compaction_threshold

    def _auto_compact(self) -> None:
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
        else:
            self._truncation_tracker.reset()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> LoopOutcome:
        """Execute the agent loop until completion or max turns."""
        start_time = time.monotonic()
        iteration = 0
        final_text = ""

        for iteration in range(1, self.config.max_turns + 1):
            # 1. Check signals
            signal = await self.delegate.check_signals()
            if signal == LoopSignal.STOP:
                return LoopOutcome(
                    kind="stopped", content=final_text,
                    duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
                )

            # 2. Pre-LLM hook
            early = await self.delegate.before_llm_call(self.ctx, iteration)
            if early:
                return early

            # 3. Call LLM
            try:
                response = await self.delegate.call_llm(self.ctx, iteration)
            except Exception as e:
                logger.error(f"LLM call failed at iteration {iteration}: {e}")
                return LoopOutcome(
                    kind="error", content=str(e),
                    duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
                )

            # 4. Parse response
            if response.tool_calls:
                # Execute tools
                self.ctx.messages.append(ChatMessage.assistant(
                    content=response.text or "", tool_calls=response.tool_calls,
                ))
                outcome = await self.delegate.execute_tool_calls(response.tool_calls, self.ctx)
                if outcome:
                    return outcome
            else:
                # Text response — done
                final_text = response.text or ""
                action = await self.delegate.handle_text_response(final_text, self.ctx)
                if action == TextAction.RETURN:
                    return LoopOutcome(
                        kind="completed", content=final_text,
                        tokens_used=response.usage.input_tokens + response.usage.output_tokens,
                        duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
                    )

            # 5. Post-iteration
            await self.delegate.after_iteration(iteration, self.ctx)

            # 6. Compaction check
            if self._compaction_needed():
                self._auto_compact()

        return LoopOutcome(
            kind="max_turns", content=final_text,
            duration_ms=(time.monotonic() - start_time) * 1000, turns=iteration,
        )
