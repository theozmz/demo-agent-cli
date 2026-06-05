"""Web search tool — search the web and return results."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement

logger = logging.getLogger(__name__)

try:
    import httpx

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web via DuckDuckGo HTML (no API key required). "
        "Returns result blocks with titles, snippets, and URLs."
    )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 2,
                    "description": "The search query",
                },
            },
            "required": ["query"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if not _HAS_HTTPX:
            return ToolOutput(content="Error: httpx not installed. Run: pip install httpx", is_error=True)

        query = params["query"]
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Harness/0.1"},
                )
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            logger.debug("Web search failed: %s", e)
            return ToolOutput(content=f"Error searching: {e}", is_error=True)

        results = self._parse_ddg_html(html)
        if not results:
            return ToolOutput(content="(no results)")

        lines = []
        for i, r in enumerate(results[:20], 1):
            lines.append(f"{i}. **{r['title']}**")
            lines.append(f"   {r['snippet']}")
            lines.append(f"   {r['url']}")
            lines.append("")

        return ToolOutput(content="\n".join(lines))

    @staticmethod
    def _parse_ddg_html(html: str) -> list[dict]:
        """Extract search results from DuckDuckGo HTML."""
        import re

        results = []
        # Match DDG result blocks: <a class="result__a">title</a> + <a class="result__snippet">snippet</a>
        blocks = re.split(r"<div class=\"result", html)
        for block in blocks[1:]:
            title_m = re.search(r'class="result__a"[^>]*>(.+?)</a>', block)
            snippet_m = re.search(r'class="result__snippet"[^>]*>(.+?)</a>', block)
            url_m = re.search(r'class="result__url"[^>]*>(.+?)</a>', block)
            if title_m:
                results.append({
                    "title": re.sub(r"<[^>]+>", "", title_m.group(1)).strip(),
                    "snippet": re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else "",
                    "url": re.sub(r"<[^>]+>", "", url_m.group(1)).strip() if url_m else "",
                })
        return results
