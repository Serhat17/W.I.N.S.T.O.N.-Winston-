"""
Tests for the Knowledge Base Skill.
Validates save, search, list, get, delete operations, and text chunking.
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from winston.skills.knowledge_base_skill import KnowledgeBaseSkill


@pytest.fixture
def kb_skill(tmp_path):
    """A KnowledgeBaseSkill wired to a temp directory."""
    skill = KnowledgeBaseSkill()
    # Override storage paths to use temp dir
    skill.KB_DIR = tmp_path / "knowledge_base"
    skill.KB_DIR.mkdir(parents=True, exist_ok=True)
    skill.INDEX_FILE = skill.KB_DIR / "index.json"
    skill._index = {"articles": {}}
    skill._collection = None  # Will mock ChromaDB
    return skill


class TestKnowledgeBaseSave:
    """Tests for saving knowledge."""

    def test_save_requires_title_or_content(self, kb_skill):
        """Save should fail without title or content."""
        result = kb_skill.execute(action="save")
        assert not result.success
        assert "Provide" in result.message

    def test_save_creates_article(self, kb_skill):
        """Save should create an article file and index entry."""
        result = kb_skill.execute(
            action="save",
            title="Test Article",
            content="This is test content about Python programming.",
            tags="python,testing",
        )
        assert result.success
        assert "Test Article" in result.message
        assert result.data["article_id"] is not None

        # Verify index updated
        article_id = result.data["article_id"]
        assert article_id in kb_skill._index["articles"]
        article = kb_skill._index["articles"][article_id]
        assert article["title"] == "Test Article"
        assert "python" in article["tags"]
        assert "testing" in article["tags"]

        # Verify content file created
        content_file = kb_skill.KB_DIR / f"{article_id}.md"
        assert content_file.exists()
        content = content_file.read_text()
        assert "Python programming" in content

    def test_save_with_source(self, kb_skill):
        """Save should store the source metadata."""
        result = kb_skill.execute(
            action="save",
            title="Web Article",
            content="Some web content",
            source="https://example.com",
        )
        assert result.success
        article_id = result.data["article_id"]
        assert kb_skill._index["articles"][article_id]["source"] == "https://example.com"


class TestKnowledgeBaseList:
    """Tests for listing knowledge."""

    def test_list_empty(self, kb_skill):
        """List on empty KB should return appropriate message."""
        result = kb_skill.execute(action="list")
        assert result.success
        assert "empty" in result.message.lower()

    def test_list_with_articles(self, kb_skill):
        """List should show saved articles."""
        kb_skill.execute(action="save", title="Article A", content="Content A", tags="alpha")
        kb_skill.execute(action="save", title="Article B", content="Content B", tags="beta")

        result = kb_skill.execute(action="list")
        assert result.success
        assert "Article A" in result.message
        assert "Article B" in result.message

    def test_list_filter_by_tag(self, kb_skill):
        """List with tag filter should only show matching articles."""
        kb_skill.execute(action="save", title="Python Guide", content="Python stuff", tags="python")
        kb_skill.execute(action="save", title="JS Guide", content="JavaScript stuff", tags="javascript")

        result = kb_skill.execute(action="list", tags="python")
        assert result.success
        assert "Python Guide" in result.message
        assert "JS Guide" not in result.message


class TestKnowledgeBaseGet:
    """Tests for retrieving specific articles."""

    def test_get_existing_article(self, kb_skill):
        """Get should return full content of a saved article."""
        save_result = kb_skill.execute(
            action="save",
            title="Detailed Article",
            content="This is the detailed body text.",
        )
        article_id = save_result.data["article_id"]

        result = kb_skill.execute(action="get", article_id=article_id)
        assert result.success
        assert "Detailed Article" in result.message
        assert "detailed body text" in result.message

    def test_get_nonexistent_article(self, kb_skill):
        """Get should fail for non-existent article."""
        result = kb_skill.execute(action="get", article_id="kb_nonexistent")
        assert not result.success
        assert "not found" in result.message

    def test_get_no_id(self, kb_skill):
        """Get without article_id should fail."""
        result = kb_skill.execute(action="get")
        assert not result.success
        assert "No article_id" in result.message


class TestKnowledgeBaseDelete:
    """Tests for deleting knowledge."""

    def test_delete_article(self, kb_skill):
        """Delete should remove article from index and disk."""
        save_result = kb_skill.execute(
            action="save",
            title="To Delete",
            content="This will be deleted.",
        )
        article_id = save_result.data["article_id"]

        result = kb_skill.execute(action="delete", article_id=article_id)
        assert result.success
        assert "Deleted" in result.message

        # Verify removed from index
        assert article_id not in kb_skill._index["articles"]

        # Verify file removed
        content_file = kb_skill.KB_DIR / f"{article_id}.md"
        assert not content_file.exists()

    def test_delete_nonexistent(self, kb_skill):
        """Delete should fail for non-existent article."""
        result = kb_skill.execute(action="delete", article_id="kb_nonexistent")
        assert not result.success


class TestKnowledgeBaseSearch:
    """Tests for semantic search (with mocked ChromaDB)."""

    def test_search_no_query(self, kb_skill):
        """Search without query should fail."""
        result = kb_skill.execute(action="search")
        assert not result.success
        assert "No search query" in result.message

    def test_search_without_chromadb(self, kb_skill):
        """Search without ChromaDB should gracefully fail."""
        kb_skill._get_collection = MagicMock(return_value=None)
        result = kb_skill.execute(action="search", query="test")
        assert not result.success
        assert "not available" in result.message

    def test_search_empty_collection(self, kb_skill):
        """Search on empty collection should return appropriate message."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 0
        kb_skill._get_collection = MagicMock(return_value=mock_collection)

        result = kb_skill.execute(action="search", query="test")
        assert result.success
        assert "empty" in result.message.lower()


class TestTextChunking:
    """Tests for the text chunking utility."""

    def test_short_text_not_chunked(self):
        """Text shorter than chunk_size should return as single chunk."""
        chunks = KnowledgeBaseSkill._chunk_text("Short text.", chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == "Short text."

    def test_long_text_chunked(self):
        """Long text should be split into multiple chunks."""
        text = "This is a sentence. " * 100  # ~2000 chars
        chunks = KnowledgeBaseSkill._chunk_text(text, chunk_size=500)
        assert len(chunks) > 1
        # All chunks should be <= chunk_size (roughly)
        for chunk in chunks:
            assert len(chunk) <= 600  # Some tolerance

    def test_chunking_preserves_content(self):
        """All original content should be preserved across chunks."""
        text = "Word " * 200
        chunks = KnowledgeBaseSkill._chunk_text(text.strip(), chunk_size=100)
        reconstructed = " ".join(chunks)
        # Content should be preserved (whitespace may differ slightly)
        assert reconstructed.replace("  ", " ").strip().startswith("Word Word")


class TestKnowledgeBaseSafety:
    """Test safety classifications for knowledge_base actions."""

    def test_kb_risk_map(self):
        """Knowledge base actions should have correct risk levels."""
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel

        kb_map = SKILL_RISK_MAP.get("knowledge_base", {})
        assert kb_map.get("save") == RiskLevel.LOW
        assert kb_map.get("search") == RiskLevel.SAFE
        assert kb_map.get("list") == RiskLevel.SAFE
        assert kb_map.get("get") == RiskLevel.SAFE
        assert kb_map.get("delete") == RiskLevel.MEDIUM
