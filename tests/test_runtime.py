"""The factory's runtime container: what factory/bin/runtime passes to docker, and
what it never passes. Building the image and the egress firewall need Docker with NET_ADMIN, so the
runtime workflow in CI checks those (.github/workflows/runtime.yml). Standard library only.

    python3 -m unittest discover -s tests -v
"""

import shlex
import unittest

from test_factory import Sandbox


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def command(self):
        return shlex.split(self.box.run("runtime", "command").stdout)

    def test_the_container_gets_the_repository_and_nothing_else_from_the_host(self):
        args = self.command()
        self.assertEqual([a for a in args if a.startswith("type=bind")],
                         [f"type=bind,source={self.box.repo},target=/workspace"])
        for flag in ("--cap-add=NET_ADMIN", "no-new-privileges", "127.0.0.1:8000:8000"):
            self.assertIn(flag, args)
        joined = " ".join(args)
        for leak in ("SSH_AUTH_SOCK", ".ssh", ".gitconfig", "docker.sock", "--privileged", "--network",
                     ".aws", "GH_TOKEN", "--env-file", "--volumes-from"):
            self.assertNotIn(leak, joined)

    def test_only_the_git_identity_goes_in_as_environment(self):
        self.box.git(self.box.repo, "config", "user.name", "Factory Test")
        self.box.git(self.box.repo, "config", "user.email", "test@example.invalid")
        args = self.command()
        envs = sorted(args[i + 1] for i, a in enumerate(args) if a == "--env")
        self.assertEqual(envs, ["GIT_AUTHOR_EMAIL=test@example.invalid", "GIT_AUTHOR_NAME=Factory Test",
                                "GIT_COMMITTER_EMAIL=test@example.invalid", "GIT_COMMITTER_NAME=Factory Test"])

    def test_the_app_key_goes_in_read_only_when_an_app_is_set_up(self):
        key = self.box.tmp / "keys" / "app.pem"
        key.parent.mkdir()
        key.write_text("placeholder, never read by the launcher\n")
        key.chmod(0o600)
        self.box.config(github_app={"app_id": 1, "installation_id": 2, "private_key": str(key)})
        self.assertIn(f"type=bind,source={key},target={key},readonly", self.command())

    def test_the_image_tag_follows_the_runtime_files(self):
        before = self.command()[-2]
        (self.box.repo / "factory" / "runtime" / "egress-allowlist").write_text("api.anthropic.com\n")
        self.assertNotEqual(self.command()[-2], before)

    def test_build_refuses_uncommitted_runtime_files(self):
        allowlist = self.box.repo / "factory" / "runtime" / "egress-allowlist"
        allowlist.write_text(allowlist.read_text() + "exfiltrate.example\n")
        res = self.box.run("runtime", "build", ok=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("uncommitted", res.stderr)


if __name__ == "__main__":
    unittest.main()
