"""
Test: Browser Agent — Order groceries on Flink.

This test:
1. Navigates to flink.com
2. Uses the browser agent to search for items and add them to cart
3. Takes a screenshot of the cart

Usage:
  First-time: Run  python tests/flink_login_setup.py  to log in and save profile.
  Then:       python tests/test_flink_grocery.py
"""

import os
import sys
import logging
import time

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
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
logger = logging.getLogger("test_flink")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── Import Winston modules ────────────────────────────────────────────
from winston.config import load_config
from winston.core.brain import Brain
from winston.core.safety import SafetyGuard, RiskOverride
from winston.skills.browser_skill import BrowserSkill
from winston.core.browser_agent import InteractiveBrowserAgent

FLINK_PROFILE_DIR = os.path.expanduser("~/.winston/browser/flink/user-data")


def main():
    print("\n" + "=" * 60)
    print("  BROWSER AGENT TEST: Flink Grocery Shopping")
    print("  Using: OpenAI API (gpt-5.2)")
    print("=" * 60 + "\n")

    # Check for persistent profile
    has_profile = os.path.isdir(FLINK_PROFILE_DIR) and os.listdir(FLINK_PROFILE_DIR)
    if has_profile:
        print(f"✓ Persistent profile found: {FLINK_PROFILE_DIR}")
        print("  → Using saved login session (cookies, localStorage, etc.)")
    else:
        print("⚠ No persistent profile found.")
        print(f"  → Run 'python tests/flink_login_setup.py' first to log in.")
        print("  → Continuing without login (captcha + address entry needed).\n")

    config = load_config()
    brain = Brain(config)
    brain.change_model("gpt-5.2")
    print(f"✓ Brain initialized — provider: {brain.get_current_provider()}")

    safety = SafetyGuard(require_confirmation=True)

    # Use persistent profile if available, otherwise fall back to ephemeral
    if has_profile:
        browser = BrowserSkill(headless=False, user_data_dir=FLINK_PROFILE_DIR)
    else:
        browser = BrowserSkill(headless=False)

    brain.register_skills({"browser": browser})
    print("✓ Browser skill registered (stealth mode enabled)")

    agent = InteractiveBrowserAgent(brain, safety, browser)
    print("✓ InteractiveBrowserAgent created")

    # Pre-open Flink
    print("\n🌐 Opening Flink...")
    browser._ensure_browser()
    browser._page.goto("https://www.goflink.com/shop/de-DE/", timeout=30000)
    time.sleep(3)

    # Check if captcha is blocking, if so retry navigation
    for attempt in range(3):
        title = browser._page.title().lower()
        content = browser._page.content()[:2000].lower()
        if "captcha" in content or "captcha-delivery" in content or "zugriff" in title:
            print(f"  ⚠ Bot-Sperre erkannt (Versuch {attempt + 1}/3), neuer Versuch in 5s...")
            time.sleep(5)
            try:
                browser._page.goto("https://www.goflink.com/shop/de-DE/", timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass
            time.sleep(3)
        else:
            print(f"  ✓ Shop geladen: {browser._page.title()}")
            break
    else:
        # All retries failed — fall back to manual captcha solving
        print("\n  ❌ Stealth reicht nicht aus — Captcha manuell lösen!")
        print("     (Browser-Fenster: Captcha lösen, dann Enter drücken)\n")
        input(">>> Captcha gelöst? Enter drücken... ")
        print("✓ Weiter geht's!")
    print()

    # Build task prompt — simpler when logged in with saved session
    if has_profile:
        task = (
            "Du bist bereits auf goflink.com/shop/de-DE/ und eingeloggt. "
            "Deine Adresse und Liefergebiet sind bereits gespeichert.\n\n"
            "Füge folgende Produkte in den Warenkorb:\n"
            "1. Bananen\n"
            "2. Vollmilch (1 Liter)\n"
            "3. Brot (irgendein Brot)\n\n"
            "VORGEHENSWEISE:\n"
            "1. Zuerst einen snapshot machen um die aktuelle Seite zu sehen.\n"
            "2. Falls Cookie-Banner erscheint, akzeptiere es.\n"
            "3. Suche nach dem ersten Produkt (Bananen) — nutze die Suchfunktion/Suchleiste.\n"
            "4. Klicke auf den '+' Button neben dem Produkt (addProduct-Button im Snapshot).\n"
            "5. Wiederhole für Milch und Brot.\n"
            "6. Am Ende: Klicke auf das Warenkorb-Icon oben rechts (NICHT open_page zu /cart/).\n"
            "   Flink ist eine SPA — open_page zerstört den Warenkorb-State!\n"
            "7. Snapshot und screenshot_page machen.\n"
            "8. Berichte welche Produkte drin sind und den Gesamtpreis.\n\n"
            "KRITISCHE REGELN:\n"
            "- NIEMALS open_page zu /cart/ oder /checkout/ benutzen! Das zerstört den Warenkorb!\n"
            "  Stattdessen das Warenkorb-Icon/Button oben rechts klicken.\n"
            "- NICHT zur Kasse gehen oder bestellen.\n"
            "- Nutze click_ref für Buttons. Bei Overlay-Problemen nutze run_script mit ref.\n"
            "- Nutze die Suchfunktion (Suche/Search-Icon) um Produkte zu finden.\n"
            "- Snapshot häufig nehmen um den aktuellen Stand zu sehen.\n"
            "- Popups und Banner immer sofort schließen/akzeptieren."
        )
    else:
        task = (
            "Du bist bereits auf goflink.com/shop/de-DE/. Füge folgende Produkte in den Warenkorb:\n"
            "1. Bananen\n"
            "2. Vollmilch (1 Liter)\n"
            "3. Brot (irgendein Brot)\n\n"
            "WICHTIG — FLINK FLOW OHNE LOGIN:\n"
            "Wenn du auf ein Produkt klickst oder 'Zum Warenkorb hinzufügen' drückst,\n"
            "erscheint ein Adress-Popup (DOM-Popup, kein Browser-Dialog).\n"
            "Du musst dann:\n"
            "  a) Die PLZ 44145 eingeben und Dortmund als Liefergebiet auswählen.\n"
            "  b) 'Weiter' drücken.\n"
            "  c) Runterscrollen und die Bedingungen akzeptieren / bestätigen.\n"
            "Danach kann es passieren, dass ein Browser-Geolocation-Popup kommt —\n"
            "das wird automatisch abgelehnt (ignoriere es einfach).\n"
            "Dann nochmal versuchen das Produkt hinzuzufügen.\n\n"
            "VORGEHENSWEISE:\n"
            "1. Zuerst einen snapshot machen um die aktuelle Seite zu sehen.\n"
            "2. Falls Cookie-Banner erscheint, akzeptiere es.\n"
            "3. Suche nach dem ersten Produkt (Bananen) — nutze die Suchfunktion/Suchleiste.\n"
            "4. Klicke auf den '+' Button neben dem Produkt (der grüne/blaue Kreis-Button mit +).\n"
            "   Wenn ein 'addProduct' Button im Snapshot sichtbar ist, benutze diesen.\n"
            "5. Wenn ein Adress-/Standort-Popup erscheint: PLZ 44145 eingeben, Dortmund wählen,\n"
            "   weiter, scrollen, akzeptieren.\n"
            "6. Danach nochmal das Produkt hinzufügen.\n"
            "7. Wiederhole für Milch und Brot.\n"
            "8. Am Ende: Klicke auf das Warenkorb-Icon oben rechts (NICHT open_page zu /cart/).\n"
            "   Flink ist eine SPA — wenn du open_page zu /cart/ benutzt, verlierst du den\n"
            "   Warenkorb-State! Das Warenkorb-Icon öffnet eine Sidebar mit den Produkten.\n"
            "9. Snapshot und screenshot_page machen.\n"
            "10. Berichte welche Produkte drin sind und den Gesamtpreis.\n\n"
            "KRITISCHE REGELN:\n"
            "- NIEMALS open_page zu /cart/ oder /checkout/ benutzen! Das zerstört den Warenkorb!\n"
            "  Stattdessen das Warenkorb-Icon/Button oben rechts klicken.\n"
            "- NICHT zur Kasse gehen oder bestellen.\n"
            "- Nutze click_ref für Buttons. Bei Overlay-Problemen nutze run_script mit ref.\n"
            "- Nutze die Suchfunktion (Suche/Search-Icon) um Produkte zu finden.\n"
            "- Wenn die Seite nach Login fragt, überspringe das wenn möglich.\n"
            "- Snapshot häufig nehmen um den aktuellen Stand zu sehen.\n"
            "- Popups und Banner immer sofort schließen/akzeptieren.\n"
            "- Wenn du nach dem Hinzufügen eines Produkts ein Popup/Sidebar siehst, schließe es\n"
            "  und suche das nächste Produkt."
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
