"""Custom Textual Message classes for agent loop events."""

from __future__ import annotations

from textual.message import Message

from harness.core.loop import LoopEvent
from harness.core.loop_delegate import LoopOutcome


class AgentEvent(Message):
    """Posted by background worker when the agent loop emits a LoopEvent."""

    def __init__(self, loop_event: LoopEvent) -> None:
        self.loop_event = loop_event
        super().__init__()


class AgentComplete(Message):
    """Posted when the agent loop finishes."""

    def __init__(self, outcome: LoopOutcome) -> None:
        self.outcome = outcome
        super().__init__()


class AgentError(Message):
    """Posted when the agent loop encounters an exception."""

    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__()
