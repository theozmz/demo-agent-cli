"""Checkpoint management for LangGraph graphs.

Supports two backends:
- MemorySaver: in-process dict, for development/testing
- AsyncSqliteSaver: SQLite-persisted, for production use

Follows DESIGN.md section 3.7.4 checkpointing pattern.
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


def create_checkpointer(
    backend: Literal["memory", "sqlite"] = "memory",
    db_path: str = "",
) -> "BaseCheckpointSaver":  # type: ignore[name-defined]  # noqa: F821
    """Create a LangGraph checkpointer.

    Args:
        backend: "memory" for in-process (dev/test) or "sqlite" for persistence.
        db_path: Path to SQLite database (only used when backend="sqlite").
                 Defaults to ~/.harness/checkpoints.db.

    Returns:
        A LangGraph BaseCheckpointSaver instance.
    """
    if backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import AsyncSqliteSaver

            import os
            path = db_path or os.path.expanduser("~/.harness/checkpoints.db")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            logger.info("Using SQLite checkpointer at %s", path)
            return AsyncSqliteSaver.from_conn_string(path)
        except ImportError:
            logger.warning(
                "langgraph-checkpoint-sqlite not installed; "
                "falling back to MemorySaver"
            )
            return MemorySaver()

    logger.info("Using in-memory checkpointer (MemorySaver)")
    return MemorySaver()
