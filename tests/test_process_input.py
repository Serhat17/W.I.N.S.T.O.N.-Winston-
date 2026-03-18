"""
Integration tests for the _process_input pipeline in server.py.
Uses mocked Brain/Memory to test the full flow without needing Ollama.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from winston.skills.base import SkillResult
from winston.skills.web_search import WebSearchSkill
from winston.core.safety import SafetyGuard, RiskLevel


def _build_server_process_input(brain, memory, skills, safety=None):
    """
    Build a callable that mimics server.py's _process_input without
    starting the full FastAPI server. Uses the shared pipeline logic.
    """
    if safety is None:
        safety = SafetyGuard(require_confirmation=False)

    from winston.core.pipeline import detect_fallback_calls, finalize_response

    class TestableProcessor:
        def __init__(self):
            self.brain = brain
            self.memory = memory
            self.skills = skills
            self.safety = safety

        def process(self, user_input, images=None):
            sanitized = self.safety.sanitize_input(user_input)
            self.memory.add_message("user", sanitized)
            context = self.memory.get_context_messages()

            response = self.brain.think(sanitized, conversation_history=context, images=images)

            skill_calls = self.brain.parse_skill_calls(response)

            if not skill_calls:
                skill_calls = detect_fallback_calls(sanitized, response, self.skills)

            if skill_calls:
                skill_results = []
                for call in skill_calls:
                    skill_name = call.get("skill", "")
                    params = call.get("parameters", {})
                    action_req = self.safety.request_action(skill_name, params)

                    if action_req.risk_level == RiskLevel.BLOCKED:
                        continue

                    if not action_req.approved:
                        if action_req.risk_level == RiskLevel.MEDIUM:
                            self.safety.approve_action(action_req.id)
                            action_req.approved = True
                        else:
                            self.safety.deny_action(action_req.id)
                            continue

                    if action_req.approved and skill_name in self.skills:
                        try:
                            result = self.skills[skill_name].execute(**params)
                            skill_results.append(result)
                        except Exception as e:
                            skill_results.append(SkillResult(success=False, message=str(e)))

                if skill_results:
                    results_text = "\n".join(r.message for r in skill_results)
                    response = self.brain.think(
                        f"The user asked: {sanitized}\n\n"
                        f"Here are the actual results from the system:\n{results_text}\n\n"
                        f"Now respond naturally to the user based on these results.",
                        conversation_history=context,
                    )
                else:
                    response = self.brain.strip_skill_blocks(response)
                    if not response:
                        response = "I tried to process that, but couldn't complete the action."

            response = finalize_response(response, self.brain, self.safety)
            self.memory.add_message("assistant", response)
            return response

    return TestableProcessor()


def test_web_search_fallback_injects_skill_call(mock_brain, mock_memory):
    """When LLM says it can't search, fallback should inject web_search."""
    mock_brain.think.return_value = "I'm sorry, I can't perform web searches at the moment."
    mock_brain.parse_skill_calls.return_value = []

    mock_skill = MagicMock()
    mock_skill.execute.return_value = SkillResult(
        success=True,
        message="Search results for 'flights': 1. Skyscanner - cheap flights...",
        data=[{"title": "Skyscanner", "body": "cheap flights", "href": "https://skyscanner.com"}],
    )

    skills = {"web_search": mock_skill}

    processor = _build_server_process_input(mock_brain, mock_memory, skills)
    processor.process("search for the latest news about AI")

    # web_search skill should have been called via fallback
    mock_skill.execute.assert_called_once()
    call_kwargs = mock_skill.execute.call_args[1]
    assert "query" in call_kwargs


def test_no_fallback_when_skill_calls_exist(mock_brain, mock_memory):
    """If LLM already emitted a skill call, no fallback should happen."""
    mock_brain.think.return_value = '{"skill": "web_search", "parameters": {"query": "test"}}'
    mock_brain.parse_skill_calls.return_value = [
        {"skill": "web_search", "parameters": {"query": "test"}}
    ]

    mock_skill = MagicMock()
    mock_skill.execute.return_value = SkillResult(success=True, message="Results here")
    skills = {"web_search": mock_skill}

    processor = _build_server_process_input(mock_brain, mock_memory, skills)
    processor.process("search for something")

    # Skill should be called exactly once (from the LLM's call, not fallback)
    mock_skill.execute.assert_called_once()


def test_skill_result_passed_to_brain(mock_brain, mock_memory):
    """Skill results should be passed back to brain.think for summarization."""
    mock_brain.think.return_value = "I'll search for that."
    mock_brain.parse_skill_calls.return_value = []

    mock_skill = MagicMock()
    mock_skill.execute.return_value = SkillResult(
        success=True, message="Found: Latest AI news from today"
    )
    skills = {"web_search": mock_skill}

    processor = _build_server_process_input(mock_brain, mock_memory, skills)
    processor.process("find the latest AI news today")

    # brain.think should be called twice: once for initial response, once to summarize results
    assert mock_brain.think.call_count == 2
    second_call_args = mock_brain.think.call_args_list[1]
    assert "Found: Latest AI news from today" in second_call_args[0][0]


def test_blocked_skill_not_executed(mock_brain, mock_memory):
    """BLOCKED skills should never execute."""
    mock_brain.think.return_value = "Let me run that command."
    mock_brain.parse_skill_calls.return_value = [
        {"skill": "system_control", "parameters": {"action": "run_command", "command": "rm -rf /"}}
    ]

    mock_skill = MagicMock()
    skills = {"system_control": mock_skill}

    # Use a safety guard that blocks dangerous commands
    safety = SafetyGuard(require_confirmation=True)

    processor = _build_server_process_input(mock_brain, mock_memory, skills, safety)
    processor.process("delete everything on the system")

    # The dangerous skill should NOT have been executed
    mock_skill.execute.assert_not_called()


def test_response_stripped_of_json(mock_brain, mock_memory):
    """JSON skill blocks should be stripped from the final response."""
    raw_response = 'Here is what I found. {"skill": "web_search", "parameters": {"query": "test"}} Great!'
    mock_brain.think.return_value = raw_response
    mock_brain.parse_skill_calls.return_value = []
    # Override the side_effect from conftest to actually strip the JSON
    mock_brain.strip_skill_blocks.side_effect = None
    mock_brain.strip_skill_blocks.return_value = "Here is what I found. Great!"

    skills = {}  # No skills registered — no fallback
    processor = _build_server_process_input(mock_brain, mock_memory, skills)
    result = processor.process("hello")

    assert '"skill"' not in result


def test_web_search_safety_is_safe():
    """web_search should always be classified as SAFE risk."""
    safety = SafetyGuard(require_confirmation=True)
    action = safety.request_action("web_search", {"query": "test"})
    assert action.risk_level == RiskLevel.SAFE
    assert action.approved is True  # SAFE actions are auto-approved
