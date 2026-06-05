"""TagCache — mtime-based incremental cache for tree-sitter tags."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from harness.repomap.tags import Tag

logger = logging.getLogger(__name__)


class TagCache:
    """Per-file cache keyed by (path, mtime).

    Avoids re-parsing files that haven't changed.  Uses an in-memory
    dict; designed to be replaced with diskcache later.
    """

    def __init__(self):
        self._cache: dict[str, tuple[float, list[Tag]]] = {}  # rel_path -> (mtime, tags)

    def get(self, file_path: str, mtime: float) -> list[Tag] | None:
        entry = self._cache.get(file_path)
        if entry and entry[0] == mtime:
            return entry[1]
        return None

    def set(self, file_path: str, mtime: float, tags: list[Tag]) -> None:
        self._cache[file_path] = (mtime, tags)

    def invalidate(self, file_path: str) -> None:
        self._cache.pop(file_path, None)

    def clear(self) -> None:
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class TokenBudgetOptimizer:
    """Selects the best subset of files to fit within a token budget.

    Files are sorted by PageRank score descending.  A binary search
    over the file count finds the largest set that stays under budget.
    """

    def __init__(self, budget: int = 4000):
        self.budget = budget

    def select(
        self,
        ranked_files: list[tuple[str, float]],
        all_tags: dict[str, list[Tag]],
    ) -> list[str]:
        """Return the list of file paths that fit within the budget."""
        if not ranked_files:
            return []

        # Estimate tokens per file: ~8 tokens per tag line
        def _estimate(paths: list[str]) -> int:
            total = 0
            for p in paths:
                tags = all_tags.get(p, [])
                for t in tags:
                    total += len(t.signature) // 3 + 4  # rough token estimate
            return total

        lo, hi = 0, len(ranked_files)
        best: list[str] = []

        while lo <= hi:
            mid = (lo + hi) // 2
            candidates = [f for f, _ in ranked_files[:mid]]
            est = _estimate(candidates)
            if est <= self.budget:
                best = candidates
                lo = mid + 1
            else:
                hi = mid - 1

        return best
