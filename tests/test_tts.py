import asyncio
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub import app as app_module  # noqa: E402
from buddy_hub.app import create_app  # noqa: E402
from buddy_hub.config import Settings  # noqa: E402
from buddy_hub.state import Hub  # noqa: E402
from buddy_hub.tts import speakable  # noqa: E402

TOKEN = "test-token"
H = {"X-Buddy-Token": TOKEN}


# ---- what gets said ---------------------------------------------------------

def test_speakable_keeps_opening_sentences():
    answer = "Terminei a integração. Os testes passaram. Falta o push. E mais isto. E aquilo."
    assert speakable(answer) == "Terminei a integração. Os testes passaram. Falta o push."


def test_speakable_drops_markdown_noise():
    answer = (
        "**Pronto:** o hub está no ar 🎉\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "```bash\nrm -rf /tmp/x\n```\n"
        "- veja [o README](https://github.com/x/y) e `config.py`\n"
    )
    said = speakable(answer)
    assert said == "Pronto: o hub está no ar. veja o README e config.py."
    for noise in ("**", "|", "```", "rm -rf", "http", "🎉", "`"):
        assert noise not in said


def test_speakable_caps_length():
    said = speakable("palavra " * 200)
    assert len(said) <= 321 and said.endswith("…")


def test_speakable_empty():
    assert speakable("") == "" and speakable("```\ncode only\n```") == ""


# ---- when it gets said: only answers to voice prompts -------------------------

def payload(event, **extra):
    return {"hook_event_name": event, "session_id": "s1", "cwd": "/x/api", **extra}


def run_turn(hub, prompt, answer="Feito. Tudo certo."):
    spoken = []

    async def on_voice_reply(session, msg):
        spoken.append((session.name, msg))

    hub.on_voice_reply = on_voice_reply

    async def turn():
        await hub.handle_hook(payload("UserPromptSubmit", prompt=prompt))
        await hub.handle_hook(payload("Stop", last_assistant_message=answer))
        await asyncio.sleep(0)  # let the reply task run

    asyncio.run(turn())
    return spoken


def test_voice_prompt_gets_spoken_reply():
    hub = Hub()
    hub.note_voice_text("Roda os testes, por favor.")
    assert run_turn(hub, "roda os testes por favor") == [("api", "Feito. Tudo certo.")]


def test_typed_prompt_stays_silent():
    hub = Hub()
    hub.note_voice_text("outra coisa que foi ditada")
    assert run_turn(hub, "roda os testes") == []


def test_dictation_plus_typed_suffix_still_counts():
    hub = Hub()
    hub.note_voice_text("abre um pull request")
    assert run_turn(hub, "abre um pull request no repo do datahub") != []


def test_voice_text_is_used_once_and_expires(monkeypatch):
    hub = Hub()
    hub.note_voice_text("roda os testes")
    assert run_turn(hub, "roda os testes") != []
    assert run_turn(hub, "roda os testes") == []  # same prompt typed again later
    hub.note_voice_text("roda os testes")
    later = time.time() + 120
    monkeypatch.setattr("buddy_hub.state.time.time", lambda: later)
    assert run_turn(hub, "roda os testes") == []


# ---- where it gets said: devices get a clip URL ---------------------------------

class FakeSpeaker:
    def __init__(self, voice):
        pass

    def available(self):
        return True

    def synthesize(self, text):
        return b"RIFF-fake-" + text.encode()


def test_reply_is_sent_to_connected_buddy(monkeypatch):
    monkeypatch.setattr(app_module, "Speaker", FakeSpeaker)
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run"))
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.receive_json()  # initial state
        app.state.hub.note_voice_text("roda os testes")
        c.post("/hook", json=payload("UserPromptSubmit", prompt="roda os testes"), headers=H)
        c.post("/hook", json=payload("Stop", last_assistant_message="Feito: 38 testes passaram."), headers=H)
        speech = next(m for m in (ws.receive_json() for _ in range(6)) if m["type"] == "speech")
        assert speech["text"] == "Feito: 38 testes passaram." and speech["session"] == "api"
        assert c.get(speech["url"]).status_code == 401
        clip = c.get(f"{speech['url']}?token={TOKEN}")
        assert clip.status_code == 200 and clip.content.startswith(b"RIFF-fake-")
