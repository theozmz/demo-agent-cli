"""Web fetch tool — fetch a URL and convert to markdown."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement

logger = logging.getLogger(__name__)

# Lightweight HTML-to-text without external deps
try:
    import httpx

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    from markdownify import markdownify as md

    _HAS_MARKDOWNIFY = True
except ImportError:
    _HAS_MARKDOWNIFY = False


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch a URL and return its content as markdown. "
        "Fails on authenticated/private URLs. HTTP is upgraded to HTTPS."
    )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
            },
            "required": ["url"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if not _HAS_HTTPX:
            return ToolOutput(content="Error: httpx not installed. Run: pip install httpx", is_error=True)

        url = params["url"]
        parsed = urlparse(url)
        if parsed.scheme == "http":
            url = url.replace("http://", "https://", 1)

        # Safety: block local/private hosts
        hostname = parsed.hostname or ""
        if hostname in ("localhost", "127.0.0.1", "::1") or hostname.startswith("192.168.") or hostname.startswith("10."):
            return ToolOutput(content="Error: requests to local/private networks are blocked.", is_error=True)

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Harness/0.1 (+https://github.com/anthropics/claude-code)"},
                )
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            logger.debug("Web fetch failed: %s", e)
            return ToolOutput(content=f"Error fetching URL: {e}", is_error=True)

        if _HAS_MARKDOWNIFY:
            try:
                text = md(html, heading_style="ATX")
            except Exception:
                text = self._strip_html(html)
        else:
            text = self._strip_html(html)

        max_len = 50000
        if len(text) > max_len:
            text = text[:max_len] + f"\n\n... (truncated at {max_len} chars)"

        return ToolOutput(content=text)

    @staticmethod
    def _strip_html(html: str) -> str:
        """Minimal HTML tag stripper (fallback when markdownify is unavailable)."""
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        return text.strip()
