"""Skills package - Pluggable capabilities for W.I.N.S.T.O.N."""

from .base import BaseSkill, SkillResult
from .email_skill import EmailSkill
from .web_search import WebSearchSkill
from .system_control import SystemControlSkill
from .notes_skill import NotesSkill

__all__ = [
    "BaseSkill",
    "SkillResult",
    "EmailSkill",
    "WebSearchSkill",
    "SystemControlSkill",
    "NotesSkill",
]
