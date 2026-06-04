"""Prompt injection detection — Aho-Corasick multi-pattern matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import ahocorasick


# Injection patterns from IronClaw's sanitizer
INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous",
    "system:",
    "you are now",
    "new instructions:",
    "<|im_start|>",
    "<|im_end|>",
    "[INST]",
    "[/INST]",
    "I'm an AI assistant",
    "as an AI language model",
    "pretend you are",
    "act as if",
    "override",
    "disregard",
]


class Sanitizer:
    """Fast multi-pattern injection detection using Aho-Corasick automaton."""

    def __init__(self, patterns: list[str] | None = None):
        patterns = patterns or INJECTION_PATTERNS
        self._automaton = ahocorasick.Automaton()
        for idx, pattern in enumerate(patterns):
            self._automaton.add_word(pattern.lower(), (idx, pattern))
        self._automaton.make_automaton()

    def scan(self, text: str) -> list[str]:
        """Return list of matched injection patterns found in text."""
        if not text:
            return []
        lower = text.lower()
        matches: list[str] = []
        for end_idx, (_, pattern) in self._automaton.iter(lower):
            matches.append(pattern)
        return matches

    def is_safe(self, text: str) -> bool:
        """Return True if no injection patterns detected."""
        return len(self.scan(text)) == 0
