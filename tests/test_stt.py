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
    ({"util": 10, "free_mb": 200, "name": "RTX"}, "200 MB"),
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


# ---- GPU voice (XTTS-v2 on the worker) with Piper fallback ---------------------

from buddy_hub.tts import RemoteFirstSpeaker  # noqa: E402


class FakeVoice:
    def __init__(self, tag=b"PIPER"):
        self.tag, self.calls = tag, 0

    def available(self):
        return True

    def synthesize(self, text):
        self.calls += 1
        return self.tag + text.encode()


def test_worker_speak_endpoint():
    c = TestClient(create_app(TOKEN, FakeTranscriber(), gpu_probe=lambda: None, speaker=FakeVoice(b"XTTS")))
    assert c.get("/health", headers=H).json()["tts"] is True
    r = c.post("/speak", json={"text": "olá"}, headers=H)
    assert r.status_code == 200 and r.content == "XTTSolá".encode()
    assert c.post("/speak", json={"text": " "}, headers=H).status_code == 400
    assert c.post("/speak", json={"text": "x"}).status_code == 401


def test_worker_without_voice():
    c = worker()
    assert c.get("/health", headers=H).json()["tts"] is False
    assert c.post("/speak", json={"text": "olá"}, headers=H).status_code == 503


def remote_speaker(monkeypatch, health, speak=b"XTTS"):
    local = FakeVoice()
    rs = RemoteFirstSpeaker("http://desktop:8766", TOKEN, local=local)

    def fake_get(path, timeout):
        if isinstance(health, Exception):
            raise health
        return health

    def fake_speak(text):
        if isinstance(speak, Exception):
            raise speak
        return speak

    monkeypatch.setattr(rs, "_get", fake_get)
    monkeypatch.setattr(rs, "_speak_remote", fake_speak)
    return rs, local


def test_speaks_on_gpu_when_ready(monkeypatch):
    rs, local = remote_speaker(monkeypatch, {"ok": True, "busy": False, "tts": True})
    assert rs.synthesize("oi") == b"XTTS" and local.calls == 0


@pytest.mark.parametrize("health,speak", [
    (ConnectionRefusedError(), b""),                          # desktop off
    ({"ok": True, "busy": False, "tts": False}, b""),         # worker without the voice
    ({"ok": True, "busy": True, "reason": "gpu 95% busy", "tts": True}, b""),  # gaming
    ({"ok": True, "busy": False, "tts": True}, TimeoutError()),  # voice too slow
])
def test_voice_falls_back_to_piper(monkeypatch, health, speak):
    rs, local = remote_speaker(monkeypatch, health, speak)
    assert rs.synthesize("oi") == b"PIPERoi" and local.calls == 1



def test_worker_still_serves_a_gpu_shared_with_a_game():
    # 67% busy and 758 MB free (a game running) used to push dictation to the CPU.
    body = worker(gpu={"util": 67, "free_mb": 758, "name": "RTX"}).get("/health", headers=H).json()
    assert body["busy"] is False
