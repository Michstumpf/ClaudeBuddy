"""Speech-to-text with faster-whisper, running on the hub (audio never leaves home).

Optional: if faster-whisper is not installed, the hub still runs and
/api/transcribe answers 503. The model is loaded on the first request and kept
in memory; requests are serialized because one CPU model can't run in parallel.
"""

import io
import logging
import threading
import time

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
                 beam_size: int = 1):
        self.model_name = model
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
            self._model = WhisperModel(self.model_name, device="cpu", compute_type=self.compute_type)
            log.info("loaded whisper %s in %.1fs", self.model_name, time.time() - started)
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
        }
