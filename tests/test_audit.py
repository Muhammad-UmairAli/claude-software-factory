"""The factory's tamper-evident audit log: job events and guard decisions in one
hash chain, rotation and retention that keep it verifiable, and shipping to a collector.
Standard library only.

    python3 -m unittest discover -s tests -v
"""

import http.server
import json
import os
import threading
import time
import unittest

from test_factory import Sandbox


class Collector:
    """An HTTP endpoint on 127.0.0.1 that keeps whatever audit records it's sent."""

    def __init__(self):
        self.records, self.auth, self.down = [], [], False
        collector = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                collector.auth.append(self.headers.get("Authorization"))
                if collector.down:
                    self.send_response(503)
                else:
                    collector.records += body["records"]
                    self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/audit"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.folder = self.box.repo / "factory" / "jobs" / "audit"

    def records(self):
        files = sorted(self.folder.glob("audit-*.jsonl")) + [self.folder / "audit.jsonl"]
        return [json.loads(l) for p in files if p.exists() for l in p.read_text().splitlines() if l.strip()]

    def guard(self, role="intake", **tool_input):
        """One tool call through the real guard hook; a Read of AGENTS.md unless told otherwise."""
        tool = "Bash" if "command" in tool_input else "Read"
        event = {"tool_name": tool, "tool_input": tool_input or {"file_path": str(self.box.repo / "AGENTS.md")},
                 "cwd": str(self.box.repo), "agent_type": role}
        return self.box.run("guard", role, stdin=json.dumps(event), ok=False)

    def audit(self, *args):
        return self.box.run("job", "audit", *args, ok=False)

    def test_job_events_and_guard_decisions_share_one_chain(self):
        jid = self.box.run("job", "create", "1").stdout.strip()
        self.guard()
        self.guard(command="cat .env")
        recs = self.records()
        self.assertIn(("job", "created", jid), [(r["source"], r["event"], r.get("job")) for r in recs])
        self.assertEqual([r["decision"] for r in recs if r["source"] == "guard"], ["allow", "block"])
        self.assertEqual([r["seq"] for r in recs], list(range(1, len(recs) + 1)))
        res = self.audit()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("chain intact", res.stdout)

    def test_an_edited_record_breaks_the_chain(self):
        self.box.run("job", "create", "1")
        self.guard()
        path = self.folder / "audit.jsonl"
        lines = path.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["event"] = "nothing-to-see"
        lines[0] = json.dumps(rec, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")
        res = self.audit()
        self.assertEqual(res.returncode, 1)
        self.assertIn("was changed after it was written", res.stdout)

    def test_a_deleted_record_breaks_the_chain(self):
        self.box.run("job", "create", "1")
        self.guard()
        self.guard()
        path = self.folder / "audit.jsonl"
        lines = path.read_text().splitlines()
        del lines[1]
        path.write_text("\n".join(lines) + "\n")
        res = self.audit()
        self.assertEqual(res.returncode, 1)
        self.assertIn("the chain breaks before record 3", res.stdout)

    def test_rotation_and_retention_keep_the_chain_verifiable(self):
        self.box.config(audit={"max_mb": 0.0005, "keep_days": 30})
        for _ in range(8):
            self.guard()
        rotated = sorted(self.folder.glob("audit-*.jsonl"))
        self.assertGreater(len(rotated), 2)
        os.utime(rotated[0], (time.time() - 40 * 86400,) * 2)
        for _ in range(4):
            self.guard()
        self.assertFalse(rotated[0].exists(), "past keep_days")
        self.assertIn("pruned", [r["event"] for r in self.records()])
        res = self.audit()
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("chain intact", res.stdout)

    def test_deleting_the_oldest_file_by_hand_is_caught(self):
        self.box.config(audit={"max_mb": 0.0005, "keep_days": 30})
        for _ in range(8):
            self.guard()
        sorted(self.folder.glob("audit-*.jsonl"))[0].unlink()
        res = self.audit()
        self.assertEqual(res.returncode, 1)
        self.assertIn("are missing", res.stdout)

    def test_audit_shows_a_daily_trend(self):
        self.box.run("job", "create", "1")
        self.guard(command="cat .env")
        out = self.audit().stdout
        today = time.strftime("%Y-%m-%d", time.gmtime())
        self.assertIn(today, out)
        self.assertIn("created 1", out)
        self.assertIn("guard block 1", out)

    def test_doctor_fails_a_broken_chain(self):
        self.box.run("job", "create", "1")
        self.guard()
        path = self.folder / "audit.jsonl"
        path.write_text(path.read_text().replace('"created"', '"forged"', 1))
        lines = [l for l in self.box.run("doctor", "--offline", ok=False).stdout.splitlines() if "audit log" in l]
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("FAIL"), lines[0])

    def test_shipping_sends_each_record_once_with_the_token(self):
        collector = Collector()
        self.addCleanup(collector.stop)
        self.box.config(audit={"forward_url": collector.url})
        self.box.env["FACTORY_AUDIT_TOKEN"] = "collector-secret"
        self.box.run("job", "create", "1")
        self.guard()
        sent = len(self.records())
        self.assertIn(f"shipped {sent} records", self.audit("ship").stdout)
        self.assertEqual([r["seq"] for r in collector.records], list(range(1, sent + 1)))
        self.assertEqual(set(collector.auth), {"Bearer collector-secret"})
        self.assertIn("nothing to ship", self.audit("ship").stdout)
        self.assertEqual(len(collector.records), sent)

    def test_records_wait_while_the_collector_is_down(self):
        collector = Collector()
        self.addCleanup(collector.stop)
        self.box.config(audit={"forward_url": collector.url})
        self.box.run("job", "create", "1")
        collector.down = True
        res = self.audit("ship")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("waiting", res.stdout + res.stderr)
        collector.down = False
        self.assertIn("shipped", self.audit("ship").stdout)
        self.assertEqual(len(collector.records), len(self.records()))

    def test_plain_http_to_another_host_is_refused(self):
        self.box.config(audit={"forward_url": "http://collector.example/audit"})
        self.box.run("job", "create", "1")
        res = self.audit("ship")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("https", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
