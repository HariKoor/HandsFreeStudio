"""
HandsFree Studio - produce mode logic: the drum grid, commands, voice and
Gemini beats. No window or camera code here (see test_studio.py).

A command is a tuple, e.g. ("play",), ("tempo", 90), ("step", 0, 4),
("mute", 2, True), ("beat", "house"). Buttons, keys, voice and Gemini all
produce the same tuples, and Studio.run() carries them out.
"""

import json
import os
import queue
import threading
import time
import urllib.request

import numpy as np

# Rows map to the first four channels of FL Studio's default template.
CHANNELS = ["kick", "clap", "hat", "snare"]
STEPS = 16
STYLES = ["hip hop", "house", "trap", "rock"]

PRESETS = {
    "hip hop": {"kick": "1000000010100000", "clap": "0000100000001000",
                "hat": "1010101010101010", "snare": "0000000000000010"},
    "house":   {"kick": "1000100010001000", "clap": "0000100000001000",
                "hat": "0010001000100010", "snare": "0000000000000000"},
    "trap":    {"kick": "1000000100100000", "clap": "0000000010000000",
                "hat": "1111111111111111", "snare": "0000000000001000"},
    "rock":    {"kick": "1000000010100000", "clap": "0000000000000000",
                "hat": "1010101010101010", "snare": "0000100000001000"},
}


class Studio:
    def __init__(self, fl, tempo=120):
        self.fl = fl
        self.grid = [[False] * STEPS for _ in CHANNELS]
        self.muted = [False] * len(CHANNELS)
        self.volume = [100] * len(CHANNELS)
        self.tempo = tempo
        self.playing = False
        self.style = 0
        self.status = "Ready"
        self.pending = queue.Queue()   # commands from voice / Gemini threads

    def say(self, text):
        self.status = text
        print(">> " + text)

    def set_step(self, ch, step, on):
        if self.grid[ch][step] != on:
            self.grid[ch][step] = on
            self.fl.drum_step(ch, step, on)

    def set_pattern(self, pattern, label):
        for ch, name in enumerate(CHANNELS):
            row = pattern[name]
            for step in range(STEPS):
                self.set_step(ch, step, bool(row[step]))
        self.say("Beat: " + label)

    def run(self, cmd):
        """Carry out one command. Returns False if it was not understood."""
        kind = cmd[0]
        if kind == "play":
            self.fl.play()
            self.playing = True
            self.say("Play")
        elif kind == "stop":
            self.fl.stop()
            self.playing = False
            self.say("Stop")
        elif kind == "record":
            self.fl.toggle_record()
            self.say("Recording. Say stop to finish")
        elif kind == "tempo":
            self.tempo = max(60, min(187, cmd[1]))
            self.fl.set_tempo(self.tempo)
            self.say("Tempo %d" % self.tempo)
        elif kind == "tempo_by":
            return self.run(("tempo", self.tempo + cmd[1]))
        elif kind == "clear":
            self.grid = [[False] * STEPS for _ in CHANNELS]
            self.fl.clear_drums(len(CHANNELS))
            self.say("Cleared")
        elif kind == "clear_notes":
            self.fl.clear_notes(cmd[1])
            self.say("Notes cleared")
        elif kind == "step":
            ch, step = cmd[1], cmd[2]
            self.set_step(ch, step, not self.grid[ch][step])
            self.say("%s step %d %s" % (CHANNELS[ch], step + 1,
                                        "on" if self.grid[ch][step] else "off"))
        elif kind == "mute":
            ch = cmd[1]
            want = (not self.muted[ch]) if cmd[2] is None else cmd[2]
            self.muted[ch] = want
            self.fl.drum_mute(ch, want)
            self.say("%s %s" % (CHANNELS[ch], "muted" if want else "unmuted"))
        elif kind == "volume_by":
            ch = cmd[1]
            self.volume[ch] = max(0, min(127, self.volume[ch] + cmd[2]))
            self.fl.drum_volume(ch, self.volume[ch])
            self.say("%s volume %d%%" % (CHANNELS[ch],
                                         round(self.volume[ch] / 1.27)))
        elif kind == "beat":
            style = cmd[1]
            if style is None:
                style = STYLES[self.style]
                self.style = (self.style + 1) % len(STYLES)
            self.say("Making a %s beat..." % style)
            threading.Thread(target=self._make_beat, args=(style,),
                             daemon=True).start()
        elif kind == "pattern":
            self.set_pattern(cmd[1], cmd[2])
        else:
            return False
        return True

    def _make_beat(self, style):
        pattern, label = None, style + " (preset)"
        if os.environ.get("GEMINI_API_KEY"):
            try:
                pattern = gemini_beat(style)
                label = style + " (Gemini)"
            except Exception as exc:            # network, quota, bad JSON...
                print("Gemini failed, using preset: %s" % exc)
        if pattern is None:
            pattern = {k: [c == "1" for c in v]
                       for k, v in PRESETS[style].items()}
        self.pending.put(("pattern", pattern, label))


def validate_pattern(data):
    """Check a pattern from Gemini: four rows of sixteen 0/1 values."""
    out = {}
    for name in CHANNELS:
        row = data[name]
        if len(row) != STEPS:
            raise ValueError("%s has %d steps" % (name, len(row)))
        out[name] = [bool(int(v)) for v in row]
    return out


def gemini_beat(style):
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    prompt = ("Write a one-bar %s drum pattern for a 16-step sequencer. "
              "Reply with JSON only: an object with keys kick, clap, hat, "
              "snare, each a list of 16 integers, 1 for a hit and 0 for "
              "silence." % style)
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/%s:"
        "generateContent" % model,
        data=body,
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    with urllib.request.urlopen(req, timeout=15) as resp:
        reply = json.load(resp)
    text = reply["candidates"][0]["content"]["parts"][0]["text"]
    return validate_pattern(json.loads(text))


# ---------------------------------------------------------------------------
# Voice commands
# ---------------------------------------------------------------------------
TEMPO_WORDS = {"eighty": 80, "ninety": 90, "one hundred": 100, "one ten": 110,
               "one twenty": 120, "one thirty": 130, "one forty": 140,
               "one fifty": 150, "one sixty": 160}

SIMPLE = {
    "play": ("play",), "stop": ("stop",), "record": ("record",),
    "stop recording": ("stop",),
    "clear drums": ("clear",), "clear the drums": ("clear",),
    # the notes in the piano roll of the sound the pads play
    "clear notes": ("clear_notes",), "clear the notes": ("clear_notes",),
    "clear piano roll": ("clear_notes",),
    "clear the piano roll": ("clear_notes",),
    "clear": ("clear",), "faster": ("tempo_by", 5), "slower": ("tempo_by", -5),
    "center": ("center",),
    "pads": ("mode", "pads"), "drums": ("mode", "drums"),
    "hum": ("mode", "hum"),
    "next scale": ("scale",), "next key": ("key",),
    "octave up": ("octave", 1), "octave down": ("octave", -1),
    "menu": ("menu",), "undo": ("undo",), "reset": ("reset",),
    "chords": ("toggle", "chords"),
    "next sound": ("instrument", 1), "previous sound": ("instrument", -1),
    # the two pointer modes: eyes for playing pads fast, head for the rest
    "play mode": ("pointer", "eyes"), "navigate mode": ("pointer", "head"),
    "normal mode": ("pointer", "head"), "back to normal": ("pointer", "head"),
    # the help sheet: every voice command on one screen
    "help": ("help", True), "close": ("help", False),
}

# What "Mozart help" shows: every voice command, grouped. test_studio.py
# checks that each phrase the recogniser accepts is listed here, so this
# sheet cannot fall behind the commands. "a / b" lists two phrases.
HELP = [
    ("Playback", [
        ("play / stop", "start FL Studio; stop also ends a recording"),
        ("record", "start recording what you play"),
        ("stop recording", "end the recording; then say play to hear it"),
        ("faster / slower", "tempo up or down by 5"),
        ("tempo ninety", "eighty to one sixty, in tens"),
        ("clear / clear drums / clear the drums", "empty the drum grid"),
        ("clear notes / clear the notes", "delete the notes you recorded"),
        ("clear piano roll / clear the piano roll", "the same"),
        ("undo", "undo the last change in FL Studio"),
        ("reset", "octave 4 and 120 BPM"),
    ]),
    ("Views", [
        ("pads / drums / hum", "switch view"),
        ("menu", "open or close the menu"),
        ("help / close", "open or close this sheet"),
    ]),
    ("Sound", [
        ("next scale / next key", "change what the pads play"),
        ("octave up / octave down", "move the pads"),
        ("chords", "each pad plays a chord, or one note"),
        ("next sound / previous sound", "the instrument the pads play"),
    ]),
    ("Drums", [
        ("mute kick / kick on", "also clap, hat, snare"),
        ("kick up / kick down", "volume of that drum"),
        ("house beat", "also hip hop, trap, rock"),
    ]),
    ("Pointer", [
        ("center", "look at the middle; that becomes the center"),
        ("play mode", "eyes pick the pad, eyebrows play it"),
        ("navigate mode / normal mode / back to normal", "head pointer again"),
    ]),
]


def help_sheet():
    """HELP as JSON-able data for the UI."""
    return [{"group": g, "items": [{"say": say, "does": does}
                                   for say, does in items]} for g, items in HELP]


def help_phrases():
    """Every phrase the help sheet names, with the examples expanded."""
    out = set()
    for _, items in HELP:
        for say, does in items:
            out.update(p.strip() for p in say.split(" / "))
    out.update("tempo " + w for w in TEMPO_WORDS)
    for name in CHANNELS:
        out.update(["mute " + name, name + " on", name + " up",
                    name + " down"])
    out.update(s + " beat" for s in STYLES)
    return out


def voice_phrases():
    phrases = list(SIMPLE)
    phrases += ["tempo " + w for w in TEMPO_WORDS]
    for name in CHANNELS:
        # "unmute" is not in the speech model's vocabulary, so say "kick on".
        phrases += ["mute " + name, name + " on",
                    name + " up", name + " down"]
    phrases += [s + " beat" for s in STYLES]
    return phrases


def parse_command(text):
    """Turn recognised speech into a command tuple, or None."""
    text = " ".join(text.lower().split())
    if text in SIMPLE:
        return SIMPLE[text]
    if text.startswith("tempo ") and text[6:] in TEMPO_WORDS:
        return ("tempo", TEMPO_WORDS[text[6:]])
    if text.endswith(" beat") and text[:-5] in STYLES:
        return ("beat", text[:-5])
    for ch, name in enumerate(CHANNELS):
        if text == "mute " + name:
            return ("mute", ch, True)
        if text == name + " on":
            return ("mute", ch, False)
        if text == name + " up":
            return ("volume_by", ch, 13)
        if text == name + " down":
            return ("volume_by", ch, -13)
    return None


TRAVIS_AUDIO_BYTES = 16000 * 2 * 12   # keep the last 12 s of a sentence


# ---------------------------------------------------------------------------
# Deciding whether speech really was a command
#
# The recogniser only knows our phrases, so it tends to squeeze any speech it
# hears (people talking nearby) into the closest one. These checks make it
# much harder for conversation to trigger something:
#   1. Wake word (optional): the utterance must start with it ("Mozart play").
#   2. The whole utterance must be exactly one command, nothing before or
#      after, and no unknown words.
#   3. Every word must be recognised with high confidence.
#   4. Every word must last a plausible time (forced matches often do not).
# ---------------------------------------------------------------------------
WAKE_FALLBACK = "computer"      # used if the speech model lacks the wake word
DEFAULT_VOICE = {"wake": True, "wake_word": "mozart", "min_conf": 0.85}


def grammar(wake_word):
    base = voice_phrases()
    return base + [wake_word + " " + p for p in base] + [wake_word, "[unk]"]


def woke(partial, control):
    """True when a sentence still being spoken starts with the wake word, so
    the UI can show that Mozart is listening before the command ends."""
    words = partial.lower().split()
    return bool(control.get("wake", True) and words
                and words[0] == control.get("wake_word", "mozart"))


def accept_voice(result, control):
    """result is Vosk's final result ({"text": ..., "result": [words]}).
    Returns (heard_text, command) or None if it should be ignored."""
    text = " ".join(result.get("text", "").lower().split())
    if not text or "[unk]" in text:
        return None
    tokens = text.split()
    woke = tokens[0] == control.get("wake_word", "mozart")
    if woke:
        tokens = tokens[1:]
    if control.get("wake", True) and not woke:
        return None
    core = " ".join(tokens)
    cmd = parse_command(core)
    if cmd is None:
        return None
    words = result.get("result") or []
    if words:
        if min(w.get("conf", 0.0) for w in words) < control.get("min_conf", 0.85):
            return None
        for w in words:
            length = w.get("end", 0.0) - w.get("start", 0.0)
            if not 0.08 <= length <= 1.5:
                return None
    return core, cmd


def _build_recognizer(vosk, model, wake_word):
    """Make the recogniser and report which of our words the speech model
    does not know (it prints a warning for each; we read those)."""
    import re
    import tempfile
    missing = []
    saved = None
    try:
        with tempfile.TemporaryFile(mode="w+b") as tmp:
            saved = os.dup(2)
            os.dup2(tmp.fileno(), 2)
            try:
                rec = vosk.KaldiRecognizer(model, 16000,
                                           json.dumps(grammar(wake_word)))
            finally:
                os.dup2(saved, 2)
                os.close(saved)
                saved = None
            tmp.seek(0)
            log = tmp.read().decode("utf-8", "replace")
            missing = re.findall(r"missing in vocabulary: '([^']+)'", log)
    except OSError:
        rec = vosk.KaldiRecognizer(model, 16000, json.dumps(grammar(wake_word)))
    return rec, sorted(set(missing))


class VoiceActivity:
    """Tracks whether the user is speaking, so that talking (which opens the
    mouth) is not mistaken for a mouth click."""

    def __init__(self):
        self.floor = 150.0
        self.last_loud = 0.0

    def feed(self, data):
        a = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if a.size == 0:
            return
        rms = float(np.sqrt(np.mean(a * a)))
        if rms > max(400.0, self.floor * 3.0):
            self.last_loud = time.monotonic()
            self.floor = 0.995 * self.floor + 0.005 * rms   # adapt slowly
        else:
            self.floor = 0.9 * self.floor + 0.1 * rms

    def speaking(self, within=0.35):
        return time.monotonic() - self.last_loud < within


def start_audio(out_queue, activity=None, hum=None, use_vosk=True,
                travis=None, control=None):
    """Open the default microphone. Feeds the voice guard, HUM mode and
    (if use_vosk) Vosk speech recognition limited to our phrases.
    With `travis` (a travis.Travis), a second recogniser with the full
    vocabulary listens for "Travis ..." and hands those sentences, and their
    audio, to it; the command grammar ignores them.
    control is a dict shared with the caller: {"wake", "wake_word",
    "min_conf"}; changing "wake" takes effect at once. It governs the app
    commands ("Mozart play"), not Travis.
    Returns (stream, voice_ok). Raises if there is no microphone."""
    import sounddevice as sd
    control = control if control is not None else dict(DEFAULT_VOICE)
    rec = free = None
    if use_vosk:
        try:
            import vosk
            vosk.SetLogLevel(-1)
            model = vosk.Model(lang="en-us")   # downloads a small model once
            rec, missing = _build_recognizer(vosk, model, control["wake_word"])
            if control["wake_word"] in missing:
                print("The speech model does not know the word '%s'. Using "
                      "'%s' as the wake word instead."
                      % (control["wake_word"], WAKE_FALLBACK))
                control["wake_word"] = WAKE_FALLBACK
                rec, missing = _build_recognizer(vosk, model, WAKE_FALLBACK)
            rec.SetWords(True)                 # per-word confidence and times
            if travis is not None:
                free = vosk.KaldiRecognizer(model, 16000)
        except Exception as exc:
            print("Voice commands are off (%s)." % exc)
            rec = free = None
    audio = queue.Queue()

    def on_audio(indata, frames, t, status):
        data = bytes(indata)
        if activity is not None:
            activity.feed(data)
        if hum is not None and hum.enabled:
            # Humming is not speech: voice commands pause in HUM mode.
            hum.process(np.frombuffer(data, dtype=np.int16),
                        time.monotonic() - frames / 16000.0)
        elif rec is not None:
            audio.put(data)

    def worker():
        said = bytearray()          # audio of the sentence being spoken
        for_travis = False          # this sentence is meant for Travis
        quiet_until = 0.0           # command grammar ignored until then
        n = m = 0
        while True:
            data = audio.get()
            if free is not None:
                said.extend(data)
                del said[:-TRAVIS_AUDIO_BYTES]
                if free.AcceptWaveform(data):
                    text = json.loads(free.Result()).get("text", "")
                    if travis.feed_final(text, bytes(said)):
                        quiet_until = time.monotonic() + 1.0
                    said.clear()
                    for_travis = False
                else:
                    n += 1
                    if n % 4 == 0:  # partials every 100 ms are plenty
                        partial = json.loads(free.PartialResult()).get("partial", "")
                        for_travis = travis.feed_partial(partial)
            if not rec.AcceptWaveform(data):
                m += 1
                if m % 4 == 0 and not for_travis:   # "Mozart ..." under way:
                    partial = json.loads(rec.PartialResult()).get("partial", "")
                    if woke(partial, control):      # the UI shows it at once
                        control["woke_at"] = time.monotonic()
            else:
                control["woke_at"] = 0.0
                # Strict: wake word (if on), exactly one command, confident.
                hit = accept_voice(json.loads(rec.Result()), control)
                if hit and (for_travis or time.monotonic() < quiet_until
                            or (travis is not None and travis.listening())):
                    hit = None      # "Travis, play ..." goes to Travis only
                if hit:
                    out_queue.put(("heard", hit[0]))  # for the UI's "Heard" card
                    out_queue.put(hit[1])

    stream = sd.RawInputStream(samplerate=16000, blocksize=400, dtype="int16",
                               channels=1, callback=on_audio)
    stream.start()
    if rec is not None:
        threading.Thread(target=worker, daemon=True).start()
    return stream, rec is not None
