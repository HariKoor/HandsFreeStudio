#!/bin/bash
# Try the app without the camera or FL Studio: the mouse is your head,
# clicking is opening your mouth, and MIDI is printed instead of sent.
cd "$(dirname "$0")" || exit 1
bash run_mac.command --mouse --dry-run
