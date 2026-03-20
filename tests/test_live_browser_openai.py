#!/usr/bin/env python3
"""
Live browser test using OpenAI API + real Playwright browser.
Tests the full InteractiveBrowserAgent loop: LLM thinks → browser acts → observe → repeat.

Usage:
    python tests/test_live_browser_openai.py

Requires:
    - OPENAI_API_KEY in .env
    - Playwright browsers installed (npx playwright install chromium)
"""

import os
import sys
import time
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from winston.config import WinstonConfig, OllamaConfig, ProvidersConfig
from winston.core.brain import Brain
from winston.core.safety import SafetyGuard, RiskOverride
from winston.skills.browser_skill import BrowserSkill
from winston.core.browser_agent import InteractiveBrowserAgent

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_live")

# Reduce noise from httpx/playwright
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def create_openai_brain() -> Brain:
    """Create a Brain instance configured to use OpenAI GPT-5.2."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in .env")
        sys.exit(1)

    config = WinstonConfig(
        ollama=OllamaConfig(
            model="gpt-5.2",
            temperature=0.1,  # Low temp for precise browser actions
        ),
        providers=ProvidersConfig(
            openai_api_key=api_key,
        ),
    )

    brain = Brain(config)
    brain._current_provider = "openai"
    return brain


def test_simple_search():
    """Test: Search for something on Wikipedia and extract info."""
    print("\n" + "=" * 70)
    print("TEST 1: Wikipedia search — 'Python programming language'")
    print("=" * 70 + "\n")

    brain = create_openai_brain()
    safety = SafetyGuard()
    browser = BrowserSkill(config=None)
    agent = InteractiveBrowserAgent(brain, safety, browser)

    try:
        result = agent.execute_task(
            "Go to wikipedia.org, search for 'Python programming language', "
            "and tell me when Python was first released and who created it.",
            override=RiskOverride.AUTONOMOUS,
        )
        print(f"\n{'='*70}")
        print(f"RESULT: {result}")
        print(f"{'='*70}\n")
        
        # Basic validation
        success = "guido" in result.lower() or "1991" in result.lower() or "python" in result.lower()
        print(f"✅ PASS" if success else f"❌ FAIL — expected mention of Guido/1991/Python")
        return success
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return False
    finally:
        browser.cleanup()


def test_hacker_news():
    """Test: Read top stories from Hacker News."""
    print("\n" + "=" * 70)
    print("TEST 2: Hacker News — read top 3 story titles")
    print("=" * 70 + "\n")

    brain = create_openai_brain()
    safety = SafetyGuard()
    browser = BrowserSkill(config=None)
    agent = InteractiveBrowserAgent(brain, safety, browser)

    try:
        result = agent.execute_task(
            "Go to news.ycombinator.com and tell me the titles of the top 3 stories on the front page.",
            override=RiskOverride.AUTONOMOUS,
        )
        print(f"\n{'='*70}")
        print(f"RESULT: {result}")
        print(f"{'='*70}\n")

        # Should have some story titles, not an error/abort message
        success = (
            len(result) > 50
            and "stopped" not in result.lower()
            and "could not" not in result.lower()
            and "maximum steps" not in result.lower()
        )
        print(f"✅ PASS" if success else f"❌ FAIL — agent got stuck or result too short")
        return success
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return False
    finally:
        browser.cleanup()


def test_form_fill():
    """Test: Fill a simple form on a test site."""
    print("\n" + "=" * 70)
    print("TEST 3: Form fill — httpbin.org/forms/post")
    print("=" * 70 + "\n")

    brain = create_openai_brain()
    safety = SafetyGuard()
    browser = BrowserSkill(config=None)
    agent = InteractiveBrowserAgent(brain, safety, browser)

    try:
        result = agent.execute_task(
            "Go to https://httpbin.org/forms/post and fill in the form with: "
            "Customer name: John Doe, Size: Large, Topping: Bacon. "
            "Then submit the form and tell me what the response says.",
            override=RiskOverride.AUTONOMOUS,
        )
        print(f"\n{'='*70}")
        print(f"RESULT: {result}")
        print(f"{'='*70}\n")

        success = "john" in result.lower() or "doe" in result.lower() or "bacon" in result.lower() or "large" in result.lower()
        print(f"✅ PASS" if success else f"❌ FAIL — expected form data in response")
        return success
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return False
    finally:
        browser.cleanup()


if __name__ == "__main__":
    print("\n🚀 W.I.N.S.T.O.N. Live Browser Test with OpenAI API")
    print("=" * 70)
    
    start = time.time()
    results = {}

    # Run tests
    results["Wikipedia Search"] = test_simple_search()
    results["Hacker News"] = test_hacker_news()
    results["Form Fill"] = test_form_fill()

    elapsed = time.time() - start

    # Summary
    print("\n" + "=" * 70)
    print(f"SUMMARY ({elapsed:.1f}s total)")
    print("=" * 70)
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {name}")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} tests passed")
    print("=" * 70)
    
    sys.exit(0 if passed == total else 1)
