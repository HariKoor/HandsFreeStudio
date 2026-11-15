"""Runs every check. None of them needs a camera, a microphone or FL Studio.

    python tests/run_all.py

Exits with an error if any check fails, so the launcher can stop.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = ["test_core", "test_studio", "test_hum", "test_ui", "test_gaze",
         "test_travis", "test_memory"]


def main():
    failed = []
    for name in TESTS:
        t = time.monotonic()
        r = subprocess.run([sys.executable, os.path.join(HERE, name + ".py")],
                           cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == 0
        print("  %-12s %s  %.1fs" % (name[5:], "ok" if ok else "FAILED",
                                    time.monotonic() - t))
        if not ok:
            failed.append(name)
            print("\n".join("      " + line for line in
                            (r.stdout + r.stderr).strip().splitlines()[-15:]))
    print("All checks passed." if not failed else
          "Failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
