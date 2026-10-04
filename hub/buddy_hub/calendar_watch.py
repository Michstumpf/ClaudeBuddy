"""Google Calendar (or any calendar) through its secret iCal address.

Google Agenda → Configurações da agenda → "Endereço secreto no formato iCal".
The URL is a secret: keep it in ~/.config/claude-buddy/calendar_url (or
config.toml [calendar] ics_url). Event titles stay on the hub and the Buddy.

The hub announces a meeting a few minutes before it starts and is in focus
mode (no jokes, other notices held back) while it runs. All-day events and
events you declined are ignored.
"""

import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("buddy.calendar")

URL_FILE = Path.home() / ".config" / "claude-buddy" / "calendar_url"
POLL_SECONDS = 300
LEAD_MIN = 10


def load_url(configured: str = "") -> str:
    if configured:
        return configured
    return URL_FILE.read_text(encoding="utf-8").strip() if URL_FILE.exists() else ""


def fetch_ics(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "claude-buddy"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def events(ics: bytes, now: datetime, hours: int = 24, email: str = "") -> list[dict]:
    """Timed events overlapping [now, now + hours], sorted, without declined ones."""
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(ics)
    out = []
    for ev in recurring_ical_events.of(cal).between(now - timedelta(hours=12), now + timedelta(hours=hours)):
        start, end = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
        if not isinstance(start, datetime):
            continue  # all-day event (a plain date)
        if start.tzinfo is None:
            start = start.replace(tzinfo=now.tzinfo or timezone.utc)
        end = end if isinstance(end, datetime) else start + timedelta(minutes=30)
        if end.tzinfo is None:
            end = end.replace(tzinfo=start.tzinfo)
        if end <= now or str(ev.get("STATUS", "")).upper() == "CANCELLED" or _declined(ev, email):
            continue
        out.append({"title": str(ev.get("SUMMARY", "Reunião")).strip() or "Reunião", "start": start, "end": end,
                    "uid": f"{ev.get('UID', '')}@{start.isoformat()}"})
    return sorted(out, key=lambda e: e["start"])


def _declined(ev, email: str) -> bool:
    if not email:
        return False
    attendees = ev.get("ATTENDEE") or []
    if not isinstance(attendees, list):
        attendees = [attendees]
    for a in attendees:
        if str(a).lower().removeprefix("mailto:") == email.lower():
            return str(a.params.get("PARTSTAT", "")).upper() == "DECLINED"
    return False


class CalendarWatch:
    def __init__(self, lead_min: int = LEAD_MIN):
        self.lead = timedelta(minutes=lead_min)
        self.upcoming: list[dict] = []
        self.announced: set[str] = set()

    def tick(self, now: datetime) -> tuple[list[str], dict]:
        """(notices to send now, public state for the Buddy)."""
        notices = []
        current = next((e for e in self.upcoming if e["start"] <= now < e["end"]), None)
        nxt = next((e for e in self.upcoming if e["start"] > now), None)
        for e in self.upcoming:
            if e["uid"] in self.announced or not (now < e["start"] <= now + self.lead):
                continue
            self.announced.add(e["uid"])
            minutes = max(1, round((e["start"] - now).total_seconds() / 60))
            notices.append(f"{e['title']} em {minutes} {'minuto' if minutes == 1 else 'minutos'}.")
        public = {
            "now": current["title"] if current else None,
            "next": {"title": nxt["title"], "starts_in": int((nxt["start"] - now).total_seconds())} if nxt else None,
        }
        return notices, public
