#!/bin/bash

# W.I.N.S.T.O.N. Automated Setup Script for macOS
# This script installs all required dependencies for W.I.N.S.T.O.N.,
# including the complex audio drivers needed for Observer Mode.

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=======================================${NC}"
echo -e "${BLUE}  W.I.N.S.T.O.N. Automated MacOS Setup  ${NC}"
echo -e "${BLUE}=======================================${NC}"

# 1. Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo -e "${YELLOW}Homebrew not found. Installing Homebrew...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    echo -e "${GREEN}✓ Homebrew is already installed.${NC}"
fi

# 2. Install System Dependencies
echo -e "\n${BLUE}[1/4] Installing system dependencies...${NC}"
brew install \
    python@3.12 \
    portaudio \
    ffmpeg \
    piper-tts
echo -e "${GREEN}✓ System dependencies installed.${NC}"


# 3. Install BlackHole for Observer Mode (Requires Sudo)
echo -e "\n${BLUE}[2/4] Installing BlackHole Virtual Audio Driver (for Observer Mode)...${NC}"
echo -e "${YELLOW}Note: You may be prompted for your Mac password to install the audio driver.${NC}"
if ! brew list --cask blackhole-2ch &> /dev/null; then
    brew install --cask blackhole-2ch
    echo -e "${GREEN}✓ BlackHole installed successfully.${NC}"
else
    echo -e "${GREEN}✓ BlackHole is already installed.${NC}"
fi


# 4. Setup Python Virtual Environment
echo -e "\n${BLUE}[3/4] Creating Python Virtual Environment...${NC}"
cd "$(dirname "$0")"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated.${NC}"


# 5. Install Python Dependencies
echo -e "\n${BLUE}[4/4] Installing Python requirements...${NC}"
pip install --upgrade pip

# Install required packages directly
pip install -r requirements.txt

echo -e "\n${GREEN}=======================================${NC}"
echo -e "${GREEN}  Setup Complete! W.I.N.S.T.O.N. ready.  ${NC}"
echo -e "${GREEN}=======================================${NC}"
echo -e "\n${YELLOW}Important Audio Setup for Observer Mode:${NC}"
echo "To allow W.I.N.S.T.O.N. to hear Zoom/Teams meetings:"
echo "1. Open 'Audio MIDI Setup' (Cmd+Space -> Audio MIDI Setup)"
echo "2. Click the '+' button -> 'Create Multi-Output Device'"
echo "3. Check both your Headphones/Speakers AND 'BlackHole 2ch'"
echo "4. Set this Multi-Output Device as your Mac's output in Sound Preferences"
echo -e "\nTo start WINSTON, run:"
echo -e "${BLUE}source venv/bin/activate && python -m winston.main --mode hybrid${NC}"
