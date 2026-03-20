#!/usr/bin/env bash
# publish_tap.sh — Create/update the Homebrew tap for Winston
# Usage: ./homebrew/publish_tap.sh [--create | --update] [--version VERSION]
#
# This script:
#   --create   Creates the serhatbilge/homebrew-tap repo on GitHub, pushes formula
#   --update   Updates the formula in an existing tap with a new version/SHA256
#   --version  Version tag to use (default: latest git tag)
#
# Prerequisites:
#   - GitHub CLI: brew install gh
#   - Authenticated: gh auth login

set -euo pipefail

GITHUB_USER="serhatbilge"
TAP_REPO="homebrew-tap"
PROJECT_REPO="W.I.N.S.T.O.N..S"
FORMULA_SRC="$(dirname "$0")/Formula/winston.rb"
TAP_DIR="${TMPDIR:-/tmp}/homebrew-tap-$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Parse arguments
ACTION=""
VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --create)  ACTION="create"; shift ;;
    --update)  ACTION="update"; shift ;;
    --version) VERSION="$2"; shift 2 ;;
    *)         error "Unknown argument: $1" ;;
  esac
done

[[ -z "$ACTION" ]] && error "Specify --create or --update"

# Check prerequisites
command -v gh >/dev/null 2>&1 || error "GitHub CLI required: brew install gh"
gh auth status >/dev/null 2>&1 || error "Not authenticated: run 'gh auth login'"
[[ -f "$FORMULA_SRC" ]] || error "Formula not found at $FORMULA_SRC"

# Get version from latest git tag if not specified
if [[ -z "$VERSION" ]]; then
  VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
  [[ -z "$VERSION" ]] && error "No version specified and no git tags found. Use --version v1.0.0"
fi

info "Version: $VERSION"

# Calculate SHA256 of the release tarball
TARBALL_URL="https://github.com/${GITHUB_USER}/${PROJECT_REPO}/archive/refs/tags/${VERSION}.tar.gz"
info "Downloading tarball to compute SHA256..."
SHA256=$(curl -sL "$TARBALL_URL" | shasum -a 256 | awk '{print $1}')

if [[ "$SHA256" == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" ]]; then
  error "Empty tarball — tag $VERSION may not exist on GitHub. Create it first:\n  git tag $VERSION && git push origin $VERSION"
fi

info "SHA256: $SHA256"

# Prepare formula with real version and SHA
FORMULA_CONTENT=$(sed \
  -e "s|url \".*tar.gz\"|url \"${TARBALL_URL}\"|" \
  -e "s|sha256 \".*\"|sha256 \"${SHA256}\"|" \
  "$FORMULA_SRC")

if [[ "$ACTION" == "create" ]]; then
  info "Creating tap repo: ${GITHUB_USER}/${TAP_REPO}"
  
  # Create the repo on GitHub
  gh repo create "${GITHUB_USER}/${TAP_REPO}" \
    --public \
    --description "Homebrew tap for W.I.N.S.T.O.N. AI assistant" \
    --clone \
    --disable-issues \
    --disable-wiki 2>/dev/null && info "Repo created" || warn "Repo may already exist"

  # Clone and set up
  rm -rf "$TAP_DIR"
  gh repo clone "${GITHUB_USER}/${TAP_REPO}" "$TAP_DIR"
  mkdir -p "${TAP_DIR}/Formula"
  
  echo "$FORMULA_CONTENT" > "${TAP_DIR}/Formula/winston.rb"
  
  # Create a README for the tap
  cat > "${TAP_DIR}/README.md" <<'TAPREADME'
# Homebrew Tap — W.I.N.S.T.O.N.

AI assistant — like Jarvis, but open-source. Runs locally with full privacy.

## Install

```bash
brew install serhatbilge/tap/winston
```

Or step by step:

```bash
brew tap serhatbilge/tap
brew install winston
```

## After Install

```bash
# Install Ollama for local AI
brew install ollama
ollama serve &
ollama pull qwen2.5:7b

# Launch Winston
winston

# Or run setup wizard
winston --setup
```

## Update

```bash
brew update && brew upgrade winston
```
TAPREADME

  cd "$TAP_DIR"
  git add -A
  git commit -m "Add Winston formula ${VERSION}"
  git push origin main
  
  info "✅ Tap published!"
  info ""
  info "Users can now install with:"
  info "  brew install ${GITHUB_USER}/tap/winston"

elif [[ "$ACTION" == "update" ]]; then
  info "Updating formula in existing tap..."
  
  TAP_LOCAL="$(brew --repository "${GITHUB_USER}/tap" 2>/dev/null || echo "")"
  
  if [[ -z "$TAP_LOCAL" || ! -d "$TAP_LOCAL" ]]; then
    # Clone fresh
    rm -rf "$TAP_DIR"
    gh repo clone "${GITHUB_USER}/${TAP_REPO}" "$TAP_DIR"
    TAP_LOCAL="$TAP_DIR"
  fi
  
  mkdir -p "${TAP_LOCAL}/Formula"
  echo "$FORMULA_CONTENT" > "${TAP_LOCAL}/Formula/winston.rb"
  
  cd "$TAP_LOCAL"
  git add -A
  git commit -m "Update Winston to ${VERSION}"
  git push origin main
  
  info "✅ Formula updated to ${VERSION}"
  info "Users: brew update && brew upgrade winston"
fi

# Cleanup
rm -rf "$TAP_DIR" 2>/dev/null || true

info "Done."
