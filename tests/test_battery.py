import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub import app as app_module  # noqa: E402
from buddy_hub.app import create_app  # noqa: E402
from buddy_hub.battery import BatteryWatch  # noqa: E402
from buddy_hub.config import Settings  # noqa: E402

TOKEN = "test-token"


def test_alerts_once_per_level_while_discharging():
    w = BatteryWatch()
    assert [w.update(p, False) for p in (40, 20, 15, 14, 12)] == [None, None, "Buddy com 15% de bateria", None, None]
    assert w.update(5, False) == "Buddy com 5% de bateria: me coloca para carregar!"
    assert w.update(4, False) is None
    assert w.last["percent"] == 4 and w.last["charging"] is False


def test_charging_rearms_and_never_alerts():
    w = BatteryWatch()
    w.update(14, False)
    assert w.update(10, True) is None          # plugged in
    assert w.update(14, False) == "Buddy com 14% de bateria"  # unplugged again, still low: warns again


def test_jump_straight_to_critical_fires_only_the_lowest():
    w = BatteryWatch()
    assert w.update(3, False).startswith("Buddy com 3%")
    assert w.update(3, False) is None


def test_rearms_after_recovering_above_margin():
    w = BatteryWatch()
    w.update(15, False)
    w.update(18, False)                         # small bounce: still disarmed
    assert w.update(15, False) is None
    w.update(25, False)                         # really recovered
    assert w.update(15, False) is not None


def test_battery_report_reaches_state_and_alerts(monkeypatch):
    alerts = []
    monkeypatch.setattr(app_module, "notify_desktop", alerts.append)
    app = create_app(Settings(token=TOKEN, dictation_backend="dry-run"))
    with TestClient(app) as c, c.websocket_connect(f"/ws?token={TOKEN}") as ws:
        assert ws.receive_json()["battery"] is None
        ws.send_json({"type": "battery", "percent": 12, "charging": False})
        state = next(m for m in (ws.receive_json() for _ in range(5)) if m.get("battery"))
        assert state["battery"]["percent"] == 12
    assert alerts == ["Buddy com 12% de bateria"]
