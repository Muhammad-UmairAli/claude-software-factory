#!/usr/bin/env python3
"""Build the agents' prompts, .claude/agents/*.md, from prompts/ (SE-2).

    python3 prompts/build.py           write the prompts
    python3 prompts/build.py --check   exit 1 if a prompt isn't what its source builds (CI runs this)

Each agent has a source in prompts/agents/: its prompt, with {{> name}} where a
block shared with other agents goes (prompts/shared/name.md) and {{var}} where a
value from AGENTS below goes. A rule fixed in a shared block is fixed in every
prompt that uses it. The stage-file format version comes from
factory/lib/stage_files.py, so the prompts and the check in `job advance` can't
drift apart. This project's facts never go in a prompt: the agents read them from
factory/project.md, so an upgrade can replace the prompts.

Edit the sources, not .claude/agents/, then run this. Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "prompts" / "agents"
SHARED = ROOT / "prompts" / "shared"
OUT = ROOT / ".claude" / "agents"
sys.path.append(str(ROOT / "factory" / "lib"))
import stage_files  # noqa: E402  (factory/lib: the stage-file format the prompts describe)

# Per agent: the stage file it writes and, for the agents a project tailors, the section of
# factory/project.md it reads and what that section holds.
AGENTS: dict[str, dict[str, str]] = {
    "intake": {"file": "intake.md", "facts": "What this project is for",
               "what": "what this project is for, so you can tell what belongs in it"},
    "spec-writer": {"file": "spec.md"},
    "builder": {"file": "build.md"},
    "reviewer-code": {"file": "review-code.md"},
    "reviewer-security": {"file": "review-security.md", "facts": "Security hot spots",
                          "what": "where this project does authentication and authorization, its data access "
                                  "rules, payment and webhook code, and where user input meets queries, HTML or "
                                  "the shell"},
    "reviewer-ux": {"file": "review-ux.md", "facts": "Users",
                    "what": "the kinds of users this app has and what they come to do"},
    "reviewer-ui": {"file": "review-ui.md", "facts": "Design system",
                    "what": "where this project's design tokens, theme and shared components live"},
    "approver": {"file": "decision.md", "facts": "Always escalate",
                 "what": "the changes a human must always see in this project; ESCALATE any change that "
                         "touches one, whatever else holds"},
}
INCLUDE = re.compile(r"\{\{> ([a-z-]+)\}\}")
VALUE = re.compile(r"\{\{([a-z]+)\}\}")


def render(agent: str) -> str:
    values = {"version": str(stage_files.VERSION), **AGENTS[agent]}
    text = (SOURCES / f"{agent}.md").read_text()
    for _ in range(3):  # a shared block may use another one
        text = INCLUDE.sub(lambda m: (SHARED / f"{m.group(1)}.md").read_text().removesuffix("\n"), text)

    def value(m: re.Match[str]) -> str:
        if m.group(1) not in values:
            raise SystemExit(f"prompts/agents/{agent}.md: no value for {{{{{m.group(1)}}}}}")
        return values[m.group(1)]

    text = VALUE.sub(value, text)
    if INCLUDE.search(text):
        raise SystemExit(f"prompts/agents/{agent}.md: includes nested too deep")
    return text


def main(argv: list[str]) -> int:
    check = argv == ["--check"]
    if argv and not check:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    sources = {p.stem for p in SOURCES.glob("*.md")}
    if sources != set(AGENTS):
        print(f"prompts/agents/ and AGENTS disagree: {sorted(sources ^ set(AGENTS))}", file=sys.stderr)
        return 1
    stale = []
    for agent in sorted(AGENTS):
        built, out = render(agent), OUT / f"{agent}.md"
        if check:
            if not out.exists() or out.read_text() != built:
                stale.append(str(out.relative_to(ROOT)))
        else:
            out.write_text(built)
    if stale:
        print("these prompts aren't what prompts/ builds; edit prompts/ and run python3 prompts/build.py:\n  "
              + "\n  ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
