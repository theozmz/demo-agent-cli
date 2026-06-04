"""Permission policy — determines whether a tool call is authorized."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from harness.tools.tool import ApprovalRequirement


class PermissionOutcome(Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


@dataclass
class ApprovalContext:
    """Context for permission decisions."""

    allowed_tools: set[str] = field(default_factory=set)
    is_interactive: bool = False
    auto_approve: bool = False

    @classmethod
    def autonomous(cls) -> "ApprovalContext":
        """Fully autonomous — all tools auto-approved."""
        return cls(auto_approve=True)

    @classmethod
    def interactive(cls) -> "ApprovalContext":
        """Interactive mode — high-risk tools need user approval."""
        return cls(is_interactive=True)

    def is_tool_blocked(self, tool_name: str) -> bool:
        """Check if a tool is explicitly blocked."""
        return len(self.allowed_tools) > 0 and tool_name not in self.allowed_tools


class PermissionPolicy:
    """
    Maps ApprovalRequirement + ApprovalContext → PermissionOutcome.

    Decision rules:
    - NEVER → ALLOW (always)
    - ALWAYS → NEEDS_APPROVAL (or DENY if not interactive)
    - UNLESS_AUTO → ALLOW if auto_approve, else NEEDS_APPROVAL
    """

    def authorize(
        self,
        tool_name: str,
        requirement: ApprovalRequirement,
        ctx: ApprovalContext,
    ) -> PermissionOutcome:
        # Block check first
        if ctx.is_tool_blocked(tool_name):
            return PermissionOutcome.DENY

        match requirement:
            case ApprovalRequirement.NEVER:
                return PermissionOutcome.ALLOW
            case ApprovalRequirement.ALWAYS:
                if ctx.auto_approve or ctx.is_interactive:
                    return PermissionOutcome.NEEDS_APPROVAL
                return PermissionOutcome.DENY
            case ApprovalRequirement.UNLESS_AUTO:
                if ctx.auto_approve:
                    return PermissionOutcome.ALLOW
                return PermissionOutcome.NEEDS_APPROVAL
