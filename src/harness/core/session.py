"""Session → Thread → Turn data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4


class SessionStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ERROR = "error"


class TurnStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class Turn:
    """One user message → full agent response cycle."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    user_input: str = ""
    status: TurnStatus = TurnStatus.RUNNING
    messages: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    llm_call_count: int = 0
    tokens_used: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    duration_ms: int = 0


@dataclass
class Thread:
    """A continuous conversation thread."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    turns: list[Turn] = field(default_factory=list)
    compaction_summary: str | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Session:
    """One CLI invocation = one session."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    status: SessionStatus = SessionStatus.ACTIVE
    threads: list[Thread] = field(default_factory=list)
    active_thread_id: str | None = None
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    workspace_path: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime | None = None

    def create_thread(self) -> Thread:
        thread = Thread()
        self.threads.append(thread)
        self.active_thread_id = thread.id
        return thread

    def create_turn(self, thread: Thread, user_input: str) -> Turn:
        turn = Turn(user_input=user_input)
        thread.turns.append(turn)
        return turn
