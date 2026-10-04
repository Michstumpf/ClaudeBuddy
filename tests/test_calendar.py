import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub.calendar_watch import CalendarWatch, events  # noqa: E402

TZ = timezone(timedelta(hours=-3))
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:daily
SUMMARY:Daily
DTSTART;TZID=America/Sao_Paulo:20261001T091000
DTEND;TZID=America/Sao_Paulo:20261001T092500
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:holiday
SUMMARY:Feriado
DTSTART;VALUE=DATE:20261005
DTEND;VALUE=DATE:20261006
END:VEVENT
BEGIN:VEVENT
UID:declined
SUMMARY:Reunião chata
DTSTART;TZID=America/Sao_Paulo:20261005T100000
DTEND;TZID=America/Sao_Paulo:20261005T110000
ATTENDEE;PARTSTAT=DECLINED:mailto:michael.stumpf@portraitspa.com
END:VEVENT
BEGIN:VEVENT
UID:review
SUMMARY:Review de arquitetura
DTSTART;TZID=America/Sao_Paulo:20261005T140000
DTEND;TZID=America/Sao_Paulo:20261005T150000
ATTENDEE;PARTSTAT=ACCEPTED:mailto:michael.stumpf@portraitspa.com
END:VEVENT
END:VCALENDAR
""".encode()


def test_events_expand_recurrence_and_skip_all_day_and_declined():
    evs = events(ICS, NOW, 24, email="michael.stumpf@portraitspa.com")
    assert [e["title"] for e in evs] == ["Daily", "Review de arquitetura"]
    assert "Reunião chata" in [e["title"] for e in events(ICS, NOW, 24)]  # without the email it is kept


def test_reminder_once_and_meeting_state():
    w = CalendarWatch(lead_min=10)
    w.upcoming = events(ICS, NOW, 24, email="michael.stumpf@portraitspa.com")
    notices, public = w.tick(NOW)                       # 09:00, Daily at 09:10
    assert notices == ["Daily em 10 minutos."] and public["now"] is None
    assert public["next"]["title"] == "Daily" and public["next"]["starts_in"] == 600
    assert w.tick(NOW + timedelta(minutes=2))[0] == []  # announced once
    notices, public = w.tick(NOW + timedelta(minutes=15))
    assert public["now"] == "Daily" and public["next"]["title"] == "Review de arquitetura"
    assert w.tick(NOW + timedelta(minutes=30))[1]["now"] is None
