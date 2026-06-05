"""Context compaction — prevents token overflow in long conversations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from harness.llm.types import ChatMessage

logger = logging.getLogger(__name__)

# Tool types whose results are safe to stub (read-only, large outputs).
_STUBABLE_TOOLS = {"file_read", "glob_search", "grep_search", "web_fetch", "web_search"}

# Messages kept at the head (system prompt anchors).
_HEAD_KEEP = 1
# Messages kept at the tail under REACTIVE compaction.
_REACTIVE_TAIL_TURNS = 5


class CompactionStrategy(Enum):
    NONE = "none"
    MICRO = "micro"
    REACTIVE = "reactive"


@dataclass
class CompactionResult:
    strategy: CompactionStrategy
    messages: list[ChatMessage]
    tokens_before: int
    tokens_after: int
    truncated_count: int = 0


class TruncationTracker:
    """Tracks consecutive compaction events to prevent thrashing."""

    def __init__(self, max_consecutive: int = 3):
        self._count = 0
        self._max = max_consecutive

    def record(self) -> None:
        self._count += 1

    def reset(self) -> None:
        self._count = 0

    @property
    def exhausted(self) -> bool:
        return self._count >= self._max

    @property
    def count(self) -> int:
        return self._count


class CompactionEngine:
    """Token-ratio-driven context compaction.

    Uses two strategies, escalating with token pressure:

    * **MICRO** (ratio > 0.80): replace old tool results with lightweight
      stubs so the model still knows a tool was called but doesn't pay
      for the full output.
    * **REACTIVE** (ratio > 0.90): drop all but the last *N* turns.
      No LLM call required — safe when context is nearly full.

    The caller is responsible for estimating token counts and choosing
    the right threshold.
    """

    def __init__(
        self,
        context_window: int = 200000,
        micro_threshold: float = 0.80,
        reactive_threshold: float = 0.90,
    ):
        self.context_window = context_window
        self.micro_threshold = micro_threshold
        self.reactive_threshold = reactive_threshold

    def evaluate(self, messages: list[ChatMessage], token_count: int) -> CompactionStrategy:
        """Return the appropriate strategy for the current token ratio."""
        ratio = token_count / self.context_window if self.context_window else 0
        if ratio > self.reactive_threshold:
            return CompactionStrategy.REACTIVE
        if ratio > self.micro_threshold:
            return CompactionStrategy.MICRO
        return CompactionStrategy.NONE

    def compact(
        self,
        messages: list[ChatMessage],
        token_count: int,
        strategy: CompactionStrategy | None = None,
    ) -> CompactionResult:
        """Apply the chosen strategy and return the compacted message list."""
        if strategy is None:
            strategy = self.evaluate(messages, token_count)

        if strategy == CompactionStrategy.NONE:
            return CompactionResult(strategy, messages, token_count, token_count)

        if strategy == CompactionStrategy.MICRO:
            return self._micro(messages, token_count)

        if strategy == CompactionStrategy.REACTIVE:
            return self._reactive(messages, token_count)

        return CompactionResult(strategy, messages, token_count, token_count)

    # ------------------------------------------------------------------
    # MICRO: stub old tool results
    # ------------------------------------------------------------------

    def _micro(self, messages: list[ChatMessage], token_count: int) -> CompactionResult:
        stubbed = 0
        cutoff = max(1, len(messages) - 10)  # keep last 10 messages intact

        result: list[ChatMessage] = []
        for i, msg in enumerate(messages):
            if i < _HEAD_KEEP or i >= cutoff:
                result.append(msg)
            elif msg.role == "tool" and msg.name in _STUBABLE_TOOLS:
                stubbed += 1
                result.append(ChatMessage.tool_result(
                    tool_call_id=msg.tool_call_id or "",
                    content=f"[stub: {msg.name} result cleared — {len(msg.content)} chars]",
                    name=msg.name,
                    is_error=False,
                ))
            elif msg.role == "tool":
                result.append(msg)
            else:
                result.append(msg)

        # Rough estimate: stubbed results are ~50 chars vs potentially thousands
        after = token_count - stubbed * 500
        logger.debug("MICRO compaction: %d tool results stubbed, ~%d → ~%d tokens", stubbed, token_count, max(after, 0))

        return CompactionResult(CompactionStrategy.MICRO, result, token_count, max(after, 0), stubbed)

    # ------------------------------------------------------------------
    # REACTIVE: truncate to last N turns
    # ------------------------------------------------------------------

    def _reactive(self, messages: list[ChatMessage], token_count: int) -> CompactionResult:
        # A "turn" is roughly (user | assistant) pair — count backwards
        turn_starts: list[int] = []
        for i, msg in enumerate(messages):
            if msg.role in ("user",):
                turn_starts.append(i)

        if len(turn_starts) <= _REACTIVE_TAIL_TURNS + 1:
            return CompactionResult(CompactionStrategy.REACTIVE, list(messages), token_count, token_count)

        # Keep system/head messages + last N turns
        keep_from = turn_starts[-(_REACTIVE_TAIL_TURNS)]
        head = list(messages[:_HEAD_KEEP])
        tail = list(messages[keep_from:])
        result = head + [
            ChatMessage.user(
                "[Context truncated — earlier turns have been removed to stay within the token budget.]"
            ),
        ] + tail

        truncated = len(messages) - len(result)
        after = int(token_count * 0.4)  # rough estimate
        logger.debug("REACTIVE compaction: %d messages dropped, ~%d → ~%d tokens", truncated, token_count, after)

        return CompactionResult(CompactionStrategy.REACTIVE, result, token_count, after, truncated)
