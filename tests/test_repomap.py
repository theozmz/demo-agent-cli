"""Tests for the RepoMap system — tag extraction, ranking, caching, budget."""

import os
import time
from pathlib import Path

import pytest

from harness.repomap.tags import TagExtractor, Tag
from harness.repomap.ranking import RepoMapRanker
from harness.repomap.cache import TagCache, TokenBudgetOptimizer


# ===================================================================
# Test data
# ===================================================================
SAMPLE_PY = '''
"""A sample module."""

import os
from pathlib import Path


class Calculator:
    """Simple calculator class."""

    def add(self, a: int, b: int) -> int:
        return a + b

    def subtract(self, a: int, b: int) -> int:
        return a - b


def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


class AdvancedCalc(Calculator):
    """Advanced calculator with more operations."""

    def power(self, base: int, exp: int) -> int:
        return base ** exp
'''

SAMPLE_PY_NO_CLASSES = '''
def hello():
    return "hello"

def goodbye():
    return "goodbye"

async def fetch_data(url: str) -> dict:
    return {}
'''


# ===================================================================
# TestTagExtractor
# ===================================================================
class TestTagExtractor:
    def test_extract_python_classes_and_functions(self, tmp_path: Path):
        py_file = tmp_path / "sample.py"
        py_file.write_text(SAMPLE_PY, encoding="utf-8")
        extractor = TagExtractor(root=str(tmp_path))

        tags = extractor.extract(py_file)
        assert len(tags) > 0

        tag_names = {t.name for t in tags}
        assert "Calculator" in tag_names
        assert "AdvancedCalc" in tag_names
        assert "multiply" in tag_names

        for t in tags:
            assert isinstance(t, Tag)
            assert t.line > 0
            assert t.signature != ""

    def test_extract_functions_only(self, tmp_path: Path):
        py_file = tmp_path / "no_classes.py"
        py_file.write_text(SAMPLE_PY_NO_CLASSES, encoding="utf-8")
        extractor = TagExtractor(root=str(tmp_path))

        tags = extractor.extract(py_file)
        tag_names = {t.name for t in tags}
        assert "hello" in tag_names
        assert "goodbye" in tag_names
        assert "fetch_data" in tag_names

    def test_extract_unsupported_extension(self, tmp_path: Path):
        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("hello world", encoding="utf-8")
        extractor = TagExtractor(root=str(tmp_path))

        tags = extractor.extract(txt_file)
        assert tags == []

    def test_extract_empty_file(self, tmp_path: Path):
        empty = tmp_path / "empty.py"
        empty.write_text("", encoding="utf-8")
        extractor = TagExtractor(root=str(tmp_path))

        tags = extractor.extract(empty)
        assert tags == []


# ===================================================================
# TestRepoMapRanker
# ===================================================================
class TestRepoMapRanker:
    def test_rank_with_files(self, tmp_path: Path):
        ranker = RepoMapRanker(root=str(tmp_path))
        files = ["a.py", "b.py", "c.py"]
        ranked = ranker.rank(files)
        assert len(ranked) == 3
        paths = [p for p, _ in ranked]
        assert "a.py" in paths
        assert "b.py" in paths

    def test_rank_empty_list(self, tmp_path: Path):
        ranker = RepoMapRanker(root=str(tmp_path))
        ranked = ranker.rank([])
        assert ranked == []

    def test_rank_single_file(self, tmp_path: Path):
        ranker = RepoMapRanker(root=str(tmp_path))
        ranked = ranker.rank(["only.py"])
        assert len(ranked) == 1
        # Without networkx, score is 0.0; with networkx it's 1.0
        assert ranked[0][1] in (0.0, 1.0)


# ===================================================================
# TestTagCache
# ===================================================================
class TestTagCache:
    def test_cache_miss_then_hit(self, tmp_path: Path):
        cache = TagCache()
        extractor = TagExtractor(root=str(tmp_path))

        py_file = tmp_path / "cached.py"
        py_file.write_text("def foo(): pass", encoding="utf-8")
        mtime = os.path.getmtime(str(py_file))

        # First access: cache miss
        cached = cache.get(str(py_file), mtime)
        assert cached is None

        tags = extractor.extract(py_file)
        cache.set(str(py_file), mtime, tags)

        # Second access: cache hit
        cached2 = cache.get(str(py_file), mtime)
        assert cached2 is not None
        assert len(cached2) == len(tags)

    def test_cache_miss_on_mtime_change(self, tmp_path: Path):
        cache = TagCache()
        extractor = TagExtractor(root=str(tmp_path))

        py_file = tmp_path / "changing.py"
        py_file.write_text("def old(): pass", encoding="utf-8")
        mtime1 = os.path.getmtime(str(py_file))
        tags1 = extractor.extract(py_file)
        cache.set(str(py_file), mtime1, tags1)

        # Modify file
        time.sleep(0.01)
        py_file.write_text("def new(): pass\ndef extra(): pass", encoding="utf-8")
        mtime2 = os.path.getmtime(str(py_file))

        cached = cache.get(str(py_file), mtime2)
        assert cached is None  # mtime changed → cache miss

    def test_cache_invalidate(self, tmp_path: Path):
        cache = TagCache()
        py_file = tmp_path / "inv.py"
        py_file.write_text("def x(): pass", encoding="utf-8")
        mtime = os.path.getmtime(str(py_file))
        cache.set(str(py_file), mtime, [])
        assert cache.size == 1
        cache.invalidate(str(py_file))
        assert cache.size == 0

    def test_cache_clear(self, tmp_path: Path):
        cache = TagCache()
        for i in range(3):
            f = tmp_path / f"f{i}.py"
            f.write_text("pass", encoding="utf-8")
            cache.set(str(f), os.path.getmtime(str(f)), [])
        assert cache.size == 3
        cache.clear()
        assert cache.size == 0


# ===================================================================
# TestTokenBudgetOptimizer
# ===================================================================
class TestTokenBudgetOptimizer:
    def test_selects_files_under_budget(self):
        optimizer = TokenBudgetOptimizer(budget=100)
        # Small tags with short signatures → should fit many
        all_tags = {
            "a.py": [Tag("a.py", 1, "function", "f1", "def f1(): pass")],
            "b.py": [Tag("b.py", 1, "function", "f2", "def f2(): pass")],
        }
        ranked = [("a.py", 0.8), ("b.py", 0.5)]
        selected = optimizer.select(ranked, all_tags)
        assert len(selected) == 2  # both should fit under 100 token budget

    def test_respects_tight_budget(self):
        optimizer = TokenBudgetOptimizer(budget=5)
        all_tags = {
            f"mod{i}.py": [
                Tag(f"mod{i}.py", j, "function",
                    f"func{j}", f"def func{j}(a: int, b: str, c: float) -> dict: pass")
                for j in range(3)
            ]
            for i in range(10)
        }
        ranked = [(f"mod{i}.py", 1.0 - i * 0.01) for i in range(10)]
        selected = optimizer.select(ranked, all_tags)
        # With budget=5 (very tight), should select only a subset
        assert len(selected) < len(all_tags)

    def test_empty_input(self):
        optimizer = TokenBudgetOptimizer(budget=100)
        selected = optimizer.select([], {})
        assert selected == []


# ===================================================================
# TestRepoMapIntegration
# ===================================================================
class TestRepoMapIntegration:
    def test_build_on_small_dir(self, tmp_path: Path):
        """RepoMap should build successfully on a small Python project."""
        from harness.repomap.repomap import RepoMap

        # Create a mini project
        (tmp_path / "mylib").mkdir()
        (tmp_path / "mylib" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "mylib" / "core.py").write_text(SAMPLE_PY, encoding="utf-8")

        repomap = RepoMap(root=str(tmp_path), max_tokens=5000)
        result = repomap.build()
        assert result is not None
        assert len(result) > 0

    def test_disabled_by_default_in_config(self):
        from harness.config.config import Config
        config = Config()
        assert config.repomap.enabled is False
