"""SafetyLayer — unified safety pipeline composing sanitizer + leak detector."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from harness.safety.sanitizer import Sanitizer
from harness.safety.leak_detector import LeakDetector, LeakFinding

logger = logging.getLogger(__name__)


@dataclass
class SafetyResult:
    """Result of a safety scan."""

    passed: bool = True
    blocked: bool = False
    redacted: bool = False
    content: str = ""
    reason: str = ""
    findings: list[LeakFinding] = field(default_factory=list)


class SafetyLayer:
    """
    Composes sanitizer and leak detector into a unified pipeline.

    Pipeline: scan_injection → scan_leaks → redact → return
    """

    def __init__(self):
        self.sanitizer = Sanitizer()
        self.leak_detector = LeakDetector()

    def scan_input(self, text: str) -> SafetyResult:
        """Scan user input for injection attempts and secret leaks."""
        injection_matches = self.sanitizer.scan(text)
        if injection_matches:
            logger.warning(f"Injection patterns detected: {injection_matches}")
            return SafetyResult(
                passed=False,
                blocked=True,
                reason=f"Injection patterns: {injection_matches}",
                content="",
            )
        # Also scan input for leaked secrets
        redacted, findings = self.leak_detector.redact(text)
        if self.leak_detector.has_blocked(findings):
            blocked_types = [f.secret_type for f in findings if f.severity.name == "BLOCK"]
            logger.warning(f"Blocked secret types in input: {blocked_types}")
            return SafetyResult(
                passed=False,
                blocked=True,
                reason=f"Secret leak detected: {blocked_types}",
                content="",
                findings=findings,
            )
        if findings:
            return SafetyResult(redacted=True, content=redacted, findings=findings)
        return SafetyResult(content=text)

    def scan_output(self, text: str, tool_name: str = "") -> SafetyResult:
        """Scan tool output for leaked secrets and injection attempts."""
        # Scan for injection patterns in output too
        injection_matches = self.sanitizer.scan(text)
        if injection_matches:
            logger.warning(f"Injection patterns in {tool_name} output: {injection_matches}")
            return SafetyResult(
                passed=False,
                blocked=True,
                reason=f"Injection patterns in output: {injection_matches}",
                content="",
            )
        redacted, findings = self.leak_detector.redact(text)

        if self.leak_detector.has_blocked(findings):
            blocked_types = [f.secret_type for f in findings if f.severity.name == "BLOCK"]
            logger.warning(f"Blocked secret types in {tool_name} output: {blocked_types}")
            return SafetyResult(
                passed=False,
                blocked=True,
                reason=f"Secret leak detected: {blocked_types}",
                content=redacted,
                findings=findings,
            )

        if findings:
            logger.info(f"Redacted {len(findings)} secrets in {tool_name} output")
            return SafetyResult(redacted=True, content=redacted, findings=findings)

        return SafetyResult(content=text)
