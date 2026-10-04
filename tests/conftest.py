import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub import daystats, prefs, summarizer  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_anthropic_key(monkeypatch, tmp_path):
    """Tests must never call the real API with the key stored on this machine."""
    monkeypatch.delenv("BUDDY_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(summarizer, "KEY_FILE", tmp_path / "no-key")
    monkeypatch.setattr(summarizer, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(prefs, "PREFS_FILE", tmp_path / "prefs.json")
    monkeypatch.setattr(daystats, "STATS_FILE", tmp_path / "today.json")
