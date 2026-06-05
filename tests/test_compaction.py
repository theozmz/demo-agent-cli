"""Tests for the compaction engine and truncation tracker."""

import pytest

from harness.core.compaction import (
    CompactionEngine,
    CompactionStrategy,
    CompactionResult,
    TruncationTracker,
)
from harness.llm.types import ChatMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_messages(count: int, role: str = "user", prefix: str = "msg") -> list[ChatMessage]:
    """Create *count* messages with the given role."""
    return [
        ChatMessage(role=role, content=f"{prefix} {i}")  # type: ignore[call-arg]
        for i in range(count)
    ]


def _make_tool_result(tool_name: str, content: str, tool_call_id: str = "") -> ChatMessage:
    """Create a tool-result message."""
    return ChatMessage.tool_result(
        tool_call_id=tool_call_id,
        content=content,
        name=tool_name,
    )


def _make_tool_call(name: str, tool_call_id: str = "") -> ChatMessage:
    """Create an assistant message with a tool call."""
    from harness.llm.types import ToolCall
    return ChatMessage.assistant(
        content="",
        tool_calls=[ToolCall(id=tool_call_id or "call_1", name=name, input={})],
    )


def _make_system(content: str = "system prompt") -> ChatMessage:
    """Create a system message."""
    return ChatMessage(role="system", content=content)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# TruncationTracker
# ---------------------------------------------------------------------------
class TestTruncationTracker:
    def test_initial_state(self):
        t = TruncationTracker()
        assert t.count == 0
        assert not t.exhausted

    def test_record_increments(self):
        t = TruncationTracker()
        t.record()
        assert t.count == 1
        t.record()
        assert t.count == 2

    def test_exhausted_after_max(self):
        t = TruncationTracker(max_consecutive=3)
        assert not t.exhausted
        t.record()
        t.record()
        assert not t.exhausted
        t.record()
        assert t.exhausted

    def test_reset_clears_count(self):
        t = TruncationTracker(max_consecutive=3)
        t.record()
        t.record()
        t.reset()
        assert t.count == 0
        assert not t.exhausted

    def test_custom_max(self):
        t = TruncationTracker(max_consecutive=5)
        for _ in range(4):
            t.record()
        assert not t.exhausted
        t.record()
        assert t.exhausted


# ---------------------------------------------------------------------------
# CompactionEngine.evaluate
# ---------------------------------------------------------------------------
class TestCompactionEvaluate:
    def test_none_below_micro_threshold(self):
        engine = CompactionEngine(context_window=100000, micro_threshold=0.80)
        s = engine.evaluate([], 50000)  # 50% — well below
        assert s == CompactionStrategy.NONE

    def test_micro_above_micro_threshold(self):
        engine = CompactionEngine(context_window=100000, micro_threshold=0.80)
        s = engine.evaluate([], 85000)  # 85% — between 0.80 and 0.90
        assert s == CompactionStrategy.MICRO

    def test_reactive_above_reactive_threshold(self):
        engine = CompactionEngine(context_window=100000, reactive_threshold=0.90)
        s = engine.evaluate([], 95000)  # 95% — above 0.90
        assert s == CompactionStrategy.REACTIVE

    def test_zero_context_window(self):
        engine = CompactionEngine(context_window=0)
        s = engine.evaluate([], 1000)
        assert s == CompactionStrategy.NONE


# ---------------------------------------------------------------------------
# CompactionEngine.micro
# ---------------------------------------------------------------------------
class TestMicroCompaction:
    def test_stubs_stubable_tools(self):
        engine = CompactionEngine()
        # MICRO: messages between _HEAD_KEEP (1) and cutoff (len-10) are stubbable.
        # Put tool results early (indices 1-4) and filler late so they're in the middle.
        msgs = [_make_system("system")]
        msgs.append(_make_tool_call("file_read", "c1"))
        msgs.append(_make_tool_result("file_read", "x" * 5000, "c1"))
        msgs.append(_make_tool_call("glob_search", "c2"))
        msgs.append(_make_tool_result("glob_search", "y" * 3000, "c2"))
        # Fill the tail with 10 recent messages (cutoff = total - 10)
        for i in range(5):
            msgs.append(ChatMessage(role="user", content=f"q{i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage(role="assistant", content=f"a{i}"))  # type: ignore[call-arg]

        # total = 15, cutoff = max(1, 15-10) = 5. Indices 1-4 are stubbable.
        result = engine._micro(msgs, 10000)
        assert result.strategy == CompactionStrategy.MICRO
        assert result.truncated_count == 2
        # Both tool results should be stubbed
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        stubbed = [m for m in tool_msgs if "[stub:" in m.content]
        assert len(stubbed) == 2

    def test_does_not_stub_non_stubable_tools(self):
        engine = CompactionEngine()
        msgs = [
            _make_system("system"),
            _make_tool_call("bash_exec", "c1"),
            _make_tool_result("bash_exec", "important output", "c1"),
            _make_tool_call("file_write", "c2"),
            _make_tool_result("file_write", "wrote file", "c2"),
        ]
        result = engine._micro(msgs, 10000)
        assert result.truncated_count == 0
        # bash_exec and file_write are NOT stubable — results preserved
        for i, m in enumerate(result.messages):
            if m.role == "tool":
                assert "[stub:" not in m.content

    def test_preserves_recent_messages(self):
        """Last 10 messages are never stubbed."""
        engine = CompactionEngine()
        msgs = [_make_system("system")]
        # Add 15 tool-call + tool-result pairs
        for i in range(15):
            msgs.append(_make_tool_call("file_read", f"c{i}"))
            msgs.append(_make_tool_result("file_read", f"content {i}" * 100, f"c{i}"))
        # Total: 1 + 30 = 31 messages. Last 10 (indices 21-30) preserved.
        result = engine._micro(msgs, 50000)
        # Last 10 = last 5 pairs. Those should NOT be stubbed.
        recent_tools = [
            m for m in result.messages[-10:] if m.role == "tool"
        ]
        for tm in recent_tools:
            assert "[stub:" not in tm.content, f"Recent tool result was stubbed: {tm.content[:80]}"

    def test_preserves_system_message(self):
        engine = CompactionEngine()
        msgs = [
            _make_system("important system prompt"),
            _make_tool_call("file_read", "c1"),
            _make_tool_result("file_read", "big output" * 500, "c1"),
        ]
        result = engine._micro(msgs, 200000)
        assert result.messages[0].content == "important system prompt"

    def test_messages_below_cutoff_not_stubbed(self):
        """If total messages <= 10, nothing gets stubbed."""
        engine = CompactionEngine()
        msgs = [
            _make_system("system"),
            _make_tool_call("file_read", "c1"),
            _make_tool_result("file_read", "big" * 500, "c1"),
        ]  # 3 messages — below cutoff of max(1, 3-10)=1
        result = engine._micro(msgs, 200000)
        assert result.truncated_count == 0


# ---------------------------------------------------------------------------
# CompactionEngine.reactive
# ---------------------------------------------------------------------------
class TestReactiveCompaction:
    def test_drops_old_turns(self):
        engine = CompactionEngine()
        msgs = [
            _make_system("system"),
        ]
        # Add 10 turns (each turn = user + assistant)
        for i in range(10):
            msgs.append(ChatMessage(role="user", content=f"question {i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage(role="assistant", content=f"answer {i}"))  # type: ignore[call-arg]
        # 1 + 20 = 21 messages, > 5 turns
        result = engine._reactive(msgs, 50000)
        assert result.strategy == CompactionStrategy.REACTIVE
        assert len(result.messages) < len(msgs)

    def test_injects_truncation_notice(self):
        engine = CompactionEngine()
        msgs = [_make_system("system")]
        for i in range(10):
            msgs.append(ChatMessage(role="user", content=f"q{i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage(role="assistant", content=f"a{i}"))  # type: ignore[call-arg]
        result = engine._reactive(msgs, 50000)
        notices = [m for m in result.messages if "truncated" in (m.content or "").lower()]
        assert len(notices) == 1

    def test_keeps_last_N_turns(self):
        engine = CompactionEngine()
        msgs = [_make_system("system")]
        for i in range(10):
            msgs.append(ChatMessage(role="user", content=f"q{i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage(role="assistant", content=f"a{i}"))  # type: ignore[call-arg]
        result = engine._reactive(msgs, 50000)
        # Should keep last 5 turns = last 10 messages (5 user + 5 assistant)
        last_user_msgs = [m for m in result.messages if m.role == "user"]
        assert len(last_user_msgs) >= 5
        assert "q9" in last_user_msgs[-1].content
        assert "q5" in last_user_msgs[0].content or "q5" in last_user_msgs[1].content

    def test_few_turns_not_truncated(self):
        engine = CompactionEngine()
        msgs = [_make_system("system")]
        for i in range(3):  # only 3 turns
            msgs.append(ChatMessage(role="user", content=f"q{i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage(role="assistant", content=f"a{i}"))  # type: ignore[call-arg]
        result = engine._reactive(msgs, 50000)
        # Should return unchanged (less than or equal to _REACTIVE_TAIL_TURNS + 1)
        assert len(result.messages) == len(msgs)
        assert result.truncated_count == 0


# ---------------------------------------------------------------------------
# CompactionEngine.compact (integration)
# ---------------------------------------------------------------------------
class TestCompactIntegration:
    def test_compact_auto_selects_strategy(self):
        engine = CompactionEngine(context_window=100000)
        # Create enough content to trigger REACTIVE
        msgs = [_make_system("sys")]
        for i in range(20):
            msgs.append(ChatMessage(role="user", content=f"question {i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage.tool_result(tool_call_id=f"c{i}", content=f"x" * 1000, name="file_read"))
        result = engine.compact(msgs, 95000)  # 95% → REACTIVE
        assert result.strategy == CompactionStrategy.REACTIVE

    def test_compact_with_explicit_strategy(self):
        engine = CompactionEngine()
        # Put tool result early (stubbable middle zone), filler late (tail)
        msgs = [_make_system("sys")]
        msgs.append(_make_tool_call("file_read", "c1"))
        msgs.append(_make_tool_result("file_read", "x" * 5000, "c1"))
        for i in range(5):
            msgs.append(ChatMessage(role="user", content=f"q{i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage(role="assistant", content=f"a{i}"))  # type: ignore[call-arg]

        result = engine.compact(msgs, 90000, strategy=CompactionStrategy.MICRO)
        assert result.strategy == CompactionStrategy.MICRO
        assert result.truncated_count >= 1

    def test_compaction_preserves_message_roles(self):
        """After compaction, all message roles should be valid."""
        engine = CompactionEngine()
        msgs = [_make_system("sys")]
        for i in range(15):
            msgs.append(ChatMessage(role="user", content=f"q{i}"))  # type: ignore[call-arg]
            msgs.append(ChatMessage.tool_result(tool_call_id=f"c{i}", content=f"x" * 2000, name="file_read"))
        result = engine.compact(msgs, 180000)  # trigger REACTIVE
        for m in result.messages:
            assert m.role in ("system", "user", "assistant", "tool")
