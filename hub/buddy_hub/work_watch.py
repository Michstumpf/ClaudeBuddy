"""Jira and Slack: notices for what changed. Both off until their credentials exist.

Jira: your open tickets (assignee = currentUser()). A ticket newly assigned to
you, or one of yours changing status, becomes a notice. Credentials:
[jira] site / email in config.toml, API token in ~/.config/claude-buddy/jira_token
(id.atlassian.com → Security → API tokens).

Slack: mentions of you (search.messages). The notice says who and where, never
the message text. Needs a user token (xoxp-…, scope search:read) in
~/.config/claude-buddy/slack_token; creating the Slack app may need the
workspace admin's approval.
"""

import base64
import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("buddy.work")

CONFIG_DIR = Path.home() / ".config" / "claude-buddy"
JIRA_POLL_SECONDS = 300
SLACK_POLL_SECONDS = 120


def _secret(name: str) -> str:
    path = CONFIG_DIR / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# ---- Jira -------------------------------------------------------------------------

def jira_fetch(site: str, email: str, token: str, timeout: float = 20.0) -> dict:
    """{"KEY-1": {"summary", "status"}} for your open tickets."""
    jql = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
    params = urllib.parse.urlencode({"jql": jql, "fields": "summary,status", "maxResults": 100})
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(f"https://{site}/rest/api/3/search/jql?{params}",
                                 headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return jira_parse(json.loads(resp.read()))


def jira_parse(data: dict) -> dict:
    return {i["key"]: {"summary": i["fields"]["summary"], "status": i["fields"]["status"]["name"]}
            for i in data.get("issues", [])}


class JiraWatch:
    def __init__(self):
        self.last: dict | None = None

    def update(self, now: dict) -> list[str]:
        before, self.last = self.last, now
        if before is None:
            return []
        notices = [f"Novo ticket para você: {k}, {now[k]['summary']}" for k in now.keys() - before.keys()]
        notices += [f"{k} mudou para {now[k]['status']}." for k in now.keys() & before.keys()
                    if now[k]["status"] != before[k]["status"]]
        return notices

    def counts(self) -> dict | None:
        return None if self.last is None else {"open": len(self.last)}


# ---- Slack ------------------------------------------------------------------------

def _slack(method: str, token: str, params: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    if not data.get("ok"):
        raise RuntimeError(f"slack {method}: {data.get('error')}")
    return data


def slack_fetch(token: str, user_id: str | None = None) -> tuple[str, list[dict]]:
    """(your user id, recent mentions as {"ts", "user", "channel"})."""
    if not user_id:
        user_id = _slack("auth.test", token, {})["user_id"]
    data = _slack("search.messages", token, {"query": f"<@{user_id}>", "sort": "timestamp", "count": 20})
    return user_id, slack_parse(data)


def slack_parse(data: dict) -> list[dict]:
    out = []
    for m in data.get("messages", {}).get("matches", []):
        channel = m.get("channel") or {}
        where = "mensagem direta" if channel.get("is_im") else f"#{channel.get('name', '?')}"
        out.append({"ts": m.get("ts", ""), "user": m.get("username") or m.get("user", "alguém"), "channel": where})
    return out


class SlackWatch:
    def __init__(self):
        self.seen: set[str] | None = None
        self.user_id: str | None = None

    def update(self, mentions: list[dict]) -> list[str]:
        ts = {m["ts"] for m in mentions}
        if self.seen is None:  # baseline
            self.seen = ts
            return []
        fresh = [m for m in mentions if m["ts"] not in self.seen]
        self.seen |= ts
        return [f"{m['user']} te mencionou no Slack ({m['channel']})." for m in reversed(fresh)]


def jira_credentials(site: str, email: str) -> tuple[str, str, str] | None:
    token = _secret("jira_token")
    return (site, email, token) if site and email and token else None


def slack_token() -> str:
    return _secret("slack_token")
