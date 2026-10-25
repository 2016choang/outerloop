"""Report-only check that numbers in an author's report were measured.

Nothing here records events or changes a gate or publish decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# A number not embedded in a word or identifier, not part of a "#123" reference.
_NUMBER = re.compile(r"(?<![#\w.])(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def measured_values(events: Iterable[dict]) -> list[float]:
    """Every measure event's baseline and candidate, in event order."""
    values: list[float] = []
    for event in events:
        data = event.get("data")
        if event.get("type") != "measure" or not isinstance(data, dict):
            continue
        for key in ("baseline", "candidate"):
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    return values


def unmatched_numbers(
    report: str,
    metric: str,
    measured: Iterable[float],
    experiment_results: Iterable[str],
) -> list[str]:
    """Numbers in the report that look like metric values but match no measurement.

    A line counts when it mentions the metric name (case-insensitive), or
    "baseline", or "candidate". On those lines every number is a candidate
    except integers that are clearly not values: below 10 (step counts, list
    indices), 1900-2099 (years), on a line mentioning a seed, followed by "%",
    or part of a "#123" reference. A candidate matches when, rounded to the
    decimals it was written with, it equals a measured value, the gap between
    two measured values (a reported improvement), or a number in an
    Experiments result cell; the sign is ignored. Returns unmatched
    candidates in first-seen order.
    """
    values = list(measured)
    known = values + [abs(a - b) for a in values for b in values]
    for cell in experiment_results:
        known.extend(float(m.group(1)) for m in _NUMBER.finditer(cell))
    metric_name = metric.strip().lower()
    unmatched: list[str] = []
    for line in report.splitlines():
        lower = line.lower()
        if not (
            "baseline" in lower or "candidate" in lower or (metric_name and metric_name in lower)
        ):
            continue
        for match in _NUMBER.finditer(line):
            token = match.group(1)
            if _ignored(line, match) or _matches(token, known):
                continue
            if token not in unmatched:
                unmatched.append(token)
    return unmatched


def _ignored(line: str, match: re.Match[str]) -> bool:
    """Whether a number is clearly not a reported metric value."""
    token = match.group(1)
    if line[match.end() :].lstrip(" ")[:1] == "%":
        return True
    if "." in token or "e" in token.lower():
        return False
    value = int(token)
    if value < 10 or 1900 <= value <= 2099:
        return True
    return "seed" in line.lower()


def _matches(token: str, known: Iterable[float]) -> bool:
    """Equality after rounding both sides to the report's decimals, ignoring sign."""
    token = token.lstrip("-")
    mantissa, _, exponent = token.lower().partition("e")
    decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
    if exponent:  # scientific notation: round to the mantissa's digits
        return any(float(f"{abs(value):.{decimals}e}") == float(token) for value in known)
    return any(f"{abs(value):.{decimals}f}" == token for value in known)
