"""What a branch changed, read from git's diff output, and the path patterns config.json uses (SE-1).

The gate and job both decide from the same diff (protected, held, sensitive and
escalated paths, and the change's size), so they read it the same way. Patterns
are fnmatch patterns where `*` also matches `/`.
"""

from __future__ import annotations

import fnmatch
from typing import Iterable, Iterator


def name_status(output: str) -> Iterator[tuple[str, str]]:
    """(status letter, path) for each path in `git diff --name-status` output. A rename or copy
    yields both the old and the new path, so a pattern can't be dodged by moving a file."""
    for line in output.splitlines():
        if not line.strip():
            continue
        status, *paths = line.split("\t")
        for path in paths:
            yield status[:1], path


def numstat(output: str) -> tuple[int, int, int]:
    """(files, insertions, deletions) from `git diff --numstat` output. Binary files count as files."""
    files = adds = dels = 0
    for line in output.splitlines():
        if not line.strip():
            continue
        added, deleted, _ = line.split("\t", 2)
        files += 1
        if added != "-":
            adds, dels = adds + int(added), dels + int(deleted)
    return files, adds, dels


def matches(path: str, patterns: Iterable[str]) -> bool:
    """Whether path matches any pattern, case as written."""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def matches_folded(path: str, patterns: Iterable[str]) -> bool:
    """Whether path matches any pattern, ignoring case: for names like Auth/ or PAYMENTS.md."""
    return any(fnmatch.fnmatchcase(path.lower(), pattern.lower()) for pattern in patterns)
