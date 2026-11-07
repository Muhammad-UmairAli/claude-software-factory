"""The stage files agents hand each other, and their format (SE-3).

Each stage writes a markdown file whose first lines the scripts read: intake's
KEY: value header, the builder's STATUS line, each reviewer's VERDICT line, the
approver's APPROVE or ESCALATE. `job advance` checks a file against its format
before a move that depends on it, so a prompt edit that changes a header stops
the job with a message instead of mis-routing it. The format is versioned with
the prompts: each agent's prompt names `stage-file format v<VERSION>` and shows
the values allowed here, and tests/test_lib.py holds the two together. Change
both, and VERSION, when the format changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

VERSION = 1
SURFACE_LIST = re.compile(r"none|[a-z][a-z-]*(?:, ?[a-z][a-z-]*)*")

# Per stage file: `header` maps each KEY to its allowed values (a frozenset) or a pattern; `first` is the
# set of allowed first lines; `title` asks for a `# title` first line. `sections` must always be there;
# `needs` maps a decision (the header's DECISION or the first line's last word) to more sections.
FORMATS: dict[str, dict[str, Any]] = {
    "intake.md": {
        "header": {"DECISION": frozenset({"ACCEPT", "NEEDS-INFO", "REJECT"}),
                   "RISK": frozenset({"low", "medium", "high"}),
                   "SURFACES": SURFACE_LIST,
                   "RESCOPED": frozenset({"yes", "no"})},
        "sections": ["Summary"],
        "needs": {"ACCEPT": ["Scope", "Acceptance criteria"], "NEEDS-INFO": ["Questions"]},
    },
    "spec.md": {"title": True, "sections": ["Summary"]},
    "build.md": {"first": frozenset({"STATUS: READY", "STATUS: BLOCKED"}), "sections": ["Summary"],
                 "needs": {"BLOCKED": ["Blocked"]}},
    "review": {"first": frozenset({"VERDICT: PASS", "VERDICT: CHANGES"}), "sections": ["Summary"],
               "needs": {"CHANGES": ["Findings"]}},
    "decision.md": {"first": frozenset({"APPROVE", "ESCALATE"}), "sections": ["Summary"]},
}


def kind(name: str) -> str:
    """The format a stage file follows: review-<lens>.md files share one."""
    return "review" if re.fullmatch(r"review-[a-z-]+\.md", name) else name


def first_line(path: Path) -> Optional[str]:
    return path.read_text().split("\n", 1)[0].strip() if path.exists() else None


def header_of(text: str) -> dict[str, str]:
    """KEY: value lines at the top of a file, up to the first blank line."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        key, _, value = line.partition(":")
        fields[key.strip().upper()] = value.strip()
    return fields


def header(path: Path) -> dict[str, str]:
    return header_of(path.read_text())


def section_of(text: str, heading: str) -> Optional[str]:
    """Body of a `## heading` section, or None."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def section(path: Path, heading: str) -> Optional[str]:
    return section_of(path.read_text(), heading) if path.exists() else None


def problems(name: str, text: str) -> list[str]:
    """What keeps a stage file from following its format, in plain words; [] when it does.
    Raises KeyError for a file that has no format."""
    fmt = FORMATS[kind(name)]
    found: list[str] = []
    lines = text.split("\n", 1)
    first = lines[0].strip()
    decision: Optional[str] = None
    if "header" in fmt:
        fields = header_of(text)
        for key, allowed in fmt["header"].items():
            value = fields.get(key)
            if value is None:
                found.append(f"{key} is missing from the header")
            elif isinstance(allowed, frozenset) and value not in allowed:
                found.append(f"{key} must be one of {', '.join(sorted(allowed))}, not {value!r}")
            elif isinstance(allowed, re.Pattern) and not allowed.fullmatch(value):
                found.append(f"{key} must be none or a comma-separated list of lowercase names, not {value!r}")
        decision = fields.get("DECISION")
    if "first" in fmt:
        if first not in fmt["first"]:
            found.append(f"the first line must be one of {', '.join(sorted(fmt['first']))}, not {first!r}")
        else:
            decision = first.split()[-1]
    if fmt.get("title") and not re.fullmatch(r"# \S.*", first):
        found.append(f"the first line must be a # title, not {first!r}")
    for heading in [*fmt.get("sections", []), *fmt.get("needs", {}).get(decision, [])]:
        if section_of(text, heading) is None:
            if heading in fmt.get("sections", []):
                found.append(f"a ## {heading} section is missing")
            else:
                article = "an" if str(decision)[:1] in "AEIOU" else "a"
                found.append(f"{article} {decision} needs a ## {heading} section")
    return found


def check(path: Path) -> list[str]:
    """problems() for a stage file on disk; a missing file is one problem."""
    if not path.exists():
        return [f"{path.name} doesn't exist"]
    return problems(path.name, path.read_text())
