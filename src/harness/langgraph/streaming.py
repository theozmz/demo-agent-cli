"""LangGraph streaming — maps astream_events to LoopEvent callbacks.

Provides the bridge between LangGraph's event system and Harness CLI's
real-time progress display system.

Event type mapping (DESIGN.md section 3.7.5):
- on_chat_model_start  → LoopEvent(kind="thinking")
- on_chat_model_stream → LoopEvent(kind="text", content=...)
- on_tool_start        → LoopEvent(kind="tool_call", tool_name=..., tool_input=...)
- on_tool_end          → LoopEvent(kind="tool_result", tool_output=...)
- on_chain_end         → LoopEvent(kind="done", outcome=...)

Human-in-the-loop: When LangGraph raises GraphInterrupt (interrupt_before),
this module catches it and emits a special event so the CLI can prompt
the user and resume execution.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph
    from harness.core.loop import LoopEvent

logger = logging.getLogger(__name__)


def _fmt_tok(n: int) -> str:
    """Format token count: 1234 → '1.2k'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


async def stream_graph_events(
    graph: "CompiledStateGraph",
    initial_state: dict,
    config: dict,
    *,
    on_event: Callable[["LoopEvent"], None] | None = None,
) -> dict:
    """Stream LangGraph graph execution as LoopEvent callbacks.

    Args:
        graph: A compiled LangGraph StateGraph.
        initial_state: Initial state dict for the graph.
        config: LangGraph config dict with thread_id.
        on_event: Callback receiving LoopEvent for real-time CLI display.

    Returns:
        The final graph state dict after completion.

    Raises:
        GraphInterrupt: If the graph is interrupted for human approval.
    """
    from harness.core.loop import LoopEvent

    final_state: dict = {}

    try:
        async for event in graph.astream_events(
            initial_state,
            config=config,
            version="v2",
        ):
            _dispatch_event(event, on_event)

            # Capture final output from chain_end
            if event.get("event") == "on_chain_end" and event.get("name") == "LangGraph":
                data = event.get("data", {})
                output = data.get("output", {})
                if output:
                    final_state = output

    except Exception as exc:
        # Check for human-in-the-loop interrupt
        exc_name = type(exc).__name__
        if "Interrupt" in exc_name or "GraphInterrupt" in exc_name:
            logger.info("Graph interrupted for human-in-the-loop")
            if on_event:
                on_event(LoopEvent(
                    kind="done",
                    content="⏸ Paused for human approval",
                ))
            raise  # Let the caller handle the interrupt

        logger.error("Graph streaming error: %s", exc)
        raise

    # Emit final done event
    if on_event:
        on_event(LoopEvent(kind="done", content="Graph execution complete"))

    return final_state


def _dispatch_event(
    event: dict,
    on_event: Callable[["LoopEvent"], None] | None,
) -> None:
    """Dispatch a single LangGraph event to LoopEvent callback."""
    if on_event is None:
        return

    from harness.core.loop import LoopEvent

    kind = event.get("event", "")
    name = event.get("name", "")
    data = event.get("data", {})
    tags = event.get("tags", [])

    match kind:
        case "on_chat_model_start":
            on_event(LoopEvent(kind="thinking"))

        case "on_chat_model_stream":
            chunk = data.get("chunk", {})
            content = getattr(chunk, "content", None) if hasattr(chunk, "content") else chunk.get("content")
            if content:
                on_event(LoopEvent(kind="text", content=str(content)))

        case "on_tool_start":
            tool_input = data.get("input", {})
            on_event(LoopEvent(
                kind="tool_call",
                tool_name=name,
                tool_input=tool_input,
            ))

        case "on_tool_end":
            output = str(data.get("output", ""))
            is_error = "error" in output.lower()[:100] if output else False
            on_event(LoopEvent(
                kind="tool_result",
                tool_name=name,
                tool_output=output[:5000],  # Truncate for display
                tool_error=is_error,
            ))

        case "on_chat_model_end":
            output = data.get("output", {})
            usage_meta = output.get("usage_metadata", {}) if isinstance(output, dict) else {}
            input_tokens = usage_meta.get("input_tokens", 0)
            output_tokens = usage_meta.get("output_tokens", 0)
            if input_tokens or output_tokens:
                on_event(LoopEvent(
                    kind="llm_tokens",
                    tool_name=name,  # node name: "coder", "reviewer", etc.
                    content=f"📊 {_fmt_tok(input_tokens)} in / {_fmt_tok(output_tokens)} out [{name}]",
                ))

        case "on_chain_end":
            # Only emit for terminal chain
            if name == "LangGraph":
                output = data.get("output", {})
                terminal = output.get("terminal_reason", "")
                if terminal:
                    on_event(LoopEvent(
                        kind="done",
                        content=f"Graph completed: {terminal}",
                    ))
