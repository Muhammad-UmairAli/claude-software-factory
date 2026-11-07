"""The dashboard server (A26, SEC-11): it answers only requests addressed to 127.0.0.1 or localhost
that carry this run's token, serves only the board and the job folders, read-only, and never serves
a job file as HTML. Standard library only.

    python3 -m unittest discover -s tests -v
"""

import http.client
import json
import threading
import unittest

from test_factory import Sandbox, load_script

TOKEN = "t0ken-for-this-run-only-0123456789abcdef"


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.jid = self.box.run("job", "create", "1").stdout.strip()
        dashboard = load_script(self.box.repo / "factory" / "bin" / "dashboard", "factory_dashboard")
        self.server = dashboard.make_server(port=0, token=TOKEN)
        self.server.log = lambda line: None
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.cookie = f"factory_dashboard={TOKEN}"

    def request(self, path, host=None, cookie=None, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, headers=headers)
        res = conn.getresponse()
        body = res.read().decode(errors="replace")
        conn.close()
        return res, body

    def get(self, path, **kwargs):
        return self.request(path, cookie=self.cookie, **kwargs)

    def test_requests_without_this_runs_token_are_refused(self):
        for path in ("/dashboard.html", "/jobs/", f"/jobs/{self.jid}/job.json"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0].status, 403)
                self.assertEqual(self.request(path, cookie="factory_dashboard=guess")[0].status, 403)
        self.assertEqual(self.request("/?token=guess")[0].status, 403)

    def test_the_link_sets_the_token_as_a_cookie_and_drops_it_from_the_address(self):
        res, _ = self.request(f"/?token={TOKEN}")
        self.assertEqual(res.status, 303)
        self.assertEqual(res.getheader("Location"), "/dashboard.html")
        cookie = res.getheader("Set-Cookie")
        self.assertIn(f"factory_dashboard={TOKEN}", cookie)
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        res, body = self.get("/dashboard.html")
        self.assertEqual(res.status, 200)
        self.assertTrue(res.getheader("Content-Type").startswith("text/html"))
        self.assertIn("Content-Security-Policy", body)

    def test_a_request_for_another_host_is_refused_even_with_the_token(self):
        """DNS rebinding: a page on evil.example resolves its name to 127.0.0.1, but its requests still
        say Host: evil.example."""
        for host in (f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", f"localhost.evil:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.get("/dashboard.html", host=host)[0].status, 403)
        self.assertEqual(self.get("/dashboard.html", host=f"localhost:{self.port}")[0].status, 200)

    def test_only_the_board_and_job_folders_are_served(self):
        jobs = self.box.repo / "factory" / "jobs"
        (jobs / self.jid / "leak.md").symlink_to(self.box.repo / "factory" / "config.json")
        for path in ("/config.json", "/bin/job", "/checks.sh", "/jobs/.integrity.json", "/jobs/.cost-cache.json",
                     "/jobs/guard.jsonl", "/jobs/audit/", "/jobs/../config.json", "/jobs/%2e%2e/config.json",
                     f"/jobs/{self.jid}/../../config.json", f"/jobs/{self.jid}/leak.md", f"/jobs/{self.jid}/.lock"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0].status, 404)

    def test_job_folders_list_only_jobs_and_files_are_plain_text(self):
        res, listing = self.get("/jobs/")
        self.assertEqual(res.status, 200)
        self.assertIn(f'href="{self.jid}/"', listing)
        for hidden in ("audit", "guard.jsonl", ".integrity.json", ".lock"):
            self.assertNotIn(hidden, listing)
        res, body = self.get(f"/jobs/{self.jid}/job.json")
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "application/json"))
        self.assertEqual(json.loads(body)["id"], self.jid)
        (self.box.repo / "factory" / "jobs" / self.jid / "round-1").mkdir()
        (self.box.repo / "factory" / "jobs" / self.jid / "round-1" / "checks.md").write_text("<script>x</script>\n")
        res, body = self.get(f"/jobs/{self.jid}/round-1/checks.md")
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/plain; charset=utf-8"))
        self.assertEqual(body, "<script>x</script>\n")
        self.assertIn('href="round-1/"', self.get(f"/jobs/{self.jid}/")[1])
        for header, value in (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                              ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer")):
            self.assertEqual(res.getheader(header), value)
        self.assertIn("frame-ancestors 'none'", res.getheader("Content-Security-Policy"))

    def test_it_is_read_only(self):
        for method in ("POST", "PUT", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(self.get(f"/jobs/{self.jid}/job.json", method=method)[0].status, 405)
        self.assertTrue((self.box.repo / "factory" / "jobs" / self.jid / "job.json").exists())

    def test_the_token_never_reaches_the_log(self):
        log = []
        self.server.log = log.append
        self.request(f"/?token={TOKEN}")
        self.get("/dashboard.html")
        self.assertTrue(log)
        self.assertNotIn(TOKEN, "\n".join(log))


class DashboardCommandTest(unittest.TestCase):
    def test_it_binds_to_this_machine_only(self):
        box = Sandbox()
        self.addCleanup(box.cleanup)
        dashboard = load_script(box.repo / "factory" / "bin" / "dashboard", "factory_dashboard_bind")
        server = dashboard.make_server(port=0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        self.assertGreaterEqual(len(server.token), 32, "a fresh random token for each run")
        other = dashboard.make_server(port=0)
        self.addCleanup(other.server_close)
        self.assertNotEqual(server.token, other.token)
        with self.assertRaises(ValueError):
            dashboard.make_server(port=0, bind="192.168.1.10")
        res = box.run("dashboard", "--bind", "10.0.0.1", ok=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("bad arguments", res.stderr)


if __name__ == "__main__":
    unittest.main()
