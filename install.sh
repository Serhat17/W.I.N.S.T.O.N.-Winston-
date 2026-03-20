#!/bin/bash
# ╔══════════════════════════════════════════════════╗
# ║   W.I.N.S.T.O.N. — One-Line Installer            ║
# ║   curl -fsSL https://raw.githubusercontent.com/   ║
# ║   serhatbilge/W.I.N.S.T.O.N..S/main/install.sh   ║
# ║   | bash                                          ║
# ╚══════════════════════════════════════════════════╝
#
# What this does:
#   1. Installs Homebrew (macOS) or apt/dnf deps (Linux)
#   2. Installs Python 3.12 + portaudio + ffmpeg + git
#   3. Installs Ollama and pulls a default model
#   4. Clones the repo (or updates if exists)
#   5. Creates venv + installs Python deps
#   6. Launches the interactive setup wizard
#
# Supports: macOS (arm64 + x86_64), Linux (Ubuntu/Debian/Fedora)

set -euo pipefail

# ── Colors ────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}  ✓${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Pre-flight checks ────────────────────────────────
# Verify we have internet
if ! curl -sf --max-time 5 https://github.com >/dev/null 2>&1; then
    fail "No internet connection. Please check your network and try again."
fi

# ── Config ────────────────────────────────────────────
REPO_URL="https://github.com/serhatbilge/W.I.N.S.T.O.N..S.git"
INSTALL_DIR="$HOME/Winston"
DEFAULT_MODEL="qwen2.5:7b"
PYTHON_MIN_VERSION="3.9"

# ── Detect OS ─────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║      W.I.N.S.T.O.N.  Installer           ║${NC}"
echo -e "${BOLD}║  Your AI Assistant — Private & Local      ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: System package manager ────────────────────
info "Step 1/6: Checking system dependencies..."

if [[ "$OS" == "Darwin" ]]; then
    # macOS — need Homebrew
    if ! command -v brew &>/dev/null; then
        info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add to PATH for Apple Silicon
        if [[ "$ARCH" == "arm64" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
        # Verify Homebrew works
        command -v brew &>/dev/null || fail "Homebrew installation failed. Please install manually: https://brew.sh"
    fi
    ok "Homebrew ready"

    # Ensure git is available (Xcode CLI tools)
    if ! command -v git &>/dev/null; then
        info "Installing Xcode Command Line Tools (for git)..."
        xcode-select --install 2>/dev/null || true
        # Wait for it
        until command -v git &>/dev/null; do
            sleep 5
        done
    fi
    ok "git available"

    # Install system deps (skip if already present)
    BREW_DEPS=(python@3.12 portaudio ffmpeg)
    for dep in "${BREW_DEPS[@]}"; do
        if ! brew list "$dep" &>/dev/null; then
            info "Installing $dep..."
            brew install "$dep"
        fi
        ok "$dep"
    done

elif [[ "$OS" == "Linux" ]]; then
    if ! command -v git &>/dev/null || ! command -v curl &>/dev/null; then
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq
            sudo apt-get install -y -qq git curl
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y git curl
        fi
    fi
    ok "git + curl available"

    if command -v apt-get &>/dev/null; then
        info "Installing system dependencies via apt..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3 python3-venv python3-pip portaudio19-dev ffmpeg git curl
        ok "System dependencies installed"
    elif command -v dnf &>/dev/null; then
        info "Installing system dependencies via dnf..."
        sudo dnf install -y python3 python3-pip python3-venv portaudio-devel ffmpeg git curl
        ok "System dependencies installed"
    else
        warn "Unsupported package manager. Please install Python 3.9+, git, ffmpeg, and portaudio manually."
    fi
else
    fail "Unsupported OS: $OS. Winston supports macOS and Linux."
fi

# ── Step 2: Python version check ─────────────────────
info "Step 2/6: Checking Python..."

PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$("$cmd" -c "import sys; print(sys.version_info.major)")
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)")
        if [[ "$major" -ge 3 ]] && [[ "$minor" -ge 9 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    fail "Python $PYTHON_MIN_VERSION+ is required but not found. Install it with: brew install python@3.12"
fi
ok "Python $version ($PYTHON_CMD)"

# ── Step 3: Ollama ────────────────────────────────────
info "Step 3/6: Setting up Ollama (local AI)..."

if ! command -v ollama &>/dev/null; then
    if [[ "$OS" == "Darwin" ]]; then
        info "Installing Ollama via Homebrew..."
        brew install --cask ollama 2>/dev/null || brew install ollama
    else
        info "Installing Ollama..."
        curl -fsSL https://ollama.ai/install.sh | sh
    fi
fi

if ! command -v ollama &>/dev/null; then
    warn "Ollama installation could not be verified. You can install it later from https://ollama.ai"
    warn "Skipping model download."
else
    ok "Ollama installed"

    # Start Ollama if not running
    if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
        info "Starting Ollama..."
        if [[ "$OS" == "Darwin" ]]; then
            open -a Ollama 2>/dev/null || (ollama serve &>/dev/null &)
        else
            ollama serve &>/dev/null &
        fi
        # Wait for it to be ready (max 30s)
        OLLAMA_READY=false
        for i in {1..30}; do
            if curl -sf http://localhost:11434/api/tags &>/dev/null; then
                OLLAMA_READY=true
                break
            fi
            sleep 1
        done
        if [[ "$OLLAMA_READY" == "false" ]]; then
            warn "Ollama did not start within 30s. You can start it manually: ollama serve"
        fi
    fi

    if curl -sf http://localhost:11434/api/tags &>/dev/null; then
        ok "Ollama is running"

        # Pull default model if not present
        if ! ollama list 2>/dev/null | grep -q "$DEFAULT_MODEL"; then
            info "Downloading AI model ($DEFAULT_MODEL) — this may take a few minutes (~4 GB)..."
            ollama pull "$DEFAULT_MODEL"
        fi
        ok "Model $DEFAULT_MODEL ready"
    else
        warn "Ollama not running. After install, start it and run: ollama pull $DEFAULT_MODEL"
    fi
fi

# ── Step 4: Clone / update repo ──────────────────────
info "Step 4/6: Setting up Winston..."

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null || warn "Could not auto-update (local changes?). Continuing with existing version..."
elif [[ -d "$INSTALL_DIR" ]]; then
    # Directory exists but is not a git repo — back it up
    warn "$INSTALL_DIR exists but is not a Winston installation."
    BACKUP_DIR="${INSTALL_DIR}.backup.$(date +%s)"
    info "Backing up to $BACKUP_DIR..."
    mv "$INSTALL_DIR" "$BACKUP_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
else
    info "Downloading Winston to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
ok "Source code ready"

# ── Step 5: Python venv + dependencies ────────────────
info "Step 5/6: Installing Python packages..."

if [[ ! -d "venv" ]]; then
    "$PYTHON_CMD" -m venv venv
fi
source venv/bin/activate

info "Upgrading pip..."
pip install --upgrade pip -q 2>&1 | tail -1

info "Installing dependencies (this may take 1-2 minutes)..."
pip install -r requirements.txt 2>&1 | tail -3
if [[ $? -ne 0 ]]; then
    fail "pip install failed. Check requirements.txt for issues."
fi

# Install Playwright browsers for web automation
if python -c "import playwright" 2>/dev/null; then
    info "Installing Playwright browser (Chromium)..."
    python -m playwright install chromium 2>/dev/null && ok "Playwright Chromium ready" || warn "Playwright browser install failed (optional — web browsing won't work)"
fi
ok "Python environment ready"

# ── Create .env if missing ────────────────────────────
if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        cp .env.example .env
        ok "Created .env from template"
    else
        touch .env
    fi
fi

# ── Create 'winston' command in PATH ──────────────────
LAUNCHER="/usr/local/bin/winston"
info "Installing 'winston' command..."

LAUNCHER_CONTENT="#!/bin/bash
# W.I.N.S.T.O.N. AI Assistant launcher
# Installed by install.sh — edit WINSTON_HOME to change location
WINSTON_HOME=\"${INSTALL_DIR}\"

if [[ ! -d \"\$WINSTON_HOME/venv\" ]]; then
    echo \"Error: Winston not found at \$WINSTON_HOME\" >&2
    echo \"Re-install: curl -fsSL https://raw.githubusercontent.com/serhatbilge/W.I.N.S.T.O.N..S/main/install.sh | bash\" >&2
    exit 1
fi

export PYTHONPATH=\"\$WINSTON_HOME:\$PYTHONPATH\"
exec \"\$WINSTON_HOME/venv/bin/python\" -m winston.main \"\$@\"
"

if [[ -w "$(dirname "$LAUNCHER")" ]]; then
    echo "$LAUNCHER_CONTENT" > "$LAUNCHER"
    chmod +x "$LAUNCHER"
    ok "'winston' command installed to $LAUNCHER"
else
    echo "$LAUNCHER_CONTENT" | sudo tee "$LAUNCHER" >/dev/null
    sudo chmod +x "$LAUNCHER"
    ok "'winston' command installed to $LAUNCHER (via sudo)"
fi

# Clean up old alias from shell rc (if it exists from a previous install)
SHELL_RC=""
if [[ -f "$HOME/.zshrc" ]]; then
    SHELL_RC="$HOME/.zshrc"
elif [[ -f "$HOME/.bashrc" ]]; then
    SHELL_RC="$HOME/.bashrc"
fi
if [[ -n "$SHELL_RC" ]] && grep -qF "alias winston=" "$SHELL_RC"; then
    sed -i'' -e '/# W\.I\.N\.S\.T\.O\.N\. AI Assistant/d' -e '/alias winston=/d' "$SHELL_RC"
    ok "Removed old shell alias from $(basename "$SHELL_RC") (replaced by $LAUNCHER)"
fi

# ── Step 6: Setup wizard ─────────────────────────────
info "Step 6/6: Running setup wizard..."
echo ""
python -m winston.main --setup || warn "Setup wizard skipped or failed — you can run it later with: winston --setup"
echo ""

# ── Done! ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║    Installation complete!                 ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}To start Winston:${NC}"
echo ""
echo -e "    ${BLUE}cd $INSTALL_DIR${NC}"
echo -e "    ${BLUE}source venv/bin/activate${NC}"
echo -e "    ${BLUE}python -m winston.main${NC}"
echo ""
echo -e "  Or just type ${BOLD}winston${NC} (after restarting your terminal)"
echo ""
echo -e "  ${BOLD}Optional — run the setup wizard:${NC}"
echo -e "    ${BLUE}python -m winston.main --setup${NC}"
echo ""
echo -e "  This lets you connect Telegram, add API keys, etc."
echo ""
echo -e "  ${BOLD}Modes:${NC}"
echo -e "    winston              → CLI chat"
echo -e "    winston --mode server → Web UI + Telegram + Discord"
echo -e "    winston --mode voice  → Microphone + Speaker"
echo ""
