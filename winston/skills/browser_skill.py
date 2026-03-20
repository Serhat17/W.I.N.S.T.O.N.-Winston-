"""
Browser Automation Skill (beta) — interact with web pages using Playwright.
Navigate, click, fill forms, extract text, take page screenshots.

Uses SYNC Playwright API to avoid event-loop lifecycle issues,
since WINSTON skills execute synchronously.

OpenClaw-inspired architecture:
- AI-friendly error translation (LLM can self-correct)
- Connection resilience with retry + backoff
- iframe-aware ref resolution
- Batch actions (multiple actions per call)
- Screenshot with ref labels (visual grounding)
- Stale ref recovery (refs cached per URL)
- Navigation retry on frame detachment
"""

import base64
import json as _json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

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
        "You can combine url + action in a single call, e.g. url='apple.com' action='screenshot_page'. "
        "Supports batch actions, iframe interaction, and labeled screenshots."
    )
    parameters = {
        "action": (
            "Action: 'open_page' (navigate and show text), 'click' (click element by CSS selector), "
            "'click_text' (click element by visible text — PREFERRED over CSS selectors), "
            "'select_option' (select dropdown option by label or value), "
            "'fill' (type into input), 'extract_text' (get page/element text), "
            "'get_page_structure' (list all clickable links, buttons, forms on the page), "
            "'screenshot_page' (navigate + capture page screenshot), 'run_script' (execute JS), "
            "'snapshot' (accessibility tree with refs), 'snapshot_interactive' (compact: only interactive elements), "
            "'batch' (execute multiple actions in sequence), 'screenshot_labels' (screenshot with ref labels). "
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
    SNAPSHOT_MAX_CHARS_COMPACT = 12000  # For interactive-only mode

    # ── Connection resilience ──
    MAX_CONNECT_RETRIES = 3
    RETRY_BACKOFF_MS = [250, 500, 750]  # Backoff between retries

    def __init__(self, config=None, headless=True, storage_state=None, user_data_dir=None):
        super().__init__(config)
        self._headless = headless
        self._storage_state = storage_state  # Path to storage_state JSON (cookies + localStorage)
        self._user_data_dir = user_data_dir  # Persistent Chrome user-data directory (OpenClaw pattern)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._refs: dict = {}        # {ref_id: {role, name, nth, frame}}
        self._ref_counter: int = 0   # Sequential counter
        self._screenshots_dir = Path.home() / ".winston" / "browser_screenshots"
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        # Browser profile persistence (cookies + localStorage)
        self._profiles_dir = Path.home() / ".winston" / "browser_profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._active_profile: Optional[str] = None
        # Stale ref recovery: cache refs per URL so navigation back restores them
        self._refs_cache: Dict[str, dict] = {}  # {url: {refs, counter}}
        self._refs_cache_max = 20  # Max cached pages
        # Page state tracking (OpenClaw-inspired)
        self._console_log: List[str] = []  # Last N console messages
        self._page_errors: List[str] = []  # Last N page errors
        self._max_console = 50
        self._max_errors = 20

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
        """Lazy-launch the browser on first use (sync API) with retry logic.
        If *user_data_dir* was set, uses persistent context (OpenClaw pattern).
        If *profile* is given, restore saved cookies for that profile."""
        if self._page is not None:
            return

        last_error = None
        for attempt in range(self.MAX_CONNECT_RETRIES):
            try:
                from playwright.sync_api import sync_playwright

                self._playwright = sync_playwright().start()

                chrome_args = [
                    "--disable-blink-features=AutomationControlled",
                ]

                context_kwargs = dict(
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    permissions=[],  # Deny all permissions (geolocation, notifications, etc.)
                )

                if self._user_data_dir:
                    # Persistent context — full Chrome user-data directory (OpenClaw pattern).
                    # Cookies, localStorage, sessionStorage, IndexedDB all persist across runs.
                    os.makedirs(self._user_data_dir, exist_ok=True)
                    self._context = self._playwright.chromium.launch_persistent_context(
                        self._user_data_dir,
                        headless=self._headless,
                        args=chrome_args,
                        **context_kwargs,
                    )
                    # Persistent context has no separate browser object
                    self._browser = None
                    # Use existing page or open a new one
                    self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
                    logger.info(f"Persistent browser context launched (user_data_dir={self._user_data_dir})")
                else:
                    # Ephemeral context (standard Playwright)
                    self._browser = self._playwright.chromium.launch(
                        headless=self._headless,
                        args=chrome_args,
                    )
                    if self._storage_state and os.path.exists(self._storage_state):
                        context_kwargs["storage_state"] = self._storage_state
                        logger.info(f"Loading storage state from {self._storage_state}")
                    self._context = self._browser.new_context(**context_kwargs)
                    self._page = self._context.new_page()

                # Apply stealth to bypass bot detection (DataDome, etc.)
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
                    stealth.apply_stealth_sync(self._context)
                    logger.info("Stealth mode applied (anti-bot-detection)")
                except ImportError:
                    logger.info("playwright-stealth not installed, skipping stealth mode")

                # Track new tabs/popups automatically (OpenClaw pattern)
                self._context.on("page", self._on_new_page)

                # Track console messages and page errors (OpenClaw pattern)
                self._page.on("console", self._on_console)
                self._page.on("pageerror", self._on_page_error)

                logger.info(f"Browser launched (attempt {attempt + 1})")

                # Restore saved profile (cookies) if available
                if profile:
                    self._load_profile(profile)
                return  # Success
            except Exception as e:
                last_error = e
                logger.warning(f"Browser launch attempt {attempt + 1} failed: {e}")
                self._cleanup_partial()
                if attempt < self.MAX_CONNECT_RETRIES - 1:
                    backoff = self.RETRY_BACKOFF_MS[attempt] / 1000.0
                    time.sleep(backoff)

        # All retries failed
        logger.error(f"Failed to launch browser after {self.MAX_CONNECT_RETRIES} attempts: {last_error}")
        if last_error and ("TargetClosedError" in str(last_error) or "executable" in str(last_error).lower()):
            raise RuntimeError(
                f"Chromium binary error: {last_error}. "
                "Try running: python3 -m playwright install chromium"
            ) from last_error
        raise RuntimeError(f"Failed to launch browser: {last_error}")

    def _cleanup_partial(self):
        """Clean up partially initialized browser state (for retry)."""
        # Close page first, then context, then browser (if ephemeral mode)
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
            setattr(self, attr, None)
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _on_console(self, msg):
        """Track console messages for debugging."""
        try:
            entry = f"[{msg.type}] {msg.text}"
            self._console_log.append(entry)
            if len(self._console_log) > self._max_console:
                self._console_log = self._console_log[-self._max_console:]
        except Exception:
            pass

    def _on_page_error(self, error):
        """Track page errors."""
        try:
            self._page_errors.append(str(error)[:200])
            if len(self._page_errors) > self._max_errors:
                self._page_errors = self._page_errors[-self._max_errors:]
        except Exception:
            pass

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
        Retry on frame detachment (OpenClaw pattern).
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

            # Navigation with retry on frame detachment (OpenClaw pattern)
            for nav_attempt in range(2):
                try:
                    response = self._page.goto(url, wait_until="load", timeout=30000)

                    if response and not response.ok:
                        return SkillResult(
                            success=False,
                            message=f"Navigation failed with status {response.status}: {response.status_text} and current title is '{self._page.title()}'"
                        )

                    try:
                        self._page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        logger.debug("Network did not go idle within 10s, proceeding anyway.")

                    break  # Success
                except Exception as e:
                    err_msg = str(e).lower()
                    is_retryable = "frame" in err_msg and "detach" in err_msg or "target closed" in err_msg
                    if is_retryable and nav_attempt == 0:
                        logger.warning(f"Navigation failed (retryable): {e} — retrying")
                        # Force reconnect: get fresh page from context
                        try:
                            pages = self._context.pages
                            if pages:
                                self._page = pages[-1]
                                self._page.on("console", self._on_console)
                                self._page.on("pageerror", self._on_page_error)
                            else:
                                self._page = self._context.new_page()
                                self._page.on("console", self._on_console)
                                self._page.on("pageerror", self._on_page_error)
                        except Exception:
                            self._page = self._context.new_page()
                            self._page.on("console", self._on_console)
                            self._page.on("pageerror", self._on_page_error)
                        continue
                    else:
                        logger.warning(f"Navigation with 'load' failed, retrying with 'domcontentloaded': {e}")
                        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        break

            # Check for error pages
            try:
                title = self._page.title().lower()
                error_keywords = ["404", "not found", "page not found", "access denied", "site not found"]
                if any(kw in title for kw in error_keywords):
                    return SkillResult(
                        success=False,
                        message=f"Navigation landed on an error page. Title: '{self._page.title()}'. URL: {self._page.url}"
                    )
            except Exception:
                pass

            # Soft 404 check
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

            # Cache refs for the previous URL before navigation clears them
            self._cache_refs()

            # Settle time
            self._page.wait_for_timeout(1000)
        else:
            logger.info(f"Already at {url}, skipping redundant navigation.")

        if wait_for:
            try:
                self._page.wait_for_selector(wait_for, state="attached", timeout=15000)
                self._page.wait_for_selector(wait_for, state="visible", timeout=15000)
            except Exception:
                logger.warning(f"Wait for selector '{wait_for}' timed out/failed. Proceeding anyway.")
                self._page.wait_for_load_state("load", timeout=10000)

        # NOTE: We intentionally do NOT auto-dismiss cookie/consent overlays.
        # Following OpenClaw's approach: let the AI agent see them in the snapshot.
        # However, we DO remove invisible full-screen curtains that block all pointer
        # events (e.g. Apple's #globalnav-curtain) since these are site framework
        # artifacts, not user-facing dialogs.
        self._remove_pointer_blocking_curtains()

    def _remove_pointer_blocking_curtains(self):
        """Remove invisible full-screen fixed elements that block pointer events.
        These are site-framework artifacts (e.g. Apple #globalnav-curtain, React
        portal containers like :r8:/:r9:), NOT cookie banners or visible dialogs."""
        if not self._page:
            return
        try:
            removed = self._page.evaluate("""() => {
                const removed = [];
                document.querySelectorAll('*').forEach(el => {
                    const s = window.getComputedStyle(el);
                    const z = parseInt(s.zIndex || '0', 10);
                    const text = (el.innerText || '').trim();
                    const isFixed = s.position === 'fixed';
                    const isFullScreen = el.offsetWidth >= window.innerWidth * 0.9 &&
                                         el.offsetHeight >= window.innerHeight * 0.5;

                    // Type 1: Fixed, high z-index, full-screen, no visible text (e.g. globalnav-curtain)
                    if (isFixed && z > 5000 && isFullScreen && !text) {
                        removed.push(el.id || el.className || el.tagName);
                        el.remove();
                        return;
                    }

                    // Type 2: React portal containers — auto-generated ID (:rN:), fixed/absolute,
                    // covers significant area, has no meaningful text content
                    const isReactPortal = /^:r\\w+:$/.test(el.id);
                    if (isReactPortal && (isFixed || s.position === 'absolute') &&
                        z > 100 && isFullScreen && text.length < 10) {
                        removed.push(el.id);
                        el.remove();
                        return;
                    }
                });
                return removed;
            }""")
            if removed:
                logger.info(f"Removed pointer-blocking curtains: {removed}")
        except Exception as e:
            logger.debug(f"Curtain removal failed: {e}")

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
        # Only target elements inside overlay/modal/cookie containers, NOT regular page links.
        dismiss_js = """
        () => {
            const keywords = [
                'alle akzeptieren', 'accept all', 'akzeptieren', 'accept',
                'zustimmen', 'i agree', 'agree', 'allow all', 'alle erlauben',
                'got it', 'einverstanden', 'ok', 'understand'
            ];
            // Only look inside elements that appear to be overlays/modals
            const overlaySelectors = [
                '[class*="cookie"]', '[class*="consent"]', '[class*="overlay"]',
                '[class*="modal"]', '[class*="banner"]', '[class*="popup"]',
                '[id*="cookie"]', '[id*="consent"]', '[id*="overlay"]',
                '[id*="modal"]', '[id*="banner"]', '[id*="popup"]',
                '[role="dialog"]', '[role="alertdialog"]'
            ];
            let containers = [];
            for (const sel of overlaySelectors) {
                try { containers.push(...document.querySelectorAll(sel)); } catch(e) {}
            }
            if (containers.length === 0) return false;
            for (const container of containers) {
                const elements = Array.from(container.querySelectorAll(
                    'button, a, div[role="button"], span[role="button"]'
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
            }
            return false;
        }
        """
        try:
            url_before = self._page.url
            success = self._page.evaluate(dismiss_js)
            if success:
                logger.info("Dismissed overlay via JS fallback on main document.")
                self._page.wait_for_timeout(2000)
                # Guard: if the dismiss accidentally navigated away, go back
                if self._page.url.rstrip("/") != url_before.rstrip("/"):
                    logger.warning(f"Overlay dismiss navigated away to {self._page.url}, going back")
                    self._page.goto(url_before, wait_until="load", timeout=15000)
                    self._page.wait_for_timeout(1000)
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
        Also scans iframes for interactive elements (OpenClaw pattern).
        Truncates to SNAPSHOT_MAX_CHARS (default 50k) to prevent token overflow."""
        if not self._page:
            return "No page is open."

        if max_chars is None:
            max_chars = self.SNAPSHOT_MAX_CHARS

        try:
            raw = self._page.locator(":root").aria_snapshot(timeout=15000)
        except Exception as e:
            logger.warning(f"aria_snapshot() failed: {e}")
            return self.extract_page_structure()

        enhanced, refs = self._parse_aria_snapshot(raw)
        self._refs = refs

        # Scan iframes for interactive elements (OpenClaw pattern)
        iframe_section = self._scan_iframes()
        if iframe_section:
            enhanced += "\n\n" + iframe_section

        # Prepend page info
        try:
            title = self._page.title()
            url = self._page.url
            header = f"Page: {title}\nURL: {url}\n\n"
        except Exception:
            header = ""

        result = header + enhanced

        if len(result) > max_chars:
            result = result[:max_chars] + "\n[...TRUNCATED — page too large. Use snapshot_interactive for a compact view, or scroll to see other sections.]"

        # Cache refs for stale recovery
        self._cache_refs()

        return result

    def _scan_iframes(self) -> str:
        """Scan visible iframes for interactive elements and assign refs.
        OpenClaw pattern: iframe-scoped refs with frame selector stored."""
        if not self._page:
            return ""
        try:
            iframe_count = self._page.locator("iframe").count()
            if iframe_count == 0:
                return ""

            sections = []
            for i in range(min(iframe_count, 5)):  # Limit to 5 iframes
                frame_selector = f"iframe >> nth={i}"
                try:
                    frame_loc = self._page.frame_locator(frame_selector)
                    # Try to get a snapshot from the iframe
                    try:
                        iframe_snap = frame_loc.locator(":root").aria_snapshot()
                    except Exception:
                        continue

                    if not iframe_snap or len(iframe_snap.strip()) < 20:
                        continue

                    # Parse and assign refs with frame context
                    enhanced, frame_refs = self._parse_aria_snapshot(iframe_snap)
                    # Tag refs with frame selector
                    for ref_id, ref_info in frame_refs.items():
                        ref_info["frame"] = frame_selector
                        self._refs[ref_id] = ref_info

                    # Get iframe src for context
                    try:
                        src = self._page.locator("iframe").nth(i).get_attribute("src") or "unknown"
                    except Exception:
                        src = "unknown"

                    # Only include if there are interactive refs
                    if frame_refs:
                        sections.append(f"── iframe[{i}] ({src[:60]}) ──\n{enhanced}")
                except Exception:
                    continue

            return "\n".join(sections) if sections else ""
        except Exception:
            return ""

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
        """Resolve a ref ID to a Playwright Locator via get_by_role().
        Supports iframe-scoped refs (OpenClaw pattern)."""
        if ref not in self._refs:
            # Try restoring from cache for current URL
            self._restore_refs_from_cache()
            if ref not in self._refs:
                raise ValueError(
                    f"Unknown ref '{ref}'. Run a new snapshot to get updated refs."
                )

        info = self._refs[ref]
        role = info["role"]
        name = info.get("name")
        nth = info.get("nth")
        frame_selector = info.get("frame")

        kwargs = {}
        if name is not None:
            kwargs["name"] = name
            kwargs["exact"] = True

        # If ref is inside an iframe, use frameLocator (OpenClaw pattern)
        if frame_selector:
            scope = self._page.frame_locator(frame_selector)
        else:
            scope = self._page

        locator = scope.get_by_role(role, **kwargs)
        if nth is not None:
            locator = locator.nth(nth)
        return locator

    def _cache_refs(self):
        """Cache current refs for the current URL (stale ref recovery)."""
        if not self._page or not self._refs:
            return
        try:
            url = self._page.url
            self._refs_cache[url] = {
                "refs": dict(self._refs),
                "counter": self._ref_counter,
            }
            # Evict oldest if over limit
            if len(self._refs_cache) > self._refs_cache_max:
                oldest = next(iter(self._refs_cache))
                del self._refs_cache[oldest]
        except Exception:
            pass

    def _restore_refs_from_cache(self):
        """Try to restore refs from cache for the current URL."""
        if not self._page:
            return
        try:
            url = self._page.url
            if url in self._refs_cache:
                cached = self._refs_cache[url]
                self._refs = cached["refs"]
                self._ref_counter = cached["counter"]
                logger.info(f"Restored {len(self._refs)} refs from cache for {url}")
        except Exception:
            pass

    def _to_ai_error(self, error, ref: str = None) -> str:
        """Translate Playwright errors to actionable messages for the LLM.
        OpenClaw-inspired: the LLM can understand these and self-correct."""
        msg = str(error).lower()

        if "strict mode violation" in msg:
            count_match = re.search(r'resolved to (\d+) elements', msg)
            n = count_match.group(1) if count_match else "multiple"
            return (
                f"Selector matched {n} elements. Run a new snapshot to get "
                "updated refs with unique identifiers."
            )

        if "timeout" in msg and "waiting" in msg:
            return (
                "Element not found within timeout. The page may have changed. "
                "Run a new snapshot to see current elements."
            )

        if "not visible" in msg or "element is not visible" in msg:
            return (
                "Element exists but is not visible (hidden or off-screen). "
                "Try scrolling to it, or run a new snapshot."
            )

        if "intercepts pointer" in msg or "pointer events" in msg:
            overlay_match = re.search(r'<(\w+)[^>]*class="([^"]+)"', str(error))
            hint = ""
            if overlay_match:
                hint = f' Blocking element: <{overlay_match.group(1)} class="{overlay_match.group(2)}">.'
            return (
                f"Element not interactable — covered by an overlay.{hint} "
                "Try: dismiss the popup/cookie banner first, or scroll, or re-snapshot."
            )

        if "detached" in msg or "frame has been" in msg:
            return (
                "Element no longer exists — the page changed or navigated. "
                "Run a new snapshot to get fresh refs."
            )

        if "target closed" in msg or "browser has been closed" in msg:
            return (
                "Browser connection lost. The page or tab was closed. "
                "Use open_page to navigate to a new URL."
            )

        if "execution context was destroyed" in msg:
            return (
                "Page navigated during action — the old page is gone. "
                "Run a new snapshot to see the new page."
            )

        if "element is not an input" in msg or "not a select" in msg:
            return (
                "Wrong element type for this action. Run a new snapshot and "
                "use click_ref instead of type_ref, or vice versa."
            )

        if "protocol error" in msg:
            return (
                "Browser protocol error — the page may be unresponsive. "
                "Try waiting (wait_for with delay:3000) then re-snapshot."
            )

        ref_hint = f" (ref={ref})" if ref else ""
        return f"Action failed{ref_hint}: {str(error)[:200]}"

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
            "force_click_ref": self._force_click_ref,
            "type_ref": self._type_ref,
            "select_ref": self._select_ref,
            "hover_ref": self._hover_ref,
            "scroll": self._scroll,
            # OpenClaw-inspired actions
            "keyboard_type": self._keyboard_type,
            "press_key": self._press_key,
            "wait_for": self._wait_for,
            "switch_tab": self._switch_tab,
            "batch": self._batch,
            "screenshot_labels": self._screenshot_with_labels,
            "snapshot_interactive": self._snapshot_interactive,
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

            # NOTE: We intentionally do NOT auto-dismiss cookie/consent overlays.
            # But we do remove invisible curtains that block pointer events.
            self._remove_pointer_blocking_curtains()

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
        """Execute JavaScript on the current page. If 'ref' is provided, the script
        receives the element as first argument (like OpenClaw's evaluate-on-ref)."""
        script = kwargs.get("script", "")
        ref = kwargs.get("ref", "")
        if not script:
            return SkillResult(success=False, message="No JavaScript provided.")

        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open. Use 'open_page' first.")

        try:
            if ref:
                # Evaluate JS with element reference — function receives the element as arg
                locator = self._resolve_ref(ref)
                result = locator.evaluate(script)
            else:
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
                err_msg = str(click_err)
                if "intercepts pointer" in err_msg.lower() or "not receive pointer events" in err_msg.lower():
                    # OpenClaw pattern: return AI-friendly error immediately.
                    # Do NOT auto-retry or JS-fallback — let the LLM decide how
                    # to handle it (dismiss overlay, or use run_script/evaluate).
                    blocking_info = ""
                    import re as _re
                    m = _re.search(r'<([^>]+)>', err_msg)
                    if m:
                        blocking_info = f" Blocking element: {m.group(0)}."

                    logger.info(f"Click [ref={ref}] intercepted by overlay — returning error to LLM")
                    return SkillResult(
                        success=False,
                        message=(
                            f"Element [ref={ref}] is covered by another element.{blocking_info} "
                            "Options: (1) use run_script with ref to click directly on the element: "
                            "{\"action\":\"run_script\",\"ref\":\"" + ref + "\","
                            "\"script\":\"el => { el.click(); return true }\"}, "
                            "(2) use force_click_ref (works only if overlay has pointer-events:none), "
                            "(3) use run_script to remove the blocker, "
                            "(4) look in the snapshot for a dismiss/close button on the overlay."
                        )
                    )
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

    def _force_click_ref(self, **kwargs) -> SkillResult:
        """Click an element by ref, bypassing overlay/visibility checks.
        Uses Playwright's force=True which dispatches real browser-level mouse events
        at the element's coordinates — works with React, Angular, and other frameworks
        that listen on native events, unlike el.click() in JS."""
        ref = kwargs.get("ref", "")
        if not ref:
            return SkillResult(success=False, message="No ref provided.")
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        try:
            locator = self._resolve_ref(ref)
            url_before = self._page.url
            pages_before = len(self._context.pages) if self._context else 0

            locator.scroll_into_view_if_needed(timeout=5000)
            locator.click(force=True, timeout=5000)

            self._page.wait_for_timeout(1500)

            pages_after = len(self._context.pages) if self._context else 0
            url_after = self._page.url

            if pages_after > pages_before:
                msg = f"Force-clicked [ref={ref}] — NEW TAB opened: {self._page.url}"
            elif url_after != url_before:
                msg = f"Force-clicked [ref={ref}] — navigated to: {url_after}"
            else:
                msg = f"Force-clicked [ref={ref}] — page did NOT change (still on {url_after})."

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

    # ── Batch Actions (OpenClaw-inspired) ──

    def _batch(self, **kwargs) -> SkillResult:
        """Execute multiple actions in sequence. Reduces LLM round-trips.
        OpenClaw pattern: up to 20 actions per batch, stop on first error.

        Example: {"action": "batch", "actions": [
            {"action": "type_ref", "ref": "e3", "value": "hello"},
            {"action": "click_ref", "ref": "e5"},
            {"action": "wait_for", "load_state": "networkidle"}
        ]}
        """
        actions = kwargs.get("actions", [])
        stop_on_error = kwargs.get("stop_on_error", True)

        if not actions:
            return SkillResult(success=False, message="No actions provided for batch.")
        if len(actions) > 20:
            return SkillResult(success=False, message="Batch limited to 20 actions.")

        results = []
        for i, action_params in enumerate(actions):
            try:
                result = self.execute(**action_params)
                results.append(f"[{i+1}] {'OK' if result.success else 'FAIL'}: {result.message[:100]}")
                if not result.success and stop_on_error:
                    results.append(f"Stopped at action {i+1} due to error.")
                    break
            except Exception as e:
                results.append(f"[{i+1}] ERROR: {str(e)[:100]}")
                if stop_on_error:
                    break

        all_ok = all("OK:" in r for r in results if r.startswith("["))
        return SkillResult(
            success=all_ok,
            message="Batch results:\n" + "\n".join(results),
            speak=False,
        )

    # ── Screenshot with Ref Labels (OpenClaw-inspired) ──

    def _screenshot_with_labels(self, **kwargs) -> SkillResult:
        """Take a screenshot with ref labels overlaid on interactive elements.
        OpenClaw pattern: orange labels with ref IDs for visual grounding.
        Useful for combining vision model analysis with ref-based interaction."""
        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))

        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        # Ensure we have refs
        if not self._refs:
            self.snapshot()

        if not self._refs:
            return self._screenshot_page(**kwargs)

        try:
            # Inject label overlays for each ref
            label_count = self._page.evaluate("""
                (refs) => {
                    // Remove any existing labels
                    document.querySelectorAll('.winston-ref-label').forEach(el => el.remove());
                    let count = 0;
                    for (const [refId, info] of Object.entries(refs)) {
                        try {
                            // Find element by role + name
                            let elements;
                            const role = info.role;
                            const name = info.name;
                            if (name) {
                                elements = document.querySelectorAll(`[role="${role}"], ${role}`);
                                elements = Array.from(elements).filter(el => {
                                    const text = (el.textContent || el.getAttribute('aria-label') || '').trim();
                                    return text.includes(name) || name.includes(text.substring(0, 30));
                                });
                            } else {
                                elements = Array.from(document.querySelectorAll(`[role="${role}"], ${role}`));
                            }
                            const nth = info.nth || 0;
                            const el = elements[nth] || elements[0];
                            if (!el) continue;

                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;

                            const label = document.createElement('div');
                            label.className = 'winston-ref-label';
                            label.textContent = refId;
                            label.style.cssText = `
                                position: fixed;
                                left: ${rect.left}px;
                                top: ${Math.max(0, rect.top - 16)}px;
                                background: #ffb020;
                                color: #000;
                                font: bold 10px monospace;
                                padding: 1px 3px;
                                border-radius: 3px;
                                z-index: 2147483647;
                                pointer-events: none;
                                line-height: 14px;
                            `;
                            document.body.appendChild(label);
                            count++;
                        } catch(e) {}
                    }
                    return count;
                }
            """, dict(self._refs))

            # Take screenshot
            filename = f"labeled_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = self._screenshots_dir / filename
            self._page.screenshot(path=str(filepath), full_page=False)
            image_b64 = base64.b64encode(filepath.read_bytes()).decode("utf-8")

            # Remove labels
            self._page.evaluate("""
                () => document.querySelectorAll('.winston-ref-label').forEach(el => el.remove())
            """)

            return SkillResult(
                success=True,
                message=f"Screenshot with {label_count} ref labels saved to `{filepath}`",
                data={"path": str(filepath), "image_b64": image_b64, "label_count": label_count},
            )
        except Exception as e:
            logger.warning(f"Labeled screenshot failed, falling back to normal: {e}")
            return self._screenshot_page(**kwargs)

    # ── Interactive-Only Snapshot (OpenClaw compact mode) ──

    def _snapshot_interactive(self, **kwargs) -> SkillResult:
        """Return only interactive elements (buttons, links, inputs, selects).
        Much shorter than full snapshot — useful for complex pages.
        OpenClaw pattern: interactive-only mode with lower char limit."""
        self._auto_navigate(kwargs.get("url", ""), kwargs.get("wait_for"))
        if not self._page:
            return SkillResult(success=False, message="No page is open.")

        max_chars = kwargs.get("max_chars", self.SNAPSHOT_MAX_CHARS_COMPACT)
        full_snap = self.snapshot(max_chars=100000)  # Get all refs first

        # Filter to only lines with [ref=] markers + header
        lines = full_snap.split("\n")
        interactive_lines = []
        for line in lines:
            if line.startswith("Page:") or line.startswith("URL:") or "[ref=" in line:
                interactive_lines.append(line)

        result = "\n".join(interactive_lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... (truncated — use scroll to see more)"

        ref_count = sum(1 for l in interactive_lines if "[ref=" in l)
        result = f"[Interactive elements only: {ref_count} refs]\n" + result

        return SkillResult(success=True, message=result, data={"snapshot": result, "ref_count": ref_count}, speak=False)

    def cleanup(self):
        """Close the browser and release resources."""
        # Cache refs before cleanup
        self._cache_refs()

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
