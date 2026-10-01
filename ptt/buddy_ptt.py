#!/usr/bin/env python3
"""Push-to-talk for the Ubuntu desktop (X11).

Two ways to use the key:
  - hold it while speaking, release to send;
  - tap it, just speak, and it sends by itself after a short silence
    (tap again to send right away).

The audio is recorded with pw-record, transcribed by the hub
(POST /api/transcribe, faster-whisper, never leaves home) and typed into the
focused window, so it works in any terminal or app, tmux or not.

Config (env):
  BUDDY_HUB        hub URL (default http://127.0.0.1:8765)
  BUDDY_TOKEN      token (default: ~/.config/claude-buddy/token)
  BUDDY_PTT_KEY    pynput key name to hold (default f9)
  BUDDY_PTT_ENTER  1 = press Enter after typing (default 1)
  BUDDY_PTT_SILENCE  seconds of silence that end a hands-free take (default 1.5)
"""

import array
import io
import json
import logging
import math
import os
import re
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from pynput import keyboard

HUB = os.environ.get("BUDDY_HUB", "http://127.0.0.1:8765").rstrip("/")
KEY = getattr(keyboard.Key, os.environ.get("BUDDY_PTT_KEY", "f9").lower())
PRESS_ENTER = os.environ.get("BUDDY_PTT_ENTER", "1") == "1"
TAP_SECONDS = 0.4  # a shorter press is a tap: switch to hands-free mode
RATE = 16000
CHUNK = RATE // 10  # 100 ms of 16-bit mono samples
SILENCE_END = float(os.environ.get("BUDDY_PTT_SILENCE", "1.5"))
NO_SPEECH_GIVE_UP = 8.0  # hands-free take with no speech at all: cancel
MAX_SECONDS = 120.0
SPEECH_RMS_MIN = 400  # 16-bit RMS; the noise floor raises it in noisy rooms
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


class Recording:
    """pw-record streaming raw PCM to us, so we can watch the level live."""

    def __init__(self):
        self.proc = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.started = time.time()
        self.chunks: list[bytes] = []
        self.levels: list[float] = []
        self.done = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while not self.done.is_set():
            data = self.proc.stdout.read(CHUNK * 2)
            if not data:
                break
            self.chunks.append(data)
            samples = array.array("h", data[: len(data) // 2 * 2])
            self.levels.append(math.sqrt(sum(x * x for x in samples) / max(1, len(samples))))
        self.done.set()

    def speech_threshold(self) -> float:
        floor = statistics.median(self.levels[:3]) if len(self.levels) >= 3 else 0.0
        return max(SPEECH_RMS_MIN, floor * 3)

    def silence_state(self) -> tuple[bool, float]:
        """(any speech yet, seconds of silence since the last speech)."""
        threshold = self.speech_threshold()
        loud = [i for i, level in enumerate(self.levels) if level > threshold]
        if not loud:
            return False, len(self.levels) * 0.1
        return True, (len(self.levels) - 1 - loud[-1]) * 0.1

    def stop(self) -> bytes:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.done.wait(timeout=1)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(b"".join(self.chunks))
        return buf.getvalue()


class PushToTalk:
    def __init__(self):
        self.notify = Notifier()
        self.typer = keyboard.Controller()
        self.rec: Recording | None = None
        self.hands_free = False
        self.ignore_release = False
        self.pending_release: threading.Timer | None = None
        self.state_lock = threading.Lock()
        self.busy = threading.Lock()  # one transcription at a time

    # ---- key events -------------------------------------------------------

    def on_press(self, key):
        if key != KEY:
            return
        with self.state_lock:
            if self.pending_release:  # X11 auto-repeat: the key is still held
                self.pending_release.cancel()
                self.pending_release = None
                return
            if self.rec and self.hands_free:  # second tap ends a hands-free take
                self.ignore_release = True
                self._finish("tap")
                return
            if self.rec is None and not self.busy.locked():
                self._start()

    def on_release(self, key):
        if key != KEY:
            return
        with self.state_lock:
            if self.ignore_release:
                self.ignore_release = False
                return
            if self.rec is None or self.hands_free:
                return
            self.pending_release = threading.Timer(REPEAT_GRACE, self._released)
            self.pending_release.start()

    def _released(self):
        with self.state_lock:
            self.pending_release = None
            if self.rec is None:
                return
            if time.time() - self.rec.started < TAP_SECONDS:
                self.hands_free = True
                self.notify.show("🎤 Ouvindo…", "pode falar; envio quando você parar")
                log.info("hands-free")
                threading.Thread(target=self._watch_silence, args=(self.rec,), daemon=True).start()
            else:
                self._finish("release")

    # ---- recording ----------------------------------------------------------

    def _start(self):
        self.rec = Recording()
        self.hands_free = False
        self.notify.show("🎤 Ouvindo…", "solte para enviar, ou toque e só fale")
        log.info("recording")

    def _watch_silence(self, rec: Recording):
        while not rec.done.is_set() and self.rec is rec:
            time.sleep(0.1)
            spoke, silent_for = rec.silence_state()
            elapsed = time.time() - rec.started
            with self.state_lock:
                if self.rec is not rec:
                    return
                if spoke and silent_for >= SILENCE_END:
                    self._finish("silence")
                elif not spoke and elapsed >= NO_SPEECH_GIVE_UP:
                    self._finish("no speech", cancel=True)
                elif elapsed >= MAX_SECONDS:
                    self._finish("max length")

    def _finish(self, why: str, cancel: bool = False):
        """Caller holds state_lock."""
        rec, self.rec, self.hands_free = self.rec, None, False
        if rec is None:
            return
        audio = rec.stop()
        log.info("stopped (%s) after %.1fs", why, time.time() - rec.started)
        if cancel:
            self.notify.show("Claude Buddy", "não ouvi nada", 1500)
            return
        threading.Thread(target=self.deliver, args=(audio,), daemon=True).start()

    # ---- transcription + typing --------------------------------------------

    def deliver(self, audio: bytes):
        with self.busy:
            self.notify.show("⏳ Transcrevendo…")
            try:
                text = self.transcribe(audio)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("transcription failed: %s", exc)
                self.notify.show("✕ Não consegui transcrever", str(exc), 4000)
                return
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
    log.info("hold or tap %s to talk (hub %s, enter=%s)", KEY, HUB, PRESS_ENTER)
    with keyboard.Listener(on_press=ptt.on_press, on_release=ptt.on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
