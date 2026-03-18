"""
Real-world smoke test for Browser Automation.
Run this to verify that the Chromium binary is correctly installed and working.
Usage: python3 tests/smoke_browser.py
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from winston.skills.browser_skill import BrowserSkill

def test_real_browser():
    print("--- Starting Browser Smoke Test ---")
    skill = BrowserSkill()
    
    test_url = "https://google.com"
    print(f"Testing navigation and screenshot of {test_url}...")
    
    try:
        # Test full flow (navigate + screenshot)
        result = skill.execute(url=test_url, action="screenshot_page")
        
        if result.success:
            print(f"SUCCESS: {result.message}")
            if "path" in result.data:
                print(f"Screenshot Path: {result.data['path']}")
                if Path(result.data['path']).exists():
                    print("Verified: File exists on disk.")
                else:
                    print("ERROR: File does NOT exist on disk.")
        else:
            print(f"FAILED: {result.message}")
            sys.exit(1)
            
        print("\nTesting text extraction...")
        result = skill.execute(action="extract_text")
        if result.success:
            print(f"SUCCESS: Extracted {len(result.data['text'])} characters.")
        else:
            print(f"FAILED: {result.message}")
            
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        print("\nCleaning up...")
        skill.cleanup()
        print("Done.")

if __name__ == "__main__":
    test_real_browser()
