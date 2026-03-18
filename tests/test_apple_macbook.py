"""
Test: Browser Agent — Configure MacBook Pro with max specs on apple.com.

This test:
1. Navigates to apple.com MacBook Pro page
2. Uses the browser agent to select the highest-spec configuration
3. Takes a screenshot of the configured product / cart
"""

import os
import sys
import logging

# Load .env file manually
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(project_root, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ[key.strip()] = value.strip()
else:
    print(f"WARNING: .env not found at {env_path}")

# Verify API key
api_key = os.getenv("OPENAI_API_KEY", "")
if not api_key:
    print("ERROR: OPENAI_API_KEY not found in .env or environment.")
    sys.exit(1)
print(f"✓ OpenAI API key loaded ({api_key[:8]}...)")

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("test_apple")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── Import Winston modules ────────────────────────────────────────────
from winston.config import load_config
from winston.core.brain import Brain
from winston.core.safety import SafetyGuard, RiskOverride
from winston.skills.browser_skill import BrowserSkill
from winston.core.browser_agent import InteractiveBrowserAgent

def main():
    print("\n" + "=" * 60)
    print("  BROWSER AGENT TEST: Apple MacBook Pro Max Config")
    print("  Using: OpenAI API (gpt-4o)")
    print("=" * 60 + "\n")

    config = load_config()
    brain = Brain(config)
    brain.change_model("gpt-4o")
    print(f"✓ Brain initialized — provider: {brain.get_current_provider()}")

    safety = SafetyGuard(require_confirmation=True)
    browser = BrowserSkill()
    brain.register_skills({"browser": browser})
    print("✓ Browser skill registered")

    agent = InteractiveBrowserAgent(brain, safety, browser)
    print("✓ InteractiveBrowserAgent created")

    task = (
        "Go to https://www.apple.com/shop/buy-mac/macbook-pro and select the most expensive "
        "MacBook Pro model available. On the configuration page, upgrade EVERY option to the "
        "maximum/most expensive choice: chip, CPU cores, GPU cores, RAM, storage, etc. "
        "After configuring everything to the max, click 'Add to Bag'. "
        "Once the item is in the bag, go to the bag/cart page and take a screenshot. "
        "Then report the final configuration and total price as plain text. "
        "Do NOT proceed to checkout or payment. "
        "IMPORTANT: If clicking a link doesn't navigate, use run_script to find the actual "
        "link URL from the DOM, then use open_page to go there directly."
    )

    print(f"\n📋 Task: {task}\n")
    print("-" * 60)
    print("Starting browser agent...\n")

    def auto_approve(action_req, channel=None):
        logger.info(f"[AUTO-APPROVE] {action_req.description}")
        safety.approve_action(action_req.id)
        return True

    try:
        result = agent.execute_task(
            user_input=task,
            override=RiskOverride.AUTONOMOUS,
            channel=None,
            confirm_callback=auto_approve,
        )
    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        result = f"ERROR: {e}"

    print("\n" + "=" * 60)
    print("  RESULT")
    print("=" * 60)
    print(f"\n{result}\n")

    print(f"Final URL: {browser.current_url}")

    # Take final screenshot
    try:
        shot = browser.execute(action="screenshot_page")
        if shot.success:
            print(f"📸 Final screenshot: {shot.data.get('path', '')}")
    except Exception:
        pass

    browser.cleanup()
    brain.close()
    print("\n✓ Test complete. Browser closed.")

if __name__ == "__main__":
    main()
