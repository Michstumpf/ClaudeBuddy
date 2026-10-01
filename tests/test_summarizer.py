import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub.state import Hub  # noqa: E402
from buddy_hub.summarizer import MODEL, Summarizer  # noqa: E402

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class FakeMessages:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def summarizer_with(outcome):
    s = Summarizer(api_key="sk-test")
    s._client = SimpleNamespace(messages=FakeMessages(outcome))
    return s


def reply(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


def test_returns_haiku_line_and_sends_question_and_answer():
    s = summarizer_with(reply("  Terminei: os testes passaram. Faço o push?  "))
    line = asyncio.run(s.summarize("roda os testes", "**Pronto.** 38 testes passaram.\n\nFaço o push?"))
    assert line == "Terminei: os testes passaram. Faço o push?"
    call = s._client.messages.calls[0]
    assert call["model"] == MODEL and call["max_tokens"] <= 300
    assert "roda os testes" in call["messages"][0]["content"] and "38 testes" in call["messages"][0]["content"]


def test_no_key_means_no_summary():
    assert Summarizer(api_key=None).enabled is False
    assert asyncio.run(Summarizer(api_key=None).summarize("q", "a")) is None


@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(request=REQUEST),
    anthropic.APIConnectionError(request=REQUEST),
    anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=REQUEST), body=None),
    anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=REQUEST), body=None),
    anthropic.InternalServerError("boom", response=httpx2.Response(500, request=REQUEST), body=None),
])
def test_api_failures_fall_back(error):
    assert asyncio.run(summarizer_with(error).summarize("q", "a")) is None


def test_refusal_or_empty_falls_back():
    assert asyncio.run(summarizer_with(reply("x", stop="refusal")).summarize("q", "a")) is None
    assert asyncio.run(summarizer_with(reply("   ")).summarize("q", "a")) is None


def test_long_answers_are_capped():
    s = summarizer_with(reply("ok"))
    asyncio.run(s.summarize("q", "x" * 50_000))
    assert len(s._client.messages.calls[0]["messages"][0]["content"]) < 7000


def test_voice_question_is_kept_for_the_summary_but_never_in_the_snapshot():
    hub = Hub()
    hub.note_voice_text("segredo da portrait")
    asyncio.run(hub.handle_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                                 "cwd": "/x/api", "prompt": "segredo da portrait"}))
    assert hub.voice_questions["s1"] == "segredo da portrait"
    assert "segredo" not in str(hub.snapshot())


def test_usage_is_logged_without_text_and_summed(monkeypatch, tmp_path):
    from buddy_hub import summarizer

    monkeypatch.setattr(summarizer, "USAGE_FILE", tmp_path / "usage.jsonl")
    resp = reply("Feito.")
    resp.usage = SimpleNamespace(input_tokens=1200, output_tokens=40,
                                 cache_read_input_tokens=0, cache_creation_input_tokens=0)
    s = summarizer_with(resp)
    for _ in range(3):
        asyncio.run(s.summarize("pergunta secreta", "resposta secreta"))
    log_text = (tmp_path / "usage.jsonl").read_text()
    assert "secreta" not in log_text and "Feito" not in log_text
    totals = summarizer.usage_summary()
    # 1200 * $1/M + 40 * $5/M = $0.0014 per call
    assert totals["month_calls"] == 3 and totals["month_usd"] == pytest.approx(0.0042)
    assert totals["month_input_tokens"] == 3600 and totals["month_output_tokens"] == 120
