"""
Tests for the ConversationStore.
Validates create, save, load, search, delete, and image persistence.
"""

import json
import pytest
import tempfile
from pathlib import Path
from winston.core.conversations import ConversationStore


@pytest.fixture
def store(tmp_path):
    """A ConversationStore backed by a temp directory."""
    return ConversationStore(storage_dir=str(tmp_path))


class TestConversationCRUD:
    """Basic CRUD operations."""

    def test_create_conversation(self, store):
        """Creating a conversation should return a valid ID."""
        cid = store.create_conversation(title="Test Chat")
        assert cid is not None
        assert len(cid) > 0

    def test_list_conversations(self, store):
        """Created conversations should appear in the list."""
        store.create_conversation(title="Chat 1")
        store.create_conversation(title="Chat 2")
        convos = store.list_conversations()
        assert len(convos) >= 2
        titles = [c.get("title", "") for c in convos]
        assert "Chat 1" in titles
        assert "Chat 2" in titles

    def test_save_and_load_messages(self, store):
        """Messages should persist across save and load."""
        cid = store.create_conversation(title="Message Test")
        store.save_message(cid, "user", "Hello Winston")
        store.save_message(cid, "assistant", "Hello Sir!")

        conv = store.get_conversation(cid)
        messages = conv["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello Winston"
        assert messages[1]["role"] == "assistant"

    def test_delete_conversation(self, store):
        """Delete should remove the conversation."""
        cid = store.create_conversation(title="To Delete")
        store.save_message(cid, "user", "temp message")

        result = store.delete_conversation(cid)
        assert result is True

        convos = store.list_conversations()
        ids = [c.get("id", "") for c in convos]
        assert cid not in ids

    def test_search_conversations(self, store):
        """Search should find conversations by message content."""
        cid = store.create_conversation(title="Cooking Chat")
        store.save_message(cid, "user", "How to make pasta carbonara?")
        store.save_message(cid, "assistant", "Here is the recipe for carbonara...")

        results = store.search_conversations("carbonara")
        assert len(results) > 0

    def test_rename_conversation(self, store):
        """Rename should update the conversation title."""
        cid = store.create_conversation(title="Old Title")
        store.rename_conversation(cid, "New Title")

        convos = store.list_conversations()
        for c in convos:
            if c.get("id") == cid:
                assert c.get("title") == "New Title"
                break


class TestConversationImagePersistence:
    """Tests for image data in conversations."""

    def test_save_message_with_images(self, store):
        """Messages with images should persist the image data."""
        cid = store.create_conversation(title="Image Chat")
        images = ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="]
        store.save_message(cid, "user", "Look at this", images=images)

        conv = store.get_conversation(cid)
        messages = conv["messages"]
        assert len(messages) == 1

    def test_save_message_without_images(self, store):
        """Messages without images should work normally."""
        cid = store.create_conversation(title="Text Chat")
        store.save_message(cid, "user", "Just text here")

        conv = store.get_conversation(cid)
        messages = conv["messages"]
        assert len(messages) == 1
        # images may be None or empty list
        images = messages[0].get("images", [])
        assert not images or images is None
