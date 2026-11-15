"""Checks for studio.py, the screen layout and the FL Studio script.
Needs no camera, microphone or FL Studio:  python tests/test_studio.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import types

import mido

import app
import studio as st
from bridge import FLBridge


class FakeBridge(FLBridge):
    """Records command messages instead of sending them."""

    def __init__(self):
        super().__init__(dry_run=True)
        self.sent = []

    def _send(self, msg):
        if msg.type == "control_change":
            self.sent.append((msg.control, msg.value))


# --- voice parsing: every phrase the recogniser can return maps to a command
for phrase in st.voice_phrases():
    assert st.parse_command(phrase) is not None, phrase
assert st.parse_command("tempo ninety") == ("tempo", 90)
assert st.parse_command("mute hat") == ("mute", 2, True)
assert st.parse_command("trap beat") == ("beat", "trap")
assert st.parse_command("drums") == ("mode", "drums")
assert st.parse_command("hum") == ("mode", "hum")
assert st.parse_command("[unk]") is None

# --- conversation must not trigger commands
def heard(text, conf=1.0, length=0.35):
    words, t = [], 0.0
    for w in text.split():
        words.append({"word": w, "conf": conf, "start": t, "end": t + length})
        t += length + 0.05
    return {"text": text, "result": words}


ON = {"wake": True, "wake_word": "computer", "min_conf": 0.85}
OFF = dict(ON, wake=False)
assert st.accept_voice(heard("computer play"), ON) == ("play", ("play",))
assert st.accept_voice(heard("computer tempo ninety"), ON) == ("tempo ninety", ("tempo", 90))
assert st.accept_voice(heard("play"), ON) is None                 # no wake word
assert st.accept_voice(heard("computer"), ON) is None             # wake word alone
assert st.accept_voice(heard("computer play", conf=0.6), ON) is None   # unsure
assert st.accept_voice(heard("computer [unk] play"), ON) is None  # other words
assert st.accept_voice(heard("[unk] play [unk]"), OFF) is None
assert st.accept_voice(heard("computer play stop"), ON) is None   # two commands
assert st.accept_voice(heard("computer play", length=0.03), ON) is None  # too short
assert st.accept_voice(heard("computer play", length=2.5), ON) is None   # too long
assert st.accept_voice(heard("play"), OFF) == ("play", ("play",)) # wake word off
assert st.accept_voice(heard("computer play"), OFF) == ("play", ("play",))
assert st.accept_voice({"text": ""}, OFF) is None
g = st.grammar("computer")
assert "computer play" in g and "play" in g and "[unk]" in g

# --- conversation must not trigger commands
def heard(text, conf=1.0, length=0.35):
    words, t = [], 0.0
    for w in text.split():
        words.append({"word": w, "conf": conf, "start": t, "end": t + length})
        t += length + 0.05
    return {"text": text, "result": words}


ON = {"wake": True, "wake_word": "mozart", "min_conf": 0.85}
OFF = dict(ON, wake=False)
assert st.DEFAULT_VOICE["wake_word"] == "mozart"
assert st.accept_voice(heard("mozart play"), ON) == ("play", ("play",))
assert st.accept_voice(heard("mozart tempo ninety"), ON) == ("tempo ninety", ("tempo", 90))
# "Mozart play mode" is the mode switch, not the transport's "play"
assert st.accept_voice(heard("mozart play mode"), ON) == ("play mode", ("pointer", "eyes"))
assert st.accept_voice(heard("mozart navigate mode"), ON) == ("navigate mode", ("pointer", "head"))
assert st.accept_voice(heard("mozart back to normal"), ON) == ("back to normal", ("pointer", "head"))
assert "mozart play mode" in st.grammar("mozart")
# the wake word is spotted while the sentence is still being spoken
assert st.woke("mozart", ON) and st.woke("mozart tempo", ON)
assert not st.woke("tempo mozart", ON) and not st.woke("", ON)
assert not st.woke("mozart play", {**ON, "wake": False})
# "Mozart help" opens the sheet, "Mozart close" shuts it, and the sheet
# lists every phrase the recogniser accepts, and nothing it does not
assert st.accept_voice(heard("mozart help"), ON) == ("help", ("help", True))
assert st.accept_voice(heard("mozart close"), ON) == ("close", ("help", False))
assert set(st.voice_phrases()) == st.help_phrases(), \
    set(st.voice_phrases()) ^ st.help_phrases()
sheet = st.help_sheet()
assert sum(len(g["items"]) for g in sheet) <= 24     # fits on one screen
assert st.accept_voice(heard("play"), ON) is None                 # no wake word
assert st.accept_voice(heard("mozart"), ON) is None               # wake word alone
assert st.accept_voice(heard("mozart play", conf=0.6), ON) is None     # unsure
assert st.accept_voice(heard("mozart [unk] play"), ON) is None    # other words
assert st.accept_voice(heard("[unk] play [unk]"), OFF) is None
assert st.accept_voice(heard("mozart play stop"), ON) is None     # two commands
assert st.accept_voice(heard("mozart play", length=0.03), ON) is None  # too short
assert st.accept_voice(heard("mozart play", length=2.5), ON) is None   # too long
assert st.accept_voice(heard("play"), OFF) == ("play", ("play",)) # wake word off
assert st.accept_voice(heard("mozart play"), OFF) == ("play", ("play",))
assert st.accept_voice({"text": ""}, OFF) is None
g = st.grammar("mozart")
assert "mozart play" in g and "play" in g and "[unk]" in g


# The app falls back to another wake word if the speech model lacks ours.
class FakeVosk:
    known = {"computer"}

    class KaldiRecognizer:
        def __init__(self, model, rate, phrases):
            import json as _j, os as _o
            for w in sorted({w for p in _j.loads(phrases) for w in p.split()}):
                if w in ("mozart", "unmute") and w not in FakeVosk.known:
                    _o.write(2, ("WARNING (VoskAPI) Ignoring word missing in "
                                 "vocabulary: '%s'\n" % w).encode())


rec, missing = st._build_recognizer(FakeVosk, None, "mozart")
assert missing == ["mozart"], missing
FakeVosk.known = {"mozart"}
assert st._build_recognizer(FakeVosk, None, "mozart")[1] == []

# --- the window's dock buttons are the ones the app knows
import os as _os, re as _re
_js = open(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "web", "app.js")).read()
assert set(_re.findall(r'\["button", "(\w+)"\]', _js)) == set(app.BUTTON_COMMANDS)
assert "dwell" in app.DEFAULTS and app.MODES == ["pads", "drums", "hum", "mix"]

# --- actions produce the expected MIDI
fl = FakeBridge()
s = st.Studio(fl)
s.run(("step", 0, 4))
s.run(("play",))
s.run(("tempo", 90))
s.run(("mute", 2, True))
s.run(("volume_by", 1, 13))
assert fl.sent == [(40, 9), (1, 0), (4, 30), (52, 1), (61, 113)], fl.sent
fl.sent.clear()
pattern = {k: [c == "1" for c in v] for k, v in st.PRESETS["house"].items()}
s.run(("pattern", pattern, "house"))
hits = sum(sum(r) for r in pattern.values())
assert len(fl.sent) == hits - 1          # kick step 5 was already on
assert st.validate_pattern({n: [1, 0] * 8 for n in st.CHANNELS})["kick"][0] is True
for style in st.STYLES:
    assert all(len(row) == 16 for row in st.PRESETS[style].values())

# --- voice guard
va = st.VoiceActivity()
quiet = (b"\x10\x00" * 800)
loud = (b"\x00\x40" * 800)
for _ in range(20):
    va.feed(quiet)
assert not va.speaking()
va.feed(loud)
assert va.speaking()

# --- FL Studio script, run against stand-ins for FL Studio's modules
log = []
state = {"playing": False, "muted": [False] * 5, "loop": 1, "sel": 0,
         "bits": {}}
ch = types.ModuleType("channels")
ch.channelCount = lambda: 5
ch.selectedChannel = lambda: state["sel"]
ch.selectOneChannel = lambda i: state.__setitem__("sel", i)
ch.getChannelName = lambda i: "ch%d" % i
ch.getGridBit = lambda i, st_: state["bits"].get((i, st_), False)
ch.setGridBit = lambda i, st_, v: (state["bits"].__setitem__((i, st_), bool(v)),
                                   log.append(("grid", i, st_, bool(v))))
ch.isChannelMuted = lambda i: state["muted"][i]
ch.muteChannel = lambda i: (state["muted"].__setitem__(i, not state["muted"][i]),
                            log.append(("mute", i)))
ch.getChannelVolume = lambda i: 0.78
ch.setChannelVolume = lambda i, v: log.append(("vol", i, round(v, 2)))
tr = types.ModuleType("transport")
tr.getLoopMode = lambda: state["loop"]
tr.setLoopMode = lambda: state.__setitem__("loop", 0)
tr.isPlaying = lambda: state["playing"]
tr.isRecording = lambda: False
tr.start = lambda: (state.__setitem__("playing", True), log.append(("start",)))
tr.stop = lambda: log.append(("stop",))
tr.record = lambda: log.append(("record",))
ge = types.ModuleType("general")
ge.processRECEvent = lambda e, v, f: log.append(("tempo", v))
ge.undoUp = lambda: log.append(("undo",))
mi = types.ModuleType("midi")
mi.REC_Tempo, mi.REC_Control, mi.REC_UpdateControl = 5, 1, 16
ui = types.ModuleType("ui")
ui.setHintMsg = lambda m: None
ui.showWindow = lambda i: log.append(("show", i))
ui.setFocused = lambda i: state.__setitem__("focus", i)
ui.getFocused = lambda i: state.get("focus") == i
ui.delete = lambda: log.append(("delete",))
pa = types.ModuleType("patterns")
sys.modules.update(channels=ch, transport=tr, general=ge, midi=mi, ui=ui,
                   patterns=pa)
sys.path.insert(0, "fl_script")
import device_HandsFreeStudio as script


class Event:
    def __init__(self, cc, value, status=0xBF):
        self.status, self.data1, self.data2, self.handled = status, cc, value, False


# Everything the bridge can send must be understood by the script.
fl = FakeBridge()
fl.ping(); fl.play(); fl.play(); fl.set_tempo(90); fl.select_channel(4)
fl.drum_step(0, 4, True); fl.drum_mute(2, True); fl.drum_mute(2, True)
fl.drum_volume(1, 113); fl.clear_drums(4); fl.toggle_step(3); fl.stop()
for cc, value in fl.sent:
    e = Event(cc, value)
    script.OnMidiMsg(e)
    assert e.handled
assert ("start",) in log and log.count(("start",)) == 1     # second play: no-op
assert ("tempo", 90000) in log
assert state["sel"] == 4 and state["loop"] == 0
assert ("grid", 0, 4, True) in log
assert log.count(("mute", 2)) == 1                           # second mute: no-op
assert ("vol", 1, 0.89) in log
assert ("grid", 4, 3, True) in log                           # toggle on selected
# clear notes: step 1 opens the piano roll, step 2 deletes, and only if the
# piano roll has the focus
script.OnMidiMsg(Event(20, 4))
assert ("show", 3) in log and ("delete",) not in log
script.OnMidiMsg(Event(21, 0))
assert log.count(("delete",)) == 1
state["focus"] = None
ui.setFocused = lambda i: None                               # focus fails
script.OnMidiMsg(Event(20, 4))
script.OnMidiMsg(Event(21, 0))
assert log.count(("delete",)) == 1                           # nothing deleted
note = Event(60, 100, status=0x90)                           # notes are left alone
script.OnMidiMsg(note)
assert not note.handled

print("studio tests passed")
