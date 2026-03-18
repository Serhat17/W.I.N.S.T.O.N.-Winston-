"""
Tests for the WebFetchSkill — metadata, input validation, SSRF protection,
HTTP fetching (mocked), content extraction, caching, and content wrapping.
"""

import pytest
from unittest.mock import patch, MagicMock

from winston.skills.web_fetch_skill import WebFetchSkill
from winston.skills.base import SkillResult
from winston.utils.web_cache import cache_clear


# ── Sample HTML fixtures ─────────────────────────────────────────────

SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Article</title></head>
<body>
<nav>Navigation links here</nav>
<article>
    <h1>The Great Article Title</h1>
    <p>This is the main article content that should be extracted by the web_fetch skill.
    It contains multiple sentences and enough text to pass the minimum length threshold
    for article extraction. The content discusses important topics.</p>
    <p>Second paragraph with more details about the topic being covered in this
    article. There is substantial content here to ensure extraction works properly.</p>
</article>
<footer>Footer content</footer>
</body>
</html>
"""

SAMPLE_LINKS_HTML = """
<html>
<body>
<a href="https://example.com/page1">Page One</a>
<a href="/relative/path">Relative Link</a>
<a href="https://example.com/page2">Page Two</a>
<a href="https://example.com/page1">Duplicate Link</a>
<a href="mailto:test@example.com">Email Link</a>
</body>
</html>
"""

SAMPLE_METADATA_HTML = """
<html>
<head>
<title>My Page Title</title>
<meta name="description" content="A description of this page">
<meta property="og:title" content="OG Title">
<meta property="og:description" content="OG Description">
<meta property="og:image" content="https://example.com/image.jpg">
<script type="application/ld+json">
{"@type": "Article", "name": "JSON-LD Article", "author": "Test Author"}
</script>
</head>
<body><p>Hello</p></body>
</html>
"""


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the web cache before each test."""
    cache_clear()
    yield
    cache_clear()


def _mock_response(html: str, status_code: int = 200, url: str = "https://example.com"):
    """Build a mock requests.Response for patching."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.url = url
    resp.encoding = "utf-8"
    resp.headers = {"Content-Type": "text/html"}

    # iter_content yields bytes
    raw = html.encode("utf-8")
    resp.iter_content = MagicMock(return_value=[raw])

    return resp


# ═════════════════════════════════════════════════════════════════════
# 1. Metadata
# ═════════════════════════════════════════════════════════════════════

class TestWebFetchMetadata:
    def test_skill_name(self, web_fetch_skill):
        assert web_fetch_skill.name == "web_fetch"

    def test_skill_description(self, web_fetch_skill):
        assert web_fetch_skill.description
        assert len(web_fetch_skill.description) > 20

    def test_skill_parameters(self, web_fetch_skill):
        assert "url" in web_fetch_skill.parameters


# ═════════════════════════════════════════════════════════════════════
# 2. Input Validation
# ═════════════════════════════════════════════════════════════════════

class TestInputValidation:
    def test_no_url(self, web_fetch_skill):
        result = web_fetch_skill.execute(url="")
        assert isinstance(result, SkillResult)
        assert result.success is False
        assert "No URL" in result.message

    def test_invalid_mode(self, web_fetch_skill):
        result = web_fetch_skill.execute(url="https://example.com", extract_mode="xyz")
        assert result.success is False
        assert "Invalid extract_mode" in result.message

    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_auto_https_prefix(self, mock_get, web_fetch_skill):
        """URLs without scheme get https:// prepended."""
        mock_get.return_value = _mock_response("<html><body>Hello</body></html>")
        result = web_fetch_skill.execute(url="example.com")
        # Should not fail with "Invalid URL" — the https:// was auto-added
        # It may fail for other reasons (SSRF DNS, etc.) but not "Invalid URL"
        assert "Invalid URL" not in result.message

    def test_no_hostname(self, web_fetch_skill):
        result = web_fetch_skill.execute(url="https://")
        assert result.success is False


# ═════════════════════════════════════════════════════════════════════
# 3. SSRF Protection
# ═════════════════════════════════════════════════════════════════════

class TestSSRFProtection:
    def test_localhost_blocked(self, web_fetch_skill):
        result = web_fetch_skill.execute(url="http://127.0.0.1/secret")
        assert result.success is False
        assert "Blocked" in result.message

    def test_private_ip_blocked(self, web_fetch_skill):
        result = web_fetch_skill.execute(url="http://192.168.1.1/admin")
        assert result.success is False
        assert "Blocked" in result.message

    def test_metadata_server_blocked(self, web_fetch_skill):
        result = web_fetch_skill.execute(url="http://169.254.169.254/latest/meta-data/")
        assert result.success is False
        assert "Blocked" in result.message

    def test_internal_hostname_blocked(self, web_fetch_skill):
        result = web_fetch_skill.execute(url="http://something.internal/api")
        assert result.success is False
        assert "Blocked" in result.message


# ═════════════════════════════════════════════════════════════════════
# 4. HTTP Fetching (mocked)
# ═════════════════════════════════════════════════════════════════════

class TestFetchWithMock:
    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_successful_fetch(self, mock_get, mock_dns, web_fetch_skill):
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        mock_get.return_value = _mock_response(SAMPLE_ARTICLE_HTML, url="https://example.com/article")
        result = web_fetch_skill.execute(url="https://example.com/article")
        assert result.success is True
        # URL should be the original or the final redirect URL
        assert "example.com" in result.data["url"]

    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_http_error(self, mock_get, mock_dns, web_fetch_skill):
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        resp = _mock_response("", status_code=404)
        mock_get.return_value = resp
        result = web_fetch_skill.execute(url="https://example.com/missing")
        assert result.success is False
        assert "404" in result.message

    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_timeout(self, mock_get, mock_dns, web_fetch_skill):
        import requests
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        mock_get.side_effect = requests.exceptions.Timeout("Timed out")
        result = web_fetch_skill.execute(url="https://example.com/slow")
        assert result.success is False
        assert "imeout" in result.message  # "Timeout" or "timed out"

    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_connection_error(self, mock_get, mock_dns, web_fetch_skill):
        import requests
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        mock_get.side_effect = requests.exceptions.ConnectionError("Refused")
        result = web_fetch_skill.execute(url="https://example.com/down")
        assert result.success is False
        assert "connect" in result.message.lower()


# ═════════════════════════════════════════════════════════════════════
# 5. Content Extraction (direct method calls)
# ═════════════════════════════════════════════════════════════════════

class TestContentExtraction:
    def test_extract_article_tag(self, web_fetch_skill):
        content = web_fetch_skill._extract_article(SAMPLE_ARTICLE_HTML, "https://example.com")
        assert "article content" in content.lower()
        # Nav and footer should not appear
        assert "Navigation links" not in content

    def test_extract_main_tag(self, web_fetch_skill):
        html = """
        <html><body>
        <nav>Skip this</nav>
        <main><h1>Main Content</h1><p>This is important main content that is long enough
        to pass the minimum threshold for extraction and contains useful information.</p></main>
        </body></html>
        """
        content = web_fetch_skill._extract_article(html, "https://example.com")
        assert "Main Content" in content

    def test_extract_text_strips_scripts(self, web_fetch_skill):
        html = """
        <html><body>
        <script>var x = 'hidden';</script>
        <style>.hidden { display: none; }</style>
        <p>Visible text only</p>
        </body></html>
        """
        content = web_fetch_skill._extract_text(html)
        assert "Visible text" in content
        assert "var x" not in content
        assert "display: none" not in content

    def test_extract_links(self, web_fetch_skill):
        content = web_fetch_skill._extract_links(SAMPLE_LINKS_HTML, "https://example.com")
        assert "Page One" in content
        assert "Page Two" in content
        # Relative URL should be resolved
        assert "https://example.com/relative/path" in content
        # Mailto should be excluded
        assert "mailto:" not in content
        # Duplicates should be removed
        assert content.count("page1") == 1

    def test_extract_metadata_og(self, web_fetch_skill):
        content = web_fetch_skill._extract_metadata(SAMPLE_METADATA_HTML, "https://example.com")
        assert "My Page Title" in content
        assert "OG Title" in content
        assert "JSON-LD" in content
        assert "Article" in content


# ═════════════════════════════════════════════════════════════════════
# 6. Caching
# ═════════════════════════════════════════════════════════════════════

class TestCaching:
    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_cache_hit(self, mock_get, mock_dns, web_fetch_skill):
        """Second call with same URL+mode should return cached result."""
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        mock_get.return_value = _mock_response(SAMPLE_ARTICLE_HTML)

        r1 = web_fetch_skill.execute(url="https://example.com/cached")
        assert r1.success is True
        assert r1.data.get("cached") is False

        r2 = web_fetch_skill.execute(url="https://example.com/cached")
        assert r2.success is True
        assert r2.data.get("cached") is True

        # requests.get should only have been called once
        assert mock_get.call_count == 1

    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_cache_key_includes_mode(self, mock_get, mock_dns, web_fetch_skill):
        """Different extract modes should use separate cache entries."""
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        mock_get.return_value = _mock_response(SAMPLE_LINKS_HTML)

        r1 = web_fetch_skill.execute(url="https://example.com/modes", extract_mode="text")
        r2 = web_fetch_skill.execute(url="https://example.com/modes", extract_mode="links")

        # Both should succeed, and the second should NOT be cached
        assert r1.success is True
        assert r2.success is True
        assert mock_get.call_count == 2


# ═════════════════════════════════════════════════════════════════════
# 7. Content Wrapping
# ═════════════════════════════════════════════════════════════════════

class TestContentWrapping:
    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_result_wrapped(self, mock_get, mock_dns, web_fetch_skill):
        """Successful results should be wrapped with EXTERNAL_CONTENT boundaries."""
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        mock_get.return_value = _mock_response(SAMPLE_ARTICLE_HTML)
        result = web_fetch_skill.execute(url="https://example.com/wrapped")
        assert result.success is True
        assert "EXTERNAL_CONTENT" in result.message
        assert "END_EXTERNAL_CONTENT" in result.message

    @patch("winston.security.ssrf_guard.socket.getaddrinfo")
    @patch("winston.skills.web_fetch_skill.requests.get")
    def test_truncation(self, mock_get, mock_dns, web_fetch_skill):
        """max_chars should limit the content length."""
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        long_html = "<html><body><article>" + ("x" * 1000) + "</article></body></html>"
        mock_get.return_value = _mock_response(long_html)
        result = web_fetch_skill.execute(url="https://example.com/long", max_chars=100)
        assert result.success is True
        assert result.data.get("truncated") is True


# ═════════════════════════════════════════════════════════════════════
# 8. Text Cleaning
# ═════════════════════════════════════════════════════════════════════

class TestCleanText:
    def test_collapse_blank_lines(self):
        text = "Line 1\n\n\n\n\nLine 2"
        result = WebFetchSkill._clean_text(text)
        assert result.count("\n\n\n") == 0
        assert "Line 1" in result and "Line 2" in result

    def test_collapse_spaces(self):
        text = "Word1     Word2    Word3"
        result = WebFetchSkill._clean_text(text)
        assert "     " not in result
        assert "Word1 Word2 Word3" == result
