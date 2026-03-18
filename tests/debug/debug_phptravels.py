"""Debug script: See what the browser snapshot looks like for phptravels.net."""
import os, sys
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from winston.skills.browser_skill import BrowserSkill

bs = BrowserSkill()
bs._ensure_browser()

# Navigate to phptravels.net
res = bs.execute(action="open_page", url="https://phptravels.net/")
print(f"Open: {res.message}")

# Take snapshot
snap = bs.snapshot()
print(f"\n=== SNAPSHOT ({len(snap)} chars) ===")
print(snap[:5000])
print("... [truncated]")

# Find all links with refs
import re
refs = re.findall(r'\[ref=(e\d+)\]', snap)
print(f"\n=== REFS FOUND: {len(refs)} ===")
# Show first 40 refs and their context
for ref in refs[:40]:
    idx = snap.find(f'[ref={ref}]')
    ctx = snap[max(0,idx-80):idx+80]
    print(f"  {ref}: ...{ctx}...")

# Check what the "Featured" section looks like
idx = snap.lower().find("featured")
if idx >= 0:
    print(f"\n=== FEATURED section ===")
    print(snap[max(0,idx-200):idx+1000])

# Check for target="_blank" links
print(f"\n=== Checking link targets ===")
page = bs._page
links = page.query_selector_all("a[target='_blank']")
print(f"Links with target=_blank: {len(links)}")
for link in links[:10]:
    href = link.get_attribute("href") or ""
    text = link.text_content()[:80].strip() if link.text_content() else ""
    print(f"  href={href}  text='{text}'")

# Check featured hotel links specifically
hotel_links = page.query_selector_all("a[href*='/stay/']")
print(f"\nHotel links (/stay/): {len(hotel_links)}")
for link in hotel_links[:10]:
    href = link.get_attribute("href") or ""
    target = link.get_attribute("target") or "(none)"
    text = link.text_content()[:80].strip() if link.text_content() else ""
    print(f"  href={href}  target={target}  text='{text}'")

# Try clicking the first hotel and see what happens
if hotel_links:
    print(f"\n=== Clicking first hotel link ===")
    pages_before = len(bs._context.pages)
    hotel_links[0].click()
    bs._page.wait_for_timeout(3000)
    pages_after = len(bs._context.pages)
    print(f"Pages before click: {pages_before}")
    print(f"Pages after click: {pages_after}")
    print(f"Current page URL: {bs._page.url}")
    for i, p in enumerate(bs._context.pages):
        print(f"  Tab [{i}]: {p.url}")

bs.cleanup()
print("\nDone.")
