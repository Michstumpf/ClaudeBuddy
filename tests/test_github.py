import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from buddy_hub.github_watch import GitHubWatch, parse  # noqa: E402


def data(reviews=(), mine=()):
    return {
        "viewer": {"login": "me", "pullRequests": {"nodes": [
            {"number": n, "title": t, "url": "", "repository": {"name": r},
             "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": st} if st else None}}]}}
            for r, n, t, st in mine]}},
        "search": {"issueCount": len(reviews), "nodes": [
            {"number": n, "title": t, "url": "", "repository": {"name": r}} for r, n, t in reviews]},
    }


def test_parse_keeps_only_failing_prs():
    d = parse(data(reviews=[("ehr-backend", 2980, "Fix")],
                   mine=[("ellamd-api", 3365, "Bump", "FAILURE"), ("docs", 7, "Typo", None), ("api", 8, "Ok", "SUCCESS")]))
    assert list(d["reviews"]) == ["ehr-backend#2980"]
    assert list(d["failing"]) == ["ellamd-api#3365"]


def test_only_changes_become_notices():
    w = GitHubWatch()
    assert w.update(parse(data(reviews=[("a", 1, "Old")], mine=[("b", 2, "Red", "FAILURE")]))) == []  # baseline
    assert w.counts() == {"reviews": 1, "failing": 1}
    notices = w.update(parse(data(reviews=[("a", 1, "Old"), ("c", 3, "New one")],
                                  mine=[("b", 2, "Red", "FAILURE"), ("d", 4, "Broke", "ERROR")])))
    assert notices == ["PR 3 do c pede sua revisão: New one", "O CI do seu PR 4 (d) falhou: Broke"]
    assert w.update(parse(data())) == []  # things going away are not notices
