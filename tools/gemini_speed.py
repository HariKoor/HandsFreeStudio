"""Times the Travis request on several Gemini models at once, to pick the
fastest. python tools/gemini_speed.py   (needs GEMINI_API_KEY)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading, time
import travis

MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash",
          "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest",
          "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]
REQUEST = "g minor chord for a quarter note then f minor"
rows = {}


def run(m):
    t = time.monotonic()
    try:
        plan = travis.gemini_plan(REQUEST, None, {"tempo": 120, "default_octave": 4},
                                  timeout=20, model=m)
        acts = travis.validate_plan(plan)
        rows[m] = (time.monotonic() - t, "OK  " + ", ".join(a["label"] for a in acts))
    except Exception as e:
        rows[m] = (time.monotonic() - t, "ERR " + str(e)[:110].replace("\n", " "))


threads = [threading.Thread(target=run, args=(m,)) for m in MODELS]
for th in threads:
    th.start()
for th in threads:
    th.join()
for m, (dt, msg) in sorted(rows.items(), key=lambda r: r[1][0]):
    print("%-26s %5.1fs  %s" % (m, dt, msg))
