# name=HandsFree Studio
"""
HandsFree Studio - FL Studio side.

Install: copy this file to
  Documents/Image-Line/FL Studio/Settings/Hardware/HandsFree Studio/device_HandsFreeStudio.py
then pick "HandsFree Studio" as the controller type for the HandsFree MIDI
input port (Options > MIDI Settings).

Protocol (must match bridge.py):
  - Notes on MIDI channels 1-15 are NOT touched. FL Studio plays them on the
    selected channel like any MIDI keyboard.
  - Control Change messages on MIDI channel 16 are commands.
    CC number = command, CC value = argument.

Debugging: View > Script output shows the print() lines below.
"""

import transport
import channels
import patterns
import general
import ui
import midi

CMD_CHANNEL = 15          # MIDI channel 16, zero-based

# --- commands that act on the SELECTED channel ---------------------------
CMD_PLAY = 1
CMD_STOP = 2
CMD_RECORD = 3            # toggle record arm
CMD_TEMPO = 4             # bpm = 60 + value  (60..187)
CMD_SELECT_CHANNEL = 5    # value = channel rack index
CMD_NEXT_CHANNEL = 6
CMD_PREV_CHANNEL = 7
CMD_VOLUME_SET = 8        # value 0..127 -> 0..1
CMD_VOLUME_UP = 9
CMD_VOLUME_DOWN = 10
CMD_MUTE = 11             # toggle mute on selected channel
CMD_STEP_TOGGLE = 12      # value = step 0..63 on selected channel
CMD_STEP_SET = 13         # value = step, turn ON
CMD_STEP_CLEAR = 14       # value = step, turn OFF
CMD_CLEAR_STEPS = 15      # clear first 16 steps of selected channel
CMD_UNDO = 16
CMD_NEXT_PATTERN = 17
CMD_PREV_PATTERN = 18
CMD_LOOP_MODE = 19        # toggle pattern / song mode

CMD_CLEAR_NOTES = 20      # value = channel rack index; empties its piano roll
WID_PIANO_ROLL = 3
CMD_CLEAR_NOTES_GO = 21   # step 2: press Delete in the piano roll

# --- commands that name a channel directly (drum grid) -------------------
CMD_DRUM_CLEAR = 22       # value = number of channels to clear
CMD_DRUM_STEP = 40        # 40..47 = channel 0..7; value = step * 2 + on
CMD_DRUM_MUTE = 50        # 50..57 = channel 0..7; value = 1 muted, 0 unmuted
CMD_DRUM_VOLUME = 60      # 60..67 = channel 0..7; value 0..127
MAX_DRUM_CHANNELS = 8
STEPS = 16

CMD_PING = 127            # connection test


def _say(text):
    print("HandsFree: " + text)
    try:
        ui.setHintMsg("HandsFree: " + text)
    except Exception:
        pass


def _selected():
    return channels.selectedChannel()


def _select(index):
    count = channels.channelCount()
    if count <= 0:
        return
    index = max(0, min(count - 1, index))
    channels.selectOneChannel(index)
    _say("channel " + str(index + 1) + ": " + channels.getChannelName(index))


def _nudge_volume(delta):
    i = _selected()
    v = channels.getChannelVolume(i) + delta
    v = max(0.0, min(1.0, v))
    channels.setChannelVolume(i, v)
    _say("volume " + str(int(v * 100)) + "%")


def _has(index):
    return index < channels.channelCount()


def _pattern_mode():
    try:
        if transport.getLoopMode() == 1:          # song mode -> pattern mode
            transport.setLoopMode()
    except Exception:
        pass


def _stop():
    """One "stop" for everything: it ends a recording (the transport stops
    first so the take is kept, then record goes off) and does nothing when
    FL Studio is idle."""
    recording = bool(transport.isRecording())
    playing = bool(transport.isPlaying())
    if playing:
        transport.stop()
    if recording:
        transport.record()                        # record button off
    if recording:
        _say("recording stopped")
    elif playing:
        _say("stop")
    else:
        _say("nothing to stop")


def _run(cmd, value):
    if cmd == CMD_PLAY:
        _pattern_mode()
        if not transport.isPlaying():
            transport.start()
        _say("play")
    elif cmd == CMD_STOP:
        _stop()
    elif cmd == CMD_RECORD:
        # One step: "record" switches record on AND starts the transport,
        # because FL Studio only writes notes while it is running. Saying
        # "record" again while recording ends it, like "stop".
        if transport.isRecording():
            _stop()
        else:
            _pattern_mode()
            transport.record()                    # record button on
            if not transport.isPlaying():
                transport.start()
            _say("recording")
    elif cmd == CMD_TEMPO:
        bpm = 60 + value
        general.processRECEvent(
            midi.REC_Tempo, bpm * 1000,
            midi.REC_Control | midi.REC_UpdateControl)
        _say("tempo " + str(bpm))
    elif cmd == CMD_SELECT_CHANNEL:
        _select(value)
    elif cmd == CMD_NEXT_CHANNEL:
        _select(_selected() + 1)
    elif cmd == CMD_PREV_CHANNEL:
        _select(_selected() - 1)
    elif cmd == CMD_VOLUME_SET:
        channels.setChannelVolume(_selected(), value / 127.0)
        _say("volume " + str(int(value / 127.0 * 100)) + "%")
    elif cmd == CMD_VOLUME_UP:
        _nudge_volume(0.1)
    elif cmd == CMD_VOLUME_DOWN:
        _nudge_volume(-0.1)
    elif cmd == CMD_MUTE:
        channels.muteChannel(_selected())
        _say("mute toggled")
    elif cmd == CMD_STEP_TOGGLE:
        i = _selected()
        on = not channels.getGridBit(i, value)
        channels.setGridBit(i, value, on)
        _say("step " + str(value + 1) + (" on" if on else " off"))
    elif cmd == CMD_STEP_SET:
        channels.setGridBit(_selected(), value, True)
    elif cmd == CMD_STEP_CLEAR:
        channels.setGridBit(_selected(), value, False)
    elif cmd == CMD_CLEAR_STEPS:
        i = _selected()
        for step in range(STEPS):
            channels.setGridBit(i, step, False)
        _say("steps cleared")
    elif cmd == CMD_UNDO:
        general.undoUp()
        _say("undo")
    elif cmd == CMD_CLEAR_NOTES:
        # Step 1 of "clear notes". There is no "delete notes" function for
        # controller scripts, so this does what a person would: open that
        # channel's piano roll, then (step 2, sent by the app a moment later
        # so the window has time to open) press Delete. With nothing
        # selected, Delete removes every note.
        if _has(value):
            channels.selectOneChannel(value)
        _focus_piano_roll()
        _say("clearing notes: piano roll opened")
    elif cmd == CMD_CLEAR_NOTES_GO:
        if not _piano_roll_focused():
            _focus_piano_roll()
        # Delete only when the piano roll really has the focus. In any other
        # window the Delete key could remove something else, such as a channel.
        if not _piano_roll_focused():
            _say("piano roll does not have the focus; nothing deleted")
        else:
            try:
                ui.selectAll()
            except Exception:
                pass
            result = ui.delete()
            _say("notes cleared (delete returned " + str(result) + ")")
    elif cmd == CMD_NEXT_PATTERN:
        patterns.jumpToPattern(patterns.patternNumber() + 1)
        _say("pattern " + str(patterns.patternNumber()))
    elif cmd == CMD_PREV_PATTERN:
        patterns.jumpToPattern(max(1, patterns.patternNumber() - 1))
        _say("pattern " + str(patterns.patternNumber()))
    elif cmd == CMD_LOOP_MODE:
        transport.setLoopMode()
        _say("loop mode toggled")
    elif cmd == CMD_DRUM_CLEAR:
        for index in range(min(value, channels.channelCount())):
            for step in range(STEPS):
                channels.setGridBit(index, step, False)
        _say("drums cleared")
    elif CMD_DRUM_STEP <= cmd < CMD_DRUM_STEP + MAX_DRUM_CHANNELS:
        index = cmd - CMD_DRUM_STEP
        if _has(index):
            channels.setGridBit(index, value // 2, bool(value % 2))
    elif CMD_DRUM_MUTE <= cmd < CMD_DRUM_MUTE + MAX_DRUM_CHANNELS:
        index = cmd - CMD_DRUM_MUTE
        if _has(index) and bool(channels.isChannelMuted(index)) != bool(value):
            channels.muteChannel(index)
    elif CMD_DRUM_VOLUME <= cmd < CMD_DRUM_VOLUME + MAX_DRUM_CHANNELS:
        index = cmd - CMD_DRUM_VOLUME
        if _has(index):
            channels.setChannelVolume(index, value / 127.0)
    elif cmd == CMD_PING:
        _say("connected " + VERSION)
    else:
        _say("unknown command " + str(cmd))


VERSION = "v4.4 (record starts FL, stop ends recording, clear notes)"


def OnInit():
    _say("script loaded " + VERSION)


def _focus_piano_roll():
    ui.showWindow(WID_PIANO_ROLL)
    try:
        ui.setFocused(WID_PIANO_ROLL)
    except Exception:
        pass


def _piano_roll_focused():
    try:
        return bool(ui.getFocused(WID_PIANO_ROLL))
    except Exception:
        return False


def OnDeInit():
    print("HandsFree: script unloaded")


def OnMidiMsg(event):
    is_cc = (event.status & 0xF0) == 0xB0
    on_cmd_channel = (event.status & 0x0F) == CMD_CHANNEL
    if not (is_cc and on_cmd_channel):
        return  # notes etc. fall through to FL Studio
    event.handled = True
    try:
        _run(event.data1, event.data2)
    except Exception as e:
        _say("error in command " + str(event.data1) + ": " + str(e))
