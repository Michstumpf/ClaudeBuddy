"""GPU speech-to-text worker for the desktop (Windows + NVIDIA).

The hub on Ubuntu sends it audio over Tailscale and falls back to its own CPU
when this worker is off, slow or busy. Stateless: nothing is stored.

    cd hub
    ..\\.venv\\Scripts\\python -m buddy_hub.worker --host 0.0.0.0 --port 8766

- POST /transcribe  audio in (body) -> text out
- POST /speak       {"text"} -> WAV, with XTTS-v2 (optional: needs coqui-tts + torch)
- GET  /health      {"ok", "busy", "reason", "gpu", "tts"}; busy means "hub, use your CPU"

Config (env): BUDDY_TOKEN or ~/.config/claude-buddy/token (same token as the
hub), BUDDY_STT_MODEL (default large-v3-turbo), BUDDY_STT_DEVICE (default
cuda), BUDDY_STT_COMPUTE (default float16), BUDDY_STT_BEAM_SIZE (default 5),
BUDDY_STT_LANGUAGE (default pt), BUDDY_WORKER_BUSY_UTIL (GPU % that counts
as busy, default 60), BUDDY_WORKER_MIN_FREE_MB (default 1000; with Whisper and
XTTS loaded an 8 GB card has ~2.9 GB left, a game takes nearly all), BUDDY_WORKER_TTS
(0 disables the voice), BUDDY_XTTS_SPEAKER (built-in voice, default "Gilberto
Mathias"), BUDDY_XTTS_SPEAKER_WAV (a 6-30 s WAV to clone a voice from instead).
"""

import argparse
import asyncio
import hmac
import io
import logging
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

log = logging.getLogger("buddy.worker")


def add_cuda_dll_dirs() -> None:
    """Let ctranslate2 (Whisper) find the CUDA/cuDNN DLLs on Windows.

    With the GPU voice installed, PyTorch ships its own CUDA and cuDNN in
    torch/lib, and both libraries must use that single copy: loading pip's
    nvidia-cudnn-cu12 for Whisper and torch's cuDNN for XTTS in one process
    crashes ("Could not load symbol cudnnGetLibConfig"). Without PyTorch, use
    pip's nvidia-cublas-cu12 / nvidia-cudnn-cu12 (site-packages/nvidia/*/bin).
    """
    if os.name != "nt":
        return
    torch_libs = [base / "torch" / "lib" for base in map(Path, sys.path) if (base / "torch" / "lib" / "cudnn64_9.dll").exists()]
    dirs = torch_libs[:1] or [d for base in map(Path, sys.path) for d in (base / "nvidia").glob("*/bin")]
    for bin_dir in dirs:
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    log.info("CUDA DLLs from %s", ", ".join(map(str, dirs)) or "the system PATH")


def gpu_status() -> dict | None:
    """Utilization and free memory of GPU 0, or None without nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        ).stdout.splitlines()[0]
        util, used, total, name = [x.strip() for x in out.split(",", 3)]
        return {"util": int(util), "free_mb": int(total) - int(used), "name": name}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class XttsSpeaker:
    """XTTS-v2 (coqui-tts) on the GPU: natural Portuguese, ~1-2 s per sentence.

    The model license (Coqui Public Model License) is non-commercial, which
    fits this personal project; loading it implies accepting that license.
    """

    MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"

    def __init__(self, speaker: str = "Gilberto Mathias", speaker_wav: str | None = None, language: str = "pt"):
        self.speaker, self.speaker_wav, self.language = speaker, speaker_wav, language
        self._tts = None

    @staticmethod
    def installed() -> bool:
        try:
            import TTS  # noqa: F401
        except ImportError:
            return False
        return True

    def load(self) -> None:
        os.environ.setdefault("COQUI_TOS_AGREED", "1")  # non-interactive license acceptance
        from TTS.api import TTS

        started = time.time()
        self._tts = TTS(self.MODEL).to("cuda")
        log.info("loaded XTTS-v2 in %.1fs", time.time() - started)

    def synthesize(self, text: str) -> bytes:
        import numpy as np

        voice = {"speaker_wav": self.speaker_wav} if self.speaker_wav else {"speaker": self.speaker}
        samples = np.asarray(self._tts.tts(text=text, language=self.language, **voice), dtype=np.float32)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self._tts.synthesizer.output_sample_rate)
            wav.writeframes(pcm)
        return buf.getvalue()


def create_app(token: str, transcriber, busy_util: int = 60, min_free_mb: int = 1000, gpu_probe=gpu_status,
               speaker=None):
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import Response

    app = FastAPI(title="Claude Buddy STT worker")
    working = asyncio.Lock()

    def check(value: str | None) -> None:
        if not value or not hmac.compare_digest(value, token):
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/health")
    async def health(x_buddy_token: str | None = Header(None)):
        check(x_buddy_token)
        gpu = await asyncio.to_thread(gpu_probe)
        # Our own transcription also loads the GPU, so only judge it when idle.
        reason = None
        if gpu and not working.locked():
            if gpu["util"] >= busy_util:
                reason = f"gpu {gpu['util']}% busy"
            elif gpu["free_mb"] < min_free_mb:
                reason = f"only {gpu['free_mb']} MB of VRAM free"
        return {"ok": True, "busy": reason is not None, "reason": reason, "gpu": gpu,
                "model": transcriber.model_name, "tts": speaker is not None}

    @app.post("/transcribe")
    async def transcribe(request: Request, x_buddy_token: str | None = Header(None)):
        check(x_buddy_token)
        audio = await request.body()
        if not audio:
            raise HTTPException(status_code=400, detail="empty audio")
        async with working:
            result = await asyncio.to_thread(transcriber.transcribe, audio)
        log.info("transcribed %.1fs of audio in %.2fs", result["audio_seconds"], result["took_seconds"])
        return result

    @app.post("/speak")
    async def speak(request: Request, x_buddy_token: str | None = Header(None)):
        check(x_buddy_token)
        if speaker is None:
            raise HTTPException(status_code=503, detail="no voice on this worker")
        text = ((await request.json()).get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="empty text")
        started = time.time()
        async with working:
            wav = await asyncio.to_thread(speaker.synthesize, text)
        log.info("spoke %d chars in %.2fs", len(text), time.time() - started)
        return Response(wav, media_type="audio/wav")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Buddy GPU STT worker")
    parser.add_argument("--host", default="0.0.0.0", help="restrict with the firewall to the Tailscale range")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--log-file", help="also log here (useful under pythonw, which has no console)")
    args = parser.parse_args()

    handlers = [logging.FileHandler(args.log_file, encoding="utf-8")] if args.log_file else []
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", handlers=handlers)
    for noisy in ("httpx", "huggingface_hub"):  # model download chatter
        logging.getLogger(noisy).setLevel(logging.WARNING)

    add_cuda_dll_dirs()
    import uvicorn

    from .config import TOKEN_FILE
    from .stt import Transcriber

    # Must be the hub's token, so never generate one here.
    token = os.environ.get("BUDDY_TOKEN") or (TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else "")
    if not token:
        sys.exit(f"No token: copy the hub's ~/.config/claude-buddy/token to {TOKEN_FILE} (or set BUDDY_TOKEN).")

    transcriber = Transcriber(
        model=os.environ.get("BUDDY_STT_MODEL", "large-v3-turbo"),
        language=os.environ.get("BUDDY_STT_LANGUAGE", "pt") or None,
        compute_type=os.environ.get("BUDDY_STT_COMPUTE", "float16"),
        beam_size=int(os.environ.get("BUDDY_STT_BEAM_SIZE", "5")),
        device=os.environ.get("BUDDY_STT_DEVICE", "cuda"),
    )
    transcriber._load()  # load at startup so the first dictation is fast
    speaker = None
    if os.environ.get("BUDDY_WORKER_TTS", "1") != "0" and XttsSpeaker.installed():
        speaker = XttsSpeaker(
            speaker=os.environ.get("BUDDY_XTTS_SPEAKER", "Gilberto Mathias"),
            speaker_wav=os.environ.get("BUDDY_XTTS_SPEAKER_WAV") or None,
            language=os.environ.get("BUDDY_STT_LANGUAGE", "pt") or "pt",
        )
        try:
            speaker.load()
            # The first synthesis warms up CUDA and takes ~40 s on an RTX 3070 Ti,
            # longer than the hub waits; pay it now instead of on the first reply.
            started = time.time()
            speaker.synthesize("Olá.")
            log.info("XTTS-v2 warmed up in %.1fs", time.time() - started)
        except Exception:
            log.exception("XTTS-v2 failed to load; serving speech-to-text only")
            speaker = None
    app = create_app(
        token, transcriber,
        busy_util=int(os.environ.get("BUDDY_WORKER_BUSY_UTIL", "60")),
        min_free_mb=int(os.environ.get("BUDDY_WORKER_MIN_FREE_MB", "1000")),
        speaker=speaker,
    )
    log.info("worker on http://%s:%s (model %s on %s)", args.host, args.port, transcriber.model_name, transcriber.device)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
