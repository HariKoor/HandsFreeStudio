"""Checks for core.py. Needs no camera, window or FL Studio:  python tests/test_core.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import OneEuro, MouthTrigger, PadGrid


def test_trigger():
    t = MouthTrigger(arm_frames=2)
    t.set_baseline(0.01)                       # on 0.05, off 0.035
    assert t.update(0.01) is None
    assert t.update(0.06) is None              # arming
    assert t.update(0.02) is None              # twitch cancelled
    assert t.state == "idle"
    assert t.update(0.06) is None
    ev = t.update(0.20)
    assert ev[0] == "on" and 55 <= ev[1] <= 127
    loud = ev[1]
    assert t.update(0.10) is None              # still held
    assert t.update(0.04) is None              # between off and on: still held
    assert t.update(0.01) == ("off",)
    t.update(0.055)
    quiet = t.update(0.056)[1]
    assert quiet < loud
    # A click that is cancelled (mode switch, menu) must not fire again
    # while the mouth stays open.
    assert t.force_off() is True
    for _ in range(30):
        assert t.update(0.20) is None
    assert t.update(0.01) is None              # mouth closes
    t.update(0.20)
    assert t.update(0.20)[0] == "on"           # a new opening clicks again


def test_grid():
    g = PadGrid("C", "pentatonic_minor", 4, cols=4, rows=2)
    assert g.notes == [60, 63, 65, 67, 70, 72, 75, 77]
    assert g.update(0.05, 0.95) == 0           # bottom-left = lowest
    assert g.note() == 60
    assert g.update(0.95, 0.05) == 7           # top-right = highest
    g.update(0.10, 0.90)
    assert g.selected == 0
    assert g.update(0.27, 0.90) == 0           # just over the edge: sticky
    assert g.update(0.40, 0.90) == 1           # clearly in the next pad
    g.next_grid()
    assert len(g.notes) == 12
    g.next_scale()
    assert g.selected is None


def test_chords():
    g = PadGrid("C", "major", 4, cols=4, rows=2)
    assert g.chord(0) == [60]                       # chords off: one note
    g.chords = True
    assert g.chord(0) == [60, 64, 67] and g.label(0) == "C"      # C major
    assert g.chord(1) == [62, 65, 69] and g.label(1) == "Dm"     # D minor
    assert g.chord(4) == [67, 71, 74] and g.label(4) == "G"
    assert g.chord(5) == [69, 72, 76] and g.label(5) == "Am"
    assert g.label(6) == "Bdim"
    m = PadGrid("A", "minor", 3, cols=4, rows=2)
    m.chords = True
    assert m.label(0) == "Am" and m.label(2) == "C" and m.label(3) == "Dm"
    p = PadGrid("C", "pentatonic_minor", 4, cols=4, rows=2)
    p.chords = True
    assert p.chord(0) == [60, 63, 67] and p.label(0) == "Cm"     # C minor
    assert p.chord(1) == [63, 67, 70] and p.label(1) == "D#"     # E flat major
    b = PadGrid("C", "blues", 4, cols=4, rows=2)
    b.chords = True
    assert b.chord(3) == [66, 73]                   # blues note: root + fifth


def test_filter():
    f = OneEuro()
    t = 0.0
    out = 0.0
    for i in range(60):
        t += 1 / 30
        out = f(0.5 + (0.01 if i % 2 else -0.01), t)   # jitter
    assert abs(out - 0.5) < 0.008
    for i in range(30):
        t += 1 / 30
        out = f(0.9, t)                                # real move
    assert abs(out - 0.9) < 0.01


def test_brow():
    from core import BrowTrigger
    b = BrowTrigger()
    events = []

    def feed(values):
        for v in values:
            b.track(v)
            e = b.update(v)
            if e:
                events.append(e[0])

    feed([0.10] * 30)                       # resting eyebrows: nothing
    assert events == [] and abs(b.base - 0.10) < 0.01
    feed([0.16, 0.18] * 10)                 # a small lift (looking up): nothing
    assert events == []
    feed([0.10] * 20 + [0.45] * 6)          # a clear raise presses ...
    assert events == ["on"] and b.lifted(0.45) and not b.lifted(0.12)
    feed([0.45] * 60)                       # ... holds while raised, and the
    assert events == ["on"]                 # resting level does not creep up
    feed([0.10] * 5)                        # lowering releases
    assert events == ["on", "off"]
    quiet = BrowTrigger()                   # someone whose brows rest higher
    for v in [0.30] * 30 + [0.36] * 30:
        quiet.track(v)
        assert quiet.update(v) is None
    for v in [0.62] * 4:
        quiet.track(v)
        quiet.update(v)
    assert quiet.state == "held"
    assert b.force_off() is False and quiet.force_off() is True


if __name__ == "__main__":
    test_trigger()
    test_brow()
    test_grid()
    test_chords()
    test_filter()
    print("core tests passed")
