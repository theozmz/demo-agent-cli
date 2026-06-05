"""RepoMap — produces a token-budgeted code map for the system prompt."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from harness.repomap.tags import TagExtractor, Tag
from harness.repomap.ranking import RepoMapRanker
from harness.repomap.cache import TagCache, TokenBudgetOptimizer

logger = logging.getLogger(__name__)

_EXCLUDE_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache",
    "dist", "build", "eggs", ".eggs", "target",  # Rust
})


class RepoMap:
    """Assembles a ranked, token-budgeted repository map for the context.

    Usage::

        repomap = RepoMap(root=".")
        text = repomap.build()
        # returns markdown like:
        # ## src/harness/core/loop.py
        # - AgenticLoop (class, L86)
        # - run (method, L98)
    """

    def __init__(self, root: str | Path, max_tokens: int = 4000):
        self._root = Path(root).resolve()
        self._max_tokens = max_tokens
        self._extractor = TagExtractor(self._root)
        self._ranker = RepoMapRanker(self._root)
        self._cache = TagCache()
        self._optimizer = TokenBudgetOptimizer(budget=max_tokens)

    def _collect_files(self) -> list[Path]:
        """Walk the repo, skipping excluded directories."""
        files: list[Path] = []
        for p in sorted(self._root.rglob("*")):
            if p.is_file() and not any(d in p.parts for d in _EXCLUDE_DIRS):
                files.append(p)
        return files

    def build(self) -> str:
        """Build the repo map text (cached tags where possible)."""
        start = time.monotonic()
        all_files = self._collect_files()
        all_tags: dict[str, list[Tag]] = {}
        cached = 0
        parsed = 0

        for fp in all_files:
            rel = str(fp.relative_to(self._root))
            mtime = fp.stat().st_mtime
            tags = self._cache.get(rel, mtime)
            if tags is not None:
                cached += 1
                all_tags[rel] = tags
            else:
                tags = self._extractor.extract(fp)
                if tags:
                    parsed += 1
                    self._cache.set(rel, mtime, tags)
                    all_tags[rel] = tags

        file_list = list(all_tags.keys())
        ranked = self._ranker.rank(file_list)
        selected = self._optimizer.select(ranked, all_tags)

        # Render
        lines: list[str] = [f"## Repository Map ({len(selected)} files)\n"]
        for path in selected:
            tags = all_tags.get(path, [])
            lines.append(f"### {path}")
            for tag in tags:
                lines.append(f"- {tag.kind}: `{tag.name}` (L{tag.line})")
            lines.append("")

        elapsed = (time.monotonic() - start) * 1000
        logger.debug(
            "RepoMap built: %d files total, %d parsed, %d cached, %d selected, %.0fms",
            len(all_files), parsed, cached, len(selected), elapsed,
        )

        return "\n".join(lines)

    def refresh_if_stale(self, stale_seconds: int = 60) -> str | None:
        """Rebuild only if stale (faster than full build)."""
        # Simple implementation: always build (caching handles per-file staleness)
        return self.build()
