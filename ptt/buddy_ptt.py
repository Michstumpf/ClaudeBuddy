#!/usr/bin/env python3
"""Push-to-talk for the Ubuntu desktop (X11) and Windows.

Two ways to use the key:
  - hold it while speaking, release to send;
  - tap it, just speak, and it sends by itself after a short silence
    (tap again to send right away).

The audio is recorded with pw-record, transcribed by the hub
(POST /api/transcribe, faster-whisper, never leaves home) and typed into the
focused window, so it works in any terminal or app, tmux or not.

Config (env):
  BUDDY_HUB        hub URL (default http://127.0.0.1:8765; from Windows over
                   Tailscale: http://dell:8765)
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
import shutil
import signal
import statistics
import subprocess
import sys
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
# Keep recording a little after the key is released: people let go while
# still saying the last word, and the mic pipeline lags a bit.
RELEASE_TAIL = 0.5
RATE = 16000
CHUNK = RATE // 10  # 100 ms of 16-bit mono samples
SILENCE_END = float(os.environ.get("BUDDY_PTT_SILENCE", "1.5"))
NO_SPEECH_GIVE_UP = 8.0  # hands-free take with no speech at all: cancel
MAX_SECONDS = 120.0
# 16-bit RMS. Measured on a HyperX headset: silence ~5, normal speech peaks
# ~250. The noise floor raises the threshold in noisy rooms.
SPEECH_RMS_MIN = 60
NOISE_FACTOR = 4
# X11 auto-repeat turns a held key into release+press pairs; a release only
# counts if no press follows within this window.
REPEAT_GRACE = 0.08
TYPE_SETTLE = 0.03  # seconds to let a keymap change land (see type_text)
# Phrases Whisper invents on silence or noise (YouTube subtitle credits).
HALLUCINATIONS = re.compile(r"legendas? pela comunidade|amara\.org|obrigad[oa] por assistir|inscreva-se", re.I)

log = logging.getLogger("buddy.ptt")


def token() -> str:
    if os.environ.get("BUDDY_TOKEN"):
        return os.environ["BUDDY_TOKEN"]
    return (Path.home() / ".config" / "claude-buddy" / "token").read_text(encoding="utf-8").strip()


def _beep(title: str) -> None:
    """No notifications (Windows): short beeps instead. One = listening, two = sent."""
    try:
        import winsound
    except ImportError:
        return
    if title.startswith("🎤"):
        winsound.Beep(880, 90)
    elif title.startswith("✓"):
        winsound.Beep(988, 70)
        winsound.Beep(1319, 90)
    elif title.startswith("✕"):
        winsound.Beep(330, 250)


class Notifier:
    """One desktop notification that is updated in place (notify-send -r).

    notify-send can take seconds to return, so it runs on its own thread and
    never delays key handling; only the latest message is shown.
    """

    def __init__(self):
        self.id = "0"
        self._latest: tuple | None = None
        self._wake = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def show(self, title: str, body: str = "", timeout_ms: int = 0) -> None:
        with self._wake:
            self._latest = (title, body, timeout_ms)
            self._wake.notify()

    def _loop(self):
        while True:
            with self._wake:
                while self._latest is None:
                    self._wake.wait()
                title, body, timeout_ms = self._latest
                self._latest = None
            if not shutil.which("notify-send"):
                _beep(title)
                continue
            cmd = ["notify-send", "-a", "Claude Buddy", "-p", "-r", self.id, "-t", str(timeout_ms), title, body]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                self.id = out.stdout.strip() or self.id
            except (OSError, subprocess.SubprocessError):
                pass


class Recording:
    """Raw 16 kHz mono PCM, with its level watched live (for hands-free mode).

    Linux: pw-record streaming to us. Elsewhere (Windows): sounddevice.
    """

    def __new__(cls):
        if cls is _RecordingBase:  # Recording() picks the backend; subclasses are used as they are
            cls = PipeWireRecording if shutil.which("pw-record") else SoundDeviceRecording
        return super().__new__(cls)

    def __init__(self):
        self.started = time.time()
        self.chunks: list[bytes] = []
        self.levels: list[float] = []
        self.done = threading.Event()
        self._start()

    def _add(self, data: bytes) -> None:
        self.chunks.append(data)
        samples = array.array("h", data[: len(data) // 2 * 2])
        self.levels.append(math.sqrt(sum(x * x for x in samples) / max(1, len(samples))))

    def speech_threshold(self) -> float:
        floor = statistics.median(self.levels[:3]) if len(self.levels) >= 3 else 0.0
        return max(SPEECH_RMS_MIN, floor * NOISE_FACTOR)

    def silence_state(self) -> tuple[bool, float]:
        """(any speech yet, seconds of silence since the last speech)."""
        threshold = self.speech_threshold()
        loud = [i for i, level in enumerate(self.levels) if level > threshold]
        if not loud:
            return False, len(self.levels) * 0.1
        return True, (len(self.levels) - 1 - loud[-1]) * 0.1

    def stop(self) -> bytes:
        self._stop()
        self.done.wait(timeout=1)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(b"".join(self.chunks))
        return buf.getvalue()


_RecordingBase = Recording


class PipeWireRecording(Recording):
    def _start(self):
        self.proc = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while not self.done.is_set():
            data = self.proc.stdout.read(CHUNK * 2)
            if not data:
                break
            self._add(data)
        self.done.set()

    def _stop(self):
        self.proc.send_signal(signal.SIGINT)  # pw-record flushes its buffer on SIGINT
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class SoundDeviceRecording(Recording):
    def _start(self):
        import sounddevice

        self.stream = sounddevice.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK,
                                                 callback=lambda data, frames, t, status: self._add(bytes(data)))
        self.stream.start()

    def _stop(self):
        self.stream.stop()
        self.stream.close()
        self.done.set()


class PushToTalk:
    def __init__(self):
        self.notify = Notifier()
        self.typer = keyboard.Controller()
        self.rec: Recording | None = None
        self.hands_free = False
        self.tailing = False  # released, still recording RELEASE_TAIL
        self.ignore_release = False
        self.pending_release: threading.Timer | None = None
        self.state_lock = threading.Lock()
        self.busy = threading.Lock()  # one transcription at a time

    # ---- key events -------------------------------------------------------

    def on_press(self, key):
        if key != KEY:
            return
        with self.state_lock:
            if self.tailing:
                return
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
            if self.rec is None or self.hands_free or self.tailing:
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
                self.tailing = True
                threading.Timer(RELEASE_TAIL, self._tail_done, args=(self.rec,)).start()

    def _tail_done(self, rec: "Recording"):
        with self.state_lock:
            self.tailing = False
            if self.rec is rec:
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
        levels = sorted(rec.levels) or [0.0]
        log.info("stopped (%s) after %.1fs; level floor=%.0f p90=%.0f peak=%.0f threshold=%.0f",
                 why, time.time() - rec.started, levels[len(levels) // 10], levels[len(levels) * 9 // 10],
                 levels[-1], rec.speech_threshold())
        if cancel:
            self.notify.show("Claude Buddy", "não ouvi nada", 1500)
            return
        threading.Thread(target=self.deliver, args=(audio,), daemon=True).start()

    # ---- transcription + typing --------------------------------------------

    def deliver(self, audio: bytes):
        with self.busy:
            self.notify.show("⏳ Transcrevendo…")
            try:
                result = self.transcribe(audio)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("transcription failed: %s", exc)
                self.notify.show("✕ Não consegui transcrever", str(exc), 4000)
                return
            if result.get("handled"):  # a voice intent ("manda para a HIPAA: …") the hub took care of
                log.info("handled by the hub")
                self.notify.show("✓ Claude Buddy", result.get("detail", ""), 3000)
                return
            text = (result.get("text") or "").strip()
            if not text or HALLUCINATIONS.search(text):
                self.notify.show("Claude Buddy", "não ouvi nada", 1500)
                return
            log.info("typing %d chars", len(text))
            self.notify.show("✓ Enviado" if PRESS_ENTER else "✓ Digitado", text, 3000)
            time.sleep(0.05)
            self.type_text(text)
            if PRESS_ENTER:
                time.sleep(0.05)
                self.typer.tap(keyboard.Key.enter)

    def type_text(self, text: str) -> None:
        """Type like pynput's type(), but in order.

        Characters with no key on the layout (ç, ã, é with a US keymap...) are
        typed by remapping a spare keycode, and the X server applies that
        remap a moment later; typed back to back, "relação" came out as
        "relçãao". Pausing around those characters keeps the order.
        """
        for ch in text:
            special = not ch.isascii()
            if special:
                time.sleep(TYPE_SETTLE)
            self.typer.type(ch)
            if special:
                time.sleep(TYPE_SETTLE)

    def transcribe(self, audio: bytes) -> dict:
        req = urllib.request.Request(
            f"{HUB}/api/transcribe", data=audio, method="POST",
            headers={"Content-Type": "audio/wav", "X-Buddy-Token": token()},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        log.info("transcribed %.1fs in %.1fs", result.get("audio_seconds", 0), result.get("took_seconds", 0))
        return result


def main() -> None:
    handlers = [logging.StreamHandler()] if sys.stderr else []
    if os.environ.get("BUDDY_PTT_LOG"):  # pythonw (Windows logon task) has no console
        handlers.append(logging.FileHandler(os.environ["BUDDY_PTT_LOG"], encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", handlers=handlers)
    ptt = PushToTalk()
    log.info("hold or tap %s to talk (hub %s, enter=%s)", KEY, HUB, PRESS_ENTER)
    with keyboard.Listener(on_press=ptt.on_press, on_release=ptt.on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
