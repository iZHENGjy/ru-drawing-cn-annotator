"""Page selections like ``"3,7-9"`` (one parser for every command and stage)."""
from __future__ import annotations


def expand(spec: str | None) -> list[int]:
    """'3,7-9' -> [3, 7, 8, 9] in the given order (blank parts ignored)."""
    out: list[int] = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def select(spec: str | None, n: int) -> list[int]:
    """Pages of a document with n pages: all of them when ``spec`` is empty, else ``expand`` within 1..n."""
    if not spec:
        return list(range(1, n + 1))
    return [p for p in expand(spec) if 1 <= p <= n]
