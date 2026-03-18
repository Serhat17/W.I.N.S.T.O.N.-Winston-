"""
Shared input-processing pipeline logic used by both main.py (CLI) and server.py (API).

Eliminates drift between the three process_input implementations by extracting
common functions: override parsing, fallback detection, response refinement, and
response finalization.
"""

import logging
import re
from typing import Optional, List

from winston.core.safety import RiskOverride
from winston.skills.base import SkillResult

logger = logging.getLogger("winston.core.pipeline")

# ── Shared constants ──────────────────────────────────────────────────

# Skills that are auto-approved without user confirmation (read-only / low-risk).
# Keep this as the single source of truth — main.py and server.py both import it.
AUTO_APPROVE_SKILLS = (
    "web_search", "web_fetch", "notes", "system_control", "clipboard",
    "desktop_screenshot", "youtube", "calendar", "file_manager",
    "smart_home", "code_runner", "audio_analysis",
    "travel", "google_calendar", "knowledge_base", "image_gen",
    "shopping",
)

# Keywords that signal the user wants current / real-time information
WEB_SEARCH_TRIGGERS = [
    "weather", "temperature", "forecast", "rain", "sunny", "snow",
    "news", "latest", "current", "today", "right now", "what is happening",
    "search", "look up", "find", "google", "who is", "what is", "price of",
    "stock", "score", "result", "when does", "how much",
]

# Phrases the LLM uses when it gives up instead of searching
LLM_GAVE_UP = [
    "i couldn't find", "i can't find", "i don't have access",
    "i'm not able to", "i am not able", "i don't know the current",
    "i cannot access", "real-time", "up-to-date information",
    "would you like me to search", "would you like me to look",
    "let me search", "let me look",
    "can't perform web search", "cannot perform web search",
]

# Travel keywords that should NOT trigger generic web search fallback
TRAVEL_KEYWORDS = [
    "flight", "flights", "hotel", "hotels", "booking", "booking.com",
    "kayak", "skyscanner", "expedia", "ryanair", "easyjet", "pegasus",
    "turkish airlines", "lufthansa", "amadeus",
]

# Regex to detect URLs in user input
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+")

# Browser-intent detection keywords (German + English)
BROWSER_TRIGGERS_DE = [
    "gehe auf", "öffne", "navigiere zu", "besuche", "konfiguriere",
    "apple.com", "amazon.com", "warenkorb", "screenshot",
]
BROWSER_TRIGGERS_EN = [
    "go to", "open", "navigate to", "visit", "browse to",
    "configure on", "add to cart", "screenshot of",
]

# Shopping-intent keywords — trigger the shopping agent persona
SHOPPING_TRIGGERS_DE = [
    "bestell", "kauf", "einkauf", "warenkorb", "nachbestell",
    "einkaufsliste", "shopping", "lebensmittel", "supermarkt",
    "bestell nochmal", "nochmal bestellen",
]
SHOPPING_TRIGGERS_EN = [
    "order", "buy", "purchase", "add to cart", "reorder",
    "shopping list", "grocery", "groceries", "checkout",
    "order again", "same order",
]

# Summarizer prompt used for context compaction
SUMMARIZER_PROMPT = "You are a concise summarizer. Output only the summary."


# ── Functions ─────────────────────────────────────────────────────────

def parse_override(sanitized: str) -> tuple:
    """
    Parse 'mach vorsichtig' / 'mach' prefix from user input.

    Returns:
        (override: RiskOverride, cleaned_input: str)
    """
    lowered = sanitized.lower()
    if lowered.startswith("mach vorsichtig ") or lowered.startswith("mach vorsichtig:"):
        return RiskOverride.CAREFUL, sanitized[16:].strip(": ")
    elif lowered.startswith("mach ") or lowered.startswith("mach:"):
        return RiskOverride.AUTONOMOUS, sanitized[5:].strip(": ")
    return RiskOverride.NONE, sanitized


def needs_web_fetch(user_input: str, skills_dict: dict) -> Optional[str]:
    """
    Return the URL if user input contains a URL and web_fetch is available.
    """
    if "web_fetch" not in skills_dict:
        return None
    match = URL_PATTERN.search(user_input)
    if match:
        return match.group(0)
    return None


def needs_web_search(user_input: str, llm_response: str, skills_dict: dict) -> bool:
    """
    Return True if the query needs a web search but the LLM didn't trigger one.
    Excludes travel queries to prevent hijacking the travel skill.
    """
    if "web_search" not in skills_dict:
        return False

    low_input = user_input.lower()

    # Prevent travel queries from being hijacked
    if any(kw in low_input for kw in TRAVEL_KEYWORDS):
        return False

    low_resp = llm_response.lower()
    has_trigger = any(kw in low_input for kw in WEB_SEARCH_TRIGGERS)
    llm_gave_up = any(ph in low_resp for ph in LLM_GAVE_UP)
    return has_trigger or llm_gave_up


def needs_browser(user_input: str, skills_dict: dict) -> bool:
    """
    Return True if the query clearly needs the browser skill.
    """
    if "browser" not in skills_dict:
        return False
    low = user_input.lower()
    return any(kw in low for kw in BROWSER_TRIGGERS_DE + BROWSER_TRIGGERS_EN)


def detect_fallback_calls(
    sanitized: str,
    response: str,
    skills_dict: dict,
    include_browser: bool = False,
) -> list:
    """
    Fallback chain: web_fetch (URL detected) → web_search (keywords) → browser (optional).

    Returns a list of skill call dicts, or empty list if no fallback needed.
    """
    fetch_url = needs_web_fetch(sanitized, skills_dict)
    if fetch_url:
        logger.info(f"URL detected in input — injecting web_fetch for {fetch_url}")
        return [{"skill": "web_fetch", "parameters": {"url": fetch_url}}]

    if needs_web_search(sanitized, response, skills_dict):
        logger.info("LLM skipped web_search skill — injecting fallback call")
        return [{"skill": "web_search", "parameters": {"query": sanitized, "max_results": 5}}]

    if include_browser and needs_browser(sanitized, skills_dict):
        logger.info("LLM skipped browser skill — injecting fallback call")
        return [{"skill": "browser", "parameters": {"url": "", "action": "open_page"}}]

    return []


def refine_response(brain, sanitized: str, skill_results: List[SkillResult], context: list) -> str:
    """
    Ask the brain to summarize skill results into a natural response.
    """
    results_text = "\n".join(r.message for r in skill_results)
    return brain.think(
        f"The user asked: {sanitized}\n\n"
        f"Here are the actual results from the system:\n{results_text}\n\n"
        f"Now respond naturally to the user based on these results. "
        f"Do NOT output any JSON blocks. Do NOT use skill calls. "
        f"Just give a clean, natural language summary.",
        conversation_history=context,
    )


def finalize_response(response: str, brain, safety) -> str:
    """
    Strip remaining skill-call JSON blocks and filter sensitive output.
    """
    if '"skill"' in response:
        response = brain.strip_skill_blocks(response)
    return safety.filter_output(response)


def compact_memory(memory, brain, context_window: int):
    """
    Compact conversation memory if approaching context window limit.
    """
    memory.compact_if_needed(
        summarize_fn=lambda text: brain.think(text, system_override=SUMMARIZER_PROMPT),
        context_window=context_window,
    )
