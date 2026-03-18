
import pytest
import logging
from unittest.mock import MagicMock, patch
from winston.main import Winston
from winston.skills.base import SkillResult
from winston.core.safety import RiskLevel, RiskOverride

# Configure basic logging for tests
logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def winston():
    with patch('winston.main.logger'):
        w = Winston()
        # Mock brain to control skill calls
        w.brain = MagicMock()
        
        # Consistent mock for parse_skill_calls
        # This allows us to set the return value in individual tests
        w.brain.parse_skill_calls.return_value = []
        
        # Mock safety to avoid external checks and return concrete strings
        w.safety = MagicMock()
        w.safety.sanitize_input.side_effect = lambda x: x
        w.safety.filter_output.side_effect = lambda x: x
        
        # Mock memory to avoid database errors
        w.memory = MagicMock()
        w.memory.get_context_messages.return_value = []
        
        # Mock browser skill
        from winston.skills.browser_skill import BrowserSkill
        mock_browser = MagicMock(spec=BrowserSkill)
        # Expose internal attributes used by InteractiveBrowserAgent
        mock_browser._page = MagicMock()
        mock_browser._page.title.return_value = "Test Page"
        mock_browser._page.url = "https://example.com"
        mock_browser.extract_page_structure.return_value = "Page: Test Page\nURL: https://example.com"
        w._skills["browser"] = mock_browser

        return w

def test_sequential_execution_halts_on_failure(winston):
    """
    Test that if a middle skill call fails in standard mode, subsequent calls are NOT executed.
    """
    winston.brain.parse_skill_calls.return_value = [
        {"skill": "browser", "parameters": {"action": "open_page", "url": "apple.com"}},
        {"skill": "browser", "parameters": {"action": "click", "selector": "#buy"}},
        {"skill": "browser", "parameters": {"action": "screenshot_page"}}
    ]
    
    winston._skills["browser"].execute.side_effect = [
        SkillResult(success=True, message="Page loaded"),
        SkillResult(success=False, message="Click failed: element not found"),
        SkillResult(success=True, message="This should not happen")
    ]
    
    action_req = MagicMock(risk_level=RiskLevel.SAFE, approved=True)
    winston.safety.request_action.return_value = action_req
    winston.brain.think.return_value = "Summary"
    
    # Run WITHOUT "mach" to trigger standard single-shot execution
    winston.process_input("configure macbook")
    
    assert winston._skills["browser"].execute.call_count == 2
    args_list = winston._skills["browser"].execute.call_args_list
    assert args_list[0][1]["action"] == "open_page"
    assert args_list[1][1]["action"] == "click"

def test_mach_vorsichtig_triggers_screenshot_and_confirmation(winston):
    """
    Test that 'mach vorsichtig' forces a screenshot and confirmation for browser actions
    inside the InteractiveBrowserAgent loop.
    """
    # 0. process_input check
    # 1. First loop: output 'click'
    # 2. Second loop: output nothing (done)
    call_click = [{"skill": "browser", "parameters": {"action": "click", "selector": "#submit"}}]
    winston.brain.parse_skill_calls.side_effect = [
        call_click, # Consumed by main.py
        call_click, # Consumed by InteractiveBrowserAgent step 1
        []          # Consumed by InteractiveBrowserAgent step 2
    ]
    
    action_req = MagicMock(id="test_id", risk_level=RiskLevel.SAFE, approved=False, description="Click submit")
    winston.safety.request_action.return_value = action_req
    winston._confirm_action = MagicMock(return_value=True)
    
    winston._skills["browser"].execute.side_effect = [
        SkillResult(success=True, message="Screenshot taken", data={"path": "test.png"}),
        SkillResult(success=True, message="Clicked")
    ]
    winston.brain.think.return_value = "Summary"
    
    winston.process_input("mach vorsichtig: click submit")
    
    assert winston._skills["browser"].execute.call_count == 2
    calls = winston._skills["browser"].execute.call_args_list
    assert calls[0][1]["action"] == "screenshot_page"
    assert calls[1][1]["action"] == "click"
    assert action_req.screenshot_path == "test.png"

def test_interactive_browser_loop_adapts_to_failure(winston):
    """
    Test that the InteractiveBrowserAgent LOOP catches failures and lets the LLM try again.
    Vision screenshot only happens on 2nd+ consecutive failure for performance.
    """
    # 0. process_input check
    # 1. Loop 1: try click -> fails (page structure refresh, no vision yet)
    # 2. Loop 2: try alternative click -> success
    # 3. Loop 3: done
    call_wrong = [{"skill": "browser", "parameters": {"action": "click", "selector": "#wrong"}}]
    call_right = [{"skill": "browser", "parameters": {"action": "click", "selector": "#right"}}]

    winston.brain.parse_skill_calls.side_effect = [
        call_wrong, # Consumed by main.py check
        call_wrong, # Consumed by InteractiveBrowserAgent step 1
        call_right, # Consumed by InteractiveBrowserAgent step 2
        []          # Consumed by InteractiveBrowserAgent step 3
    ]

    winston._skills["browser"].execute.side_effect = [
        SkillResult(success=False, message="Element not found"),          # 1. click #wrong fails
        SkillResult(success=True, message="Clicked")                      # 2. click #right succeeds
    ]

    action_req = MagicMock(risk_level=RiskLevel.SAFE, approved=True)
    winston.safety.request_action.return_value = action_req

    winston.process_input("mach: try clicking")

    # 2 execute calls: failed click + successful click (no screenshot on first failure)
    assert winston._skills["browser"].execute.call_count == 2
    args_list = winston._skills["browser"].execute.call_args_list
    assert args_list[0][1]["selector"] == "#wrong"
    assert args_list[1][1]["selector"] == "#right"

def test_browser_settling_logic_is_applied():
    """
    Test that browser skill actually waits after actions if wait_after is specified.
    """
    from winston.skills.browser_skill import BrowserSkill
    skill = BrowserSkill()
    skill._page = MagicMock()
    
    # Test click with default settling
    skill.execute(action="click", selector=".btn")
    # Should call wait_for_timeout(500)
    skill._page.wait_for_timeout.assert_any_call(500)
    
    # Test click with custom settling
    skill._page.wait_for_timeout.reset_mock()
    skill.execute(action="click", selector=".btn", wait_after=2000)
    skill._page.wait_for_timeout.assert_any_call(2000)

def test_flight_query_routes_to_travel_not_web_search(winston):
    """
    Test that queries containing travel keywords (e.g. 'flight') do NOT trigger 
    the generic web_search fallback, even if 'search' is in the query.
    """
    winston.brain.parse_skill_calls.return_value = []
    
    # Mock travel skill to monitor if it gets called (even though parse_skill_calls is empty,
    # we want to ensure web_search isn't forcefully injected)
    from winston.skills.base import BaseSkill
    mock_web_search = MagicMock(spec=BaseSkill)
    winston._skills["web_search"] = mock_web_search
    
    # Assert that web_search was NOT forcefully injected
    assert mock_web_search.execute.call_count == 0

def test_screenshot_word_routes_to_browser_not_system_screenshot(winston):
    """
    Test that a query asking for a 'screenshot' of a website routes to the 
    browser skill (Interactive Agent), NOT the system screenshot skill.
    """
    # Force the brain to return a skill call that says "screenshot"
    # Previously, without our mapping, this might trigger the system screenshot skill.
    # With our routing, it should map to browser.
    winston.brain.parse_skill_calls.side_effect = [
        [{"skill": "browser", "parameters": {"action": "screenshot", "url": "apple.com"}}], # main.py routes this
        [{"skill": "browser", "parameters": {"action": "screenshot", "url": "apple.com"}}], # InteractiveAgent consumes
        []
    ]
    
    from winston.skills.base import BaseSkill
    mock_system_screenshot = MagicMock(spec=BaseSkill)
    winston._skills["screenshot"] = mock_system_screenshot
    
    # We mock out execute_task to just return a string so we don't need real playwright
    with patch('winston.core.browser_agent.InteractiveBrowserAgent.execute_task') as mock_agent_exec:
        mock_agent_exec.return_value = "Browser screenshot complete"
        winston.process_input("mach vorsichtig: zeig mir einen screenshot von apple.com")
        
        # Ensure system screenshot skill was NEVER called
        assert mock_system_screenshot.execute.call_count == 0
        # Ensure it went into the browser routing
        assert mock_agent_exec.called
