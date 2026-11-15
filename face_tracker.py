"""
HandsFree Studio - face tracker.

Reads the webcam and returns, for each frame:
  yaw, pitch  head rotation in degrees (right / up are positive)
  mouth       mouth openness: lip gap divided by face height (0 = closed)
  eyes_closed 0..1, how closed both eyes are
  brow        0..1, how far the eyebrows are raised
  gaze        the eye points, face matrix and eye-look scores that the eye
              pointer (gaze.py) reads; None if the eyes were not found
  frame       the mirrored camera image, for the on-screen preview

Uses MediaPipe Face Landmarker. The model file (about 4 MB) is downloaded
next to this script the first time it runs. Nothing is recorded or uploaded.
"""

import math
import os
import queue
import threading
import time
import urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from gaze import GAZE_POINTS, LOOKS

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "face_landmarker.task")

# Face mesh landmark numbers
NOSE_TIP = 1
LIP_UPPER = 13
LIP_LOWER = 14
FOREHEAD = 10
CHIN = 152
CHEEK_L = 234
CHEEK_R = 454
DETECT_WIDTH = 480      # frames are shrunk to this width before tracking
HIRES_WIDTH = 960       # ... or to this when the eye pointer is on: the iris
                        # moves only a few pixels, so it needs a sharper face
HD_DETECT_WIDTH = 640   # head pointer on a 1280 x 720 camera (see hd below)


class FaceState:
    def __init__(self, frame):
        self.frame = frame
        self.found = False
        self.yaw = 0.0
        self.pitch = 0.0
        self.mouth = 0.0
        self.eyes_closed = 0.0   # 0 open .. 1 both eyes shut
        self.brow = 0.0          # 0 relaxed .. 1 eyebrows fully raised
        self.points = []      # a few (x, y) in 0..1 for the preview
        self.gaze = None      # see gaze.features()


def _ensure_model():
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 100000:
        return
    print("Downloading face model (one time)...")
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as e:
        raise RuntimeError(
            "Could not download the face model (%s).\nDownload it in a "
            "browser from\n  %s\nand save it as\n  %s" % (e, MODEL_URL, MODEL_PATH))
    print("Saved", MODEL_PATH)


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


class FaceTracker:
    def __init__(self, camera=0, hd=False):
        """hd=True opens the camera at 1280 x 720 and keeps it there. The
        eye pointer needs the sharper picture, and the head pointer then
        uses a shrunk copy of the same picture. One camera format for both
        modes matters: switching formats changes how the head angle reads,
        which would move the pointer's centre every time the mode changes
        (and the switch itself takes a moment)."""
        _ensure_model()
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True)
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        self.cap = cv2.VideoCapture(camera)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280 if hd else 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720 if hd else 480)
        self._low = HD_DETECT_WIDTH if hd else DETECT_WIDTH
        if not self.cap.isOpened():
            raise RuntimeError(
                "Could not open camera %d. On Mac, allow camera access for "
                "Terminal in System Settings > Privacy & Security > Camera, "
                "or try --camera 1." % camera)
        self._t0 = time.monotonic()
        self._last_ms = -1
        self.fps = 0.0
        self._last_frame_t = None
        self._queue = None
        self._running = False
        self.detect_width = self._low

    def set_hires(self, on):
        """Track on sharper frames for the eye pointer (on) or the usual
        small, fast ones (off). The camera itself is not touched."""
        self.detect_width = HIRES_WIDTH if on else self._low

    # ---- background capture ----------------------------------------------
    def start(self):
        """Read and track the camera on its own thread, so drawing the
        window does not wait for the camera and the camera does not wait
        for the window. Use get() instead of read() afterwards."""
        self._queue = queue.Queue(maxsize=1)
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            state = self.read()
            if state is None:
                time.sleep(0.02)
                continue
            try:
                self._queue.put_nowait(state)
            except queue.Full:                 # the window is behind: drop
                try:                           # the stale frame
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                self._queue.put(state)

    def get(self, timeout=0.5):
        """Next tracked frame, or None if the camera has stalled."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        now = time.monotonic()
        if self._last_frame_t is not None:
            dt = max(1e-3, now - self._last_frame_t)
            self.fps = 0.9 * self.fps + 0.1 / dt if self.fps else 1.0 / dt
        self._last_frame_t = now
        h, w = frame.shape[:2]
        dw = self.detect_width
        if w > dw:                                      # smaller = much faster
            frame = cv2.resize(frame, (dw, int(h * dw / w)),
                               interpolation=cv2.INTER_AREA)
        frame = cv2.flip(frame, 1)                      # mirror view
        state = FaceState(frame)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ms = int((time.monotonic() - self._t0) * 1000)
        if ms <= self._last_ms:
            ms = self._last_ms + 1
        self._last_ms = ms
        result = self.landmarker.detect_for_video(image, ms)
        if not result.face_landmarks:
            return state

        lm = result.face_landmarks[0]
        state.found = True
        face_h = _dist(lm[FOREHEAD], lm[CHIN]) or 1e-6
        state.mouth = _dist(lm[LIP_UPPER], lm[LIP_LOWER]) / face_h
        state.points = [(lm[i].x, lm[i].y)
                        for i in (NOSE_TIP, LIP_UPPER, LIP_LOWER,
                                  FOREHEAD, CHIN, CHEEK_L, CHEEK_R)]

        shapes = getattr(result, "face_blendshapes", None)
        scores = {}
        if shapes:
            scores = {c.category_name: c.score for c in shapes[0]}
            state.eyes_closed = min(scores.get("eyeBlinkLeft", 0.0),
                                    scores.get("eyeBlinkRight", 0.0))
            state.brow = (scores.get("browInnerUp", 0.0)
                          + scores.get("browOuterUpLeft", 0.0)
                          + scores.get("browOuterUpRight", 0.0)) / 3

        matrices = getattr(result, "facial_transformation_matrixes", None)
        if matrices:
            m = matrices[0]
            # Direction the face points, in camera space (z toward camera).
            fx, fy, fz = float(m[0][2]), float(m[1][2]), float(m[2][2])
            state.yaw = math.degrees(math.atan2(fx, fz))
            state.pitch = math.degrees(math.atan2(fy, fz))
        else:
            # Fallback from landmarks alone (less accurate up/down).
            face_w = _dist(lm[CHEEK_L], lm[CHEEK_R]) or 1e-6
            mid_x = (lm[CHEEK_L].x + lm[CHEEK_R].x) / 2
            mid_y = (lm[FOREHEAD].y + lm[CHIN].y) / 2
            state.yaw = (lm[NOSE_TIP].x - mid_x) / face_w * 90
            state.pitch = -(lm[NOSE_TIP].y - mid_y) / face_h * 180

        if len(lm) > max(GAZE_POINTS):                  # the mesh has irises
            fh, fw = frame.shape[:2]
            state.gaze = {
                "pts": {i: (lm[i].x * fw, lm[i].y * fh) for i in GAZE_POINTS},
                "m": matrices[0] if matrices else None,
                "look": {k: scores.get(k, 0.0) for k in LOOKS},
                "blink": (scores.get("eyeBlinkLeft", 0.0)
                          + scores.get("eyeBlinkRight", 0.0)) / 2}
        return state

    def close(self):
        self._running = False
        time.sleep(0.1)
        self.cap.release()
        self.landmarker.close()
