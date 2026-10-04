"""Today's numbers, for the summary the Buddy says at "boa noite".

Counted as things happen (sessions, voice turns, approvals, longest working
stretch, pomodoros) and kept in a small JSON file so a hub restart doesn't lose
the day; commits are counted at summary time from the sessions' git repos.
"""

import json
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger("buddy.daystats")

STATS_FILE = Path.home() / ".local" / "share" / "claude-buddy" / "today.json"


def today() -> str:
    return time.strftime("%Y-%m-%d")


class DayStats:
    def __init__(self, path: Path | None = None):
        self.path = path or STATS_FILE
        self.data = self._fresh()
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if stored.get("date") == today():
                self.data.update(stored)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _fresh() -> dict:
        return {"date": today(), "sessions": [], "cwds": [], "voice_turns": 0, "approved": 0, "denied": 0,
                "longest": {"name": "", "seconds": 0}, "pomodoros": 0}

    def _roll(self) -> None:
        if self.data["date"] != today():
            self.data = self._fresh()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except OSError:
            log.warning("could not save %s", self.path)

    def session_used(self, name: str, cwd: str = "") -> None:
        self._roll()
        changed = False
        if name and name not in self.data["sessions"]:
            self.data["sessions"].append(name)
            changed = True
        if cwd and cwd not in self.data["cwds"]:
            self.data["cwds"].append(cwd)
            changed = True
        if changed:
            self._save()

    def count(self, key: str) -> None:
        self._roll()
        self.data[key] += 1
        self._save()

    def worked(self, name: str, seconds: float) -> None:
        self._roll()
        if seconds > self.data["longest"]["seconds"]:
            self.data["longest"] = {"name": name, "seconds": int(seconds)}
            self._save()

    def commits(self, extra_cwds=()) -> int:
        """Your commits today across the git repos of today's sessions (plus extra_cwds:
        the folders of every session the hub knows, even ones not prompted today)."""
        roots = set()
        for cwd in {*self.data["cwds"], *extra_cwds}:
            r = _git(cwd, "rev-parse", "--show-toplevel")
            if r:
                roots.add(r)
        total = 0
        for root in roots:
            email = _git(root, "config", "user.email")
            if not email:
                continue
            log_out = _git(root, "log", "--all", "--since=midnight", f"--author={email}", "--format=%H")
            total += len(set(log_out.split())) if log_out else 0
        return total

    def summary(self, commits: int | None = None, extra_cwds=()) -> str:
        self._roll()
        d = self.data
        n = len(d["sessions"])
        if commits is None:
            commits = self.commits(extra_cwds)
        parts = [f"Resumo do dia: {n} {'sessão' if n == 1 else 'sessões'} e {commits} "
                 f"{'commit' if commits == 1 else 'commits'}."]
        decided = d["approved"] + d["denied"]
        if decided:
            parts.append(f"Você aprovou {d['approved']} e negou {d['denied']} pedidos.")
        if d["voice_turns"]:
            parts.append(f"Conversamos por voz {d['voice_turns']} {'vez' if d['voice_turns'] == 1 else 'vezes'}.")
        if d["longest"]["seconds"] >= 60:
            parts.append(f"A tarefa mais longa foi em {d['longest']['name']}, com {d['longest']['seconds'] // 60} minutos.")
        if d["pomodoros"]:
            parts.append(f"{d['pomodoros']} {'pomodoro' if d['pomodoros'] == 1 else 'pomodoros'}.")
        parts.append("Boa noite!")
        return " ".join(parts)


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""
