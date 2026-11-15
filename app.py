"""
HandsFree Studio: make music in FL Studio with your head, eyes, face and
voice. This is the main loop: camera -> pointer and clicks -> commands ->
MIDI, with the window (ui_web.py + web/) drawing the result.

    python desktop.py          the normal start, in its own window
    python app.py              the same, in a browser window
    python app.py --mouse      mouse instead of the camera (testing)
    python app.py --dry-run    print MIDI instead of sending it
    python app.py --no-voice   microphone off
    python app.py --camera 1   pick a different camera
    python app.py --eyes       start in play mode (eye pointer)
    python app.py --project "my song"   Travis's memory file for this song
    python app.py --no-window  start the UI server only; open the printed URL

Pointing: navigate mode turns the head into the pointer and opening the
mouth clicks. Play mode (gaze.py) lets the eyes pick a pad's column and a
small nod its row; raising the eyebrows plays it. Voice: "Mozart" + a command
(studio.py), "Travis" + anything (travis.py). The keys in KEYS are for
helpers and testing; nothing needs the keyboard.
"""

import argparse
import collections
import json
import os
import threading
import time

from bridge import FLBridge, SCALES, NOTE_NAMES
from core import OneEuro, MouthTrigger, BrowTrigger, PadGrid
from hum import HumEngine
from studio import help_sheet, Studio, CHANNELS, VoiceActivity, start_audio
from project_memory import ProjectMemory
from travis import Travis, Sequencer, plan_start

W, H = 1280, 720                     # the window's canvas

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "settings.json")
DEFAULTS = {
    "range_yaw": 15.0,       # degrees of head turn from centre to screen edge
    "range_pitch": 10.0,
    "flip_x": False,
    "flip_y": False,
    "root": "C",
    "scale": "pentatonic_minor",
    "octave": 4,
    "cols": 4,
    "rows": 2,
    "preview": True,
    "tempo": 120,
    "melody_channel": 4,     # channel rack row the pads play (0 = first row)
    "voice_guard": True,
    "wake_word_on": True,    # app voice commands must start with the wake word
    "wake_word": "mozart",   # "Mozart play". Travis has its own wake word
    "voice_confidence": 0.85,
    "project": "default",    # Travis keeps a memory file per project
    "chords": False,         # each pad plays a three-note chord
    "dwell": False,          # click by holding the pointer still on a target
    "dwell_time": 1.0,       # seconds
    "hum_latency": 0.03,     # microphone delay in seconds
    "cursor": "solid",       # new UI: solid / hand / mouth
    "theme": "sunset",       # new UI: sunset / aurora / ultraviolet / mono
    "pointer": "head",       # head = navigate mode, eyes = play mode
    "brow_click": True,      # play mode: raising the eyebrows clicks
    "play_rows": 2,          # play mode: rows of pads (taller pads are
                             # easier for the eyes to pick; 0 = leave as is)
    "eye_calibration": "follow",   # follow a moving dot, or "dots" (12 dots)
    "play_rows_by": "head",  # play mode: the eyes pick the column and a small
                             # nod picks the row ("eyes" = eyes pick both)
    "play_nod_gain": 2.5,    # how far a nod moves, compared with navigate mode
    "eye_nudge": True,       # eye pointer: a head lean moves the highlight
}
CURSORS = ["solid", "hand", "mouth"]
THEMES = ["sunset", "aurora", "ultraviolet", "mono"]
DEGREES = ["1", "♭2", "2", "♭3", "3", "4", "♭5", "5", "♭6", "6",
           "♭7", "7"]
HEARD_SECONDS = 3.0      # how long the Mozart capsule shows a command
CALIBRATION_FRAMES = 30
MODES = ["pads", "drums", "hum", "mix"]
LOCK_MAX_AGE = 0.5      # seconds: how far back a click may reach for its position
LOCK_FALLBACK = 0.15    # used when the mouth was never fully closed

EYES_CLOSED = 0.6       # blink score above which the eyes count as shut
EYES_HOLD = 1.5         # seconds shut that asks for a recentre
DWELL_NOTE_LENGTH = 0.45  # seconds a dwell-clicked pad sounds

BUTTON_COMMANDS = {
    "PLAY": ("play",), "STOP": ("stop",), "REC": ("record",),
    "SLOWER": ("tempo_by", -5), "FASTER": ("tempo_by", 5),
    "CLEAR": ("clear",), "BEAT": ("beat", None), "MENU": ("menu",),
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


KEYS = {"w": ("toggle", "wake_word_on"), "tab": ("mode", None), "m": ("menu",), "0": ("reset",),
        "h": ("toggle", "chords"), ",": ("instrument", -1),
        ".": ("instrument", 1), "c": ("center",), "r": ("record",),
        "b": ("beat", None), "e": ("clear",), "s": ("scale",), "k": ("key",),
        "[": ("octave", -1), "]": ("octave", 1), "g": ("grid",),
        "-": ("range", 1), "=": ("range", -1), "x": ("toggle", "flip_x"),
        "y": ("toggle", "flip_y"), "v": ("toggle", "preview"),
        "n": ("toggle", "voice_guard"), "t": ("travis_demo",),
        "p": ("pointer", None), "i": ("gazecal",), "?": ("help", None),
        "/": ("help", None)}
TRAVIS_DEMO = "put a g minor chord for a quarter note followed by an f minor chord"


def key_command(key, playing):
    """The command for a helper key ("a", "tab", "esc", " " ...), "quit",
    or None."""
    if key in ("q", "esc"):
        return "quit"
    if key == " ":
        return ("stop",) if playing else ("play",)
    return KEYS.get(key)


def web_state(now, state, grid, studio, hover, cursor_px, level, dwell,
              message, s, fl, voice_on, mic_on, activity, tracker, st, hum,
              n_samples, travis=None, wake_word="mozart", eye=None, wake=None):
    """Everything the new UI draws, as plain JSON-able values."""
    root = NOTE_NAMES.index(grid.root)
    steps = SCALES.get(grid.scale, [])
    degrees, hues = [], []
    for note in grid.notes:
        pc = (note - root) % 12
        degrees.append(DEGREES[pc])
        hues.append(steps.index(pc) if pc in steps else 0)
    s16 = 15.0 / max(1, studio.tempo)
    heard = None
    if state["heard"] and now - state["heard"][1] < HEARD_SECONDS:
        heard = state["heard"][0]
    gazecal = bool(eye and eye.get("cal"))
    view = ("calibrate" if state["calibrating"] else "gazecal" if gazecal
            else "help" if state.get("help") else "menu" if state["menu"]
            else state["mode"])
    return {
        "view": view, "mode": state["mode"], "menu": state["menu"],
        "calib": n_samples / CALIBRATION_FRAMES if state["calibrating"] else 1,
        "eyes": state["eyes_long"], "message": message, "status": studio.status,
        "cursor": (None if state["calibrating"] or gazecal
                   or cursor_px is None
                   else [round(cursor_px[0], 1), round(cursor_px[1], 1)]),
        "hover": hover, "level": round(level, 3), "dwell": round(dwell, 3),
        "clicks": state["clicks"], "held": state["held"] is not None,
        "playing": studio.playing, "rec": state["rec"], "tempo": studio.tempo,
        "beat_pos": ((now - state["t_play"]) / s16
                     if studio.playing and state["t_play"] else None),
        "midi": fl.name, "voice": voice_on, "mic": mic_on,
        "speaking": activity.speaking(), "heard": heard,
        "camera": tracker is not None,
        "fps": round(tracker.fps) if tracker is not None else 0,
        "points": (list(st.points) if (st is not None and s["preview"]
                                       and st.found) else []),
        "pads": {"cols": grid.cols, "rows": grid.rows,
                 "labels": [grid.label(i) for i in range(len(grid.notes))],
                 "degrees": degrees, "hues": hues, "selected": grid.selected,
                 "playing": state["playing_index"], "root": grid.root,
                 "scale": grid.scale.replace("_", " "), "octave": grid.octave,
                 "chords": grid.chords},
        "drums": {"names": CHANNELS, "grid": studio.grid,
                  "muted": studio.muted, "volume": studio.volume},
        "hum": hum,
        "travis": travis,
        "eye": eye,
        "wake": wake or {"mozart": False, "travis": False},
        "beat_card": ({"label": state["beat_card"]["label"]}
                      if state["beat_card"] else None),
        "quit_armed": now - state["quit_armed"] < 4.0,
        "help": help_sheet() if view == "help" else None,
        "settings": {k: s.get(k) for k in (
            "dwell", "dwell_time", "voice_guard", "preview", "flip_x", "flip_y",
            "range_yaw", "chords", "melody_channel", "cursor", "theme",
            "wake_word_on", "pointer", "eye_nudge", "brow_click")},
        "wake_word": wake_word,
    }


def main():
    ap = argparse.ArgumentParser(description="HandsFree Studio")
    ap.add_argument("--mouse", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--project", help="project name for Travis's memory "
                    "(projects/<name>.md); remembered until you change it")
    ap.add_argument("--eyes", action="store_true",
                    help="start with the eye pointer")
    args = ap.parse_args()
    modes = MODES
    from ui_web import WebUI
    ui = WebUI()
    ui.start(open_window=not args.no_window)

    s = load_settings()
    if s.get("theme") not in THEMES:       # e.g. saved by an earlier UI
        s["theme"] = THEMES[0]
    if s.get("cursor") not in CURSORS:
        s["cursor"] = CURSORS[0]
    if args.eyes:
        s["pointer"] = "eyes"
    fl = FLBridge(dry_run=args.dry_run)
    studio = Studio(fl, tempo=s["tempo"])
    grid = PadGrid(s["root"], s["scale"], s["octave"], s["cols"], s["rows"])
    grid.chords = s["chords"]
    fx, fy = OneEuro(), OneEuro()

    activity = VoiceActivity()
    hum = HumEngine(tempo=studio.tempo, root=grid.root, scale=grid.scale,
                    octave_shift=grid.octave - 3, latency=s["hum_latency"])
    # Travis: "Travis, G minor chord for a quarter note ..." -> Gemini -> notes
    seq = Sequencer()
    travis = Travis(studio.pending, context_fn=lambda: {
        "tempo": studio.tempo, "time_signature": "4/4",
        "key": "%s %s" % (grid.root, grid.scale.replace("_", " ")),
        "default_octave": grid.octave,
        "transport": "playing" if studio.playing else "stopped"})
    if args.project:
        s["project"] = args.project
        save_settings(s)
    travis.memory = ProjectMemory(s["project"])
    print("Project: %s (Travis's memory: %s)" % (
        travis.memory.name, os.path.relpath(travis.memory.path)))
    voice_on = False
    mic_on = False
    # Shared with the microphone thread, so the Menu switch works at once.
    voice_control = {"wake": s["wake_word_on"], "wake_word": s["wake_word"],
                     "min_conf": s["voice_confidence"]}
    try:
        # keep mic_stream referenced, or the microphone stops
        mic_stream, voice_on = start_audio(studio.pending, activity, hum,
                                           use_vosk=not args.no_voice,
                                           travis=travis,
                                           control=voice_control)
        mic_on = True
    except Exception as exc:
        print("Microphone is off (%s). Voice and HUM mode are unavailable."
              % exc)
    # With voice on, wait a little longer before a mouth click fires, so the
    # microphone has time to notice that the user is speaking.
    trigger = MouthTrigger(arm_frames=4 if voice_on else 2)
    brow = BrowTrigger()                 # play mode: eyebrows click too

    running = {"on": True}

    def hum_sender():
        """Sends hummed notes and Travis's notes at their exact times."""
        while running["on"]:
            now = time.monotonic()
            msgs = seq.due(now)
            if hum.enabled:
                msgs += hum.due(now)
            for msg in msgs:
                if msg[0] == "on":
                    fl.note_on(msg[1], msg[2])
                else:
                    fl.note_off(msg[1])
            busy = hum.enabled or seq.busy_until() > now
            time.sleep(0.003 if busy else 0.02)

    threading.Thread(target=hum_sender, daemon=True).start()

    def hum_off():
        if hum.enabled:
            hum.enabled = False
            for msg in hum.stop():
                fl.note_off(msg[1])

    tracker = None
    if not args.mouse:
        from face_tracker import FaceTracker
        tracker = FaceTracker(args.camera, hd=True)  # one format, both modes
        tracker.start()

    # Eye pointer (gaze.py). It needs the camera; the window tells it where
    # the targets are.
    gp = None
    eye = {"cal": None, "pending": False, "snap": False, "key": None,
           "targets": [], "mid": None}
    if tracker is not None:
        from gaze import GazePointer, features as gaze_features, nod_y
        gp = GazePointer(os.path.join(os.path.dirname(SETTINGS_PATH),
                                      "gaze_model.json"), W, H)
        gp.load()

    def eyes_on():
        return gp is not None and s.get("pointer") == "eyes"

    if eyes_on():
        tracker.set_hires(True)
        eye["pending"] = not gp.ready        # calibrate, after centring

    mouse = {"x": W / 2, "y": H / 2, "down": False, "was_down": False}

    state = {"mode": "pads", "held": None, "playing_index": None,
             "calibrating": tracker is not None, "menu": False,
             "quit": False, "quit_armed": 0.0,
             "eyes_since": None, "eyes_long": False, "note_until": None,
             "rec": False, "t_play": None, "clicks": 0, "heard": None,
             "beat_card": None, "click_src": None, "nav_grid": None}
    dwell = {"key": None, "since": 0.0, "fired": False}
    centre = {"yaw": 0.0, "pitch": 0.0}
    samples = []
    cursor_px = (W / 2, H / 2)
    lost_since = None
    # Opening the mouth moves the head a little. A click therefore lands where
    # the cursor was just before the mouth started to open, and the cursor
    # stays locked there until the mouth closes again.
    history = collections.deque(maxlen=60)       # (time, cursor) while at rest
    lock = {"px": None, "rest": 0.0}

    def view_name():
        """The view on screen, as the window names its layouts."""
        return ("help" if state.get("help") else "menu" if state["menu"]
                else state["mode"])

    def release():
        for note in state["held"] or []:
            fl.note_off(note)
        state["held"] = None
        state["playing_index"] = None
        state["note_until"] = None
        trigger.force_off()
        brow.force_off()

    def start_calibration():
        release()
        samples.clear()
        history.clear()
        lock["px"] = None
        fx.reset()
        fy.reset()
        state["calibrating"] = tracker is not None
        studio.say("Centred")

    def start_gazecal():
        """Eye calibration. Waits for the centring step if that is running."""
        release()
        if state["calibrating"]:
            eye["pending"] = True
            return
        kx = (-1 if s["flip_x"] else 1) * W / (2 * s["range_yaw"])
        ky = (1 if s["flip_y"] else -1) * H / (2 * s["range_pitch"])
        eye["cal"] = gp.start_calibration(
            time.monotonic(), kx, ky, s.get("eye_calibration", "follow"))
        eye["pending"] = False
        state["menu"] = False
        studio.say("Follow the dot")

    def play_grid(on):
        """Play mode shows fewer, taller rows of pads: up-and-down is where
        a webcam is least sure of the gaze. Navigate mode gets its own grid
        back."""
        if on:
            rows = int(s.get("play_rows", 2) or 0)
            if state["nav_grid"] is None and rows in (2, 3, 4):
                state["nav_grid"] = (grid.cols, grid.rows)
                if rows != grid.rows:
                    grid.rows = rows
                    grid._rebuild()
        elif state["nav_grid"] is not None:
            (grid.cols, grid.rows), state["nav_grid"] = state["nav_grid"], None
            grid._rebuild()

    def set_pointer(want):
        release()
        if want == "eyes":
            if gp is None:
                studio.say("Play mode needs the camera")
                return
            s["pointer"] = "eyes"
            save_settings(s)
            tracker.set_hires(True)
            state["menu"] = False
            if state["mode"] != "pads":          # play mode is for the pads
                do(("mode", "pads"))
            play_grid(True)
            if gp.ready:
                gp.reset()
                studio.say("Play mode")
            else:
                start_gazecal()
        else:
            s["pointer"] = "head"
            save_settings(s)
            eye.update(cal=None, pending=False, snap=False)
            play_grid(False)
            if gp is not None:
                gp.cal = None
                tracker.set_hires(False)
            fx.reset()
            fy.reset()
            history.clear()
            lock["px"] = None
            studio.say("Navigate mode")

    def snap_targets(view):
        """Where the eye pointer may land: every target of the view, with
        the pad area split into its pads."""
        key = (view, ui.layout_version, grid.cols, grid.rows)
        if eye["key"] != key:
            out = []
            eye["mid"] = None
            for hit, x, y, w, h in ui.targets(view):
                if hit[0] == "pads":
                    eye["mid"] = (y + h / 2, h)  # the pad area, for nodding
                if hit[0] != "pads":
                    out.append((hit, x, y, w, h))
                    continue
                cw, ch = w / grid.cols, h / grid.rows
                for i in range(grid.cols * grid.rows):
                    cx, cy = x + (i % grid.cols) * cw, y + (i // grid.cols) * ch
                    got = ui.hit(view, cx + cw / 2, cy + ch / 2)
                    if got and got[0] == "pads":
                        out.append((("pad", i), cx + 4, cy + 4, cw - 8, ch - 8))
            eye["key"], eye["targets"] = key, out
        return eye["targets"]

    def keep_grid():
        # In play mode the saved grid stays the navigate-mode one.
        cols, rows = state["nav_grid"] or (grid.cols, grid.rows)
        s.update(root=grid.root, scale=grid.scale, octave=grid.octave,
                 cols=cols, rows=rows)
        save_settings(s)
        hum.configure(grid.root, grid.scale, grid.octave - 3)
        studio.say(grid.title())

    def do(cmd):
        kind = cmd[0]
        if kind == "mode":
            release()
            hum_off()
            state["mode"] = cmd[1] or modes[
                (modes.index(state["mode"]) + 1) % len(modes)]
            if state["mode"] in ("pads", "hum"):
                fl.select_channel(s["melody_channel"])
            if state["mode"] == "hum" and mic_on:
                hum.set_tempo(studio.tempo, time.monotonic())
                hum.enabled = True
            studio.say({"pads": "Pads", "drums": "Drums", "mix": "Mix",
                        "hum": "Hum" if mic_on else "Hum: no microphone"}
                       [state["mode"]])
        elif kind == "heard":
            state["heard"] = (cmd[1], time.monotonic())
        elif kind == "travis_plan":
            run_travis(cmd[1], cmd[2])
        elif kind == "travis_demo":
            travis.submit(TRAVIS_DEMO)
        elif kind == "cycle":
            names = CURSORS if cmd[1] == "cursor" else THEMES
            cur = s[cmd[1]] if s[cmd[1]] in names else names[0]
            s[cmd[1]] = names[(names.index(cur) + 1) % len(names)]
            save_settings(s)
            studio.say("%s: %s" % (cmd[1].capitalize(), s[cmd[1]]))
        elif kind == "pattern":
            prev = {name: list(studio.grid[ch])
                    for ch, name in enumerate(CHANNELS)}
            studio.run(cmd)
            state["beat_card"] = {"label": cmd[2], "prev": prev}
        elif kind == "beat_keep":
            state["beat_card"] = None
            studio.say("Kept")
        elif kind == "beat_undo":
            if state["beat_card"]:
                studio.set_pattern(state["beat_card"]["prev"], "previous pattern")
            state["beat_card"] = None
        elif kind == "center":
            start_calibration()
        elif kind == "pointer":
            set_pointer(cmd[1] or ("head" if s.get("pointer") == "eyes"
                                   else "eyes"))
        elif kind == "gazecal":
            if gp is None:
                studio.say("Play mode needs the camera")
            else:
                s["pointer"] = "eyes"
                save_settings(s)
                tracker.set_hires(True)
                start_gazecal()
        elif kind == "help":
            release()
            want = (not state.get("help")) if cmd[1] is None else cmd[1]
            if want or state.get("help"):
                state["help"] = want
                if want:
                    state["menu"] = False
                studio.say("Help" if want else "Help closed")
        elif kind == "menu":
            release()
            state["help"] = False
            state["menu"] = not state["menu"]
            studio.say("Menu" if state["menu"] else "Menu closed")
        elif kind == "range":
            # +1 = "move more": a bigger head turn is needed to cross the screen
            s["range_yaw"] = min(35.0, max(6.0, s["range_yaw"] + 2 * cmd[1]))
            s["range_pitch"] = round(s["range_yaw"] * 2 / 3, 1)
            save_settings(s)
            studio.say("Head range %d degrees" % s["range_yaw"])
        elif kind == "toggle":
            s[cmd[1]] = not s[cmd[1]]
            save_settings(s)
            if cmd[1] == "chords":
                release()
                grid.chords = s["chords"]
            elif cmd[1] == "wake_word_on":
                voice_control["wake"] = s["wake_word_on"]
            studio.say("%s %s" % (cmd[1].replace("_", " "),
                                  "on" if s[cmd[1]] else "off"))
        elif kind == "clear_notes":
            for msg in seq.cancel():           # Travis notes still to come
                fl.note_off(msg[1])
            studio.run(("clear_notes", s["melody_channel"]))
        elif kind == "undo":
            fl.undo()
            studio.say("Undo")
        elif kind == "instrument":
            # Which channel rack row the pads and HUM mode play.
            release()
            s["melody_channel"] = max(0, min(31, s["melody_channel"] + cmd[1]))
            save_settings(s)
            fl.select_channel(s["melody_channel"])
            studio.say("Sound: channel rack row %d" % (s["melody_channel"] + 1))
        elif kind == "reset":
            release()
            grid.shift_octave(DEFAULTS["octave"] - grid.octave)
            keep_grid()
            do(("tempo", DEFAULTS["tempo"]))
            studio.say("Reset: octave %d, tempo %d"
                       % (DEFAULTS["octave"], DEFAULTS["tempo"]))
        elif kind == "quit":
            if time.monotonic() - state["quit_armed"] < 4.0:
                state["quit"] = True
            else:
                state["quit_armed"] = time.monotonic()
                studio.say("Click QUIT again to close")
        elif kind in ("scale", "key", "octave", "grid"):
            release()
            if kind == "scale":
                grid.next_scale()
            elif kind == "key":
                grid.next_root()
            elif kind == "octave":
                grid.shift_octave(cmd[1])
            else:
                grid.next_grid()
            keep_grid()
        else:
            if kind == "record" and state["rec"]:
                cmd, kind = ("stop",), "stop"  # "record" again ends the take
            if kind == "stop":
                for msg in seq.cancel():       # Travis notes still to come
                    fl.note_off(msg[1])
            studio.run(cmd)
            if kind == "record":
                # Record also starts FL Studio: it only writes notes while
                # it is running.
                state["rec"] = True
                if not studio.playing:
                    studio.playing = True
                    state["t_play"] = time.monotonic()
                    hum.reset_clock(time.monotonic())
            elif kind == "play":
                state["t_play"] = time.monotonic()
            elif kind == "stop":
                state["t_play"] = None
                state["rec"] = False           # stop also ends a recording
            elif kind == "beat":
                state["beat_card"] = None
            if kind in ("tempo", "tempo_by"):
                s["tempo"] = studio.tempo
                save_settings(s)
                hum.set_tempo(studio.tempo, time.monotonic())
            elif kind == "play":
                # FL Studio starts its bar now, so the metronome does too.
                hum.reset_clock(time.monotonic())

    def run_travis(actions, info):
        """Carry out a checked Travis plan: app commands at once, then the
        notes on the timeline (next bar while playing, else right away)."""
        for a in actions:
            if a["kind"] == "command":
                do(a["cmd"])
        times = [None] * len(actions)
        if any(a["kind"] != "command" for a in actions):
            fl.select_channel(s["melody_channel"])
            start = plan_start(time.monotonic(), studio.tempo,
                               state["t_play"] if studio.playing else None,
                               seq.busy_until())
            times, _ = seq.schedule(actions, start, studio.tempo)
        travis.scheduled(actions, times, info)
        studio.say("Travis: " + (info.get("say") or ", ".join(
            a["label"] for a in actions)))

    def click(hover, velocity, timed=False):
        """Act on whatever is under the pointer. timed=True (dwell click)
        plays a pad for a fixed length instead of holding it."""
        if hover[0] == "button":
            do(BUTTON_COMMANDS[hover[1]])
        elif hover[0] == "pads" and state["held"] is None:
            notes = grid.chord()             # one note, or a chord
            if notes:
                for note in notes:
                    fl.note_on(note, velocity)
                state["held"] = notes
                state["playing_index"] = grid.selected
                if timed:
                    state["note_until"] = time.monotonic() + DWELL_NOTE_LENGTH
        elif hover[0] == "step":
            do(("step", hover[1], hover[2]))
        elif hover[0] == "label":
            do(("mute", hover[1], None))
        elif hover[0] == "cmd":
            do(tuple(hover[1]))
        elif hover[0] == "mode":
            do(("mode", hover[1]))
        state["clicks"] += 1

    if eyes_on():
        play_grid(True)
    fl.ping()
    fl.set_tempo(studio.tempo)
    fl.select_channel(s["melody_channel"])

    st = None
    try:
        while True:
            now = time.monotonic()
            message = ""
            level = 0.0
            event = None
            dwell_progress = 0.0
            tracking_ok = tracker is None

            if tracker is not None:
                st = tracker.get()
                now = time.monotonic()
                if st is None:
                    message = "Camera not responding"
                else:
                    if not st.found:
                        lost_since = lost_since or now
                        if now - lost_since > 0.4:
                            release()
                            message = "No face found - face the camera"
                    else:
                        lost_since = None
                        if st.eyes_closed > EYES_CLOSED:
                            state["eyes_since"] = state["eyes_since"] or now
                            if (now - state["eyes_since"] > EYES_HOLD
                                    and not state["eyes_long"]):
                                state["eyes_long"] = True
                                release()
                        else:
                            state["eyes_since"] = None
                            if state["eyes_long"] and st.eyes_closed < 0.3:
                                state["eyes_long"] = False
                                do(("center",))
                        if state["eyes_long"]:
                            message = "Open your eyes and look at the centre"
                        elif state["calibrating"]:
                            samples.append((st.yaw, st.pitch, st.mouth))
                            message = "Look at the centre, mouth closed..."
                            if len(samples) >= CALIBRATION_FRAMES:
                                n = len(samples)
                                centre["yaw"] = sum(a for a, _, _ in samples) / n
                                centre["pitch"] = sum(b for _, b, _ in samples) / n
                                trigger.set_baseline(
                                    sum(c for _, _, c in samples) / n)
                                state["calibrating"] = False
                                if eye["pending"] and eyes_on():
                                    start_gazecal()
                        elif eye["cal"] is not None:
                            # Eye calibration: the page shows the dots.
                            if gp.feed_calibration(
                                    gaze_features(st.gaze, st.yaw, st.pitch),
                                    st.gaze["blink"] if st.gaze else 1.0, now):
                                eye["cal"] = None
                                history.clear()
                                lock["px"] = None
                                if gp.finish_calibration():
                                    studio.say("Play mode ready (%s)"
                                               % gp.quality())
                                    print("Eye calibration: about %d px off on "
                                          "an unseen dot, %d px frame scatter"
                                          % (gp.accuracy, gp.model.noise))
                                else:
                                    studio.say("Eye calibration failed: "
                                               "press i to try again")
                        else:
                            dx = (st.yaw - centre["yaw"]) / (2 * s["range_yaw"])
                            dy = (st.pitch - centre["pitch"]) / (2 * s["range_pitch"])
                            if s["flip_x"]:
                                dx = -dx
                            if s["flip_y"]:
                                dy = -dy
                            hx, hy = fx(0.5 + dx, now), fy(0.5 - dy, now)
                            x = min(1.0, max(0.0, hx))
                            y = min(1.0, max(0.0, hy))
                            live_px = (x * (W - 1), y * (H - 1))
                            play = eyes_on() and gp.ready
                            brows = play and s.get("brow_click", True)
                            if brows:
                                brow.track(st.brow)
                            elif brow.state != "idle":
                                brow.reset()
                            if play:
                                # The eyes choose the column. The row comes
                                # from the head: a small nod up or down from
                                # the centre, amplified. (A webcam reads
                                # up-down eye movement too poorly.)
                                # Raised eyebrows bend the eye shape, so the
                                # pointer holds still meanwhile (as in a blink).
                                targets = snap_targets(view_name())
                                head_y = None
                                if s.get("play_rows_by", "head") == "head":
                                    head_y = min(H - 1.0, max(0.0, hy * (H - 1)))
                                    if eye["mid"]:           # pads on screen
                                        head_y = nod_y(
                                            (hy - 0.5) * (H - 1), eye["mid"][0],
                                            eye["mid"][1], grid.rows,
                                            s.get("play_nod_gain", 2.5), H)
                                got = gp.update(
                                    gaze_features(st.gaze, st.yaw, st.pitch),
                                    1.0 if (brows and brow.lifted(st.brow))
                                    else st.gaze["blink"] if st.gaze else 1.0,
                                    now, targets,
                                    (hx * (W - 1), hy * (H - 1)),
                                    s.get("eye_nudge", True), head_y=head_y)
                                if got:
                                    live_px = got["px"]
                                    eye["snap"] = got["snap"]
                            level = trigger.level(st.mouth)
                            if brows:
                                level = max(level, brow.level(st.brow))
                            if st.mouth < trigger.rest and not (
                                    brows and st.brow >= brow.rest):
                                lock["rest"] = now
                            was_idle = (trigger.state == "idle"
                                        and brow.state == "idle")
                            event = trigger.update(st.mouth)
                            if (event and event[0] == "on" and voice_on
                                    and s["voice_guard"] and activity.speaking()):
                                event = None          # talking, not clicking
                            # Mouth or eyebrows: whichever started a note is
                            # the one that ends it.
                            if event and event[0] == "on":
                                if state["held"] is None:
                                    state["click_src"] = "mouth"
                            elif event and state["click_src"] == "brow":
                                event = None
                            if brows:
                                bev = brow.update(st.brow)
                                if (bev and bev[0] == "on" and event is None
                                        and state["held"] is None
                                        and trigger.state == "idle"):
                                    event = bev
                                    state["click_src"] = "brow"
                                elif (bev and bev[0] == "off" and event is None
                                      and state["click_src"] == "brow"):
                                    event = bev
                            if trigger.state == "idle" and brow.state == "idle":
                                lock["px"] = None
                                history.append((now, live_px))
                            elif was_idle or lock["px"] is None:
                                t_lock = lock["rest"] - 0.06   # a touch earlier
                                if now - t_lock > LOCK_MAX_AGE:
                                    t_lock = now - LOCK_FALLBACK
                                lock["px"] = live_px
                                for t, p in reversed(history):
                                    lock["px"] = p
                                    if t <= t_lock:
                                        break
                            cursor_px = lock["px"] or live_px
                            tracking_ok = True
            else:
                cursor_px = (mouse["x"], mouse["y"])
                if mouse["down"] and not mouse["was_down"]:
                    event = ("on", 100)
                elif not mouse["down"] and mouse["was_down"]:
                    event = ("off",)
                mouse["was_down"] = mouse["down"]
                level = 1.0 if mouse["down"] else 0.0

            while not studio.pending.empty():
                do(studio.pending.get())

            for msg in ui.poll_input():
                if msg.get("type") == "key":
                    cmd = key_command(msg.get("key"), studio.playing)
                    if cmd == "quit":
                        state["quit"] = True
                    elif cmd:
                        do(cmd)
                elif msg.get("type") == "mouse" and args.mouse:
                    mouse["x"], mouse["y"] = msg["x"], msg["y"]
                    mouse["down"] = bool(msg["down"])

            hover = None
            if not state["calibrating"] and eye["cal"] is None:
                hover = ui.hit(view_name(), cursor_px[0], cursor_px[1])
                if state["held"] is None:     # pad stays locked while it sounds
                    if hover and hover[0] == "pads":
                        grid.update(hover[1], hover[2])
                    else:
                        grid.selected = None
                if event and event[0] == "on" and hover:
                    click(hover, event[1])
                    if eyes_on() and gp.ready:   # what was clicked is what
                        gp.learn_click(cursor_px, lock["rest"])   # was looked at
                elif event and event[0] == "off" and state["held"] is not None:
                    release()

                if state["note_until"] and now >= state["note_until"]:
                    release()

                # Dwell click: hold the pointer still on a target to click it.
                if s["dwell"] and tracking_ok and not state["eyes_long"]:
                    key_ = hover
                    if hover and hover[0] == "pads":
                        key_ = (("pads", grid.selected)
                                if grid.selected is not None else None)
                    if key_ != dwell["key"]:
                        dwell.update(key=key_, since=now, fired=False)
                    elif (key_ is not None and not dwell["fired"]
                          and state["held"] is None):
                        dwell_progress = min(
                            1.0, (now - dwell["since"]) / s["dwell_time"])
                        if dwell_progress >= 1.0:
                            dwell["fired"] = True
                            dwell_progress = 0.0
                            click(hover, 96, timed=True)
                            if eyes_on() and gp.ready:
                                gp.learn_click(cursor_px, now)
                else:
                    dwell["key"] = None

            ui.publish(web_state(
                now, state, grid, studio, hover, cursor_px, level,
                dwell_progress, message, s, fl, voice_on, mic_on,
                activity, tracker, st if tracker is not None else None,
                hum.snapshot(now) if (state["mode"] == "hum" and mic_on)
                else None, len(samples), travis=travis.snapshot(now),
                wake_word=voice_control["wake_word"],
                wake={"mozart": now - voice_control.get("woke_at", 0.0) < 4.0,
                      "travis": travis.wake_heard or travis.listening(now)},
                eye={"on": eyes_on() and (gp.ready or eye["cal"] is not None),
                     "snap": eye["snap"],
                     "cal": (eye["cal"].snapshot(now)
                             if eye["cal"] is not None else None)}))
            if state["quit"]:
                break
            if tracker is None:
                time.sleep(1 / 60)
    finally:
        running["on"] = False
        for msg in seq.cancel():
            fl.note_off(msg[1])
        hum_off()
        release()
        fl.close()
        if tracker is not None:
            tracker.close()
        ui.close()


if __name__ == "__main__":
    main()
