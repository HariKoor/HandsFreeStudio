"""Checks for the eye pointer, with a simulated face instead of a camera:
    python tests/test_gaze.py

The simulator is a head with two eyeballs in front of a laptop camera. It
looks at a point on the screen from any head pose and produces the same
numbers the face tracker does (eye corners, iris centres, lids, the face
matrix, the eye-look scores), with camera-like noise. It proves the maths
and the head-movement handling; it cannot prove how well a real webcam
sees a real iris.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
import os
import tempfile

import numpy as np

import gaze
from gaze import (Calibrator, FixationFilter, GazePointer, Snapper, features,
                  rect_distance)

W, H = 1280, 720
KX, KY = W / 30.0, -H / 20.0          # the head pointer's pixels per degree
FOCAL = 860.0                          # pixels, for a 960-wide camera frame


def rot(yaw, pitch, roll):
    y, p, r = (math.radians(a) for a in (yaw, pitch, roll))
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0],
                   [-math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)],
                   [0, -math.sin(p), math.cos(p)]])
    Rz = np.array([[math.cos(r), -math.sin(r), 0],
                   [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


class SimFace:
    """World: camera at the origin on the top edge of the screen, x to the
    right, y up, z from the screen towards the user (centimetres)."""

    def __init__(self, seed=0, px_noise=0.3):
        self.rng = np.random.default_rng(seed)
        self.px_noise = px_noise

    @staticmethod
    def screen(px):
        return np.array([px[0] / W * 31.0 - 15.5, -1.5 - px[1] / H * 17.5, 0.0])

    def frame(self, target_px, yaw=0.0, pitch=0.0, roll=0.0,
              pos=(0.0, -4.0, 55.0), blink=0.0):
        rng = self.rng
        R = rot(yaw, pitch, roll)
        head = np.asarray(pos, float)
        P = self.screen(target_px)
        world = {}
        look = dict.fromkeys(gaze.LOOKS, 0.0)
        els = []
        for (a, b, iris, top, bottom), side in zip(gaze.EYES, (-1, 1)):
            centre = head + R @ np.array([side * 3.2, 0.0, 0.0])
            g = P - centre
            g /= np.linalg.norm(g)
            gh = R.T @ g                            # gaze in the head
            az = math.degrees(math.atan2(gh[0], -gh[2]))
            el = math.degrees(math.asin(gh[1]))
            els.append(el)
            world[iris] = centre + 1.2 * g
            world[a] = head + R @ np.array([side * 3.2 - 1.5, 0.0, -0.6])
            world[b] = head + R @ np.array([side * 3.2 + 1.5, 0.0, -0.6])
            world[top] = head + R @ np.array(
                [side * 3.2, 0.5 + 0.45 * math.sin(math.radians(el)), -1.1])
            world[bottom] = head + R @ np.array(
                [side * 3.2, -0.5 + 0.15 * math.sin(math.radians(el)), -1.0])
            name = "Left" if side > 0 else "Right"
            out, inn = (az, -az) if side > 0 else (-az, az)
            look["eyeLookOut" + name] = max(0.0, out / 40) + rng.normal(0, 0.03)
            look["eyeLookIn" + name] = max(0.0, inn / 40) + rng.normal(0, 0.03)
            look["eyeLookUp" + name] = max(0.0, el / 30) + rng.normal(0, 0.03)
            look["eyeLookDown" + name] = max(0.0, -el / 30) + rng.normal(0, 0.03)
        world[gaze.FOREHEAD] = head + R @ np.array([0.0, 6.5, -0.8])
        world[gaze.CHIN] = head + R @ np.array([0.0, -10.5, -0.5])
        pts = {}
        for i, p in world.items():
            pts[i] = (480 + FOCAL * p[0] / p[2] + rng.normal(0, self.px_noise),
                      270 - FOCAL * p[1] / p[2] + rng.normal(0, self.px_noise))
        S = np.diag([1.0, 1.0, -1.0])               # matrix space: z to camera
        M = np.eye(4)
        M[:3, :3] = S @ R @ S
        M[:3, 3] = head + rng.normal(0, 0.15, 3)
        f = M[:3, 2]
        y = math.degrees(math.atan2(f[0], f[2])) + rng.normal(0, 0.3)
        p = math.degrees(math.atan2(f[1], f[2])) + rng.normal(0, 0.3)
        return {"pts": pts, "m": M, "look": look, "blink": blink}, y, p


def calibrate_follow(sim, gp, lag=0.12, seed=1):
    """Follow the moving dot at 30 frames a second. The eyes (and camera)
    trail the dot by `lag` seconds; the head is still in lap 1 and turns
    and nods in lap 2, as the screen asks."""
    rng = np.random.default_rng(seed)
    t = 0.0
    cal = gp.start_calibration(t, KX, KY)
    assert isinstance(cal, gaze.FollowCalibrator)
    while True:
        t += 1 / 30
        snap = cal.snapshot(t)
        target = cal.pos(t - lag)
        yaw = pitch = 0.0
        if snap["phase"] != "intro" and snap["i"] == 1:
            yaw, pitch = 7 * math.sin(1.7 * t), 4.5 * math.sin(2.3 * t + 1)
        g, y, p = sim.frame(target, yaw + rng.normal(0, 0.2),
                            pitch + rng.normal(0, 0.2), rng.normal(0, 0.5),
                            (rng.normal(0, 0.3), -4 + rng.normal(0, 0.3), 55))
        if gp.feed_calibration(features(g, y, p), 0.0, t):
            break
        assert t < 200, "calibration never finished"
    cal_ref = gp.cal
    assert gp.finish_calibration()
    return t, cal_ref.lag


def calibrate(sim, gp, move=True, seed=1):
    """Run the 12 dots at 30 frames a second. move=True: the head turns and
    nods at each dot, as the screen asks."""
    rng = np.random.default_rng(seed)
    t = 0.0
    cal = gp.start_calibration(t, KX, KY, "dots")
    done = False
    while not done:
        t += 1 / 30
        snap = cal.snapshot(t)
        target = (snap["x"], snap["y"])
        tt = t - cal.t_point
        base_yaw = 0.25 * (target[0] - W / 2) / KX      # head half follows
        base_pitch = 0.25 * (target[1] - H / 2) / KY
        if move:
            yaw = base_yaw + 7 * math.sin(2.2 * tt)
            pitch = base_pitch + 4.5 * math.sin(2.9 * tt + 1)
        else:
            yaw, pitch = base_yaw * 0, base_pitch * 0
        g, y, p = sim.frame(target, yaw + rng.normal(0, 0.2),
                            pitch + rng.normal(0, 0.2), rng.normal(0, 0.5),
                            (rng.normal(0, 0.3), -4 + rng.normal(0, 0.3), 55))
        done = gp.feed_calibration(features(g, y, p), 0.0, t)
        assert t < 400, "calibration never finished"
    assert gp.finish_calibration()
    return t


def error(sim, gp, n=150, yaw=10, pitch=7, shift=3, seed=5):
    """Mean and 90th-percentile distance (px) between where the simulated
    user looks and where the model says, over random targets and poses."""
    rng = np.random.default_rng(seed)
    errs = []
    for _ in range(n):
        target = (rng.uniform(60, W - 60), rng.uniform(60, H - 60))
        pose = (rng.uniform(-yaw, yaw), rng.uniform(-pitch, pitch),
                rng.uniform(-4, 4))
        pos = (rng.uniform(-shift, shift), -4 + rng.uniform(-shift, shift),
               55 + rng.uniform(-shift, shift))
        preds = []
        for _ in range(12):
            g, y, p = sim.frame(target, *pose, pos)
            preds.append(gp.model.predict(features(g, y, p))[0])
        errs.append(np.linalg.norm(np.mean(preds, 0) - target))
    return float(np.mean(errs)), float(np.percentile(errs, 90))


def new_pointer():
    return GazePointer(os.path.join(tempfile.mkdtemp(), "gaze_model.json"), W, H)


if __name__ == "__main__":
    # ---- features: the right numbers, and None when something is missing
    sim = SimFace()
    g, y, p = sim.frame((640, 360))
    f = features(g, y, p)
    assert f is not None and f.shape == (gaze.N_FEAT,)
    assert 0.3 < f[gaze.U] < 0.7
    assert features(None, 0, 0) is None
    assert features({"pts": {}, "m": None, "look": {}}, 0, 0) is None
    g2, y2, p2 = sim.frame((1200, 360))
    assert abs(features(g2, y2, p2)[gaze.U] - f[gaze.U]) > 0.03   # iris moved

    # ---- following the moving dot: the default calibration. It works out
    #      how far the eyes trail the dot, and is accurate with the head
    #      still or moving afterwards
    gf = new_pointer()
    seconds, lag = calibrate_follow(SimFace(8), gf, lag=0.12)
    f_still, _ = error(SimFace(2), gf, yaw=0.5, pitch=0.5, shift=0.3)
    f_move, f_p90 = error(SimFace(3), gf)
    print("follow-the-dot calibration: %.0f s, %d stretches, trail found "
          "%.2f s (true 0.12), accuracy %s" % (seconds, len(gf.dots), lag,
                                               gf.quality()))
    print("  error, head still  %.0f px" % f_still)
    print("  error, head moving %.0f px (90%% under %.0f px)" % (f_move, f_p90))
    assert abs(lag - 0.12) <= 0.041
    assert f_still < 45 and f_move < 60 and f_p90 < 110
    cal = gaze.FollowCalibrator(W, H, 0.0)
    assert cal.pos(0.0) == cal.pos(cal.INTRO + 0.5)            # resting
    xs = [cal.pos(t)[0] for t in np.arange(cal.t_start, cal.t_end, 0.1)]
    ys = [cal.pos(t)[1] for t in np.arange(cal.t_start, cal.t_end, 0.1)]
    assert min(xs) < 0.1 * W and max(xs) > 0.9 * W            # whole screen
    assert min(ys) < 0.15 * H and max(ys) > 0.85 * H
    assert cal.result(KX, KY) == []                            # nothing seen

    # ---- the 12 fixed dots with head movement at each: accurate, and stays
    #      accurate when the head turns, nods, tilts and shifts afterwards
    gp = new_pointer()
    seconds = calibrate(SimFace(1), gp, move=True)
    still_mean, _ = error(SimFace(2), gp, yaw=0.5, pitch=0.5, shift=0.3)
    move_mean, move_p90 = error(SimFace(3), gp)
    print("head-move calibration: %.0f s, %d dots, accuracy %s"
          % (seconds, len(gp.dots), gp.quality()))
    print("  error, head still  %.0f px" % still_mean)
    print("  error, head moving %.0f px (90%% under %.0f px)"
          % (move_mean, move_p90))
    assert still_mean < 45 and move_mean < 60 and move_p90 < 110
    assert gp.accuracy is not None and gp.accuracy < 90

    # ---- the same model, saved and loaded
    gp.save()
    again = GazePointer(gp.path, W, H)
    assert again.load() and again.ready
    assert abs(error(SimFace(3), again)[0] - move_mean) < 8
    assert not GazePointer(gp.path, W + 1, H).load()    # other window size

    # ---- calibrated without head movement (a user who cannot turn their
    #      head): still usable when the head does drift, thanks to the
    #      head-pointer prior
    gs = new_pointer()
    calibrate(SimFace(4), gs, move=False)
    assert gs.cal is None
    s_still, _ = error(SimFace(5), gs, yaw=0.5, pitch=0.5, shift=0.3)
    s_move, _ = error(SimFace(6), gs, yaw=6, pitch=4, shift=1.5)
    print("still calibration: error head still %.0f px, head moving %.0f px"
          % (s_still, s_move))
    assert s_still < 45 and s_move < 150

    # ---- fixation filter: steady on a fixation, fast on a jump
    rng = np.random.default_rng(0)
    ff = FixationFilter(noise=45)
    outs = [ff.update(np.array([400.0, 300.0]) + rng.normal(0, 32, 2))
            for _ in range(60)]
    late = np.array(outs[30:])
    assert late.std(0).max() < 12, late.std(0)
    assert np.linalg.norm(late.mean(0) - [400, 300]) < 15
    frames = 0
    while np.linalg.norm(ff.update(np.array([900.0, 500.0])
                                   + rng.normal(0, 32, 2)) - [900, 500]) > 60:
        frames += 1
        assert frames < 8, "too slow to follow a jump"
    # a move to the neighbouring target is followed too, a little later
    frames = 0
    while np.linalg.norm(ff.update(np.array([990.0, 500.0])
                                   + rng.normal(0, 32, 2)) - [990, 500]) > 45:
        frames += 1
        assert frames < 20

    # ---- snapping: reach, stickiness, and no flicker on an edge
    targets = [("a", 100, 100, 300, 160), ("b", 414, 100, 300, 160),
               ("c", 100, 500, 64, 64)]
    sn = Snapper()
    assert sn.update((250, 180), targets)[0] == "a"
    assert sn.update((405, 180), targets)[0] == "a"        # just past the edge
    assert sn.update((440, 180), targets)[0] == "a"        # still sticky
    for _ in range(3):
        got = sn.update((470, 180), targets)
    assert got[0] == "b"                                   # clearly on b now
    assert sn.update((408, 180), targets)[0] == "b"        # and b is sticky
    flips, last = 0, "b"
    for i in range(200):                                   # jitter on the edge
        k = sn.update((407 + rng.normal(0, 12), 180), targets)[0]
        flips += k != last
        last = k
    assert flips <= 2, flips
    assert Snapper().update((132, 590), targets)[0] == "c"     # near a button
    assert Snapper().update((640, 420), targets) is None       # empty space
    assert rect_distance((250, 180), (100, 100, 300, 160)) < 0

    # ---- rows by nodding: two rows in a pad area 100..588 (middle 344)
    ny = lambda off: gaze.nod_y(off, 344.0, 488.0, 2, 2.5, 720)
    assert ny(0) == 344
    assert ny(-20) < 344 - 40 and ny(20) > 344 + 40         # half a degree
    assert abs(ny(-49) - 222) < 3 and abs(ny(49) - 466) < 3  # 1.4 deg: row centre
    for off in (60, 90, 120, 149):                           # nodding too far
        assert 100 < ny(-off) < 222 and 466 < ny(off) < 588  # is still that row
    assert ny(300) > 620 and ny(-300) < 70                   # a big nod: dock, tabs
    assert ny(360) == 719 and ny(-360) == 0
    ys = [ny(o) for o in range(-360, 361, 3)]
    assert all(b >= a for a, b in zip(ys, ys[1:]))           # never backwards
    assert abs(rect_distance((90, 180), (100, 100, 300, 160)) - 10) < 1e-9

    # ---- the whole pointer: looking at a pad while the head moves keeps
    #      that pad; a head nudge moves to the neighbour; clicks correct
    #      a constant offset
    pads = [("pad%d" % i, 24 + (i % 4) * 308, 100 + (i // 4) * 163, 300, 155)
            for i in range(12)]
    sim = SimFace(7)
    gp.reset()
    t = 100.0
    wrong = 0
    for k in range(240):                    # 8 s looking at pad 5, head moving
        t += 1 / 30
        yaw, pitch = 8 * math.sin(k / 20), 5 * math.sin(k / 31)
        g, y, p = sim.frame((486, 340), yaw, pitch, 2 * math.sin(k / 17))
        head = (W / 2 + KX * y, H / 2 + KY * p)
        r = gp.update(features(g, y, p), 0.0, t, pads, head, nudge=False)
        if k > 15:
            wrong += not (r["snap"] and rect_distance(r["px"], pads[5][1:]) <= 0)
    assert wrong <= 4, "lost the pad %d times while the head moved" % wrong

    gp.reset()                               # nudge: eyes stay, head leans
    for k in range(60):
        t += 1 / 30
        g, y, p = sim.frame((486, 340))
        r = gp.update(features(g, y, p), 0.0, t, pads,
                      (W / 2 + KX * y, H / 2 + KY * p))
    assert rect_distance(r["px"], pads[5][1:]) <= 0
    for k in range(40):
        t += 1 / 30
        g, y, p = sim.frame((486, 340), yaw=-min(12.0, k * 0.8))
        r = gp.update(features(g, y, p), 0.0, t, pads,
                      (W / 2 + KX * y, H / 2 + KY * p))
    assert rect_distance(r["px"], pads[6][1:]) <= 0, "nudge did not move right"

    blinked = gp.update(None, 1.0, t + 0.01, pads, None)       # a blink holds
    assert blinked is r

    # rows by head (play mode's default): the eyes choose the column and
    # head_y chooses the row, wherever the eyes seem to be up-down
    gp.reset()
    for k in range(40):
        t += 1 / 30
        g, y, p = sim.frame((1090, 180))             # looking at the top right
        r = gp.update(features(g, y, p), 0.0, t, pads, None, head_y=520.0)
    assert rect_distance(r["px"], pads[11][1:]) <= 0     # ... bottom right
    for k in range(10):
        t += 1 / 30
        g, y, p = sim.frame((1090, 180))
        r = gp.update(features(g, y, p), 0.0, t, pads, None, head_y=150.0)
    assert rect_distance(r["px"], pads[3][1:]) <= 0      # a nod up: top right
    assert gp.learn_click(r["px"], t + 0.05)

    # the user slides 6 cm sideways and back 8 cm after calibrating: the
    # estimate is off until a few clicks pull it back
    def looked(gp, sim, target, t, n=24):
        for _ in range(n):
            t += 1 / 30
            g, y, p = sim.frame(target, pos=(6.0, -4.0, 63.0))
            r = gp.update(features(g, y, p), 0.0, t, pads, None, nudge=False)
        return r, t

    gp.reset()
    centres = [(x + w / 2, y + h / 2) for _, x, y, w, h in pads]
    r, t = looked(gp, sim, centres[0], t)
    before = float(np.mean([np.linalg.norm(
        np.subtract(looked(gp, sim, c, t)[0]["gaze"], c)) for c in centres]))
    for i in (0, 3, 5, 6, 8, 11, 2, 9, 4, 7, 1, 10):
        r, t = looked(gp, sim, centres[i], t)
        gp.learn_click(centres[i], t + 0.05)
    after = float(np.mean([np.linalg.norm(
        np.subtract(looked(gp, sim, c, t)[0]["gaze"], c)) for c in centres]))
    print("after the user shifts in their seat: error %.0f px, "
          "after 12 clicks %.0f px" % (before, after))
    assert after < before * 0.75 and after < 90

    print("gaze tests passed")
