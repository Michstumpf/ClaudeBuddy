import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub.intents import parse  # noqa: E402

SESSIONS = [("a", "DataHub Sharing Chat"), ("b", "HIPAA Compliance"), ("c", "git-d6"), ("d", "Ideas & Votes")]


@pytest.mark.parametrize("text,session,body", [
    ("Manda para a HIPAA: roda os testes.", "HIPAA Compliance", "roda os testes."),
    ("Buddy, manda pra sessão data hub que abra um pull request.", "DataHub Sharing Chat", "abra um pull request."),
    ("Envia para o git-d6, faz o commit", "git-d6", "faz o commit"),
    ("Pede pra DataHub Sharing Chat revisar o README", "DataHub Sharing Chat", "revisar o README"),
    ("Manda para a Ideas and Votes: anota essa ideia", "Ideas & Votes", "anota essa ideia"),
])
def test_send_to_session(text, session, body):
    intent = parse(text, SESSIONS)
    assert intent and intent.kind == "send"
    assert (intent.session_name, intent.body) == (session, body)


@pytest.mark.parametrize("text,session", [
    ("O que a DataHub está fazendo?", "DataHub Sharing Chat"),
    ("Buddy, o que a sessão HIPAA tá fazendo?", "HIPAA Compliance"),
    ("Como está a HIPAA?", "HIPAA Compliance"),
    ("Status do git-d6", "git-d6"),
])
def test_status_question(text, session):
    intent = parse(text, SESSIONS)
    assert intent and intent.kind == "status" and intent.session_name == session


@pytest.mark.parametrize("text,kind", [
    ("Pode aprovar.", "approve"), ("Buddy, aprova", "approve"), ("Pode seguir!", "approve"),
    ("Nega.", "deny"), ("Não aprova", "deny"), ("Recusa", "deny"),
])
def test_approve_deny(text, kind):
    assert parse(text, SESSIONS).kind == kind


@pytest.mark.parametrize("text", [
    "Roda os testes e me diz se passou",                     # ordinary dictation
    "Manda para a sessão Fulano: oi",                        # unknown session
    "Pode aprovar o pull request do DataHub amanhã cedo, por favor",  # long: not a command
    "Como funciona o hook de PermissionRequest?",            # 'como' but not a session
    "Manda para a HIPAA",                                    # nothing to send
])
def test_not_an_intent(text):
    assert parse(text, SESSIONS) is None


# ---- through the hub: dictation handled instead of typed -------------------------

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from buddy_hub import app as app_module  # noqa: E402
from buddy_hub.app import create_app  # noqa: E402
from buddy_hub.config import Settings  # noqa: E402

TOKEN = "test-token"
H = {"X-Buddy-Token": TOKEN}


def hub_with(monkeypatch, said):
    monkeypatch.setattr(app_module.Transcriber, "available", staticmethod(lambda: True))
    monkeypatch.setattr(app_module.Transcriber, "transcribe",
                        lambda self, audio: {"text": said, "audio_seconds": 1, "took_seconds": 0.1})
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run"))
    return app


def hook(c, event, sid, name, **extra):
    c.post("/hook", headers=H, json={"hook_event_name": event, "session_id": sid, "cwd": f"/x/{name}",
                                     "buddy": {"host": "dell", "tmux_session": name, "tmux_pane": "%7"}, **extra})


def test_send_to_session_is_delivered_not_typed(monkeypatch):
    app = hub_with(monkeypatch, "Manda para a HIPAA: roda os testes.")
    sent = []
    with TestClient(app) as c:
        monkeypatch.setattr(app.state.sender, "send", lambda pane, text: (sent.append((pane, text)), (True, "ok"))[1])
        hook(c, "SessionStart", "b", "HIPAA Compliance")
        r = c.post("/api/transcribe", content=b"RIFF", headers=H).json()
    assert r["handled"] is True and r["text"] == "" and "HIPAA" in r["detail"]
    assert sent == [("%7", "roda os testes.")]


def test_ordinary_dictation_is_typed(monkeypatch):
    app = hub_with(monkeypatch, "Roda os testes e me diz se passou")
    with TestClient(app) as c:
        r = c.post("/api/transcribe", content=b"RIFF", headers=H).json()
    assert r["text"] == "Roda os testes e me diz se passou" and "handled" not in r


def test_pode_aprovar_without_pending_is_just_typed(monkeypatch):
    app = hub_with(monkeypatch, "Pode seguir!")
    with TestClient(app) as c:
        r = c.post("/api/transcribe", content=b"RIFF", headers=H).json()
    assert r["text"] == "Pode seguir!"  # likely an answer to the session


def test_voice_approval(monkeypatch):
    app = hub_with(monkeypatch, "Pode aprovar.")
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={TOKEN}") as ws:  # a Buddy makes it wait
        ws.receive_json()
        hub = app.state.hub
        result = {}

        def ask():
            result["r"] = c.post("/hook", headers=H, json={
                "hook_event_name": "PermissionRequest", "session_id": "b", "cwd": "/x/hipaa",
                "tool_name": "Bash", "tool_input": {"command": "npm test"}}).json()

        import threading
        t = threading.Thread(target=ask)
        t.start()
        for _ in range(50):
            if hub.approvals:
                break
            asyncio.run(asyncio.sleep(0.02))
        r = c.post("/api/transcribe", content=b"RIFF", headers=H).json()
        t.join(timeout=5)
    assert r["handled"] is True and r["detail"].startswith("aprovado")
    assert result["r"]["decision"] == "allow"
