"""Types dictated text into whatever window has focus on this machine.

For sessions that are not in tmux (the hub can only type into a given session
through tmux): the Buddy's "Janela em foco" target. It is the F9 push-to-talk's
virtual keyboard (ptt/buddy_ptt.py: uinput, ABNT2 layout, accents through dead
keys), so the user must be in the "input" group, as for F9. Linux only.
"""

import importlib.util
import logging
import threading
from pathlib import Path

log = logging.getLogger("buddy.focus")

PTT_SCRIPT = Path(__file__).resolve().parents[2] / "ptt" / "buddy_ptt.py"


class FocusTyper:
    def __init__(self):
        self._typer = None
        self._lock = threading.Lock()  # one text at a time, letters never interleave

    def _load(self):
        if self._typer is None:
            spec = importlib.util.spec_from_file_location("buddy_ptt", PTT_SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._typer = module.UInputTyper()
        return self._typer

    def type(self, text: str, press_enter: bool = True) -> None:
        with self._lock:
            typer = self._load()
            typer.type(text)
            if press_enter:
                typer.enter()

    def type_soon(self, text: str) -> None:
        """In the background: typing takes a moment and must not hold the hub."""
        def run():
            try:
                self.type(text)
            except Exception:
                log.exception("could not type in the focused window")
        threading.Thread(target=run, daemon=True).start()
