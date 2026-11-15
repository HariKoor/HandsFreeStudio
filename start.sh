#!/bin/bash
# HandsFree Studio: one command to install everything and run it (macOS).
#   ./start.sh            install what's missing, then start
#   ./start.sh --mouse --dry-run    try it without a camera or FL Studio
# Any option is passed on to run_mac.command (see README).
cd "$(dirname "$0")" || exit 1

if [ "$(uname)" != "Darwin" ]; then
  echo "HandsFree Studio runs on macOS only."; exit 1
fi

# 1. Python 3.12 (through Homebrew if it is missing)
if ! command -v python3.12 >/dev/null 2>&1; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is needed to install Python 3.12. Install it from https://brew.sh, then run ./start.sh again."
    exit 1
  fi
  echo "Installing Python 3.12 with Homebrew..."
  brew install python@3.12 || exit 1
fi

# 2. API key file (optional; without a key Travis uses offline rules)
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env. Put your Gemini key in it to turn Travis's AI on (optional)."
fi

# 3. Virtual environment, packages, FL Studio script, checks, then start
exec bash run_mac.command "$@"
