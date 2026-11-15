"""
HandsFree Studio - HUM mode.

Hum a melody; each hummed note is snapped to the current scale and to a
sixteenth-note grid, then sent to FL Studio.

  detect_pitch()  finds the pitch of a short block of microphone audio
  HumEngine       turns pitches into notes, quantises them and schedules the
                  MIDI note on / note off times

Quantising rules
  - The grid is sixteenth notes at the current tempo.
  - A note's start is moved to the nearest grid line.
  - A note's length is rounded to the nearest sixteenth, minimum one.
  - A note is only known once it has been hummed, so it cannot be placed on
    a grid line that has already passed. Each note is therefore sent exactly
    one bar (16 sixteenths) later, like a looper: it lands on the same step
    of the next bar, exactly on the grid.

No audio device code here, so it can be tested with synthetic tones
(test_hum.py).
"""

import collections
import math
import threading

import numpy as np

from bridge import SCALES, NOTE_NAMES, note_name

SR = 16000
WINDOW = 1024             # samples analysed per frame (64 ms)
FMIN, FMAX = 80.0, 800.0  # humming range in Hz
CLARITY = 0.45            # how periodic the sound must be to count as a hum
HOLD_ON = 2               # frames a hum must last before it is a note
HOLD_CHANGE = 4           # frames a different pitch must last to change note
HOLD_OFF = 4              # frames of silence before a note ends
STICK = 0.35              # semitones past the halfway point needed to change
SILENCE = -1
END_BIAS = 0.027          # seconds; measured with test tones
DELAY_STEPS = 16          # notes play this many sixteenths after being hummed


def detect_pitch(x, sr=SR):
    """Return the pitch of x in Hz, or None if it is not a clear tone."""
    x = x.astype(np.float32)
    x = x - x.mean()
    n = len(x)
    if n < 256 or float(np.abs(x).max()) < 1e-6:
        return None
    spec = np.fft.rfft(x * np.hanning(n), 2 * n)
    r = np.fft.irfft(spec * np.conj(spec))[:n]
    if r[0] <= 0:
        return None
    r = r / r[0]
    lo = max(2, int(sr / FMAX))
    hi = min(int(sr / FMIN), n - 2)
    seg = r[lo:hi]
    peak = float(seg.max())
    if peak < CLARITY:
        return None
    # The first strong local maximum, so we do not land an octave too low.
    mid = seg[1:-1]
    ok = (mid >= 0.85 * peak) & (mid >= seg[:-2]) & (mid >= seg[2:])
    hits = np.nonzero(ok)[0]
    if len(hits) == 0:
        return None
    lag = lo + 1 + int(hits[0])
    a, b, c = r[lag - 1], r[lag], r[lag + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if abs(denom) > 1e-9 else 0.0
    return sr / (lag + shift)


def hz_to_midi(hz):
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def _runs(cells):
    """Merge the cells of one held note: [(first_step, length, note)]."""
    out = []
    for i, c in enumerate(cells):
        if c is None:
            continue
        if out and out[-1][3] == c[1] and out[-1][0] + out[-1][1] == i:
            out[-1][1] += 1
        else:
            out.append([i, 1, c[0], c[1]])
    return [(a, n, note) for a, n, note, _ in out]


class HumEngine:
    def __init__(self, tempo=120, root="C", scale="pentatonic_minor",
                 octave_shift=1, latency=0.03, gate=120.0,
                 delay_steps=DELAY_STEPS):
        self.lock = threading.Lock()
        self.enabled = False
        self.latency = latency          # microphone delay to subtract, seconds
        self.gate = gate                # quietest level that counts (int16 RMS)
        self.delay = delay_steps
        self.buffer = np.zeros(WINDOW, np.int16)
        self.t0 = 0.0
        self.s16 = 15.0 / tempo         # length of a sixteenth note, seconds
        self.tempo = tempo
        self.configure(root, scale, octave_shift)
        self._reset_tracking()

    # ---- settings --------------------------------------------------------
    def configure(self, root, scale, octave_shift):
        """Which notes are allowed, and how far to transpose the hum."""
        with self.lock:
            pc = NOTE_NAMES.index(root)
            steps = SCALES[scale]
            self.allowed = [n for n in range(24, 109)
                            if (n - pc) % 12 in steps]
            self.shift = 12 * octave_shift

    def set_tempo(self, bpm, now):
        with self.lock:
            self.tempo = bpm
            self.s16 = 15.0 / bpm
            self.t0 = now

    def reset_clock(self, now):
        """Start a new bar now (call when FL Studio starts playing)."""
        with self.lock:
            self.t0 = now

    def _reset_tracking(self):
        self.cur = SILENCE          # snapped hum note being held, or SILENCE
        self.cand = SILENCE
        self.cand_count = 0
        self.cand_t = 0.0
        self.active = None          # {"note", "onset", "g", "id"}
        self.events = []            # (time, order, kind, id, note)
        self.sounding = None        # (id, note)
        self.cells = {}             # absolute sixteenth index -> output note
        self.heard = None           # snapped note in the latest frame
        self.recent = collections.deque(maxlen=3)   # recent pitches, smoothed
        self._next_id = 1
        self._order = 0

    def stop(self):
        """Leave HUM mode: returns the note-offs still owed."""
        with self.lock:
            out = [("off", self.sounding[1])] if self.sounding else []
            self._reset_tracking()
            return out

    # ---- audio in --------------------------------------------------------
    def snap(self, midi):
        return min(self.allowed, key=lambda n: abs(n - midi))

    def _steady(self, midi):
        """Snap to the scale, but keep the note being held unless the pitch
        has clearly moved. A voice wobbles; one hum should stay one note."""
        cur = self.cur
        if cur == SILENCE:
            return self.snap(midi)
        if abs(abs(midi - cur) - 12.0) < 1.0:      # detector octave slip
            return cur
        new = self.snap(midi)
        if new == cur:
            return cur
        halfway = (cur + new) / 2.0
        past = (midi - halfway) if new > cur else (halfway - midi)
        return new if past >= STICK else cur

    def process(self, samples, t_start, now=None):
        """Feed one block of int16 microphone samples that began at t_start."""
        samples = np.asarray(samples, dtype=np.int16)
        k = len(samples)
        if k >= WINDOW:
            self.buffer = samples[-WINDOW:].copy()
        else:
            self.buffer = np.concatenate([self.buffer[k:], samples])
        # A frame is stamped at the middle of its analysis window: a hum is
        # first seen (and last seen) when it fills about half the window.
        t_frame = t_start + k / SR - WINDOW / (2.0 * SR) - self.latency
        if now is None:
            now = t_start + k / SR
        x = self.buffer.astype(np.float32)
        rms = float(np.sqrt(np.mean((x - x.mean()) ** 2)))
        hz = detect_pitch(self.buffer) if rms >= self.gate else None
        with self.lock:
            if hz:
                self.recent.append(hz_to_midi(hz))
                # Median of three steadies a wobble; at the very start of a hum
                # there is nothing to smooth with yet.
                midi = (sorted(self.recent)[1] if len(self.recent) == 3
                        else self.recent[-1])
                snapped = self._steady(midi)
            else:
                self.recent.clear()
                snapped = SILENCE
            self.heard = None if snapped == SILENCE else snapped
            if snapped == self.cur:
                self.cand_count = 0
                self.cand = SILENCE
                return
            if snapped != self.cand or self.cand_count == 0:
                self.cand = snapped
                self.cand_count = 1
                self.cand_t = t_frame
            else:
                self.cand_count += 1
            if snapped == SILENCE:
                need = HOLD_OFF
            elif self.cur == SILENCE:
                need = HOLD_ON
            else:
                need = HOLD_CHANGE
            if self.cand_count >= need:
                if self.active is not None:
                    # A hum is still detected for a moment after it stops.
                    t_end = self.cand_t - (END_BIAS if snapped == SILENCE
                                           else 0.0)
                    self._end(t_end, now)
                if snapped != SILENCE:
                    self._start(snapped, self.cand_t, now)
                self.cur = snapped
                self.cand_count = 0

    # ---- quantising ------------------------------------------------------
    def _push(self, t, kind, ident, note):
        self._order += 1
        self.events.append((t, self._order, kind, ident, note))

    def _start(self, hum_note, onset, now):
        note = max(0, min(127, hum_note + self.shift))
        g = int(round((onset - self.t0) / self.s16))
        ident = self._next_id
        self._next_id += 1
        self.active = {"note": note, "onset": onset, "g": g, "id": ident}
        self._push(max(self.t0 + (g + self.delay) * self.s16, now), "on",
                   ident, note)
        self.cells[g] = (note, ident)

    def _end(self, t_end, now):
        a = self.active
        n = max(1, int(round((t_end - a["onset"]) / self.s16)))
        self._push(max(self.t0 + (a["g"] + n + self.delay) * self.s16, now),
                   "off", a["id"], a["note"])
        for i in range(n):
            self.cells[a["g"] + i] = (a["note"], a["id"])
        self.active = None

    # ---- MIDI out --------------------------------------------------------
    def due(self, now):
        """Messages whose time has come: [("on", note, velocity) | ("off", note)]."""
        out = []
        with self.lock:
            if not self.events:
                return out
            self.events.sort()
            while self.events and self.events[0][0] <= now:
                _, _, kind, ident, note = self.events.pop(0)
                if kind == "on":
                    if self.sounding:
                        out.append(("off", self.sounding[1]))
                    out.append(("on", note, 100))
                    self.sounding = (ident, note)
                elif self.sounding and self.sounding[0] == ident:
                    out.append(("off", note))
                    self.sounding = None
        return out

    # ---- for the screen --------------------------------------------------
    def snapshot(self, now):
        with self.lock:
            pos = (now - self.t0) / self.s16          # sixteenths since t0
            step = int(math.floor(pos))
            bar_start = step - step % 16
            beat = (step % 16) // 4
            flash = (pos / 4.0) % 1.0 < 0.22          # first part of each beat
            cells = [self.cells.get(bar_start + i) for i in range(16)]
            if self.active is not None:
                for g in range(max(self.active["g"], bar_start),
                               min(step, bar_start + 15) + 1):
                    cells[g - bar_start] = (self.active["note"],
                                            self.active["id"])
            # What was hummed one bar ago is what is sounding now.
            echo = [self.cells.get(bar_start + i - self.delay)
                    for i in range(16)]
            for g in [g for g in self.cells if g < bar_start - 32]:
                del self.cells[g]
            heard = self.heard
            return {
                "step": step % 16, "beat": beat, "flash": flash,
                "cells": [c[0] if c else None for c in cells],
                "echo": [c[0] if c else None for c in echo],
                "runs": _runs(cells), "echo_runs": _runs(echo),
                "heard": note_name(heard) if heard is not None else None,
                "playing": (note_name(self.sounding[1])
                            if self.sounding else None),
            }
