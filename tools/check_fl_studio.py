"""
Phase 1 check: does Python control FL Studio?

    python tools/check_fl_studio.py            # real test, FL Studio open
    python tools/check_fl_studio.py --dry-run  # just print the MIDI messages

Watch FL Studio while it runs. Each step says what you should see or hear.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import time

from bridge import FLBridge, scale_notes, note_name


def step(text):
    print("\n>>", text)


def main():
    dry = "--dry-run" in sys.argv
    fl = FLBridge(dry_run=dry)
    pause = 0 if dry else 1.5

    step("PING - hint bar (top left of FL Studio) should say 'HandsFree: connected'")
    fl.ping()
    time.sleep(pause)

    step("NOTES - you should hear a C minor scale on the selected channel")
    for note in scale_notes("C", "minor", octave=4, count=8):
        print("  ", note_name(note))
        fl.play_note(note, velocity=100, seconds=0 if dry else 0.25)
    time.sleep(pause)

    step("TEMPO - tempo display should change to 90")
    fl.set_tempo(90)
    time.sleep(pause)

    step("DRUMS - channel 1 gets steps 1, 5, 9, 13 (four on the floor)")
    fl.set_pattern(0, [0, 4, 8, 12])
    time.sleep(pause)

    step("DRUMS - channel 2 gets steps 5 and 13 (backbeat)")
    fl.set_pattern(1, [4, 12])
    time.sleep(pause)

    step("PLAY - playback should start")
    fl.play()
    time.sleep(0 if dry else 6)

    step("VOLUME - selected channel gets quieter, then louder")
    fl.volume_down()
    fl.volume_down()
    time.sleep(pause)
    fl.volume_up()
    fl.volume_up()
    time.sleep(pause)

    step("STOP - playback should stop")
    fl.stop()
    fl.close()
    print("\nDone. If every step happened, Phase 1 works.")


if __name__ == "__main__":
    main()
