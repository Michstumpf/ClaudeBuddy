"""GPU speech-to-text worker for the desktop (Windows + NVIDIA).

The hub on Ubuntu sends it audio over Tailscale and falls back to its own CPU
when this worker is off, slow or busy. Stateless: nothing is stored.

    cd hub
    ..\\.venv\\Scripts\\python -m buddy_hub.worker --host 0.0.0.0 --port 8766

- POST /transcribe  audio in (body) -> text out
- GET  /health      {"ok", "busy", "reason", "gpu"}; busy means "hub, use your CPU"

Config (env): BUDDY_TOKEN or ~/.config/claude-buddy/token (same token as the
hub), BUDDY_STT_MODEL (default large-v3-turbo), BUDDY_STT_DEVICE (default
cuda), BUDDY_STT_COMPUTE (default float16), BUDDY_STT_BEAM_SIZE (default 5),
BUDDY_STT_LANGUAGE (default pt), BUDDY_WORKER_BUSY_UTIL (GPU % that counts
as busy, default 60), BUDDY_WORKER_MIN_FREE_MB (default 2500).
"""

import argparse
import asyncio
import hmac
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("buddy.worker")


def add_cuda_dll_dirs() -> None:
    """On Windows, pip's nvidia-cublas-cu12 / nvidia-cudnn-cu12 put their DLLs in
    site-packages/nvidia/*/bin, which ctranslate2 does not search by itself."""
    if os.name != "nt":
        return
    for base in map(Path, sys.path):
        for bin_dir in (base / "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


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


def create_app(token: str, transcriber, busy_util: int = 60, min_free_mb: int = 2500, gpu_probe=gpu_status):
    from fastapi import FastAPI, Header, HTTPException, Request

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
                "model": transcriber.model_name}

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
    app = create_app(
        token, transcriber,
        busy_util=int(os.environ.get("BUDDY_WORKER_BUSY_UTIL", "60")),
        min_free_mb=int(os.environ.get("BUDDY_WORKER_MIN_FREE_MB", "2500")),
    )
    log.info("worker on http://%s:%s (model %s on %s)", args.host, args.port, transcriber.model_name, transcriber.device)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
