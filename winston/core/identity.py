"""
Persistent Identity & Memory System for W.I.N.S.T.O.N.
Manages personality files, user profiles, long-term memory, and daily journals.
Inspired by clawdbot's SOUL.md/IDENTITY.md/USER.md/MEMORY.md system.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("winston.identity")


class IdentityManager:
    """
    Manages W.I.N.S.T.O.N.'s persistent personality and memory files.

    Directory structure:
        ~/.winston/identity/
            PERSONALITY.md  - Core personality, speaking style, beliefs
            USER.md         - Info about the user (auto-learned)
            MEMORY.md       - Long-term curated memory
        ~/.winston/memory/
            YYYY-MM-DD.md   - Daily session journals
    """

    def __init__(self, identity_dir: str = "~/.winston/identity",
                 memory_dir: str = "~/.winston/memory"):
        self.identity_dir = Path(identity_dir).expanduser()
        self.memory_dir = Path(memory_dir).expanduser()
        self.identity_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_files_exist()
        logger.info(f"Identity system initialized at {self.identity_dir}")

    def _ensure_files_exist(self):
        """Create default identity files if they don't exist."""
        defaults = {
            "PERSONALITY.md": (
                "# W.I.N.S.T.O.N. Personality\n\n"
                "## Core Identity\n"
                "- Name: W.I.N.S.T.O.N. (Wildly Intelligent Network System for Task Operations and Navigation)\n"
                "- Tone: Professional yet friendly, with dry British wit\n"
                "- Address the user as 'Sir' unless told otherwise\n"
                "- Style: Concise and actionable. Avoid filler words.\n\n"
                "## Principles\n"
                "- Be genuinely helpful, not performatively helpful\n"
                "- Have opinions and preferences when asked\n"
                "- Be resourceful -- try to figure things out before asking\n"
                "- Earn trust through competence and reliability\n"
                "- Remember what the user tells you and build on it\n\n"
                "## Boundaries\n"
                "- Private things stay private\n"
                "- Ask before acting on external systems\n"
                "- Never fabricate information\n"
                "- Admit when you don't know something\n"
            ),
            "USER.md": (
                "# About the User\n\n"
                "## Profile\n"
                "- **Name:** Unknown\n"
                "- **Preferred address:** Sir\n"
                "- **Timezone:** Unknown\n"
                "- **Location:** Unknown\n\n"
                "## Preferences\n"
                "(Will be learned over time from conversations)\n\n"
                "## Interests\n"
                "(Will be learned over time from conversations)\n"
            ),
            "MEMORY.md": (
                "# Long-Term Memory\n\n"
                "Key facts and knowledge accumulated across sessions.\n"
                "This file is auto-updated by the memory curator.\n\n"
                "## Facts\n\n"
                "## Recurring Topics\n\n"
                "## Important Dates\n\n"
            ),
        }

        for filename, content in defaults.items():
            filepath = self.identity_dir / filename
            if not filepath.exists():
                filepath.write_text(content)
                logger.info(f"Created default identity file: {filepath}")

    def read_file(self, filename: str, max_lines: int = None) -> str:
        """Read an identity file, optionally truncated to last N lines."""
        filepath = self.identity_dir / filename
        if not filepath.exists():
            return ""
        content = filepath.read_text().strip()
        if max_lines and content:
            lines = content.split("\n")
            if len(lines) > max_lines:
                content = "\n".join(lines[-max_lines:])
        return content

    def update_file(self, filename: str, content: str):
        """Overwrite an identity file."""
        filepath = self.identity_dir / filename
        filepath.write_text(content)
        logger.info(f"Updated identity file: {filename}")

    def append_to_file(self, filename: str, content: str):
        """Append content to an identity file."""
        filepath = self.identity_dir / filename
        existing = filepath.read_text() if filepath.exists() else ""
        filepath.write_text(existing.rstrip() + "\n" + content + "\n")

    def read_journal(self, date: str = None) -> str:
        """Read a session journal for a specific date (default: today)."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        journal_file = self.memory_dir / f"{date}.md"
        if journal_file.exists():
            return journal_file.read_text().strip()
        return ""

    def append_to_journal(self, entry: str):
        """Append an entry to today's session journal."""
        today = datetime.now().strftime("%Y-%m-%d")
        journal_file = self.memory_dir / f"{today}.md"
        if not journal_file.exists():
            journal_file.write_text(f"# Session Journal - {today}\n\n")
        with open(journal_file, "a") as f:
            timestamp = datetime.now().strftime("%H:%M")
            f.write(f"- [{timestamp}] {entry}\n")

    def build_system_prompt(self, base_prompt: str = "") -> str:
        """
        Build a dynamic system prompt from identity files.
        Merges personality + user info + long-term memory + today's journal
        with the base prompt (skill rules, security rules).
        """
        sections = []

        # Core personality
        personality = self.read_file("PERSONALITY.md")
        if personality:
            sections.append(personality)

        # User knowledge
        user_info = self.read_file("USER.md")
        if user_info:
            sections.append(user_info)

        # Long-term memory (last 50 lines to stay within context budget)
        memory = self.read_file("MEMORY.md", max_lines=50)
        if memory:
            sections.append(f"## Your Long-Term Memories\n{memory}")

        # Today's journal (if exists)
        journal = self.read_journal()
        if journal:
            sections.append(f"## Today's Session Notes\n{journal}")

        identity_block = "\n\n---\n\n".join(sections)

        # Merge: identity context + base prompt (skill rules, security rules)
        if base_prompt:
            return f"{identity_block}\n\n---\n\n{base_prompt}"
        return identity_block

    def list_files(self) -> list[str]:
        """List all identity files."""
        return [f.name for f in self.identity_dir.iterdir() if f.is_file() and f.suffix == ".md"]

    def list_journals(self, limit: int = 10) -> list[str]:
        """List recent journal files."""
        journals = sorted(
            [f.stem for f in self.memory_dir.iterdir() if f.suffix == ".md"],
            reverse=True,
        )
        return journals[:limit]


class MemoryCurator:
    """
    Analyzes conversations and extracts key facts for long-term memory.
    Uses the LLM to identify important information and update identity files.
    """

    def __init__(self, identity: IdentityManager, brain=None):
        self.identity = identity
        self.brain = brain  # Set later to avoid circular dependency

    def curate_session(self, conversation_history: list[dict]):
        """
        Run at end of session or periodically.
        Uses LLM to extract key facts from conversation and update files.
        """
        if not self.brain:
            logger.warning("No brain configured for memory curation")
            return

        # Need enough conversation to be worth curating
        if len(conversation_history) < 4:
            return

        # Format recent conversation for the LLM
        conv_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:300]}"
            for m in conversation_history[-20:]
        )

        extraction_prompt = (
            "Analyze this conversation between a user and an AI assistant. Extract:\n\n"
            "1. KEY_FACTS: Important facts or knowledge learned (one per line, prefixed with '- ')\n"
            "2. USER_INFO: Anything new learned about the user (name, preferences, location, interests, timezone)\n"
            "3. JOURNAL: A one-sentence summary of what this session was about\n\n"
            "If a section has nothing new, write 'None' for that section.\n"
            "Format your response EXACTLY like this:\n\n"
            "KEY_FACTS:\n- fact 1\n- fact 2\n\n"
            "USER_INFO:\n- info 1\n\n"
            "JOURNAL: one sentence summary\n\n"
            f"Conversation:\n{conv_text}"
        )

        try:
            response = self.brain.think(
                extraction_prompt,
                system_override=(
                    "You are a memory curator. Extract and organize information concisely. "
                    "Only extract genuinely new and useful information. Do not repeat things "
                    "that are obvious or trivial."
                ),
            )
            self._parse_and_store(response)
        except Exception as e:
            logger.error(f"Memory curation failed: {e}")

    def _parse_and_store(self, response: str):
        """Parse LLM extraction response and update identity files."""
        # Extract KEY_FACTS
        facts_match = re.search(
            r"KEY_FACTS:\s*\n(.*?)(?=\n\s*(?:USER_INFO|JOURNAL):|\Z)",
            response, re.DOTALL
        )
        if facts_match:
            facts = facts_match.group(1).strip()
            if facts and facts.lower() != "none":
                timestamp = datetime.now().strftime("%Y-%m-%d")
                self.identity.append_to_file(
                    "MEMORY.md",
                    f"\n### Learned on {timestamp}\n{facts}"
                )
                logger.info(f"Added facts to MEMORY.md")

        # Extract USER_INFO
        user_match = re.search(
            r"USER_INFO:\s*\n(.*?)(?=\n\s*(?:KEY_FACTS|JOURNAL):|\Z)",
            response, re.DOTALL
        )
        if user_match:
            user_info = user_match.group(1).strip()
            if user_info and user_info.lower() != "none":
                timestamp = datetime.now().strftime("%Y-%m-%d")
                self.identity.append_to_file(
                    "USER.md",
                    f"\n### Updated {timestamp}\n{user_info}"
                )
                logger.info(f"Updated USER.md with new info")

        # Extract JOURNAL
        journal_match = re.search(r"JOURNAL:\s*(.+)", response)
        if journal_match:
            summary = journal_match.group(1).strip()
            if summary and summary.lower() != "none":
                self.identity.append_to_journal(summary)
                logger.info(f"Added session summary to journal")
