import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub.app import create_app  # noqa: E402
from buddy_hub.config import Settings  # noqa: E402
from buddy_hub.state import Hub, is_dangerous  # noqa: E402

TOKEN = "test-token"
H = {"X-Buddy-Token": TOKEN}


def payload(event, sid="s1", **extra):
    return {
        "hook_event_name": event,
        "session_id": sid,
        "cwd": "/home/dev/portrait/api",
        "buddy": {"host": "ubuntu", "tmux_session": "portrait-api", "tmux_pane": "%3"},
        **extra,
    }


@pytest.fixture
def client():
    app = create_app(Settings(token=TOKEN, approval_timeout=2.0, dictation_backend="dry-run"))
    with TestClient(app) as c:
        yield c


def test_rejects_bad_token(client):
    assert client.post("/hook", json=payload("SessionStart")).status_code == 401
    assert client.post("/hook", json=payload("SessionStart"), headers={"X-Buddy-Token": "nope"}).status_code == 401


def test_status_lifecycle(client):
    client.post("/hook", json=payload("SessionStart"), headers=H)
    client.post("/hook", json=payload("UserPromptSubmit", prompt="hi"), headers=H)
    s = client.get("/api/state", headers=H).json()["sessions"][0]
    assert (s["name"], s["host"], s["status"]) == ("portrait-api", "ubuntu", "working")

    client.post("/hook", json=payload("Stop", last_assistant_message="All tests pass."), headers=H)
    s = client.get("/api/state", headers=H).json()["sessions"][0]
    assert s["status"] == "done" and s["last_message"] == "All tests pass."

    client.post("/hook", json=payload("SessionEnd", reason="exit"), headers=H)
    assert client.get("/api/state", headers=H).json()["sessions"][0]["status"] == "offline"


def test_name_falls_back_to_cwd_basename(client):
    p = payload("SessionStart")
    p["buddy"] = {"host": "win"}
    p["cwd"] = "C:\\Users\\me\\projects\\claude-buddy"
    client.post("/hook", json=p, headers=H)
    assert client.get("/api/state", headers=H).json()["sessions"][0]["name"] == "claude-buddy"


def test_permission_without_device_returns_immediately(client):
    start = time.time()
    r = client.post("/hook", json=payload("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}), headers=H)
    assert r.json() == {"decision": None}
    assert time.time() - start < 1


async def _approval_flow(command, answer=None, timeout=0.5):
    """Runs a PermissionRequest through the Hub with a fake device that answers via `answer`."""
    hub = Hub(approval_timeout=timeout)
    hub.devices.add(object())
    seen = []

    async def device(snapshot, event):
        if snapshot["pending"]:
            seen.append(snapshot["pending"][0])
            if answer:
                hub.decide(snapshot["pending"][0]["id"], answer)

    hub.add_listener(device)
    body = await hub.handle_hook(payload("PermissionRequest", tool_name="Bash", tool_input={"command": command}))
    return body, seen, hub


def test_permission_approved_by_device():
    body, seen, hub = asyncio.run(_approval_flow("npm test", answer="allow"))
    assert body == {"decision": "allow"}
    assert seen[0]["summary"] == "npm test" and not seen[0]["dangerous"]
    assert hub.approvals == {} and hub.sessions["s1"].status == "working"


def test_permission_times_out_to_normal_dialog():
    body, seen, hub = asyncio.run(_approval_flow("npm test", answer=None, timeout=0.2))
    assert body == {"decision": None} and len(seen) == 1 and hub.approvals == {}


def test_websocket_receives_state(client):
    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "state" and first["devices"] == 1
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text()) == {"type": "pong"}


def test_websocket_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=nope") as ws:
            ws.receive_text()


def test_dangerous_command_cannot_be_voice_approved():
    async def run():
        hub = Hub(approval_timeout=0.5)
        hub.devices.add(object())
        session = hub._upsert(payload("SessionStart"))
        task = asyncio.create_task(
            hub.request_approval(session, {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}})
        )
        await asyncio.sleep(0.05)
        (approval_id,) = hub.approvals
        assert hub.decide(approval_id, "allow", via="voice") is False
        assert hub.decide(approval_id, "deny", via="voice") is True
        return await task

    assert asyncio.run(run()) == "deny"


@pytest.mark.parametrize("cmd,expected", [
    ("rm -rf build", True),
    ("git push --force origin main", True),
    ("git push -f", True),
    ("git reset --hard HEAD~1", True),
    ("npm test", False),
    ("git push origin feature", False),
    ("ls -la", False),
])
def test_is_dangerous(cmd, expected):
    assert is_dangerous("Bash", {"command": cmd}) is expected


def test_dictation_dry_run(client):
    client.post("/hook", json=payload("SessionStart"), headers=H)
    r = client.post("/api/dictate", json={"session_id": "s1", "text": "rode os testes"}, headers=H).json()
    assert r["ok"] and r["backend"] == "dry-run" and r["session"] == "portrait-api"
    assert client.post("/api/dictate", json={"session_id": "nope", "text": "x"}, headers=H).json()["ok"] is False
