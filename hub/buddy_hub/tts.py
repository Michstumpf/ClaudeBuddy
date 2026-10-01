"""Spoken replies: turn a Claude Code answer into a short sentence and a WAV.

Text-to-speech runs on the desktop's GPU worker (XTTS-v2) when available, else
locally with Piper (pt-BR voice); either way audio never leaves home.
Optional: without a worker voice, piper-tts or the voice file, the hub runs
without speech.
"""

import io
import json
import logging
import re
import threading
import time
import urllib.request
import wave
from pathlib import Path

log = logging.getLogger("buddy.tts")

VOICES_DIR = Path.home() / ".local" / "share" / "claude-buddy" / "voices"
SPOKEN_MAX_CHARS = 320
SPOKEN_MAX_SENTENCES = 3

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def speakable(text: str) -> str:
    """The first few sentences of a markdown answer, cleaned up for reading aloud.

    Answers lead with the conclusion, so the opening sentences are what to say;
    code, tables and links read terribly and are dropped.
    """
    text = _FENCE.sub(" ", text or "")
    text = _TABLE_ROW.sub(" ", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _EMOJI.sub("", text)
    # Lines without final punctuation (list items, headings) become sentences.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = " ".join(line if line[-1] in ".!?:…" else line + "." for line in lines)
    text = re.sub(r":\s*$", ".", text)
    text = re.sub(r"\s+", " ", text).strip()

    spoken = []
    for sentence in _SENTENCE_END.split(text):
        if spoken and len(" ".join(spoken + [sentence])) > SPOKEN_MAX_CHARS:
            break
        spoken.append(sentence)
        if len(spoken) >= SPOKEN_MAX_SENTENCES:
            break
    result = " ".join(spoken)
    if len(result) > SPOKEN_MAX_CHARS:  # one huge sentence: cut at a word
        result = result[:SPOKEN_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return result


class Speaker:
    """Piper voice, loaded on first use; one synthesis at a time."""

    def __init__(self, voice: str = "pt_BR-faber-medium", voices_dir: Path = VOICES_DIR):
        self.voice_name = voice
        self.model_path = voices_dir / f"{voice}.onnx"
        self._voice = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return self.model_path.exists()

    def synthesize(self, text: str) -> bytes:
        with self._lock:
            if self._voice is None:
                from piper import PiperVoice

                started = time.time()
                self._voice = PiperVoice.load(self.model_path)
                log.info("loaded voice %s in %.1fs", self.voice_name, time.time() - started)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                self._voice.synthesize_wav(text, wav)
            return buf.getvalue()


class RemoteFirstSpeaker:
    """XTTS-v2 on the desktop's GPU worker first, Piper on the hub as fallback.

    Same rule as speech-to-text: skip the worker when it is down, slow to
    answer /health, busy (e.g. a game) or has no voice installed.
    """

    def __init__(self, url: str, token: str, local: Speaker, health_timeout: float = 1.0,
                 speak_timeout: float = 15.0):
        self.url = url.rstrip("/")
        self.token = token
        self.local = local
        self.health_timeout = health_timeout
        self.speak_timeout = speak_timeout

    def available(self) -> bool:
        return True  # the worker may have a voice even when Piper is not installed

    def _get(self, path: str, timeout: float) -> dict:
        req = urllib.request.Request(f"{self.url}{path}", headers={"X-Buddy-Token": self.token})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def _speak_remote(self, text: str) -> bytes:
        req = urllib.request.Request(
            f"{self.url}/speak", data=json.dumps({"text": text}).encode("utf-8"), method="POST",
            headers={"X-Buddy-Token": self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.speak_timeout) as resp:
            return resp.read()

    def synthesize(self, text: str) -> bytes:
        try:
            health = self._get("/health", self.health_timeout)
            if not health.get("tts"):
                why = "has no voice"
            elif health.get("busy"):
                why = f"busy ({health.get('reason')})"
            else:
                return self._speak_remote(text)
        except Exception as exc:
            why = f"unreachable ({exc.__class__.__name__})"
        log.info("gpu worker %s: speaking with Piper on the hub", why)
        if not self.local.available():
            raise RuntimeError(f"gpu worker {why} and Piper is not installed on the hub")
        return self.local.synthesize(text)


LOUDNESS_TARGET_DBFS = -14.0  # louder than raw TTS (~-19 dBFS); laptop speakers lose the lows
_KNEE = 0.89  # above this the limiter bends the waveform instead of clipping


def louder(wav: bytes, target_dbfs: float = LOUDNESS_TARGET_DBFS) -> bytes:
    """Bring a 16-bit mono/stereo WAV to target_dbfs RMS with a soft limiter.

    XTTS and Piper come out around -19 dBFS; a deep voice at that level was
    barely audible on the Ubuntu speakers.
    """
    import numpy as np

    try:
        with wave.open(io.BytesIO(wav), "rb") as src:
            params = src.getparams()
            frames = src.readframes(src.getnframes())
    except (wave.Error, EOFError):  # not a plain PCM WAV: play it as it is
        return wav
    if params.sampwidth != 2 or not frames:
        return wav
    x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x)))
    if rms < 1e-5:  # silence
        return wav
    y = x * (10 ** (target_dbfs / 20) / rms)
    over = np.abs(y) > _KNEE
    y[over] = np.sign(y[over]) * (_KNEE + (1 - _KNEE) * np.tanh((np.abs(y[over]) - _KNEE) / (1 - _KNEE)))
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setparams(params)
        dst.writeframes((np.clip(y, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def to_stereo(wav: bytes) -> bytes:
    """Duplicate a mono 16-bit WAV into two channels.

    On the Ubuntu desktop (HyperX headset via PipeWire) a mono stream played
    noticeably quieter than the same audio in stereo; local playback uses this.
    """
    import numpy as np

    try:
        with wave.open(io.BytesIO(wav), "rb") as src:
            params = src.getparams()
            frames = src.readframes(src.getnframes())
    except (wave.Error, EOFError):
        return wav
    if params.nchannels != 1 or params.sampwidth != 2:
        return wav
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(2)
        dst.setsampwidth(2)
        dst.setframerate(params.framerate)
        dst.writeframes(np.repeat(np.frombuffer(frames, dtype="<i2"), 2).tobytes())
    return out.getvalue()
