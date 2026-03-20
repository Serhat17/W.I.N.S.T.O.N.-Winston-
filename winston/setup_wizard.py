"""
Interactive first-run setup wizard for W.I.N.S.T.O.N.
Guides new users through connecting Telegram, adding API keys, choosing a model, etc.
Run with: python -m winston.main --setup
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

# ── Colours ───────────────────────────────────────────
BOLD = "\033[1m"
GREEN = "\033[32m"
BLUE = "\033[34m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
NC = "\033[0m"

WINSTON_DIR = Path.home() / ".winston"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user with an optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def _ask_yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} ({hint})", "y" if default else "n")
    return answer.lower() in ("y", "yes", "ja", "j")


def _section(title: str):
    print(f"\n{BOLD}{BLUE}── {title} ──{NC}\n")


def _ok(msg: str):
    print(f"  {GREEN}✓{NC} {msg}")


def _skip(msg: str):
    print(f"  {DIM}– {msg} (skipped){NC}")


def _write_env(key: str, value: str):
    """Append or update a key in the .env file."""
    env_path = ENV_FILE
    lines = []
    found = False
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────
# Wizard steps
# ──────────────────────────────────────────────────────

def step_ollama():
    """Check Ollama is installed and a model is available."""
    _section("Local AI (Ollama)")

    if not shutil.which("ollama"):
        print(f"  {YELLOW}Ollama is not installed.{NC}")
        print(f"  Install it from: {BOLD}https://ollama.ai{NC}")
        print(f"  Then run: {BOLD}ollama pull qwen2.5:7b{NC}")
        return

    # Check if Ollama is running
    import httpx
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        print(f"  {YELLOW}Ollama is installed but not running.{NC}")
        print(f"  Start it with: {BOLD}ollama serve{NC}")
        if _ask_yn("Start Ollama now?"):
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            import time
            time.sleep(3)
            try:
                resp = httpx.get("http://localhost:11434/api/tags", timeout=5)
                models = [m["name"] for m in resp.json().get("models", [])]
                _ok("Ollama started")
            except Exception:
                print(f"  {RED}Could not start Ollama. Please start it manually.{NC}")
                return
        else:
            return

    if models:
        _ok(f"Ollama running with models: {', '.join(models[:5])}")
    else:
        print(f"  No models found. Pulling default model...")
        if _ask_yn("Download qwen2.5:7b (~4.7 GB)?"):
            subprocess.run(["ollama", "pull", "qwen2.5:7b"])
            _ok("Model downloaded")
        else:
            _skip("No model pulled")


def step_telegram():
    """Configure Telegram bot."""
    _section("Telegram Bot")

    print(f"  Winston can chat via Telegram. You need a bot token from @BotFather.\n")
    print(f"  {DIM}How to get one:{NC}")
    print(f"  {DIM}1. Open Telegram → search @BotFather → /newbot{NC}")
    print(f"  {DIM}2. Give it a name (e.g. 'My Winston'){NC}")
    print(f"  {DIM}3. Copy the token it gives you{NC}\n")

    token = _ask("Telegram bot token (or Enter to skip)")
    if token and len(token) > 20:
        _write_env("WINSTON_TELEGRAM_TOKEN", token)
        _ok("Telegram token saved")
    else:
        _skip("Telegram")


def step_cloud_llm():
    """Configure optional cloud LLM providers."""
    _section("Cloud LLM Providers (optional)")

    print(f"  Winston works fully offline with Ollama.")
    print(f"  But you can add cloud providers for smarter models.\n")

    providers = [
        ("OPENAI_API_KEY", "OpenAI (GPT-5, GPT-4o)", "https://platform.openai.com/api-keys"),
        ("ANTHROPIC_API_KEY", "Anthropic (Claude)", "https://console.anthropic.com/"),
        ("GOOGLE_API_KEY", "Google (Gemini)", "https://aistudio.google.com/apikey"),
    ]

    if not _ask_yn("Configure a cloud LLM provider?", default=False):
        _skip("Cloud providers")
        return

    for env_key, name, url in providers:
        key = _ask(f"{name} API key (or Enter to skip)")
        if key and len(key) > 10:
            _write_env(env_key, key)
            _ok(f"{name} configured")
        else:
            _skip(name)


def step_tts():
    """Configure text-to-speech."""
    _section("Voice (Text-to-Speech)")

    print(f"  Winston can speak responses aloud.\n")
    print(f"  Options:")
    print(f"    1. {BOLD}macOS say{NC} — Built-in, always works, robotic voice")
    print(f"    2. {BOLD}Piper{NC}    — Free, local, natural ({DIM}brew install piper-tts{NC})")
    print(f"    3. {BOLD}ElevenLabs{NC} — Best quality, cloud, needs API key\n")

    if shutil.which("piper"):
        _ok("Piper TTS is installed (will be used automatically)")
    else:
        if _ask_yn("Install Piper TTS for natural-sounding voice?", default=False):
            if sys.platform == "darwin":
                subprocess.run(["brew", "install", "piper-tts"])
                _ok("Piper installed")
            else:
                print(f"  Install via: pip install piper-tts")
        else:
            _ok("Using macOS say (always available)")

    el_key = _ask("ElevenLabs API key for premium voice (or Enter to skip)")
    if el_key and len(el_key) > 10:
        _write_env("WINSTON_ELEVENLABS_KEY", el_key)
        _ok("ElevenLabs configured")


def step_name():
    """Let user customize Winston's name."""
    _section("Personalisation")

    name = _ask("What should the AI call you?", default="Sir")
    if name:
        # Update settings.yaml
        config_path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
        if config_path.exists():
            content = config_path.read_text()
            content = content.replace("user_name: Sir", f"user_name: {name}")
            config_path.write_text(content)
            _ok(f"Winston will call you '{name}'")


def step_mode():
    """Ask which mode to default to."""
    _section("Default Mode")

    print(f"  How do you want to use Winston?\n")
    print(f"    1. {BOLD}text{NC}   — CLI-only (simplest)")
    print(f"    2. {BOLD}server{NC} — Web UI + Telegram + Discord (recommended)")
    print(f"    3. {BOLD}voice{NC}  — Microphone + Speaker")
    print(f"    4. {BOLD}hybrid{NC} — Text + Voice combined\n")

    choice = _ask("Choose mode (1-4)", "2")
    mode_map = {"1": "text", "2": "server", "3": "voice", "4": "hybrid"}
    mode = mode_map.get(choice, "server")
    _ok(f"Default mode: {mode}")
    return mode


# ──────────────────────────────────────────────────────
# Main wizard
# ──────────────────────────────────────────────────────

def run_wizard() -> str:
    """Run the interactive setup wizard. Returns the chosen default mode."""
    print(f"\n{BOLD}╔══════════════════════════════════════════╗{NC}")
    print(f"{BOLD}║      W.I.N.S.T.O.N.   Setup Wizard       ║{NC}")
    print(f"{BOLD}╚══════════════════════════════════════════╝{NC}")
    print(f"\n  This wizard helps you configure Winston in ~2 minutes.")
    print(f"  Press Enter to accept defaults or skip any step.\n")

    # Ensure .winston directory exists
    WINSTON_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure .env exists
    if not ENV_FILE.exists():
        env_example = ENV_FILE.parent / ".env.example"
        if env_example.exists():
            shutil.copy(env_example, ENV_FILE)
        else:
            ENV_FILE.touch()

    step_ollama()
    step_telegram()
    step_cloud_llm()
    step_tts()
    step_name()
    mode = step_mode()

    # Mark setup as done
    (WINSTON_DIR / ".setup_done").touch()

    print(f"\n{GREEN}╔══════════════════════════════════════════╗{NC}")
    print(f"{GREEN}║        Setup complete! 🎉                ║{NC}")
    print(f"{GREEN}╚══════════════════════════════════════════╝{NC}")
    print(f"\n  Start Winston with:")
    print(f"    {BLUE}winston{NC}                — CLI chat")
    print(f"    {BLUE}winston --mode server{NC}  — Web UI + Telegram")
    print(f"\n  Re-run this wizard anytime with:")
    print(f"    {BLUE}winston --setup{NC}\n")

    return mode


def should_show_wizard() -> bool:
    """Check if this is the first run (no .setup_done marker)."""
    return not (WINSTON_DIR / ".setup_done").exists()
