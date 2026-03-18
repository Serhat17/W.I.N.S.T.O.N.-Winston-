"""
Conversation history persistence.
Save, load, search, and manage conversation sessions.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import uuid

logger = logging.getLogger("winston.conversations")


class ConversationStore:
    """Persist and manage conversation history."""

    def __init__(self, storage_dir: str = "~/.winston/conversations"):
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self.storage_dir / "index.json"
        self._index = self._load_index()

    def _load_index(self) -> dict:
        if self._index_file.exists():
            try:
                return json.loads(self._index_file.read_text())
            except Exception:
                return {"conversations": []}
        return {"conversations": []}

    def _save_index(self):
        self._index_file.write_text(json.dumps(self._index, indent=2))

    def create_conversation(self, title: str = "") -> str:
        """Create a new conversation and return its ID."""
        conv_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()

        entry = {
            "id": conv_id,
            "title": title or f"Conversation {now[:10]}",
            "created": now,
            "updated": now,
            "message_count": 0,
            "preview": "",
        }
        self._index["conversations"].insert(0, entry)
        self._save_index()

        # Create conversation file
        conv_file = self.storage_dir / f"{conv_id}.json"
        conv_file.write_text(json.dumps({"messages": []}, indent=2))

        return conv_id

    def save_message(self, conv_id: str, role: str, content: str, images: Optional[list[str]] = None):
        """Add a message to a conversation."""
        conv_file = self.storage_dir / f"{conv_id}.json"
        if not conv_file.exists():
            conv_file.write_text(json.dumps({"messages": []}, indent=2))

        data = json.loads(conv_file.read_text())
        message_obj = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        if images:
            message_obj["images"] = images
            
        data["messages"].append(message_obj)
        conv_file.write_text(json.dumps(data, indent=2))

        # Update index
        for entry in self._index["conversations"]:
            if entry["id"] == conv_id:
                entry["updated"] = datetime.now().isoformat()
                entry["message_count"] = len(data["messages"])
                if role == "user":
                    entry["preview"] = content[:80]
                # Auto-title from first user message if still default
                if entry["title"].startswith("Conversation 20") and role == "user" and entry["message_count"] <= 2:
                    entry["title"] = content[:50] + ("..." if len(content) > 50 else "")
                break
        self._save_index()

    def get_conversation(self, conv_id: str) -> Optional[dict]:
        """Load a full conversation."""
        conv_file = self.storage_dir / f"{conv_id}.json"
        if not conv_file.exists():
            return None
        try:
            data = json.loads(conv_file.read_text())
            # Merge with index info
            for entry in self._index["conversations"]:
                if entry["id"] == conv_id:
                    data["info"] = entry
                    break
            return data
        except Exception:
            return None

    def list_conversations(self, limit: int = 50) -> list[dict]:
        """List recent conversations (newest first)."""
        convs = self._index.get("conversations", [])
        # Sort by updated date descending
        convs.sort(key=lambda c: c.get("updated", ""), reverse=True)
        return convs[:limit]

    def delete_conversation(self, conv_id: str) -> bool:
        """Delete a conversation."""
        conv_file = self.storage_dir / f"{conv_id}.json"
        if conv_file.exists():
            conv_file.unlink()

        self._index["conversations"] = [
            c for c in self._index["conversations"] if c["id"] != conv_id
        ]
        self._save_index()
        return True

    def rename_conversation(self, conv_id: str, new_title: str) -> bool:
        """Rename a conversation."""
        for entry in self._index["conversations"]:
            if entry["id"] == conv_id:
                entry["title"] = new_title
                self._save_index()
                return True
        return False

    def search_conversations(self, query: str, limit: int = 20) -> list[dict]:
        """Search conversations by content."""
        query_lower = query.lower()
        results = []

        for entry in self._index.get("conversations", []):
            # Check title
            if query_lower in entry.get("title", "").lower():
                results.append(entry)
                continue

            # Check message content
            conv_file = self.storage_dir / f"{entry['id']}.json"
            if conv_file.exists():
                try:
                    data = json.loads(conv_file.read_text())
                    for msg in data.get("messages", []):
                        if query_lower in msg.get("content", "").lower():
                            results.append(entry)
                            break
                except Exception:
                    continue

        return results[:limit]
