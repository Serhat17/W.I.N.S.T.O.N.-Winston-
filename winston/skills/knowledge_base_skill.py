"""
Knowledge Base Skill — persistent, searchable personal knowledge store.
Save articles, research, and snippets with semantic search via ChromaDB.
"""

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.knowledge_base")


class KnowledgeBaseSkill(BaseSkill):
    """Save and search a personal knowledge base with semantic retrieval."""

    name = "knowledge_base"
    description = (
        "Save articles, research, notes, and information to a persistent knowledge base. "
        "Search saved knowledge semantically. Use this when the user says "
        "'save this', 'remember this article', 'what did I save about X', "
        "'search my knowledge base', or wants to store/retrieve information."
    )
    parameters = {
        "action": "Action: 'save', 'search', 'list', 'delete', 'get'",
        "title": "(save) Title of the article or knowledge item",
        "content": "(save) The content/text to store",
        "source": "(save) Where this came from (URL, book, etc.)",
        "tags": "(save/list) Comma-separated tags for categorization",
        "query": "(search) What to search for in the knowledge base",
        "article_id": "(delete/get) ID of the article to act on",
        "limit": "(search/list) Max results to return (default: 10)",
    }

    KB_DIR = Path.home() / ".winston" / "knowledge_base"
    INDEX_FILE = KB_DIR / "index.json"

    def __init__(self, config=None):
        super().__init__(config)
        self.KB_DIR.mkdir(parents=True, exist_ok=True)
        self._collection = None
        self._index = self._load_index()

    def _load_index(self) -> dict:
        """Load the knowledge base index."""
        if self.INDEX_FILE.exists():
            try:
                return json.loads(self.INDEX_FILE.read_text())
            except Exception:
                return {"articles": {}}
        return {"articles": {}}

    def _save_index(self):
        """Persist the index to disk."""
        self.INDEX_FILE.write_text(json.dumps(self._index, indent=2, default=str))

    def _get_collection(self):
        """Lazy-initialize the ChromaDB collection."""
        if self._collection is not None:
            return self._collection

        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(self.KB_DIR / "chroma"))
            self._collection = client.get_or_create_collection(
                name="knowledge_base",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"Knowledge base collection loaded ({self._collection.count()} items)")
            return self._collection
        except ImportError:
            logger.warning("ChromaDB not installed. Install with: pip install chromadb")
            return None

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "list")

        handlers = {
            "save": self._save,
            "search": self._search,
            "list": self._list,
            "delete": self._delete,
            "get": self._get,
        }

        handler = handlers.get(action)
        if not handler:
            return SkillResult(
                success=False,
                message=f"Unknown action: {action}. Use: save, search, list, delete, get",
            )
        return handler(**kwargs)

    def _save(self, **kwargs) -> SkillResult:
        """Save an article/snippet to the knowledge base."""
        title = kwargs.get("title", "").strip()
        content = kwargs.get("content", "").strip()
        source = kwargs.get("source", "").strip()
        tags_str = kwargs.get("tags", "")
        tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()] if tags_str else []

        if not title and not content:
            return SkillResult(success=False, message="Provide a title and/or content to save.")

        article_id = f"kb_{secrets.token_hex(6)}"
        now = datetime.now().isoformat()

        # Store metadata in the index
        article = {
            "id": article_id,
            "title": title or content[:60],
            "source": source,
            "tags": tags,
            "created": now,
            "content_length": len(content),
        }
        self._index["articles"][article_id] = article
        self._save_index()

        # Store full content as a file
        content_file = self.KB_DIR / f"{article_id}.md"
        content_file.write_text(f"# {title}\n\n{content}\n\n---\nSource: {source}\nTags: {', '.join(tags)}\nSaved: {now}\n")

        # Add to ChromaDB for semantic search
        collection = self._get_collection()
        if collection and content:
            # Chunk long content (ChromaDB works better with shorter chunks)
            chunks = self._chunk_text(content, chunk_size=500)
            for i, chunk in enumerate(chunks):
                chunk_id = f"{article_id}_chunk_{i}"
                collection.upsert(
                    ids=[chunk_id],
                    documents=[chunk],
                    metadatas=[{
                        "article_id": article_id,
                        "title": title,
                        "tags": ",".join(tags),
                        "source": source,
                        "chunk_index": i,
                    }],
                )

        tag_str = f" | Tags: {', '.join(tags)}" if tags else ""
        return SkillResult(
            success=True,
            message=(
                f"Saved to knowledge base: **{title}**\n"
                f"ID: `{article_id}` | {len(content)} chars{tag_str}"
            ),
            data={"article_id": article_id},
        )

    def _search(self, **kwargs) -> SkillResult:
        """Semantic search across the knowledge base."""
        query = kwargs.get("query", "").strip()
        limit = int(kwargs.get("limit", 10))

        if not query:
            return SkillResult(success=False, message="No search query provided.")

        collection = self._get_collection()
        if not collection:
            return SkillResult(
                success=False,
                message="Knowledge base not available (ChromaDB not installed).",
            )

        if collection.count() == 0:
            return SkillResult(
                success=True,
                message="Knowledge base is empty. Save something first with 'save'.",
            )

        try:
            results = collection.query(
                query_texts=[query],
                n_results=min(limit, 20),
            )

            if not results["documents"] or not results["documents"][0]:
                return SkillResult(
                    success=True,
                    message=f"No results found for: '{query}'",
                )

            # Deduplicate by article_id and format results
            seen_articles = {}
            for doc, meta, distance in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                aid = meta.get("article_id", "")
                if aid not in seen_articles:
                    seen_articles[aid] = {
                        "title": meta.get("title", "Untitled"),
                        "tags": meta.get("tags", ""),
                        "source": meta.get("source", ""),
                        "relevance": round(1 - distance, 3),
                        "preview": doc[:200],
                        "article_id": aid,
                    }

            lines = [f"**Knowledge Base Results** for '{query}':\n"]
            for i, (aid, item) in enumerate(seen_articles.items(), 1):
                tags = f" [{item['tags']}]" if item["tags"] else ""
                source = f" — {item['source']}" if item["source"] else ""
                lines.append(
                    f"{i}. **{item['title']}**{tags} (relevance: {item['relevance']})\n"
                    f"   {item['preview']}...{source}\n"
                    f"   ID: `{aid}`\n"
                )

            return SkillResult(
                success=True,
                message="\n".join(lines),
                data=list(seen_articles.values()),
                speak=False,
            )

        except Exception as e:
            logger.error(f"Knowledge base search failed: {e}")
            return SkillResult(success=False, message=f"Search error: {e}")

    def _list(self, **kwargs) -> SkillResult:
        """List articles in the knowledge base, optionally filtered by tag."""
        tag_filter = kwargs.get("tags", "").strip().lower()
        limit = int(kwargs.get("limit", 20))

        articles = list(self._index.get("articles", {}).values())

        if tag_filter:
            filter_tags = {t.strip() for t in tag_filter.split(",")}
            articles = [
                a for a in articles
                if filter_tags.intersection(set(a.get("tags", [])))
            ]

        # Sort by creation date (newest first)
        articles.sort(key=lambda a: a.get("created", ""), reverse=True)
        articles = articles[:limit]

        if not articles:
            msg = "Knowledge base is empty."
            if tag_filter:
                msg = f"No articles found with tags: {tag_filter}"
            return SkillResult(success=True, message=msg)

        lines = [f"**Knowledge Base** ({len(articles)} articles):\n"]
        for i, a in enumerate(articles, 1):
            tags = f" [{', '.join(a.get('tags', []))}]" if a.get("tags") else ""
            lines.append(
                f"{i}. **{a['title']}**{tags}\n"
                f"   {a.get('content_length', 0)} chars | {a['created'][:10]}\n"
                f"   ID: `{a['id']}`\n"
            )

        return SkillResult(
            success=True,
            message="\n".join(lines),
            data=articles,
            speak=False,
        )

    def _get(self, **kwargs) -> SkillResult:
        """Get the full content of a specific article."""
        article_id = kwargs.get("article_id", "").strip()
        if not article_id:
            return SkillResult(success=False, message="No article_id provided.")

        content_file = self.KB_DIR / f"{article_id}.md"
        if not content_file.exists():
            return SkillResult(success=False, message=f"Article '{article_id}' not found.")

        content = content_file.read_text()
        return SkillResult(
            success=True,
            message=content,
            data={"article_id": article_id, "content": content},
            speak=False,
        )

    def _delete(self, **kwargs) -> SkillResult:
        """Delete an article from the knowledge base."""
        article_id = kwargs.get("article_id", "").strip()
        if not article_id:
            return SkillResult(success=False, message="No article_id provided.")

        if article_id not in self._index.get("articles", {}):
            return SkillResult(success=False, message=f"Article '{article_id}' not found.")

        title = self._index["articles"][article_id].get("title", article_id)

        # Remove from index
        del self._index["articles"][article_id]
        self._save_index()

        # Remove content file
        content_file = self.KB_DIR / f"{article_id}.md"
        content_file.unlink(missing_ok=True)

        # Remove from ChromaDB
        collection = self._get_collection()
        if collection:
            try:
                # Delete all chunks for this article
                existing = collection.get(where={"article_id": article_id})
                if existing["ids"]:
                    collection.delete(ids=existing["ids"])
            except Exception as e:
                logger.warning(f"Failed to remove from ChromaDB: {e}")

        return SkillResult(
            success=True,
            message=f"Deleted from knowledge base: **{title}** (`{article_id}`)",
        )

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 500) -> list[str]:
        """Split text into chunks, trying to break at sentence boundaries."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        while text:
            if len(text) <= chunk_size:
                chunks.append(text)
                break

            # Find a good break point (sentence end)
            break_at = text.rfind(". ", 0, chunk_size)
            if break_at == -1 or break_at < chunk_size // 3:
                break_at = text.rfind(" ", 0, chunk_size)
            if break_at == -1:
                break_at = chunk_size

            chunks.append(text[:break_at + 1].strip())
            text = text[break_at + 1:].strip()

        return chunks
