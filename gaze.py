"""
HandsFree Studio - eye pointer.

Look at a button and it lights up; open your mouth to click it. The camera
cannot place a gaze to the pixel, so this never drives a free cursor. It
decides which target you mean:

  features()      per frame: where the head points and where it is (from the
                  face matrix), plus where each iris sits in its eye
  GazeModel       maps those numbers to a point on the screen. Head and eyes
                  are learned together, so turning your head while you keep
                  looking at one spot leaves the answer where it was
  FollowCalibrator  the dot travels across the whole screen and you follow
                  it: one lap with the head still, one lap while turning and
                  nodding. Hundreds of screen positions instead of a dozen,
                  and the model sees them from many head poses
  Calibrator      the older 12 fixed dots ("eye_calibration": "dots")
  FixationFilter  rock steady while the eyes rest, instant when they jump
  Snapper         picks the target under the gaze and holds on to it
  GazePointer     ties these together, and corrects itself on every click:
                  whatever you clicked is what you were looking at

Only numpy is needed. Nothing here touches the camera, so the whole file is
tested with a simulated face (test_gaze.py).
"""

import collections
import json
import math
import os
import time

import numpy as np

# MediaPipe face mesh point numbers. Each eye: the two corners (ordered so
# that both eyes run the same way across the face), iris centre, upper lid,
# lower lid.
EYES = ((33, 133, 468, 159, 145),
        (362, 263, 473, 386, 374))
FOREHEAD, CHIN = 10, 152
GAZE_POINTS = tuple(sorted({i for eye in EYES for i in eye} | {FOREHEAD, CHIN}))
LOOKS = ("eyeLookInLeft", "eyeLookOutLeft", "eyeLookUpLeft", "eyeLookDownLeft",
         "eyeLookInRight", "eyeLookOutRight", "eyeLookUpRight",
         "eyeLookDownRight")

YAW, PITCH, ROLL, TX, TY, TZ, U, V, LID, BH, BV, OPEN, SHUT = range(13)
N_FEAT = 13
# The smallest spread each feature is scaled by. Without these a head that
# barely moved during calibration would be blown up into a huge number the
# first time it does move.
FLOORS = np.array([3.0, 2.0, 2.0, 1.0, 1.0, 2.0, 0.02, 0.012, 0.012, 0.05, 0.05,
                   0.02, 0.05])
QUAD = ((U, YAW), (V, PITCH), (LID, PITCH), (U, U), (V, V), (U, V))
# Feature sets the fit may choose between. Up-and-down is the hard direction
# for a webcam, and which cues help depends on the face and the lighting, so
# the calibration data decides (measured on a real calibration: leaving out
# the head position helped up-and-down by about 15%).
MASKS = {"all": (), "no position": (TX, TY, TZ), "no lids": (OPEN, SHUT),
         "plain": (TX, TY, TZ, OPEN, SHUT)}
CONFIGS = [(mask, quad, lam) for mask in MASKS for quad in (False, True)
           for lam in (0.2, 0.05, 0.01, 0.003)]   # (features, curved, ridge)

BLINK = 0.6             # blink score above which a frame is ignored (the
                        # lids also drop when looking down, so not lower)
MODEL_VERSION = 4
MAX_MODEL_AGE = 12 * 3600


def features(gaze, yaw, pitch):
    """The 13 numbers for one frame, or None. `gaze` is FaceState.gaze:
    {"pts": {index: (x, y) in pixels}, "m": 4x4 face matrix or None,
     "look": {blendshape name: score}}."""
    if not gaze:
        return None
    pts = gaze["pts"]
    try:
        up = np.subtract(pts[FOREHEAD], pts[CHIN])
        us, vs, lids, opens = [], [], [], []
        for a, b, iris, top, bottom in EYES:
            A, B = np.asarray(pts[a], float), np.asarray(pts[b], float)
            d = B - A
            w2 = float(d @ d)
            if w2 < 1e-6:
                return None
            w = math.sqrt(w2)
            n = np.array([-d[1], d[0]]) / w        # across the eye, towards
            if n @ up < 0:                         # the forehead
                n = -n
            mid = (A + B) / 2
            us.append(float((np.asarray(pts[iris]) - A) @ d) / w2)
            vs.append(float((np.asarray(pts[iris]) - mid) @ n) / w)
            lids.append(float((np.asarray(pts[top]) - mid) @ n) / w)
            opens.append(float(np.subtract(pts[top], pts[bottom]) @ n) / w)
    except (KeyError, TypeError, ValueError):
        return None
    m = gaze.get("m")
    roll = tx = ty = tz = 0.0
    if m is not None:
        roll = math.degrees(math.atan2(m[1][0], m[0][0]))
        tx, ty, tz = float(m[0][3]), float(m[1][3]), float(m[2][3])
    look = gaze.get("look") or {}
    g = look.get
    bh = (g("eyeLookOutLeft", 0) - g("eyeLookInLeft", 0)
          + g("eyeLookInRight", 0) - g("eyeLookOutRight", 0)) / 2
    bv = (g("eyeLookUpLeft", 0) + g("eyeLookUpRight", 0)
          - g("eyeLookDownLeft", 0) - g("eyeLookDownRight", 0)) / 2
    f = np.array([yaw, pitch, roll, tx, ty, tz, sum(us) / 2, sum(vs) / 2,
                  sum(lids) / 2, bh, bv, sum(opens) / 2,
                  float(gaze.get("blink") or 0.0)], float)
    return f if np.all(np.isfinite(f)) else None


class GazeModel:
    """Screen point = weights x features. Ridge regression, pulled towards
    what is already known about the head: with the eyes still in the head, a
    head turn moves the gaze exactly as it moves the head pointer (kx, ky
    pixels per degree). The data overrules that wherever it has something
    to say."""

    def __init__(self, kx, ky):
        self.kx, self.ky = float(kx), float(ky)
        self.mu = np.zeros(N_FEAT)
        self.sd = FLOORS.copy()
        self.quad = False
        self.lam = 0.05
        self.mask = "all"
        self.w = None
        self.noise = 60.0       # px: frame-to-frame scatter while fixating
        self.accuracy = None    # px: error on a dot it was not trained on

    def design(self, F):
        Z = (np.atleast_2d(F) - self.mu) / self.sd
        if MASKS[self.mask]:
            Z = Z.copy()
            Z[:, list(MASKS[self.mask])] = 0.0
        cols = [Z]
        if self.quad:
            cols.append(np.stack([Z[:, a] * Z[:, b] for a, b in QUAD], 1))
        cols.append(np.ones((len(Z), 1)))
        return np.hstack(cols)

    def _normal(self, groups):
        """Per group: (X'X, X'y, weight) with this model's features."""
        out = []
        for F, T, wgt in groups:
            X = self.design(F)
            out.append((wgt * X.T @ X, wgt * X.T @ T, wgt * len(F)))
        return out

    def _solve(self, A, B, n):
        d = A.shape[0]
        w0 = np.zeros((d, 2))
        w0[YAW, 0] = self.kx * self.sd[YAW]
        w0[PITCH, 1] = self.ky * self.sd[PITCH]
        L = self.lam * max(n, 1.0) * np.eye(d)
        L[-1, -1] = 1e-9                           # the offset is free
        return w0 + np.linalg.solve(A + L, B - A @ w0)

    def predict(self, F):
        return self.design(F) @ self.w

    @staticmethod
    def _groups(groups, least):
        """Every group as (features (n, 13), targets (n, 2), weight). A
        single (x, y) target stands for all the frames of its group."""
        out = []
        for F, t, w in groups:
            F, t = np.asarray(F, float), np.asarray(t, float)
            if len(F) < least:
                continue
            if t.ndim == 1:
                t = np.tile(t, (len(F), 1))
            out.append((F, t, w))
        return out

    def quick_error(self, F, T):
        """RMS miss (px) of a plain straight-line fit. Used to compare ways
        of labelling the same frames."""
        self.mu, self.sd = F.mean(0), np.maximum(F.std(0), FLOORS)
        self.mask, self.quad, self.lam = "plain", False, 0.05
        (A, B, n), = self._normal([(F, T, 1.0)])
        R = self.design(F) @ self._solve(A, B, n) - T
        return float(np.sqrt((R ** 2).sum(1).mean()))

    def fit(self, dots, extra=(), select=True):
        """dots: [(features (n, 13), target, weight)] from calibration, one
        group per dot or per stretch of the path; target is one (x, y) or
        one per frame. extra: the same from clicks. Returns False if there
        is not enough to learn from."""
        dots = self._groups(dots, 8)
        extra = self._groups(extra, 3)
        if len(dots) < 6:
            return False
        allF = np.vstack([F for F, _, _ in dots])
        self.mu = allF.mean(0)
        self.sd = np.maximum(allF.std(0), FLOORS)

        def solve_all(groups):
            parts = self._normal(groups)
            return self._solve(sum(p[0] for p in parts),
                               sum(p[1] for p in parts),
                               sum(p[2] for p in parts))

        if select:
            # First pass, then drop the frames where the eyes were plainly
            # somewhere else (a glance away from the dot).
            self.mask, self.quad, self.lam = "plain", False, 0.05
            self.w = solve_all(dots)
            kept = []
            for F, T, wgt in dots:
                R = self.predict(F) - T
                dist = np.linalg.norm(R - np.median(R, 0), axis=1)
                ok = dist <= max(60.0, 3.0 * float(np.median(dist)))
                kept.append((F[ok], T[ok], wgt) if ok.sum() >= 8
                            else (F, T, wgt))
            dots = kept
            # Pick the model that best predicts a stretch it has not seen:
            # how far off a look of about a third of a second would be.
            best = None
            for mask, quad, lam in CONFIGS:
                self.mask, self.quad, self.lam = mask, quad, lam
                parts = self._normal(dots)
                ex = self._normal(extra)
                A = sum(p[0] for p in parts + ex)
                B = sum(p[1] for p in parts + ex)
                n = sum(p[2] for p in parts + ex)
                err2 = []
                for (a, b, m), (F, T, _) in zip(parts, dots):
                    R = self.design(F) @ self._solve(A - a, B - b, n - m) - T
                    k = max(1, len(R) // 10)
                    looks = [c.mean(0) for c in np.array_split(R, k)]
                    err2.append(float(np.mean(np.sum(np.square(looks), 1))))
                score = math.sqrt(float(np.mean(err2)))
                if best is None or score < best[0] * 0.97:
                    best = (score, mask, quad, lam)
            self.accuracy, self.mask, self.quad, self.lam = best
        self.kept = dots
        self.w = solve_all(dots + extra)
        # Frame-to-frame scatter, from successive differences so that a slow
        # drift along the path does not count as noise.
        var, m = 0.0, 0
        for F, T, _ in dots:
            d = np.diff(self.predict(F) - T, axis=0)
            var += float((d ** 2).sum()) / 2
            m += len(d)
        self.noise = float(np.clip(math.sqrt(var / max(m, 1)), 15.0, 200.0))
        return True


class FollowCalibrator:
    """The dot glides over the whole screen and the user follows it.

    Lap 1 runs left-right across four rows with the head still; lap 2 runs
    up-down across four columns while the user turns and nods. The eyes
    trail a moving dot, and the camera adds its own delay, so the frames
    are matched to where the dot was a moment earlier; how much earlier is
    worked out from the data (result())."""

    INTRO = 4.0          # seconds of instructions
    LEAD = 1.2           # the dot rests at the start of a lap
    PAUSE = 3.5          # between the laps, with the second instruction
    SPEEDS = (330.0, 290.0)   # px per second: about 8 degrees per second
    SEGMENT = 1.2        # seconds of path per group handed to the fit
    LAGS = (0.0, 0.04, 0.08, 0.12, 0.16, 0.2, 0.24, 0.28, 0.32, 0.36)

    def __init__(self, width, height, now):
        xs = [0.07 * width, 0.36 * width, 0.64 * width, 0.93 * width]
        ys = [0.13 * height, 0.38 * height, 0.62 * height, 0.87 * height]
        lap1, lap2 = [], []
        for r, y in enumerate(ys):                 # rows, left-right
            ends = (xs[0], xs[-1]) if r % 2 == 0 else (xs[-1], xs[0])
            lap1 += [(ends[0], y), (ends[1], y)]
        for c, x in enumerate(xs):                 # columns, up-down
            ends = (ys[-1], ys[0]) if c % 2 == 0 else (ys[0], ys[-1])
            lap2 += [(x, ends[0]), (x, ends[1])]
        # timeline: [(start, end, kind, lap index, points, cumulative lengths)]
        self.parts = []
        t = now + self.INTRO
        for i, (pts, speed) in enumerate(zip((lap1, lap2), self.SPEEDS)):
            pts = np.array(pts, float)
            cum = np.concatenate([[0.0], np.cumsum(
                np.linalg.norm(np.diff(pts, axis=0), axis=1))])
            if i:
                self.parts.append((t, t + self.PAUSE, "pause", i, pts, cum))
                t += self.PAUSE
            self.parts.append((t, t + self.LEAD, "lead", i, pts, cum))
            t += self.LEAD
            self.parts.append((t, t + cum[-1] / speed, "move", i, pts, cum))
            t += cum[-1] / speed
        self.t_start, self.t_end = now + self.INTRO, t
        self.samples = []                          # (time, features)

    def part(self, t):
        for p in self.parts:
            if t < p[1]:
                return p
        return self.parts[-1]

    def pos(self, t):
        """Where the dot is at time t."""
        t0, t1, kind, i, pts, cum = self.part(t)
        if kind != "move" or t <= t0:
            return (float(pts[0][0]), float(pts[0][1]))
        d = min(cum[-1], (t - t0) * self.SPEEDS[i])
        k = min(int(np.searchsorted(cum, d, side="right")) - 1, len(pts) - 2)
        f = (d - cum[k]) / max(cum[k + 1] - cum[k], 1e-9)
        p = pts[k] + f * (pts[k + 1] - pts[k])
        return (float(p[0]), float(p[1]))

    def feed(self, feat, blink, now):
        """One camera frame. Returns True when both laps are done."""
        if now >= self.t_end:
            return True
        t0, _, kind, _, _, _ = self.part(now)
        if feat is not None and blink < BLINK - 0.05 and (
                kind == "move" or (kind == "lead" and now - t0 > 0.7)):
            self.samples.append((now, feat))
        return False

    def snapshot(self, now):
        """What the page draws."""
        if now < self.t_start:
            x, y = self.pos(now)
            return {"phase": "intro", "x": x, "y": y, "p": 0, "i": 0, "n": 2,
                    "hint": "", "left": round(self.t_start - now, 1)}
        t0, t1, kind, i, _, _ = self.part(now)
        x, y = self.pos(now)
        return {"phase": "pause" if kind == "pause" else "follow",
                "x": round(x, 1), "y": round(y, 1), "i": i, "n": 2,
                "p": round(min(1.0, (now - self.t_start)
                               / (self.t_end - self.t_start)), 3),
                "hint": "still" if i == 0 else "move", "left": 0}

    def result(self, kx, ky):
        """The frames with their targets, cut into stretches of path:
        [(features, targets, weight)], or [] if too little was seen."""
        if len(self.samples) < 150:
            return []
        ts = np.array([t for t, _ in self.samples])
        F = np.array([f for _, f in self.samples])
        best = None
        for lag in self.LAGS:                      # eyes + camera delay
            T = np.array([self.pos(t - lag) for t in ts])
            err = GazeModel(kx, ky).quick_error(F, T)
            if best is None or err < best[0]:
                best = (err, lag, T)
        _, self.lag, T = best
        groups, start = [], 0
        for k in range(1, len(ts) + 1):
            if k == len(ts) or ts[k] - ts[start] >= self.SEGMENT \
                    or ts[k] - ts[k - 1] > 0.5:
                groups.append((F[start:k], T[start:k], 1.0))
                start = k
        return groups


class Calibrator:
    """Twelve dots, four across and three down. At each dot: a moment for the
    eyes to arrive, then frames are collected while the head turns and nods.
    Users who do not (or cannot) move their head get a fixed time per dot."""

    INTRO = 4.0          # seconds of instructions before the first dot
    SETTLE = 0.8         # the eyes travel to the dot
    MIN_FRAMES = 45
    YAW_SPAN = 9.0       # degrees of head turn that fill half the ring
    PITCH_SPAN = 6.0
    TIMEOUT = 9.0        # a dot never takes longer than this
    STILL = 2.5          # collection time per dot without head movement

    def __init__(self, width, height, now):
        xs = [0.09 * width, 0.36 * width, 0.64 * width, 0.91 * width]
        ys = [0.14 * height, 0.50 * height, 0.87 * height]
        self.points = []
        for r, y in enumerate(ys):
            for x in (xs if r % 2 == 0 else xs[::-1]):
                self.points.append((x, y))
        self.groups = []
        self.i = 0
        self.still = False
        self.t_point = now + self.INTRO
        self._intro = True
        self._clear()

    def _clear(self):
        self.F = []
        self.lo = [1e9, 1e9]
        self.hi = [-1e9, -1e9]

    def _cover(self):
        if not self.F:
            return 0.0, 0.0
        return (min(1.0, (self.hi[0] - self.lo[0]) / self.YAW_SPAN),
                min(1.0, (self.hi[1] - self.lo[1]) / self.PITCH_SPAN))

    def progress(self, now):
        el = now - self.t_point - self.SETTLE
        if el <= 0:
            return 0.0
        if self.still:
            return min(1.0, el / self.STILL) if len(self.F) >= 12 else 0.0
        cy, cp = self._cover()
        return min(len(self.F) / self.MIN_FRAMES, (cy + cp) / 2)

    def feed(self, feat, blink, now):
        """One camera frame (feat is None when the face or eyes are not
        usable). Returns True when the last dot is done."""
        if self.i >= len(self.points):
            return True
        el = now - self.t_point
        if el >= 0:
            self._intro = False
        if el < self.SETTLE:
            return False
        if feat is not None and blink < BLINK - 0.05:
            self.F.append(feat)
            for k, j in ((0, YAW), (1, PITCH)):
                self.lo[k] = min(self.lo[k], feat[j])
                self.hi[k] = max(self.hi[k], feat[j])
        limit = self.SETTLE + (self.STILL + 2.0 if self.still else self.TIMEOUT)
        if self.progress(now) >= 1.0 or el > limit:
            if len(self.F) < 12:                  # face lost: do it again
                self.t_point = now
                self._clear()
                return False
            if not self.still and el > limit and sum(self._cover()) < 0.6:
                self.still = True                 # no head movement: fine
            self.groups.append((np.array(self.F), self.points[self.i], 1.0))
            self.i += 1
            self.t_point = now
            self._clear()
        return self.i >= len(self.points)

    def result(self, kx, ky):
        return self.groups

    def snapshot(self, now):
        """What the page draws."""
        if self._intro and now < self.t_point:
            return {"phase": "intro", "i": 0, "n": len(self.points),
                    "x": self.points[0][0], "y": self.points[0][1], "p": 0,
                    "hint": "", "left": round(self.t_point - now, 1)}
        i = min(self.i, len(self.points) - 1)
        cy, cp = self._cover()
        if now - self.t_point < self.SETTLE:
            hint = "look"
        elif self.still:
            hint = "hold"
        elif cy < 1.0:
            hint = "turn"
        elif cp < 1.0:
            hint = "nod"
        else:
            hint = "hold"
        return {"phase": "dots", "i": i, "n": len(self.points),
                "x": self.points[i][0], "y": self.points[i][1],
                "p": round(self.progress(now), 3), "hint": hint, "left": 0}


class FixationFilter:
    """Eyes rest on a spot, then jump. While they rest the estimate is a
    running average (steady). When the last few frames agree on a new place
    it moves there at once: three frames for a big jump, seven for a move to
    a neighbour."""

    def __init__(self, noise=60.0):
        self.set_noise(noise)
        self.reset()

    def set_noise(self, noise):
        self.near = max(40.0, 1.5 * noise)       # "not where I was"
        self.far = max(110.0, 2.8 * noise)       # "clearly somewhere else"

    def reset(self):
        self.p = None
        self.n = 0
        self.buf = collections.deque(maxlen=7)
        self.jumped = False

    def update(self, raw):
        raw = np.asarray(raw, float)
        self.jumped = False
        self.buf.append(raw)
        if self.p is None:
            self.p, self.n = raw.copy(), 1
            self.jumped = True
            return self.p
        pts = np.array(self.buf)
        if len(pts) >= 3:
            m3 = np.median(pts[-3:], 0)
            if np.linalg.norm(m3 - self.p) > self.far:
                self.p, self.n, self.jumped = m3, 3, True
                return self.p
        if len(pts) >= 7:
            m7 = np.median(pts, 0)
            if np.linalg.norm(m7 - self.p) > self.near:
                self.p, self.n, self.jumped = m7, 4, True
                return self.p
        if np.linalg.norm(raw - self.p) < self.far:
            self.n = min(self.n + 1, 24)
            self.p = self.p + (raw - self.p) / self.n
        return self.p


def rect_distance(p, rect):
    """Distance from a point to a rectangle (x, y, w, h): 0 at the edge,
    negative inside (how deep), positive outside."""
    x, y, w, h = rect
    dx = max(x - p[0], p[0] - (x + w))
    dy = max(y - p[1], p[1] - (y + h))
    if dx <= 0 and dy <= 0:
        return max(dx, dy)
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


class Snapper:
    """Chooses the target for a gaze point. The current target is sticky: the
    gaze has to be clearly on another one before the highlight moves."""

    REACH = 80.0         # px: how far outside a target still selects it
    STICKY = 60.0        # px: advantage of the current target
    FRAMES = 2           # the challenger must win this many frames running

    def __init__(self):
        self.key = None
        self._rival = None
        self._count = 0

    def reset(self):
        self.key = None
        self._rival, self._count = None, 0

    def update(self, p, targets):
        """targets: [(key, x, y, w, h)]. Returns the chosen (key, x, y, w, h)
        or None."""
        best, best_d, cur, cur_d = None, self.REACH, None, None
        for t in targets:
            d = rect_distance(p, t[1:])
            if t[0] == self.key:
                cur, cur_d = t, d
            if d < best_d:
                best, best_d = t, d
        if cur is None or cur_d > self.REACH + self.STICKY:
            self.key = best[0] if best else None
            self._rival, self._count = None, 0
            return best
        if best is not None and best[0] != self.key \
                and best_d < cur_d - self.STICKY:
            if self._rival == best[0]:
                self._count += 1
            else:
                self._rival, self._count = best[0], 1
            if self._count >= self.FRAMES:
                self.key = best[0]
                self._rival, self._count = None, 0
                return best
        else:
            self._rival, self._count = None, 0
        return cur


def nod_y(offset, mid, height, rows, gain, screen_h):
    """Up-down position (px) for play mode, from the head. `offset` is how
    far the head pointer sits below (+) or above (-) its centre, in px;
    `mid` and `height` describe the pad area, with `rows` rows.

    A small nod is amplified so that it reaches the outer rows quickly.
    Past the middle of an outer row the pointer slows right down, so that
    nodding a bit too far still means that row and not whatever lies
    beyond the pads. Only a big, deliberate nod carries on to the edge of
    the screen (the dock below, the tabs above)."""
    a = abs(offset)
    reach = height * (rows - 1) / (2.0 * rows)     # centre of an outer row
    edge = height / 2.0 - 6                        # just inside the pad area
    near = reach / max(gain, 1.0)                  # nod that gets there
    far = max(2.2 * near, 150.0)                   # still that row up to here
    half = screen_h / 2.0
    if a <= near:
        d = gain * a
    elif a <= far:
        d = reach + (edge - reach) * (a - near) / (far - near)
    else:
        room = (screen_h - 1 - mid) if offset > 0 else mid
        d = edge + (room - edge) * min(1.0, (a - far) / max(half - far, 1.0))
    y = mid + math.copysign(d, offset)
    return min(screen_h - 1.0, max(0.0, y))


class GazePointer:
    """The eye pointer as app.py uses it."""

    NUDGE = 0.6          # how much of a head lean shifts the choice ...
    NUDGE_DEAD = (110.0, 90.0)   # ... beyond this much (about 2.5 degrees):
                         # ordinary head movement must not move the highlight
    NUDGE_AFTER = 0.5    # seconds after the eyes land before nudging counts
    NUDGE_MAX = 240.0
    LEARN = 0.2          # share of a click's error corrected at once
    BIAS_MAX = 170.0
    CLICKS_KEPT = 80

    def __init__(self, path, width, height):
        self.path = path
        self.W, self.H = width, height
        self.model = None
        self.cal = None
        self.dots = []                   # calibration data, kept for refits
        self.clicks = collections.deque(maxlen=self.CLICKS_KEPT)
        self.filter = FixationFilter()
        self.snapper = Snapper()
        self.bias = np.zeros(2)
        self.recent = collections.deque(maxlen=90)   # (time, features, raw)
        self.targets = []
        self.reset()

    @property
    def ready(self):
        return self.model is not None and self.model.w is not None

    @property
    def accuracy(self):
        return self.model.accuracy if self.ready else None

    def reset(self):
        self.filter.reset()
        self.snapper.reset()
        self.recent.clear()
        self.last = None
        self.blink_until = 0.0
        self.t_fix = 0.0
        self.ref = None
        self.n_clicks = 0

    # ---- calibration -----------------------------------------------------
    def start_calibration(self, now, kx, ky, kind="follow"):
        """kind: "follow" (the dot moves, the default) or "dots" (12 fixed
        dots)."""
        make = Calibrator if kind == "dots" else FollowCalibrator
        self.cal = make(self.W, self.H, now)
        self._prior = (kx, ky)
        return self.cal

    def feed_calibration(self, feat, blink, now):
        return self.cal.feed(feat, blink, now)

    def finish_calibration(self):
        """Fit the model from the calibration just shown. False if it failed
        (the old model, if any, stays)."""
        cal, self.cal = self.cal, None
        model = GazeModel(*self._prior)
        if not model.fit(cal.result(*self._prior)):
            return False
        self.model, self.dots = model, model.kept
        self.clicks.clear()
        self.bias[:] = 0
        self.filter.set_noise(model.noise)
        self.reset()
        self.save()
        return True

    def quality(self):
        a = self.accuracy
        if a is None:
            return ""
        return "good" if a < 70 else "fair" if a < 130 else "rough"

    # ---- pointing --------------------------------------------------------
    def update(self, feat, blink, now, targets, head_px, nudge=True,
               head_y=None):
        """One frame. targets: [(key, x, y, w, h)] that can be clicked right
        now; head_px: where the head pointer would be. head_y: if given, the
        up-down position comes from the head (this value, in px) and the
        eyes only decide left-right. A webcam reads left-right eye movement
        far better than up-down: on real calibrations the up-down miss was
        100 px or more whatever the model, against about 70 px left-right.
        Returns {"px": (x, y), "snap": bool, "gaze": (x, y)} or None before
        the first usable frame."""
        self.targets = targets
        self.head_rows = head_y is not None
        if feat is None or blink > BLINK:
            self.blink_until = now + 0.12        # lids distort the eye shape
            return self.last
        if now < self.blink_until:
            return self.last
        raw = self.model.predict(feat)[0]
        self.recent.append((now, feat, raw))
        est = raw + self.bias
        if self.head_rows:
            est = np.array([est[0], 0.0])        # the eyes' up-down is unused
        g = self.filter.update(est)
        if self.filter.jumped:
            self.t_fix, self.ref = now, None
        shift = np.zeros(2)
        if nudge and head_px is not None:
            # Eyes pick, head refines: once the eyes have settled, leaning
            # the head towards a neighbour moves the highlight there.
            if self.ref is None:
                if now - self.t_fix >= self.NUDGE_AFTER:
                    self.ref = np.asarray(head_px, float)
            else:
                lean = np.asarray(head_px, float) - self.ref
                lean = np.sign(lean) * np.maximum(
                    0.0, np.abs(lean) - self.NUDGE_DEAD)
                shift = np.clip(self.NUDGE * lean,
                                -self.NUDGE_MAX, self.NUDGE_MAX)
        p = g + shift
        if self.head_rows:
            p = np.array([p[0], float(head_y)])
            g = p - np.array([shift[0], 0.0])
        t = self.snapper.update(p, targets)
        if t is not None:
            _, x, y, w, h = t
            mx, my = min(14.0, w / 4), min(14.0, h / 4)
            px = (min(max(p[0], x + mx), x + w - mx),
                  min(max(p[1], y + my), y + h - my))
        else:
            px = (min(max(p[0], 0.0), self.W - 1.0),
                  min(max(p[1], 0.0), self.H - 1.0))
        self.last = {"px": px, "snap": t is not None,
                     "gaze": (float(g[0]), float(g[1]))}
        return self.last

    def learn_click(self, px, t_ref):
        """The user clicked the target at px, having looked at it just before
        t_ref: move the estimate towards that target's centre and remember
        the frames for the next refit."""
        if not self.ready:
            return False
        hit = None
        for t in self.targets:
            if rect_distance(px, t[1:]) <= 1.0:
                hit = t
        frames = [(f, r) for (ts, f, r) in self.recent
                  if t_ref - 0.5 <= ts <= t_ref - 0.03]
        if hit is None or len(frames) < 4:
            return False
        _, x, y, w, h = hit
        centre = np.array([x + w / 2, y + h / 2])
        raw = np.mean([r for _, r in frames], 0)
        if getattr(self, "head_rows", False):
            raw = np.array([raw[0], centre[1] - self.bias[1]])   # x only
        if rect_distance(raw + self.bias, hit[1:]) > 220:
            return False                 # eyes were elsewhere: learn nothing
        self.bias += self.LEARN * ((centre - raw) - self.bias)
        np.clip(self.bias, -self.BIAS_MAX, self.BIAS_MAX, out=self.bias)
        self.clicks.append((np.array([f for f, _ in frames]), centre, 0.4))
        self.n_clicks += 1
        if self.n_clicks % 4 == 0:
            before = self.model.w.copy()
            if self.model.fit(self.dots, list(self.clicks), select=False):
                # The refit has absorbed part of the offset: keep the
                # prediction for these frames where it was.
                new = self.model.predict(np.array([f for f, _ in frames])).mean(0)
                self.bias += raw - new
                np.clip(self.bias, -self.BIAS_MAX, self.BIAS_MAX, out=self.bias)
            else:
                self.model.w = before
        return True

    # ---- saved calibration -----------------------------------------------
    def save(self):
        if not self.ready:
            return
        data = {"v": MODEL_VERSION, "W": self.W, "H": self.H,
                "time": time.time(), "prior": [self.model.kx, self.model.ky],
                "dots": [{"t": np.round(T, 1).tolist(), "w": w,
                          "F": np.round(F, 5).tolist()}
                         for F, T, w in self.dots]}
        try:
            with open(self.path, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def load(self):
        """Reuse the last calibration if it is from the last few hours."""
        try:
            with open(self.path) as f:
                data = json.load(f)
            if (data.get("v") != MODEL_VERSION or data.get("W") != self.W
                    or data.get("H") != self.H
                    or time.time() - data.get("time", 0) > MAX_MODEL_AGE):
                return False
            dots = [(np.array(d["F"], float), np.array(d["t"], float),
                     d.get("w", 1.0)) for d in data["dots"]]
            model = GazeModel(*data["prior"])
            if not model.fit(dots):
                return False
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self.model, self.dots = model, model.kept
        self.filter.set_noise(model.noise)
        self.reset()
        return True
