"""How job commands report on jobs: times, the benchmarking row, and text made safe to show.

Nothing here reads state on its own: factory/bin/job passes the job and its folder.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

Job = dict[str, Any]

METRIC_FIELDS = ["job", "issue", "profile", "risk", "surfaces", "stage", "rounds", "gate_fails",
                 "review_changes", "feedback", "cost_usd", "orchestrator_usd", "minutes", "files", "insertions",
                 "deletions", "pr"]


def when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def minutes(job: Job, now: Optional[str] = None) -> float:
    """Minutes from the job's start to its end, or to now while it runs."""
    stamp: Optional[str] = job["ended"] or now
    end = when(stamp) if stamp else datetime.now(timezone.utc)
    return (end - when(job["started"])).total_seconds() / 60


def elapsed(job: Job) -> str:
    m = int(minutes(job))
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def first_line(path: Path) -> Optional[str]:
    return path.read_text().split("\n", 1)[0].strip() if path.exists() else None


def metrics_row(job: Job, folder: Path, profile: str, now: str) -> dict[str, Any]:
    """One benchmarking row: what the job cost, how long it took, how much rework it needed."""
    firsts = lambda pattern: [first_line(p) for p in folder.glob(pattern)]  # noqa: E731
    intake, size = job.get("intake") or {}, job.get("size") or {}
    return {
        "job": job["id"], "issue": job["issue"], "profile": profile,
        "risk": intake.get("risk") or "", "surfaces": " ".join(intake.get("surfaces") or []),
        "stage": job["stage"], "rounds": job["round"],
        "gate_fails": sum(1 for line in firsts("round-*/checks.md") if line == "GATE: FAIL"),
        "review_changes": sum(1 for line in firsts("round-*/review-*.md") if line == "VERDICT: CHANGES"),
        "feedback": len(list(folder.glob("feedback-*.md"))),
        "cost_usd": f"{job['cost_usd']:.2f}", "orchestrator_usd": f"{job.get('orchestrator_usd', 0.0):.2f}",
        "minutes": round(minutes(job, now), 1),
        "files": size.get("files", ""), "insertions": size.get("insertions", ""),
        "deletions": size.get("deletions", ""), "pr": job["pr_url"] or "",
    }


def printable(text: str) -> str:
    """Text without control characters or invisible Unicode (format characters such as tag
    characters, bidi overrides and zero-width spaces; private-use and unassigned code points;
    variation selectors). Files can't hide words from the person reading, or carry text to the
    orchestrator that nobody sees."""
    return "".join(c for c in text if c in "\n\t" or not (
        unicodedata.category(c) in {"Cc", "Cf", "Co", "Cn"}
        or "︀" <= c <= "️" or "\U000e0100" <= c <= "\U000e01ef"))
