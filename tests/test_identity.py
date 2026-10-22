"""Personal mode: the kernel shares the person's login, so the marker decides.

One rule (`forge.is_kernel_post`) tells the kernel's posts from the person's:
the login must be one of the kernel's, and in personal mode the body must also
carry an `outerloop:` marker. Alias logins are former dedicated identities and
keep the login check alone."""

from __future__ import annotations


def _item(login: str, body: str, association: str = "OWNER") -> dict:
    return {
        "user": {"login": login},
        "body": body,
        "author_association": association,
    }


def test_the_persons_issue_is_a_work_order_in_personal_mode(monkeypatch) -> None:
    from outerloop.intake import disqualification

    monkeypatch.setenv("OUTERLOOP_IDENTITY", "personal")
    monkeypatch.delenv("OUTERLOOP_BOT_ALIASES", raising=False)
    issue = {**_item("person", "please widen the search"), "number": 4, "title": "Wider search"}
    assert disqualification(issue, "person") == ""
    # the kernel's marked issue under the same login is still the kernel's
    marked = {**issue, "body": "<!-- outerloop:research-log -->\none line per run"}
    assert disqualification(marked, "person") == "the kernel's own issue"


def test_the_persons_pr_comment_reaches_the_inbox_in_personal_mode(monkeypatch) -> None:
    from outerloop.inbox import qualifying_comments

    monkeypatch.setenv("OUTERLOOP_IDENTITY", "personal")
    monkeypatch.delenv("OUTERLOOP_BOT_ALIASES", raising=False)
    kernel = {**_item("person", "<!-- outerloop:kernel -->\nrun report"), "id": 1}
    person = {**_item("person", "please retry that seed"), "id": 2}
    assert [c[2] for c in qualifying_comments([kernel, person], "person", 0)] == [
        "please retry that seed"
    ]


def test_a_marked_kernel_post_is_still_the_kernels(monkeypatch) -> None:
    from outerloop.forge import is_kernel_post

    monkeypatch.setenv("OUTERLOOP_IDENTITY", "personal")
    assert is_kernel_post(_item("person", "<!-- outerloop:kernel -->\nhi"), "person")
    # outside personal mode the login alone decides
    monkeypatch.delenv("OUTERLOOP_IDENTITY")
    assert is_kernel_post(_item("person", "unmarked, but a dedicated login"), "person")
    assert not is_kernel_post(_item("person", "unmarked"), "someone-else")


def test_an_alias_login_keeps_the_login_check_alone(monkeypatch) -> None:
    """Legacy fixture: an unmarked post under a former login predates markers."""
    from outerloop.forge import is_kernel_post

    monkeypatch.setenv("OUTERLOOP_IDENTITY", "personal")
    monkeypatch.setenv("OUTERLOOP_BOT_ALIASES", "old-bot")
    assert is_kernel_post(_item("old-bot", "an unmarked post from the old account"), "person")
    # a stranger cannot buy recognition by pasting the public marker
    assert not is_kernel_post(_item("stranger", "<!-- outerloop:kernel -->\nforged"), "person")
