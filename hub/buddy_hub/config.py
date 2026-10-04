import logging
import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("buddy.config")

TOKEN_FILE = Path.home() / ".config" / "claude-buddy" / "token"
CONFIG_FILE = Path.home() / ".config" / "claude-buddy" / "config.toml"


def load_config_file(path: Path | None = None) -> dict:
    """The installation's config.toml (see hub/config.example.toml); {} if absent or broken."""
    path = path or Path(os.environ.get("BUDDY_CONFIG") or CONFIG_FILE).expanduser()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("ignoring %s: %s", path, exc)
        return {}


class _Layered:
    """Built-in default < config.toml < BUDDY_* env var, one setting at a time."""

    def __init__(self, file: dict):
        self.file = file

    def get(self, section: str, key: str, env: str | None, default, cast=str):
        value = self.file.get(section, {}).get(key, default)
        if env and os.environ.get(env) is not None:
            value = os.environ[env]
        try:
            return cast(value) if value is not None else None
        except (TypeError, ValueError):
            log.warning("bad value for [%s] %s: %r; using %r", section, key, value, default)
            return default


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
    # Claude Code's local session registry. None disables reading it (tests,
    # or a hub running on a different machine than the sessions).
    sessions_dir: Path | None = None
    sessions_poll: float = 3.0
    # A hook-only session stuck in 'working' this long without events goes idle.
    stale_working: float = 600.0
    # Speech-to-text (faster-whisper on CPU). Language None = auto-detect.
    stt_model: str = "small"
    stt_language: str | None = "pt"
    stt_beam_size: int = 1
    # GPU worker (buddy_hub.worker on the desktop), tried before the local CPU.
    stt_remote: str | None = None
    # Spoken replies to voice turns (Piper). speak_on: auto = on the Buddy /
    # simulator if one is connected, else the hub's speakers; local; devices; off.
    tts_voice: str = "pt_BR-faber-medium"
    speak_on: str = "auto"
    # Joke and weather loops (off in tests, which must not hit the network).
    background_extras: bool = False
    prefs_file: Path | None = None  # None = ~/.config/claude-buddy/prefs.json
    # [defaults] of config.toml: first-run choices of the settings screen.
    pref_defaults: dict = field(default_factory=dict)
    # [weather]: default city and the coordinates used when search is unavailable.
    weather_city: str = "Canoas"
    weather_lat: float = -29.92
    weather_lon: float = -51.18
    # [calendar]: secret iCal URL (or ~/.config/claude-buddy/calendar_url) and your
    # email, to skip meetings you declined.
    calendar_url: str = ""
    calendar_email: str = ""

    @classmethod
    def from_env(cls, config: dict | None = None) -> "Settings":
        c = _Layered(load_config_file() if config is None else config)
        return cls(
            token=load_or_create_token(),
            approval_timeout=c.get("hub", "approval_timeout", "BUDDY_APPROVAL_TIMEOUT", 20.0, float),
            dictation_backend=os.environ.get("BUDDY_DICTATION", "auto"),
            sessions_dir=_sessions_dir(),
            stale_working=c.get("hub", "stale_working", "BUDDY_STALE_WORKING", 600.0, float),
            stt_model=c.get("stt", "model", "BUDDY_STT_MODEL", "small"),
            stt_language=c.get("stt", "language", "BUDDY_STT_LANGUAGE", "pt") or None,
            stt_beam_size=c.get("stt", "beam_size", "BUDDY_STT_BEAM_SIZE", 1, int),
            stt_remote=c.get("stt", "remote", "BUDDY_STT_REMOTE", "") or None,
            tts_voice=c.get("tts", "voice", "BUDDY_TTS_VOICE", "pt_BR-faber-medium"),
            speak_on=c.get("hub", "speak_on", "BUDDY_SPEAK_ON", "auto"),
            background_extras=True,
            pref_defaults=dict(c.file.get("defaults", {})),
            weather_city=c.get("weather", "city", "BUDDY_WEATHER_PLACE", "Canoas"),
            weather_lat=c.get("weather", "latitude", "BUDDY_WEATHER_LAT", -29.92, float),
            weather_lon=c.get("weather", "longitude", "BUDDY_WEATHER_LON", -51.18, float),
            calendar_url=c.get("calendar", "ics_url", "BUDDY_CALENDAR_URL", ""),
            calendar_email=c.get("calendar", "email", "BUDDY_CALENDAR_EMAIL", ""),
        )


def _sessions_dir() -> Path | None:
    """BUDDY_SESSIONS_DIR overrides; set it to an empty string to disable."""
    value = os.environ.get("BUDDY_SESSIONS_DIR")
    if value == "":
        return None
    path = Path(value).expanduser() if value else Path.home() / ".claude" / "sessions"
    return path if path.is_dir() else None
