import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub import claude_usage  # noqa: E402
from buddy_hub.claude_usage import ClaudeUsage, message_cost  # noqa: E402
from buddy_hub.daystats import DayStats  # noqa: E402


def test_summary_and_persistence(tmp_path):
    s = DayStats(tmp_path / "t.json")
    s.session_used("HIPAA Compliance", "/x")
    s.session_used("DataHub", "/y")
    s.session_used("HIPAA Compliance", "/x")
    s.count("approved"), s.count("approved"), s.count("denied"), s.count("voice_turns"), s.count("pomodoros")
    s.worked("HIPAA Compliance", 42 * 60)
    s.worked("DataHub", 5 * 60)
    text = s.summary(commits=12)
    assert text.startswith("Resumo do dia: 2 sessões e 12 commits.")
    assert "aprovou 2 e negou 1" in text and "por voz 1 vez" in text
    assert "HIPAA Compliance, com 42 minutos" in text and "1 pomodoro." in text and text.endswith("Boa noite!")
    assert DayStats(tmp_path / "t.json").data["approved"] == 2  # survives a restart


def test_commits_counts_only_mine_today(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "me@example.com")
    git("config", "user.name", "Me")
    for i in range(3):
        (repo / f"f{i}").write_text(str(i))
        git("add", ".")
        git("commit", "-qm", f"c{i}")
    git("-c", "user.email=other@example.com", "commit", "-q", "--allow-empty", "-m", "theirs")
    s = DayStats(tmp_path / "t.json")
    s.session_used("repo", str(repo / "subdir-does-not-matter"))
    s.session_used("repo2", str(repo))
    assert s.commits() == 3


def test_message_cost_by_model_and_cache():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
             "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000, "ephemeral_5m_input_tokens": 0}}
    # Opus 5.5: 4 in + 20 out + 0.4 cache read + 8 (1h write = 2x input)
    assert abs(message_cost("claude-opus-5-5", usage) - 32.4) < 1e-6
    assert abs(message_cost("claude-haiku-4-5-20251001", {"input_tokens": 1_000_000}) - 1.0) < 1e-6


def test_usage_dedupes_streamed_messages(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    line = ('{"type":"assistant","timestamp":"2026-10-04T10:00:00Z","requestId":"r1",'
            '"message":{"id":"m1","model":"claude-haiku-4-5","usage":{"input_tokens":1000000,"output_tokens":0}}}\n')
    (proj / "s.jsonl").write_text(line * 3)  # the same message streamed three times
    month = ClaudeUsage(tmp_path).month("2026-10")
    assert month["messages"] == 1 and month["usd"] == 1.0
    assert ClaudeUsage(tmp_path).month("2026-09")["messages"] == 0
