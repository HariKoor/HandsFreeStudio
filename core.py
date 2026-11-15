"""
HandsFree Studio - core logic with no camera or window code, so it can be
tested anywhere (see test_core.py).

  OneEuro       smooths the head cursor
  MouthTrigger  turns mouth openness into note on / note off
  BrowTrigger   the same for raised eyebrows (play mode)
  PadGrid       maps a cursor position to a pad and a MIDI note
"""

import math

from bridge import scale_notes, note_name, SCALES, NOTE_NAMES


class OneEuro:
    """One Euro filter: heavy smoothing when still, light when moving fast."""

    def __init__(self, min_cutoff=1.2, beta=2.0, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x = None
        self.dx = 0.0
        self.t = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self.x = None
        self.dx = 0.0
        self.t = None

    def __call__(self, x, t):
        if self.x is None:
            self.x, self.t = x, t
            return x
        dt = max(1e-3, t - self.t)
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x


class MouthTrigger:
    """
    update(mouth) returns ("on", velocity), ("off",) or None.

    The mouth has to stay open for `arm_frames` frames before a note fires.
    That short hold filters out twitches and talking, and lets us read how
    wide the mouth opened to set the note's loudness.
    """

    def __init__(self, on=0.06, off=0.04, span=0.15, arm_frames=2):
        self.on = on
        self.off = off
        self.span = span
        self.arm_frames = arm_frames
        self.rest = off * 0.5        # below this the mouth counts as closed
        self.state = "idle"
        self._count = 0
        self._peak = 0.0

    def set_baseline(self, closed_value):
        """Call after calibration with the user's mouth-closed reading."""
        self.on = closed_value + 0.04
        self.off = closed_value + 0.025
        self.rest = closed_value + 0.012

    def level(self, mouth):
        """0..1 for the on-screen meter."""
        return max(0.0, min(1.0, (mouth - self.off) / self.span))

    def _velocity(self, peak):
        f = max(0.0, min(1.0, (peak - self.on) / self.span))
        return int(55 + f * 72)

    def update(self, mouth):
        if self.state == "idle":
            if mouth >= self.on:
                self.state = "arming"
                self._count = 1
                self._peak = mouth
                if self._count >= self.arm_frames:
                    self.state = "held"
                    return ("on", self._velocity(self._peak))
        elif self.state == "arming":
            if mouth < self.off:
                self.state = "idle"
                return None
            self._count += 1
            self._peak = max(self._peak, mouth)
            if self._count >= self.arm_frames:
                self.state = "held"
                return ("on", self._velocity(self._peak))
        elif self.state == "held":
            if mouth < self.off:
                self.state = "idle"
                return ("off",)
        elif self.state == "blocked":
            if mouth < self.off:
                self.state = "idle"
        return None

    def force_off(self):
        """Cancel the current click. If the mouth is still open, it must close
        before another click can start, so one long opening is one click."""
        was_held = self.state == "held"
        if self.state != "idle":
            self.state = "blocked"
        return was_held


class BrowTrigger(MouthTrigger):
    """Raised eyebrows as a click: raise to press, lower to release, higher
    for a louder note. Everyone's resting eyebrows sit at a different
    height and drift with their expression, so the trigger measures the
    rise above a resting level that it keeps tracking. Call track() every
    frame before update()."""

    def __init__(self, rise=0.22, arm_frames=2):
        super().__init__(on=rise, off=rise * 0.6, span=0.45,
                         arm_frames=arm_frames)
        self.rise = rise
        self.base = None

    def track(self, value):
        if self.base is None or value < self.base:
            self.base = value                      # relaxed further: follow
        elif self.state in ("idle", "arming"):
            # Follow quickly near rest, slowly when the brows sit higher for
            # a while (looking up, a new expression); never while clicking.
            near = value < self.base + 0.5 * self.rise
            self.base += (0.02 if near else 0.004) * (value - self.base)
        self.on = self.base + self.rise
        self.off = self.base + 0.6 * self.rise
        self.rest = self.base + 0.3 * self.rise

    def lifted(self, value):
        """True while the eyebrows are clearly above rest: the eye shape is
        distorted then, so the eye pointer holds its last answer."""
        return self.base is not None and value > self.base + 0.5 * self.rise

    def reset(self):
        self.state = "idle"
        self._count = 0


# Five- and six-note scales borrow their chords from the full scale they
# belong to.
CHORD_PARENT = {"pentatonic_major": "major", "pentatonic_minor": "minor",
                "blues": "minor"}


def chord_notes(note, root, scale):
    """The three-note chord built on `note` using only notes of the scale
    (root, third, fifth). In C major: C -> C E G, D -> D F A, and so on."""
    steps = SCALES[CHORD_PARENT.get(scale, scale)]
    pc = (note - NOTE_NAMES.index(root)) % 12
    if pc not in steps:                      # e.g. the blues note
        return [note, min(127, note + 7)]
    d = steps.index(pc)
    third = note + (steps[(d + 2) % 7] - steps[d]) % 12
    fifth = note + (steps[(d + 4) % 7] - steps[d]) % 12
    return [n for n in (note, third, fifth) if n <= 127]


def chord_name(notes):
    """C, Dm, Bdim ... from the chord's notes."""
    name = NOTE_NAMES[notes[0] % 12]
    if len(notes) < 3:
        return name + "5"
    shape = (notes[1] - notes[0], notes[2] - notes[0])
    return name + {(4, 7): "", (3, 7): "m", (3, 6): "dim",
                   (4, 8): "aug"}.get(shape, "")


class PadGrid:
    """Scale-locked pads. Lowest note is bottom-left."""

    GRIDS = [(4, 2), (4, 3), (4, 4)]

    def __init__(self, root="C", scale="pentatonic_minor", octave=4,
                 cols=4, rows=2, sticky=0.18):
        self.root = root
        self.scale = scale
        self.octave = octave
        self.cols = cols
        self.rows = rows
        self.sticky = sticky
        self.chords = False          # True: each pad plays a chord
        self.selected = None
        self.notes = []
        self._rebuild()

    def _rebuild(self):
        self.notes = scale_notes(self.root, self.scale, self.octave,
                                 self.cols * self.rows)
        self.selected = None

    # ---- settings --------------------------------------------------------
    def next_scale(self):
        names = list(SCALES)
        self.scale = names[(names.index(self.scale) + 1) % len(names)]
        self._rebuild()

    def next_root(self):
        self.root = NOTE_NAMES[(NOTE_NAMES.index(self.root) + 1) % 12]
        self._rebuild()

    def shift_octave(self, delta):
        self.octave = max(1, min(6, self.octave + delta))
        self._rebuild()

    def next_grid(self):
        i = self.GRIDS.index((self.cols, self.rows)) \
            if (self.cols, self.rows) in self.GRIDS else 0
        self.cols, self.rows = self.GRIDS[(i + 1) % len(self.GRIDS)]
        self._rebuild()

    def title(self):
        return "%s %s  oct %d" % (self.root, self.scale.replace("_", " "),
                                  self.octave)

    # ---- geometry (all in 0..1 of the pad area) --------------------------
    def rect(self, index):
        """(x0, y0, x1, y1) of a pad, y grows downward."""
        col = index % self.cols
        row = index // self.cols            # 0 = bottom row
        w = 1.0 / self.cols
        h = 1.0 / self.rows
        x0 = col * w
        y1 = 1.0 - row * h
        return (x0, y1 - h, x0 + w, y1)

    def _index_at(self, x, y):
        x = max(0.0, min(0.9999, x))
        y = max(0.0, min(0.9999, y))
        col = int(x * self.cols)
        row = self.rows - 1 - int(y * self.rows)
        index = row * self.cols + col
        return index if index < len(self.notes) else None

    def update(self, x, y):
        """Move the selection. Sticky edges stop flicker between two pads."""
        if self.selected is not None:
            x0, y0, x1, y1 = self.rect(self.selected)
            mx = (x1 - x0) * self.sticky
            my = (y1 - y0) * self.sticky
            if x0 - mx <= x <= x1 + mx and y0 - my <= y <= y1 + my:
                return self.selected
        self.selected = self._index_at(x, y)
        return self.selected

    def note(self, index=None):
        index = self.selected if index is None else index
        if index is None or index >= len(self.notes):
            return None
        return self.notes[index]

    def chord(self, index=None):
        """The notes this pad plays: one note, or a chord if chords are on."""
        note = self.note(index)
        if note is None:
            return []
        if not self.chords:
            return [note]
        return chord_notes(note, self.root, self.scale)

    def label(self, index):
        if self.chords:
            return chord_name(self.chord(index))
        return note_name(self.notes[index])
