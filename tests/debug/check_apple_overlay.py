"""Quick script to check what overlay Apple uses on the MacBook Pro shop page."""
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="en-US")
    page = ctx.new_page()
    page.goto("https://www.apple.com/shop/buy-mac/macbook-pro", wait_until="load")
    page.wait_for_timeout(3000)

    # 1. Check what element is at the center of the viewport
    overlay_info = page.evaluate("""() => {
        const el = document.elementFromPoint(window.innerWidth/2, window.innerHeight/2);
        if (!el) return {center: null};
        const info = {
            tag: el.tagName,
            id: el.id,
            className: String(el.className).slice(0, 200),
            role: el.getAttribute("role"),
            ariaLabel: el.getAttribute("aria-label"),
            text: (el.innerText||'').slice(0, 200),
            zIndex: window.getComputedStyle(el).zIndex,
            position: window.getComputedStyle(el).position,
            outerHTML: el.outerHTML.slice(0, 500),
        };
        // Find all fixed/sticky elements with high z-index
        const fixed = [];
        document.querySelectorAll('*').forEach(e => {
            const s = window.getComputedStyle(e);
            if ((s.position==='fixed'||s.position==='sticky') && e.offsetWidth>100 && e.offsetHeight>50) {
                fixed.push({
                    tag: e.tagName,
                    id: e.id,
                    className: String(e.className).slice(0, 100),
                    text: (e.innerText||'').slice(0, 100),
                    z: s.zIndex,
                    w: e.offsetWidth,
                    h: e.offsetHeight,
                    outerHTML: e.outerHTML.slice(0, 300),
                });
            }
        });
        return {center: info, fixedElements: fixed};
    }""")
    print("=== OVERLAY INFO ===")
    print(json.dumps(overlay_info, indent=2, default=str))

    # 2. Take a snapshot to see what the LLM would see
    print("\n=== ARIA SNAPSHOT (first 3000 chars) ===")
    try:
        snap = page.locator(":root").aria_snapshot(timeout=10000)
        print(snap[:3000])
    except Exception as e:
        print(f"Snapshot failed: {e}")

    # 3. Try clicking one of the "Select" buttons to see what intercepts
    print("\n=== CLICK TEST ===")
    try:
        btns = page.get_by_role("link", name="Select")
        count = btns.count()
        print(f"Found {count} 'Select' links")
        if count > 0:
            btns.last.click(timeout=5000)
            print(f"Click succeeded, now at: {page.url}")
    except Exception as e:
        print(f"Click failed: {e}")

    browser.close()
