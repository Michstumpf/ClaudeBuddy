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
import ipaddress
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
from .battery import BatteryWatch, notify_desktop
from . import intents
from .claude_usage import ClaudeUsage
from .daystats import DayStats
from . import calendar_watch, github_watch, work_watch
from .jokes import JokeTeller
from .local_sessions import read_local_sessions
from .pomodoro import Pomodoro
from .prefs import Prefs
from . import weather as weather_api
from .state import Hub, voice_command
from .stt import RemoteFirst, Transcriber
from .summarizer import Summarizer, load_key, usage_summary
from .tts import RemoteFirstSpeaker, Speaker, louder, speakable, to_stereo

log = logging.getLogger("buddy.hub")
SIMULATOR = Path(__file__).resolve().parents[2] / "simulator" / "index.html"


class NetworkAllowlist:
    """Serves only clients from the allowed networks (HTTP and WebSocket),
    whatever token they bring: the hub may listen on every interface of a
    notebook that also joins other people's networks."""

    def __init__(self, app, networks: list):
        self.app = app
        self.networks = [ipaddress.ip_network(n, strict=False) for n in networks]

    def allowed(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in n for n in self.networks)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            host = (scope.get("client") or ("", 0))[0]
            if not self.allowed(host):
                log.warning("refused a %s from %s (not in allowed_networks)", scope["type"], host)
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"forbidden network"})
                else:
                    await send({"type": "websocket.close", "code": 4403})
                return
        await self.app(scope, receive, send)


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
    battery = BatteryWatch()
    hub.extra["battery"] = None
    pomodoro = Pomodoro()
    hub.extra["pomodoro"] = None
    hub.extra["focus"] = None  # why the Buddy is in focus mode (no jokes): "pomodoro", "reunião"…
    stats = DayStats()
    claude_usage = ClaudeUsage()
    hub.extra["claude_month"] = None  # API-equivalent estimate from local transcripts
    github = github_watch.GitHubWatch()
    hub.extra["github"] = None  # {"reviews": n, "failing": n}
    deferred: list[tuple[str, str]] = []  # notices held back while in focus mode
    jira = work_watch.JiraWatch()
    slack = work_watch.SlackWatch()
    hub.extra["jira"] = None  # {"open": n}
    calendar = calendar_watch.CalendarWatch()
    hub.extra["calendar"] = None  # {"now": title | None, "next": {"title", "starts_in"} | None}
    working_since: dict[str, float] = {}  # session id -> when it started working
    long_warned: set[str] = set()
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

    # ---- notices: short messages in the Buddy's bubble (beep, optionally spoken) --

    async def send_notice(text: str, kind: str, speak: bool = False) -> None:
        log.info("notice (%s)", kind)
        message = {"type": "notice", "kind": kind, "text": text}
        if speak and not hub.night and speaker.available():
            try:
                wav = await asyncio.to_thread(speaker.synthesize, text)
                message.update(store_clip(await asyncio.to_thread(louder, wav)))
            except Exception:
                log.exception("could not voice the notice")
        if not await send_to_buddy(message) and speak and settings.speak_on in ("auto", "local") and "url" in message:
            await asyncio.to_thread(play_locally, clips[message["id"]])  # no Buddy: say it on the hub

    def refresh_focus() -> None:
        in_meeting = (hub.extra.get("calendar") or {}).get("now")
        hub.extra["focus"] = "reunião" if in_meeting else "pomodoro" if pomodoro.focusing else None
        hub.extra["pomodoro"] = pomodoro.public()

    async def pomodoro_command(action: str) -> None:
        text = pomodoro.start() if action == "start" else pomodoro.stop()
        refresh_focus()
        await hub.notify(None)
        if text:
            await send_notice(text, "pomodoro", speak=True)

    def check_long_tasks(now: float) -> list[str]:
        """Sessions that just crossed the long-task threshold."""
        limit = prefs["long_task_min"] * 60
        crossed = []
        for sid, s in hub.sessions.items():
            if s.status != "working":
                since = working_since.pop(sid, None)
                if since:
                    stats.worked(s.name, now - since)
                long_warned.discard(sid)
                continue
            since = working_since.setdefault(sid, now)
            if limit and now - since >= limit and sid not in long_warned:
                long_warned.add(sid)
                crossed.append(f"{s.name} está trabalhando há {int((now - since) // 60)} minutos.")
        return crossed

    # ---- jokes and weather --------------------------------------------------

    def calm() -> bool:
        return not hub.night and not hub.extra.get("focus") and not hub.approvals and not any(
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

    async def github_loop() -> None:
        while True:
            if prefs["github"] and github_watch.available():
                try:
                    for text in github.update(await asyncio.to_thread(github_watch.fetch)):
                        deferred.append((text, "github"))
                    hub.extra["github"] = github.counts()
                    await hub.notify(None)
                except Exception as exc:
                    log.warning("github poll failed (%s)", exc.__class__.__name__)
            await flush_deferred()
            await asyncio.sleep(github_watch.POLL_SECONDS)

    async def flush_deferred() -> None:
        """Notices that shouldn't interrupt focus (pomodoro, meeting) wait for it to end."""
        while deferred and not hub.extra.get("focus") and not hub.night:
            text, kind = deferred.pop(0)
            await send_notice(text, kind)
            await asyncio.sleep(16)  # one bubble at a time

    async def jira_loop() -> None:
        creds = work_watch.jira_credentials(settings.jira_site, settings.jira_email)
        if not creds:
            return
        while True:
            if prefs["jira"]:
                try:
                    for text in jira.update(await asyncio.to_thread(work_watch.jira_fetch, *creds)):
                        deferred.append((text, "jira"))
                    hub.extra["jira"] = jira.counts()
                except Exception as exc:
                    log.warning("jira poll failed (%s)", exc.__class__.__name__)
            await flush_deferred()
            await asyncio.sleep(work_watch.JIRA_POLL_SECONDS)

    async def slack_loop() -> None:
        token = work_watch.slack_token()
        if not token:
            return
        while True:
            if prefs["slack"]:
                try:
                    slack.user_id, mentions = await asyncio.to_thread(work_watch.slack_fetch, token, slack.user_id)
                    for text in slack.update(mentions):
                        deferred.append((text, "slack"))
                except Exception as exc:
                    log.warning("slack poll failed (%s)", exc.__class__.__name__)
            await flush_deferred()
            await asyncio.sleep(work_watch.SLACK_POLL_SECONDS)

    async def calendar_loop() -> None:
        url = calendar_watch.load_url(settings.calendar_url)
        if not url:
            return
        fetched_at = 0.0
        while True:
            now_ts = time.time()
            if prefs["calendar"] and now_ts - fetched_at > calendar_watch.POLL_SECONDS:
                try:
                    from datetime import datetime

                    ics = await asyncio.to_thread(calendar_watch.fetch_ics, url)
                    now = datetime.now().astimezone()
                    calendar.upcoming = await asyncio.to_thread(calendar_watch.events, ics, now, 24, settings.calendar_email)
                    fetched_at = now_ts
                except Exception as exc:
                    log.warning("calendar fetch failed (%s)", exc.__class__.__name__)
            if prefs["calendar"]:
                from datetime import datetime

                notices, public = calendar.tick(datetime.now().astimezone())
                if public != hub.extra.get("calendar"):
                    hub.extra["calendar"] = public
                    refresh_focus()
                    await hub.notify(None)
                for text in notices:
                    await send_notice(text, "calendar", speak=True)
            await flush_deferred()
            await asyncio.sleep(30)

    async def claude_usage_loop() -> None:
        while True:
            try:
                hub.extra["claude_month"] = await asyncio.to_thread(claude_usage.month)
            except Exception:
                log.exception("claude usage estimate failed")
            await asyncio.sleep(600)

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

    STATUS_PT = {"working": "trabalhando", "waiting": "esperando você", "done": "terminou",
                 "idle": "parada", "offline": "offline"}

    async def handle_intent(text: str) -> str | None:
        """Voice intents (intents.py). Returns what was done, or None to type the text."""
        live = [(sid, s.name) for sid, s in hub.sessions.items() if s.status != "offline"]
        intent = intents.parse(text, live)
        if intent is None:
            return None
        if intent.kind in ("approve", "deny"):
            pending = list(hub.approvals.values())
            if not pending:
                return None  # probably an answer to the session itself: type it
            if len(pending) > 1:
                await send_notice(f"Tem {len(pending)} pedidos esperando: decide pelo toque.", "voice", speak=True)
                return "vários pedidos: decida pelo toque"
            a = pending[0]
            if intent.kind == "approve" and a.dangerous:
                await send_notice("Esse comando é perigoso: só aprovo pelo toque.", "voice", speak=True)
                return "comando perigoso: aprove pelo toque"
            hub.decide(a.id, "allow" if intent.kind == "approve" else "deny", via="voice")
            word = "Aprovado" if intent.kind == "approve" else "Negado"
            await send_notice(f"{word}: {a.tool_name} em {a.session_name}.", "voice")
            return f"{word.lower()} ({a.session_name})"
        session = hub.sessions.get(intent.session_id)
        if session is None:
            return None
        if intent.kind == "send":
            if not session.tmux_pane:
                await send_notice(f"Não consigo escrever em {session.name}: ela não está no tmux.", "voice", speak=True)
                return f"{session.name} não está no tmux"
            hub.note_voice_text(intent.body)  # its answer gets spoken back
            ok, why = sender.send(session.tmux_pane, intent.body)
            await send_notice(f"Enviado para {session.name}." if ok else f"Falhou: {why}", "voice")
            return f"enviado para {session.name}" if ok else why
        if intent.kind == "status":
            answer = hub.last_answers.get(session.id, "")
            line = f"{session.name} está {STATUS_PT.get(session.status, session.status)}."
            if answer:
                summary = await summarizer.summarize(f"O que a sessão {session.name} está fazendo?", answer)
                line += " " + (summary or speakable(answer))
            await send_notice(line, "voice", speak=True)
            return f"status de {session.name}"
        return None

    async def apply_voice_command(text: str) -> str | None:
        """Short voice commands; returns what was done when the text must not be typed."""
        command = voice_command(text)
        if command in ("pomodoro_start", "pomodoro_stop"):
            await pomodoro_command("start" if command == "pomodoro_start" else "stop")
            return "pomodoro " + ("iniciado" if command == "pomodoro_start" else "encerrado")
        if command == "night":
            if prefs["daily_summary"] and not hub.night:
                cwds = [x.cwd for x in hub.sessions.values() if x.cwd]
                await send_notice(await asyncio.to_thread(stats.summary, None, cwds), "summary", speak=True)
            await set_night(True, "voice")
        elif hub.night:  # "bom dia", or simply talking to it again
            await set_night(False, "voice")
        return None  # "boa noite"/"bom dia" are still typed: the session may answer them

    async def housekeeping() -> None:
        host = socket.gethostname()
        while True:
            try:
                changed = False
                if settings.sessions_dir:
                    entries = await asyncio.to_thread(read_local_sessions, settings.sessions_dir)
                    changed = hub.reconcile(entries, host)
                changed = hub.expire_stale(settings.stale_working) or changed
                now = time.time()
                for text in check_long_tasks(now):
                    await send_notice(text, "long_task")
                turn = pomodoro.tick(now)
                if turn or pomodoro.phase:
                    refresh_focus()  # also refreshes the countdown the Buddy shows
                    changed = True
                if turn:
                    if pomodoro.phase == "break":
                        stats.count("pomodoros")
                    await send_notice(turn, "pomodoro", speak=True)
                if changed:
                    await hub.notify(None)
            except Exception:
                log.exception("housekeeping failed")
            await asyncio.sleep(settings.sessions_poll)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks = [asyncio.create_task(housekeeping())]
        if settings.background_extras:
            tasks += [asyncio.create_task(joke_loop()), asyncio.create_task(weather_loop()),
                      asyncio.create_task(claude_usage_loop()), asyncio.create_task(github_loop()),
                      asyncio.create_task(calendar_loop()), asyncio.create_task(jira_loop()),
                      asyncio.create_task(slack_loop())]
        yield
        for task in tasks:
            task.cancel()

    app = FastAPI(title="Claude Buddy hub", lifespan=lifespan)
    if settings.allowed_networks is not None:
        app.add_middleware(NetworkAllowlist, networks=settings.allowed_networks)
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
        response = await hub.handle_hook(payload)
        event = payload.get("hook_event_name")
        session = hub.sessions.get(payload.get("session_id") or "")
        if event == "UserPromptSubmit" and session:
            stats.session_used(session.name, session.cwd)
            if session.voice_turn:
                stats.count("voice_turns")
        elif event == "PermissionRequest" and response.get("decision") in ("allow", "deny"):
            stats.count("approved" if response["decision"] == "allow" else "denied")
        return response

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
        text = result.get("text", "")
        hub.note_voice_text(text)
        handled = await apply_voice_command(text)
        detail = handled or await handle_intent(text)
        if detail:  # the hub took care of it: nothing to type in the focused window
            result.update({"text": "", "handled": True, "detail": detail})
        return result

    @app.get("/api/today")
    async def api_today(x_buddy_token: str | None = Header(None)):
        """Today's numbers (the "boa noite" summary) and this month's Claude usage estimate."""
        check(x_buddy_token)
        commits = await asyncio.to_thread(stats.commits, [x.cwd for x in hub.sessions.values() if x.cwd])
        return {"stats": {**stats.data, "commits": commits}, "summary": stats.summary(commits),
                "claude_month": hub.extra.get("claude_month")}

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
                if kind not in ("ping", "battery"):  # a touch, a decision, a dictation: the Buddy in use
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
                elif kind == "battery":
                    alert = battery.update(msg.get("percent", 0), msg.get("charging", False))
                    hub.extra["battery"] = battery.last
                    if alert:
                        asyncio.get_running_loop().run_in_executor(None, notify_desktop, alert)
                    await hub.notify(None)
                elif kind == "night":  # the device itself: screen face down on the desk / picked up
                    await set_night(bool(msg.get("on")), "device")
                elif kind == "pomodoro":
                    await pomodoro_command("start" if msg.get("action") == "start" else "stop")
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
