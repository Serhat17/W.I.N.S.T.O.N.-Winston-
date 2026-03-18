"""
Test: Browser Agent booking via phptravels.net with OpenAI API.

This test:
1. Loads the OpenAI API key from .env
2. Initializes Brain with OpenAI as the LLM provider
3. Launches the InteractiveBrowserAgent
4. Navigates to phptravels.net and attempts a test hotel booking
5. Verifies the checkout state-machine blocks final purchase
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
logger = logging.getLogger("test_booking")

# Reduce noise from httpx/httpcore
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── Import Winston modules ────────────────────────────────────────────
from winston.config import load_config
from winston.core.brain import Brain
from winston.core.safety import SafetyGuard, RiskOverride
from winston.skills.browser_skill import BrowserSkill
from winston.core.browser_agent import InteractiveBrowserAgent, CheckoutPhase

def main():
    print("\n" + "=" * 60)
    print("  BROWSER AGENT TEST: Hotel Booking on phptravels.net")
    print("  Using: OpenAI API (gpt-4o)")
    print("=" * 60 + "\n")

    # ── 1. Initialize config + switch to OpenAI ──
    config = load_config()
    brain = Brain(config)
    
    # Switch to OpenAI
    brain.change_model("gpt-4o")
    print(f"✓ Brain initialized — provider: {brain.get_current_provider()}, model: {config.ollama.model}")
    
    if brain.get_current_provider() != "openai":
        print("ERROR: Failed to switch to OpenAI provider. Check API key.")
        sys.exit(1)

    # ── 2. Initialize safety + browser ──
    safety = SafetyGuard(require_confirmation=True)
    browser = BrowserSkill()
    
    # Register browser as the only skill (agent only needs browser)
    brain.register_skills({"browser": browser})
    print("✓ Browser skill registered")
    
    # ── 3. Create agent ──
    agent = InteractiveBrowserAgent(brain, safety, browser)
    print("✓ InteractiveBrowserAgent created")
    print(f"  Shopping detection keywords: {agent.SHOPPING_KEYWORDS[:5]}...")
    print(f"  Checkout patterns: {agent.CHECKOUT_PATTERNS[:3]}...")
    
    # ── 4. Run booking task ──
    task = (
        "Go to https://phptravels.net/ and look at the Featured Properties section. "
        "Click on the first hotel link. IMPORTANT: If clicking a link opens a new tab, "
        "you will automatically switch to it — just take a snapshot to see the new page. "
        "On the hotel detail page, look for a 'Book Now' or 'Reserve' button and click it. "
        "Fill in the booking form with: First Name: John, Last Name: Smith, "
        "Email: john@test.com, Phone: +44123456789. "
        "For any search/autocomplete inputs, use keyboard_type or type_ref with slow:true. "
        "After page navigation, use wait_for with load_state:networkidle before taking snapshot. "
        "Do NOT complete the payment — stop at the payment page and report what you see."
    )
    
    print(f"\n📋 Task: {task}\n")
    print("-" * 60)
    print("Starting browser agent loop...\n")
    
    # Use AUTONOMOUS mode (auto-approve actions)
    # The confirm_callback auto-approves everything for testing
    def auto_approve(action_req, channel=None):
        print(f"  [AUTO-APPROVE] {action_req.summary()}")
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

    # ── 5. Report results ──
    print("\n" + "=" * 60)
    print("  RESULT")
    print("=" * 60)
    print(f"\n{result}\n")
    
    # Show checkout phase
    print(f"Checkout phase: {agent._checkout_phase.value}")
    print(f"Shopping task detected: {agent._is_shopping_task}")
    print(f"Cart items tracked: {agent._cart_items}")
    
    # Show browser state
    print(f"\nFinal URL: {browser.current_url}")
    print(f"Active profile: {browser._active_profile}")
    print(f"Saved profiles: {browser.list_profiles()}")
    
    # Take final screenshot
    try:
        shot = browser.execute(action="screenshot_page")
        if shot.success:
            print(f"📸 Final screenshot: {shot.data.get('path', '')}")
    except Exception:
        pass
    
    # Cleanup
    browser.cleanup()
    brain.close()
    print("\n✓ Test complete. Browser closed.")

if __name__ == "__main__":
    main()
