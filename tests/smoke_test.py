#!/usr/bin/env python3
"""
W.I.N.S.T.O.N. Smoke Test — quick health check to run before/after deploy.

Usage:
    python3 tests/smoke_test.py

Checks:
    1. All skill modules importable
    2. Web search dependency installed
    3. Web search returns live results
    4. Ollama reachable with models
    5. Telegram bot token configured
"""

import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

results = []


def check(name, fn):
    try:
        ok, msg = fn()
        status = PASS if ok else FAIL
        results.append(ok)
        print(f"  [{status}] {name}: {msg}")
    except Exception as e:
        results.append(False)
        print(f"  [{FAIL}] {name}: {e}")


def check_skill_imports():
    from winston.skills.web_search import WebSearchSkill
    from winston.skills.email_skill import EmailSkill
    from winston.skills.system_control import SystemControlSkill
    from winston.skills.notes_skill import NotesSkill
    from winston.skills.calendar_skill import CalendarSkill
    from winston.skills.code_runner_skill import CodeRunnerSkill
    from winston.skills.price_monitor_skill import PriceMonitorSkill
    return True, "All core skill modules importable"


def check_web_search_dependency():
    from winston.skills.web_search import _HAS_DDGS
    if _HAS_DDGS:
        return True, "duckduckgo-search is installed"
    return False, "duckduckgo-search NOT installed — pip install duckduckgo-search"


def check_web_search_live():
    from winston.skills.web_search import WebSearchSkill, _HAS_DDGS
    if not _HAS_DDGS:
        return False, "Skipped (dependency missing)"
    skill = WebSearchSkill()
    result = skill.execute(query="python programming language", max_results=2)
    if result.success and result.data:
        return True, f"Got {len(result.data)} results from DuckDuckGo"
    return False, f"Search failed: {result.message}"


def check_ollama():
    import requests
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            if models:
                return True, f"Ollama OK, models: {', '.join(models[:5])}"
            return False, "Ollama running but no models installed"
        return False, f"Ollama returned status {resp.status_code}"
    except requests.ConnectionError:
        return False, "Ollama not reachable at localhost:11434"


def check_telegram_token():
    try:
        from winston.config import load_config
        config = load_config()
        token = config.channels.telegram.bot_token
        if token and len(token) > 10:
            return True, f"Token configured ({token[:8]}...)"
        return False, "No Telegram bot token in config"
    except Exception as e:
        return False, f"Could not load config: {e}"


def check_safety_guard():
    from winston.core.safety import SafetyGuard, RiskLevel
    sg = SafetyGuard(require_confirmation=False)
    action = sg.request_action("web_search", {"query": "test"})
    if action.risk_level == RiskLevel.SAFE and action.approved:
        return True, "web_search classified as SAFE and auto-approved"
    return False, f"web_search risk={action.risk_level}, approved={action.approved}"


def main():
    print("\n  W.I.N.S.T.O.N. Smoke Test")
    print("  " + "=" * 40)

    check("Skill imports", check_skill_imports)
    check("Web search dependency", check_web_search_dependency)
    check("Web search live", check_web_search_live)
    check("Ollama connectivity", check_ollama)
    check("Telegram bot token", check_telegram_token)
    check("Safety guard", check_safety_guard)

    print("  " + "=" * 40)
    passed = sum(1 for r in results if r)
    total = len(results)
    if all(results):
        print(f"  [{PASS}] All {total} checks passed\n")
    else:
        failed = total - passed
        print(f"  [{FAIL}] {failed}/{total} checks failed\n")

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
