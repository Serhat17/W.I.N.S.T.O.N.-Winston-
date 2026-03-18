"""
Multi-Agent Routing for W.I.N.S.T.O.N.
Enables different personas (Home, Work, Coding) with isolated skill access,
system prompts, and memory namespaces.
"""

import logging
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("winston.agent_router")


@dataclass
class AgentProfile:
    """Defines a single agent persona."""
    name: str
    display_name: str = ""
    description: str = ""
    system_prompt: str = ""
    allowed_skills: list[str] = field(default_factory=list)  # empty = all skills
    blocked_skills: list[str] = field(default_factory=list)
    memory_namespace: str = "default"
    personality_traits: dict = field(default_factory=dict)
    # Channel auto-select: map channel names to this agent
    auto_channels: list[str] = field(default_factory=list)

    def filter_skills(self, all_skills: dict) -> dict:
        """Return only the skills this agent is allowed to use."""
        if self.allowed_skills:
            # Whitelist mode
            return {k: v for k, v in all_skills.items() if k in self.allowed_skills}
        elif self.blocked_skills:
            # Blacklist mode
            return {k: v for k, v in all_skills.items() if k not in self.blocked_skills}
        # No restrictions
        return dict(all_skills)


# Built-in agent profiles
BUILTIN_AGENTS = {
    "home": AgentProfile(
        name="home",
        display_name="Home Assistant",
        description="Casual, full-featured mode for personal use",
        system_prompt=(
            "You are in HOME mode. Be casual, friendly, and use humor. "
            "All skills are available. Address the user informally."
        ),
        blocked_skills=[],  # All skills available
        memory_namespace="home",
        auto_channels=["telegram"],
    ),
    "work": AgentProfile(
        name="work",
        display_name="Work Professional",
        description="Professional mode — no entertainment, focused productivity",
        system_prompt=(
            "You are in WORK mode. Be professional, concise, and focused. "
            "Prioritize productivity. No casual chat or entertainment skills."
        ),
        blocked_skills=["youtube", "smart_home", "audio_analysis"],
        memory_namespace="work",
        auto_channels=[],
    ),
    "coding": AgentProfile(
        name="coding",
        display_name="Code Companion",
        description="Minimal, terse coding assistant — code only",
        system_prompt=(
            "You are in CODING mode. Be extremely terse. "
            "Respond with code, commands, and technical answers only. "
            "No pleasantries. Skip explanations unless asked."
        ),
        allowed_skills=["web_search", "code_runner", "file_manager", "clipboard",
                        "system_control", "browser", "knowledge_base", "desktop_screenshot"],
        memory_namespace="coding",
        auto_channels=[],
    ),
}


class AgentRouter:
    """
    Routes conversations to the appropriate agent persona.

    Supports:
    - Manual switching via chat ("switch to work mode")
    - Auto-detection from channel source
    - YAML config overrides
    """

    AGENTS_FILE = "config/agents.yaml"
    SWITCH_PATTERNS = [
        "switch to {} mode",
        "use {} mode",
        "{} mode",
        "switch to {}",
        "be {}",
    ]

    def __init__(self, config_dir: str = None):
        self._agents: dict[str, AgentProfile] = dict(BUILTIN_AGENTS)
        self._active_agent: str = "home"
        self._session_agents: dict[str, str] = {}  # session_id -> agent_name

        # Load custom agents from YAML
        if config_dir:
            self._load_custom_agents(config_dir)

        logger.info(
            f"Agent router initialized: {len(self._agents)} agents "
            f"({', '.join(self._agents.keys())})"
        )

    def _load_custom_agents(self, config_dir: str):
        """Load or merge agent definitions from agents.yaml."""
        agents_file = Path(config_dir) / "agents.yaml"
        if not agents_file.exists():
            return

        try:
            with open(agents_file) as f:
                data = yaml.safe_load(f) or {}

            for name, agent_data in data.get("agents", {}).items():
                if name in self._agents:
                    # Merge with existing
                    existing = self._agents[name]
                    for key, value in agent_data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                else:
                    # Create new agent
                    self._agents[name] = AgentProfile(name=name, **agent_data)

            logger.info(f"Loaded custom agents from {agents_file}")
        except Exception as e:
            logger.warning(f"Failed to load agents config: {e}")

    def get_agent(self, session_id: str = None, channel: str = None) -> AgentProfile:
        """Get the active agent for a session or channel."""
        # Session-specific override takes priority
        if session_id and session_id in self._session_agents:
            agent_name = self._session_agents[session_id]
            return self._agents.get(agent_name, self._agents["home"])

        # Auto-detect from channel
        if channel:
            for name, agent in self._agents.items():
                if channel in agent.auto_channels:
                    return agent

        # Default to active agent
        return self._agents.get(self._active_agent, self._agents["home"])

    def switch_agent(self, agent_name: str, session_id: str = None) -> Optional[AgentProfile]:
        """Switch to a different agent. Returns the new agent or None if not found."""
        agent_name = agent_name.lower().strip()
        if agent_name not in self._agents:
            return None

        if session_id:
            self._session_agents[session_id] = agent_name
        else:
            self._active_agent = agent_name

        agent = self._agents[agent_name]
        logger.info(f"Switched to agent: {agent.display_name or agent.name}")
        return agent

    def detect_switch_command(self, user_input: str) -> Optional[str]:
        """Check if user input is a mode switch command. Returns agent name or None."""
        input_lower = user_input.lower().strip()

        for agent_name in self._agents:
            for pattern in self.SWITCH_PATTERNS:
                target = pattern.format(agent_name)
                if input_lower == target or input_lower.startswith(target):
                    return agent_name

        return None

    def list_agents(self) -> list[dict]:
        """List all available agents."""
        return [
            {
                "name": a.name,
                "display_name": a.display_name or a.name,
                "description": a.description,
                "active": a.name == self._active_agent,
            }
            for a in self._agents.values()
        ]

    @property
    def active_agent_name(self) -> str:
        return self._active_agent
