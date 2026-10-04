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


# ---- weather city ----------------------------------------------------------------

from buddy_hub import weather as weather_module  # noqa: E402
from buddy_hub.weather import pick  # noqa: E402

RESULTS = [
    {"name": "São José", "admin1": "Santa Catarina", "country": "Brasil", "country_code": "BR", "latitude": -27.6, "longitude": -48.6},
    {"name": "San José", "admin1": "San José", "country": "Costa Rica", "country_code": "CR", "latitude": 9.9, "longitude": -84.1},
    {"name": "São José", "admin1": "Rio Grande do Sul", "country": "Brasil", "country_code": "BR", "latitude": -29.0, "longitude": -51.0},
]


def test_pick_city():
    assert pick(RESULTS, "RS")["admin1"] == "Rio Grande do Sul"           # UF code
    assert pick(RESULTS, "santa catarina")["admin1"] == "Santa Catarina"  # state name
    assert pick(RESULTS, "Costa Rica")["country_code"] == "CR"            # country
    assert pick(RESULTS[1:2] + RESULTS[:1])["country_code"] == "BR"       # Brazil first by default
    assert pick([]) is None


def test_change_city_over_websocket(monkeypatch):
    found = {"name": "Porto Alegre", "admin1": "Rio Grande do Sul", "country_code": "BR", "lat": -30.03, "lon": -51.23}
    monkeypatch.setattr(weather_module, "geocode", lambda q: found if q.startswith("Porto") else None)
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run"))
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={TOKEN}") as ws:
        assert ws.receive_json()["settings"]["city"] == "Canoas"   # fallback
        ws.send_json({"type": "settings", "values": {"city": "Cidadeinexistente"}})
        bad = next(m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "settings_result")
        assert bad["ok"] is False and "Cidadeinexistente" in bad["detail"]

        ws.send_json({"type": "settings", "values": {"city": "Porto   Alegre", "city_geo": {"name": "hack", "lat": 0, "lon": 0}}})
        good = next(m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "settings_result")
        assert good["ok"] is True and good["detail"] == "Porto Alegre, Rio Grande do Sul"
        state = next(m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "state")
        assert state["settings"]["city"] == "Porto Alegre"
        assert state["settings"]["city_geo"]["name"] == "Porto Alegre"  # the hub's search, not the client's


# ---- config.toml ---------------------------------------------------------------

from buddy_hub.config import Settings as HubSettings  # noqa: E402


def test_config_file_layers(monkeypatch, tmp_path):
    monkeypatch.setattr("buddy_hub.config.TOKEN_FILE", tmp_path / "token")
    for var in ("BUDDY_STT_MODEL", "BUDDY_APPROVAL_TIMEOUT", "BUDDY_WEATHER_PLACE"):
        monkeypatch.delenv(var, raising=False)
    config = {"hub": {"approval_timeout": 30}, "stt": {"model": "medium"},
              "weather": {"city": "Florianópolis", "latitude": -27.6, "longitude": -48.5},
              "defaults": {"jokes": False, "joke_interval_min": 60}}
    s = HubSettings.from_env(config)
    assert (s.approval_timeout, s.stt_model, s.weather_city) == (30.0, "medium", "Florianópolis")
    assert s.stt_beam_size == 1  # not in the file: built-in default
    monkeypatch.setenv("BUDDY_STT_MODEL", "large-v3-turbo")  # env beats the file
    assert HubSettings.from_env(config).stt_model == "large-v3-turbo"

    p = Prefs(tmp_path / "prefs.json", defaults={**s.pref_defaults, "city": s.weather_city})
    assert (p["jokes"], p["joke_interval_min"], p["city"]) == (False, 60, "Florianópolis")
    p.update({"jokes": True})  # a choice on the screen...
    again = Prefs(tmp_path / "prefs.json", defaults={**s.pref_defaults, "city": s.weather_city})
    assert again["jokes"] is True  # ...beats the config default


def test_example_config_is_valid_and_matches_defaults():
    import tomllib
    from pathlib import Path

    example = tomllib.loads((Path(__file__).resolve().parents[1] / "hub" / "config.example.toml").read_text())
    s = HubSettings(token="x")
    assert example["hub"]["approval_timeout"] == s.approval_timeout
    assert example["stt"]["model"] == s.stt_model and example["tts"]["voice"] == s.tts_voice
    assert example["weather"]["city"] == s.weather_city
    assert {k: v for k, v in example["defaults"].items()} == {k: prefs_module.DEFAULTS[k] for k in example["defaults"]}


def test_skin_pref():
    p = Prefs()
    assert p["skin"] == "classic"
    assert p.update({"skin": "eyes"}) and p["skin"] == "eyes"
    assert not p.update({"skin": "neon"}) and p["skin"] == "eyes"  # unknown skins are ignored


# ---- network allowlist ---------------------------------------------------------------

def test_allowlist_blocks_other_networks_even_with_the_token():
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run",
                              allowed_networks=["127.0.0.0/8", "192.168.0.0/24", "100.64.0.0/10"]))
    H = {"X-Buddy-Token": TOKEN}
    for ip, ok in (("127.0.0.1", True), ("192.168.0.42", True), ("100.88.15.1", True),
                   ("10.0.0.5", False), ("203.0.113.9", False)):
        with TestClient(app, client=(ip, 5555)) as c:
            assert (c.get("/api/state", headers=H).status_code == 200) is ok, ip


def test_allowlist_closes_foreign_websockets():
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run", allowed_networks=["127.0.0.0/8"]))
    with TestClient(app, client=("10.0.0.5", 5555)) as c:
        with pytest.raises(Exception):
            with c.websocket_connect(f"/ws?token={TOKEN}") as ws:
                ws.receive_json()


def test_networks_from_env(monkeypatch, tmp_path):
    monkeypatch.setattr("buddy_hub.config.TOKEN_FILE", tmp_path / "token")
    monkeypatch.setenv("BUDDY_ALLOWED_NETWORKS", "127.0.0.0/8, 192.168.1.0/24")
    assert HubSettings.from_env({}).allowed_networks == ["127.0.0.0/8", "192.168.1.0/24"]
    monkeypatch.delenv("BUDDY_ALLOWED_NETWORKS")
    assert HubSettings.from_env({}).allowed_networks == ["127.0.0.0/8", "::1/128"]
