"""
HandsFree Studio - Travis, the Gemini voice assistant.

Say "Travis" and then what you want, in plain words:

    "Travis, put a G minor chord for a quarter note followed by an F minor chord"
    "Travis ... C, A minor, F, G, one bar each"
    "Travis, play an E flat note for half a beat, then rest for a beat"

How it works
  1. A second Vosk recogniser (free vocabulary, not the command grammar)
     listens for the wake word. See find_wake().
  2. The words after it, and the audio of that sentence, go to Gemini
     (gemini_plan). Gemini must answer in a fixed JSON shape (SCHEMA):
     a list of actions such as {"type": "chord", "symbol": "Gm", "beats": 1}.
  3. validate_plan() checks every action. Chord and note symbols are turned
     into MIDI notes here, by chord_notes() / note_number(), not by Gemini,
     so a chord is always spelled correctly.
  4. app.py puts the notes on a timeline (Sequencer). If FL Studio is
     playing they start on the next bar, so with REC on they record on the
     grid; if it is stopped they play straight away as a preview.

No API key, or no network: local_plan() understands the simple cases
("G minor quarter note then F minor"), so the demo still works offline.

Try it without the app or a microphone:
    python travis.py "g minor chord for a quarter note followed by f minor"
"""

import base64
import collections
import io
import json
import math
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

from bridge import NOTE_NAMES, note_name

SR = 16000
LISTEN_SECONDS = 8.0      # after a bare "Travis", how long we wait for the request
CARD_SECONDS = 4.0        # how long the card stays up after the last note
HELP_SECONDS = 12.0       # how long "Travis help" stays up
HELP_ASKS = {"help", "help me", "what can you do", "what do you do",
             "how does this work", "how do i use you"}
MAX_AUDIO_SECONDS = 10.0  # audio sent to Gemini (the end of the sentence)
MAX_BEATS = 64.0          # 16 bars: the most one request may write
MAX_ACTIONS = 64
DEFAULT_VELOCITY = 96
# Google retires model names; set GEMINI_MODEL to override without editing code.
DEFAULT_MODEL = "gemini-3.5-flash-lite"   # ~1.4 s in our test; full Flash took 14-20 s
# Travis asks these at the same time and uses the first good answer, so one
# slow model does not leave the stage silent. Override: TRAVIS_MODELS=a,b
RACE_MODELS = [DEFAULT_MODEL, "gemini-3.1-flash-lite"]
GEMINI_DEADLINE = 6.0     # seconds; after this the offline parser takes over
GATE = 0.95               # a note sounds for 95% of its length, so repeats re-strike

# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------
# What the speech model writes when someone says "Travis". The small Vosk
# model does not always know the name, so close spellings count too. Add
# your own with the TRAVIS_WAKE environment variable (comma separated).
# The app prints the sentences Travis acts on as "[travis] heard: ...".
# Everything else it hears is discarded without being printed or stored. To
# tune the spellings, run with TRAVIS_DEBUG=1 and it prints every sentence,
# so you can see what to add.
WAKE_WORDS = {"travis", "travers", "traverse", "travess", "trevis", "travus",
              "travas", "travi", "trav", "traviss", "travel", "travels"}
WAKE_PAIRS = {("trav", "is"), ("travel", "is"), ("travis", "is"),
              ("trap", "is"), ("drive", "is"), ("travel", "us")}
LEAD = {"hey", "hi", "ok", "okay", "um", "uh", "so", "and", "yo", "oh"}
FILLER = {"hey", "hi", "ok", "okay", "please", "um", "uh", "so", "and", "yo",
          "can", "you", "could", "would"}


def _wake_words():
    extra = os.environ.get("TRAVIS_WAKE", "")
    return WAKE_WORDS | {w.strip().lower() for w in extra.split(",") if w.strip()}


def find_wake(text):
    """If the sentence starts with the wake word, return the words after it
    ("" when nothing follows); otherwise None. Only the start counts (after
    words like "hey" or "okay"), so the name coming up in the middle of a
    conversation does not set Travis off."""
    words = text.lower().replace(",", " ").split()
    wake = _wake_words()
    i = 0
    while i < len(words) and words[i] in LEAD:
        i += 1
    if i + 1 < len(words) and (words[i], words[i + 1]) in WAKE_PAIRS:
        return _strip_filler(words[i + 2:])
    if i < len(words) and words[i] in wake:
        return _strip_filler(words[i + 1:])
    return None


def _strip_filler(words):
    while words and words[0] in FILLER:
        words = words[1:]
    return " ".join(words)


# ---------------------------------------------------------------------------
# Music: chord and note symbols -> MIDI note numbers
# ---------------------------------------------------------------------------
PITCH = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}

# (spellings, intervals). Spellings are compared lower case with spaces
# removed, except the ones in CASE_SENSITIVE (M7 = major seventh, m7 = minor).
QUALITIES = [
    (["", "maj", "major", "triad"], [0, 4, 7]),
    (["m", "min", "minor", "mi"], [0, 3, 7]),
    (["7", "dom7", "dominant", "dominant7", "seven", "seventh"], [0, 4, 7, 10]),
    (["maj7", "major7", "majorseven", "majorseventh", "ma7", "Δ", "Δ7"],
     [0, 4, 7, 11]),
    (["m7", "min7", "minor7", "minorseven", "minorseventh", "mi7"],
     [0, 3, 7, 10]),
    (["mmaj7", "minmaj7", "minormajor7", "minormajorseven"], [0, 3, 7, 11]),
    (["dim", "diminished", "°", "o"], [0, 3, 6]),
    (["dim7", "diminished7", "diminishedseven", "°7", "o7"], [0, 3, 6, 9]),
    (["m7b5", "min7b5", "halfdiminished", "ø", "ø7"], [0, 3, 6, 10]),
    (["aug", "augmented", "+"], [0, 4, 8]),
    (["sus2", "suspended2", "sustwo"], [0, 2, 7]),
    (["sus4", "sus", "suspended4", "suspended", "susfour"], [0, 5, 7]),
    (["6", "maj6", "six", "major6"], [0, 4, 7, 9]),
    (["m6", "min6", "minor6", "minorsix"], [0, 3, 7, 9]),
    (["9", "dom9", "nine", "ninth"], [0, 4, 7, 10, 14]),
    (["maj9", "major9", "majornine"], [0, 4, 7, 11, 14]),
    (["m9", "min9", "minor9", "minornine"], [0, 3, 7, 10, 14]),
    (["add9", "addnine", "add2"], [0, 4, 7, 14]),
    (["madd9", "minoradd9", "minoraddnine"], [0, 3, 7, 14]),
    (["5", "power", "powerchord", "five"], [0, 7]),
]
CASE_SENSITIVE = {"M": [0, 4, 7], "M7": [0, 4, 7, 11], "M9": [0, 4, 7, 11, 14],
                  "M6": [0, 4, 7, 9]}
QUALITY = {}
for _names, _iv in QUALITIES:
    for _n in _names:
        QUALITY[_n] = _iv
# Short names used for display, by interval shape
SHORT = {tuple(iv): names[0] for names, iv in QUALITIES}

_ROOT = re.compile(r"^\s*([A-Ga-g])(#|♯|b|♭|\s*sharp|\s*flat|-sharp|-flat)?",
                   re.IGNORECASE)


def _root(text):
    """('G#m7', ...) -> (pitch class, rest of the text, display root)."""
    m = _ROOT.match(text)
    if not m:
        raise ValueError("no root note in %r" % text)
    letter = m.group(1).upper()
    acc = (m.group(2) or "").strip().lower().lstrip("-")
    shift = 1 if acc in ("#", "♯", "sharp") else -1 if acc in ("b", "♭", "flat") else 0
    shown = letter + ("#" if shift > 0 else "b" if shift < 0 else "")
    return (PITCH[letter.lower()] + shift) % 12, text[m.end():], shown


def parse_chord(symbol):
    """'Gm', 'F#7', 'Bbmaj7', 'C/E', 'G minor' -> (root pc, intervals,
    bass pc or None, display name). Raises ValueError if not a chord."""
    text = symbol.strip().replace("chord", "").strip()
    bass = bass_name = None
    if "/" in text:
        text, b = text.split("/", 1)
        bass, _, bass_name = _root(b)
    pc, rest, shown = _root(text)
    rest = rest.strip()
    if rest in CASE_SENSITIVE:
        iv = CASE_SENSITIVE[rest]
    else:
        key = rest.lower().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if rest.startswith("-"):                # C-7 = Cm7
            key = "m" + key
        if key not in QUALITY:
            raise ValueError("unknown chord quality %r in %r" % (rest, symbol))
        iv = QUALITY[key]
    name = shown + SHORT.get(tuple(iv), "")
    if bass is not None:
        name += "/" + bass_name
    return pc, list(iv), bass, name


def chord_notes(symbol, octave=4):
    """MIDI notes for a chord symbol. The root sits in `octave` (C4 = 60);
    a slash bass goes an octave below the root."""
    pc, iv, bass, _ = parse_chord(symbol)
    root = 12 * (octave + 1) + pc
    notes = [root + i for i in iv]
    if bass is not None:
        b = root - ((root - bass) % 12 or 12)   # the bass note just below the root
        notes = [b] + [n for n in notes if n % 12 != bass or n == root]
    return [n for n in notes if 0 <= n <= 127]


def note_number(symbol, octave=4):
    """'G4', 'Bb3', 'F#', 'C sharp 5' -> MIDI number. No octave: `octave`."""
    pc, rest, _ = _root(symbol)
    rest = rest.strip()
    if rest:
        if not re.fullmatch(r"-?\d", rest):
            raise ValueError("bad note %r" % symbol)
        octave = int(rest)
    n = 12 * (octave + 1) + pc
    if not 0 <= n <= 127:
        raise ValueError("note out of range %r" % symbol)
    return n


def beats_label(beats):
    """1 -> 'quarter', 0.5 -> '8th', 6 -> '1½ bars' ..."""
    names = {4.0: "whole", 2.0: "half", 1.0: "quarter", 0.5: "8th",
             0.25: "16th", 3.0: "dotted half", 1.5: "dotted quarter",
             0.75: "dotted 8th", 8.0: "2 bars", 16.0: "4 bars"}
    b = round(beats, 3)
    if b in names:
        return names[b]
    if abs(b - 1 / 3) < 0.01:
        return "8th triplet"
    if abs(b - 2 / 3) < 0.01:
        return "quarter triplet"
    if b % 4 == 0:
        return "%d bars" % (b // 4)
    return ("%g beats" % b) if b != 1 else "1 beat"


# ---------------------------------------------------------------------------
# The plan format (what Gemini must return)
# ---------------------------------------------------------------------------
def _phrases():
    from studio import voice_phrases
    return voice_phrases()


def schema():
    """Gemini response schema (OpenAPI subset)."""
    return {
        "type": "OBJECT",
        "properties": {
            "actions": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "type": {"type": "STRING",
                                 "enum": ["chord", "note", "rest", "command"]},
                        "symbol": {"type": "STRING",
                                   "description": "chord symbol like Gm, F#7, "
                                   "Bbmaj7, C/E; or note like G4, Bb3, F#"},
                        "beats": {"type": "NUMBER",
                                  "description": "length; quarter note = 1"},
                        "octave": {"type": "INTEGER",
                                   "description": "octave of the chord root "
                                   "or note, 4 = middle C octave"},
                        "velocity": {"type": "INTEGER",
                                     "description": "1-127, default 96"},
                        "phrase": {"type": "STRING", "enum": _phrases(),
                                   "description": "for type command only"},
                    },
                    "required": ["type"],
                    "propertyOrdering": ["type", "symbol", "beats", "octave",
                                         "velocity", "phrase"],
                },
            },
            "say": {"type": "STRING",
                    "description": "very short confirmation, max 8 words"},
            "clarify": {"type": "STRING",
                        "description": "short question if the request could "
                        "not be understood, else empty"},
        },
        "required": ["actions", "say", "clarify"],
        "propertyOrdering": ["actions", "say", "clarify"],
    }


SYSTEM = """You are Travis, the voice assistant inside HandsFree Studio, a hands-free app that writes music into FL Studio. Turn one spoken request into a list of actions, in the order they should sound.

Action types:
- chord: symbol in standard notation. Root letter, optional # or b, then the quality: "" major, m, 7, maj7, m7, mMaj7, dim, dim7, m7b5, aug, sus2, sus4, 6, m6, 9, maj9, m9, add9, madd9, 5. Optional /bass. Examples: Gm, F#7, Bbmaj7, C/E.
- note: one note, like G4 (C4 is middle C) or just G to use the default octave.
- rest: silence for some beats.
- command: an app command; phrase must be one of the allowed phrases (for "play", "stop", "tempo ninety", "record", "undo" and so on). "clear" empties the drum grid; "clear notes" deletes the notes in the piano roll.

Durations are in beats, quarter note = 1: whole 4, half 2, eighth 0.5, sixteenth 0.25, dotted quarter 1.5, eighth triplet 1/3, a bar 4 (4/4 time).
If a length is not given, reuse the last length given in the same request, else 1 beat.
Expand repeats ("four times", "each for a bar", "then the same again") into explicit actions.
Roman numerals and "the one chord" are relative to the key in the context.
Octaves: every chord and note is played in the default octave from the context unless you set its octave field. To play something higher or lower ("an octave up", "raise those an octave", "two octaves down"), set the octave field on every chord and note that should move; the memory shows the octave each earlier chord and note was played in. Do not use the "octave up" or "octave down" commands for this: those only move the on-screen pads and do not change what you play. Use them only when the user asks to change the pads' octave and asks for no music.

The request comes from speech recognition and may be misheard: "gee" = G, "be"/"bee" = B, "see"/"sea" = C, "dee" = D, "eff" = F, "flat" may be "flap", "quarter" may be "border"/"water", "minor" may be "mine or"/"miner". If audio is attached, trust what you hear in the audio over the text. The audio may begin with the wake word "Travis"; ignore it.

If the request is not about music or the app, or you cannot tell what is meant, return no actions and put one short question in clarify. Otherwise clarify is "". The microphone also picks up people talking to each other, so a request can be a stray piece of conversation. Play something only when the request clearly asks for music or an app command. Never guess, never invent music to fit unclear words, and never replay something from the project memory unless the request clearly asks for it ("again", "the same", "repeat that").
say: a very short confirmation, at most 8 words, e.g. "G minor, then F minor".

The message may end with the project memory: the user's notes and the earlier requests in this project, with what was played for each. Use it to understand references such as "the same again", "that progression but slower", "the chords from before, an octave higher", "continue it" or "undo the last one, play this instead". When a request refers back, write out the full actions again; never answer with a reference. If the request does not refer back, ignore the memory."""


def validate_plan(data, default_octave=4):
    """Check a plan (from Gemini or local_plan) and turn it into actions the
    app can run:
        {"kind": "notes", "notes": [67, 70, 74], "beats": 1, "velocity": 96,
         "label": "Gm"}
        {"kind": "rest", "beats": 1, "label": "Rest"}
        {"kind": "command", "cmd": ("play",), "label": "play"}
    Anything malformed raises ValueError; nothing is half-accepted."""
    from studio import parse_command
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        raise ValueError("plan has no actions list")
    raw = data["actions"]
    if len(raw) > MAX_ACTIONS:
        raise ValueError("too many actions (%d)" % len(raw))
    out, total = [], 0.0
    has_music = any(isinstance(a, dict) and a.get("type") in ("chord", "note")
                    for a in raw)
    shift = 0          # "octave up/down" inside a musical plan moves the music
    for a in raw:
        if not isinstance(a, dict):
            raise ValueError("action is not an object")
        kind = a.get("type")
        if kind == "command":
            phrase = str(a.get("phrase") or "")
            cmd = parse_command(phrase)
            if cmd is None:
                raise ValueError("unknown command phrase %r" % phrase)
            if cmd[0] == "octave" and has_music:
                # Gemini sometimes says "octave up" and then plays the chords
                # with no octave. That would only move the pads. Apply the
                # shift to the chords and notes instead, and leave the pads.
                shift += cmd[1]
                continue
            out.append({"kind": "command", "cmd": cmd, "label": phrase})
            continue
        beats = float(a.get("beats") or 1.0)
        if not 1 / 16 - 1e-9 <= beats <= 16:
            raise ValueError("length %g beats is out of range" % beats)
        total += beats
        if total > MAX_BEATS + 1e-9:
            raise ValueError("request is longer than %d beats" % MAX_BEATS)
        if kind == "rest":
            out.append({"kind": "rest", "beats": beats, "label": "Rest"})
            continue
        octave = a.get("octave")
        octave = default_octave + shift if octave is None else int(octave)
        if not 0 <= octave <= 8:
            raise ValueError("octave %d out of range" % octave)
        vel = int(a.get("velocity") or DEFAULT_VELOCITY)
        vel = max(1, min(127, vel))
        symbol = str(a.get("symbol") or "")
        if kind == "chord":
            notes = chord_notes(symbol, octave)
            label = parse_chord(symbol)[3]
        elif kind == "note":
            notes = [note_number(symbol, octave)]
            label = _root(symbol)[2] + str(notes[0] // 12 - 1)
        else:
            raise ValueError("unknown action type %r" % kind)
        if not notes:
            raise ValueError("no notes in %r" % symbol)
        act = {"kind": "notes", "notes": notes, "beats": beats,
               "velocity": vel, "label": label}
        if kind == "chord":
            act["octave"] = octave          # notes carry it in the label (G4)
        out.append(act)
    return out


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
def wav_bytes(pcm16, sr=SR):
    """Raw 16-bit mono PCM -> a WAV file in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16)
    return buf.getvalue()


def gemini_plan(text, audio=None, context=None, timeout=12, model=None):
    """Ask Gemini for a plan. Returns the parsed JSON (not yet validated)."""
    model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    key = os.environ["GEMINI_API_KEY"]
    parts = []
    if audio:
        audio = audio[-int(MAX_AUDIO_SECONDS * SR) * 2:]
        parts.append({"inlineData": {
            "mimeType": "audio/wav",
            "data": base64.b64encode(wav_bytes(audio)).decode()}})
    context = dict(context or {})
    memory = context.pop("project_memory", "")
    ask = "Speech recognition heard: %r\n\nContext: %s" % (
        text, json.dumps(context))
    if memory:
        ask += "\n\nProject memory:\n" + memory
    parts.append({"text": ask})
    config = {"responseMimeType": "application/json",
              "responseSchema": schema(), "temperature": 0.2}
    # Least thinking the model allows: this is a lookup, not a puzzle.
    thinking = ([{"thinkingBudget": 0}] if "2.5-flash" in model else
                [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}]
                if model.startswith("gemini-3") else [])
    if thinking:
        config["thinkingConfig"] = thinking.pop(0)
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": config}
    url = ("https://generativelanguage.googleapis.com/v1beta/models/%s:"
           "generateContent" % model)

    def post(b):
        req = urllib.request.Request(
            url, data=json.dumps(b).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    while True:
        try:
            reply = post(body)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code == 400 and "thinkingConfig" in config:
                # this model does not take that setting: try the next one
                if thinking:
                    config["thinkingConfig"] = thinking.pop(0)
                else:
                    config.pop("thinkingConfig")
                continue
            raise RuntimeError("Gemini HTTP %d: %s" % (exc.code, detail))
    parts = reply["candidates"][0]["content"]["parts"]
    answer = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    return json.loads(answer)


def race_models():
    env = os.environ.get("TRAVIS_MODELS")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    first = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    return [first] + [m for m in RACE_MODELS if m != first]


def gemini_race(text, audio=None, context=None, deadline=GEMINI_DEADLINE,
                models=None, default_octave=4):
    """Ask several models at once; return (model, plan, seconds) for the
    first answer that passes validate_plan. Raises if none does in time."""
    models = models or race_models()
    results = queue.Queue()

    def ask(model):
        t0 = time.monotonic()
        try:
            plan = gemini_plan(text, audio, context, timeout=deadline,
                               model=model)
            validate_plan(plan, default_octave)    # must be usable as is
            results.put((model, plan, None, time.monotonic() - t0))
        except Exception as exc:                   # network, quota, bad JSON
            results.put((model, None, str(exc)[:200], time.monotonic() - t0))

    for m in models:
        threading.Thread(target=ask, args=(m,), daemon=True).start()
    end = time.monotonic() + deadline
    errors = []
    for _ in models:
        try:
            model, plan, err, dt = results.get(timeout=max(0.0, end - time.monotonic()))
        except queue.Empty:
            break
        if plan is not None:
            return model, plan, dt
        errors.append("%s: %s" % (model, err))
    errors.append("no answer within %gs" % deadline)
    raise RuntimeError("; ".join(errors))


# ---------------------------------------------------------------------------
# Offline fallback: the simple cases, no network
# ---------------------------------------------------------------------------
LETTERS = {"a": "a", "b": "b", "be": "b", "bee": "b", "c": "c", "see": "c",
           "sea": "c", "d": "d", "dee": "d", "e": "e", "f": "f", "eff": "f",
           "g": "g", "gee": "g", "ji": "g"}
QUALITY_WORDS = {"major": "", "minor": "m", "seven": "7", "seventh": "7",
                 "diminished": "dim", "augmented": "aug", "sus": "sus4",
                 "suspended": "sus4"}
NOTE_LEN = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
            "eight": 0.5, "8th": 0.5, "sixteenth": 0.25, "16th": 0.25}
NUMBERS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4,
           "five": 5, "six": 6, "seven": 7, "eight": 8, "1": 1, "2": 2,
           "3": 3, "4": 4}


def local_plan(text):
    """Very small parser for '<letter> [sharp|flat] [major|minor|seven]
    chord [for a <length> note|for N beats|bars], then ...'. Returns a plan
    dict in the same shape as Gemini's, or None if nothing was found."""
    from studio import parse_command
    if parse_command(text):
        return {"actions": [{"type": "command", "phrase": " ".join(text.lower().split())}],
                "say": text, "clarify": ""}
    w = text.lower().replace(",", " ").replace("-", " ").split()
    items = []                     # [kind, symbol, beats or None]
    i = 0
    while i < len(w):
        tok = w[i]
        # a chord or note: letter [sharp/flat] [quality] [chord|note]
        if tok in LETTERS and i + 1 <= len(w):
            j = i + 1
            acc = ""
            if j < len(w) and w[j] in ("sharp", "flat"):
                acc = "#" if w[j] == "sharp" else "b"
                j += 1
            qual = None
            if j < len(w) and w[j] in QUALITY_WORDS:
                qual = QUALITY_WORDS[w[j]]
                j += 1
                if j < len(w) and w[j] in ("seven", "seventh") and qual in ("", "m"):
                    qual = {"": "maj7", "m": "m7"}[qual]
                    j += 1
            is_chord = j < len(w) and w[j] == "chord"
            is_note = j < len(w) and w[j] == "note"
            # "a" and "an" are usually just articles: only count them when
            # something musical follows
            if tok in ("a",) and not (acc or qual is not None or is_chord or is_note):
                i += 1
                continue
            if acc or qual is not None or is_chord or is_note:
                sym = LETTERS[tok].upper() + acc
                if is_note and qual is None:
                    items.append(["note", sym, None])
                else:
                    items.append(["chord", sym + (qual or ""), None])
                i = j + (1 if (is_chord or is_note) else 0)
                continue
        if tok in ("rest", "silence", "pause"):
            items.append(["rest", "", None])
            i += 1
            continue
        # lengths: "<quarter> note", "dotted quarter", "N beats", "N bars"
        half_beat = tok == "half" and i + 1 < len(w) and w[i + 1] in (
            "a", "beat", "bar", "measure")
        if tok in NOTE_LEN and not half_beat and items and items[-1][2] is None:
            b = NOTE_LEN[tok]
            if i > 0 and w[i - 1] == "dotted":
                b *= 1.5
            items[-1][2] = b
        elif tok in ("beat", "beats", "bar", "bars", "measure", "measures") \
                and items and items[-1][2] is None:
            n = NUMBERS.get(w[i - 1], 1) if i > 0 else 1
            if (i > 0 and w[i - 1] == "half") or (i > 1 and w[i - 2] == "half"):
                n = 0.5
            items[-1][2] = n * (4 if tok.startswith(("bar", "measure")) else 1)
        i += 1
    if not items:
        return None
    last = 1.0
    actions = []
    for kind, sym, beats in items:
        beats = beats if beats is not None else last
        last = beats
        a = {"type": kind, "beats": beats}
        if sym:
            a["symbol"] = sym
        actions.append(a)
    return {"actions": actions, "say": "", "clarify": ""}


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
def plan_start(now, tempo, playing_since=None, busy_until=0.0, lead=0.08):
    """When the next request should start. Back to back with one that is
    still playing; else, while FL Studio plays, on the next bar line (up to
    `lead` seconds late still counts as this bar); else right away."""
    if busy_until > now:
        return busy_until
    if playing_since is not None:
        bar = 240.0 / tempo
        k = math.ceil((now - playing_since - lead) / bar)
        return max(playing_since + k * bar, now)
    return now + 0.03


class Sequencer:
    """Notes at exact times. schedule() adds a request, due(now) returns the
    MIDI messages whose time has come (called from a fast thread)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = []          # (time, order, kind, note, velocity)
        self.sounding = collections.Counter()
        self.end = 0.0
        self._order = 0

    def schedule(self, actions, start, tempo):
        """Returns [(t_on, t_off)] for each sounding action (None for rests
        and commands), and the end time."""
        beat = 60.0 / tempo
        t = start
        times = []
        with self.lock:
            for a in actions:
                if a["kind"] == "command":
                    times.append(None)
                    continue
                dur = a["beats"] * beat
                if a["kind"] == "notes":
                    off = t + max(0.03, dur * GATE)
                    for n in a["notes"]:
                        self._order += 1
                        self.events.append((t, 1, self._order, "on", n, a["velocity"]))
                        self._order += 1
                        self.events.append((off, 0, self._order, "off", n, 0))
                    times.append((t, t + dur))
                else:
                    times.append((t, t + dur))
                t += dur
            self.end = max(self.end, t)
            self.events.sort()
        return times, t

    def due(self, now):
        """[("on", note, velocity) | ("off", note)] due by `now`. At the same
        moment, note-offs go first so a repeated note re-strikes."""
        out = []
        with self.lock:
            while self.events and self.events[0][0] <= now:
                _, _, _, kind, note, vel = self.events.pop(0)
                if kind == "on":
                    if self.sounding[note]:
                        out.append(("off", note))
                    self.sounding[note] += 1
                    out.append(("on", note, vel))
                elif self.sounding[note]:
                    self.sounding[note] -= 1
                    if not self.sounding[note]:
                        out.append(("off", note))
        return out

    def busy_until(self):
        with self.lock:
            return self.end

    def cancel(self):
        """Drop everything still to come; returns the note-offs owed."""
        with self.lock:
            self.events = []
            offs = [("off", n) for n, c in self.sounding.items() if c]
            self.sounding.clear()
            self.end = 0.0
        return offs


# ---------------------------------------------------------------------------
# The assistant: wake word -> Gemini -> plan, plus what the UI shows
# ---------------------------------------------------------------------------
class Travis:
    """Fed by the voice thread (feed_partial / feed_final). Puts
    ("travis_plan", actions, info) on `out_queue` for app.py to run.
    context_fn() returns what Gemini should know (tempo, key, ...)."""

    def __init__(self, out_queue, context_fn=None, use_gemini=None):
        self.q = out_queue
        self.context_fn = context_fn or (lambda: {})
        self.use_gemini = (bool(os.environ.get("GEMINI_API_KEY"))
                           if use_gemini is None else use_gemini)
        self.lock = threading.Lock()
        self.armed_until = 0.0
        self.wake_heard = False   # wake word in the sentence being spoken
        self.history = collections.deque(maxlen=4)
        self.card = None
        self.memory = None        # a project_memory.ProjectMemory, set by app.py

    # ---- from the voice thread -------------------------------------------
    def feed_partial(self, partial):
        """Called with Vosk's partial text. True while the current sentence
        is meant for Travis, so the command grammar should not act on it."""
        if find_wake(partial) is not None:
            self.wake_heard = True
        return self.wake_heard or self.listening()

    def listening(self, now=None):
        return (now or time.monotonic()) < self.armed_until

    def feed_final(self, text, audio=None):
        """A finished sentence. Returns True if Travis took it."""
        self.wake_heard = False
        now = time.monotonic()
        rest = find_wake(text)
        if text and (rest is not None or self.listening(now)
                     or os.environ.get("TRAVIS_DEBUG")):
            print("[travis] heard: %r" % text)
        if self.listening(now) and text.strip() and rest is None:
            self.armed_until = 0.0
            self._take(text, audio)
            return True
        if rest is None:
            return False
        if rest:
            self._take(rest, audio)
        else:
            self.armed_until = now + LISTEN_SECONDS
            self._set_card(phase="listening", text="", chips=[], times=[])
        return True

    def _take(self, text, audio):
        """A request. "help" shows what Travis does; the rest goes to work."""
        if " ".join(text.lower().replace("?", " ").split()) in HELP_ASKS:
            self.armed_until = 0.0
            self._set_card(phase="help", text=text, chips=[], times=[])
            return
        self.submit(text, audio)

    # ---- work ------------------------------------------------------------
    def submit(self, text, audio=None):
        self._set_card(phase="thinking", text=text, chips=[], times=[])
        threading.Thread(target=self._work, args=(text, audio), daemon=True).start()

    def _work(self, text, audio):
        ctx = dict(self.context_fn())
        ctx["previous_requests"] = list(self.history)
        if self.memory is not None:
            ctx["project_memory"] = self.memory.context()
        octave = int(ctx.get("default_octave", 4))
        source, plan, error = "Offline", None, None
        if self.use_gemini:
            try:
                model, plan, dt = gemini_race(text, audio, ctx,
                                              default_octave=octave)
                source = "Gemini"
                print("[travis] %s %.2fs: %s" % (model, dt, json.dumps(plan)))
            except Exception as exc:              # network, quota, bad JSON
                error = str(exc)
                print("[travis] Gemini failed (%s); trying offline" % exc)
        if plan is None:
            plan = local_plan(text)
        if plan is None:
            self._set_card(phase="error", text=text,
                           msg="Didn't catch that. Try “Travis, G minor chord”.")
            return
        if plan.get("clarify") and not plan.get("actions"):
            self._set_card(phase="error", text=text, msg=plan["clarify"],
                           source=source)
            return
        try:
            actions = validate_plan(plan, octave)
        except ValueError as exc:
            print("[travis] plan rejected: %s" % exc)
            self._set_card(phase="error", text=text, source=source,
                           msg="Couldn't use that answer (%s)." % exc)
            return
        if not actions:
            self._set_card(phase="error", text=text, source=source,
                           msg=plan.get("clarify") or "Nothing to play.")
            return
        self.history.append(text)
        if self.memory is not None:
            self.memory.add(text, [
                (a["label"] + (" oct %d" % a["octave"] if "octave" in a else ""),
                 "" if a["kind"] == "command"
                 else beats_label(a["beats"])) for a in actions],
                source, ctx)
        self.q.put(("travis_plan", actions,
                    {"text": text, "say": plan.get("say") or "",
                     "source": source, "error": error}))

    # ---- called by app.py once the plan is on the timeline ------------------
    def scheduled(self, actions, times, info):
        chips = [{"label": a["label"],
                  "dur": beats_label(a["beats"]) if "beats" in a else "command",
                  "kind": a["kind"]} for a in actions]
        self._set_card(phase="playing", text=info.get("text", ""),
                       say=info.get("say", ""), chips=chips, times=times,
                       source=info.get("source", ""))

    def _set_card(self, **kw):
        with self.lock:
            card = {"phase": "", "text": "", "say": "", "chips": [], "times": [],
                    "source": "Gemini" if self.use_gemini else "Offline",
                    "msg": "", "t": time.monotonic()}
            card.update(kw)
            self.card = card

    def snapshot(self, now):
        """JSON-able card for the UI, or None when nothing is shown."""
        with self.lock:
            c = self.card
            if c is None:
                return None
            if c["phase"] == "listening" and not self.listening(now):
                self.card = None
                return None
            ends = [t[1] for t in c["times"] if t]
            last = max(ends) if ends else c["t"]
            if c["phase"] in ("playing", "error") and now > max(last, c["t"]) + CARD_SECONDS:
                self.card = None
                return None
            if c["phase"] == "help" and now > c["t"] + HELP_SECONDS:
                self.card = None
                return None
            active = None
            for i, t in enumerate(c["times"]):
                if t and t[0] <= now < t[1]:
                    active = i
            waiting = c["phase"] == "playing" and ends and now < min(
                t[0] for t in c["times"] if t)
            return {"phase": c["phase"], "text": c["text"], "say": c["say"],
                    "chips": c["chips"], "active": active, "source": c["source"],
                    "msg": c["msg"], "waiting": bool(waiting),
                    "done": c["phase"] == "playing" and bool(ends) and now >= last}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or \
        "put a g minor chord for a quarter note followed by an f minor chord"
    print("Request:", request)
    if os.environ.get("GEMINI_API_KEY"):
        print("Asking at once:", ", ".join(race_models()))
        try:
            model, raw, dt = gemini_race(request, None, {
                "tempo": 120, "key": "C major", "default_octave": 4})
            print("First good answer: %s in %.2fs" % (model, dt))
        except RuntimeError as exc:
            print("Gemini failed (%s)\nOffline parser instead:" % exc)
            raw = local_plan(request)
    else:
        raw = local_plan(request)
        print("No GEMINI_API_KEY: offline parser:")
    print(json.dumps(raw, indent=2))
    if raw:
        for a in validate_plan(raw):
            if a["kind"] == "notes":
                print("  %-8s %-15s %s" % (a["label"], beats_label(a["beats"]),
                                           " ".join(note_name(n) for n in a["notes"])))
            else:
                print("  %-8s %s" % (a["label"], a.get("beats", "")))
