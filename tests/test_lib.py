"""The shared modules in factory/lib (A23, SE-1 and SE-3): one config loader for every script, one
reading of a branch's diff for the gate and job, and the stage-file format that `job advance` checks
and the agents' prompts describe. Standard library only.

    python3 -m unittest discover -s tests -v
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_factory import ROOT, Sandbox
from test_transitions import Case, build, to_build

sys.path.append(str(ROOT / "factory" / "lib"))
import branch_diff  # noqa: E402
import factory_config  # noqa: E402
import stage_files  # noqa: E402

AGENTS = ROOT / ".claude" / "agents"


class ConfigTest(unittest.TestCase):
    def test_every_script_loads_config_the_same_way(self):
        for script in ("job", "gate", "route", "guard", "runtime", "doctor"):
            text = (ROOT / "factory" / "bin" / script).read_text()
            with self.subTest(script=script):
                self.assertIn("factory_config", text)
                self.assertNotRegex(text, r"json\.loads\(\(.*config\.json", "no private copy of the loader")

    def test_defaults_fill_what_an_older_config_leaves_out(self):
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text(json.dumps({"base_branch": "main", "labels": {"queue": "factory"}}))
        raw, cfg = factory_config.read(d), factory_config.load(d)
        self.assertNotIn("concurrency", raw, "doctor checks what is written, not the defaults")
        self.assertEqual((cfg["concurrency"], cfg["max_rounds"], cfg["labels"]["approved"]), (1, 3, "factory:approved"))
        self.assertEqual(cfg["ci"]["wait_minutes"], 30)
        self.assertEqual(cfg["audit"]["keep_days"], 365)

    def test_a_config_that_isnt_an_object_is_refused(self):
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("[]")
        with self.assertRaises(ValueError):
            factory_config.load(d)


class BranchDiffTest(unittest.TestCase):
    def test_renames_and_copies_name_both_paths(self):
        out = "M\tsrc/a.py\nR100\told/name.py\tnew/name.py\nC075\tbase.cfg\tcopy.cfg\nD\tgone.txt\n"
        self.assertEqual(list(branch_diff.name_status(out)), [
            ("M", "src/a.py"), ("R", "old/name.py"), ("R", "new/name.py"),
            ("C", "base.cfg"), ("C", "copy.cfg"), ("D", "gone.txt")])

    def test_numstat_counts_binary_files_as_files(self):
        self.assertEqual(branch_diff.numstat("3\t1\ta.py\n-\t-\tlogo.png\n10\t0\tb.py\n"), (3, 13, 1))
        self.assertEqual(branch_diff.numstat(""), (0, 0, 0))

    def test_patterns_match_across_folders(self):
        self.assertTrue(branch_diff.matches("factory/bin/job", ["factory/*"]))
        self.assertFalse(branch_diff.matches("src/factory.py", ["factory/*"]))
        self.assertFalse(branch_diff.matches("src/Auth/login.py", ["*auth*"]))
        self.assertTrue(branch_diff.matches_folded("src/Auth/login.py", ["*auth*"]))


INTAKE_OK = ("DECISION: ACCEPT\nRISK: medium\nSURFACES: ui, security\nRESCOPED: no\n\n## Summary\n- small\n\n"
             "## Scope\nAdd a greeting.\n\n## Acceptance criteria\n1. It greets.\n")


class StageFileTest(unittest.TestCase):
    def check(self, name, text):
        return stage_files.problems(name, text)

    def test_files_that_follow_the_format_pass(self):
        self.assertEqual(self.check("intake.md", INTAKE_OK), [])
        self.assertEqual(self.check("intake.md", "DECISION: NEEDS-INFO\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n"
                                                 "## Summary\n- unclear\n\n## Questions\n1. Which page?\n"), [])
        self.assertEqual(self.check("spec.md", "# Add a greeting\n\n## Summary\n- greeting\n"), [])
        self.assertEqual(self.check("build.md", "STATUS: READY\n\n# Build\n\n## Summary\n- done\n"), [])
        self.assertEqual(self.check("review-code.md", "VERDICT: CHANGES\n\n## Summary\n- no\n\n## Findings\n1. x\n"), [])
        self.assertEqual(self.check("decision.md", "APPROVE\n\n## Summary\n- good\n"), [])

    def test_a_header_that_drifts_is_named(self):
        cases = [
            ("intake.md", INTAKE_OK.replace("RESCOPED: no", "RESCOPED: maybe"), "RESCOPED must be one of no, yes"),
            ("intake.md", INTAKE_OK.replace("RISK: medium\n", ""), "RISK is missing"),
            ("intake.md", INTAKE_OK.replace("RISK: medium", "Risk level: medium"), "RISK is missing"),
            ("intake.md", INTAKE_OK.replace("SURFACES: ui, security", "SURFACES: UI and security"),
             "SURFACES must be none or a comma-separated list"),
            ("intake.md", INTAKE_OK.replace("## Acceptance criteria\n1. It greets.\n", ""),
             "an ACCEPT needs a ## Acceptance criteria section"),
            ("intake.md", INTAKE_OK.replace("DECISION: ACCEPT", "DECISION: NEEDS-INFO"),
             "a NEEDS-INFO needs a ## Questions section"),
            ("spec.md", "Add a greeting\n\n## Summary\n- x\n", "the first line must be a # title"),
            ("build.md", "Status: ready\n\n## Summary\n- x\n", "the first line must be one of"),
            ("review-ux.md", "VERDICT: PASS\n\nLooks fine.\n", "a ## Summary section is missing"),
            ("review-code.md", "VERDICT: CHANGES\n\n## Summary\n- x\n", "a CHANGES needs a ## Findings section"),
            ("decision.md", "Approve\n\n## Summary\n- x\n", "the first line must be one of"),
        ]
        for name, text, problem in cases:
            with self.subTest(name=name, problem=problem):
                found = self.check(name, text)
                self.assertTrue(any(problem in p for p in found), found)

    def test_only_known_stage_files_have_a_format(self):
        with self.assertRaises(KeyError):
            stage_files.problems("notes.md", "anything")


class PromptFormatTest(unittest.TestCase):
    """SE-3: the format is versioned with the prompts. Each prompt that writes a stage file names this
    format version and shows the header values the schema allows, so a prompt edit that changes a
    header fails here instead of mis-routing a job."""

    WRITERS = {"intake": "intake.md", "spec-writer": "spec.md", "builder": "build.md",
               "reviewer-code": "review-code.md", "reviewer-security": "review-security.md",
               "reviewer-ux": "review-ux.md", "reviewer-ui": "review-ui.md", "approver": "decision.md"}

    def test_the_prompts_are_what_their_sources_build(self):
        res = subprocess.run([sys.executable, str(ROOT / "prompts" / "build.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_each_prompt_names_the_format_version(self):
        for agent, name in self.WRITERS.items():
            with self.subTest(agent=agent):
                text = (AGENTS / f"{agent}.md").read_text()
                self.assertIn(f"stage-file format v{stage_files.VERSION}", text)

    def test_intakes_template_shows_exactly_the_values_the_schema_allows(self):
        intake = (AGENTS / "intake.md").read_text()
        for key, allowed in stage_files.FORMATS["intake.md"]["header"].items():
            if isinstance(allowed, frozenset):
                with self.subTest(key=key):
                    line = re.search(rf"^{key}: (.+)$", intake, re.M).group(1)
                    self.assertEqual({v.strip() for v in line.split("|")}, set(allowed))

    def test_each_prompt_names_every_first_line_the_schema_allows(self):
        for agent, name in self.WRITERS.items():
            for first in stage_files.FORMATS[stage_files.kind(name)].get("first", ()):
                with self.subTest(agent=agent, first=first):
                    self.assertIn(first, (AGENTS / f"{agent}.md").read_text())


class AdvanceChecksFormatTest(Case):
    def test_advance_refuses_a_stage_file_that_drifts_from_the_format(self):
        box = self.sandbox()
        jid = box.run("job", "create", "1").stdout.strip()
        box.write(f"factory/jobs/{jid}/intake.md", Sandbox.INTAKE.format(risk="medium").replace("RESCOPED: no",
                                                                                               "RESCOPED: maybe"))
        box.agent("intake")
        box.run("route", jid)
        refused = box.run("job", "advance", jid, "spec", ok=False)
        self.assertIn("intake.md doesn't follow stage-file format v1", refused.stderr)
        self.assertIn("RESCOPED must be one of no, yes", refused.stderr)
        self.assertEqual(box.job(jid)["stage"], "intake")

    def test_a_review_without_a_summary_is_refused(self):
        box = self.sandbox(checks="grep -qx hi src/greeting.txt\n")
        jid = to_build(box)
        build(box, jid, "hi\n")
        box.run("gate", jid)
        box.run("job", "advance", jid, "review")
        for r in box.job(jid)["agents"]["reviewers"]:
            box.write(f"factory/jobs/{jid}/round-1/review-{r}.md", "VERDICT: PASS\n\nLooks fine to me.\n")
            box.agent(f"reviewer-{r}")
        refused = box.run("job", "advance", jid, "approve", ok=False)
        self.assertIn("doesn't follow stage-file format v1", refused.stderr)
        self.assertIn("a ## Summary section is missing", refused.stderr)


if __name__ == "__main__":
    unittest.main()
