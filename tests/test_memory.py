"""Checks for Travis's project memory. No microphone or network needed:
    python tests/test_memory.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datetime
import os
import queue
import shutil
import tempfile
import time

import travis
from project_memory import ProjectMemory, slug

tmp = tempfile.mkdtemp()
try:
    assert slug("My Song #2!") == "my-song-2" and slug("  ") == "default"
    m = ProjectMemory("Demo Song", folder=tmp)
    assert m.path.endswith("demo-song.md")
    assert m.context() == ""                         # nothing yet, no file
    when = datetime.datetime(2026, 11, 14, 15, 50)
    m.add("a minor then f major each for a bar", [("Am", "1 bar"), ("F", "1 bar")],
          "Gemini", {"key": "C minor", "tempo": 120}, when)
    text = open(m.path, encoding="utf-8").read()
    assert text.startswith("# HandsFree Studio project: Demo Song")
    assert "### 2026-11-14 15:50 · key C minor · 120 BPM" in text
    assert '- Asked: "a minor then f major each for a bar"' in text
    assert "- Played: Am (1 bar), F (1 bar)" in text and "- Answered by: Gemini" in text
    ctx = m.context()
    assert "Earlier requests" in ctx and "Am (1 bar)" in ctx
    assert "Write anything Travis" not in ctx        # placeholder is not sent

    # The user's own notes are passed on
    text = text.replace("Write anything Travis should know about this project here: the style, the\n"
                        "song's structure, chords or melodies you want to keep.", "Lo-fi, slow, jazzy chords.")
    open(m.path, "w", encoding="utf-8").write(text)
    assert "Notes from the user:\nLo-fi, slow, jazzy chords." in m.context()

    # Long histories: the newest requests are kept, the oldest dropped
    for i in range(80):
        m.add("request number %d" % i, [("C", "1 beat")] * 4, "Offline", {"tempo": 100})
    ctx = m.context()
    assert len(ctx) < 5200 and "request number 79" in ctx and "request number 0\"" not in ctx
    assert ctx.index("request number 78") < ctx.index("request number 79")   # oldest first

    # A second project has its own file and its own memory
    other = ProjectMemory("Other", folder=tmp)
    assert other.context() == "" and other.path != m.path

    # A new ProjectMemory (the app restarted) still remembers
    assert "request number 79" in ProjectMemory("Demo Song", folder=tmp).context()

    # Travis writes what it carried out, and sends the memory to Gemini
    mem = ProjectMemory("Travis test", folder=tmp)
    q = queue.Queue()
    t = travis.Travis(q, context_fn=lambda: {"tempo": 90, "key": "G minor",
                                             "default_octave": 4}, use_gemini=False)
    t.memory = mem
    t.submit("g minor chord for a quarter note then f minor")
    kind, actions, info = q.get(timeout=5)
    assert kind == "travis_plan" and [a["label"] for a in actions] == ["Gm", "Fm"]
    saved = mem.context()
    assert '"g minor chord for a quarter note then f minor"' in saved
    assert "Gm oct 4 (" in saved and "Fm oct 4 (" in saved and "90 BPM" in saved

    seen = {}

    def fake_race(text, audio, ctx, default_octave=4, **kw):
        seen.update(ctx)
        return "fake-model", {"actions": [{"type": "chord", "symbol": "Gm", "beats": 1}],
                              "say": "again", "clarify": ""}, 0.01

    travis.gemini_race = fake_race
    t2 = travis.Travis(q, context_fn=lambda: {"tempo": 90, "default_octave": 4},
                       use_gemini=True)
    t2.memory = mem
    t2.submit("the same again")
    q.get(timeout=5)
    assert "g minor chord for a quarter note" in seen["project_memory"]

    # The request text Gemini gets ends with the memory, not inside the JSON
    sent = {}

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json
            return json.dumps({"candidates": [{"content": {"parts": [{"text": '{"actions": [], "say": "", "clarify": "x"}'}]}}]}).encode()

    def fake_open(req, timeout=None):
        import json
        sent["body"] = json.loads(req.data)
        return Reply()

    travis.urllib.request.urlopen = fake_open
    os.environ["GEMINI_API_KEY"] = "test"
    travis.gemini_plan("the same again", None, {"tempo": 90, "project_memory": "MEMORY HERE"})
    ask = sent["body"]["contents"][0]["parts"][-1]["text"]
    assert ask.endswith("Project memory:\nMEMORY HERE") and '"project_memory"' not in ask
    print("memory tests passed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
