import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub import stt  # noqa: E402
from buddy_hub.stt import RemoteFirst  # noqa: E402
from buddy_hub.worker import create_app  # noqa: E402

TOKEN = "test-token"
H = {"X-Buddy-Token": TOKEN}


class FakeTranscriber:
    model_name = "fake"
    device = "fake"

    def __init__(self, text="olá"):
        self.text, self.calls = text, 0

    def transcribe(self, audio):
        self.calls += 1
        return {"text": self.text, "audio_seconds": 1.0, "took_seconds": 0.1, "backend": self.device}


# ---- worker (runs on the desktop) ---------------------------------------------

def worker(gpu=None, **kw):
    return TestClient(create_app(TOKEN, FakeTranscriber("do worker"), gpu_probe=lambda: gpu, **kw))


def test_worker_requires_token():
    c = worker()
    assert c.get("/health").status_code == 401
    assert c.post("/transcribe", content=b"x", headers={"X-Buddy-Token": "nope"}).status_code == 401


def test_worker_transcribes():
    r = worker().post("/transcribe", content=b"RIFF....", headers=H)
    assert r.status_code == 200 and r.json()["text"] == "do worker"


def test_worker_health_idle_and_without_gpu_info():
    assert worker(gpu={"util": 5, "free_mb": 6000, "name": "RTX"}).get("/health", headers=H).json()["busy"] is False
    assert worker(gpu=None).get("/health", headers=H).json()["busy"] is False


@pytest.mark.parametrize("gpu,reason", [
    ({"util": 95, "free_mb": 6000, "name": "RTX"}, "95% busy"),
    ({"util": 10, "free_mb": 800, "name": "RTX"}, "800 MB"),
])
def test_worker_reports_busy_gpu(gpu, reason):
    body = worker(gpu=gpu).get("/health", headers=H).json()
    assert body["busy"] is True and reason in body["reason"]


# ---- hub side: GPU first, CPU fallback ----------------------------------------

def remote_first(monkeypatch, health=None, transcribe=None):
    local = FakeTranscriber("da cpu")
    rf = RemoteFirst("http://desktop:8766", TOKEN, local=local)

    def fake_request(path, data, timeout):
        outcome = health if path == "/health" else transcribe
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(rf, "_request", fake_request)
    monkeypatch.setattr(stt.Transcriber, "available", staticmethod(lambda: True))
    return rf, local


def test_uses_gpu_worker_when_ready(monkeypatch):
    rf, local = remote_first(monkeypatch, health={"ok": True, "busy": False},
                             transcribe={"text": "da gpu", "backend": "cuda"})
    result = rf.transcribe(b"audio")
    assert (result["text"], result["backend"], local.calls) == ("da gpu", "remote:cuda", 0)


@pytest.mark.parametrize("health,transcribe,why", [
    (ConnectionRefusedError(), None, "unreachable"),       # desktop off
    (TimeoutError(), None, "unreachable"),                 # desktop asleep / slow
    ({"ok": True, "busy": True, "reason": "gpu 95% busy"}, None, "busy"),  # gaming
    ({"ok": True, "busy": False}, OSError("reset"), "failed"),            # died mid-request
])
def test_falls_back_to_cpu(monkeypatch, health, transcribe, why):
    rf, local = remote_first(monkeypatch, health=health, transcribe=transcribe)
    result = rf.transcribe(b"audio")
    assert result["text"] == "da cpu" and local.calls == 1 and why in result["fallback_reason"]


def test_no_fallback_available_raises(monkeypatch):
    rf, _ = remote_first(monkeypatch, health=ConnectionRefusedError())
    monkeypatch.setattr(stt.Transcriber, "available", staticmethod(lambda: False))
    with pytest.raises(RuntimeError, match="not installed"):
        rf.transcribe(b"audio")
