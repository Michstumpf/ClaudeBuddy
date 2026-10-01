"""Read the session registry Claude Code keeps in ~/.claude/sessions/<pid>.json.

Each running Claude Code process writes one file with its session id, cwd,
user-given name (/rename), busy/idle status and tmux pane. The hub runs on the
same machine, so it uses these files to:

- show sessions that have not fired a hook yet (e.g. started before the hooks
  were installed),
- use the name given with /rename instead of the folder name,
- fix states hooks never report (an interrupted turn fires no Stop hook),
- notice sessions whose process died without a SessionEnd.
"""

import json
import os
from pathlib import Path


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def read_local_sessions(directory: Path) -> list[dict]:
    """Registry entries of live Claude Code processes. Unreadable files are skipped."""
    entries = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("sessionId") and _alive(data.get("pid")):
            entries.append(data)
    return entries


def parse_tmux(value) -> tuple[str | None, str | None]:
    """'buddy-test:@1.%1' -> ('buddy-test', '%1')."""
    if not isinstance(value, str) or ":" not in value:
        return None, None
    name, _, target = value.partition(":")
    pane = target.rsplit(".", 1)[-1] if "." in target else None
    return name or None, pane if pane and pane.startswith("%") else None
