#!/usr/bin/env python3
"""Draws the README's three diagrams as SVG, each in a light and a dark version.

    python3 docs/images/diagrams.py

The README shows each pair with a <picture> element, so GitHub picks the one that
matches the reader's theme. Change a drawing here, never in the SVG files: both
versions come from the same coordinates. Standard library only.
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"

# GitHub's own (Primer) colors, so the drawings sit naturally on its light and dark pages.
# Each node kind is (fill, stroke, title color, subtitle color).
THEMES = {
    "light": {
        "line": "#59636E", "label": "#1F2328", "muted": "#59636E",
        "agent": ("#DDF4FF", "#54AEFF", "#0A3069", "#0550AE"),
        "script": ("#FBEFFF", "#C297FF", "#3E1F79", "#6639BA"),
        "you": ("#FFF8C5", "#D4A72C", "#633C01", "#7D4E00"),
        "neutral": ("#F6F8FA", "#D0D7DE", "#1F2328", "#59636E"),
        "good": ("#DAFBE1", "#4AC26B", "#044F1E", "#116329"),
        "bad": ("#FFEBE9", "#FF8182", "#82071E", "#A40E26"),
        "chip": ("#FFFFFF", "#D0D7DE", "#1F2328", "#59636E"),
        "machine": "#D0D7DE", "container": "#0969DA",
    },
    "dark": {
        "line": "#9198A1", "label": "#E6EDF3", "muted": "#9198A1",
        "agent": ("#0D2747", "#388BFD", "#CAE8FF", "#A5D6FF"),
        "script": ("#2A1B47", "#A371F7", "#ECD8FF", "#D2A8FF"),
        "you": ("#3A2E09", "#D29922", "#F8E3A1", "#F2CC60"),
        "neutral": ("#161B22", "#3D444D", "#E6EDF3", "#9198A1"),
        "good": ("#11291B", "#3FB950", "#AFF5B4", "#7EE787"),
        "bad": ("#3D1418", "#F85149", "#FFDCD7", "#FFA198"),
        "chip": ("#0D1117", "#3D444D", "#E6EDF3", "#9198A1"),
        "machine": "#3D444D", "container": "#4493F8",
    },
}


class Drawing:
    def __init__(self, width, height, theme, label):
        self.t, self.w, self.h, self.label, self.parts = THEMES[theme], width, height, label, []

    def text(self, x, y, s, color, size=12, bold=False, anchor="middle", rotate=None):
        weight = ' font-weight="600"' if bold else ""
        turn = f' transform="rotate({rotate} {x} {y})"' if rotate else ""
        self.parts.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}"{weight} '
                          f'text-anchor="{anchor}"{turn}>{escape(s)}</text>')

    def box(self, x, y, w, h, kind, title, sub=None, rx=8, size=14):
        fill, stroke, title_color, sub_color = self.t[kind]
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        cx = x + w / 2
        if sub:
            self.text(cx, y + h / 2 - 3, title, title_color, size, bold=True)
            self.text(cx, y + h / 2 + 14, sub, sub_color, 12)
        else:
            self.text(cx, y + h / 2 + 5, title, title_color, size, bold=True)

    def chip(self, x, y, w, h, lines, stroke_kind="chip"):
        fill, _, color, _ = self.t["chip"]
        stroke = self.t[stroke_kind][1]
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>')
        top = y + h / 2 - (len(lines) - 1) * 8 + 4.5
        for i, line in enumerate(lines):
            self.text(x + w / 2, top + i * 16, line, color, 12.5)

    def frame(self, x, y, w, h, color, label, label_color=None, bold=False):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" '
                          f'stroke="{color}" stroke-width="1.5" stroke-dasharray="6 5"/>')
        self.text(x + 16, y + 22, label, label_color or color, 12.5, bold=bold, anchor="start")

    def arrow(self, points, label=None, at=None, anchor="middle", head=True):
        d = " ".join(f"{x},{y}" for x, y in points)
        end = ' marker-end="url(#head)"' if head else ""
        self.parts.append(f'<polyline points="{d}" fill="none" stroke="{self.t["line"]}" '
                          f'stroke-width="1.5" stroke-linejoin="round"{end}/>')
        if label:
            self.text(at[0], at[1], label, self.t["label"], 11.5, bold=True, anchor=anchor)

    def legend(self, x, y, items):
        for kind, text in items:
            fill, stroke, _, _ = self.t[kind]
            self.parts.append(f'<rect x="{x}" y="{y - 11}" width="14" height="14" rx="3" '
                              f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
            self.text(x + 21, y, text, self.t["muted"], 12.5, anchor="start")
            x += 30 + len(text) * 6.6

    def svg(self):
        head = (f'<marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
                f'markerHeight="8" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" '
                f'fill="{self.t["line"]}"/></marker>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img" aria-label="{escape(self.label)}" '
                f'font-family="{FONT}">\n<title>{escape(self.label)}</title>\n<defs>{head}</defs>\n'
                + "\n".join(self.parts) + "\n</svg>\n")


JOB_FLOW = ("How a job flows: an issue labeled factory goes to the intake agent, which accepts it, "
            "asks its author a question, or rejects it. Accepted work is routed, specified and built, "
            "then the gate pushes the branch, opens a draft PR and runs your checks: a failure sends it "
            "back to the builder, and dependency changes wait for you to allow them. Reviewers check the "
            "change and can send it back; the approver approves or escalates to you. By default you "
            "merge the PR; with auto_merge on and CI passing, the factory merges it.")


def job_flow(theme):
    d = Drawing(874, 556, theme, JOB_FLOW)
    bw = 122                                # box width; gaps are 56, wide enough for a label
    col = [20 + i * (bw + 56) for i in range(5)]
    mid = [x + bw / 2 for x in col]
    r0, r1, r2, r3, h = 28, 136, 308, 440, 52

    # The authors' and your decisions sit above and below the main path.
    d.box(col[1], r0, bw, h, "you", "Author", "answers")
    d.box(col[2], r0, bw, h, "bad", "Rejected", rx=26)
    # Main path, left to right, then back right to left.
    d.box(col[0], r1, bw, h, "neutral", "Issue", "labeled factory", rx=26)
    d.box(col[1], r1, bw, h, "agent", "Intake", "accept or ask")
    d.box(col[2], r1, bw, h, "script", "Route", "lite or full", rx=3)
    d.box(col[3], r1, bw, h, "agent", "Spec", "testable criteria")
    d.box(col[4], r1, bw, h, "agent", "Build", "code and commit")
    d.box(col[4], r2, bw, h, "script", "Gate", "push, PR, checks", rx=3)
    d.box(col[3], r2, bw, h, "agent", "Review", "code, security, UI")
    d.box(col[2], r2, bw, h, "agent", "Approve", "or escalate")
    d.box(col[1], r2, bw, h, "script", "Merge", "policy, sync, gate", rx=3)
    d.box(col[0], r2, bw, h, "you", "You", "merge the PR")
    d.box(col[4], r3, bw, h, "you", "You review", "job allow")
    d.box(col[2], r3, bw, h, "you", "You decide", "merge or feedback")
    d.box(col[0], r3, bw, h, "good", "Merged", rx=26)

    y1, y2 = r1 + h / 2, r2 + h / 2
    for a, b in zip(col[:4], col[1:]):
        d.arrow([(a + bw, y1), (b, y1)])
    d.text((col[1] + bw + col[2]) / 2, y1 - 8, "ACCEPT", d.t["label"], 11.5, bold=True)
    d.arrow([(mid[1] - 20, r1), (mid[1] - 20, r0 + h)], "NEEDS-INFO", (mid[1] - 27, 112), "end")
    d.arrow([(mid[1] + 20, r0 + h), (mid[1] + 20, r1)], "reply", (mid[1] + 27, 112), "start")
    d.arrow([(col[1] + bw - 8, r1), (col[2] + 12, r0 + h)], "REJECT", (col[2] + 6, 118), "start")

    d.arrow([(mid[4] - 20, r1 + h), (mid[4] - 20, r2)], "READY", (mid[4] - 14, 226), "start")
    d.arrow([(mid[4] + 24, r2), (mid[4] + 24, r1 + h)], "FAIL", (mid[4] + 31, 252), "start")
    d.arrow([(mid[3], r2), (mid[3], 264), (col[4] + 16, 264), (col[4] + 16, r1 + h)],
            "CHANGES", ((mid[3] + col[4] + 16) / 2, 257))
    for a, b, label in ((col[4], col[3], "PASS"), (col[3], col[2], "PASS"),
                        (col[2], col[1], "APPROVE"), (col[1], col[0], "default")):
        d.arrow([(a, y2), (b + bw, y2)], label, ((a + b + bw) / 2, y2 - 8))

    d.arrow([(mid[4] - 20, r2 + h), (mid[4] - 20, r3)], "HOLD", (mid[4] - 27, 404), "end")
    d.arrow([(mid[4] + 24, r3), (mid[4] + 24, r2 + h)], "allowed", (mid[4] + 31, 404), "start")
    d.arrow([(mid[2], r2 + h), (mid[2], r3)], "ESCALATE", (mid[2] + 7, 404), "start")
    d.arrow([(mid[0], r2 + h), (mid[0], r3)])
    d.arrow([(mid[1], r2 + h), (mid[1], r3 + h / 2), (col[0] + bw, r3 + h / 2)],
            "auto_merge + CI", (mid[1] + 7, 404), "start")

    d.legend(20, 536, [("agent", "Agent (Claude Code)"), ("script", "Script"),
                       ("you", "You, or the issue's author")])
    return d.svg()


ARCHITECTURE = ("How it's built: on your machine, inside the runtime container, your Claude Code session "
                "runs the orchestrator. It spawns the agents and runs the scripts. Every agent tool call "
                "passes the guard hook: each agent may write only its one file in factory/jobs, and the "
                "builder commits only in its job's worktree. The scripts are the only writer of job state "
                "and the only path to GitHub, for pushes, PRs and merges. The container holds no host "
                "credentials and can reach only GitHub and an allowlist.")


def architecture(theme):
    d = Drawing(900, 444, theme, ARCHITECTURE)
    d.frame(12, 12, 622, 420, d.t["machine"], "Your machine", d.t["muted"])
    d.frame(32, 46, 582, 370, d.t["container"],
            "Runtime container: no host credentials; egress to GitHub and an allowlist only", bold=True)

    d.box(60, 86, 200, 56, "you", "Orchestrator", "your Claude Code session")
    d.box(420, 86, 180, 56, "script", "Scripts", "job, gate, route, doctor", rx=3)
    d.box(720, 86, 160, 56, "neutral", "GitHub", "issues, PRs, CI")
    d.box(420, 196, 180, 56, "neutral", "factory/jobs", "job state, audit log")
    d.box(420, 272, 180, 56, "neutral", ".worktrees/", "one checkout per job")

    fill, stroke, title_color, _ = d.t["agent"]
    d.parts.append(f'<rect x="60" y="196" width="200" height="196" rx="8" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="1.5"/>')
    d.text(160, 218, "Agents", title_color, 14, bold=True)
    for i, name in enumerate(["intake", "spec-writer", "builder", "reviewers", "approver"]):
        d.chip(76, 228 + i * 30, 168, 24, [name], "agent")

    bad_fill, bad_stroke, bad_text, _ = d.t["bad"]
    d.parts.append(f'<rect x="300" y="196" width="44" height="196" rx="6" fill="{bad_fill}" '
                   f'stroke="{bad_stroke}" stroke-width="1.5"/>')
    d.text(327, 294, "guard hook: every tool call", bad_text, 12.5, bold=True, rotate=-90)

    d.arrow([(260, 114), (420, 114)], "runs", (340, 106))
    d.arrow([(600, 114), (720, 114)], "pushes, PRs", (642, 106), "start")
    d.arrow([(160, 142), (160, 196)], "spawns", (167, 174), "start")
    d.arrow([(510, 142), (510, 196)], "only writer", (517, 174), "start")
    d.arrow([(260, 224), (300, 224)], head=False)
    d.arrow([(344, 224), (420, 224)], "its one file", (382, 216))
    d.arrow([(260, 300), (300, 300)], head=False)
    d.arrow([(344, 300), (420, 300)], "commits", (382, 292))
    return d.svg()


LAYERS = ("The security layers: untrusted input (issue text, comments, branch code) passes three "
          "layers before a change merges. Before any agent acts, only labeled issues from trusted "
          "authors run, and issue text reaches agents as quoted data. While agents work, a guard hook "
          "checks every tool call, a seal stops every job if the factory changes, and the runtime "
          "container holds no host credentials. Before anything merges, the gate runs your checks and "
          "holds dependency changes, reviewers look, with a security review for sensitive paths, and "
          "merge policy in code means you merge by default. Throughout, an outbound filter screens "
          "text posted to GitHub, a hash-chained log records every event, and doctor checks the setup "
          "before each run.")


def layers(theme):
    d = Drawing(900, 520, theme, LAYERS)
    d.box(110, 16, 420, 44, "bad", "Untrusted input: issue text, comments, branch code", rx=22, size=13.5)
    bands = [
        (92, "Before any agent acts", [["Only labeled issues", "from trusted authors"],
                                       ["Issue text reaches agents", "as quoted data"]]),
        (212, "While agents work", [["Guard hook on", "every tool call"],
                                    ["Seal: a factory change", "stops every job"],
                                    ["Runtime container:", "no host credentials"]]),
        (340, "Before anything merges", [["Gate runs your checks;", "dependencies held"],
                                         ["Reviews, plus security", "for sensitive paths"],
                                         ["Merge policy in code;", "you merge by default"]]),
    ]
    fill, stroke, title, _ = d.t["neutral"]
    for top, name, chips in bands:
        height = 92 if len(chips) == 2 else 100
        d.parts.append(f'<rect x="40" y="{top}" width="560" height="{height}" rx="10" fill="{fill}" '
                       f'stroke="{stroke}" stroke-width="1.5"/>')
        d.text(56, top + 22, name, title, 13.5, bold=True, anchor="start")
        width = (528 - 10 * (len(chips) - 1)) / len(chips)
        for i, lines in enumerate(chips):
            d.chip(56 + i * (width + 10), top + 34, width, 44, lines)
    d.arrow([(320, 60), (320, 92)])
    d.arrow([(320, 184), (320, 212)])
    d.arrow([(320, 312), (320, 340)])
    d.arrow([(320, 440), (320, 468)])
    d.box(170, 468, 300, 40, "good", "A merged change", rx=20, size=13.5)

    d.parts.append(f'<rect x="630" y="92" width="250" height="348" rx="10" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="1.5"/>')
    d.text(646, 114, "On everything", title, 13.5, bold=True, anchor="start")
    for i, lines in enumerate([["Outbound filter on all", "text posted to GitHub"],
                               ["Hash-chained audit log", "of every event"],
                               ["doctor checks the setup", "before every run"]]):
        d.chip(646, 150 + i * 92, 218, 56, lines)
    return d.svg()


def main():
    for name, draw in (("job-flow", job_flow), ("architecture", architecture), ("security-layers", layers)):
        for theme in THEMES:
            suffix = "" if theme == "light" else "-dark"
            (OUT / f"{name}{suffix}.svg").write_text(draw(theme))
    print(f"wrote 6 diagrams to {OUT}")


if __name__ == "__main__":
    main()
