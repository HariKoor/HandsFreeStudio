"""
HandsFree Studio in its own native window. This is the normal way to run it.

    python desktop.py [same options as app.py]

app.py does all the work, unchanged. This file only gives it a native
window (made by pywebview) instead of a browser window. If the native window
cannot be opened for any reason, it falls back to app.py's browser window so
the app still runs.
"""

import sys
import threading
import time


def main():
    try:
        import webview
    except ImportError:
        print("pywebview is not installed, so a browser window is used. "
              "For the native window: pip install pywebview")
        webview = None

    import app
    import ui_web

    if webview is None or "--no-window" in sys.argv:
        return app.main()

    sys.argv.append("--no-window")                  # the window is opened here
    done = threading.Event()

    def run():
        try:
            app.main()
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    while not ui_web.CURRENT["url"] and not done.is_set():
        time.sleep(0.05)
    url = ui_web.CURRENT["url"]
    if not url:
        return

    try:
        window = webview.create_window("HandsFree Studio", url, width=1280,
                                       height=808, min_size=(960, 620),
                                       background_color="#000000")

        def close_when_app_quits():
            done.wait()
            try:
                window.destroy()
            except Exception:
                pass

        threading.Thread(target=close_when_app_quits, daemon=True).start()
        webview.start()                             # returns when it closes
    except Exception as exc:
        print("The native window could not open (%s). Using a browser "
              "window instead." % exc)
        ui_web._open_window(url)
        done.wait()
        return

    ui = ui_web.CURRENT["ui"]
    if ui is not None and not done.is_set():        # window closed by hand
        ui.inputs.put({"type": "key", "key": "q"})
        done.wait(3)


if __name__ == "__main__":
    main()
