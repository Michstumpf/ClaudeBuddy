import os
import secrets
from dataclasses import dataclass
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "claude-buddy" / "token"


def load_or_create_token() -> str:
    """BUDDY_TOKEN env wins; otherwise a token persisted in ~/.config/claude-buddy/token."""
    env = os.environ.get("BUDDY_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return token


@dataclass
class Settings:
    token: str
    # How long a PermissionRequest waits for a tap on the Buddy before falling
    # back to the normal Claude Code dialog (terminal / Remote Control).
    approval_timeout: float = 20.0
    # "auto" uses tmux when available, otherwise dry-run (just logs).
    dictation_backend: str = "auto"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            token=load_or_create_token(),
            approval_timeout=float(os.environ.get("BUDDY_APPROVAL_TIMEOUT", "20")),
            dictation_backend=os.environ.get("BUDDY_DICTATION", "auto"),
        )
