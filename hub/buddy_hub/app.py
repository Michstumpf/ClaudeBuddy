"""HTTP + WebSocket server.

- POST /hook        Claude Code hook events (from hooks/buddy_hook.py)
- WS   /ws          the Buddy device (or the browser simulator)
- POST /api/dictate send dictated text to a session (e.g. from a hotkey client)
- POST /api/transcribe  audio in (body) -> text out, with faster-whisper
- GET  /api/speech/{id} a spoken reply or joke (WAV) announced to devices
- GET  /api/state   current snapshot
- GET  /            the Buddy simulator
"""

import asyncio
import hmac
import json
import logging
import random
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

from .config import Settings
from .dictation import make_sender
from .jokes import JokeTeller
from .local_sessions import read_local_sessions
from .prefs import Prefs
from . import weather as weather_api
from .state import Hub, voice_command
from .stt import RemoteFirst, Transcriber
from .summarizer import Summarizer, load_key, usage_summary
from .tts import RemoteFirstSpeaker, Speaker, louder, speakable, to_stereo

log = logging.getLogger("buddy.hub")
SIMULATOR = Path(__file__).resolve().parents[2] / "simulator" / "index.html"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    hub = Hub(approval_timeout=settings.approval_timeout)
    sender = make_sender(settings.dictation_backend)
    transcriber = Transcriber(settings.stt_model, settings.stt_language, beam_size=settings.stt_beam_size)
    if settings.stt_remote:
        transcriber = RemoteFirst(settings.stt_remote, settings.token, local=transcriber)
    speaker = Speaker(settings.tts_voice)
    if settings.stt_remote:  # the same desktop worker also has the GPU voice
        speaker = RemoteFirstSpeaker(settings.stt_remote, settings.token, local=speaker)
    summarizer = Summarizer(load_key())
    prefs = Prefs(settings.prefs_file, defaults={**settings.pref_defaults, "city": settings.weather_city})
    jokes = JokeTeller()
    hub.extra["settings"] = prefs.values
    hub.extra["weather"] = None
    clips: OrderedDict[str, bytes] = OrderedDict()  # recent spoken replies, for devices
    # When several Buddies are connected (e.g. the device plus a simulator tab),
    # only the most recently used one speaks; otherwise the same reply plays
    # twice, a few ms apart, and sounds like two robotic voices.
    last_active: dict = {}

    def play_locally(wav: bytes) -> None:
        if not shutil.which("pw-play"):
            log.warning("pw-play not found; cannot speak on the hub")
            return
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(to_stereo(wav))  # mono plays much quieter on this desktop
            f.flush()
            subprocess.run(["pw-play", f.name], timeout=120, check=False)

    async def speak_reply(session, answer: str) -> None:
        target = settings.speak_on
        if target == "off" or hub.night or not speaker.available():
            return
        question = hub.voice_questions.pop(session.id, "")
        text = await summarizer.summarize(question, answer) or speakable(answer)
        if not text:
            return
        try:
            wav = await asyncio.to_thread(speaker.synthesize, text)
            wav = await asyncio.to_thread(louder, wav)
        except Exception:
            log.exception("speech synthesis failed")
            return
        if target == "auto":
            target = "devices" if hub.devices else "local"
        log.info("speaking reply of %s on %s (%d chars)", session.name, target, len(text))
        if target == "local":
            await asyncio.to_thread(play_locally, wav)
            return
        await send_to_buddy({"type": "speech", "session": session.name, "text": text, **store_clip(wav)})

    def store_clip(wav: bytes) -> dict:
        clip_id = uuid.uuid4().hex[:12]
        clips[clip_id] = wav
        while len(clips) > 10:
            clips.popitem(last=False)
        return {"id": clip_id, "url": f"/api/speech/{clip_id}"}

    async def send_to_buddy(message: dict) -> bool:
        """To the most recently used Buddy only: one voice, one bubble."""
        text = json.dumps(message)
        for ws in sorted(hub.devices, key=lambda d: last_active.get(d, 0.0), reverse=True):
            try:
                await ws.send_text(text)
                return True
            except Exception:
                hub.devices.discard(ws)
        return False

    # ---- jokes and weather --------------------------------------------------

    def calm() -> bool:
        return not hub.night and not hub.approvals and not any(
            s.status == "waiting" for s in hub.sessions.values())

    async def tell_joke() -> None:
        text = await jokes.tell(hub.snapshot(), hub.extra.get("weather"))
        message = {"type": "joke", "text": text}
        if prefs["joke_voice"] and speaker.available():
            try:
                wav = await asyncio.to_thread(speaker.synthesize, text)
                message.update(store_clip(await asyncio.to_thread(louder, wav)))
            except Exception:
                log.exception("could not voice the joke; showing it only")
        await send_to_buddy(message)

    async def joke_loop() -> None:
        next_at = time.time() + 10 * 60  # first one a while after start
        while True:
            await asyncio.sleep(30)
            if time.time() < next_at:
                continue
            if prefs["jokes"] and hub.devices and calm():
                try:
                    await tell_joke()
                except Exception:
                    log.exception("joke failed")
                next_at = time.time() + prefs["joke_interval_min"] * 60 * random.uniform(0.8, 1.2)

    weather_now = asyncio.Event()  # set to refresh right away (city changed)

    async def refresh_weather() -> None:
        geo = prefs["city_geo"]
        where = (geo["lat"], geo["lon"], geo["name"]) if geo else \
            (settings.weather_lat, settings.weather_lon, settings.weather_city)
        try:
            hub.extra["weather"] = await asyncio.to_thread(weather_api.fetch, *where)
            await hub.notify(None)
        except Exception as exc:
            log.warning("weather unavailable (%s)", exc.__class__.__name__)

    async def weather_loop() -> None:
        while True:
            if prefs["weather"]:
                await refresh_weather()
            weather_now.clear()
            try:
                await asyncio.wait_for(weather_now.wait(), weather_api.REFRESH_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def change_city(ws, query: str) -> None:
        """Search the city first; only a city that exists replaces the current one."""
        try:
            geo = await asyncio.to_thread(weather_api.geocode, query)
        except Exception as exc:
            log.warning("city search failed (%s)", exc.__class__.__name__)
            geo = None
        if geo is None:
            await ws.send_text(json.dumps({"type": "settings_result", "ok": False,
                                           "detail": f"não encontrei a cidade \"{query}\""}))
            return
        prefs.update({"city": query, "city_geo": geo})
        await ws.send_text(json.dumps({"type": "settings_result", "ok": True,
                                       "detail": f"{geo['name']}, {geo['admin1'] or geo['country_code']}"}))
        await hub.notify(None)
        weather_now.set()

    hub.on_voice_reply = speak_reply

    async def set_night(night: bool, why: str) -> None:
        if hub.night != night:
            hub.night = night
            log.info("%s (%s)", "night mode" if night else "awake", why)
            await hub.notify({"kind": "night" if night else "morning", "session": ""})

    async def apply_voice_command(text: str) -> None:
        command = voice_command(text)
        if command == "night":
            await set_night(True, "voice")
        elif hub.night:  # "bom dia", or simply talking to it again
            await set_night(False, "voice")

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
        tasks = [asyncio.create_task(housekeeping())]
        if settings.background_extras:
            tasks += [asyncio.create_task(joke_loop()), asyncio.create_task(weather_loop())]
        yield
        for task in tasks:
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
        hub.note_voice_text(text)  # its answer gets spoken back
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
        if not settings.stt_remote and not Transcriber.available():
            raise HTTPException(status_code=503, detail="faster-whisper is not installed on the hub")
        audio = await request.body()
        if not audio:
            raise HTTPException(status_code=400, detail="empty audio")
        try:
            result = await asyncio.to_thread(transcriber.transcribe, audio)
        except RuntimeError as exc:  # worker unavailable and no local fallback
            raise HTTPException(status_code=503, detail=str(exc))
        log.info("transcribed %.1fs of audio in %.1fs on %s",
                 result["audio_seconds"], result["took_seconds"], result.get("backend"))
        hub.note_voice_text(result.get("text", ""))
        await apply_voice_command(result.get("text", ""))
        return result

    @app.get("/api/usage")
    async def usage(x_buddy_token: str | None = Header(None)):
        """Claude Haiku spend for spoken summaries: this month and all time."""
        check(x_buddy_token)
        return await asyncio.to_thread(usage_summary)

    @app.get("/api/speech/{clip_id}")
    async def speech(clip_id: str, token: str | None = Query(None), x_buddy_token: str | None = Header(None)):
        check(x_buddy_token or token)  # query token: an <audio> element can't send headers
        wav = clips.get(clip_id)
        if wav is None:
            raise HTTPException(status_code=404, detail="unknown or expired clip")
        return Response(wav, media_type="audio/wav")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, token: str | None = Query(None)):
        if not token or not hmac.compare_digest(token, settings.token):
            await ws.close(code=4401)
            return
        await ws.accept()
        hub.devices.add(ws)
        last_active[ws] = time.time()
        await hub.notify(None)
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                kind = msg.get("type")
                if kind != "ping":  # a touch, a decision, a dictation: this is the Buddy in use
                    last_active[ws] = time.time()
                    if kind == "touch" and hub.night:
                        await set_night(False, "touch")
                if kind == "settings":
                    values = dict(msg.get("values", {}))
                    values.pop("city_geo", None)  # only the hub sets it, after a search
                    city = values.pop("city", None)
                    if prefs.update(values):
                        if not prefs["weather"]:
                            hub.extra["weather"] = None
                        else:
                            weather_now.set()
                        await hub.notify(None)
                    if city and " ".join(str(city).split()) != prefs["city"]:
                        await change_city(ws, " ".join(str(city).split()))
                elif kind == "joke_now":
                    asyncio.get_running_loop().create_task(tell_joke())
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
            last_active.pop(ws, None)
            await hub.notify(None)

    return app
