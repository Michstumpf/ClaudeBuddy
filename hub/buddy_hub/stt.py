"""Speech-to-text with faster-whisper (audio never leaves home).

Runs on the hub's CPU, or on the desktop's GPU through buddy_hub.worker with
the hub falling back to its CPU (RemoteFirst).

Optional: if faster-whisper is not installed and no worker is configured,
the hub still runs and /api/transcribe answers 503. The model is loaded on the first request and kept
in memory; requests are serialized because one CPU model can't run in parallel.
"""

import io
import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("buddy.stt")

# Biases Whisper toward the words actually spoken at this desk: Portuguese
# mixed with English dev jargon.
INITIAL_PROMPT = (
    "Conversa com o Claude Code sobre programação. Termos comuns: commit, push, pull request, "
    "branch, merge, deploy, hook, endpoint, backend, frontend, Python, TypeScript, React, "
    "FastAPI, pytest, Docker, tmux, Jira, Slack, Portrait, EHR, HIPAA, DataHub."
)


class Transcriber:
    def __init__(self, model: str = "small", language: str | None = "pt", compute_type: str = "int8",
                 beam_size: int = 1, device: str = "cpu"):
        self.model_name = model
        self.device = device
        # 1 = greedy decoding: 2-3x faster than 5 on a busy CPU, slightly less accurate.
        self.beam_size = beam_size
        self.language = language or None
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            started = time.time()
            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
            log.info("loaded whisper %s on %s in %.1fs", self.model_name, self.device, time.time() - started)
        return self._model

    def transcribe(self, audio: bytes) -> dict:
        """audio: any format ffmpeg/PyAV can decode (the PTT client sends 16 kHz mono WAV)."""
        with self._lock:
            model = self._load()
            started = time.time()
            segments, info = model.transcribe(
                io.BytesIO(audio),
                language=self.language,
                initial_prompt=INITIAL_PROMPT,
                vad_filter=True,
                beam_size=self.beam_size,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "audio_seconds": round(info.duration, 2),
            "took_seconds": round(time.time() - started, 2),
            "model": self.model_name,
            "backend": self.device,
        }


class RemoteFirst:
    """Try the GPU worker (buddy_hub.worker on the desktop), fall back to the local CPU.

    The worker is skipped when it is down, slow to answer /health, or reports
    its GPU busy (e.g. a game). The local model is only loaded when a fallback
    actually happens, so with the desktop on the hub saves its RAM.
    """

    def __init__(self, url: str, token: str, local: Transcriber, health_timeout: float = 1.0,
                 transcribe_timeout: float = 60.0):
        self.url = url.rstrip("/")
        self.token = token
        self.local = local
        self.health_timeout = health_timeout
        self.transcribe_timeout = transcribe_timeout

    def _request(self, path: str, data: bytes | None, timeout: float) -> dict:
        req = urllib.request.Request(
            f"{self.url}{path}", data=data, method="POST" if data is not None else "GET",
            headers={"X-Buddy-Token": self.token, "Content-Type": "audio/wav"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def remote_ready(self) -> tuple[bool, str]:
        try:
            health = self._request("/health", None, self.health_timeout)
        except Exception as exc:  # down, refused, timeout, bad JSON
            return False, f"unreachable ({exc.__class__.__name__})"
        if health.get("busy"):
            return False, f"busy ({health.get('reason', 'gpu in use')})"
        return True, "ok"

    def transcribe(self, audio: bytes) -> dict:
        ready, why = self.remote_ready()
        if ready:
            try:
                result = self._request("/transcribe", audio, self.transcribe_timeout)
                result["backend"] = f"remote:{result.get('backend', '?')}"
                return result
            except Exception as exc:
                why = f"failed ({exc.__class__.__name__})"
        log.info("gpu worker %s: transcribing on the hub", why)
        if not Transcriber.available():
            raise RuntimeError(f"gpu worker {why} and faster-whisper is not installed on the hub")
        result = self.local.transcribe(audio)
        result["fallback_reason"] = why
        return result
