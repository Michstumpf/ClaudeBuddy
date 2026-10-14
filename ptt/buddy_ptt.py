#!/usr/bin/env python3
"""Push-to-talk for the Ubuntu desktop (Wayland or X11) and Windows.

Two ways to use the key:
  - hold it while speaking, release to send;
  - tap it, just speak, and it sends by itself after a short silence
    (tap again to send right away).

The audio is recorded with pw-record, transcribed by the hub
(POST /api/transcribe, faster-whisper, never leaves home) and typed into the
focused window, so it works in any terminal or app, tmux or not.

Linux reads the key from /dev/input (evdev) and types through a virtual
keyboard (/dev/uinput), which works under Wayland, where no app may watch or
fake keys through the display server. Needs the user in the "input" group and
/dev/uinput writable by it (see the README). Windows uses pynput.

Config (env):
  BUDDY_HUB        hub URL (default http://127.0.0.1:8765; from Windows over
                   Tailscale: http://dell:8765)
  BUDDY_TOKEN      token (default: ~/.config/claude-buddy/token)
  BUDDY_PTT_KEY    key to hold (default f9)
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
import unicodedata
import urllib.error
import urllib.request
import wave
from pathlib import Path

HUB = os.environ.get("BUDDY_HUB", "http://127.0.0.1:8765").rstrip("/")
KEY_NAME = os.environ.get("BUDDY_PTT_KEY", "f9").lower()
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
KEY_DELAY = 0.004  # between virtual key events, so the compositor keeps them in order
DEAD_KEY_SETTLE = 0.04  # seconds for the input method to commit around an accent
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


# ---- typing on Linux: a virtual keyboard with the ABNT2 layout ----------------
#
# The compositor applies the desktop's layout (br/ABNT2) to the virtual keyboard
# too, so each character is sent as the physical key that makes it there.
# Accented letters are typed as a person does, with the dead keys
# (´ then a = á, ~ then a = ã).

def _abnt2_keys() -> dict[str, tuple[str, bool]]:
    """Character -> (evdev key name, Shift) on ABNT2."""
    keys = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        keys[c] = (f"KEY_{c.upper()}", False)
        keys[c.upper()] = (f"KEY_{c.upper()}", True)
    for digit, shifted in zip("1234567890", "!@#$%¨&*()"):
        keys[digit] = (f"KEY_{digit}", False)
        if shifted != "¨":  # a dead key on ABNT2, see DEAD_KEYS
            keys[shifted] = (f"KEY_{digit}", True)
    for key, plain, shifted in (
        ("KEY_MINUS", "-", "_"), ("KEY_EQUAL", "=", "+"), ("KEY_RIGHTBRACE", "[", "{"),
        ("KEY_BACKSLASH", "]", "}"), ("KEY_SEMICOLON", "ç", "Ç"), ("KEY_GRAVE", "'", '"'),
        ("KEY_COMMA", ",", "<"), ("KEY_DOT", ".", ">"), ("KEY_SLASH", ";", ":"),
        ("KEY_RO", "/", "?"), ("KEY_102ND", "\\", "|"),
    ):
        keys[plain], keys[shifted] = (key, False), (key, True)
    keys[" "], keys["\n"], keys["\t"] = ("KEY_SPACE", False), ("KEY_ENTER", False), ("KEY_TAB", False)
    return keys


ABNT2_KEYS = _abnt2_keys()
# Combining mark -> its dead key on ABNT2.
DEAD_KEYS = {"́": ("KEY_LEFTBRACE", False), "̀": ("KEY_LEFTBRACE", True),
             "̃": ("KEY_APOSTROPHE", False), "̂": ("KEY_APOSTROPHE", True),
             "̈": ("KEY_6", True)}
# The accents on their own: the dead key, then space.
SPACING_ACCENTS = {"´": "́", "`": "̀", "~": "̃", "^": "̂", "¨": "̈"}
# Typography Whisper sometimes writes, as what the keyboard has.
PLAIN = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...", " ": " "})


def abnt2_strokes(ch: str) -> list[tuple[str, bool]] | None:
    """The (key, Shift) presses that type ch on ABNT2; several for a dead-key
    combination. None if the layout cannot type it."""
    if ch in ABNT2_KEYS:
        return [ABNT2_KEYS[ch]]
    if ch in SPACING_ACCENTS:
        return [DEAD_KEYS[SPACING_ACCENTS[ch]], ABNT2_KEYS[" "]]
    decomposed = unicodedata.normalize("NFD", ch)
    if len(decomposed) == 2 and decomposed[1] in DEAD_KEYS and decomposed[0] in ABNT2_KEYS:
        return [DEAD_KEYS[decomposed[1]], ABNT2_KEYS[decomposed[0]]]
    return None


class UInputTyper:
    NAME = "Claude Buddy keyboard"  # also how the key listener skips it

    def __init__(self):
        from evdev import UInput, ecodes

        self.ecodes = ecodes
        names = {key for key, _ in ABNT2_KEYS.values()} | {key for key, _ in DEAD_KEYS.values()} | {"KEY_LEFTSHIFT"}
        self.ui = UInput({ecodes.EV_KEY: sorted(ecodes.ecodes[n] for n in names)}, name=self.NAME)
        time.sleep(0.5)  # the compositor picks up a new keyboard asynchronously

    def _press(self, key: str, shift: bool) -> None:
        ec = self.ecodes
        events = [(ec.KEY_LEFTSHIFT, 1)] if shift else []
        events += [(ec.ecodes[key], 1), (ec.ecodes[key], 0)]
        events += [(ec.KEY_LEFTSHIFT, 0)] if shift else []
        for code, value in events:
            self.ui.write(ec.EV_KEY, code, value)
            self.ui.syn()
            time.sleep(KEY_DELAY)

    def type(self, text: str) -> None:
        skipped = 0
        for ch in text.translate(PLAIN):
            strokes = abnt2_strokes(ch)
            if strokes is None:
                skipped += 1
                continue
            if len(strokes) > 1:
                # The input method (IBus) composes dead keys asynchronously: typed
                # back to back, the letter before the accent was committed after it
                # ("não" -> "ãno", "nós" -> "óns"). Let it settle around the combo.
                time.sleep(DEAD_KEY_SETTLE)
            for key, shift in strokes:
                self._press(key, shift)
            if len(strokes) > 1:
                time.sleep(DEAD_KEY_SETTLE)
        if skipped:
            log.info("skipped %d characters the layout cannot type", skipped)

    def enter(self) -> None:
        self._press("KEY_ENTER", False)


class PynputTyper:
    """Windows: pynput types any character itself."""

    def __init__(self):
        from pynput import keyboard

        self.keyboard = keyboard
        self.controller = keyboard.Controller()

    def type(self, text: str) -> None:
        self.controller.type(text.translate(PLAIN))

    def enter(self) -> None:
        self.controller.tap(self.keyboard.Key.enter)


# ---- the push-to-talk key ----------------------------------------------------

def listen_evdev(key_name: str, on_down, on_up) -> None:
    """Watch every keyboard that has the key (plugged in later too), forever.

    Auto-repeat arrives as value 2 and is ignored, so holding the key is one
    down and one up. The key still reaches the focused app, as before.
    """
    import glob
    import selectors

    from evdev import InputDevice, ecodes

    code = ecodes.ecodes[f"KEY_{key_name.upper()}"]
    sel = selectors.DefaultSelector()
    watched: dict[str, InputDevice] = {}
    warned = False
    while True:
        # Not evdev.list_devices(): newer versions skip devices we can only read.
        for path in set(glob.glob("/dev/input/event*")) - watched.keys():
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            if dev.name == UInputTyper.NAME or code not in dev.capabilities().get(ecodes.EV_KEY, []):
                dev.close()
                continue
            watched[path] = dev
            sel.register(dev, selectors.EVENT_READ)
            log.info("listening on %s (%s)", dev.name, path)
        if not watched and not warned:
            log.warning("no readable keyboard: is this user in the 'input' group? (see the README)")
            warned = True
        for selkey, _ in sel.select(timeout=5):  # rescan for new keyboards every 5 s
            dev = selkey.fileobj
            try:
                for event in dev.read():
                    if event.type == ecodes.EV_KEY and event.code == code:
                        if event.value == 1:
                            on_down()
                        elif event.value == 0:
                            on_up()
            except OSError:  # unplugged
                sel.unregister(dev)
                watched.pop(dev.path, None)
                dev.close()


def listen_pynput(key_name: str, on_down, on_up) -> None:
    from pynput import keyboard

    key = getattr(keyboard.Key, key_name)
    with keyboard.Listener(on_press=lambda k: k == key and on_down(),
                           on_release=lambda k: k == key and on_up()) as listener:
        listener.join()


class PushToTalk:
    def __init__(self):
        self.notify = Notifier()
        self.typer = UInputTyper() if sys.platform == "linux" else PynputTyper()
        self.rec: Recording | None = None
        self.hands_free = False
        self.tailing = False  # released, still recording RELEASE_TAIL
        self.ignore_release = False
        self.state_lock = threading.Lock()
        self.busy = threading.Lock()  # one transcription at a time

    # ---- key events -------------------------------------------------------

    def key_down(self):
        with self.state_lock:
            if self.tailing:
                return
            if self.rec and self.hands_free:  # second tap ends a hands-free take
                self.ignore_release = True
                self._finish("tap")
                return
            if self.rec is None and not self.busy.locked():
                self._start()

    def key_up(self):
        with self.state_lock:
            if self.ignore_release:
                self.ignore_release = False
                return
            if self.rec is None or self.hands_free or self.tailing:
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
            self.typer.type(text)
            if PRESS_ENTER:
                time.sleep(0.05)
                self.typer.enter()

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
    log.info("hold or tap %s to talk (hub %s, enter=%s)", KEY_NAME, HUB, PRESS_ENTER)
    listen = listen_evdev if sys.platform == "linux" else listen_pynput
    listen(KEY_NAME, ptt.key_down, ptt.key_up)


if __name__ == "__main__":
    main()
