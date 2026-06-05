"""tree-sitter tag extraction — functions, classes, methods, interfaces."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Language -> (tree_sitter_language_pack import, file extensions)
_SUPPORTED: dict[str, tuple[str, tuple[str, ...]]] = {
    "python": ("tree_sitter_language_pack", (".py",)),
    "typescript": ("tree_sitter_language_pack", (".ts", ".tsx")),
    "rust": ("tree_sitter_language_pack", (".rs",)),
    "go": ("tree_sitter_language_pack", (".go",)),
    "java": ("tree_sitter_language_pack", (".java",)),
}

# tree-sitter node types to extract per language
_TAG_KINDS: dict[str, dict[str, list[str]]] = {
    "python": {
        "function": ["function_definition"],
        "class": ["class_definition"],
    },
    "typescript": {
        "function": ["function_declaration", "arrow_function", "function_expression"],
        "class": ["class_declaration"],
        "method": ["method_definition"],
        "interface": ["interface_declaration"],
    },
    "rust": {
        "function": ["function_item"],
        "struct": ["struct_item"],
        "trait": ["trait_item"],
        "impl": ["impl_item"],
        "enum": ["enum_item"],
    },
    "go": {
        "function": ["function_declaration", "method_declaration"],
        "type": ["type_declaration"],
        "interface": ["interface_type"],
    },
    "java": {
        "function": ["method_declaration"],
        "class": ["class_declaration"],
        "interface": ["interface_declaration"],
    },
}


class Tag(NamedTuple):
    path: str
    line: int
    kind: str  # "function", "class", "method", etc.
    name: str
    signature: str  # first line of the definition


class TagExtractor:
    """Extract code structure tags using tree-sitter."""

    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        self._parsers: dict[str, object] = {}

    def _parser_for(self, lang: str):
        if lang in self._parsers:
            return self._parsers[lang]
        try:
            from tree_sitter_language_pack import get_parser
            parser = get_parser(lang)
            self._parsers[lang] = parser
        except Exception as e:
            logger.debug("tree-sitter init failed for %s: %s", lang, e)
            return None
        return self._parsers[lang]

    def _detect_language(self, file_path: Path) -> str | None:
        suffix = file_path.suffix.lower()
        for lang, (_, exts) in _SUPPORTED.items():
            if suffix in exts:
                return lang
        return None

    def extract(self, file_path: Path) -> list[Tag]:
        lang = self._detect_language(file_path)
        if lang is None:
            return []

        parser = self._parser_for(lang)
        if parser is None:
            return []

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

        tree = parser.parse(source)
        kinds = _TAG_KINDS.get(lang, {})
        tags: list[Tag] = []

        rel = str(file_path.relative_to(self._root))

        def _walk(node, depth=0):
            if depth > 80:  # safety limit
                return
            for kind_label, kind_names in kinds.items():
                if node.kind() in kind_names:
                    name = self._extract_name(node, source)
                    if name:
                        sig = source[node.start_byte():node.end_byte()].split("\n")[0].strip()[:120]
                        tags.append(Tag(rel, node.start_position().row + 1, kind_label, name, sig))
            for i in range(node.child_count()):
                _walk(node.child(i), depth + 1)

        _walk(tree.root_node())
        return tags

    @staticmethod
    def _extract_name(node, source: str) -> str | None:
        name_child = node.child_by_field_name("name")
        if name_child:
            return source[name_child.start_byte():name_child.end_byte()]
        # Fallback: search for identifier child
        for i in range(node.child_count()):
            child = node.child(i)
            if child.kind() == "identifier":
                return source[child.start_byte():child.end_byte()]
        return None

    def extract_all(self, paths: list[Path] | None = None) -> dict[str, list[Tag]]:
        if paths is None:
            paths = sorted(self._root.rglob("*"))

        result: dict[str, list[Tag]] = {}
        for p in paths:
            if p.is_file() and self._detect_language(p):
                tags = self.extract(p)
                if tags:
                    result[str(p.relative_to(self._root))] = tags
        return result
