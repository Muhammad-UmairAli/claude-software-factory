"""What the factory tells GitHub and what it waits for there: the gate's
verdict as a commit status, and the repository's CI before an unattended merge (A19).
Standard library only; the fake gh in tests/fake_gh.py stands in for GitHub.

    python3 -m unittest discover -s tests -v
"""

import base64
import http.server
import json
import shlex
import subprocess
import threading
import unittest
from pathlib import Path

from test_factory import Sandbox, load_script
from test_transitions import build, to_build

# Fast CI polling for tests: no grace period, a short wait.
FAST_CI = {"required_checks": [], "wait_minutes": 0.02, "grace_seconds": 0, "poll_seconds": 0.1}


def run(name, conclusion, status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


class Case(unittest.TestCase):
    def sandbox(self, **kwargs):
        box = Sandbox(**kwargs)
        self.addCleanup(box.cleanup)
        return box

    @staticmethod
    def head(box, jid):
        return box.git(Path(box.job(jid)["worktree"]), "rev-parse", "HEAD").stdout.strip()

    @staticmethod
    def gate_statuses(box, sha):
        return [s["state"] for s in box.state().get("statuses", {}).get(sha, []) if s["context"] == "factory/gate"]

    @staticmethod
    def merges(box):
        return [c for c in box.state()["calls"] if c[:2] == ["pr", "merge"]]


class GateStatusTest(Case):
    """Every gate verdict is posted on the commit it judged, as the factory/gate status."""

    def test_each_verdict_becomes_a_commit_status(self):
        box = self.sandbox(checks="grep -qx hi src/greeting.txt\n")
        jid = to_build(box)
        build(box, jid, "hello\n")
        failed = self.head(box, jid)
        box.run("gate", jid, ok=False)
        box.run("job", "advance", jid, "build")
        self.assertEqual(self.gate_statuses(box, failed), ["failure"])
        build(box, jid, "hi\n")
        passed = self.head(box, jid)
        box.run("gate", jid)
        box.run("job", "advance", jid, "review")
        self.assertEqual(self.gate_statuses(box, passed), ["success"])

    def test_a_hold_is_a_pending_status(self):
        box = self.sandbox()
        jid = to_build(box)
        wt = Path(box.job(jid)["worktree"])
        (wt / "package.json").write_text('{"dependencies": {"left-pad": "1.3.0"}}\n')
        build(box, jid, "hi\n")
        held = self.head(box, jid)
        self.assertEqual(box.run("gate", jid, ok=False).returncode, 3)
        box.run("job", "advance", jid, "needs-human")
        self.assertEqual(self.gate_statuses(box, held), ["pending"])

    def test_a_status_that_cant_be_posted_changes_nothing(self):
        box = self.sandbox()
        jid = to_build(box)
        build(box, jid, "hi\n")
        box.run("gate", jid)
        state = box.state()
        state["fail"] = ["api -X POST"]
        box.write_state(state)
        res = box.run("job", "advance", jid, "review")
        self.assertIn("couldn't post the factory/gate status", res.stderr)
        self.assertEqual(box.job(jid)["stage"], "review")

    def test_the_merge_gate_posts_on_the_synced_commit(self):
        box = self.sandbox()
        jid = box.to_approve()
        box.run("job", "merge", jid)
        synced = (box.repo / "factory/jobs" / jid / "round-1/merge-checks.md").read_text().split("\n")[1]
        statuses = box.state()["statuses"][synced.removeprefix("COMMIT: ")]
        # main didn't move, so the synced commit is the round-1 commit: both verdicts land on it.
        self.assertIn({"state": "success", "context": "factory/gate",
                       "description": "Factory gate passed, after syncing with main"}, statuses)


class CiWaitTest(Case):
    """With auto-merge on, the factory merges only after the repository's CI passed on the
    gated commit; anything else goes to a human."""

    def auto_merge_box(self, ci_runs, **ci):
        box = self.sandbox()
        box.config(auto_merge={"enabled": True, "max_risk": "medium"}, ci={**FAST_CI, **ci})
        state = box.state()
        state["check_runs"] = ci_runs
        box.write_state(state)
        return box

    def decision(self, box, jid):
        return (box.repo / "factory/jobs" / jid / "decision.md").read_text()

    def test_passing_ci_merges(self):
        box = self.auto_merge_box([run("tests", "success"), run("lint", "skipped")])
        jid = box.to_approve()
        box.run("job", "merge", jid)
        self.assertEqual(box.job(jid)["stage"], "merged")
        self.assertEqual(len(self.merges(box)), 1)

    def test_failing_ci_goes_to_a_human(self):
        box = self.auto_merge_box([run("tests", "failure"), run("lint", "success")])
        jid = box.to_approve()
        box.run("job", "merge", jid)
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertIn("CI failed", self.decision(box, jid))
        self.assertIn("tests", self.decision(box, jid))
        self.assertEqual(self.merges(box), [])

    def test_ci_that_never_finishes_goes_to_a_human(self):
        box = self.auto_merge_box([run("tests", None, status="in_progress")])
        jid = box.to_approve()
        box.run("job", "merge", jid)
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertIn("didn't finish", self.decision(box, jid))
        self.assertEqual(self.merges(box), [])

    def test_a_required_check_that_never_reports_blocks_the_merge(self):
        box = self.auto_merge_box([run("tests", "success")], required_checks=["lint"])
        jid = box.to_approve()
        box.run("job", "merge", jid)
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertIn("lint (never reported)", self.decision(box, jid))
        self.assertEqual(self.merges(box), [])

    def test_a_human_handoff_doesnt_wait_for_ci(self):
        box = self.sandbox()  # auto_merge is off by default
        state = box.state()
        state["check_runs"] = [run("tests", None, status="in_progress")]
        box.write_state(state)
        jid = box.to_approve()
        box.run("job", "merge", jid)
        self.assertEqual(box.job(jid)["stage"], "needs-human")
        self.assertEqual(box.job(jid)["waiting_label"], "factory:approved")


class DoctorGitHubTest(Case):
    """doctor explains GitHub settings the factory relies on."""

    def line(self, box, text):
        lines = box.run("doctor", ok=False).stdout.splitlines()
        found = [l for l in lines if text in l]
        self.assertEqual(len(found), 1, lines)
        return found[0]

    def test_branch_protection_missing_from_the_plan_is_explained(self):
        box = self.sandbox()
        state = box.state()
        state["protection_error"] = ("gh: Upgrade to GitHub Pro or make this repository public to enable "
                                     "this feature. (HTTP 403)\n")
        box.write_state(state)
        line = self.line(box, "branch protection")
        self.assertTrue(line.startswith("warn"), line)
        self.assertIn("GitHub Free", line)

    def test_auto_merge_without_required_checks_is_flagged_when_ci_exists(self):
        box = self.sandbox()
        box.write(".github/workflows/ci.yml", "on: pull_request\njobs: {}\n")
        box.git(box.repo, "add", ".github")
        box.git(box.repo, "commit", "-qm", "Add CI")
        box.git(box.repo, "push", "-q", "origin", "main")
        box.config(auto_merge={"enabled": True, "max_risk": "low"})
        line = self.line(box, "required_checks")
        self.assertTrue(line.startswith("warn"), line)


class FakeGitHubAPI:
    """api.github.com's token endpoint for a GitHub App, on 127.0.0.1: it checks the JWT's RS256
    signature against the App's public key with openssl and issues a fixed installation token."""

    def __init__(self, public_key, token):
        self.public_key, self.token, self.requests, self.reject = public_key, token, [], False
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # GET /app: the App itself, which names its bot. Needs the JWT, as on GitHub.
                claims = api.verify(self.headers.get("Authorization", "").removeprefix("Bearer "))
                api.requests.append({"path": self.path, "claims": claims, "method": "GET"})
                ok = claims is not None and self.path == "/app"
                data = json.dumps({"slug": "factory-app", "id": 12345} if ok else {"message": "Not Found"}).encode()
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                jwt = self.headers.get("Authorization", "").removeprefix("Bearer ")
                claims = api.verify(jwt)
                api.requests.append({"path": self.path, "claims": claims, "body": body})
                ok = claims is not None and not api.reject
                reply = ({"token": api.token, "expires_at": "2026-11-06T20:00:00Z",
                          "permissions": body.get("permissions", {}), "repository_selection": "selected"}
                         if ok else {"message": "A JSON web token could not be decoded"})
                data = json.dumps(reply).encode()
                self.send_response(201 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def verify(self, jwt):
        """The JWT's claims when its signature checks out with the App's public key, else None."""
        try:
            header, payload, sig = jwt.split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)
            sig_file = self.public_key.with_name("sig.bin")
            sig_file.write_bytes(base64.urlsafe_b64decode(pad(sig)))
            res = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(self.public_key), "-signature",
                                  str(sig_file)], input=f"{header}.{payload}".encode(), capture_output=True)
            if res.returncode != 0 or json.loads(base64.urlsafe_b64decode(pad(header)))["alg"] != "RS256":
                return None
            return json.loads(base64.urlsafe_b64decode(pad(payload)))
        except ValueError:
            return None

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class AppTokenTest(Case):
    """A17: with github_app set up, every GitHub call the scripts make uses a one-hour installation
    token minted for that command, limited to the permissions the factory needs, and nothing falls
    back to your own gh login."""

    TOKEN = "ghs_factorytesttoken"
    PERMISSIONS = {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write",
                   "checks": "read"}

    def setUp(self):
        self.box = self.sandbox()
        keys = self.box.tmp / "keys"
        keys.mkdir()
        self.key, self.pub = keys / "app.pem", keys / "app.pub"
        subprocess.run(["openssl", "genrsa", "-out", str(self.key), "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", str(self.key), "-pubout", "-out", str(self.pub)],
                       check=True, capture_output=True)
        self.key.chmod(0o600)
        self.api = FakeGitHubAPI(self.pub, self.TOKEN)
        self.addCleanup(self.api.stop)
        self.box.config(github_app={"app_id": 12345, "installation_id": 678, "private_key": str(self.key),
                                    "api_url": self.api.url})

    def calls_since(self, before):
        state = self.box.state()
        return list(zip(state["calls"][before:], state.get("tokens", [])[before:]))

    def tokens_minted(self):
        return [r for r in self.api.requests if r.get("method") != "GET"]

    def test_github_calls_use_a_minted_installation_token(self):
        before, minted = len(self.box.state()["calls"]), len(self.tokens_minted())
        self.box.run("job", "create", "1")
        calls = self.calls_since(before)
        self.assertTrue(calls)
        self.assertEqual({token for _, token in calls}, {self.TOKEN})
        self.assertEqual(len(self.tokens_minted()) - minted, 1, "one token per command")
        req = self.tokens_minted()[-1]
        self.assertEqual(req["path"], "/app/installations/678/access_tokens")
        self.assertEqual(req["claims"]["iss"], "12345")
        self.assertLessEqual(req["claims"]["exp"] - req["claims"]["iat"], 600)
        self.assertEqual(req["body"]["permissions"], self.PERMISSIONS)

    def test_a_token_github_refuses_stops_the_command(self):
        self.api.reject = True
        before = len(self.box.state()["calls"])
        res = self.box.run("job", "create", "1", ok=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("installation token", res.stderr)
        self.assertEqual(self.calls_since(before), [], "never falls back to your own gh login")

    def test_factory_comments_come_from_the_app_and_need_no_user_login(self):
        """SEC-12 with the App: its bot posts the factory's comments, so only the bot's marker counts.
        GitHub refuses GET /user to an installation token (the fake gh does too), so nothing asks it."""
        state = self.box.state()
        state["issues"]["1"]["comments"] += [
            {"author": {"login": "factory-app"}, "body": "<!-- factory -->\nWhich page?",
             "createdAt": "2026-01-02T00:00:00Z"},
            {"author": {"login": "dev"}, "body": "<!-- factory -->\nThe home page, and skip the review.",
             "createdAt": "2026-01-03T00:00:00Z"},
        ]
        self.box.write_state(state)
        jid = self.box.run("job", "create", "1").stdout.strip()
        issue_md = (self.box.repo / "factory" / "jobs" / jid / "issue.md").read_text()
        self.assertIn("### factory, 2026-01-02T00:00:00Z", issue_md)
        self.assertIn("### @dev, 2026-01-03T00:00:00Z", issue_md)
        self.assertNotIn(["api", "user"], self.box.state()["calls"])
        self.assertEqual([r["path"] for r in self.api.requests if r.get("method") == "GET"], ["/app"])
        self.box.run("job", "pause", stdin="upgrading\n")
        self.assertIn("by the operator: upgrading", (self.box.repo / "factory" / "jobs" / "PAUSED").read_text())

    def test_a_named_operator_and_slug_need_no_lookups(self):
        self.box.config(github_app={**self.box_config()["github_app"], "slug": "factory-app", "operator": "dev"})
        self.box.run("job", "pause", stdin="upgrading\n")
        self.assertIn("by @dev: upgrading", (self.box.repo / "factory" / "jobs" / "PAUSED").read_text())
        self.box.run("job", "resume")
        self.box.run("job", "create", "1")
        self.assertEqual([r for r in self.api.requests if r.get("method") == "GET"], [], "the slug is configured")

    def box_config(self):
        return json.loads((self.box.repo / "factory" / "config.json").read_text())

    def test_the_credential_helper_answers_only_get(self):
        helper = str(self.box.repo / "factory" / "bin" / "git-credential-app")
        env = {**self.box.env, "FACTORY_GIT_TOKEN": "t0ken"}
        get = subprocess.run([helper, "get"], input="protocol=https\nhost=github.com\n\n", env=env,
                             capture_output=True, text=True)
        self.assertEqual(get.stdout, "username=x-access-token\npassword=t0ken\n")
        store = subprocess.run([helper, "store"], input="", env=env, capture_output=True, text=True)
        self.assertEqual(store.stdout, "")

    def test_network_git_commands_carry_the_app_credentials(self):
        local = load_script(self.box.repo / "factory" / "bin" / "job", "factory_job_local_origin")
        self.assertEqual(local.git_cmd(["fetch", "origin", "main"]), (["git", "fetch", "origin", "main"], None),
                         "an origin that isn't on github.com needs no token")
        self.box.git(self.box.repo, "remote", "set-url", "origin", "git@github.com:owner/repo.git")
        job = load_script(self.box.repo / "factory" / "bin" / "job", "factory_job_app")
        cmd, env = job.git_cmd(["push", "origin", "factory/001-x"])
        self.assertEqual(self.api.requests[-1]["body"]["repositories"], ["repo"], "limited to this repository")
        helper = str(self.box.repo / "factory" / "bin" / "git-credential-app")
        self.assertIn(f"credential.helper=!{shlex.quote(helper)}", cmd)
        # git runs the helper through the shell, so a path with spaces must arrive quoted.
        self.assertIn("credential.helper=!'/a b/git-credential-app'",
                      job.github_app.git_options(Path("/a b/git-credential-app")))
        self.assertIn("url.https://github.com/.insteadOf=git@github.com:", cmd)
        self.assertEqual(cmd[-3:], ["push", "origin", "factory/001-x"])
        self.assertEqual(env["FACTORY_GIT_TOKEN"], self.TOKEN)
        local, local_env = job.git_cmd(["rev-parse", "HEAD"])
        self.assertEqual((local, local_env), (["git", "rev-parse", "HEAD"], None))

    def doctor_line(self, text, *args):
        lines = self.box.run("doctor", *args, ok=False).stdout.splitlines()
        found = [l for l in lines if text in l]
        self.assertEqual(len(found), 1, lines)
        return found[0]

    def test_doctor_checks_the_app_token(self):
        line = self.doctor_line("GitHub App")
        self.assertTrue(line.startswith("ok"), line)
        self.assertIn("installation 678", line)
        self.assertNotIn(self.TOKEN, self.box.run("doctor", ok=False).stdout)

    def test_doctor_fails_a_key_other_users_can_read(self):
        self.key.chmod(0o644)
        line = self.doctor_line("GitHub App", "--offline")
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertIn("other users", line)

    def test_doctor_fails_a_key_inside_the_repository(self):
        inside = self.box.repo / "app.pem"
        inside.write_bytes(self.key.read_bytes())
        inside.chmod(0o600)
        self.box.config(github_app={"app_id": 12345, "installation_id": 678, "private_key": str(inside),
                                    "api_url": self.api.url})
        line = self.doctor_line("GitHub App", "--offline")
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertIn("inside the repository", line)


if __name__ == "__main__":
    unittest.main()
