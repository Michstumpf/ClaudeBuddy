import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from buddy_hub import app as app_module
from buddy_hub import prefs as prefs_module
from buddy_hub.app import create_app
from buddy_hub.config import Settings
from buddy_hub.jokes import FALLBACK, JokeTeller, moment
from buddy_hub.prefs import Prefs
from buddy_hub.weather import describe, parse

TOKEN = "test-token"


# ---- preferences --------------------------------------------------------------

def test_prefs_validate_and_persist(tmp_path):
    path = tmp_path / "p.json"
    p = Prefs(path)
    assert p["jokes"] is True and p["joke_interval_min"] == 45
    assert p.update({"joke_voice": 0, "joke_interval_min": "30", "unknown": 1}) is True
    assert p["joke_voice"] is False and p["joke_interval_min"] == 30
    assert p.update({"joke_interval_min": 7}) is False  # not an allowed interval
    assert json.loads(path.read_text()) == p.values
    assert Prefs(path)["joke_voice"] is False  # reloaded from disk


def test_default_prefs_file_is_isolated_in_tests(tmp_path):
    assert str(prefs_module.PREFS_FILE).startswith(str(tmp_path.parent))


# ---- weather ----------------------------------------------------------------------

SAMPLE = {"current": {"temperature_2m": 17.4, "weather_code": 3, "is_day": 1},
          "daily": {"temperature_2m_min": [12.0], "temperature_2m_max": [18.2]}}


def test_weather_parse():
    w = parse(SAMPLE)
    assert (w["temp"], w["min"], w["max"], w["text"], w["icon"]) == (17, 12, 18, "nublado", "☁")


def test_weather_night_icon():
    assert describe(0, is_day=False) == ("céu limpo", "☾")
    assert describe(61)[0] == "chuva" and describe(999)[0] == "tempo incerto"


# ---- jokes -------------------------------------------------------------------------

def test_moment_has_counts_but_no_session_content():
    snap = {"sessions": [{"name": "HIPAA Compliance", "status": "working", "last_message": "segredo"},
                         {"name": "DataHub", "status": "idle"}]}
    m = moment(snap, {"temp": 17, "text": "nublado"})
    assert "2 sessões abertas, 1 trabalhando" in m and "17°C, nublado" in m
    assert "HIPAA" not in m and "DataHub" not in m and "segredo" not in m


def test_fallback_jokes_without_key_do_not_repeat_soon():
    teller = JokeTeller(api_key="")
    told = [asyncio.run(teller.tell({"sessions": []}, None)) for _ in range(5)]
    assert len(set(told)) == 5 and all(j in FALLBACK for j in told)


# ---- through the Buddy -------------------------------------------------------------

class FakeSpeaker:
    def __init__(self, voice):
        pass

    def available(self):
        return True

    def synthesize(self, text):
        return b"RIFF-joke"


def test_settings_screen_and_joke_now(monkeypatch):
    monkeypatch.setattr(app_module, "Speaker", FakeSpeaker)
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run"))
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={TOKEN}") as ws:
        first = ws.receive_json()
        assert first["settings"]["jokes"] is True and "weather" in first
        ws.send_json({"type": "settings", "values": {"joke_interval_min": 15}})
        state = next(m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "state")
        assert state["settings"]["joke_interval_min"] == 15

        ws.send_json({"type": "joke_now"})
        joke = next(m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "joke")
        assert joke["text"] in FALLBACK and joke["url"].startswith("/api/speech/")
        assert c.get(f"{joke['url']}?token={TOKEN}").content == b"RIFF-joke"

        ws.send_json({"type": "settings", "values": {"joke_voice": False}})
        ws.send_json({"type": "joke_now"})
        joke = next(m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "joke")
        assert "url" not in joke  # bubble only
