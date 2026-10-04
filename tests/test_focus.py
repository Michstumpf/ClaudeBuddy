import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub.app import create_app  # noqa: E402
from buddy_hub.config import Settings  # noqa: E402
from buddy_hub.pomodoro import Pomodoro  # noqa: E402
from buddy_hub.prefs import Prefs  # noqa: E402
from buddy_hub.state import voice_command  # noqa: E402

TOKEN = "test-token"


def test_pomodoro_cycle():
    p = Pomodoro(focus_min=25, break_min=5)
    assert p.public() is None and not p.focusing
    assert "25 minutos" in p.start(now=0)
    assert p.focusing and p.public(now=60)["ends_in"] == 25 * 60 - 60
    assert p.tick(now=100) is None
    assert "pausa" in p.tick(now=25 * 60)
    assert p.phase == "break" and not p.focusing and p.rounds == 1
    assert "foco" in p.tick(now=30 * 60)
    assert p.stop() == "Pomodoro encerrado." and p.public() is None
    assert p.stop() is None


@pytest.mark.parametrize("text,expected", [
    ("Buddy, começa um pomodoro", "pomodoro_start"),
    ("Pomodoro!", "pomodoro_start"),
    ("Para o pomodoro", "pomodoro_stop"),
    ("Boa noite", "night"),
])
def test_voice_commands(text, expected):
    assert voice_command(text) == expected


def test_long_task_pref():
    p = Prefs()
    assert p["long_task_min"] == 15
    assert p.update({"long_task_min": 30}) and p["long_task_min"] == 30
    assert not p.update({"long_task_min": 7})


def test_pomodoro_from_the_buddy_sets_focus_and_notices():
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run"))
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={TOKEN}") as ws:
        first = ws.receive_json()
        assert first["pomodoro"] is None and first["focus"] is None
        ws.send_json({"type": "pomodoro", "action": "start"})
        msgs = [ws.receive_json() for _ in range(3)]
        state = next(m for m in msgs if m["type"] == "state")
        notice = next(m for m in msgs if m["type"] == "notice")
        assert state["focus"] == "pomodoro" and state["pomodoro"]["phase"] == "focus"
        assert notice["kind"] == "pomodoro" and "foco" in notice["text"]
        ws.send_json({"type": "pomodoro", "action": "stop"})
        state = next(m for m in (ws.receive_json() for _ in range(3)) if m["type"] == "state")
        assert state["focus"] is None and state["pomodoro"] is None
