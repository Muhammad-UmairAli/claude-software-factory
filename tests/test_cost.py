"""Cost tracking (A25, MON-3): the orchestrator's own tokens, attributed per run; an alert when
transcripts stop parsing, so a Claude Code format change can't quietly zero the costs; and a
named owner and a monthly check for the price table. Standard library only.

    python3 -m unittest discover -s tests -v
"""

import csv
import io
import json
import unittest
from datetime import date, timedelta

from test_factory import Sandbox, load_script

OPUS_1M = {"input_tokens": 1_000_000, "output_tokens": 0}  # $4 at config.json's prices


def assistant(mid, usage=OPUS_1M, model="claude-opus-5-5"):
    return {"type": "assistant", "message": {"id": mid, "model": model, "usage": usage}}


class CostCase(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        job = load_script(self.box.repo / "factory" / "bin" / "job", "factory_job_cost")
        self.projects = self.box.tmp / "claude" / "projects" / job.transcripts_dir().name
        self.jobs = self.box.repo / "factory" / "jobs"

    def write(self, rel, records, tail=""):
        path = self.projects / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(r if isinstance(r, str) else json.dumps(r) + "\n" for r in records) + tail)
        return path

    def agent(self, session, n, jid, *records, tail=""):
        """A subagent transcript in a session: its prompt names the job folder, as every factory prompt does."""
        head = {"message": {"content": f"Job: {jid}. Job folder: {self.jobs / jid}/."}}
        return self.write(f"{session}/subagents/agent-{n}.jsonl", [head, *records], tail)

    def orchestrator(self, session, *records):
        return self.write(f"{session}.jsonl", [{"type": "user", "message": {"content": "/factory run"}}, *records])

    def metrics(self):
        return {row["job"]: row for row in csv.DictReader(io.StringIO(self.box.run("job", "metrics").stdout))}


class OrchestratorCostTest(CostCase):
    def test_each_runs_orchestrator_cost_is_shared_by_its_jobs(self):
        self.box.config(concurrency=2)
        state = self.box.state()
        state["issues"]["3"] = Sandbox.issue(3, "Add a farewell", "dev")
        self.box.write_state(state)
        one = self.box.run("job", "create", "1").stdout.strip()
        two = self.box.run("job", "create", "3").stdout.strip()
        # Run s1 worked on one job; run s2 on both. s3 is some other session in this repo.
        self.agent("s1", 1, one, assistant("a"))
        self.orchestrator("s1", assistant("o1", {"input_tokens": 500_000, "output_tokens": 0}))  # $2
        self.agent("s2", 1, one, assistant("b"))
        self.agent("s2", 2, two, assistant("c"))
        self.orchestrator("s2", assistant("o2"))  # $4, $2 to each job
        self.orchestrator("s3", assistant("o3", {"input_tokens": 2_500_000, "output_tokens": 0}))  # $10, not a factory run

        summary = self.box.run("job", "summary").stdout
        self.assertIn("total $12.00", summary, "the agents' cost, as before")
        self.assertIn("orchestrator $6.00 over 2 runs", summary)
        rows = self.metrics()
        self.assertEqual((rows[one]["cost_usd"], rows[one]["orchestrator_usd"]), ("8.00", "4.00"))
        self.assertEqual((rows[two]["cost_usd"], rows[two]["orchestrator_usd"]), ("4.00", "2.00"))
        self.box.run("job", "sync", one)
        self.assertEqual(self.box.job(one)["orchestrator_usd"], 4.0)

    def test_the_budget_counts_the_agents_only(self):
        """A job's budget can't move because another job ran in the same session."""
        self.box.config(max_cost_usd_per_job=5)
        jid = self.box.run("job", "create", "1").stdout.strip()
        self.agent("s1", 1, jid, assistant("a"))
        self.orchestrator("s1", assistant("o1"), assistant("o2"))
        self.box.run("job", "sync", jid)
        job = self.box.job(jid)
        self.assertEqual((job["cost_usd"], job["orchestrator_usd"]), (4.0, 8.0))


class UnreadableTranscriptTest(CostCase):
    def test_lines_that_dont_parse_raise_an_alert(self):
        jid = self.box.run("job", "create", "1").stdout.strip()
        self.agent("s1", 1, jid, assistant("a"), "{not json\n", assistant("b"))
        res = self.box.run("job", "sync", jid)
        self.assertIn(f"the cost of {jid} may be low", res.stderr)
        self.assertEqual(self.box.job(jid)["cost_unreadable"], 1)
        self.assertEqual(self.box.job(jid)["cost_usd"], 8.0, "what did parse still counts")
        events = [json.loads(l) for l in (self.jobs / jid / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["records"] for e in events if e["event"] == "cost_unreadable"], [1])
        self.box.run("job", "sync", jid)
        events = [json.loads(l) for l in (self.jobs / jid / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len([e for e in events if e["event"] == "cost_unreadable"]), 1, "logged once, not per command")
        summary = self.box.run("job", "summary").stdout
        self.assertIn("may be low", summary)
        self.assertIn(jid, summary.split("may be low")[1])

    def test_a_transcript_with_no_usage_at_all_is_an_alert(self):
        """A format change that renames the usage records parses as JSON but costs nothing."""
        jid = self.box.run("job", "create", "1").stdout.strip()
        reply = {"type": "reply", "data": {"tokens": {"in": 1_000_000, "out": 0}}}
        self.agent("s1", 1, jid, *[reply] * 5)
        res = self.box.run("job", "sync", jid)
        self.assertIn("may be low", res.stderr)
        self.assertEqual(self.box.job(jid)["cost_unreadable"], 1)

    def test_a_line_still_being_written_is_not_an_alert(self):
        jid = self.box.run("job", "create", "1").stdout.strip()
        self.agent("s1", 1, jid, assistant("a"), tail='{"type": "assistant", "mess')
        res = self.box.run("job", "sync", jid)
        self.assertNotIn("may be low", res.stderr)
        self.assertNotIn("cost_unreadable", self.box.job(jid))

    def test_doctor_reports_unreadable_transcripts(self):
        jid = self.box.run("job", "create", "1").stdout.strip()
        self.agent("s1", 1, jid, assistant("a"), "{not json\n")
        self.box.run("job", "summary")
        line = [l for l in self.box.run("doctor", "--offline", ok=False).stdout.splitlines() if "transcript" in l]
        self.assertEqual(len(line), 1, line)
        self.assertTrue(line[0].startswith("warn"), line)


class PriceOwnerTest(CostCase):
    def price_line(self):
        lines = [l for l in self.box.run("doctor", "--offline", ok=False).stdout.splitlines() if "price" in l]
        self.assertEqual(len(lines), 1, lines)
        return lines[0]

    def test_prices_need_an_owner(self):
        self.box.config(prices_owner=None, prices_checked=f"{date.today().isoformat()}, https://example.test/pricing")
        line = self.price_line()
        self.assertTrue(line.startswith("warn"), line)
        self.assertIn("prices_owner", line)

    def test_prices_need_a_check_every_month(self):
        old = (date.today() - timedelta(days=40)).isoformat()
        self.box.config(prices_owner="dev", prices_checked=f"{old}, https://example.test/pricing")
        line = self.price_line()
        self.assertTrue(line.startswith("warn"), line)
        self.assertIn("40 days ago", line)
        self.assertIn("dev", line)

    def test_a_current_check_by_a_named_owner_is_ok(self):
        self.box.config(prices_owner="dev", prices_checked=f"{date.today().isoformat()}, https://example.test/pricing")
        self.assertTrue(self.price_line().startswith("ok"))

    def test_a_check_without_a_date_is_a_warning(self):
        self.box.config(prices_owner="dev", prices_checked="recently")
        line = self.price_line()
        self.assertTrue(line.startswith("warn"), line)
        self.assertIn("date", line)


if __name__ == "__main__":
    unittest.main()
