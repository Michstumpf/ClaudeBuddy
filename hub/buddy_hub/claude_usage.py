"""This month's Claude Code usage, from the local transcripts (~/.claude/projects).

Each assistant message records its model and token usage. Summed at API list
prices this is an *estimate of the API-equivalent value*: on a subscription
plan (Claude Team, Max…) nothing is billed per token. Streaming writes the
same message more than once, so messages are counted once by id.

Files are re-read only when they change (size/mtime), since the folder grows
to hundreds of MB.
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("buddy.claude_usage")

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# USD per million tokens (input, output), API list prices.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0), "claude-mythos-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
DEFAULT_PRICE = (4.0, 20.0)
CACHE_READ = 0.1                       # x input price
CACHE_WRITE_5M, CACHE_WRITE_1H = 1.25, 2.0


def price_for(model: str) -> tuple[float, float]:
    for prefix, price in PRICES.items():  # tolerate dated ids ("claude-haiku-4-5-20251001")
        if model == prefix or model.startswith(prefix + "-"):
            return price
    return DEFAULT_PRICE


def message_cost(model: str, usage: dict, speed: str = "standard") -> float:
    p_in, p_out = price_for(model)
    if speed == "fast":
        p_in, p_out = p_in * 2, p_out * 2
    cache = usage.get("cache_creation") or {}
    write_1h = cache.get("ephemeral_1h_input_tokens")
    write_5m = cache.get("ephemeral_5m_input_tokens")
    if write_1h is None and write_5m is None:  # older records: no split
        write_5m, write_1h = usage.get("cache_creation_input_tokens", 0), 0
    tokens_in = (usage.get("input_tokens", 0)
                 + CACHE_READ * usage.get("cache_read_input_tokens", 0)
                 + CACHE_WRITE_5M * (write_5m or 0) + CACHE_WRITE_1H * (write_1h or 0))
    return (tokens_in * p_in + usage.get("output_tokens", 0) * p_out) / 1e6


def _file_totals(path: Path) -> dict[str, dict]:
    """{"YYYY-MM": {"usd", "messages", "output_tokens"}} for one transcript."""
    totals: dict[str, dict] = {}
    seen: set[str] = set()
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                msg = d.get("message")
                if d.get("type") != "assistant" or not isinstance(msg, dict) or not msg.get("usage"):
                    continue
                key = msg.get("id") or d.get("requestId") or d.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                month = (d.get("timestamp") or "")[:7]
                usage = msg["usage"]
                t = totals.setdefault(month, {"usd": 0.0, "messages": 0, "output_tokens": 0})
                t["usd"] += message_cost(msg.get("model") or "", usage, usage.get("speed") or "standard")
                t["messages"] += 1
                t["output_tokens"] += usage.get("output_tokens", 0)
    except OSError:
        pass
    return totals


class ClaudeUsage:
    def __init__(self, projects_dir: Path = PROJECTS_DIR):
        self.dir = projects_dir
        self._files: dict[Path, tuple[tuple[float, int], dict]] = {}

    def month(self, month: str | None = None) -> dict:
        month = month or time.strftime("%Y-%m")
        out = {"month": month, "usd": 0.0, "messages": 0, "output_tokens": 0, "estimate": True}
        if not self.dir.is_dir():
            return out
        current = set()
        for path in self.dir.rglob("*.jsonl"):
            current.add(path)
            try:
                st = path.stat()
            except OSError:
                continue
            stamp = (st.st_mtime, st.st_size)
            cached = self._files.get(path)
            if cached is None or cached[0] != stamp:
                self._files[path] = (stamp, _file_totals(path))
            t = self._files[path][1].get(month)
            if t:
                out["usd"] += t["usd"]
                out["messages"] += t["messages"]
                out["output_tokens"] += t["output_tokens"]
        for gone in set(self._files) - current:
            del self._files[gone]
        out["usd"] = round(out["usd"], 2)
        return out
