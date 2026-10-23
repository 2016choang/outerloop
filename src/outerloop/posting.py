"""Shared GitHub posting helpers for the reviewer and verifier.

Round-numbered comments, inline reviews, and skip stubs — the machinery for
getting a judge's findings onto a PR thread. Backend-agnostic on purpose: no
model dependency, so any judge backend posts through here.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from outerloop.github import GitHubClient, GitHubError
from outerloop.harness import redact
from outerloop.markers import marker

log = logging.getLogger(__name__)

# Posting/transport failures an advisory role tolerates — logged, never fatal,
# because an advisory reviewer or verifier must not turn a target repo's CI red.
# Programming errors (AttributeError, KeyError, TypeError) deliberately
# propagate. Model-call errors are NOT here: they arise inside the agent
# session, which handles them itself — posting has nothing to do with the model.
EXPECTED_FAILURES = (
    GitHubError,
    ValueError,
    OSError,
    json.JSONDecodeError,
)


def read_emitted_envelope(path: Path, repo: str, number: int, noun: str) -> dict | None:
    """Decode and gate the untrusted envelope a session job emitted: the
    envelope when it names this PR and is postable, else None (with a log)."""
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("%s file unreadable (%s); nothing posted", noun, exc)
        return None
    if not isinstance(envelope, dict):
        log.warning("%s file is not an object; nothing posted", noun)
        return None
    if envelope.get("repo") != repo or envelope.get("number") != number:
        log.warning("%s envelope names a different PR; refused", noun)
        return None
    kind = envelope.get("kind")
    if kind == "skip-clean":
        # a clean skip (bot PR, opt-out) posts nothing by design; the
        # envelope exists so a MISSING artifact means a broken session
        log.info("session skipped cleanly (%s); nothing to post", envelope.get("detail", ""))
        return None
    if kind not in ("skip-stub", "findings"):
        log.warning("unknown envelope kind %r; nothing posted", kind)
        return None
    return envelope


def post_round(
    client: GitHubClient,
    repo: str,
    number: int,
    marker: str,
    body: str,
    pr_data: dict,
    reviewed_by: str = "",
) -> str:
    """Post one NEW comment per round — numbered, stamped with the reviewed
    head — so every round notifies and stays visible (edits do neither).
    Shared by the advisory reviewer and the verifier; each counts rounds by
    ITS OWN marker. Runs are only PR-open or an explicit label request, so
    volume is human-bounded.
    """
    stamp, round_label = _round_stamp(client, repo, number, marker, pr_data, reviewed_by)
    client.comment(repo, number, body.replace(marker, f"{marker}\n{stamp}", 1))
    return round_label


# every attribution a round stamp carries — one capture per "reviewer `...`"
# clause; the attribution itself is backtick-free (sanitized below)
_REVIEWER_ATTR = re.compile(r"reviewer `([^`]*)`")


def _reviewer_key(by: str) -> str:
    """The stable reviewer id behind an attribution: the summarizer posts
    as `summarizer:<backend> over lens+lens` while a single-reviewer round
    posts as plain `<backend>` — the same underlying reviewer, one count,
    so a label-triggered round never restarts at Round 1."""
    if by.startswith("summarizer:"):
        return by.removeprefix("summarizer:").split(" over ", 1)[0]
    return by


def sanitize_attribution(by: str) -> str:
    """Make an attribution safe to render in a comment: it is render-side
    data that can cross a job boundary in the least-token split, so strip
    backticks/newlines, cap, never trust. The cap fits a panel line
    ("summarizer:<backend> over lens+lens+..."); a real overflow ends in an
    ellipsis so it never reads as a mid-word bug."""
    by = " ".join(str(by).split()).replace("`", "")
    if len(by) > 120:
        by = by[:119].rstrip() + "…"
    return by


def _round_stamp(
    client: GitHubClient,
    repo: str,
    number: int,
    marker: str,
    pr_data: dict,
    reviewed_by: str = "",
) -> tuple[str, str]:
    """(stamp line, round label): prior rounds are counted across BOTH
    issue comments and review bodies, so switching a role between posting
    styles never resets its numbering."""
    head = pr_data.get("head")
    head_sha = str(head.get("sha", ""))[:8] if isinstance(head, dict) else ""
    by = sanitize_attribution(reviewed_by)
    # The round number is cosmetic: an EXPECTED failure counting prior
    # rounds must never cost the round itself. Programming errors still
    # propagate, per this module's policy.
    try:
        bodies = [str(c.get("body", "")) for c in client.list_comments(repo, number)]
        bodies += [str(r.get("body", "")) for r in client.list_pr_reviews(repo, number)]
        # STARTS WITH the marker: a quote-reply prefixes every line with
        # "> ", so it cannot match — and this stays true for any posting
        # identity (Actions token, GitHub App, or a self-hoster's
        # machine-user PAT, which posts as type User)
        prior = [b for b in bodies if b.lstrip().startswith(marker)]
        # Rounds count PER REVIEWER: with several standing opinions on one
        # PR, a shared counter reads as re-reviews that never happened
        # (terra "Round 1", claude "Round 2"). The key strips the summarizer
        # decoration, so the summarizer path and the single-reviewer path
        # for the SAME reviewer share one count; unattributed rounds keep
        # the shared count.
        if by:
            key = _reviewer_key(by)
            prior = [
                b for b in prior if key in {_reviewer_key(a) for a in _REVIEWER_ATTR.findall(b)}
            ]
        round_label = f"**Round {len(prior) + 1}**"
        if head_sha and any(f"reviewed head `{head_sha}`" in b for b in prior):
            round_label += " (re-run on the same head)"
    except EXPECTED_FAILURES as exc:
        log.warning("could not count prior rounds: %s", exc)
        round_label = "**New round** (prior count unavailable)"
    by_clause = f" — reviewer `{by}`" if by else ""
    return f"{round_label} — reviewed head `{head_sha or 'unknown'}`{by_clause}.\n\n", round_label


def post_round_review(
    client: GitHubClient,
    repo: str,
    number: int,
    marker: str,
    body: str,
    inline: list[dict],
    pr_data: dict,
    fallback_body: str,
    reviewed_by: str = "",
    commit_id: str = "",
) -> str:
    """The Reviews-API sibling of post_round: body summary plus anchored
    inline comments, event COMMENT always (the client hard-codes it). A
    posting failure falls back to a plain issue comment carrying
    fallback_body — the FULL single-comment rendering, because the review
    body alone may say no more than "findings are attached" while the
    findings live in the rejected inline payload. `commit_id` pins the
    review to the head that was reviewed, so a push meanwhile cannot move it."""
    stamp, round_label = _round_stamp(client, repo, number, marker, pr_data, reviewed_by)
    try:
        client.create_pr_review(
            repo,
            number,
            body.replace(marker, f"{marker}\n{stamp}", 1),
            inline,
            commit_id=commit_id,
        )
    except EXPECTED_FAILURES as exc:
        if commit_id:
            # a push can be why the pinned review was refused; an unpinned
            # comment would land the old findings on the new head
            head = client.get_pull_request(repo, number).get("head")
            current = str(head.get("sha", "")) if isinstance(head, dict) else ""
            if current != commit_id:
                log.warning(
                    "pinned review failed (%s) and the head moved (%s -> %s); nothing posted",
                    exc,
                    commit_id[:8],
                    current[:8] or "unknown",
                )
                return ""
        log.warning("inline review failed (%s); falling back to a comment", exc)
        client.comment(repo, number, fallback_body.replace(marker, f"{marker}\n{stamp}", 1))
    return round_label


SKIP_MARKER = marker("round-skipped")
# the model needs a newer harness than the reviewer release pins
MODEL_TOO_NEW = re.compile(r"does not support this model|or newer is required", re.IGNORECASE)
# the runner's harness install is missing or does not match the pins
HARNESS_STALE = re.compile(r"run outerloop harness upgrade", re.IGNORECASE)


def post_skip_stub(
    client: GitHubClient,
    repo: str,
    number: int,
    role: str,
    exc: Exception,
    secrets: tuple[str, ...] = (),
) -> None:
    """Silence is invisible: when the model API refuses a round (dead
    credits, spend cap, auth), say so on the thread instead of leaving a
    gap only the Actions tab can see. A DIFFERENT marker than a real
    round, deliberately — a stub never counts toward round numbering and
    never rides as follow-up wake context (both match on their own
    markers).

    `secrets` are the model API key(s) the caller holds — an auth error is
    exactly the class that can echo request material, so we scrub them from the
    posted text. The caller supplies them (the harness owns its own key) so
    posting stays backend-agnostic — the key env var is provider-specific, this
    module is not.
    """
    full = redact(str(exc), tuple(s for s in secrets if s))
    note = full[:300]
    if MODEL_TOO_NEW.search(full):
        body = (
            f"{SKIP_MARKER}\n*The {role} round could not run — the pinned agent "
            f"harness is too old for the requested model ({type(exc).__name__}: "
            f"{note}). This is not an outage: point the workflow at a newer "
            f"reviewer release (the `uses:` ref and `reviewer_ref`), then re-add "
            f"the review label.*"
        )
    elif HARNESS_STALE.search(full):
        body = (
            f"{SKIP_MARKER}\n*The {role} round could not run — the agent harness "
            f"on this runner is missing or does not match the reviewer's pins "
            f"({type(exc).__name__}: {note}). This is not an outage: run the "
            f"named `outerloop harness upgrade` where the reviewer runs; in a "
            f"workflow, keep the `uses:` ref and `reviewer_ref` on the same "
            f"release. Then re-add the review label.*"
        )
    else:
        body = (
            f"{SKIP_MARKER}\n*The {role} round could not run — the model API "
            f"refused the request ({type(exc).__name__}: {note}). Treat this "
            f"as an outage, not a clean read; re-add the review label to "
            f"re-request once the API recovers.*"
        )
    try:
        client.comment(repo, number, body)
    except EXPECTED_FAILURES as post_exc:
        log.warning("could not post the skip stub: %s", post_exc)
