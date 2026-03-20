import logging
import json
import time
import re
from enum import Enum
from typing import Optional, List, Dict
from winston.skills.browser_skill import BrowserSkill
from winston.core.brain import Brain
from winston.core.safety import SafetyGuard, RiskOverride, RiskLevel

logger = logging.getLogger("winston.browser_agent")


class CheckoutPhase(Enum):
    """Tracks where we are in a shopping checkout flow."""
    NAVIGATE = "navigate"     # Getting to the shop
    SEARCH = "search"         # Searching for products
    PRODUCT = "product"       # Viewing product details
    CART = "cart"              # Adding to cart / viewing cart
    CHECKOUT = "checkout"     # Filling shipping/payment
    CONFIRM = "confirm"       # Final confirmation — MUST pause here
    COMPLETE = "complete"     # Order placed

class InteractiveBrowserAgent:
    """
    Handles complex, multi-step browser interactions (booking, configuring, shopping, etc.).
    Uses an iterative Think -> Act -> Observe loop.

    For shopping tasks, tracks checkout phases (NAVIGATE → SEARCH → PRODUCT → CART →
    CHECKOUT → CONFIRM → COMPLETE) and enforces a mandatory pause before final purchase.

    NOTE: Browser automation is a beta feature. It works reliably on simple/medium
    complexity websites but may struggle with heavy SPA sites that use aggressive
    overlays, client-side rendering, or complex JavaScript navigation.
    """

    # Keywords that indicate a shopping/ordering task
    SHOPPING_KEYWORDS = [
        "buy", "purchase", "order", "bestell", "kauf", "add to cart", "warenkorb",
        "checkout", "shopping", "einkauf", "grocery", "groceries", "lebensmittel",
        "reorder", "nachbestell",
    ]

    # Patterns that indicate we've reached checkout/payment
    CHECKOUT_PATTERNS = [
        "place order", "place your order", "buy now", "confirm order",
        "complete purchase", "submit order", "pay now", "bezahlen",
        "bestellung aufgeben", "jetzt kaufen", "bestellung abschließen",
        "bestätigen", "confirm and pay", "proceed to payment",
    ]

    def __init__(self, brain: Brain, safety: SafetyGuard, browser_skill: BrowserSkill):
        self.brain = brain
        self.safety = safety
        self.browser = browser_skill
        self.max_steps = 60
        self._checkout_phase = CheckoutPhase.NAVIGATE
        self._is_shopping_task = False
        self._cart_items: list = []  # Track items added to cart
        self._global_action_counts: Dict[str, int] = {}  # Total count per action signature
        self._loop_count: int = 0  # How many times loop detection has triggered

    def _build_system_prompt(self) -> str:
        """Focused system prompt for browser automation only."""
        base = (
            "You are a browser automation agent. Complete the user's web task step by step.\n\n"
            "## How it works\n"
            "You receive an accessibility snapshot of the page with [ref=eN] markers on interactive elements.\n"
            "Use these refs to interact with elements. One action per step.\n\n"
            "## ACTIONS (output exactly ONE JSON per step):\n"
            "- open_page: Navigate to URL. {\"skill\":\"browser\",\"parameters\":{\"action\":\"open_page\",\"url\":\"https://...\"}}\n"
            "- snapshot: Re-capture page accessibility tree. {\"skill\":\"browser\",\"parameters\":{\"action\":\"snapshot\"}}\n"
            "- snapshot_interactive: COMPACT snapshot — only interactive elements. Use for complex/large pages. {\"skill\":\"browser\",\"parameters\":{\"action\":\"snapshot_interactive\"}}\n"
            "- click_ref: Click element by ref. {\"skill\":\"browser\",\"parameters\":{\"action\":\"click_ref\",\"ref\":\"e5\"}}\n"
            "- force_click_ref: Click element by ref, bypassing overlay checks. Uses real browser mouse events "
            "(works with React/Angular). {\"skill\":\"browser\",\"parameters\":{\"action\":\"force_click_ref\",\"ref\":\"e5\"}}\n"
            "  - Use when click_ref fails due to an overlay intercepting the click.\n"
            "- type_ref: Type into input. {\"skill\":\"browser\",\"parameters\":{\"action\":\"type_ref\",\"ref\":\"e3\",\"value\":\"...\",\"submit\":true}}\n"
            "  - Add \"slow\":true for autocomplete/search inputs that need char-by-char typing.\n"
            "- select_ref: Select dropdown option. {\"skill\":\"browser\",\"parameters\":{\"action\":\"select_ref\",\"ref\":\"e7\",\"value\":\"...\"}}\n"
            "- hover_ref: Hover over element. {\"skill\":\"browser\",\"parameters\":{\"action\":\"hover_ref\",\"ref\":\"e2\"}}\n"
            "- scroll: Scroll the page. {\"skill\":\"browser\",\"parameters\":{\"action\":\"scroll\",\"direction\":\"down\"}}\n"
            "- keyboard_type: Type char-by-char (triggers autocomplete/JS). {\"skill\":\"browser\",\"parameters\":{\"action\":\"keyboard_type\",\"ref\":\"e3\",\"text\":\"...\"}}\n"
            "  - Use for search boxes, autocomplete inputs, date pickers — where fill() doesn't trigger suggestions.\n"
            "- press_key: Press a keyboard key. {\"skill\":\"browser\",\"parameters\":{\"action\":\"press_key\",\"key\":\"Enter\"}}\n"
            "  - Keys: Enter, Tab, Escape, ArrowDown, ArrowUp, Backspace, etc.\n"
            "- wait_for: Wait for a condition. Options:\n"
            "  - Text visible: {\"skill\":\"browser\",\"parameters\":{\"action\":\"wait_for\",\"text\":\"Order placed\"}}\n"
            "  - Network idle: {\"skill\":\"browser\",\"parameters\":{\"action\":\"wait_for\",\"load_state\":\"networkidle\"}}\n"
            "  - Raw delay: {\"skill\":\"browser\",\"parameters\":{\"action\":\"wait_for\",\"delay\":3000}}\n"
            "- switch_tab: Switch browser tab. {\"skill\":\"browser\",\"parameters\":{\"action\":\"switch_tab\",\"index\":1}}\n"
            "  - Or by URL: {\"skill\":\"browser\",\"parameters\":{\"action\":\"switch_tab\",\"url\":\"booking\"}}\n"
            "  - No params: lists all open tabs.\n"
            "- batch: Execute MULTIPLE actions in sequence (reduces round-trips):\n"
            "  {\"skill\":\"browser\",\"parameters\":{\"action\":\"batch\",\"actions\":[\n"
            "    {\"action\":\"type_ref\",\"ref\":\"e3\",\"value\":\"hello\"},\n"
            "    {\"action\":\"click_ref\",\"ref\":\"e5\"},\n"
            "    {\"action\":\"wait_for\",\"load_state\":\"networkidle\"}\n"
            "  ]}}\n"
            "  Perfect for: fill form + submit, or type + wait + click suggestion.\n"
            "- screenshot_page: Take screenshot. {\"skill\":\"browser\",\"parameters\":{\"action\":\"screenshot_page\"}}\n"
            "- screenshot_labels: Screenshot with ref labels overlaid. {\"skill\":\"browser\",\"parameters\":{\"action\":\"screenshot_labels\"}}\n"
            "- extract_text: Get page text. {\"skill\":\"browser\",\"parameters\":{\"action\":\"extract_text\"}}\n\n"
            "## Legacy actions (still available):\n"
            "- click_text: Click by visible text. {\"skill\":\"browser\",\"parameters\":{\"action\":\"click_text\",\"text\":\"...\"}}\n"
            "- click: Click by CSS selector. {\"skill\":\"browser\",\"parameters\":{\"action\":\"click\",\"selector\":\"...\"}}\n"
            "- fill: Type into input by CSS. {\"skill\":\"browser\",\"parameters\":{\"action\":\"fill\",\"selector\":\"...\",\"value\":\"...\"}}\n"
            "- select_option: Select by CSS. {\"skill\":\"browser\",\"parameters\":{\"action\":\"select_option\",\"selector\":\"...\",\"value\":\"...\"}}\n"
            "- run_script: Run JS on page. {\"skill\":\"browser\",\"parameters\":{\"action\":\"run_script\",\"script\":\"...\"}}\n"
            "  - With ref (evaluate on element): {\"skill\":\"browser\",\"parameters\":{\"action\":\"run_script\",\"ref\":\"e5\",\"script\":\"el => el.click()\"}}\n"
            "  - When ref is provided, the script receives the DOM element as first argument.\n\n"
            "## CRITICAL RULES:\n"
            "1. READ the snapshot carefully. ONLY interact with elements that have [ref=eN] markers.\n"
            "2. PREFER ref-based actions (click_ref, type_ref, select_ref) over CSS selectors.\n"
            "3. After page-changing actions, you get a fresh snapshot automatically.\n"
            "4. If the page changes unexpectedly, use snapshot to get fresh refs.\n"
            "5. On 404/error: go to the homepage and navigate from there.\n"
            "6. For product configs (e.g. Apple): pick the most expensive model, then upgrade RAM/storage to maximum.\n"
            "7. Be EFFICIENT. Never repeat a failed action — try something different.\n"
            "8. Output ONLY JSON for actions. Plain text ONLY when task is COMPLETE.\n"
            "9. If the page has no relevant links, try open_page to navigate directly to a likely URL.\n"
            "10. For SEARCH/AUTOCOMPLETE fields: use keyboard_type or type_ref with slow:true, then snapshot, then click the suggestion.\n"
            "11. If a link opens a new tab, you will automatically switch to it. Use switch_tab to go back if needed.\n"
            "12. If you notice you are repeating the same action, STOP and try a completely different approach.\n"
            "13. After clicking a button that triggers navigation, use wait_for with load_state:networkidle before taking a snapshot.\n"
            "14. Use batch action to combine related actions (e.g. fill + submit, or type + wait + click).\n"
            "15. If the snapshot says TRUNCATED or the page is very large, use snapshot_interactive for a compact view.\n"
            "16. Elements in iframes also have refs. Interact with them normally using click_ref/type_ref.\n"
            "17. Error messages tell you WHAT TO DO. Read them carefully and follow the guidance.\n"
            "18. READ-ONLY tasks: If the task only asks to READ information (titles, prices, headlines, etc.), "
            "use extract_text or read the snapshot — do NOT click links unless navigation is needed. "
            "The snapshot and extract_text already contain the text you need.\n"
            "19. AVOID external links: Never click share buttons (Facebook, Twitter, email) or ad links. "
            "Stay on the target site. If you accidentally leave, use switch_tab or open_page to go back.\n"
            "20. THINK before acting: Before each action, consider if you already have the answer "
            "in the current snapshot or extracted text. If yes, respond with the answer directly.\n"
            "21. OVERLAYS/POPUPS: If click_ref fails because an overlay blocks it:\n"
            "   - BEST: use run_script with ref: {\"action\":\"run_script\",\"ref\":\"eN\",\"script\":\"el => { el.click(); return true }\"} "
            "— this executes directly on the element, completely bypassing any overlay.\n"
            "   - OK for buttons (not radios): force_click_ref dispatches at coordinates. Works if no overlay at those coords.\n"
            "   - ALT: dismiss overlay via close button, or use run_script to remove the blocking element.\n"
            "22. E-COMMERCE NAVIGATION: On product listing pages, use run_script to discover product links/prices "
            "in the DOM, then open_page to navigate directly. For product configurators with radio buttons, "
            "use run_script with ref to click them: {\"action\":\"run_script\",\"ref\":\"eN\",\"script\":\"el => { el.click(); return el.checked }\"} "
            "This fires the click event FROM the element itself, properly triggering React/Angular event delegation.\n"
        )

        if self._is_shopping_task:
            base += (
                "\n## SHOPPING RULES (this is a shopping task):\n"
                "10. Follow the checkout phases: SEARCH → PRODUCT → CART → CHECKOUT → CONFIRM.\n"
                "11. After adding items to cart, ALWAYS verify the cart contents and total.\n"
                "12. At checkout, report the order summary as plain text: items, quantities, total price.\n"
                "13. NEVER click 'Place Order', 'Buy Now', 'Confirm Order', or similar final purchase buttons.\n"
                "    Instead, output: CHECKOUT_PAUSE: {summary of order} and STOP.\n"
                "14. Report prices clearly in each step so the user can track spending.\n"
                "15. If you need to log in, do so — the session will be saved automatically.\n"
            )

        return base

    def _get_page_context(self) -> str:
        """Get page accessibility snapshot with refs for the LLM.
        Falls back to extract_page_structure() if snapshot fails."""
        if not self.browser._page:
            return "No page is open yet."
        try:
            snap = self.browser.snapshot()
            if snap and snap != "No page is open.":
                return snap
        except Exception as e:
            logger.warning(f"Snapshot failed, falling back to page structure: {e}")
        try:
            return self.browser.extract_page_structure()
        except Exception as e:
            logger.warning(f"Failed to extract page structure: {e}")
            try:
                return f"Page: {self.browser._page.title()}\nURL: {self.browser._page.url}"
            except Exception:
                return "Could not read page state."

    def _analyze_screenshot_with_vision(self, context_hint: str = "") -> str:
        """Capture screenshot and analyze with vision model.
        Only used as last resort — local vision models are slow (~15-30s).
        Skip entirely when using a cloud LLM (it already has page structure).
        """
        # Skip vision if using cloud provider — page structure text is sufficient
        if self.brain._current_provider != "ollama":
            logger.info("Skipping vision analysis (cloud provider has page structure)")
            return ""
        try:
            shot_res = self.browser.execute(action="screenshot_page")
            if not shot_res.success or not shot_res.data:
                return ""
            image_b64 = shot_res.data.get("image_b64", "")
            if not image_b64:
                return ""
            query = "Describe this web page briefly: what page, what buttons/links/dropdowns visible, any popups."
            if context_hint:
                query += f" Context: {context_hint}"
            return self.brain._get_image_description(images=[image_b64], user_query=query)
        except Exception as e:
            logger.warning(f"Vision analysis failed: {e}")
            return ""

    def execute_task(self, user_input: str, override: RiskOverride, channel=None, confirm_callback=None) -> str:
        """Executes a web task iteratively.
        For shopping tasks, enforces the checkout state-machine and blocks final purchase."""
        logger.info(f"Starting InteractiveBrowserAgent for task: {user_input}")

        # Detect if this is a shopping task
        low_input = user_input.lower()
        self._is_shopping_task = any(kw in low_input for kw in self.SHOPPING_KEYWORDS)
        if self._is_shopping_task:
            self._checkout_phase = CheckoutPhase.NAVIGATE
            self._cart_items = []
            logger.info("Shopping task detected — checkout state-machine active")

        system_prompt = self._build_system_prompt()

        # History uses only user/assistant roles for OpenAI compatibility.
        # Observations go as "user" messages (the system feeding info back).
        history = [
            {"role": "user", "content": f"Task: {user_input}"}
        ]

        step = 0
        final_response = ""
        consecutive_failures = 0
        last_page_structure = ""
        self._global_action_counts = {}  # Total count per action signature
        self._loop_count = 0  # Times loop detection has triggered

        try:
            self.browser._ensure_browser()
        except Exception as e:
            return f"Failed to initialize browser: {e}"

        while step < self.max_steps:
            step += 1
            logger.info(f"Browser Agent Step {step}/{self.max_steps}")

            if step > 1:
                time.sleep(0.5)

            # Get page context if we don't already have it from the last action
            if not last_page_structure:
                last_page_structure = self._get_page_context()

            prompt = (
                f"Step {step}/{self.max_steps}. What is your NEXT action?\n\n"
                f"CURRENT PAGE (only click items listed here):\n{last_page_structure}"
            )

            # Keep history compact: task + last 6 exchanges
            trimmed = history[:1] + history[-10:] if len(history) > 11 else history

            # Use system_override to skip brain's default system prompt + all skill descriptions
            response = self.brain.think(
                prompt,
                conversation_history=trimmed,
                system_override=system_prompt
            )
            logger.info(f"LLM response (first 300 chars): {response[:300]}")
            history.append({"role": "assistant", "content": response})

            skill_calls = self.brain.parse_skill_calls(response)

            # Fallback: malformed JSON with "action" but no "skill" wrapper
            if not skill_calls:
                try:
                    match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
                    if match:
                        raw = json.loads(match.group(1))
                        if "action" in raw and "skill" not in raw:
                            skill_calls = [{"skill": "browser", "parameters": raw}]
                except Exception:
                    pass

            # Fallback: bare JSON without fences
            if not skill_calls:
                try:
                    match = re.search(r'\{[^{}]*"action"\s*:\s*"[^"]+?"[^{}]*\}', response)
                    if match:
                        raw = json.loads(match.group(0))
                        if "action" in raw:
                            skill_calls = [{"skill": "browser", "parameters": raw}]
                except Exception:
                    pass

            # No skill calls = task complete or LLM gave up
            if not skill_calls:
                stripped = self.brain.strip_skill_blocks(response).strip()
                # If response is too short/garbage, it's a malformed LLM response, not completion
                if len(stripped) < 20 and step < self.max_steps - 2:
                    logger.warning(f"Malformed LLM response (len={len(stripped)}), retrying step")
                    history.append({"role": "user", "content": (
                        "[System] Your response was not valid. Output EXACTLY ONE JSON action per step. "
                        "Example: {\"skill\":\"browser\",\"parameters\":{\"action\":\"snapshot\"}}"
                    )})
                    last_page_structure = ""
                    continue
                logger.info("No skill calls — task complete or needs input.")
                final_response = stripped
                if not final_response:
                    final_response = "Browser task completed."

                # Check for CHECKOUT_PAUSE signal from the LLM
                if self._is_shopping_task and "CHECKOUT_PAUSE" in response:
                    final_response = self._format_checkout_pause(response)
                    self._checkout_phase = CheckoutPhase.CONFIRM
                break

            call = skill_calls[0]
            if call.get("skill") != "browser":
                final_response = "Error: unexpected non-browser skill."
                break

            parameters = call.get("parameters", {})
            logger.info(f"Action: {parameters}")

            # ── Simple loop detection ──
            # Exclude read-only/observation actions from loop detection —
            # these are safe to repeat and don't indicate the agent is stuck.
            action_name = parameters.get("action", "")
            READ_ONLY_ACTIONS = {
                "extract_text", "snapshot", "snapshot_interactive", "screenshot_page",
                "screenshot_labels", "get_page_structure", "switch_tab", "wait_for",
                "scroll",
            }
            if action_name not in READ_ONLY_ACTIONS:
                action_sig = json.dumps(parameters, sort_keys=True)
                self._global_action_counts[action_sig] = self._global_action_counts.get(action_sig, 0) + 1
                count = self._global_action_counts[action_sig]
            else:
                count = 0  # Never trigger loop detection for read-only actions

            # Block open_page to the same URL we're already on
            if parameters.get("action") == "open_page":
                target = (parameters.get("url") or "").rstrip("/").lower()
                current = (self.browser.current_url or "").rstrip("/").lower()
                if target and current and target == current:
                    logger.info(f"  BLOCKED: Already on {current}")
                    history.append({"role": "user", "content": (
                        f"[System] BLOCKED: You are already on {self.browser.current_url}. "
                        "Do NOT navigate to the same page. Try: scroll down, click a different "
                        "element, or use run_script to inspect links in the DOM."
                    )})
                    last_page_structure = ""
                    continue

            if count >= 3:
                self._loop_count += 1
                logger.warning(f"Action repeated {count}x (loop #{self._loop_count})")

                # Hard abort after 3 loop detections
                if self._loop_count >= 3:
                    logger.error("3 loops detected — aborting task")
                    final_response = (
                        f"Task stopped after {step} steps — could not make progress.\n"
                        f"Page: {self.browser.current_url}"
                    )
                    break

                # Auto-extract DOM links and provide to LLM
                try:
                    js_res = self.browser.execute(
                        action="run_script",
                        script="JSON.stringify([...document.querySelectorAll('a[href]')].map(a=>({href:a.href,text:(a.textContent||'').trim().slice(0,60)})).filter(a=>a.text&&a.href&&!a.href.includes('#')&&!a.href.includes('javascript')&&a.href!==location.href).slice(0,25))"
                    )
                    links = js_res.message if js_res.success else "Could not read DOM"
                except Exception:
                    links = "DOM read failed"

                history.append({"role": "user", "content": (
                    f"[System] STUCK: This action has been tried {count} times without progress. "
                    "It will NOT work. Try something completely different.\n\n"
                    f"Links on this page:\n{links}\n\n"
                    "Use open_page with a URL from above, or try scroll + snapshot."
                )})
                last_page_structure = ""
                continue

            # Shopping guard: block final purchase clicks
            if self._is_shopping_task and self._is_final_purchase_action(parameters, last_page_structure):
                logger.warning("BLOCKED: Agent tried to click final purchase button.")
                self._checkout_phase = CheckoutPhase.CONFIRM
                # Extract what we know about the order
                final_response = (
                    "⚠️ **Bestellung pausiert!**\n\n"
                    "Ich war kurz davor, die Bestellung abzuschließen, habe aber gestoppt.\n"
                    "Bitte überprüfe die Bestellung im Browser und bestätige manuell.\n\n"
                    f"Aktuelle Seite: {self.browser.current_url}"
                )
                # Take a screenshot for the user to review
                try:
                    shot = self.browser.execute(action="screenshot_page")
                    if shot.success and shot.data:
                        final_response += f"\n📸 Screenshot: {shot.data.get('path', '')}"
                except Exception:
                    pass
                break

            # Safety check
            action_req = self.safety.request_action("browser", parameters, override=override)

            if action_req.risk_level == RiskLevel.BLOCKED:
                history.append({"role": "user", "content": f"[System] Action blocked: {action_req.description}"})
                last_page_structure = ""
                continue

            if not action_req.approved:
                if override == RiskOverride.CAREFUL and parameters.get("action") in [
                    "click", "click_text", "fill", "select_option", "run_script",
                    "click_ref", "type_ref", "select_ref",
                ]:
                    try:
                        shot_res = self.browser.execute(action="screenshot_page")
                        if shot_res.success:
                            action_req.screenshot_path = shot_res.data.get("path")
                    except Exception:
                        pass
                confirmed = confirm_callback(action_req, channel) if confirm_callback else False
                if not confirmed:
                    final_response = "Action denied. Stopping."
                    break

            # Execute the action
            try:
                result = self.browser.execute(**parameters)

                if result.success:
                    consecutive_failures = 0
                    current_url = self.browser.current_url or "unknown"
                    logger.info(f"  ✓ {action_name}: {result.message[:200]}")
                    obs = f"[Result] OK: {result.message}"

                    # Auto-snapshot after page-changing actions (including ref-based)
                    page_changing = (
                        "open_page", "click", "click_text", "click_ref",
                        "force_click_ref", "select_option", "select_ref",
                    )
                    # type_ref with submit also changes the page
                    is_type_submit = action_name == "type_ref" and parameters.get("submit")

                    if action_name in page_changing or is_type_submit:
                        time.sleep(1.5)  # Wait for dynamic content to load
                        try:
                            last_page_structure = self.browser.snapshot()
                            obs += f"\n\nCurrent URL: {self.browser.current_url}\n{last_page_structure}"
                        except Exception:
                            try:
                                last_page_structure = self.browser.extract_page_structure()
                                obs += f"\n\nCurrent URL: {self.browser.current_url}\n{last_page_structure}"
                            except Exception:
                                last_page_structure = ""
                    elif action_name == "snapshot":
                        last_page_structure = ""
                    elif action_name == "extract_text":
                        # Truncate long extract_text results to prevent LLM context overflow
                        text_data = result.data.get("text", "") if result.data else ""
                        if len(text_data) > 3000:
                            text_data = text_data[:3000] + "\n\n[...TRUNCATED — use snapshot for structured view]"
                        obs = f"[Extracted text from {current_url}]:\n{text_data}"
                        obs += "\n\nREMINDER: If you already have the answer in this text, respond with plain text now. Do NOT click more links."
                        last_page_structure = ""
                    else:
                        last_page_structure = ""

                    if result.data and action_name not in ("open_page", "click", "click_text", "click_ref", "snapshot", "extract_text"):
                        obs += f"\nData: {json.dumps(result.data)[:500]}"

                    history.append({"role": "user", "content": obs})
                else:
                    consecutive_failures += 1
                    logger.info(f"  ✗ {action_name}: {result.message[:200]}")
                    obs = f"[Result] FAILED: {result.message}"

                    if "Navigation failed" in result.message or "error page" in result.message:
                        obs += " Try the homepage instead."

                    # Vision only on 3rd+ consecutive failure (snapshots are more informative)
                    if consecutive_failures >= 3:
                        vision_desc = self._analyze_screenshot_with_vision(f"Failed: {result.message}")
                        if vision_desc:
                            obs += f"\nVision: {vision_desc}"

                    # Always refresh snapshot on failure
                    try:
                        last_page_structure = self.browser.snapshot()
                        obs += f"\n\nCurrent page:\n{last_page_structure}"
                    except Exception:
                        try:
                            last_page_structure = self.browser.extract_page_structure()
                            obs += f"\n\nCurrent page:\n{last_page_structure}"
                        except Exception:
                            last_page_structure = ""

                    history.append({"role": "user", "content": obs})

                    if consecutive_failures >= 3:
                        history.append({"role": "user", "content":
                            "[System] WARNING: 3 failures. Try a COMPLETELY different approach."
                        })

            except Exception as e:
                history.append({"role": "user", "content": f"[System] Error: {str(e)}"})
                logger.error(f"Browser error: {e}")
                consecutive_failures += 1
                last_page_structure = ""

        if step >= self.max_steps:
            logger.warning("Agent reached maximum steps.")
            final_response = "Reached maximum steps. Task may be incomplete."

        # Auto-save browser profile for shopping tasks (preserves login session)
        if self._is_shopping_task:
            self._auto_save_profile()

        return final_response

    # ── Shopping State-Machine Helpers ─────────────────────────────────

    def _is_final_purchase_action(self, parameters: dict, page_snapshot: str) -> bool:
        """Check if the agent is about to click a final purchase/order button."""
        action = parameters.get("action", "")
        if action not in ("click_ref", "click_text", "click"):
            return False

        # Check click_text target
        text = parameters.get("text", "").lower()
        if text and any(p in text for p in self.CHECKOUT_PATTERNS):
            return True

        # Check if the ref resolves to a purchase button
        ref = parameters.get("ref", "")
        if ref and ref in self.browser._refs:
            ref_info = self.browser._refs[ref]
            name = (ref_info.get("name") or "").lower()
            if any(p in name for p in self.CHECKOUT_PATTERNS):
                return True

        # Check the page snapshot for common final-purchase contexts
        if page_snapshot:
            low_snap = page_snapshot.lower()
            # If page has "order summary" or "payment" AND the action targets a button
            checkout_page = any(kw in low_snap for kw in [
                "order summary", "payment method", "bestellübersicht",
                "zahlungsart", "review your order", "order total",
            ])
            if checkout_page and action in ("click_ref", "click_text"):
                # Check if the button being clicked looks like a purchase confirmation
                if ref and ref in self.browser._refs:
                    role = self.browser._refs[ref].get("role", "")
                    if role == "button":
                        name = (self.browser._refs[ref].get("name") or "").lower()
                        confirm_words = ["order", "buy", "purchase", "pay", "confirm",
                                         "bestell", "kauf", "bezahl", "bestätig"]
                        if any(w in name for w in confirm_words):
                            return True

        return False

    def _format_checkout_pause(self, response: str) -> str:
        """Format the checkout pause message with order details."""
        # Extract the summary after CHECKOUT_PAUSE:
        match = re.search(r'CHECKOUT_PAUSE:\s*(.+)', response, re.DOTALL)
        summary = match.group(1).strip() if match else "Order details available on the page."

        return (
            "⚠️ **Bestellung bereit — Bestätigung erforderlich!**\n\n"
            f"{summary}\n\n"
            f"🔗 Aktuelle Seite: {self.browser.current_url}\n\n"
            "Die Bestellung wurde NICHT abgeschickt. "
            "Bitte überprüfe alles im Browser und bestätige manuell, "
            "oder sage mir 'bestätigen' um fortzufahren."
        )

    def _auto_save_profile(self):
        """Auto-save the browser profile based on the current domain."""
        try:
            url = self.browser.current_url
            if url and url.startswith("http"):
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.replace("www.", "")
                # Use the base domain as profile name (e.g. "tesco.com")
                if domain:
                    self.browser.save_profile(domain)
                    logger.info(f"Auto-saved browser profile for '{domain}'")
        except Exception as e:
            logger.debug(f"Auto-save profile failed: {e}")
