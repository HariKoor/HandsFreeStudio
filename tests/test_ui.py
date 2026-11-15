"""Checks for the new UI plumbing. No browser, camera or FL Studio needed:
    python tests/test_ui.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json

import app
from ui_web import WebUI

ui = WebUI()
ui._set_layout({"view": "pads", "targets": [
    {"hit": ["pads"], "x": 24, "y": 100, "w": 1232, "h": 488},
    {"hit": ["button", "PLAY"], "x": 300, "y": 622, "w": 64, "h": 64},
    {"hit": ["cmd", ["toggle", "dwell"]], "x": 900, "y": 622, "w": 64, "h": 64},
    {"hit": ["none"], "x": 600, "y": 300, "w": 100, "h": 100},
]})

# Buttons and commands come back as tuples, ready for click()
assert ui.hit("pads", 330, 650) == ("button", "PLAY")
assert ui.hit("pads", 930, 650) == ("cmd", ("toggle", "dwell"))
# A few pixels outside a button still counts (forgiving edges) ...
assert ui.hit("pads", 300 - 4, 650) == ("button", "PLAY")
# ... but not far outside
assert ui.hit("pads", 200, 650) is None
# The pad area reports where in it the pointer is, 0..1
kind, x, y = ui.hit("pads", 24 + 1232 / 2, 100 + 488 / 4)
assert kind == "pads" and abs(x - 0.5) < 1e-6 and abs(y - 0.25) < 1e-6
# A card on top blocks what is under it
assert ui.hit("pads", 650, 350) is None
# The eye pointer asks for the targets that can be reached: not the card,
# and nothing hidden under it
ui._set_layout({"view": "drums", "targets": [
    {"hit": ["step", 0, 0], "x": 100, "y": 100, "w": 58, "h": 108},
    {"hit": ["step", 0, 1], "x": 164, "y": 100, "w": 58, "h": 108},
    {"hit": ["none"], "x": 150, "y": 90, "w": 400, "h": 200},
    {"hit": ["cmd", ["beat_keep"]], "x": 200, "y": 200, "w": 180, "h": 72},
]})
assert [t[0] for t in ui.targets("drums")] == [("step", 0, 0), ("cmd", ("beat_keep",))]
assert [t[0] for t in ui.targets("pads")] == [("pads",), ("button", "PLAY"),
                                             ("cmd", ("toggle", "dwell"))]
v = ui.layout_version
ui._set_layout({"view": "mix", "targets": []})
assert ui.layout_version == v + 1 and ui.targets("mix") == []
assert app.key_command("p", False) == ("pointer", None)
assert app.key_command("i", False) == ("gazecal",)

# Views the page has not reported yet have no targets
assert ui.hit("menu", 330, 650) is None and not ui.has_layout("menu")

# Keys map to the same commands in both UIs
assert app.key_command("q", False) == "quit"
assert app.key_command(" ", False) == ("play",)
assert app.key_command(" ", True) == ("stop",)
assert app.key_command("tab", False) == ("mode", None)
assert app.key_command("z", False) is None

# The state the page draws from is plain JSON
ui.publish({"view": "pads", "hover": ("button", "PLAY")})
assert json.loads(ui._state)["hover"] == ["button", "PLAY"]

print("ui tests passed")
