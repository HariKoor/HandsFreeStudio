"""
HandsFree Studio - Python side of the FL Studio bridge.

Everything else in the project (face tracker, voice, UI) talks to FL Studio
only through the FLBridge class in this file.

    from bridge import FLBridge
    fl = FLBridge()
    fl.play()
    fl.note_on(60, 100); fl.note_off(60)

Protocol (must match fl_script/device_HandsFreeStudio.py):
  notes    -> MIDI channel 1, played by FL's selected channel
  commands -> Control Change on MIDI channel 16
"""

import sys
import threading
import time

import mido

PORT_HINT = "HandsFree"
NOTE_CHANNEL = 0
CMD_CHANNEL = 15

CMD_PLAY = 1
CMD_STOP = 2
CMD_RECORD = 3
CMD_TEMPO = 4
CMD_SELECT_CHANNEL = 5
CMD_NEXT_CHANNEL = 6
CMD_PREV_CHANNEL = 7
CMD_VOLUME_SET = 8
CMD_VOLUME_UP = 9
CMD_VOLUME_DOWN = 10
CMD_MUTE = 11
CMD_STEP_TOGGLE = 12
CMD_STEP_SET = 13
CMD_STEP_CLEAR = 14
CMD_CLEAR_STEPS = 15
CMD_UNDO = 16
CMD_NEXT_PATTERN = 17
CMD_PREV_PATTERN = 18
CMD_LOOP_MODE = 19
CMD_CLEAR_NOTES = 20    # value = channel rack index whose notes go
CMD_CLEAR_NOTES_GO = 21 # step 2 of clear notes: press Delete
CMD_DRUM_CLEAR = 22
CMD_DRUM_STEP = 40      # + channel index 0..7
CMD_DRUM_MUTE = 50
CMD_DRUM_VOLUME = 60
CMD_PING = 127

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10],
}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def scale_notes(root="C", scale="minor", octave=4, count=16):
    """MIDI note numbers for the pad grid. C4 = 60."""
    base = 12 * (octave + 1) + NOTE_NAMES.index(root.upper())
    steps = SCALES[scale]
    notes = []
    i = 0
    while len(notes) < count:
        note = base + 12 * (i // len(steps)) + steps[i % len(steps)]
        if note > 127:
            break
        notes.append(note)
        i += 1
    return notes


def note_name(note):
    return NOTE_NAMES[note % 12] + str(note // 12 - 1)


class FLBridge:
    def __init__(self, port_hint=PORT_HINT, dry_run=False):
        """dry_run=True prints messages instead of sending them (no FL needed)."""
        self.dry_run = dry_run
        self.port = None
        self.name = "dry run (no MIDI)"
        self._held = set()
        self._lock = threading.Lock()   # the hum thread also sends
        if dry_run:
            return
        names = mido.get_output_names()
        matches = [n for n in names if port_hint.lower() in n.lower()]
        if matches:
            self.port = mido.open_output(matches[0])
            self.name = matches[0]
            print("Connected to MIDI port:", matches[0])
        elif sys.platform != "win32":
            # macOS/Linux can create the port themselves.
            self.port = mido.open_output(port_hint, virtual=True)
            self.name = port_hint + " (virtual)"
            print("Created virtual MIDI port:", port_hint,
                  "(refresh the device list in FL Studio's MIDI settings)")
        else:
            raise RuntimeError(
                "No MIDI port containing '%s' found. Open loopMIDI and add a "
                "port named HandsFree. Ports seen: %s" % (port_hint, names))

    # ---- low level -------------------------------------------------------
    def _send(self, msg):
        with self._lock:
            if self.dry_run:
                print("  [dry run]", msg)
            else:
                self.port.send(msg)

    def _cmd(self, command, value=0):
        value = max(0, min(127, int(value)))
        self._send(mido.Message("control_change", channel=CMD_CHANNEL,
                                control=command, value=value))

    # ---- notes -----------------------------------------------------------
    def note_on(self, note, velocity=100):
        velocity = max(1, min(127, int(velocity)))
        self._send(mido.Message("note_on", channel=NOTE_CHANNEL,
                                note=int(note), velocity=velocity))
        self._held.add(int(note))

    def note_off(self, note):
        self._send(mido.Message("note_off", channel=NOTE_CHANNEL,
                                note=int(note), velocity=0))
        self._held.discard(int(note))

    def all_notes_off(self):
        for note in list(self._held):
            self.note_off(note)

    def play_note(self, note, velocity=100, seconds=0.3):
        """Blocking helper for tests. The app should use note_on/note_off."""
        self.note_on(note, velocity)
        time.sleep(seconds)
        self.note_off(note)

    # ---- transport -------------------------------------------------------
    def ping(self):
        self._cmd(CMD_PING)

    def play(self):
        self._cmd(CMD_PLAY)

    def stop(self):
        self._cmd(CMD_STOP)

    def toggle_record(self):
        self._cmd(CMD_RECORD)

    def set_tempo(self, bpm):
        """60..187 BPM."""
        self._cmd(CMD_TEMPO, int(bpm) - 60)

    def toggle_loop_mode(self):
        self._cmd(CMD_LOOP_MODE)

    def undo(self):
        self._cmd(CMD_UNDO)

    def clear_notes(self, channel):
        """Delete every note in that channel's piano roll (current pattern)."""
        # Two steps: open the piano roll, then press Delete once FL Studio
        # has had time to bring the window to the front.
        self._cmd(CMD_CLEAR_NOTES, channel)
        t = threading.Timer(0.5, self._cmd, args=(CMD_CLEAR_NOTES_GO,))
        t.daemon = True
        t.start()

    # ---- channel rack ----------------------------------------------------
    def select_channel(self, index):
        """index 0 = first channel in the channel rack."""
        self._cmd(CMD_SELECT_CHANNEL, index)

    def next_channel(self):
        self._cmd(CMD_NEXT_CHANNEL)

    def prev_channel(self):
        self._cmd(CMD_PREV_CHANNEL)

    def set_volume(self, fraction):
        """0.0..1.0 on the selected channel."""
        self._cmd(CMD_VOLUME_SET, round(fraction * 127))

    def volume_up(self):
        self._cmd(CMD_VOLUME_UP)

    def volume_down(self):
        self._cmd(CMD_VOLUME_DOWN)

    def toggle_mute(self):
        self._cmd(CMD_MUTE)

    # ---- step sequencer (selected channel) ---------------------------------
    def toggle_step(self, step):
        """step 0..15"""
        self._cmd(CMD_STEP_TOGGLE, step)

    def set_step(self, step, on=True):
        self._cmd(CMD_STEP_SET if on else CMD_STEP_CLEAR, step)

    def clear_steps(self):
        self._cmd(CMD_CLEAR_STEPS)

    def set_pattern(self, channel_index, steps_on):
        """Write a whole 16-step row, e.g. set_pattern(0, [0, 4, 8, 12])."""
        self.select_channel(channel_index)
        time.sleep(0.02)
        self.clear_steps()
        for step in steps_on:
            time.sleep(0.005)
            self.set_step(step, True)

    # ---- drum grid (names the channel directly, no selection needed) ----
    def drum_step(self, channel, step, on):
        self._cmd(CMD_DRUM_STEP + channel, step * 2 + (1 if on else 0))

    def drum_mute(self, channel, muted):
        self._cmd(CMD_DRUM_MUTE + channel, 1 if muted else 0)

    def drum_volume(self, channel, value):
        """value 0..127"""
        self._cmd(CMD_DRUM_VOLUME + channel, value)

    def clear_drums(self, channel_count):
        self._cmd(CMD_DRUM_CLEAR, channel_count)

    # ---- patterns --------------------------------------------------------
    def next_pattern(self):
        self._cmd(CMD_NEXT_PATTERN)

    def prev_pattern(self):
        self._cmd(CMD_PREV_PATTERN)

    def close(self):
        self.all_notes_off()
        if self.port is not None:
            self.port.close()
