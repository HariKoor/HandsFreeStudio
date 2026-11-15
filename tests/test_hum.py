"""Checks for hum.py using computer-generated tones:  python tests/test_hum.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from hum import HumEngine, detect_pitch, SR

BLOCK = 400                      # 25 ms, same as the microphone stream
rng = np.random.default_rng(1)


def tone(hz, seconds, level=3000.0):
    """A hum-like tone: fundamental plus two harmonics and a little noise."""
    t = np.arange(int(seconds * SR)) / SR
    x = (np.sin(2 * np.pi * hz * t) + 0.5 * np.sin(2 * np.pi * 2 * hz * t)
         + 0.25 * np.sin(2 * np.pi * 3 * hz * t))
    x = x * level / 1.75 + rng.normal(0, 40, len(t))
    return x.astype(np.int16)


def silence(seconds):
    return rng.normal(0, 40, int(seconds * SR)).astype(np.int16)


# --- pitch detection across the humming range, no octave errors
for hz in (90, 110, 147, 196, 220, 262, 330, 440, 523, 700):
    got = detect_pitch(tone(hz, 0.064)[:1024])
    assert got is not None and abs(got - hz) / hz < 0.02, (hz, got)
assert detect_pitch(silence(0.064)[:1024]) is None


def run(parts, tempo=120, scale="major", **kw):
    """Feed audio through the engine in simulated time. Returns the engine
    and the list of (time, message) it sent."""
    eng = HumEngine(tempo=tempo, root="C", scale=scale, octave_shift=0,
                    latency=0.0, **kw)
    eng.set_tempo(tempo, 0.0)
    audio = np.concatenate(parts + [silence(2.6)])
    sent = []
    for i in range(0, len(audio) - BLOCK + 1, BLOCK):
        t = i / SR
        eng.process(audio[i:i + BLOCK], t)
        for step in range(5):                       # scheduler ticks
            now = t + BLOCK / SR * (step + 1) / 5
            for msg in eng.due(now):
                sent.append((round(now, 3), msg))
    return eng, sent


def notes(sent):
    """[(note, start_sixteenth, length_sixteenths)] at tempo 120 (125 ms).
    The start has the one-bar delay taken off."""
    out, on = [], {}
    for t, msg in sent:
        if msg[0] == "on":
            on[msg[1]] = t
        else:
            start = on.pop(msg[1])
            out.append((msg[1], start / 0.125 - 16, (t - start) / 0.125))
    assert not on, "a note was never turned off"
    return out


S = 0.125   # one sixteenth at 120 bpm

TOL = 0.06      # scheduler tick in the test is 5 ms = 0.04 sixteenths


def close(a, b):
    return abs(a - b) < TOL


# --- one note: A3 (220 Hz) hummed for ~2 sixteenths, starting just before
#     the 4th grid line. Lands on step 4, length exactly 2. A is in C major.
eng, sent = run([silence(0.47), tone(220, 0.26)])
got = notes(sent)
assert len(got) == 1, got
assert got[0][0] == 57 and close(got[0][1], 4) and close(got[0][2], 2), got

# --- starting just after a grid line still snaps back to it
eng, sent = run([silence(0.53), tone(220, 0.36)])
got = notes(sent)
assert close(got[0][1], 4) and close(got[0][2], 3), got

# --- a very short blip becomes exactly one sixteenth (the minimum)
eng, sent = run([silence(0.5), tone(262, 0.07)])
got = notes(sent)
assert len(got) == 1 and got[0][0] == 60 and close(got[0][2], 1), got

# --- an out-of-scale pitch snaps to the scale: C#4 (277 Hz) -> C4 or D4
eng, sent = run([silence(0.5), tone(277.2, 0.25)])
assert notes(sent)[0][0] in (60, 62)

# --- legato: E4 then G4 with no gap -> two notes, back to back
eng, sent = run([silence(0.5), tone(329.6, 0.25), tone(392.0, 0.25)])
got = notes(sent)
assert [n for n, _, _ in got] == [64, 67], got
assert close(got[0][1], 4) and close(got[0][2], 2), got
assert close(got[1][1], 6) and close(got[1][2], 2), got

# --- repeated note with a short gap -> two separate notes
eng, sent = run([silence(0.5), tone(262, 0.2), silence(0.17), tone(262, 0.2)])
got = notes(sent)
assert [n for n, _, _ in got] == [60, 60], got

# --- random notes: start and length always land where expected
for _ in range(40):
    hz = float(rng.choice([131, 165, 196, 220, 262, 330, 392]))
    g, n = int(rng.integers(3, 9)), int(rng.integers(1, 7))
    eng, sent = run([silence(g * S + rng.uniform(-0.035, 0.035)),
                     tone(hz, n * S + rng.uniform(-0.035, 0.035))])
    got = notes(sent)
    assert len(got) == 1 and close(got[0][1], g) and close(got[0][2], n), \
        (hz, g, n, got)

# --- a long, wobbly hum is ONE long note, not a string of sixteenths
def wobbly(hz, seconds, level=3000.0):
    t = np.arange(int(seconds * SR)) / SR
    f = hz * 2 ** (0.45 * np.sin(2 * np.pi * 5.5 * t) / 12)      # vibrato
    ph = 2 * np.pi * np.cumsum(f) / SR
    amp = 1 + 0.5 * np.sin(2 * np.pi * 3.1 * t)                  # tremolo
    x = (np.sin(ph) + 0.5 * np.sin(2 * ph) + 0.25 * np.sin(3 * ph)) * amp
    return (x * level / 2.6 + rng.normal(0, 40, len(t))).astype(np.int16)


for hz, n in ((196.0, 8), (262.0, 4), (146.8, 16)):
    eng, sent = run([silence(0.5), wobbly(hz, n * S)], scale="blues")
    got = notes(sent)
    assert len(got) == 1 and close(got[0][1], 4) and close(got[0][2], n), \
        (hz, n, got)

# --- a 40 ms break in the voice does not split the note
eng, sent = run([silence(0.5), tone(220, 0.4), silence(0.04), tone(220, 0.56)])
got = notes(sent)
assert len(got) == 1 and close(got[0][2], 8), got

# --- the screen shows a held note as one block
eng = HumEngine(tempo=120, root="C", scale="major", octave_shift=0, latency=0.0)
eng.set_tempo(120, 0.0)
audio = np.concatenate([silence(0.5), tone(262, 0.5), silence(0.3)])
for i in range(0, len(audio) - BLOCK + 1, BLOCK):
    eng.process(audio[i:i + BLOCK], i / SR)
assert eng.snapshot(1.2)["runs"] == [(4, 4, 60)], eng.snapshot(1.2)["runs"]

# --- only one note sounds at a time
depth = 0
for _, msg in sent:
    depth += 1 if msg[0] == "on" else -1
    assert 0 <= depth <= 1

# --- room noise and silence produce nothing
eng, sent = run([silence(2.0)])
assert sent == []

# --- transposing and the screen snapshot
eng = HumEngine(octave_shift=1)
eng.set_tempo(120, 0.0)
snap = eng.snapshot(0.3)
assert snap["step"] == 2 and snap["beat"] == 0 and len(snap["cells"]) == 16
assert eng.snapshot(0.51)["beat"] == 1 and eng.snapshot(0.51)["flash"]
assert len(snap["echo"]) == 16
assert eng.stop() == []

print("hum tests passed")
