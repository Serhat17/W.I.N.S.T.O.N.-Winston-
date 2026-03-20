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
    print("  Using: OpenAI API (gpt-5.2)")
    print("=" * 60 + "\n")

    config = load_config()
    brain = Brain(config)
    brain.change_model("gpt-5.2")
    print(f"✓ Brain initialized — provider: {brain.get_current_provider()}")

    safety = SafetyGuard(require_confirmation=True)
    browser = BrowserSkill()
    brain.register_skills({"browser": browser})
    print("✓ Browser skill registered")

    agent = InteractiveBrowserAgent(brain, safety, browser)
    print("✓ InteractiveBrowserAgent created")

    task = (
        "Configure the most expensive MacBook Pro on apple.com and add it to the bag.\n\n"
        "STEP-BY-STEP PLAN:\n"
        "1. open_page https://www.apple.com/shop/buy-mac/macbook-pro\n"
        "2. Use run_script to find config links matching /shop/buy-mac/macbook-pro/. Pick the most expensive.\n"
        "3. open_page to the config page URL.\n"
        "4. Snapshot the page. For EACH option group (Size, Color, Display, Chip, sub-Chip, Memory, Storage):\n"
        "   Find the most expensive radio button ref and click it with:\n"
        "   {\"action\":\"run_script\",\"ref\":\"eN\",\"script\":\"el => { el.click(); return el.checked }\"}\n"
        "5. After the main config options, scroll down. There will be more sections:\n"
        "   - Software extras (Final Cut Pro, Logic Pro) — select the most expensive options.\n"
        "   - Trade-in — select 'No trade-in'.\n"
        "   - Payment method — select 'Buy' (full price). May start disabled; scroll + snapshot until enabled.\n"
        "   - AppleCare — select the most expensive AppleCare option when enabled.\n"
        "6. After ALL sections are completed, 'Add to Bag' button will appear in the snapshot.\n"
        "   Take snapshot_interactive, find the 'Add to Bag' button ref.\n"
        "   Click it using click_ref (NOT page-level run_script without ref).\n"
        "7. CRITICAL WAIT: After clicking Add to Bag, you MUST do these steps IN ORDER:\n"
        "   a. wait_for load_state:networkidle\n"
        "   b. wait_for delay:5000\n"
        "   c. snapshot to check the page — look for 'bag' badge, confirmation, or item count.\n"
        "8. ONLY after confirming Add to Bag succeeded: open_page https://www.apple.com/shop/bag\n"
        "9. snapshot and screenshot_page. Report the cart contents and total price.\n\n"
        "CRITICAL RULES:\n"
        "- For radio buttons, ALWAYS use run_script with ref: el => { el.click(); return el.checked }\n"
        "- For 'Add to Bag' button, use click_ref with the ref from the snapshot. Do NOT use page-level JS.\n"
        "- Do NOT go to /shop/bag until AFTER Add to Bag is clicked AND you have waited for networkidle.\n"
        "- Do NOT proceed to checkout.\n"
        "- Take snapshots frequently to see updated/newly-enabled options.\n"
        "- If the bag is empty after navigating to /shop/bag, the Add to Bag click did not work.\n"
        "  Go back to the config page, find the Add to Bag ref, and try clicking it again differently.\n"
        "- Some sections enable only after previous sections are completed — scroll and snapshot to check."
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
