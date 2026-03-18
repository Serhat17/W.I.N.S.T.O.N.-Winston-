"""
Web Search Skill - Search the web using DuckDuckGo (no API key needed).
"""

import logging
from winston.skills.base import BaseSkill, SkillResult
from winston.security.content_wrapper import wrap_search_results

logger = logging.getLogger("winston.skills.web_search")

# Check at import time so missing dependency is visible in startup logs.
# Prefer 'ddgs' (current package name) over 'duckduckgo_search' (legacy).
_HAS_DDGS = False
try:
    from ddgs import DDGS as _DDGS  # noqa: F401
    _HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS as _DDGS  # noqa: F401
        _HAS_DDGS = True
    except ImportError:
        pass

if not _HAS_DDGS:
    logger.warning(
        "duckduckgo-search not installed — web_search skill will be unavailable. "
        "Fix: pip install duckduckgo-search"
    )


class WebSearchSkill(BaseSkill):
    """Search the web for information."""

    name = "web_search"
    description = (
        "Search the web for information, news, or answers. "
        "Use this when the user asks you to look up something, "
        "find information, or needs current/real-time data."
    )
    parameters = {
        "query": "The search query",
        "max_results": "Maximum number of results (default: 5)",
    }

    def execute(self, **kwargs) -> SkillResult:
        """Search the web using DuckDuckGo."""
        query = kwargs.get("query", "")
        max_results = int(kwargs.get("max_results", 5))

        if not query:
            return SkillResult(success=False, message="No search query provided.")

        if not _HAS_DDGS:
            logger.error("web_search called but duckduckgo-search is not installed")
            return SkillResult(
                success=False,
                message="Web search not available. Install with: pip install duckduckgo-search",
            )

        try:
            ddgs = _DDGS()
            results = list(ddgs.text(query, max_results=max_results))

            if not results:
                return SkillResult(
                    success=True,
                    message=f"No results found for '{query}'.",
                )

            # Wrap results with external content boundaries for prompt injection defense
            response = wrap_search_results(results, query)

            return SkillResult(
                success=True,
                message=response,
                data=results,
                speak=False,  # Don't speak search results, too long
            )

        except Exception as e:
            logger.error(f"Web search error: {e}")
            return SkillResult(
                success=False,
                message=f"Search failed: {str(e)}",
            )
