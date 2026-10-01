"""Deliver dictated text into a Claude Code session as if typed by the user."""

import logging
import shutil
import subprocess

log = logging.getLogger("buddy.dictation")


class DryRunSender:
    name = "dry-run"

    def send(self, pane: str | None, text: str) -> tuple[bool, str]:
        log.info("[dry-run] -> %s: %s", pane or "?", text)
        return True, f"dry-run: would type into {pane or '?'}"


class TmuxSender:
    """Types literal text into a tmux pane, then presses Enter."""

    name = "tmux"

    def send(self, pane: str | None, text: str) -> tuple[bool, str]:
        if not pane:
            return False, "session is not running inside tmux"
        text = text.replace("\r", " ").replace("\n", " ").strip()
        if not text:
            return False, "empty text"
        try:
            # -l sends the text literally, so words like "Enter" or "C-c" are not keys.
            subprocess.run(["tmux", "send-keys", "-t", pane, "-l", text], check=True, timeout=5)
            subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True, timeout=5)
        except (subprocess.SubprocessError, OSError) as exc:
            return False, f"tmux failed: {exc}"
        return True, f"sent to {pane}"


def make_sender(backend: str):
    if backend == "tmux" or (backend == "auto" and shutil.which("tmux")):
        return TmuxSender()
    return DryRunSender()
