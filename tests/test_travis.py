"""Checks for travis.py (the Gemini voice assistant). Needs no microphone,
network or FL Studio:  python tests/test_travis.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import json
import queue
import time
import urllib.request

import travis as tv
from bridge import note_name


def names(notes):
    return [note_name(n) for n in notes]


# --- chords are spelled here, not by Gemini
assert names(tv.chord_notes("Gm")) == ["G4", "A#4", "D5"]
assert names(tv.chord_notes("Fm")) == ["F4", "G#4", "C5"]
assert names(tv.chord_notes("G minor")) == ["G4", "A#4", "D5"]
assert names(tv.chord_notes("Bbmaj7")) == ["A#4", "D5", "F5", "A5"]
assert names(tv.chord_notes("CM7")) == names(tv.chord_notes("Cmaj7"))
assert names(tv.chord_notes("Cm7")) == ["C4", "D#4", "G4", "A#4"]
assert names(tv.chord_notes("D-7")) == names(tv.chord_notes("Dm7"))
assert names(tv.chord_notes("C/E")) == ["E3", "C4", "G4"]
assert names(tv.chord_notes("G/B")) == ["B3", "G4", "D5"]
assert names(tv.chord_notes("E5", 2)) == ["E2", "B2"]
assert tv.parse_chord("bbm7b5")[3] == "Bbm7b5"
assert tv.parse_chord("F sharp minor")[3] == "F#m"
for bad in ("H", "Gzz", "", "minor"):
    try:
        tv.parse_chord(bad)
        raise AssertionError("accepted " + bad)
    except ValueError:
        pass
assert tv.note_number("C4") == 60 and tv.note_number("Bb3") == 58
assert tv.note_number("F#") == 66 and tv.note_number("E flat", 5) == 75
assert tv.beats_label(1) == "quarter" and tv.beats_label(0.5) == "8th"
assert tv.beats_label(8) == "2 bars" and tv.beats_label(1.5) == "dotted quarter"

# --- wake word
assert tv.find_wake("travis put a g minor chord") == "put a g minor chord"
assert tv.find_wake("hey travis") == ""
assert tv.find_wake("okay travis please play") == "play"
assert tv.find_wake("trav is stop") == "stop"
assert tv.find_wake("play") is None and tv.find_wake("") is None
# only at the start of a sentence: the name inside conversation is ignored
assert tv.find_wake("they are the same travis for that make a scene") is None
assert tv.find_wake("i want a d minor and travis our okay") is None
assert tv.find_wake("oh travis play the chord") == "play the chord"

# --- offline parser: the example from the pitch
p = tv.local_plan("put a g minor chord for a quarter note followed by an f minor chord")
acts = tv.validate_plan(p)
assert [(a["label"], a["beats"]) for a in acts] == [("Gm", 1.0), ("Fm", 1.0)], acts
p = tv.local_plan("c sharp minor seven chord for a half note then rest for a beat "
                  "then e flat note for half a beat")
assert [(a["label"], a["beats"]) for a in tv.validate_plan(p)] == \
    [("C#m7", 2.0), ("Rest", 1.0), ("Eb4", 0.5)]
p = tv.local_plan("an a minor chord for two bars")
assert [(a["label"], a["beats"]) for a in tv.validate_plan(p)] == [("Am", 8.0)]
assert tv.local_plan("play")["actions"][0]["phrase"] == "play"
assert tv.local_plan("what is the weather") is None

# --- validating a Gemini answer
good = {"actions": [
    {"type": "command", "phrase": "tempo ninety"},
    {"type": "chord", "symbol": "Gm", "beats": 1},
    {"type": "chord", "symbol": "Fm", "beats": 1, "octave": 3, "velocity": 200},
    {"type": "rest", "beats": 0.5},
    {"type": "note", "symbol": "D5", "beats": 0.5}], "say": "ok", "clarify": ""}
acts = tv.validate_plan(good)
assert acts[0] == {"kind": "command", "cmd": ("tempo", 90), "label": "tempo ninety"}
assert names(acts[2]["notes"]) == ["F3", "G#3", "C4"] and acts[2]["velocity"] == 127
assert acts[4]["notes"] == [74]
for bad in ({"actions": "x"}, {"actions": [{"type": "chord", "symbol": "Q"}]},
            {"actions": [{"type": "chord", "symbol": "C", "beats": 100}]},
            {"actions": [{"type": "command", "phrase": "delete everything"}]},
            {"actions": [{"type": "explode"}]},
            {"actions": [{"type": "note", "symbol": "C", "beats": 4}] * 17}):
    try:
        tv.validate_plan(bad)
        raise AssertionError("accepted %r" % bad)
    except (ValueError, TypeError):
        pass
json.dumps(tv.schema())                       # sendable as-is

# --- timeline
assert tv.plan_start(10.0, 120) == 10.03                     # stopped: now
assert tv.plan_start(10.0, 120, busy_until=11.0) == 11.0      # after the last one
assert tv.plan_start(10.5, 120, playing_since=10.0) == 12.0   # next bar (2 s at 120)
assert tv.plan_start(12.05, 120, playing_since=10.0) == 12.05  # just missed: this bar
seq = tv.Sequencer()
times, end = seq.schedule(acts, 100.0, 120)                  # 0.5 s per beat
assert times[0] is None and times[1] == (100.0, 100.5) and times[2] == (100.5, 101.0)
assert times[3] == (101.0, 101.25) and end == 101.5
assert seq.due(99.9) == []
assert seq.due(100.0) == [("on", 67, 96), ("on", 70, 96), ("on", 74, 96)]
msgs = seq.due(100.5)                        # Gm off before Fm on
assert [m[0] for m in msgs] == ["off"] * 3 + ["on"] * 3, msgs
# the same note twice in a row re-strikes
seq2 = tv.Sequencer()
two = tv.validate_plan({"actions": [{"type": "note", "symbol": "C4"}] * 2})
seq2.schedule(two, 0.0, 120)
assert seq2.due(0.0) == [("on", 60, 96)]
assert seq2.due(0.5) == [("off", 60), ("on", 60, 96)]
assert seq2.due(1.0) == [("off", 60)]
seq2.schedule(two, 5.0, 120)
seq2.due(5.0)
assert seq2.cancel() == [("off", 60)] and seq2.due(9.0) == []

# --- the assistant, end to end without the network
q = queue.Queue()
t = tv.Travis(q, context_fn=lambda: {"default_octave": 4}, use_gemini=False)
assert not t.feed_final("play")                               # not for Travis
assert t.feed_final("hey travis")                             # arms it
assert t.listening() and t.snapshot(time.monotonic())["phase"] == "listening"
assert t.feed_final("g minor chord for a quarter note then f minor chord")
kind, acts, info = q.get(timeout=2)
assert kind == "travis_plan" and [a["label"] for a in acts] == ["Gm", "Fm"]
assert info["source"] == "Offline"
times, _ = tv.Sequencer().schedule(acts, time.monotonic() + 0.2, 120)
t.scheduled(acts, times, info)
snap = t.snapshot(time.monotonic())
assert snap["phase"] == "playing" and snap["waiting"] and snap["chips"][0] == \
    {"label": "Gm", "dur": "quarter", "kind": "notes"}
assert t.snapshot(time.monotonic() + 0.3)["active"] == 0
assert t.snapshot(time.monotonic() + 0.8)["active"] == 1
assert t.snapshot(time.monotonic() + 60) is None              # card goes away
assert t.feed_partial("travis put a")          # grammar commands held back
assert not t.feed_final("")
assert not t.feed_partial("")
t.feed_final("travis blah blah")
time.sleep(0.2)
assert t.snapshot(time.monotonic())["phase"] == "error"

# --- the Gemini call: request shape and reply parsing (no network)
seen = {}


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=0):
    seen["body"] = json.loads(req.data)
    seen["key"] = req.headers.get("X-goog-api-key")
    plan = {"actions": [{"type": "chord", "symbol": "Gm", "beats": 1}],
            "say": "G minor", "clarify": ""}
    return Reply(json.dumps({"candidates": [{"content": {"parts": [
        {"text": json.dumps(plan)}]}}]}).encode())


import os
os.environ["GEMINI_API_KEY"] = "test-key"
real = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
try:
    plan = tv.gemini_plan("g minor quarter", b"\0\0" * 1600, {"tempo": 120})
finally:
    urllib.request.urlopen = real
assert plan["actions"][0]["symbol"] == "Gm"
body = seen["body"]
assert seen["key"] == "test-key"
assert body["generationConfig"]["responseMimeType"] == "application/json"
assert body["generationConfig"]["responseSchema"]["required"] == ["actions", "say", "clarify"]
assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "audio/wav"
assert "g minor quarter" in body["contents"][0]["parts"][1]["text"]

# "Travis help" shows the help card and asks Gemini nothing
import queue as _hq, time as _ht
hq = _hq.Queue()
ht = tv.Travis(hq, use_gemini=False)
assert ht.feed_final("travis help") is True
assert ht.snapshot(_ht.monotonic())["phase"] == "help" and hq.empty()
assert ht.feed_final("travis") is True and ht.listening()     # wake, pause,
assert ht.feed_final("what can you do") is True               # then ask
assert ht.snapshot(_ht.monotonic())["phase"] == "help" and not ht.listening()
assert ht.snapshot(_ht.monotonic() + tv.HELP_SECONDS + 1) is None   # goes away
assert hq.empty()

print("travis tests passed")
