#!/bin/bash
# HandsFree Studio: set up (the first time) and start, on macOS.
#   bash run_mac.command                      head + mouth + voice
#   bash run_mac.command --eyes               start in play mode (eye pointer)
#   bash run_mac.command --mouse --dry-run    mouse only, no FL Studio needed
#   bash run_mac.command --project "my song"  Travis keeps a memory per project
#   bash run_mac.command --browser            a browser window instead of the
#                                             app's own window
#   bash run_mac.command --check              run the checks and stop
cd "$(dirname "$0")" || exit 1

PY=python3.12
if ! command -v $PY >/dev/null 2>&1; then
  echo "Python 3.12 not found. Install it with:  brew install python@3.12"
  exit 1
fi
FIRST=0
if [ ! -x .venv/bin/python ] || ! .venv/bin/python --version | grep -q "3.12"; then
  echo "Creating the Python 3.12 environment (first run only)..."
  rm -rf .venv
  $PY -m venv .venv || exit 1
  FIRST=1
fi

# Install packages only when requirements.txt has changed since last time.
REQ_HASH="$(shasum requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.requirements-hash 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "Installing packages (slow the first time)..."
  .venv/bin/pip install -q -r requirements.txt || exit 1
  echo "$REQ_HASH" > .venv/.requirements-hash
fi

# FL Studio loads its controller script from here.
FL_DIR="$HOME/Documents/Image-Line/FL Studio/Settings/Hardware/HandsFree Studio"
if [ -d "$HOME/Documents/Image-Line/FL Studio" ]; then
  mkdir -p "$FL_DIR"
  cp fl_script/device_HandsFreeStudio.py "$FL_DIR/"
fi

# Checks: on the first run, or when asked. They need no camera, mic or FL.
if [ "$FIRST" = 1 ] || [ "$1" = "--check" ]; then
  echo "Running checks..."
  .venv/bin/python tests/run_all.py || exit 1
  [ "$1" = "--check" ] && exit 0
fi

# Gemini key: put  GEMINI_API_KEY=your-key  in a file named .env in this
# folder (see .env.example) and it is loaded here every time.
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
if [ -n "$GEMINI_API_KEY" ]; then
  echo "Gemini: ON (key found). Travis and Beat will use Gemini."
else
  echo "Gemini: OFF (no GEMINI_API_KEY). Travis and Beat use the built-in offline rules."
fi

echo "Starting HandsFree Studio. Allow camera and microphone access if macOS asks."
if [ "$1" = "--browser" ]; then
  shift
  .venv/bin/python app.py "$@"
else
  .venv/bin/python desktop.py "$@"       # its own window; falls back by itself
fi
