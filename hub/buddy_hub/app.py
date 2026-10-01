"""HTTP + WebSocket server.

- POST /hook        Claude Code hook events (from hooks/buddy_hook.py)
- WS   /ws          the Buddy device (or the browser simulator)
- POST /api/dictate send dictated text to a session (e.g. from a hotkey client)
- POST /api/transcribe  audio in (body) -> text out, with faster-whisper
- GET  /api/state   current snapshot
- GET  /            the Buddy simulator
"""

import asyncio
import hmac
import json
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .config import Settings
from .dictation import make_sender
from .local_sessions import read_local_sessions
from .state import Hub
from .stt import Transcriber

log = logging.getLogger("buddy.hub")
SIMULATOR = Path(__file__).resolve().parents[2] / "simulator" / "index.html"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    hub = Hub(approval_timeout=settings.approval_timeout)
    sender = make_sender(settings.dictation_backend)
    transcriber = Transcriber(settings.stt_model, settings.stt_language)

    async def housekeeping() -> None:
        host = socket.gethostname()
        while True:
            try:
                changed = False
                if settings.sessions_dir:
                    entries = await asyncio.to_thread(read_local_sessions, settings.sessions_dir)
                    changed = hub.reconcile(entries, host)
                changed = hub.expire_stale(settings.stale_working) or changed
                if changed:
                    await hub.notify(None)
            except Exception:
                log.exception("housekeeping failed")
            await asyncio.sleep(settings.sessions_poll)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(housekeeping())
        yield
        task.cancel()

    app = FastAPI(title="Claude Buddy hub", lifespan=lifespan)
    app.state.hub = hub
    app.state.sender = sender

    def check(token: str | None) -> None:
        if not token or not hmac.compare_digest(token, settings.token):
            raise HTTPException(status_code=401, detail="bad token")

    async def broadcast(snapshot: dict, event: dict | None) -> None:
        message = json.dumps({**snapshot, "event": event})
        for ws in list(hub.devices):
            try:
                await ws.send_text(message)
            except Exception:
                hub.devices.discard(ws)

    hub.add_listener(broadcast)

    def dictate(session_id: str | None, text: str) -> dict:
        session = hub.sessions.get(session_id or "")
        if session is None:
            return {"ok": False, "detail": "unknown session"}
        ok, detail = sender.send(session.tmux_pane, text)
        return {"ok": ok, "detail": detail, "backend": sender.name, "session": session.name}

    @app.get("/")
    async def simulator():
        # Always revalidate: the page reconnects on its own, so a cached copy
        # would silently keep running an old simulator.
        return FileResponse(SIMULATOR, headers={"Cache-Control": "no-cache"})

    @app.get("/api/state")
    async def state(x_buddy_token: str | None = Header(None)):
        check(x_buddy_token)
        return hub.snapshot()

    @app.post("/hook")
    async def hook(request: Request, x_buddy_token: str | None = Header(None)):
        check(x_buddy_token)
        payload = await request.json()
        return await hub.handle_hook(payload)

    @app.post("/api/dictate")
    async def api_dictate(request: Request, x_buddy_token: str | None = Header(None)):
        check(x_buddy_token)
        body = await request.json()
        return dictate(body.get("session_id"), body.get("text", ""))

    @app.post("/api/transcribe")
    async def api_transcribe(request: Request, x_buddy_token: str | None = Header(None)):
        check(x_buddy_token)
        if not Transcriber.available():
            raise HTTPException(status_code=503, detail="faster-whisper is not installed on the hub")
        audio = await request.body()
        if not audio:
            raise HTTPException(status_code=400, detail="empty audio")
        result = await asyncio.to_thread(transcriber.transcribe, audio)
        log.info("transcribed %.1fs of audio in %.1fs", result["audio_seconds"], result["took_seconds"])
        return result

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, token: str | None = Query(None)):
        if not token or not hmac.compare_digest(token, settings.token):
            await ws.close(code=4401)
            return
        await ws.accept()
        hub.devices.add(ws)
        await hub.notify(None)
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                kind = msg.get("type")
                if kind == "decision":
                    ok = hub.decide(msg.get("id", ""), msg.get("behavior", ""), msg.get("via", "touch"))
                    await ws.send_text(json.dumps({"type": "decision_result", "id": msg.get("id"), "ok": ok}))
                elif kind == "dictate":
                    result = dictate(msg.get("session_id"), msg.get("text", ""))
                    await ws.send_text(json.dumps({"type": "dictate_result", **result}))
                elif kind == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            hub.devices.discard(ws)
            await hub.notify(None)

    return app
