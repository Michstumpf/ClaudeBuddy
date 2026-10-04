"""The Buddy's battery, as it reports it: low-battery alerts on the desktop."""

import logging
import shutil
import subprocess
import time

log = logging.getLogger("buddy.battery")

ALERT_LEVELS = (15, 5)  # warn once when dropping to each, unless charging
REARM_MARGIN = 5        # an alert re-arms once the level is this much above it


class BatteryWatch:
    def __init__(self, levels=ALERT_LEVELS):
        self.levels = levels
        self.armed = set(levels)
        self.last: dict | None = None

    def update(self, percent: int, charging: bool) -> str | None:
        """Record a report; returns an alert message the first time a level is crossed."""
        percent = max(0, min(100, int(percent)))
        self.last = {"percent": percent, "charging": bool(charging), "updated_at": time.time()}
        if charging:
            self.armed = set(self.levels)
            return None
        for level in self.levels:
            if percent > level + REARM_MARGIN:
                self.armed.add(level)
        due = [lvl for lvl in self.levels if percent <= lvl and lvl in self.armed]
        if not due:
            return None
        lowest = min(due)
        self.armed -= {lvl for lvl in self.levels if lvl >= lowest}  # don't also fire 15% after 5%
        return f"Buddy com {percent}% de bateria" + (": me coloca para carregar!" if lowest <= 5 else "")


def notify_desktop(message: str) -> None:
    """A desktop notification on the hub's machine (Ubuntu)."""
    log.info("battery alert: %s", message)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Claude Buddy", "-u", "critical" if "carregar" in message else "normal",
                        "🔋 Claude Buddy", message], check=False, timeout=5)
