"""
Web Fetch Skill — Lightweight HTTP page fetching with intelligent content extraction.

Replaces the need to spin up a full Chromium browser for simple page reads.
Uses trafilatura for article extraction (like Mozilla Readability.js) with
BeautifulSoup as fallback.  Results are cached for 15 minutes.

Extract modes:
  - article  : Main article content, stripped of nav/ads/footer (default)
  - text     : All visible text on the page (BeautifulSoup get_text)
  - links    : All hyperlinks with their text labels
  - metadata : Title, description, OG tags, JSON-LD structured data
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from winston.skills.base import BaseSkill, SkillResult
from winston.utils.web_cache import cache_get, cache_set
from winston.utils.retry import retry_call, HTTP_POLICY
from winston.security.content_wrapper import wrap_external_content
from winston.security.ssrf_guard import validate_url, SSRFError

logger = logging.getLogger("winston.skills.web_fetch")

# ── Optional dependency: trafilatura ──────────────────────────────────
_HAS_TRAFILATURA = False
try:
    import trafilatura  # noqa: F401
    _HAS_TRAFILATURA = True
except ImportError:
    logger.warning(
        "trafilatura not installed — web_fetch will fall back to BeautifulSoup. "
        "For better article extraction: pip install trafilatura"
    )

# Browser-like headers to avoid bot detection
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

_VALID_MODES = ("article", "text", "links", "metadata")
_DEFAULT_MAX_CHARS = 50_000
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB download limit
_REQUEST_TIMEOUT = 20  # seconds


class WebFetchSkill(BaseSkill):
    """Fetch and extract content from web pages without a browser."""

    name = "web_fetch"
    description = (
        "Fetch a web page and extract its readable content using a lightweight HTTP request "
        "(no browser needed). Use this for reading articles, documentation, blog posts, "
        "API responses, or any public web page. Much faster than the browser skill. "
        "Use the browser skill only when you need to interact with a page (click, fill forms, login)."
    )
    parameters = {
        "url": "The URL to fetch (required)",
        "extract_mode": "What to extract: 'article' (clean article text, default), 'text' (all visible text), 'links' (all hyperlinks), 'metadata' (title, description, OG tags)",
        "max_chars": "Maximum characters to return (default: 50000)",
    }

    def execute(self, **kwargs) -> SkillResult:
        url = kwargs.get("url", "").strip()
        mode = kwargs.get("extract_mode", "article").strip().lower()
        max_chars = int(kwargs.get("max_chars", _DEFAULT_MAX_CHARS))

        # ── Validate inputs ──
        if not url:
            return SkillResult(success=False, message="No URL provided. Please specify a URL to fetch.")

        # Auto-add scheme if missing
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)
        if not parsed.hostname:
            return SkillResult(success=False, message=f"Invalid URL: {url}")

        if mode not in _VALID_MODES:
            return SkillResult(
                success=False,
                message=f"Invalid extract_mode '{mode}'. Choose from: {', '.join(_VALID_MODES)}",
            )

        # ── SSRF check ──
        try:
            validate_url(url)
        except SSRFError as e:
            logger.warning(f"SSRF blocked: {e}")
            return SkillResult(success=False, message=f"Blocked URL for security reasons: {e}")

        # ── Check cache ──
        cache_key = f"{url}|{mode}"
        cached = cache_get(cache_key)
        if cached is not None:
            truncated = len(cached) > max_chars
            content = cached[:max_chars]
            wrapped = wrap_external_content(content, source="web_fetch", url=url, extra_meta={"Mode": mode})
            return SkillResult(
                success=True,
                message=wrapped,
                data={"url": url, "mode": mode, "cached": True, "truncated": truncated, "chars": len(content)},
                speak=False,
            )

        # ── Fetch page (with retry) ──
        html, final_url, status, error = retry_call(self._fetch, url, policy=HTTP_POLICY)
        if error:
            return SkillResult(success=False, message=error)

        # ── Extract content based on mode ──
        if mode == "article":
            content = self._extract_article(html, url)
        elif mode == "text":
            content = self._extract_text(html)
        elif mode == "links":
            content = self._extract_links(html, final_url or url)
        elif mode == "metadata":
            content = self._extract_metadata(html, final_url or url)
        else:
            content = self._extract_article(html, url)

        if not content or not content.strip():
            # Fallback: if article extraction returned nothing, try plain text
            if mode == "article":
                content = self._extract_text(html)
            if not content or not content.strip():
                return SkillResult(
                    success=False,
                    message=f"Could not extract content from {url}. The page may be empty, require JavaScript, or block automated access.",
                )

        # ── Cache and return ──
        cache_set(cache_key, content)

        truncated = len(content) > max_chars
        content = content[:max_chars]

        # Wrap in external content boundary for prompt injection defense
        wrapped = wrap_external_content(
            content,
            source="web_fetch",
            url=final_url or url,
            extra_meta={"Mode": mode},
        )

        return SkillResult(
            success=True,
            message=wrapped,
            data={
                "url": final_url or url,
                "mode": mode,
                "cached": False,
                "truncated": truncated,
                "chars": len(content),
                "status": status,
            },
            speak=False,
        )

    # ── HTTP fetch ────────────────────────────────────────────────────

    def _fetch(self, url: str) -> tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
        """
        GET a URL and return (html, final_url, status_code, error_message).
        On success error_message is None; on failure html is None.
        """
        try:
            resp = requests.get(
                url,
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )

            # Check content length before downloading
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
                resp.close()
                return None, None, None, f"Page too large ({int(content_length) // 1024 // 1024}MB). Max is {_MAX_RESPONSE_BYTES // 1024 // 1024}MB."

            # Read with size limit
            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536, decode_unicode=False):
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    resp.close()
                    break
                chunks.append(chunk)

            raw_bytes = b"".join(chunks)

            # Detect encoding
            encoding = resp.encoding or "utf-8"
            try:
                html = raw_bytes.decode(encoding, errors="replace")
            except (LookupError, UnicodeDecodeError):
                html = raw_bytes.decode("utf-8", errors="replace")

            final_url = str(resp.url) if resp.url != url else None
            status = resp.status_code

            if status >= 400:
                return None, final_url, status, f"HTTP {status} error fetching {url}"

            return html, final_url, status, None

        except requests.exceptions.Timeout:
            return None, None, None, f"Timeout after {_REQUEST_TIMEOUT}s fetching {url}"
        except requests.exceptions.ConnectionError:
            return None, None, None, f"Could not connect to {url}"
        except requests.exceptions.TooManyRedirects:
            return None, None, None, f"Too many redirects for {url}"
        except Exception as e:
            logger.error(f"Fetch error for {url}: {e}")
            return None, None, None, f"Failed to fetch {url}: {str(e)}"

    # ── Content extractors ────────────────────────────────────────────

    def _extract_article(self, html: str, url: str) -> str:
        """Extract the main article content. trafilatura first, BS4 fallback."""
        # Strategy 1: trafilatura (best quality)
        if _HAS_TRAFILATURA:
            try:
                result = trafilatura.extract(
                    html,
                    url=url,
                    include_links=True,
                    include_tables=True,
                    include_comments=False,
                    output_format="txt",
                    favor_recall=True,
                )
                if result and len(result.strip()) > 100:
                    return result.strip()
            except Exception as e:
                logger.warning(f"trafilatura extraction failed: {e}")

        # Strategy 2: BeautifulSoup article extraction
        return self._extract_article_bs4(html)

    def _extract_article_bs4(self, html: str) -> str:
        """Fallback article extraction using BeautifulSoup heuristics."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove noise elements
        for tag in soup.find_all(["script", "style", "noscript", "nav", "header", "footer", "aside", "iframe"]):
            tag.decompose()

        # Try <article> tag first
        article = soup.find("article")
        if article:
            text = article.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return self._clean_text(text)

        # Try <main> tag
        main = soup.find("main")
        if main:
            text = main.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return self._clean_text(text)

        # Try common content divs
        for selector in [
            {"class_": re.compile(r"article|post-content|entry-content|content-body|main-content", re.I)},
            {"id": re.compile(r"article|content|main|post", re.I)},
            {"role": "main"},
        ]:
            el = soup.find("div", **selector)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return self._clean_text(text)

        # Last resort: body text
        body = soup.find("body")
        if body:
            return self._clean_text(body.get_text(separator="\n", strip=True))

        return ""

    def _extract_text(self, html: str) -> str:
        """Extract all visible text from the page."""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return self._clean_text(text)

    def _extract_links(self, html: str, base_url: str) -> str:
        """Extract all hyperlinks as a formatted list."""
        soup = BeautifulSoup(html, "html.parser")
        links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True) or href

            # Resolve relative URLs
            if href.startswith("/"):
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            elif not href.startswith(("http://", "https://", "mailto:", "tel:")):
                continue

            if href not in seen and href.startswith(("http://", "https://")):
                seen.add(href)
                links.append(f"- [{text}]({href})")

        if not links:
            return "No links found on the page."

        header = f"Links found on {base_url} ({len(links)} total):\n\n"
        return header + "\n".join(links)

    def _extract_metadata(self, html: str, url: str) -> str:
        """Extract page metadata: title, description, OG tags, JSON-LD."""
        soup = BeautifulSoup(html, "html.parser")
        parts = []

        # Title
        title_tag = soup.find("title")
        if title_tag:
            parts.append(f"Title: {title_tag.get_text(strip=True)}")

        # Meta description
        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            parts.append(f"Description: {desc['content']}")

        # OpenGraph tags
        og_tags = soup.find_all("meta", property=re.compile(r"^og:"))
        if og_tags:
            parts.append("\nOpenGraph:")
            for tag in og_tags:
                prop = tag.get("property", "")
                content = tag.get("content", "")
                parts.append(f"  {prop}: {content}")

        # Twitter card tags
        tw_tags = soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")})
        if tw_tags:
            parts.append("\nTwitter Card:")
            for tag in tw_tags:
                name = tag.get("name", "")
                content = tag.get("content", "")
                parts.append(f"  {name}: {content}")

        # JSON-LD structured data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                parts.append(f"\nJSON-LD ({data.get('@type', 'unknown')}):")
                parts.append(json.dumps(data, indent=2, ensure_ascii=False)[:5000])
            except (json.JSONDecodeError, TypeError):
                continue

        # Canonical URL
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            parts.append(f"\nCanonical URL: {canonical['href']}")

        if not parts:
            return f"No metadata found on {url}."

        return f"Metadata for {url}:\n\n" + "\n".join(parts)

    # ── Text cleaning ─────────────────────────────────────────────────

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalize whitespace and remove excessive blank lines."""
        # Collapse multiple blank lines into at most two
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Collapse multiple spaces
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()
