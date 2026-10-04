"""User preferences changed from the Buddy's settings screen, kept on disk."""

import json
import logging
from pathlib import Path

log = logging.getLogger("buddy.prefs")

PREFS_FILE = Path.home() / ".config" / "claude-buddy" / "prefs.json"

DEFAULTS = {
    "jokes": True,             # a joke in a speech bubble now and then
    "joke_voice": True,        # ...also spoken aloud
    "joke_interval_min": 45,   # minimum minutes between jokes
    "weather": True,           # outside temperature on the face screen
    "skin": "classic",         # classic: the whole mascot | eyes: only its eyes, for a mascot-shaped case
    "battery": True,           # battery indicator in the status bar (boards with a battery)
    "long_task_min": 15,       # notice when a session works this long (0 = off)
    "daily_summary": True,     # say the day's summary at "boa noite"
    "calendar": True,          # meeting reminders and focus mode during meetings (needs the iCal URL)
    "github": True,            # notices for PRs awaiting your review and your PRs with failing CI (gh CLI)
    "city": "Canoas",          # weather city as typed ("Porto Alegre", "São José, SC")
    # Where "city" resolved to (set by the hub after a successful search, not by
    # the user). None: use the built-in default (Canoas).
    "city_geo": None,
}
CITY_MAX_CHARS = 60
JOKE_INTERVALS = (15, 30, 45, 60, 120)
SKINS = ("classic", "eyes")
LONG_TASK_OPTIONS = (0, 10, 15, 30, 60)


class Prefs:
    def __init__(self, path: Path | None = None, defaults: dict | None = None):
        """defaults: the installation's first-run choices (config.toml), over DEFAULTS."""
        self.path = path or PREFS_FILE  # looked up at call time (tests patch it)
        self.values = dict(DEFAULTS)
        self._apply(defaults or {})
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            self.values.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.values[key]

    def update(self, changes: dict) -> bool:
        """Apply known keys with the right types and save; True if anything changed."""
        changed = self._apply(changes)
        if changed:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.values, indent=2), encoding="utf-8")
            except OSError:
                log.warning("could not save %s", self.path)
        return changed

    def _apply(self, changes: dict) -> bool:
        changed = False
        for key, value in (changes or {}).items():
            if key not in DEFAULTS:
                continue
            if key == "city_geo":
                if value is not None and not (isinstance(value, dict) and {"name", "lat", "lon"} <= value.keys()):
                    continue
            elif key == "skin":
                if value not in SKINS:
                    continue
            elif key == "city":
                value = " ".join(str(value or "").split())[:CITY_MAX_CHARS]
                if not value:
                    continue
            elif isinstance(DEFAULTS[key], bool):
                value = bool(value)
            elif key == "long_task_min":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
                if value not in LONG_TASK_OPTIONS:
                    continue
            elif key == "joke_interval_min":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
                if value not in JOKE_INTERVALS:
                    continue
            if self.values[key] != value:
                self.values[key] = value
                changed = True
        return changed
