"""The job state machine: every move the factory allows and every one it refuses.

test_factory.PipelineTest drives the straight path from intake to merge. These tests drive the
loops and limits around it: a failed gate or review changes send the job back to build, a
question waits for the author's reply, a human's feedback or rejection, merges that fail after
approval, the concurrency limit, and the round and budget limits. Standard library only.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import unittest
import unittest.mock
from pathlib import Path

from test_factory import Sandbox, load_script

STAGES = ["intake", "needs-info", "spec", "build", "gate", "review", "approve", "merging",
          "needs-human", "merged", "rejected"]

# The moves `job advance` allows. Any unfinished job may also move to merged once its PR is
# merged on GitHub, and to rejected when `job cancel` stops it. A new move needs tests here.
LEGAL = {
    "intake": {"needs-info", "rejected", "spec"},
    "needs-info": {"intake", "rejected"},
    "spec": {"build"},
    "build": {"gate", "needs-human"},
    "gate": {"review", "build", "needs-human"},
    "review": {"approve", "build", "needs-human"},
    "approve": {"merging", "needs-human"},
    "merging": {"needs-human"},
    "needs-human": {"build", "gate", "rejected"},
}

NEEDS_INFO = ("DECISION: NEEDS-INFO\nRISK: medium\nSURFACES: none\nRESCOPED: no\n\n"
              "## Summary\n- the issue doesn't say where\n\n## Why\nIt names no page.\n\n"
              "## Questions\n1. Which page should show the greeting?\n")


def to_build(box, risk="medium"):
    """Drive issue #1 from create to its first build round (low risk runs lite). Returns the job id."""
    jid = box.run("job", "create", "1").stdout.strip()
    box.write(f"factory/jobs/{jid}/intake.md", Sandbox.INTAKE.format(risk=risk))
    box.agent("intake")
    box.run("route", jid)
    box.run("job", "advance", jid, "spec")
    box.write(f"factory/jobs/{jid}/spec.md", "# Add a greeting\n\n## Summary\n- greeting\n")
    box.agent("spec-writer")
    box.run("job", "advance", jid, "build")
    return jid


def build(box, jid, greeting, extra=None):
    """One builder round: set the greeting (and any extra files), commit, write build.md, then move to
    the gate, which pushes the branch."""
    wt = Path(box.job(jid)["worktree"])
    (wt / "src" / "greeting.txt").write_text(greeting)
    for rel, text in (extra or {}).items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(text)
    box.git(wt, "add", "-A")
    box.git(wt, "commit", "-qm", "Set the greeting")
    box.write(f"factory/jobs/{jid}/build.md", "STATUS: READY\n\n# Build\n\n## Summary\n- set the greeting\n")
    box.agent("builder")
    box.run("job", "advance", jid, "gate")


def ask_author(box, jid):
    """Intake asks the author a question: the job moves to needs-info."""
    box.write(f"factory/jobs/{jid}/intake.md", NEEDS_INFO)
    box.agent("intake")
    box.run("job", "advance", jid, "needs-info")


def push_to_base(box, rel, text):
    """Someone else's change lands on main while the job is open."""
    box.write(rel, text)
    box.git(box.repo, "add", rel)
    box.git(box.repo, "commit", "-qm", f"Change {rel}")
    box.git(box.repo, "push", "-q", "origin", "main")


class Case(unittest.TestCase):
    def sandbox(self, **kwargs):
        box = Sandbox(**kwargs)
        self.addCleanup(box.cleanup)
        return box

    @staticmethod
    def folder(box, jid):
        return box.repo / "factory" / "jobs" / jid

    @staticmethod
    def merges(box):
        return [c for c in box.state()["calls"] if c[:2] == ["pr", "merge"]]


class MoveTableTest(Case):
    """The table of moves, and a refusal for every move outside it."""

    def test_the_transition_table_is_pinned(self):
        job = load_script(self.sandbox().repo / "factory" / "bin" / "job", "factory_job_table")
        self.assertEqual(job.TRANSITIONS, LEGAL)
        self.assertEqual(job.TERMINAL, {"merged", "rejected"})

    def test_every_move_outside_the_table_is_refused(self):
        box = self.sandbox()
        job = load_script(box.repo / "factory" / "bin" / "job", "factory_job_moves")
        # The table is checked before anything else, so these calls touch no files or GitHub. If a
        # broken table lets a move run further, any gh call it makes still reaches only the fake.
        fake = {"PATH": box.env["PATH"], "FAKE_GH_STATE": box.env["FAKE_GH_STATE"]}
        with unittest.mock.patch.dict(os.environ, fake):
            for old in STAGES:
                for new in STAGES:
                    if new in LEGAL.get(old, ()) or (new == "merged" and old in LEGAL):
                        continue
                    with self.subTest(move=f"{old} -> {new}"):
                        with self.assertRaises(job.Refused) as caught:
                            job._advance({"id": "001-x", "stage": old}, new)
                        self.assertIn("is not a legal transition", str(caught.exception))

    def test_a_refused_move_changes_nothing_and_is_logged(self):
        box = self.sandbox()
        jid = box.run("job", "create", "1").stdout.strip()
        res = box.run("job", "advance", jid, "merging", ok=False)
        self.assertEqual(res.returncode, 1)
        self.assertIn("intake → merging is not a legal transition", res.stderr)
        self.assertEqual(box.job(jid)["stage"], "intake")
        last = json.loads((self.folder(box, jid) / "events.jsonl").read_text().splitlines()[-1])
        self.assertEqual((last["event"], last["stage"], last["to"]), ("refused", "intake", "merging"))


class LoopTest(Case):
    """The ways a job goes back a step or waits, and how it comes out again."""

    def test_a_failed_gate_sends_the_job_back_to_build(self):
        box = self.sandbox(checks="grep -qx hi src/greeting.txt\n")
        jid = to_build(box)
        build(box, jid, "hello\n")
        self.assertEqual(box.run("gate", jid, ok=False).returncode, 1)
        self.assertIn("the gate didn't pass", box.run("job", "advance", jid, "review", ok=False).stderr)
        box.run("job", "advance", jid, "build")
        self.assertEqual(box.job(jid)["round"], 2)
        build(box, jid, "hi\n")
        box.run("gate", jid)
        box.run("job", "advance", jid, "review")
        d = self.folder(box, jid)
        self.assertTrue((d / "round-1/checks.md").read_text().startswith("GATE: FAIL"))
        self.assertTrue((d / "round-2/checks.md").read_text().startswith("GATE: PASS"))
        self.assertEqual(box.job(jid)["stage"], "review")

    def test_review_changes_send_the_job_back_to_build(self):
        box = self.sandbox()
        jid = to_build(box)
        build(box, jid, "hi\n")
        box.run("gate", jid)
        box.run("job", "advance", jid, "review")
        box.write(f"factory/jobs/{jid}/round-1/review-code.md",
                  "VERDICT: CHANGES\n\n## Summary\n- wrong word\n\n## Findings\n1. src/greeting.txt:1 say hello\n")
        box.agent("reviewer-code")
        self.assertIn("reviews want CHANGES: code", box.run("job", "advance", jid, "approve", ok=False).stderr)
        box.run("job", "advance", jid, "build")
        job = box.job(jid)
        self.assertEqual((job["stage"], job["round"], job["reviews"]), ("build", 2, {}))

    def test_a_question_waits_for_the_authors_reply(self):
        box = self.sandbox()
        jid = box.run("job", "create", "1").stdout.strip()
        ask_author(box, jid)
        issue = box.state()["issues"]["1"]
        self.assertIn({"name": "factory:needs-info"}, issue["labels"])
        self.assertIn("Which page should show the greeting?", issue["comments"][-1]["body"])
        self.assertIn(f"wait\t1\t{jid}\tneeds-info\twaiting for the author", box.run("job", "queue").stdout)

        state = box.state()  # the author answers on the issue
        state["issues"]["1"]["comments"].append(
            {"author": {"login": "dev"}, "body": "The home page.", "createdAt": "2026-01-02T00:00:00Z"})
        box.write_state(state)
        self.assertIn(f"resume\t1\t{jid}\tneeds-info\tauthor replied", box.run("job", "queue").stdout)
        box.run("job", "advance", jid, "intake")
        d = self.folder(box, jid)
        self.assertEqual(box.job(jid)["stage"], "intake")
        self.assertTrue((d / "intake-1.md").exists(), "the first intake is kept")
        self.assertFalse((d / "intake.md").exists())
        self.assertIn("The home page.", (d / "issue.md").read_text())
        self.assertNotIn({"name": "factory:needs-info"}, box.state()["issues"]["1"]["labels"])

    def test_feedback_sends_a_waiting_job_back_to_build(self):
        box = self.sandbox()
        jid = box.to_approve()
        box.run("job", "merge", jid)  # auto_merge is off: the approved PR waits for a human
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertIn("use `job feedback`", box.run("job", "advance", jid, "build", ok=False).stderr)
        box.run("job", "feedback", jid, stdin="Say hello instead.\n")
        job = box.job(jid)
        self.assertEqual((job["stage"], job["round"], job["round_limit"]), ("build", 2, 4))
        self.assertIn("Say hello instead.", (self.folder(box, jid) / "feedback-1.md").read_text())

    def test_only_a_waiting_job_can_be_rejected(self):
        box = self.sandbox()
        jid = box.run("job", "create", "1").stdout.strip()
        refused = box.run("job", "reject", jid, stdin="No.\n", ok=False)
        self.assertIn("not needs-info or needs-human", refused.stderr)
        ask_author(box, jid)
        box.run("job", "reject", jid, stdin="Out of scope for this product.\n")
        self.assertEqual(box.job(jid)["stage"], "rejected")
        issue = box.state()["issues"]["1"]
        self.assertIn("Out of scope for this product.", issue["comments"][-1]["body"])
        self.assertNotIn({"name": "factory"}, issue["labels"])


class MergeFailureTest(Case):
    """An approved job whose merge would go wrong goes to a human instead, and nothing merges."""

    def test_a_conflict_with_the_base_goes_to_a_human(self):
        box = self.sandbox()
        box.config(auto_merge={"enabled": True, "max_risk": "medium"})
        jid = box.to_approve()
        push_to_base(box, "src/greeting.txt", "bye\n")
        box.run("job", "merge", jid)
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertIn("The branch conflicts with main", (self.folder(box, jid) / "decision.md").read_text())
        self.assertEqual(self.merges(box), [])

    def test_a_gate_failure_after_syncing_goes_to_a_human(self):
        box = self.sandbox(checks="test ! -e src/broken.txt\n")
        box.config(auto_merge={"enabled": True, "max_risk": "medium"})
        jid = box.to_approve()
        push_to_base(box, "src/broken.txt", "breaks the checks once merged\n")
        box.run("job", "merge", jid)
        d = self.folder(box, jid)
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertIn("The gate failed after syncing with main", (d / "decision.md").read_text())
        self.assertTrue((d / "round-1/merge-checks.md").read_text().startswith("GATE: FAIL"))
        self.assertEqual(self.merges(box), [])


class PushTest(Case):
    """A16: agents never push; job pushes the builder's commits when the build moves to the gate."""

    def test_the_factory_pushes_the_builders_commits(self):
        box = self.sandbox()
        jid = to_build(box)
        build(box, jid, "hi\n")
        wt = Path(box.job(jid)["worktree"])
        head = box.git(wt, "rev-parse", "HEAD").stdout.strip()
        remote = box.git(box.origin, "rev-parse", f"refs/heads/factory/{jid}").stdout.strip()
        self.assertEqual(remote, head)
        self.assertTrue(box.job(jid)["pr"], "the draft PR opens from the pushed branch")

    def test_uncommitted_work_is_refused_before_anything_is_pushed(self):
        box = self.sandbox()
        jid = to_build(box)
        wt = Path(box.job(jid)["worktree"])
        (wt / "src" / "greeting.txt").write_text("hi\n")
        box.write(f"factory/jobs/{jid}/build.md", "STATUS: READY\n\n# Build\n\n## Summary\n- forgot to commit\n")
        box.agent("builder")
        refused = box.run("job", "advance", jid, "gate", ok=False)
        self.assertIn("uncommitted changes", refused.stderr)
        res = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/factory/{jid}"],
                             cwd=box.origin, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "nothing reached origin")


class RerouteTest(Case):
    """A20: the route follows the actual diff. A change that touches sensitive_paths gets the security
    reviewer, the full profile and a high risk floor, whatever intake made of the issue."""

    def to_review(self, box, risk, extra=None):
        jid = to_build(box, risk=risk)
        build(box, jid, "hi\n", extra)
        box.run("gate", jid)
        box.run("job", "advance", jid, "review")
        return jid

    def test_a_lite_job_touching_auth_code_gets_security_review_and_the_full_profile(self):
        box = self.sandbox()
        jid = self.to_review(box, "low", {"src/Auth/session.txt": "remember me\n"})
        job = box.job(jid)
        self.assertEqual(job["agents"]["profile"], "full")
        self.assertIn("security", job["agents"]["reviewers"])
        self.assertEqual(job["risk_floor"], "high")
        self.assertEqual(set(job["reviews"]), {"code", "security"})
        events = [json.loads(l) for l in (self.folder(box, jid) / "events.jsonl").read_text().splitlines()]
        self.assertIn("src/Auth/session.txt", [e for e in events if e["event"] == "rerouted"][0]["paths"])

    def test_a_sensitive_change_never_merges_unattended_below_high(self):
        box = self.sandbox()
        box.config(auto_merge={"enabled": True, "max_risk": "medium"})
        jid = self.to_review(box, "medium", {"src/payments/refund.txt": "full refund\n"})
        for r in ("code", "security"):
            box.write(f"factory/jobs/{jid}/round-1/review-{r}.md", "VERDICT: PASS\n\n## Summary\n- fine\n")
            box.agent(f"reviewer-{r}")
        box.run("job", "advance", jid, "approve")
        box.write(f"factory/jobs/{jid}/decision.md", "APPROVE\n\n## Summary\n- good\n\n## Why\nMeets the spec.\n")
        box.agent("approver")
        out = box.run("job", "merge", jid).stdout
        self.assertIn("its risk (high) is above auto_merge.max_risk (medium)", out)
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertEqual(self.merges(box), [])

    def test_an_ordinary_change_keeps_its_route(self):
        box = self.sandbox()
        jid = self.to_review(box, "low")
        job = box.job(jid)
        self.assertEqual((job["agents"]["profile"], list(job["agents"]["reviewers"])), ("lite", ["code"]))
        self.assertNotIn("risk_floor", job)


class LimitTest(Case):
    """Concurrency, rounds and budget stop new work; the job goes to a human, never further."""

    def test_concurrency_holds_new_work_until_a_slot_frees(self):
        box = self.sandbox()
        state = box.state()
        state["issues"]["3"] = Sandbox.issue(3, "Add a footer", "dev")
        box.write_state(state)
        jid = box.run("job", "create", "1").stdout.strip()
        self.assertIn("hold\t3\t-\t-\tconcurrency 1 reached", box.run("job", "queue").stdout)
        self.assertIn("concurrency 1 reached", box.run("job", "create", "3", ok=False).stderr)
        ask_author(box, jid)  # a job waiting on its author holds no slot
        self.assertIn("new\t3\t-\t-", box.run("job", "queue").stdout)
        box.run("job", "create", "3")

    def test_the_last_round_goes_to_a_human(self):
        box = self.sandbox(checks="grep -qx hi src/greeting.txt\n")
        box.config(max_rounds=1)
        jid = to_build(box)
        build(box, jid, "hello\n")
        box.run("gate", jid, ok=False)
        refused = box.run("job", "advance", jid, "build", ok=False)
        self.assertIn("go to needs-human: round 1 was the last of 1", refused.stderr)
        box.run("job", "advance", jid, "needs-human")
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertIn("The gate failed in round 1", (self.folder(box, jid) / "decision.md").read_text())

    def test_a_job_over_budget_goes_to_a_human(self):
        box = self.sandbox(checks="grep -qx hi src/greeting.txt\n")
        box.config(max_cost_usd_per_job=1)
        jid = to_build(box)
        build(box, jid, "hello\n")
        box.run("gate", jid, ok=False)
        # The builder's transcript: 1M Opus input tokens, $4 at config.json's prices.
        job = load_script(box.repo / "factory" / "bin" / "job", "factory_job_budget")
        d = box.tmp / "claude" / "projects" / job.transcripts_dir().name / "s1" / "subagents"
        d.mkdir(parents=True)
        (d / "agent-1.jsonl").write_text("\n".join(json.dumps(line) for line in (
            {"message": {"content": f"Job: {jid}. Job folder: {job.job_dir(jid)}/."}},
            {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5",
                                              "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}},
        )) + "\n")
        refused = box.run("job", "advance", jid, "build", ok=False)
        self.assertIn("go to needs-human: it has cost $4.00, over its $1.00 budget", refused.stderr)
        box.run("job", "advance", jid, "needs-human")
        self.assertEqual(box.job(jid)["stage"], "needs-human")


if __name__ == "__main__":
    unittest.main()
