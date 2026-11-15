"""
HandsFree Studio - project memory for Travis.

Each project has one Markdown file, projects/<name>.md. Every request
Travis carries out is added to it, and the file is given to Gemini with the
next request, so you can say things like "the same chords again, but slower"
or "now the second half an octave higher" and it knows what came before,
even after the app has been closed and reopened.

The file is plain Markdown that you can read and edit. Anything you write
under "## Notes" (the style you want, the song's structure, chords you
like) is passed to Gemini too.

Only requests made to Travis are written here. Nothing else that the
microphone hears is stored.

    python project_memory.py            show the current project's memory
"""

import datetime
import os
import re
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "projects")
MAX_CONTEXT_CHARS = 3500        # how much of the file Gemini is given
MAX_NOTES_CHARS = 1200
MAX_PLAYED = 32                 # actions listed per request

TEMPLATE = """# HandsFree Studio project: {name}

Travis reads this file before every request, so it can refer back to what
you asked for earlier in this project. You can edit it.

## Notes

Write anything Travis should know about this project here: the style, the
song's structure, chords or melodies you want to keep.

## Requests
"""


def slug(name):
    """A safe file name for a project name."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s[:60] or "default"


class ProjectMemory:
    def __init__(self, name="default", folder=None):
        self.name = str(name).strip() or "default"
        self.folder = folder or FOLDER
        self.path = os.path.join(self.folder, slug(self.name) + ".md")
        self.lock = threading.Lock()

    # ---- file ------------------------------------------------------------
    def _ensure(self):
        if not os.path.exists(self.path):
            os.makedirs(self.folder, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(TEMPLATE.format(name=self.name))

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    # ---- for Gemini ------------------------------------------------------
    def context(self, max_chars=MAX_CONTEXT_CHARS):
        """The user's notes plus the most recent requests, oldest first.
        Empty string if there is nothing yet."""
        with self.lock:
            text = self._read()
        if not text:
            return ""
        head, _, requests = text.partition("\n## Requests")
        notes = head.partition("\n## Notes")[2].strip()
        if notes.startswith("Write anything Travis should know"):
            notes = ""                          # the untouched placeholder
        notes = notes[:MAX_NOTES_CHARS]
        entries = [e.strip() for e in re.split(r"\n(?=### )", requests)
                   if e.strip().startswith("### ")]
        kept, used = [], len(notes)
        for entry in reversed(entries):         # newest first, until full
            if used + len(entry) > max_chars and kept:
                break
            kept.append(entry)
            used += len(entry)
        kept.reverse()
        parts = []
        if notes:
            parts.append("Notes from the user:\n" + notes)
        if kept:
            parts.append("Earlier requests in this project (oldest first):\n"
                         + "\n\n".join(kept))
        return "\n\n".join(parts)

    # ---- after a request has been carried out ----------------------------
    def add(self, request, played, source="", context=None, when=None):
        """request: what was asked. played: [(label, length_text)], e.g.
        [("Am", "1 bar"), ("F", "1 bar")]. context: tempo, key ..."""
        context = context or {}
        when = when or datetime.datetime.now()
        bits = [when.strftime("%Y-%m-%d %H:%M")]
        if context.get("key"):
            bits.append("key %s" % context["key"])
        if context.get("tempo"):
            bits.append("%s BPM" % context["tempo"])
        items = ["%s (%s)" % (label, length) if length else str(label)
                 for label, length in played[:MAX_PLAYED]]
        if len(played) > MAX_PLAYED:
            items.append("and %d more" % (len(played) - MAX_PLAYED))
        request = " ".join(str(request).split())
        entry = "\n### %s\n- Asked: \"%s\"\n- Played: %s\n" % (
            " · ".join(bits), request, ", ".join(items) or "nothing")
        if source:
            entry += "- Answered by: %s\n" % source
        try:
            with self.lock:
                self._ensure()
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(entry)
        except OSError as exc:                  # never stop the music for this
            print("[memory] could not write %s: %s" % (self.path, exc))


if __name__ == "__main__":
    import json
    name = "default"
    try:
        with open(os.path.join(HERE, "settings.json")) as f:
            name = json.load(f).get("project", "default")
    except (OSError, ValueError):
        pass
    memory = ProjectMemory(name)
    print("Project: %s\nFile: %s\n" % (memory.name, memory.path))
    print(memory.context() or "(nothing yet)")
