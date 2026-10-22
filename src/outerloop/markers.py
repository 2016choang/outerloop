"""Body markers and labels the kernel writes and later recognizes.

The kernel finds its own past comments, issues, and claims by an HTML-comment
marker (`<!-- outerloop:advisory-review -->`), and routes work by labels
(`outerloop:review`). Reads go through `has_marker` / `has_label`; writes and
documentation use `marker` / `label_name`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PREFIX = "outerloop"


def marker(kind: str) -> str:
    """The marker we write for `kind`, e.g. `<!-- outerloop:followup -->`."""
    return f"<!-- {PREFIX}:{kind} -->"


def has_marker(body: str, kind: str) -> bool:
    """Does `body` carry the `kind` marker?"""
    return marker(kind) in body


_ANY_MARKER = re.compile(rf"<!-- {PREFIX}:[a-z][a-z-]*(?: [^<>]*)? -->")


def has_outerloop_marker(body: str) -> bool:
    """Does `body` carry a whole `outerloop:` HTML-comment marker (a kind, and
    an argument for some kinds)? Personal mode uses this, not a specific kind,
    to tell a marked kernel post from the person's writing under the same
    login."""
    return bool(_ANY_MARKER.search(body))


def with_kernel_marker(body: str) -> str:
    """`body` with the generic kernel marker prepended, unless it already
    carries an `outerloop:` marker (a specific kind wins)."""
    if has_outerloop_marker(body):
        return body
    return f"{marker('kernel')}\n{body}"


def label_name(kind: str) -> str:
    """The label we apply and document for `kind`, e.g. `outerloop:review`."""
    return f"{PREFIX}:{kind}"


def is_label(name: str, kind: str) -> bool:
    """Is `name` the `kind` label? Case-insensitive, as GitHub label matching
    is."""
    return name.casefold() == label_name(kind).casefold()


def has_label(labels: Iterable[str], kind: str) -> bool:
    return any(is_label(name, kind) for name in labels)
