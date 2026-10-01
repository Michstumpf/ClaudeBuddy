#!/usr/bin/env python3
"""Push-to-talk for the Ubuntu desktop (X11): hold a key, speak, release.

The audio is recorded with pw-record, transcribed by the hub
(POST /api/transcribe, faster-whisper, never leaves home) and typed into the
focused window, so it works in any terminal or app, tmux or not.

Config (env):
  BUDDY_HUB        hub URL (default http://127.0.0.1:8765)
  BUDDY_TOKEN      token (default: ~/.config/claude-buddy/token)
  BUDDY_PTT_KEY    pynput key name to hold (default f9)
  BUDDY_PTT_ENTER  1 = press Enter after typing (default 1)
"""

import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from pynput import keyboard

HUB = os.environ.get("BUDDY_HUB", "http://127.0.0.1:8765").rstrip("/")
KEY = getattr(keyboard.Key, os.environ.get("BUDDY_PTT_KEY", "f9").lower())
PRESS_ENTER = os.environ.get("BUDDY_PTT_ENTER", "1") == "1"
MIN_SECONDS = 0.4  # shorter holds are accidental taps
# X11 auto-repeat turns a held key into release+press pairs; a release only
# counts if no press follows within this window.
REPEAT_GRACE = 0.08
# Phrases Whisper invents on silence or noise (YouTube subtitle credits).
HALLUCINATIONS = re.compile(r"legendas? pela comunidade|amara\.org|obrigad[oa] por assistir|inscreva-se", re.I)

log = logging.getLogger("buddy.ptt")


def token() -> str:
    if os.environ.get("BUDDY_TOKEN"):
        return os.environ["BUDDY_TOKEN"]
    return (Path.home() / ".config" / "claude-buddy" / "token").read_text(encoding="utf-8").strip()


class Notifier:
    """One desktop notification that is updated in place (notify-send -r)."""

    def __init__(self):
        self.id = "0"

    def show(self, title: str, body: str = "", timeout_ms: int = 0) -> None:
        cmd = ["notify-send", "-a", "Claude Buddy", "-p", "-r", self.id, "-t", str(timeout_ms), title, body]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            self.id = out.stdout.strip() or self.id
        except (OSError, subprocess.SubprocessError):
            pass


class PushToTalk:
    def __init__(self):
        self.notify = Notifier()
        self.typer = keyboard.Controller()
        self.recorder: subprocess.Popen | None = None
        self.wav: Path | None = None
        self.started = 0.0
        self.pending_stop: threading.Timer | None = None
        self.busy = threading.Lock()  # one transcription at a time

    # ---- key events -------------------------------------------------------

    def on_press(self, key):
        if key != KEY:
            return
        if self.pending_stop:  # auto-repeat: still held
            self.pending_stop.cancel()
            self.pending_stop = None
            return
        if self.recorder is None and not self.busy.locked():
            self.start()

    def on_release(self, key):
        if key != KEY or self.recorder is None:
            return
        self.pending_stop = threading.Timer(REPEAT_GRACE, self.stop)
        self.pending_stop.start()

    # ---- recording ----------------------------------------------------------

    def start(self):
        fd, path = tempfile.mkstemp(prefix="buddy-ptt-", suffix=".wav")
        os.close(fd)
        self.wav = Path(path)
        self.recorder = subprocess.Popen(
            ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(self.wav)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.started = time.time()
        self.notify.show("🎤 Ouvindo…", "solte a tecla para enviar")
        log.info("recording")

    def stop(self):
        self.pending_stop = None
        recorder, wav, held = self.recorder, self.wav, time.time() - self.started
        self.recorder = None
        if recorder is None:
            return
        recorder.send_signal(signal.SIGINT)  # lets pw-record finish the WAV header
        try:
            recorder.wait(timeout=2)
        except subprocess.TimeoutExpired:
            recorder.kill()
        if held < MIN_SECONDS:
            self.notify.show("Claude Buddy", "muito curto, segure enquanto fala", 1500)
            wav.unlink(missing_ok=True)
            return
        threading.Thread(target=self.deliver, args=(wav,), daemon=True).start()

    # ---- transcription + typing --------------------------------------------

    def deliver(self, wav: Path):
        with self.busy:
            self.notify.show("⏳ Transcrevendo…")
            try:
                text = self.transcribe(wav.read_bytes())
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("transcription failed: %s", exc)
                self.notify.show("✕ Não consegui transcrever", str(exc), 4000)
                return
            finally:
                wav.unlink(missing_ok=True)
            if not text or HALLUCINATIONS.search(text):
                self.notify.show("Claude Buddy", "não ouvi nada", 1500)
                return
            log.info("typing %d chars", len(text))
            self.notify.show("✓ Enviado" if PRESS_ENTER else "✓ Digitado", text, 3000)
            time.sleep(0.05)
            self.typer.type(text)
            if PRESS_ENTER:
                time.sleep(0.05)
                self.typer.tap(keyboard.Key.enter)

    def transcribe(self, audio: bytes) -> str:
        req = urllib.request.Request(
            f"{HUB}/api/transcribe", data=audio, method="POST",
            headers={"Content-Type": "audio/wav", "X-Buddy-Token": token()},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        log.info("transcribed %.1fs in %.1fs", result.get("audio_seconds", 0), result.get("took_seconds", 0))
        return (result.get("text") or "").strip()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ptt = PushToTalk()
    log.info("hold %s to talk (hub %s, enter=%s)", KEY, HUB, PRESS_ENTER)
    with keyboard.Listener(on_press=ptt.on_press, on_release=ptt.on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
