"""Credential and secret leak detection — regex-based scanning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class LeakSeverity(Enum):
    WARN = "warn"
    REDACT = "redact"
    BLOCK = "block"


# Regex patterns for common secret types
LEAK_PATTERNS: dict[str, tuple[str, LeakSeverity]] = {
    "anthropic_api_key": (r"sk-ant-[a-zA-Z0-9_-]{20,}", LeakSeverity.BLOCK),
    "openai_api_key": (r"sk-[a-zA-Z0-9]{20,}", LeakSeverity.BLOCK),
    "aws_access_key": (r"AKIA[0-9A-Z]{16}", LeakSeverity.BLOCK),
    "aws_secret_key": (r"(?i)aws.{0,10}secret.{0,10}[0-9a-zA-Z/+]{40}", LeakSeverity.BLOCK),
    "github_token": (r"gh[pousr]_[A-Za-z0-9_]{20,}", LeakSeverity.BLOCK),
    "private_key_pem": (r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", LeakSeverity.BLOCK),
    "google_api_key": (r"AIza[0-9A-Za-z_-]{35}", LeakSeverity.BLOCK),
    "slack_token": (r"xox[abps]-[0-9A-Za-z_-]{10,}", LeakSeverity.BLOCK),
    "gitlab_token": (r"glpat-[0-9A-Za-z_-]{20,}", LeakSeverity.BLOCK),
    "jwt_token": (r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", LeakSeverity.REDACT),
    "generic_api_key": (r"(?i)(api[_-]?key|apikey|secret|token|password|auth)\s*[:=]\s*['\"]?[^\s'\"]{8,}['\"]?", LeakSeverity.REDACT),
}

REDACT_PLACEHOLDER = "[REDACTED]"


@dataclass
class LeakFinding:
    """A detected secret leak."""

    secret_type: str
    severity: LeakSeverity
    match: str
    position: int = 0


class LeakDetector:
    """Scans text for credential leaks using regex patterns."""

    def __init__(self, patterns: dict | None = None):
        self._patterns = patterns or LEAK_PATTERNS
        self._compiled: dict[str, tuple[re.Pattern, LeakSeverity]] = {}
        for name, (regex, severity) in self._patterns.items():
            self._compiled[name] = (re.compile(regex), severity)

    def scan(self, text: str) -> list[LeakFinding]:
        """Return all detected leaks in text."""
        if not text:
            return []
        findings: list[LeakFinding] = []
        for name, (pattern, severity) in self._compiled.items():
            for match in pattern.finditer(text):
                findings.append(LeakFinding(
                    secret_type=name,
                    severity=severity,
                    match=match.group(),
                    position=match.start(),
                ))
        return findings

    def redact(self, text: str) -> tuple[str, list[LeakFinding]]:
        """Return text with detected secrets replaced by [REDACTED]."""
        findings = self.scan(text)
        result = text
        # Replace in reverse order to preserve positions
        for f in sorted(findings, key=lambda x: x.position, reverse=True):
            if f.severity in (LeakSeverity.REDACT, LeakSeverity.BLOCK):
                result = result[:f.position] + REDACT_PLACEHOLDER + result[f.position + len(f.match):]
        return result, findings

    def has_blocked(self, findings: list[LeakFinding]) -> bool:
        return any(f.severity == LeakSeverity.BLOCK for f in findings)
