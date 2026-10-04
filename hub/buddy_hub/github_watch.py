"""GitHub: PRs waiting for your review and your PRs whose CI fails.

Uses the gh CLI already logged in on the hub's machine (no token of our own)
and one GraphQL query per poll. Only changes become notices: a new review
request, or one of your PRs turning red. The first poll is the baseline.
"""

import json
import logging
import shutil
import subprocess

log = logging.getLogger("buddy.github")

POLL_SECONDS = 300
FAILED = ("FAILURE", "ERROR")

QUERY = """
query {
  viewer { login pullRequests(first: 50, states: OPEN) { nodes { number title url repository { name }
    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } } } }
  search(query: "is:pr is:open review-requested:@me archived:false", type: ISSUE, first: 50) {
    issueCount nodes { ... on PullRequest { number title url repository { name } } } }
}
"""


def available() -> bool:
    return shutil.which("gh") is not None


def fetch(timeout: float = 30.0) -> dict:
    """{"reviews": {key: pr}, "failing": {key: pr}} where key is "repo#number"."""
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUERY}"], capture_output=True, text=True,
                         timeout=timeout, check=True)
    return parse(json.loads(out.stdout)["data"])


def parse(data: dict) -> dict:
    def pr(node: dict) -> dict:
        return {"repo": node["repository"]["name"], "number": node["number"], "title": node["title"]}

    reviews = {f"{n['repository']['name']}#{n['number']}": pr(n) for n in data["search"]["nodes"] if n}
    failing = {}
    for n in data["viewer"]["pullRequests"]["nodes"]:
        commits = n["commits"]["nodes"]
        state = ((commits[0]["commit"].get("statusCheckRollup") or {}).get("state")) if commits else None
        if state in FAILED:
            failing[f"{n['repository']['name']}#{n['number']}"] = pr(n)
    return {"reviews": reviews, "failing": failing}


class GitHubWatch:
    def __init__(self):
        self.last: dict | None = None

    def update(self, now: dict) -> list[str]:
        """Notices for what changed since the last poll (none on the first)."""
        before, self.last = self.last, now
        if before is None:
            return []
        notices = []
        for key in now["reviews"].keys() - before["reviews"].keys():
            p = now["reviews"][key]
            notices.append(f"PR {p['number']} do {p['repo']} pede sua revisão: {p['title']}")
        for key in now["failing"].keys() - before["failing"].keys():
            p = now["failing"][key]
            notices.append(f"O CI do seu PR {p['number']} ({p['repo']}) falhou: {p['title']}")
        return notices

    def counts(self) -> dict | None:
        if self.last is None:
            return None
        return {"reviews": len(self.last["reviews"]), "failing": len(self.last["failing"])}
