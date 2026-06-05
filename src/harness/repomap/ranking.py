"""PageRank-based file ranking for RepoMap token budget allocation."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import networkx as nx

    _HAS_NETWORKX = True
except ImportError:
    _HAS_NETWORKX = False


class RepoMapRanker:
    """Rank files by importance using a dependency graph + PageRank.

    Edges are inferred from imports and cross-file references found
    in tree-sitter tags.
    """

    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        self._graph: "nx.DiGraph | None" = None if _HAS_NETWORKX else None

    def build_graph(self, files: list[str]) -> None:
        """Build a directed graph from import/reference relationships.

        *files* are relative paths as extracted by TagExtractor.
        """
        if not _HAS_NETWORKX:
            self._graph = None
            return

        g = nx.DiGraph()
        basename_to_path: dict[str, list[str]] = {}

        for f in files:
            g.add_node(f, weight=1.0)
            bn = Path(f).stem
            basename_to_path.setdefault(bn, []).append(f)

        # Add edges: file A references file B if B's basename appears in A's path
        for f in files:
            parts = set(Path(f).stem.replace("_", " ").replace("-", " ").split())
            for bn, paths in basename_to_path.items():
                if bn != Path(f).stem and bn.lower() in {p.lower() for p in parts}:
                    for target in paths:
                        if target != f:
                            g.add_edge(f, target)

        self._graph = g

    def rank(self, files: list[str]) -> list[tuple[str, float]]:
        """Return files sorted by PageRank score (descending)."""
        if not _HAS_NETWORKX or self._graph is None:
            # No networkx: return files in alphabetical order
            return [(f, 0.0) for f in sorted(files)]

        if len(files) <= 1:
            return [(f, 1.0) for f in files]

        self.build_graph(files)

        try:
            scores = nx.pagerank(self._graph, alpha=0.85, max_iter=50)
        except Exception:
            return [(f, 0.0) for f in sorted(files)]

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked
