"""
Base skill class - All W.I.N.S.T.O.N. skills inherit from this.
Provides a standard interface for skill registration and execution.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("winston.skills")


@dataclass
class SkillResult:
    """Result from executing a skill."""
    success: bool
    message: str
    data: Any = None
    speak: bool = True  # Whether to speak the result


class BaseSkill(ABC):
    """
    Base class for all W.I.N.S.T.O.N. skills.

    To create a new skill:
    1. Inherit from BaseSkill
    2. Set name, description, and parameters
    3. Implement the execute() method
    4. Register the skill with the orchestrator

    Example:
        class MySkill(BaseSkill):
            name = "my_skill"
            description = "Does something cool"
            parameters = {"input": "The input text"}

            def execute(self, **kwargs) -> SkillResult:
                return SkillResult(success=True, message="Done!")
    """

    name: str = "base_skill"
    description: str = "Base skill - override this"
    parameters: dict = {}

    def __init__(self, config=None):
        self.config = config
        self.logger = logging.getLogger(f"winston.skills.{self.name}")

    @abstractmethod
    def execute(self, **kwargs) -> SkillResult:
        """
        Execute the skill with given parameters.

        Args:
            **kwargs: Skill-specific parameters

        Returns:
            SkillResult with success status and message
        """
        pass

    def validate_params(self, required: list[str], provided: dict) -> Optional[str]:
        """Validate that required parameters are provided."""
        missing = [p for p in required if p not in provided or not provided[p]]
        if missing:
            return f"Missing required parameters: {', '.join(missing)}"
        return None

    def __repr__(self):
        return f"<Skill: {self.name}>"
