#!/bin/bash
# HandsFree Studio starting in play mode: look at a pad and raise your
# eyebrows to play it. The first time, follow a dot across the screen.
cd "$(dirname "$0")" || exit 1
bash run_mac.command --eyes
