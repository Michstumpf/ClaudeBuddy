import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub.work_watch import JiraWatch, SlackWatch, jira_parse, slack_parse  # noqa: E402


def jira(*issues):
    return jira_parse({"issues": [{"key": k, "fields": {"summary": s, "status": {"name": st}}} for k, s, st in issues]})


def test_jira_notices_new_tickets_and_status_changes():
    w = JiraWatch()
    assert w.update(jira(("NEHR-1", "Login", "To Do"))) == []  # baseline
    notices = w.update(jira(("NEHR-1", "Login", "In Review"), ("NEHR-2", "HIPAA audit log", "To Do")))
    assert notices == ["Novo ticket para você: NEHR-2, HIPAA audit log", "NEHR-1 mudou para In Review."]
    assert w.counts() == {"open": 2}


def test_slack_mentions_say_who_and_where_but_not_the_text():
    data = lambda *ms: {"messages": {"matches": [
        {"ts": ts, "username": u, "text": "segredo", "channel": {"name": c, "is_im": c == "dm"}} for ts, u, c in ms]}}
    w = SlackWatch()
    assert w.update(slack_parse(data(("1", "ana", "eng")))) == []
    notices = w.update(slack_parse(data(("3", "bia", "dm"), ("2", "caio", "eng"), ("1", "ana", "eng"))))
    assert notices == ["caio te mencionou no Slack (#eng).", "bia te mencionou no Slack (mensagem direta)."]
    assert all("segredo" not in n for n in notices)
