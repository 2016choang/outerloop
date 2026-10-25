"""Report-only check that numbers in an author's report were measured.

Nothing here records events or changes a gate or publish decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# A number not embedded in a word or identifier, not part of a "#123" reference.
_NUMBER = re.compile(r"(?<![#\w.])(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")
# The numbers named as seeds: "seed 8271", "seed=8271", "seeds 3, 4 and 5".
_SEEDS = re.compile(r"\bseeds?\b\s*[=:]?\s*(\d+(?:\s*(?:,|and)\s*\d+)*)", re.IGNORECASE)


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
    "baseline", or "candidate". On those lines every number is checked except
    the two that cannot be a measured value: a seed ("seed 8271", "seeds 3,
    4") and a "#123" reference. A number matches when, rounded to the decimals
    it was written with and ignoring sign, it equals a measured value, the gap
    between two measured values, or a number in an Experiments result cell; a
    percentage matches the relative gap between two measured values. Returns
    unmatched numbers in first-seen order. Report-only, so a rare false flag
    is cheaper than hiding a claim.
    """
    values = list(measured)
    known = values + [abs(a - b) for a in values for b in values]
    percents = [abs(a - b) / abs(b) * 100 for a in values for b in values if b]
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
            if _is_seed(line, match):
                continue
            percent = line[match.end() :].lstrip(" ")[:1] == "%"
            if _matches(token, percents if percent else known):
                continue
            if token not in unmatched:
                unmatched.append(token)
    return unmatched


def _is_seed(line: str, match: re.Match[str]) -> bool:
    """A number in a "seed 8271" / "seeds 3, 4" / "seed=8271" phrase."""
    return any(s.start(1) <= match.start(1) < s.end(1) for s in _SEEDS.finditer(line))


def _matches(token: str, known: Iterable[float]) -> bool:
    """Equality after rounding both sides to the report's decimals, ignoring sign."""
    token = token.lstrip("-")
    mantissa, _, exponent = token.lower().partition("e")
    decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
    if exponent:  # scientific notation: round to the mantissa's digits
        return any(float(f"{abs(value):.{decimals}e}") == float(token) for value in known)
    return any(f"{abs(value):.{decimals}f}" == token for value in known)
