#!/usr/bin/env python3
"""
Flink Login Setup — One-time manual login to save persistent browser profile.

Opens a headed Chromium browser with the same stealth settings as WINSTON,
navigates to goflink.com, and waits for you to log in manually.
Once logged in, close the browser or press Ctrl+C — the session is automatically
persisted to ~/.winston/browser/flink/user-data/.

Usage:
    python tests/flink_login_setup.py

After running this once, the Flink test will reuse the saved session (cookies,
localStorage, sessionStorage, IndexedDB) so you skip captcha + address entry.
"""

import os
import sys
import time

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FLINK_PROFILE_DIR = os.path.expanduser("~/.winston/browser/flink/user-data")


def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(FLINK_PROFILE_DIR, exist_ok=True)

    print(f"\n{'='*60}")
    print("  FLINK LOGIN SETUP")
    print(f"{'='*60}")
    print(f"\n  Profil-Verzeichnis: {FLINK_PROFILE_DIR}")
    print("  Browser wird geöffnet — bitte bei Flink einloggen.")
    print("  Nach dem Login einfach das Browserfenster schließen.")
    print(f"\n{'='*60}\n")

    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            FLINK_PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        # Apply stealth
        try:
            from playwright_stealth import Stealth
            stealth = Stealth(
                navigator_webdriver=True,
                navigator_plugins=True,
                navigator_permissions=True,
                navigator_languages=True,
                navigator_platform=True,
                navigator_vendor=True,
                navigator_user_agent=True,
                chrome_app=True,
                chrome_csi=True,
                chrome_load_times=True,
                webgl_vendor=True,
                navigator_platform_override="MacIntel",
                navigator_languages_override=("de-DE", "de", "en-US", "en"),
            )
            stealth.apply_stealth_sync(context)
            print("  ✓ Stealth-Modus aktiviert")
        except ImportError:
            print("  ⚠ playwright-stealth nicht installiert")

        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.goflink.com/de-DE/", wait_until="domcontentloaded", timeout=30000)
        print("  ✓ goflink.com geladen")
        print("\n  → Bitte jetzt einloggen und Adresse eingeben.")
        print("  → Wenn fertig: Browserfenster schließen.\n")

        # Wait until user closes the browser
        try:
            while context.pages:
                time.sleep(1)
        except Exception:
            pass

        print("\n  ✓ Browser geschlossen — Profil gespeichert!")
        print(f"  Gespeichert in: {FLINK_PROFILE_DIR}\n")

    except KeyboardInterrupt:
        print("\n\n  Abgebrochen — Profil wurde trotzdem gespeichert.")
    finally:
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


if __name__ == "__main__":
    main()
