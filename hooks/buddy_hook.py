#!/usr/bin/env python3
"""Claude Code hook -> Claude Buddy hub.

Stdlib only, so it runs on any machine with Python 3.8+. Reads the hook
payload from stdin, adds host/tmux info, POSTs it to the hub and, for
PermissionRequest, prints the Buddy's decision.

Never gets in the way: if the hub is down or slow, it exits 0 with no
output and Claude Code behaves exactly as without the hook.

Config (env):
  BUDDY_HUB    hub URL (default http://127.0.0.1:8765)
  BUDDY_TOKEN  token (default: ~/.config/claude-buddy/token)
"""

import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

HUB = os.environ.get("BUDDY_HUB", "http://127.0.0.1:8765").rstrip("/")
# Must be a bit longer than the hub's approval timeout, and shorter than the
# hook timeout configured in settings.json.
PERMISSION_HTTP_TIMEOUT = float(os.environ.get("BUDDY_PERMISSION_HTTP_TIMEOUT", "25"))
EVENT_HTTP_TIMEOUT = 2.0


def token() -> str:
    if os.environ.get("BUDDY_TOKEN"):
        return os.environ["BUDDY_TOKEN"]
    path = Path.home() / ".config" / "claude-buddy" / "token"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def tmux_info() -> dict:
    if not os.environ.get("TMUX"):
        return {}
    pane = os.environ.get("TMUX_PANE", "")
    info = {"tmux_pane": pane}
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#S"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            info["tmux_session"] = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    event = payload.get("hook_event_name", "")
    payload["buddy"] = {"host": socket.gethostname(), **tmux_info()}

    req = urllib.request.Request(
        f"{HUB}/hook",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Buddy-Token": token()},
        method="POST",
    )
    timeout = PERMISSION_HTTP_TIMEOUT if event == "PermissionRequest" else EVENT_HTTP_TIMEOUT
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read() or b"{}")
    except Exception:
        return 0  # hub offline: behave as if the hook did not exist

    if event == "PermissionRequest" and body.get("decision") in ("allow", "deny"):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": body["decision"]},
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
