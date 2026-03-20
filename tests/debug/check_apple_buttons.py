"""Check Apple's product selection buttons after removing nav curtain."""
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="en-US")
    page = ctx.new_page()
    page.goto("https://www.apple.com/shop/buy-mac/macbook-pro", wait_until="load")
    page.wait_for_timeout(3000)

    # Remove the globalnav-curtain
    removed = page.evaluate('(() => { const el = document.getElementById("globalnav-curtain"); if (el) { el.remove(); return true; } return false; })()')
    print(f"Removed globalnav-curtain: {removed}")
    page.wait_for_timeout(500)

    # Check what 'Select' or 'Buy' buttons exist
    result = page.evaluate("""() => {
        const keywords = ['select', 'buy', 'choose', 'add to bag', 'configure'];
        const links = Array.from(document.querySelectorAll('a')).filter(a => {
            const t = (a.innerText||'').trim().toLowerCase();
            return keywords.some(k => t.includes(k));
        }).map(a => ({text:(a.innerText||'').trim().slice(0,80), href:a.href, visible: a.offsetParent!==null}));
        const btns = Array.from(document.querySelectorAll('button')).filter(b => {
            const t = (b.innerText||'').trim().toLowerCase();
            return keywords.some(k => t.includes(k));
        }).map(b => ({text:(b.innerText||'').trim().slice(0,80), visible: b.offsetParent!==null}));
        return {links, buttons: btns};
    }""")
    print(json.dumps(result, indent=2))

    # Now try the aria snapshot for product cards
    print("\\n=== ARIA SNAPSHOT (searching for product selection) ===")
    snap = page.locator(":root").aria_snapshot(timeout=15000)
    # Find lines with prices or Select
    for line in snap.split("\\n"):
        low = line.lower()
        if any(w in low for w in ["select", "buy", "price", "$", "macbook", "chip", "choose", "configure"]):
            print(line)

    # Try clicking the last product card
    print("\\n=== CLICK TEST (last product) ===")
    try:
        # Look for any clickable product card
        cards = page.locator('[data-autom*="productSelection"]')
        count = cards.count()
        print(f"Product cards found: {count}")
        if count > 0:
            cards.last.click(timeout=5000)
            page.wait_for_timeout(2000)
            print(f"After click: {page.url}")
    except Exception as e:
        print(f"Click failed: {e}")

    browser.close()
