<p align="center"><img src="web/logo.png" width="120" alt="HandsFree Studio logo"></p>

# HandsFree Studio

**Make music in FL Studio without using your hands.**

HandsFree Studio turns your webcam and microphone into a full controller for FL Studio. You point with your head or your eyes, click by opening your mouth or raising your eyebrows, and talk to two voice assistants:

- **Mozart** runs the app: *"Mozart, tempo ninety"*, *"Mozart, record"*, *"Mozart, play mode"*, *"Mozart help"*.
- **Travis** writes music from a description, using Google Gemini: *"Travis, a sad progression in A minor"*, *"Travis, a four on the floor beat"*.

It was built for people who can't use a mouse, keyboard or MIDI controller comfortably: people with limited hand mobility, RSI, or injuries. It's also fun for anyone who wants to make a beat with their face.

Built at **HackUMass XIV** (November 2026).

## Features

- **Navigate mode (head):** a head-tracked pointer with smoothing. Open your mouth to click, or hold still to dwell-click.
- **Play mode (eyes):** eye tracking that takes head movement into account. You calibrate it once by following a dot. Look at a pad and raise your eyebrows to play it. It gets more accurate with every click.
- **Four modes:** Pads (melody), Drums (a 16-step grid), Hum (hum a melody and it becomes snapped MIDI notes), and Mix.
- **Voice commands:** speech recognition runs offline with Vosk and only acts on confident, exact commands after the wake word.
- **Travis:** an AI music assistant (Gemini). It checks every answer before anything plays, starts on the next bar, and keeps a per-project memory.
- **One-screen help:** say "Mozart help" or "Travis help".
- **Privacy:** the camera image never leaves the computer.

## Requirements

- A Mac (macOS 12 or later) with a webcam and a microphone
- [Homebrew](https://brew.sh) (used to install Python 3.12 if you don't have it)
- [FL Studio](https://www.image-line.com/) 21 or later (not needed for the mouse test mode)
- Optional: a free [Gemini API key](https://aistudio.google.com/apikey) for Travis

## Setup

### 1. Download

```bash
git clone https://github.com/HariKoor/HandsFreeStudio.git
cd HandsFreeStudio
```

You can also click **Code ▸ Download ZIP** on GitHub, unzip it, and `cd` into the folder.

### 2. Install and run (one command)

```bash
./start.sh
```

That's it. The first run:
1. installs Python 3.12 with Homebrew if it's missing,
2. creates a `.env` file from `.env.example`,
3. creates a virtual environment in `.venv/` and installs everything in `requirements.txt`,
4. copies the FL Studio controller script into `~/Documents/Image-Line/FL Studio/Settings/Hardware/`,
5. runs the self-checks, then starts the app.

After that, `./start.sh` starts the app in a few seconds. It only reinstalls packages when `requirements.txt` changes. There's nothing to compile: it's plain Python plus a small web UI.

When macOS asks, allow **camera** and **microphone** access for Terminal. The first time, Vosk downloads a small English speech model (about 40 MB).

Want to try it without a camera or FL Studio? Run:

```bash
./start.sh --mouse --dry-run
```

In this mode the mouse is your head, a click is opening your mouth, and MIDI is printed instead of sent.

### 3. Environment variables (`.env`)

`start.sh` creates `.env` for you. Open it and paste your key:

```
GEMINI_API_KEY=your-key-here
```

Without a key, Travis and the Beat button fall back to simpler built-in rules, and everything else works the same. **`.env` is in `.gitignore`. Never commit it.**

Optional variables:

| Variable | Default | What it does |
| --- | --- | --- |
| `GEMINI_API_KEY` | (none) | Turns on Gemini for Travis and the Beat button |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Main model Travis uses |
| `TRAVIS_MODELS` | (built in) | Comma-separated models to race against each other |
| `TRAVIS_WAKE` | (none) | Extra spellings of "Travis" for the speech model |
| `TRAVIS_DEBUG` | `0` | `1` prints every sentence heard |

### 4. Connect FL Studio (once)

1. **Create a MIDI port:** open *Audio MIDI Setup* ▸ *Window* ▸ *Show MIDI Studio* ▸ double-click **IAC Driver**. Tick **Device is online** and add or rename a port to `HandsFree`.
2. **In FL Studio:** go to *Options* ▸ *MIDI Settings*. Under *Input*, select the **HandsFree** port, click **Enable**, and set *Controller type* to **HandsFree Studio**. Quit and reopen FL Studio if it doesn't appear in the list.
3. Use the default template: channels 1–4 are Kick, Clap, Hat and Snare. Put an instrument on channel 5 for the pads.

To check the connection, run `.venv/bin/python tools/check_fl_studio.py` and watch FL Studio react to each command.

## Running

| Command | What it does |
| --- | --- |
| `./start.sh` | Normal start: head pointer, mouth click, voice |
| `./start.sh --eyes` (or double-click `Eye tracking.command`) | Start in play mode (eye pointer) |
| `./start.sh --mouse --dry-run` (or `Test with mouse.command`) | Mouse instead of camera; no FL Studio needed |
| `./start.sh --project "my song"` | Travis keeps a separate memory for each project |
| `./start.sh --browser` | Opens in a browser window instead of the app window |
| `./start.sh --check` | Runs the self-checks and stops |

Once it's running, say **"Mozart help"** to see every command on one screen.

## Built with

[MediaPipe Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) (face and eye tracking), [OpenCV](https://opencv.org/) (camera), [Vosk](https://alphacephei.com/vosk/) (offline speech recognition), [Google Gemini API](https://ai.google.dev/) (Travis), [mido](https://mido.readthedocs.io/) and [python-rtmidi](https://github.com/SpotlightKid/python-rtmidi) (MIDI), [sounddevice](https://python-sounddevice.readthedocs.io/) (microphone), [pywebview](https://pywebview.flowrl.com/) (app window), NumPy, and the FL Studio MIDI scripting API. The fonts in `web/fonts/` are under the SIL Open Font License (see `web/fonts/LICENSE.txt`). Everything else in this repository was written during HackUMass XIV.

---

# For developers

## Folder structure

| Path | What it is |
| --- | --- |
| `app.py` | The main loop: camera, pointer, clicks, commands, settings. Publishes the UI state every frame |
| `desktop.py` | Opens the app in its own window (pywebview), falling back to a browser window |
| `ui_web.py` | Small local web server for the window: streams state out, gets button positions and keys back. Standard library only |
| `web/` | The window: `index.html`, `app.js` (all drawing, including the grapes), `app.css`, `logo.png`, bundled fonts |
| `face_tracker.py` | Webcam + MediaPipe: head angle, mouth, eyebrows, blinks and the eye points the eye pointer needs |
| `core.py` | Pointer smoothing (OneEuro), mouth and eyebrow click triggers, the pad grid |
| `gaze.py` | The eye pointer for play mode: calibration, the gaze model, fixation filter, snapping to targets |
| `studio.py` | Drum grid, app commands, Mozart voice commands and help sheet, microphone and speech recognition |
| `travis.py` | Travis: wake word, Gemini request and validation, chord spelling, note timeline |
| `project_memory.py` | Travis's memory, one Markdown file per project in `projects/` |
| `hum.py` | Hum mode: pitch detection and snapping to the scale and to sixteenth notes |
| `bridge.py` | Sends notes and commands to FL Studio over MIDI |
| `fl_script/device_HandsFreeStudio.py` | The controller script FL Studio runs; the launcher copies it into place |
| `face_landmarker.task` | MediaPipe's face model (bundled so the first run works offline) |
| `tests/` | Checks that need no camera, mic or FL Studio. `python tests/run_all.py` |
| `tools/` | Developer tools: `check_fl_studio.py` (watch FL Studio respond to each command), `gemini_speed.py` (time Gemini models) |
| `requirements.txt` | Python packages |
| `start.sh` | One command to install and run: Python 3.12, `.env`, then `run_mac.command` |
| `run_mac.command` | Creates `.venv`, installs packages, copies the FL script, runs checks, starts the app |

Created on first use, personal, **not shipped** (see `.gitignore`): `.env` (API key), `settings.json` (your settings), `gaze_model.json` (your eye calibration), `projects/` (Travis's memory), `.venv/`.

## How it fits together

```
camera ──> face_tracker ──> app.py ──────────────> bridge ──MIDI──> FL Studio (fl_script)
                             │  ^  │
  mic ──> studio (Mozart) ───┘  │  └──> ui_web ──state──> web/ window
       └> travis (Gemini) ──────┘          <──button positions, keys──┘
```

- **Pointer.** Navigate mode: head angle → OneEuro smoothing → pointer; opening the mouth clicks, and the click lands where the pointer was just before the mouth opened. Play mode (`gaze.py`): the eyes choose the pad's column, a small nod chooses its row, the eyebrows click, and every click improves the calibration.
- **Hit testing.** The window reports where each clickable element is (`data-hit` in `app.js`), and `ui_web.hit()` tells `app.py` what the pointer is over.
- **Voice.** One microphone, two recognisers (Vosk, on the computer). Mozart's recogniser only knows the command phrases and acts only on a confident, exact command after the wake word. Travis's recogniser hears everything, but a sentence that does not start with "Travis" (after words like "hey" or "okay") is thrown away; requests go to Gemini as text plus audio, and the reply is checked before anything plays.
- **MIDI.** Notes go on channel 1 and commands as Control Change on channel 16. The command numbers are listed at the top of `bridge.py` and the FL script; keep the two in step.
- **Recording.** "Record" switches FL Studio's record button on and starts the transport, because FL Studio only writes notes while it is running. "Stop" (or "stop recording", or "record" again) stops the transport and switches record off; with nothing playing or recording it does nothing. The FL script decides this from FL Studio's real state.
- **Clear notes.** "Clear" empties the drum grid. "Clear notes" (or "clear the piano roll") deletes the notes of the channel the pads play. Controller scripts have no delete-notes function, so the app sends two commands half a second apart: the first opens that channel's piano roll, the second presses Delete, and only if the piano roll has the focus. Not yet confirmed working in FL Studio; "undo" is the fallback.
- **Travis and octaves.** Chords and notes play in the pads' octave unless Gemini sets one. If Gemini answers with "octave up/down" plus music, the app shifts that music and leaves the pads alone.
- **Script version.** The FL script prints its version in View > Script output when it loads (`script loaded v4.4 ...`). After changing the script, quit and reopen FL Studio.

## app.py options

```
python app.py [--mouse] [--dry-run] [--no-voice] [--camera N] [--eyes]
              [--project NAME] [--no-window]
```

## Settings

`settings.json` is written as you change things in the menu. Defaults are in `DEFAULTS` in `app.py`. The ones without a menu tile:

| Key | Default | Meaning |
| --- | --- | --- |
| `melody_channel` | 4 | Channel rack row the pads and Hum play (0 = first) |
| `wake_word` | `mozart` | Mozart's wake word (restart needed) |
| `voice_confidence` | 0.85 | How sure the recogniser must be of every word (0–1) |
| `dwell_time` | 1.0 | Seconds to hold still for a dwell click |
| `hum_latency` | 0.03 | Microphone delay in seconds; larger moves hummed notes earlier |
| `play_rows` | 2 | Rows of pads in play mode (2, 3 or 4; 0 = leave the grid alone) |
| `play_rows_by` | `head` | Play mode: a nod picks the row; `eyes` lets the eyes pick it too |
| `play_nod_gain` | 2.5 | How small the play-mode nod can be (higher = smaller) |
| `eye_nudge` | true | Play mode: a clear head lean moves to the neighbouring pad |
| `eye_calibration` | `follow` | `follow` (moving dot) or `dots` (12 fixed dots) |

Environment: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.5-flash-lite`), `TRAVIS_MODELS`, `TRAVIS_WAKE` (extra spellings of "Travis"), `TRAVIS_DEBUG=1` (print every sentence heard).

## Development

- `python tests/run_all.py` runs all checks in about 5 seconds. `tests/test_gaze.py` uses a simulated face, so it proves the eye pointer's maths, not how well a given webcam sees an iris.
- `python app.py --mouse --no-voice --dry-run` runs the whole app with the mouse; press `t` to send Travis a demo request, `?` for the help sheet.
- `python travis.py "g minor for a quarter note then f minor"` shows what Gemini returns, with no app running.
- `python tools/check_fl_studio.py` steps through every MIDI command while you watch FL Studio.
- The help sheet ("Mozart help") is generated from `HELP` in `studio.py`, and a check fails if a voice command is missing from it.

## Known limits

- Play mode: the eyes pick the column reliably; picking the row (by nod) is not reliable yet.
- Clear notes is unconfirmed in FL Studio (see above).
- Travis only knows what it wrote itself; it cannot replay or edit notes that were played on the pads or hummed.
- Not yet tested by people with limited mobility.
- macOS only. Travis and the Gemini beat need an internet connection.

## Privacy

Speech is transcribed on the computer. Only sentences that start with "Travis" are sent anywhere: to Gemini, as text and audio. Only requests to Travis are written to `projects/`. The camera image never leaves the computer and is never saved.
