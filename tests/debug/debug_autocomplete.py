"""Debug: inspect autocomplete dropdown on phptravels.net"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winston.skills.browser_skill import BrowserSkill

b = BrowserSkill()
b._ensure_browser()

r = b.execute(action='open_page', url='https://phptravels.net/')
time.sleep(3)

# Check what the search box is
js_check = """() => {
    const input = document.querySelector('input[placeholder="Search By City"]');
    if (!input) return 'No input found';
    return {
        tagName: input.tagName,
        type: input.type,
        value: input.value,
        className: input.className,
        id: input.id,
    };
}"""
print("=== Input element:", b._page.evaluate(js_check))

# Use keyboard-style typing to trigger autocomplete
el = b._page.query_selector('input[placeholder="Search By City"]')
el.click()
time.sleep(0.5)
b._page.keyboard.type("London", delay=100)
time.sleep(3)  # Wait for autocomplete

# Check for dropdown suggestions
js_suggestions = """() => {
    const all = document.querySelectorAll('*');
    const result = [];
    for (const el of all) {
        const text = el.innerText || '';
        const isVisible = el.offsetHeight > 0 && el.offsetWidth > 0;
        if (isVisible && text.toLowerCase().includes('london') && el.children.length === 0) {
            const rect = el.getBoundingClientRect();
            result.push({
                tag: el.tagName,
                cls: el.className.substring(0, 80),
                text: text.substring(0, 100),
                y: Math.round(rect.top),
            });
        }
    }
    return result;
}"""
london_els = b._page.evaluate(js_suggestions)
print("\n=== Elements with 'London' visible:", len(london_els))
for el in london_els:
    print(f"  {el['tag']}.{el['cls']} @ y={el['y']}: {el['text']}")

# Check for any list-like dropdown
js_lists = """() => {
    const candidates = document.querySelectorAll('ul, ol, [role="listbox"], .dropdown, .suggestions, .autocomplete-results, div[class*="dropdown"], div[class*="suggest"]');
    const result = [];
    for (const el of candidates) {
        if (el.offsetHeight > 0 && el.innerText && el.innerText.trim().length > 0) {
            result.push({
                tag: el.tagName,
                cls: el.className.substring(0, 100),
                text: el.innerText.substring(0, 200),
                height: el.offsetHeight,
            });
        }
    }
    return result;
}"""
lists = b._page.evaluate(js_lists)
print("\n=== Visible dropdown/list elements:", len(lists))
for el in lists[:10]:
    print(f"  {el['tag']}.{el['cls']} h={el['height']}: {el['text'][:100]}")

# Take snapshot to see full state
snap = b.snapshot()
# Find lines mentioning london or near the textbox
lines = snap.split('\n')
for i, l in enumerate(lines):
    if 'london' in l.lower() or 'e16' in l or 'Search By' in l:
        print(f"\nSnap line {i}: {l}")

# Try clicking Search Hotels directly without autocomplete selection
print("\n=== Trying direct search without autocomplete ===")
b.execute(action='click_ref', ref='e19')
time.sleep(3)
print("URL after search:", b._page.url)
snap2 = b.snapshot()
for line in snap2.split('\n')[:40]:
    print(line)

b.cleanup()
print("\nDone.")
