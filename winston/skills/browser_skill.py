"""
Browser Automation Skill (beta) — interact with web pages using Playwright.
Navigate, click, fill forms, extract text, take page screenshots.

Uses SYNC Playwright API to avoid event-loop lifecycle issues,
since WINSTON skills execute synchronously.
"""

import base64
import json as _json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.browser")


class BrowserSkill(BaseSkill):
    """Automate browser interactions — navigate, click, fill, extract, screenshot."""

    name = "browser"
    description = (
        "Interact with web pages using a headless browser. Navigate to URLs, click buttons, "
        "fill forms, and take screenshots of web pages (NOT the desktop). "
        "Use this when the user asks to go to a website, interact with a site, scrape content, "
        "or take a screenshot of a specific URL/web page. "
        "You can combine url + action in a single call, e.g. url='apple.com' action='screenshot_page'."
    )
    parameters = {
        "action": (
            "Action: 'open_page' (navigate and show text), 'click' (click element by CSS selector), "
            "'click_text' (click element by visible text — PREFERRED over CSS selectors), "
            "'select_option' (select dropdown option by label or value), "
            "'fill' (type into input), 'extract_text' (get page/element text), "
            "'get_page_structure' (list all clickable links, buttons, forms on the page), "
            "'screenshot_page' (navigate + capture page screenshot), 'run_script' (execute JS). "
            "Default: 'screenshot_page' if url is provided, 'open_page' otherwise."
        ),
        "url": "URL to navigate to. Works with ANY action — the page opens automatically first.",
        "selector": "(click/fill/extract_text/select_option) CSS selector for the target element",
        "text": "(click_text) Visible text of the element to click. Partial match supported.",
        "value": "(fill/select_option) Text to type or option label/value to select",
        "script": "(run_script) JavaScript code to execute on the page",
        "wait_for": "Optional CSS selector to wait for before performing the action",
    }

    # Roles that always get a ref (interactive elements)
    INTERACTIVE_ROLES = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "menuitem", "menuitemcheckbox", "menuitemradio",
        "tab", "switch", "searchbox", "slider", "spinbutton", "treeitem",
        "listbox",
    }
    # Roles that get a ref only when they have a name
    CONTENT_ROLES = {"heading", "img"}
    # Roles whose option children get collapsed into a summary
    COLLAPSIBLE_PARENTS = {"combobox", "listbox"}
    # Max options to show inline on combobox/listbox summary
    MAX_INLINE_OPTIONS = 5
    # Structural noise roles removed in compact mode (when they have no name)
    NOISE_ROLES = {
        "document", "rowgroup", "row", "listitem",
        "paragraph", "group", "generic", "none",
        "separator", "presentation",
    }

    # ── Snapshot limits ──
    SNAPSHOT_MAX_CHARS = 50000  # Truncate snapshots beyond this to prevent token overflow

    def __init__(self, config=None):
        super().__init__(config)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._refs: dict = {}        # {ref_id: {role, name, nth}}
        self._ref_counter: int = 0   # Sequential counter
        self._screenshots_dir = Path.home() / ".winston" / "browser_screenshots"
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        # Browser profile persistence (cookies + localStorage)
        self._profiles_dir = Path.home() / ".winston" / "browser_profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._active_profile: Optional[str] = None

    @property
    def current_url(self) -> str:
        """Return the current page URL."""
        if self._page:
            try:
                return self._page.url
            except Exception:
                return "Error retrieving URL"
        return "No page open"

    def _ensure_browser(self, profile: str = None):
        """Lazy-launch the browser on first use (sync API).
        If *profile* is given, restore saved cookies for that profile."""
        if self._page is not None:
            return

        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._page = self._context.new_page()

            # Track new tabs/popups automatically (OpenClaw pattern)
            self._context.on("page", self._on_new_page)

            logger.info("Browser launched (headless Chromium, sync API)")

            # Restore saved profile (cookies) if available
            if profile:
                self._load_profile(profile)
        except Exception as e:
            logger.error(f"Failed to launch browser: {e}")
            if "TargetClosedError" in str(e) or "executable" in str(e).lower():
                raise RuntimeError(
                    f"Chromium binary error: {e}. "
                    "This usually means the browser binary is missing or incompatible. "
                    "Try running: python3 -m playwright install chromium"
                ) from e
            raise e

    def _on_new_page(self, page):
        """Handle new tabs/popups opened by target=_blank or window.open().
        Automatically switches focus to the new page (OpenClaw pattern)."""
        logger.info(f"New tab detected: {page.url}")
        # Wait for the new page to load
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        # Switch to the new page
        self._page = page
        # Reset refs since we're on a new page
        self._refs = {}
        self._ref_counter = 0
        logger.info(f"Switched to new tab: {page.url}")

    # ── Browser Profile Persistence ──

    def _profile_path(self, name: str) -> Path:
        """Return the JSON file path for a named browser profile."""
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
        return self._profiles_dir / f"{safe_name}.json"

    def save_profile(self, name: str) -> bool:
        """Save current browser session (cookies) to a named profile."""
        if not self._context:
            logger.warning("No browser context to save.")
            return False
        try:
            cookies = self._context.cookies()
            profile_data = {
                "cookies": cookies,
                "saved_at": datetime.now().isoformat(),
                "url": self._page.url if self._page else None,
            }
            path = self._profile_path(name)
            path.write_text(_json.dumps(profile_data, indent=2, default=str))
            self._active_profile = name
            logger.info(f"Browser profile '{name}' saved ({len(cookies)} cookies)")
            return True
        except Exception as e:
            logger.error(f"Failed to save browser profile '{name}': {e}")
            return False

    def _load_profile(self, name: str) -> bool:
        """Load cookies from a saved profile into the current context."""
        path = self._profile_path(name)
        if not path.exists():
            logger.info(f"No saved profile '{name}' found — starting fresh.")
            return False
        try:
            profile_data = _json.loads(path.read_text())
            cookies = profile_data.get("cookies", [])
            if cookies:
                self._context.add_cookies(cookies)
                self._active_profile = name
                logger.info(f"Browser profile '{name}' restored ({len(cookies)} cookies)")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to load browser profile '{name}': {e}")
            return False

    def list_profiles(self) -> list:
        """Return names of all saved browser profiles."""
        profiles = []
        for p in self._profiles_dir.glob("*.json"):
            try:
                data = _json.loads(p.read_text())
                profiles.append({
                    "name": p.stem,
                    "saved_at": data.get("saved_at"),
                    "url": data.get("url"),
                    "cookies": len(data.get("cookies", [])),
                })
            except Exception:
                profiles.append({"name": p.stem})
        return profiles

    def _auto_navigate(self, url: str, wait_for: str = None):
        """
        Auto-navigate to a URL if provided.
        Smart Navigation: Skips if already on the target page.
        """
        if not url:
            return

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        self._ensure_browser()

        # Smart Navigation: Only navigate if the URL is different
        current_url = self._page.url.rstrip("/")
        normalized_url = url.rstrip("/")

        if normalized_url != current_url and normalized_url not in current_url:
            logger.info(f"Navigating to: {url}")
            try:
                # Use load + networkidle for heavy sites like Apple
                response = self._page.goto(url, wait_until="load", timeout=30000)

                # Check for 404 or other HTTP errors
                if response and not response.ok:
                    return SkillResult(
                        success=False,
                        message=f"Navigation failed with status {response.status}: {response.status_text} and current title is '{self._page.title()}'"
                    )

                # Shorter wait for network idle to catch remaining JS/XHR
                try:
                    self._page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    logger.debug("Network did not go idle within 10s, proceeding anyway.")

                # Check page title for common error patterns
                title = self._page.title().lower()
                error_keywords = ["404", "not found", "page not found", "access denied", "site not found"]
                if any(kw in title for kw in error_keywords):
                    return SkillResult(
                        success=False,
                        message=f"Navigation landed on an error page. Title: '{self._page.title()}'. URL: {self._page.url}"
                    )

                # Also check body text for soft 404s (page returns 200 but shows error content)
                try:
                    body_text = self._page.evaluate("""
                        () => {
                            const body = document.body;
                            if (!body) return '';
                            return body.innerText.substring(0, 500).toLowerCase();
                        }
                    """)
                    soft_404_patterns = [
                        "page not found", "404 error", "this page doesn't exist",
                        "the page you requested", "seite nicht gefunden",
                        "we can't find the page", "page you are looking for"
                    ]
                    if any(p in body_text for p in soft_404_patterns):
                        return SkillResult(
                            success=False,
                            message=f"Navigation landed on a soft error page (content indicates 404). URL: {self._page.url}. Title: '{self._page.title()}'"
                        )
                except Exception:
                    pass

                # Settle time
                self._page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Initial navigation with 'load' failed, retrying with 'domcontentloaded': {e}")
                self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        else:
            logger.info(f"Already at {url}, skipping redundant navigation.")

        if wait_for:
            try:
                # First wait for it to be in the DOM
                self._page.wait_for_selector(wait_for, state="attached", timeout=15000)
                # Then try to wait for it to be visible (shorter timeout)
                self._page.wait_for_selector(wait_for, state="visible", timeout=15000)
            except Exception:
                logger.warning(f"Wait for selector '{wait_for}' timed out/failed. Proceeding anyway.")
                self._page.wait_for_load_state("load", timeout=10000)

        # Proactively check for common overlays (cookie banners)
        self._dismiss_overlays()

    def _dismiss_overlays(self):
        """
        Attempt to dismiss common overlays: cookie consent, country selectors,
        newsletter popups, chat widgets, etc.
        Uses Playwright's frame-aware locator API to handle cross-origin iframes.
        """
        if not self._page:
            return

        # First: try closing any visible modal/overlay via close/dismiss buttons
        close_keywords = [
            "close", "schließen", "dismiss", "no thanks", "nein danke",
            "not now", "×", "✕", "✖", "weiter", "continue",
            "skip", "überspringen",
        ]
        for kw in close_keywords:
            try:
                # Look for close buttons on overlays/modals
                btn = self._page.get_by_role("button", name=re.compile(kw, re.IGNORECASE))
                if btn.count() > 0 and btn.first.is_visible():
                    # Only click if it looks like a dismiss button (inside a modal/overlay)
                    parent_html = btn.first.evaluate(
                        "el => { let p = el; for(let i=0;i<5;i++){p=p.parentElement;if(!p)break;"
                        "if(p.getAttribute('role')==='dialog'||p.className.match(/modal|overlay|popup|banner|sheet|drawer/i)"
                        "||p.id.match(/modal|overlay|popup|banner/i))return true;} return false; }"
                    )
                    if parent_html:
                        btn.first.click(timeout=3000)
                        logger.info(f"Dismissed overlay via close button: '{kw}'")
                        self._page.wait_for_timeout(1000)
                        return
            except Exception:
                pass

        # Second: try region/country selector "continue" or "close" buttons
        # (Apple, Amazon, etc. show "Choose your country" overlays)
        try:
            # Look for complementary/dialog regions with region-related text
            close_btn = self._page.locator(
                '[role="dialog"] button, '
                '[role="complementary"] button, '
                '[class*="modal"] button, '
                '[class*="overlay"] button, '
                '[class*="region"] button, '
                '[class*="country"] button, '
                '[class*="locale"] button'
            )
            if close_btn.count() > 0:
                # Click the last button (usually "close" or "continue")
                for i in range(close_btn.count()):
                    b = close_btn.nth(i)
                    try:
                        if b.is_visible():
                            text = (b.text_content() or "").strip().lower()
                            aria = (b.get_attribute("aria-label") or "").lower()
                            if any(w in text or w in aria for w in ["close", "schließen", "dismiss", "weiter", "continue", "×"]):
                                b.click(timeout=3000)
                                logger.info(f"Dismissed region/country overlay via: '{text or aria}'")
                                self._page.wait_for_timeout(1000)
                                return
                    except Exception:
                        continue
        except Exception:
            pass

        # Third: cookie consent buttons
        keywords = [
            "alle akzeptieren", "accept all", "i agree", "agree",
            "allow all", "alle erlauben", "akzeptieren", "zustimmen",
            "accept", "ok", "understand", "got it", "einverstanden",
            "alle cookies akzeptieren", "accept all cookies",
            "alle cookies erlauben", "allow all cookies",
        ]

        # Strategy 1: Playwright's main page locator (handles shadow DOM too)
        for kw in keywords:
            try:
                # Try button role first (most reliable for consent buttons)
                btn = self._page.get_by_role("button", name=re.compile(kw, re.IGNORECASE))
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=3000)
                    logger.info(f"Dismissed overlay via button role match: '{kw}'")
                    self._page.wait_for_timeout(2000)
                    return
            except Exception:
                pass

            try:
                # Fallback: text-based locator (catches <a>, <div>, <span> etc.)
                el = self._page.get_by_text(re.compile(f"^{re.escape(kw)}$", re.IGNORECASE))
                if el.count() > 0 and el.first.is_visible():
                    el.first.click(timeout=3000)
                    logger.info(f"Dismissed overlay via text match: '{kw}'")
                    self._page.wait_for_timeout(2000)
                    return
            except Exception:
                pass

        # Strategy 2: Check all iframes using Playwright's frame_locator (cross-origin safe)
        try:
            iframes = self._page.query_selector_all("iframe")
            for i in range(len(iframes)):
                frame_loc = self._page.frame_locator(f"iframe >> nth={i}")
                for kw in keywords[:6]:  # Check top keywords only for speed
                    try:
                        btn = frame_loc.get_by_role("button", name=re.compile(kw, re.IGNORECASE))
                        if btn.count() > 0:
                            btn.first.click(timeout=3000)
                            logger.info(f"Dismissed overlay in iframe via: '{kw}'")
                            self._page.wait_for_timeout(2000)
                            return
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"Iframe overlay check failed: {e}")

        # Strategy 3: Last resort — JS-based search on main document
        dismiss_js = """
        () => {
            const keywords = [
                'alle akzeptieren', 'accept all', 'akzeptieren', 'accept',
                'zustimmen', 'i agree', 'agree', 'allow all', 'alle erlauben',
                'got it', 'einverstanden', 'ok', 'understand'
            ];
            const elements = Array.from(document.querySelectorAll(
                'button, a, div[role="button"], span[role="button"], [class*="cookie"] button, [class*="consent"] button, [id*="cookie"] button, [id*="consent"] button'
            ));
            for (const el of elements) {
                const text = (el.innerText || el.textContent || '').toLowerCase().trim();
                if (keywords.some(k => text === k || text.includes(k))) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        }
        """
        try:
            success = self._page.evaluate(dismiss_js)
            if success:
                logger.info("Dismissed overlay via JS fallback on main document.")
                self._page.wait_for_timeout(2000)
        except Exception as e:
            logger.debug(f"JS overlay dismissal failed: {e}")

    def extract_page_structure(self) -> str:
        """
        Extract the interactive structure of the current page: links, buttons,
        form fields, dropdowns — with visible text and CSS selectors.
        This gives the LLM the 'eyes' it needs to decide what to click.
        Returns a concise text summary of the page structure.
        """
        if not self._page:
            return "No page is open."

        structure_js = """
        () => {
            const results = { links: [], buttons: [], inputs: [], selects: [] };
            const seen = new Set();

            function getSelector(el) {
                // Build a reasonable CSS selector
                if (el.id) return '#' + CSS.escape(el.id);

                // Try aria-label
                const aria = el.getAttribute('aria-label');
                if (aria) return `[aria-label="${aria.replace(/"/g, '\\\\"')}"]`;

                // Try data-testid
                const testid = el.getAttribute('data-testid');
                if (testid) return `[data-testid="${testid}"]`;

                // Try name attribute for inputs
                const name = el.getAttribute('name');
                if (name) return `[name="${name}"]`;

                // Fallback: tag + nth-of-type
                const tag = el.tagName.toLowerCase();
                const parent = el.parentElement;
                if (parent) {
                    const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
                    const idx = siblings.indexOf(el);
                    if (siblings.length > 1) {
                        return tag + ':nth-of-type(' + (idx + 1) + ')';
                    }
                }
                return tag;
            }

            function isVisible(el) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
            }

            // Links
            document.querySelectorAll('a[href]').forEach(a => {
                if (!isVisible(a)) return;
                const text = (a.innerText || a.textContent || '').trim().substring(0, 80);
                if (!text || seen.has(text)) return;
                seen.add(text);
                const href = a.getAttribute('href') || '';
                results.links.push({ text, href: href.substring(0, 120), selector: getSelector(a) });
            });

            // Buttons
            document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]').forEach(btn => {
                if (!isVisible(btn)) return;
                const text = (btn.innerText || btn.textContent || btn.value || btn.getAttribute('aria-label') || '').trim().substring(0, 80);
                if (!text || seen.has('btn:' + text)) return;
                seen.add('btn:' + text);
                results.buttons.push({ text, selector: getSelector(btn) });
            });

            // Inputs
            document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea').forEach(inp => {
                if (!isVisible(inp)) return;
                const label = inp.getAttribute('placeholder') || inp.getAttribute('aria-label') || inp.getAttribute('name') || inp.type;
                results.inputs.push({ label: label.substring(0, 60), type: inp.type || 'text', selector: getSelector(inp) });
            });

            // Select dropdowns
            document.querySelectorAll('select').forEach(sel => {
                if (!isVisible(sel)) return;
                const label = sel.getAttribute('aria-label') || sel.getAttribute('name') || 'dropdown';
                const options = Array.from(sel.options).map(o => o.text.trim()).filter(t => t).slice(0, 10);
                results.selects.push({ label: label.substring(0, 60), options, selector: getSelector(sel) });
            });

            // Trim to avoid context overflow
            results.links = results.links.slice(0, 20);
            results.buttons = results.buttons.slice(0, 15);
            results.inputs = results.inputs.slice(0, 10);
            results.selects = results.selects.slice(0, 10);

            return results;
        }
        """

        try:
            data = self._page.evaluate(structure_js)
        except Exception as e:
            logger.warning(f"Page structure extraction failed: {e}")
            return "Could not extract page structure."

        lines = []
        title = self._page.title()
        url = self._page.url
        lines.append(f"Page: {title}")
        lines.append(f"URL: {url}")

        if data.get("links"):
            lines.append(f"\nLinks ({len(data['links'])}):")
            for lnk in data["links"]:
                lines.append(f"  - \"{lnk['text']}\" → {lnk['href']}")

        if data.get("buttons"):
            lines.append(f"\nButtons ({len(data['buttons'])}):")
            for btn in data["buttons"]:
                lines.append(f"  - \"{btn['text']}\" [selector: {btn['selector']}]")

        if data.get("inputs"):
            lines.append(f"\nInput fields ({len(data['inputs'])}):")
            for inp in data["inputs"]:
                lines.append(f"  - {inp['label']} (type: {inp['type']}) [selector: {inp['selector']}]")

        if data.get("selects"):
            lines.append(f"\nDropdowns ({len(data['selects'])}):")
            for sel in data["selects"]:
                opts = ", ".join(sel["options"][:5])
                if len(sel["options"]) > 5:
                    opts += f"... (+{len(sel['options']) - 5} more)"
                lines.append(f"  - {sel['label']} [selector: {sel['selector']}] options: [{opts}]")

        if not data.get("links") and not data.get("buttons"):
            # Page might use custom elements — extract visible text as fallback
            try:
                text = self._page.evaluate("""
                    () => {
                        const clone = document.body.cloneNode(true);
                        clone.querySelectorAll('script, style, noscript').forEach(el => el.remove());
                        return clone.innerText.trim().substring(0, 2000);
                    }
                """)
                lines.append(f"\nPage text (no standard links/buttons found):\n{text}")
            except Exception:
                pass

        return "\n".join(lines)

    # ── Snapshot + Ref System ──

    def snapshot(self, max_chars: int = None) -> str:
        """Capture page accessibility tree with [ref=eN] markers on interactive elements.
        Returns enhanced ARIA text that the LLM can read and act on using refs.
        Truncates to SNAPSHOT_MAX_CHARS (default 50k) to prevent token overflow."""
        if not self._page:
            return "No page is open."

        if max_chars is None:
            max_chars = self.SNAPSHOT_MAX_CHARS

        try:
            raw = self._page.locator(":root").aria_snapshot()
        except Exception as e:
            logger.warning(f"aria_snapshot() failed: {e}")
            return self.extract_page_structure()

        enhanced, refs = self._parse_aria_snapshot(raw)
        self._refs = refs

        # Prepend page info
        try:
            title = self._page.title()
            url = self._page.url
            header = f"Page: {title}\nURL: {url}\n\n"
        except Exception:
            header = ""

        result = header + enhanced
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... (truncated)"
        return result

    def _parse_aria_snapshot(self, raw: str) -> tuple:
        """Parse ARIA snapshot text and inject [ref=eN] markers.

        Optimizations (OpenClaw-inspired):
        1. Options collapse: option children of combobox/listbox are summarized
           inline (first 5 + count) instead of getting individual refs.
        2. Compact mode: structural noise roles (document, rowgroup, row, etc.)
           are removed when they carry no name and no ref.

        Returns (enhanced_text, refs_dict)."""
        self._ref_counter = 0
        refs: dict = {}
        lines = raw.split("\n")

        # Line pattern: optional leading whitespace + '- ' + role + optional ' "name"' + rest
        line_re = re.compile(r'^(\s*-\s*)(\w+)(?:\s+"([^"]*)")?(.*)$')

        # ── Pass 1: Parse all lines, detect indent levels, collect options ──
        parsed_lines = []
        for line in lines:
            m = line_re.match(line)
            if m:
                indent = len(m.group(1))  # length of "  - " prefix
                role = m.group(2)
                name = m.group(3)  # May be None
                parsed_lines.append((line, m, role, name, indent))
            else:
                parsed_lines.append((line, None, None, None, 0))

        # ── Pass 1b: Identify collapsible parents and collect their options ──
        # Map from line index of parent → list of option names
        parent_options: dict = {}
        # Track which lines are options to be suppressed
        option_lines: set = set()

        for i, (line, m, role, name, indent) in enumerate(parsed_lines):
            if role in self.COLLAPSIBLE_PARENTS:
                # Find child options: lines immediately following with deeper indent
                options = []
                for j in range(i + 1, len(parsed_lines)):
                    _, child_m, child_role, child_name, child_indent = parsed_lines[j]
                    if child_indent <= indent:
                        break  # Back to same or higher level
                    if child_role == "option" and child_name is not None:
                        options.append(child_name)
                        option_lines.add(j)
                    elif child_role == "option" and child_name is None:
                        option_lines.add(j)
                if options:
                    parent_options[i] = options

        # ── Pass 2: Count occurrences for duplicate detection (excluding options) ──
        role_name_counts: dict = {}
        for i, (line, m, role, name, indent) in enumerate(parsed_lines):
            if i in option_lines:
                continue
            if m and role != "option":
                key = (role, name)
                role_name_counts[key] = role_name_counts.get(key, 0) + 1

        # ── Pass 3: Assign refs and build enhanced lines ──
        enhanced_lines = []
        role_name_seen: dict = {}

        for i, (line, m, role, name, indent) in enumerate(parsed_lines):
            # Skip option lines (they're collapsed into parent summary)
            if i in option_lines:
                continue

            if m is None:
                enhanced_lines.append(line)
                continue

            # Skip standalone options not under a collapsible parent
            if role == "option":
                continue

            needs_ref = (
                role in self.INTERACTIVE_ROLES
                or (role in self.CONTENT_ROLES and name is not None)
            )

            if not needs_ref:
                enhanced_lines.append(line)
                continue

            key = (role, name)
            is_duplicate = role_name_counts.get(key, 1) > 1

            idx = role_name_seen.get(key, 0)
            role_name_seen[key] = idx + 1

            ref_id = f"e{self._ref_counter}"
            self._ref_counter += 1

            ref_entry = {"role": role, "name": name}
            if is_duplicate:
                ref_entry["nth"] = idx
            refs[ref_id] = ref_entry

            # Build enhanced line with ref
            prefix = m.group(1)  # indent + '-'
            rest = m.group(4)    # everything after the name

            if name is not None:
                enhanced_line = f'{prefix}{role} "{name}" [ref={ref_id}]{rest}'
            else:
                enhanced_line = f'{prefix}{role} [ref={ref_id}]{rest}'

            # Append options summary for collapsible parents
            if i in parent_options:
                opts = parent_options[i]
                shown = opts[:self.MAX_INLINE_OPTIONS]
                summary = ", ".join(shown)
                remaining = len(opts) - len(shown)
                if remaining > 0:
                    summary += f" (+{remaining} more)"
                enhanced_line += f" [options: {summary}]"

            enhanced_lines.append(enhanced_line)

        # ── Pass 4: Compact mode — remove structural noise ──
        compact_lines = []
        for line in enhanced_lines:
            # Always keep lines with refs
            if "[ref=" in line:
                compact_lines.append(line)
                continue

            # Always keep lines with URL info
            if "/url:" in line:
                compact_lines.append(line)
                continue

            # Check if this is a noise role line
            m = line_re.match(line)
            if m:
                role = m.group(2)
                name = m.group(3)
                rest = m.group(4)

                if role in self.NOISE_ROLES and name is None:
                    # Check if rest has meaningful content (": text content")
                    if rest and rest.strip().startswith(":") and len(rest.strip()) > 1:
                        compact_lines.append(line)
                    else:
                        continue  # Drop pure noise
                else:
                    compact_lines.append(line)
            else:
                # Non-role lines (plain text, etc.) — keep
                compact_lines.append(line)

        return "\n".join(compact_lines), refs

    def _resolve_ref(self, ref: str):
        """Resolve a ref ID to a Playwright Locator via get_by_role()."""
        if ref not in self._refs:
            raise ValueError(f"Unknown ref '{ref}'. Take a new snapshot to get fresh refs.")

        info = self._refs[ref]
        role = info["role"]
        name = info.get("name")
        nth = info.get("nth")

        kwargs = {}
        if name is not None:
            kwargs["name"] = name
            kwargs["exact"] = True

        locator = self._page.get_by_role(role, **kwargs)
        if nth is not None:
            locator = locator.nth(nth)
        return locator

    def _to_ai_error(self, error, ref: str = None) -> str:
        """Translate Playwright errors to actionable messages for the LLM."""
        msg = str(error)
        if "strict mode violation" in msg.lower():
            return "Multiple elements match. Take a new snapshot to get updated refs."
        if "timeout" in msg.lower() or "not visible" in msg.lower():
            return "Element not found or hidden. Try scrolling or take a new snapshot."
        if "intercepts pointer" in msg.lower():
            return "Element covered by overlay. Dismiss popup first."
        if "detached" in msg.lower():
            return "Element gone. Page changed — take a new snapshot."
        ref_hint = f" (ref={ref})" if ref else ""
        return f"Action failed{ref_hint}: {msg}"

    def execute(self, **kwargs) -> SkillResult:
        """Execute a browser action."""
        url = kwargs.get("url", "")
        # Smart default: if URL provided but no explicit action, screenshot the page
        action = kwargs.get("action", "screenshot_page" if url else "open_page")

        actions = {
            "open_page": self._open_page,
            "click": self._click,
            "click_text": self._click_text,
            "select_option": self._select_option,
            "fill": self._fill,
            "extract_text": self._extract_text,
            "get_page_structure": self._get_page_structure,
            "screenshot_page": self._screenshot_page,
            "screenshot": self._screenshot_page,
            "run_script": self._run_script,
            # Snapshot + ref-based actions
            "snapshot": self._snapshot_action,
            "click_ref": self._click_ref,
            "type_ref": self._type_ref,
            "select_ref": self._select_ref,
            "hover_ref": self._hover_ref,
            "scroll": self._scroll,
            # New actions (OpenClaw-inspired)
            "keyboard_type": self._keyboard_type,
            "press_key": self._press_key,
            "wait_for": self._wait_for,
            "switch_tab": self._switch_tab,
        }

        handler = actions.get(action)
        if not handler:
            return SkillResult(
                success=False,
                message=f"Unknown browser action: {action}. "
                        f"Available: {', '.join(actions.keys())}",
            )

        try:
            return handler(**kwargs)
        except ImportError:
            return SkillResult(
                success=False,
                message=(
                    "Playwright is not installed. Install with:\n"
                    "  pip install playwright\n"
                    "  python -m playwright install chromium"
                ),
            )
        except Exception as e:
            logger.error(f"Browser action '{action}' failed: {e}")
            return SkillResult(success=False, message=f"Browser error: {e}")

    def _open_page(self, **kwargs) -> SkillResult:
        """Navigate to a URL and return the page title and summary."""
        url = kwargs.get("url", "")
        wait_for = kwargs.get("wait_for")

        if not url:
            return SkillResult(success=False, message="No URL provided.")

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        self._ensure_browser()

        try:
            # Use load instead of domcontentloaded for direct open_page calls
            self._page.goto(url, wait_until="load", timeout=30000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            # Settle time
            self._page.wait_for_timeout(1000)

            # Dismiss overlays before extracting content
            self._dismiss_overlays()

            if wait_for:
                self._page.wait_for_selector(wait_for, timeout=10000)

            title = self._page.title()

            # Extract visible text (first 2000 chars)
            text = self._page.evaluate("""
                () => {
                    const body = document.body;
                    if (!body) return '';
                    const clone = body.cloneNode(true);
                    clone.querySelectorAll('script, style, noscript').forEach(el => el.remove());
                    return clone.innerText.trim().substring(0, 2000);
                }
            """)

            return SkillResult(
                success=True,
                message=f"**{title}**\n\nPage content:\n{text[:1500]}",
                data={"title": title, "url": url, "text_length": len(text)},
                speak=False,
            )
        except Exception as e:
            return SkillResult(success=False, message=f"Failed to load {url}: {e}")

    def _click(self, **kwargs) -> SkillResult:
        """Click an element by CSS selector."""
        selector = kwargs.get("selector", "")
        if not selector:
            return SkillResult(success=False, message="No CSS selector provided.")

        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        try:
            # Wait for element to be visible and stable
            self._page.wait_for_selector(selector, state="visible", timeout=8000)
            self._page.click(selector, timeout=8000)

            # Allow page to settle
            wait_after = kwargs.get("wait_after", 500) # Default 500ms for clicks
            self._page.wait_for_timeout(wait_after)

            return SkillResult(success=True, message=f"Clicked element: `{selector}`")
        except Exception as e:
            msg = f"Click failed on '{selector}' at {self.current_url}: {e}"
            logger.error(msg)
            return SkillResult(success=False, message=msg)

    def _click_text(self, **kwargs) -> SkillResult:
        """Click an element by its visible text using Playwright's text-based locators.
        Much more reliable than CSS selectors for dynamic sites."""
        text = kwargs.get("text", "")
        if not text:
            return SkillResult(success=False, message="No text provided for click_text.")

        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        try:
            # Strategy 1: Try get_by_role("link") with exact name first
            try:
                link = self._page.get_by_role("link", name=re.compile(re.escape(text), re.IGNORECASE))
                if link.count() > 0 and link.first.is_visible():
                    link.first.click(timeout=5000)
                    wait_after = kwargs.get("wait_after", 1000)
                    self._page.wait_for_timeout(wait_after)
                    return SkillResult(success=True, message=f"Clicked link with text: \"{text}\"")
            except Exception:
                pass

            # Strategy 2: Try get_by_role("button")
            try:
                btn = self._page.get_by_role("button", name=re.compile(re.escape(text), re.IGNORECASE))
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=5000)
                    wait_after = kwargs.get("wait_after", 1000)
                    self._page.wait_for_timeout(wait_after)
                    return SkillResult(success=True, message=f"Clicked button with text: \"{text}\"")
            except Exception:
                pass

            # Strategy 3: Generic text locator (any visible element)
            el = self._page.get_by_text(re.compile(re.escape(text), re.IGNORECASE))
            if el.count() > 0:
                # Find the first visible, clickable one
                for i in range(min(el.count(), 5)):
                    try:
                        candidate = el.nth(i)
                        if candidate.is_visible():
                            candidate.click(timeout=5000)
                            wait_after = kwargs.get("wait_after", 1000)
                            self._page.wait_for_timeout(wait_after)
                            return SkillResult(success=True, message=f"Clicked element with text: \"{text}\"")
                    except Exception:
                        continue

            return SkillResult(
                success=False,
                message=f"No visible clickable element found with text: \"{text}\" on {self.current_url}"
            )

        except Exception as e:
            msg = f"click_text failed for '{text}' at {self.current_url}: {e}"
            logger.error(msg)
            return SkillResult(success=False, message=msg)

    def _select_option(self, **kwargs) -> SkillResult:
        """Select an option from a dropdown by label or value."""
        selector = kwargs.get("selector", "")
        value = kwargs.get("value", "")

        if not selector:
            return SkillResult(success=False, message="No CSS selector provided for the dropdown.")
        if not value:
            return SkillResult(success=False, message="No value/label provided to select.")

        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        try:
            self._page.wait_for_selector(selector, state="visible", timeout=15000)

            # Try selecting by label first (visible text), then by value
            try:
                self._page.select_option(selector, label=value, timeout=10000)
            except Exception:
                self._page.select_option(selector, value=value, timeout=10000)

            wait_after = kwargs.get("wait_after", 500)
            self._page.wait_for_timeout(wait_after)

            return SkillResult(
                success=True,
                message=f"Selected \"{value}\" in dropdown `{selector}`"
            )
        except Exception as e:
            msg = f"select_option failed on '{selector}' with value '{value}' at {self.current_url}: {e}"
            logger.error(msg)
            return SkillResult(success=False, message=msg)

    def _fill(self, **kwargs) -> SkillResult:
        """Fill an input field."""
        selector = kwargs.get("selector", "")
        value = kwargs.get("value", "")

        if not selector:
            return SkillResult(success=False, message="No CSS selector provided.")
        if not value:
            return SkillResult(success=False, message="No value provided to fill.")

        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        try:
            self._page.wait_for_selector(selector, state="visible", timeout=10000)
            self._page.fill(selector, value, timeout=10000)

            # Allow page to settle (animations, validation)
            wait_after = kwargs.get("wait_after", 500) # Default 500ms for fills
            self._page.wait_for_timeout(wait_after)

            return SkillResult(
                success=True,
                message=f"Filled `{selector}` with: {value[:50]}",
            )
        except Exception as e:
            msg = f"Fill failed on '{selector}' at {self.current_url}: {e}"
            logger.error(msg)
            return SkillResult(success=False, message=msg)

    def _extract_text(self, **kwargs) -> SkillResult:
        """Extract text content from the page or a specific element."""
        selector = kwargs.get("selector")

        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        try:
            if selector:
                element = self._page.query_selector(selector)
                if not element:
                    return SkillResult(success=False, message=f"Element '{selector}' not found.")
                text = element.inner_text()
            else:
                text = self._page.evaluate("""
                    () => {
                        const clone = document.body.cloneNode(true);
                        clone.querySelectorAll('script, style, noscript').forEach(el => el.remove());
                        return clone.innerText.trim();
                    }
                """)

            return SkillResult(
                success=True,
                message=f"Extracted text ({len(text)} chars):\n\n{text[:3000]}",
                data={"text": text, "selector": selector},
                speak=False,
            )
        except Exception as e:
            return SkillResult(success=False, message=f"Text extraction failed: {e}")

    def _get_page_structure(self, **kwargs) -> SkillResult:
        """Return the interactive structure of the current page."""
        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        structure = self.extract_page_structure()
        return SkillResult(
            success=True,
            message=structure,
            data={"structure": structure},
            speak=False,
        )

    def _screenshot_page(self, **kwargs) -> SkillResult:
        """Navigate to URL (if provided) and take a screenshot of the page."""
        url = kwargs.get("url", "")
        wait_for = kwargs.get("wait_for")

        # Auto-navigate if URL is provided
        self._auto_navigate(url, wait_for)

        if not self._page:
            return SkillResult(success=False, message="No page is open. Provide a 'url' parameter.")

        try:
            filename = f"page_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = self._screenshots_dir / filename

            self._page.screenshot(path=str(filepath), full_page=False)

            # Read as base64 for potential vision analysis
            image_b64 = base64.b64encode(filepath.read_bytes()).decode("utf-8")

            title = self._page.title()
            current_url = self._page.url
            return SkillResult(
                success=True,
                message=f"Screenshot of **{title}** saved to `{filepath}`\nURL: {current_url}",
                data={"path": str(filepath), "image_b64": image_b64, "url": current_url},
            )
        except Exception as e:
            return SkillResult(success=False, message=f"Screenshot failed: {e}")

    def _run_script(self, **kwargs) -> SkillResult:
        """Execute JavaScript on the current page."""
        script = kwargs.get("script", "")
        if not script:
            return SkillResult(success=False, message="No JavaScript provided.")

        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        try:
            result = self._page.evaluate(script)

            # Allow page to settle if script performed UI changes
            wait_after = kwargs.get("wait_after", 0)
            if wait_after > 0:
                self._page.wait_for_timeout(wait_after)

            result_str = str(result)[:2000] if result is not None else "undefined"
            return SkillResult(
                success=True,
                message=f"Script result:\n{result_str}",
                data={"result": result},
                speak=False,
            )
        except Exception as e:
            return SkillResult(success=False, message=f"Script execution failed: {e}")

    # ── Snapshot + Ref Action Handlers ──

    def _snapshot_action(self, **kwargs) -> SkillResult:
        """Capture page accessibility tree with refs."""
        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))
        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        max_chars = kwargs.get("max_chars", 20000)
        text = self.snapshot(max_chars=max_chars)
        return SkillResult(success=True, message=text, data={"snapshot": text}, speak=False)

    def _click_ref(self, **kwargs) -> SkillResult:
        """Click an element by its ref ID. Handles new tabs automatically.
        Reports whether the click actually caused navigation or opened a new tab."""
        ref = kwargs.get("ref", "")
        if not ref:
            return SkillResult(success=False, message="No ref provided. Use snapshot first to get refs.")

        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            locator = self._resolve_ref(ref)

            # Capture state before click
            url_before = self._page.url
            pages_before = len(self._context.pages) if self._context else 0

            try:
                locator.click(timeout=8000)
            except Exception as click_err:
                if "intercepts pointer" in str(click_err).lower():
                    # Overlay is blocking — try to dismiss it and retry
                    logger.info("Click intercepted by overlay — dismissing and retrying")
                    self._dismiss_overlays()
                    locator.click(timeout=8000)
                else:
                    raise

            # Wait for potential navigation or new tab
            self._page.wait_for_timeout(1500)

            # Check what changed
            pages_after = len(self._context.pages) if self._context else 0
            url_after = self._page.url

            if pages_after > pages_before:
                # _on_new_page already switched self._page
                msg = f"Clicked [ref={ref}] — NEW TAB opened: {self._page.url}"
            elif url_after != url_before:
                msg = f"Clicked [ref={ref}] — navigated to: {url_after}"
            else:
                msg = (
                    f"Clicked [ref={ref}] — page did NOT change (still on {url_after}). "
                    "The element may be a JS toggle, accordion, or non-navigating element. "
                    "Try a different approach: scroll to find other elements, use run_script "
                    "to inspect the element, or navigate directly via open_page."
                )

            return SkillResult(success=True, message=msg)
        except ValueError as e:
            return SkillResult(success=False, message=str(e))
        except Exception as e:
            return SkillResult(success=False, message=self._to_ai_error(e, ref))

    def _type_ref(self, **kwargs) -> SkillResult:
        """Fill an input by its ref ID, optionally press Enter.
        Uses fill() by default. For autocomplete inputs that need character-by-character
        typing to trigger JS events, use keyboard_type action instead."""
        ref = kwargs.get("ref", "")
        value = kwargs.get("value", "")
        submit = kwargs.get("submit", False)
        slow = kwargs.get("slow", False)  # Character-by-character for autocomplete

        if not ref:
            return SkillResult(success=False, message="No ref provided.")
        if not value:
            return SkillResult(success=False, message="No value provided to type.")
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            locator = self._resolve_ref(ref)

            if slow:
                # Click to focus, clear, then type character by character
                locator.click(timeout=5000)
                locator.fill("", timeout=3000)  # Clear existing value
                locator.press_sequentially(value, delay=80, timeout=8000)
                # Wait for autocomplete/dropdown to appear
                self._page.wait_for_timeout(1500)
            else:
                locator.fill(value, timeout=8000)

            if submit:
                locator.press("Enter", timeout=5000)
                self._page.wait_for_timeout(1000)
            else:
                self._page.wait_for_timeout(500)
            return SkillResult(
                success=True,
                message=f"Typed '{value[:50]}' into [ref={ref}]" + (" (slow)" if slow else "") + (" and submitted" if submit else "")
            )
        except ValueError as e:
            return SkillResult(success=False, message=str(e))
        except Exception as e:
            return SkillResult(success=False, message=self._to_ai_error(e, ref))

    def _select_ref(self, **kwargs) -> SkillResult:
        """Select a dropdown option by ref ID."""
        ref = kwargs.get("ref", "")
        value = kwargs.get("value", "")

        if not ref:
            return SkillResult(success=False, message="No ref provided.")
        if not value:
            return SkillResult(success=False, message="No value provided to select.")
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            locator = self._resolve_ref(ref)
            locator.select_option(label=value, timeout=8000)
            self._page.wait_for_timeout(500)
            return SkillResult(success=True, message=f"Selected '{value}' in [ref={ref}]")
        except ValueError as e:
            return SkillResult(success=False, message=str(e))
        except Exception as e:
            # Fallback: try by value instead of label
            try:
                locator = self._resolve_ref(ref)
                locator.select_option(value=value, timeout=8000)
                self._page.wait_for_timeout(500)
                return SkillResult(success=True, message=f"Selected '{value}' in [ref={ref}]")
            except Exception:
                return SkillResult(success=False, message=self._to_ai_error(e, ref))

    def _hover_ref(self, **kwargs) -> SkillResult:
        """Hover over an element by ref ID."""
        ref = kwargs.get("ref", "")
        if not ref:
            return SkillResult(success=False, message="No ref provided.")
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            locator = self._resolve_ref(ref)
            locator.hover(timeout=8000)
            self._page.wait_for_timeout(300)
            return SkillResult(success=True, message=f"Hovered over [ref={ref}]")
        except ValueError as e:
            return SkillResult(success=False, message=str(e))
        except Exception as e:
            return SkillResult(success=False, message=self._to_ai_error(e, ref))

    def _scroll(self, **kwargs) -> SkillResult:
        """Scroll the page up or down."""
        direction = kwargs.get("direction", "down")
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            pixels = 600 if direction == "down" else -600
            self._page.evaluate(f"window.scrollBy(0, {pixels})")
            self._page.wait_for_timeout(500)
            return SkillResult(success=True, message=f"Scrolled {direction}")
        except Exception as e:
            return SkillResult(success=False, message=f"Scroll failed: {e}")

    # ── New Actions (OpenClaw-inspired) ──

    def _keyboard_type(self, **kwargs) -> SkillResult:
        """Type text character-by-character using keyboard events.
        Unlike fill(), this triggers input/change/keydown events that
        autocomplete UIs and JS frameworks rely on."""
        text = kwargs.get("text", kwargs.get("value", ""))
        ref = kwargs.get("ref", "")
        delay = int(kwargs.get("delay", 80))  # ms between keystrokes

        if not text:
            return SkillResult(success=False, message="No text provided.")
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            if ref:
                locator = self._resolve_ref(ref)
                locator.click(timeout=5000)
                locator.fill("", timeout=3000)  # Clear field
                locator.press_sequentially(text, delay=delay, timeout=15000)
            else:
                # Type into whatever is focused
                self._page.keyboard.type(text, delay=delay)

            self._page.wait_for_timeout(1500)  # Wait for autocomplete dropdown
            return SkillResult(
                success=True,
                message=f"Typed '{text[:50]}' character-by-character" + (f" into [ref={ref}]" if ref else "")
            )
        except ValueError as e:
            return SkillResult(success=False, message=str(e))
        except Exception as e:
            return SkillResult(success=False, message=f"Keyboard type failed: {e}")

    def _press_key(self, **kwargs) -> SkillResult:
        """Press a keyboard key (Enter, Tab, Escape, ArrowDown, etc.)."""
        key = kwargs.get("key", "")
        ref = kwargs.get("ref", "")

        if not key:
            return SkillResult(success=False, message="No key provided.")
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            if ref:
                locator = self._resolve_ref(ref)
                locator.press(key, timeout=5000)
            else:
                self._page.keyboard.press(key)
            self._page.wait_for_timeout(500)
            return SkillResult(success=True, message=f"Pressed key: {key}")
        except ValueError as e:
            return SkillResult(success=False, message=str(e))
        except Exception as e:
            return SkillResult(success=False, message=f"Press key failed: {e}")

    def _wait_for(self, **kwargs) -> SkillResult:
        """Wait for a condition: text visible, selector visible, or network idle.
        Inspired by OpenClaw's waitForViaPlaywright."""
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        text = kwargs.get("text", "")
        selector = kwargs.get("selector", "")
        load_state = kwargs.get("load_state", "")  # "load", "networkidle", "domcontentloaded"
        timeout = int(kwargs.get("timeout", 10000))
        delay_ms = int(kwargs.get("delay", 0))  # Raw delay in ms

        try:
            if delay_ms > 0:
                capped = min(delay_ms, 30000)
                self._page.wait_for_timeout(capped)
                return SkillResult(success=True, message=f"Waited {capped}ms")

            if text:
                self._page.get_by_text(text).first.wait_for(state="visible", timeout=timeout)
                return SkillResult(success=True, message=f"Text '{text[:50]}' is now visible")

            if selector:
                self._page.wait_for_selector(selector, state="visible", timeout=timeout)
                return SkillResult(success=True, message=f"Element '{selector}' is now visible")

            if load_state:
                self._page.wait_for_load_state(load_state, timeout=timeout)
                return SkillResult(success=True, message=f"Page reached '{load_state}' state")

            # Default: wait for networkidle
            self._page.wait_for_load_state("networkidle", timeout=timeout)
            return SkillResult(success=True, message="Network is idle")
        except Exception as e:
            return SkillResult(success=False, message=f"Wait failed: {e}")

    def _switch_tab(self, **kwargs) -> SkillResult:
        """Switch to a different open browser tab by index or URL pattern."""
        if not self._context:
            return SkillResult(success=False, message="No browser context.")

        tab_index = kwargs.get("index")
        url_pattern = kwargs.get("url", "")

        pages = self._context.pages
        if not pages:
            return SkillResult(success=False, message="No tabs open.")

        if tab_index is not None:
            idx = int(tab_index)
            if 0 <= idx < len(pages):
                self._page = pages[idx]
                self._refs = {}
                self._ref_counter = 0
                return SkillResult(success=True, message=f"Switched to tab {idx}: {self._page.url}")
            return SkillResult(success=False, message=f"Tab index {idx} out of range (0-{len(pages)-1})")

        if url_pattern:
            for p in pages:
                if url_pattern.lower() in p.url.lower():
                    self._page = p
                    self._refs = {}
                    self._ref_counter = 0
                    return SkillResult(success=True, message=f"Switched to tab: {self._page.url}")
            return SkillResult(success=False, message=f"No tab matching '{url_pattern}'")

        # Default: list all tabs
        tab_list = "\n".join(f"  [{i}] {p.url}" for i, p in enumerate(pages))
        return SkillResult(success=True, message=f"Open tabs:\n{tab_list}")

    def cleanup(self):
        """Close the browser and release resources."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._page = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logger.info("Browser closed")
