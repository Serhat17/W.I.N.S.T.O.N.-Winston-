"""
Utility helpers for W.I.N.S.T.O.N.
Terminal UI, formatting, and logging setup.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# ANSI color codes for terminal UI
COLORS = {
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
    "CYAN": "\033[36m",
    "BLUE": "\033[34m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "RED": "\033[31m",
    "MAGENTA": "\033[35m",
    "WHITE": "\033[97m",
}


def setup_logging(log_file: str = None, debug: bool = False):
    """Set up logging for W.I.N.S.T.O.N."""
    log_level = logging.DEBUG if debug else logging.INFO

    # Create log directory
    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        log_path = Path("~/.winston/winston.log").expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure logging
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_path)),
            logging.StreamHandler(sys.stdout) if debug else logging.NullHandler(),
        ],
    )

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)


def print_banner():
    """Print the W.I.N.S.T.O.N. startup banner."""
    banner = f"""
{COLORS['CYAN']}{COLORS['BOLD']}
██╗    ██╗██╗███╗   ██╗███████╗████████╗ ██████╗ ███╗   ██╗
██║    ██║██║████╗  ██║██╔════╝╚══██╔══╝██╔═══██╗████╗  ██║
██║ █╗ ██║██║██╔██╗ ██║███████╗   ██║   ██║   ██║██╔██╗ ██║
██║███╗██║██║██║╚██╗██║╚════██║   ██║   ██║   ██║██║╚██╗██║
╚███╔███╔╝██║██║ ╚████║███████║   ██║   ╚██████╔╝██║ ╚████║
 ╚══╝╚══╝ ╚═╝╚═╝  ╚═══╝╚══════╝   ╚═╝    ╚═════╝ ╚═╝  ╚═══╝
{COLORS['RESET']}
{COLORS['DIM']}  Wildly Intelligent Network System for Task Operations and Navigation{COLORS['RESET']}
{COLORS['DIM']}  ────────────────────────────────────────────────────────────────────────{COLORS['RESET']}
"""
    print(banner)


def print_winston(message: str):
    """Print a message from W.I.N.S.T.O.N."""
    print(f"\n  {COLORS['CYAN']}{COLORS['BOLD']}W.I.N.S.T.O.N.:{COLORS['RESET']} {message}")


def print_user(message: str):
    """Print a user message."""
    print(f"\n  {COLORS['GREEN']}{COLORS['BOLD']}You:{COLORS['RESET']} {message}")


def print_system(message: str):
    """Print a system/status message."""
    print(f"  {COLORS['DIM']}[{message}]{COLORS['RESET']}")


def print_error(message: str):
    """Print an error message."""
    print(f"  {COLORS['RED']}✗ {message}{COLORS['RESET']}")


def print_success(message: str):
    """Print a success message."""
    print(f"  {COLORS['GREEN']}✓ {message}{COLORS['RESET']}")


def print_skill_result(skill_name: str, message: str, success: bool):
    """Print a skill execution result."""
    icon = f"{COLORS['GREEN']}✓" if success else f"{COLORS['RED']}✗"
    print(f"\n  {icon} [{skill_name}]{COLORS['RESET']} {message}")


def get_greeting() -> str:
    """Get a time-appropriate greeting."""
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    elif hour < 21:
        return "Good evening"
    else:
        return "Good evening"
