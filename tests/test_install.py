"""The setup command (`./install <project>`): it copies the factory into a project, drafts
factory/checks.sh from the project's own toolchain, and refuses anything it could get wrong.
Standard library only; each test builds a throwaway project with a local bare origin.

    python3 -m unittest discover -s tests -v
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_factory import load_script

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install"


class Project:
    """A git repository with an origin, as a project the factory gets installed into."""

    def __init__(self, files=None, base="main"):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="factory-install-")))
        self.dir = self.tmp / "project"
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "Factory Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Factory Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
                    "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
                    "CLAUDE_CONFIG_DIR": str(self.tmp / "claude")}
        self.git(self.tmp, "init", "-q", "--bare", "-b", base, "origin.git")
        self.git(self.tmp, "init", "-q", "-b", base, "project")
        for rel, text in {"README.md": "# Demo\n", **(files or {})}.items():
            (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / rel).write_text(text)
        self.git(self.dir, "add", "-A")
        self.git(self.dir, "commit", "-qm", "init")
        self.git(self.dir, "remote", "add", "origin", str(self.tmp / "origin.git"))
        self.git(self.dir, "push", "-q", "-u", "origin", base)

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, text=True)

    def install(self, *args):
        return subprocess.run([str(INSTALL), str(self.dir), *args], env=self.env, capture_output=True, text=True)

    def status(self):
        return self.git(self.dir, "status", "--porcelain", "--untracked-files=all").stdout

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


NODE = {"package.json": json.dumps({"scripts": {"lint": "eslint .", "test": "jest", "build": "tsc"}}),
        "package-lock.json": "{}\n"}


class InstallTest(unittest.TestCase):
    def project(self, files=None, base="main"):
        p = Project(files, base)
        self.addCleanup(p.cleanup)
        return p

    def test_a_dry_run_shows_the_plan_and_writes_nothing(self):
        p = self.project(NODE)
        res = p.install("--dry-run")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("factory/bin/job", res.stdout)
        self.assertIn("npm ci", res.stdout)
        self.assertEqual(p.status(), "")

    def test_it_installs_the_factory_and_adapts_it_to_the_project(self):
        p = self.project(NODE)
        res = p.install()
        self.assertEqual(res.returncode, 0, res.stderr)
        d = p.dir
        installer = load_script(INSTALL, "factory_installer")
        for rel in installer.MANIFEST:
            self.assertTrue((d / rel).exists(), rel)
        for rel in ("factory/LICENSE", "factory/NOTICE"):
            self.assertTrue((d / rel).exists(), rel)
        for rel in ("factory/bin/job", "factory/bin/guard", "factory/checks.sh", "factory/runtime/init-firewall.sh"):
            self.assertTrue(os.access(d / rel, os.X_OK), rel)
        checks = (d / "factory/checks.sh").read_text()
        self.assertEqual(re.findall(r"^(npm .*)$", checks, re.M), ["npm ci", "npm run lint", "npm test", "npm run build"])
        self.assertEqual(json.loads((d / "factory/config.json").read_text())["base_branch"], "main")
        ignored = p.git(d, "check-ignore", "factory/jobs/x", ".worktrees/x").stdout.split()
        self.assertEqual(ignored, ["factory/jobs/x", ".worktrees/x"])
        self.assertIn("## Software factory", (d / "AGENTS.md").read_text())
        self.assertIn("gh label create factory ", res.stdout, "labels are printed, not created, by default")
        self.assertIn("doctor", res.stdout)
        # Nothing is committed; the user reviews the change first.
        self.assertEqual(p.git(d, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        summary = subprocess.run([str(d / "factory/bin/job"), "summary"], cwd=d, env=p.env,
                                 capture_output=True, text=True)
        self.assertEqual(summary.returncode, 0, summary.stderr)

    def test_it_reads_the_base_branch_from_origin(self):
        p = self.project(NODE, base="trunk")
        self.assertEqual(p.install().returncode, 0)
        self.assertEqual(json.loads((p.dir / "factory/config.json").read_text())["base_branch"], "trunk")

    def test_it_adds_to_an_existing_claude_md_and_keeps_its_text(self):
        p = self.project({"CLAUDE.md": "# House rules\n\nUse tabs.\n"})
        self.assertEqual(p.install().returncode, 0)
        text = (p.dir / "CLAUDE.md").read_text()
        self.assertTrue(text.startswith("# House rules\n\nUse tabs.\n"))
        self.assertIn("## Software factory", text)
        self.assertFalse((p.dir / "AGENTS.md").exists())

    def test_it_refuses_a_dirty_tree(self):
        p = self.project(NODE)
        (p.dir / "scratch.txt").write_text("work in progress\n")
        res = p.install()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("uncommitted", res.stderr)
        self.assertEqual(p.status(), "?? scratch.txt\n")

    def test_it_never_overwrites_existing_files(self):
        p = self.project({**NODE, "factory/config.json": "{}\n"})
        res = p.install()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("factory/config.json", res.stderr)
        self.assertIn("--upgrade", res.stderr, "an installed factory is upgraded, not installed again")
        self.assertEqual(p.status(), "")
        self.assertFalse((p.dir / "factory/bin/job").exists())

    def test_it_refuses_a_folder_inside_a_repository(self):
        p = self.project(NODE)
        (p.dir / "sub").mkdir()
        res = subprocess.run([str(INSTALL), str(p.dir / "sub")], env=p.env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("root", res.stderr)

    def test_an_unknown_toolchain_gets_checks_that_fail_until_edited(self):
        p = self.project()
        self.assertEqual(p.install().returncode, 0)
        res = subprocess.run(["bash", "factory/checks.sh"], cwd=p.dir, env=p.env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("add the project's", res.stderr)


def sha(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


class VersionedInstallTest(unittest.TestCase):
    """A22 (DEP-1, DEP-3, SE-2): an install records its version and a hash of every file it placed, so
    an upgrade can replace what you didn't change, keep what you did, and an uninstall can take out
    exactly what the installer put in."""

    def project(self, files=None):
        p = Project(files)
        self.addCleanup(p.cleanup)
        return p

    def installed(self, *args):
        p = self.project(NODE)
        res = p.install(*args)
        self.assertEqual(res.returncode, 0, res.stderr)
        return p, res

    @staticmethod
    def commit(p, message="Install the software factory"):
        p.git(p.dir, "add", "-A")
        p.git(p.dir, "commit", "-qm", message)

    def record(self, p):
        return json.loads((p.dir / "factory/install.json").read_text())

    def test_an_install_records_its_version_and_every_file_it_placed(self):
        p, _ = self.installed()
        version = (ROOT / "factory/VERSION").read_text().strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual((p.dir / "factory/VERSION").read_text().strip(), version)
        record = self.record(p)
        self.assertEqual(record["version"], version)
        installer = load_script(INSTALL, "factory_installer_record")
        placed = set(installer.MANIFEST) | set(installer.EXTRA.values())
        self.assertEqual(set(record["files"]), placed)
        for rel, digest in record["files"].items():
            self.assertEqual(sha(p.dir / rel), digest, rel)
        for rel in ("factory/config.json", "factory/checks.sh", "factory/project.md"):
            self.assertNotIn(rel, record["files"], "the project's own files are never replaced")
            self.assertTrue((p.dir / rel).exists(), rel)
        self.assertIn("## Security hot spots", (p.dir / "factory/project.md").read_text())

    def test_the_manifest_covers_every_file_the_factory_runs(self):
        installer = load_script(INSTALL, "factory_installer_cover")
        for rel in ("factory/bin/dashboard", "factory/lib/stage_files.py", "factory/lib/job_cost.py",
                    "factory/lib/factory_config.py", "factory/lib/branch_diff.py", "factory/lib/issue_text.py",
                    "factory/VERSION"):
            self.assertIn(rel, installer.MANIFEST)
        self.assertNotIn("factory/config.json", installer.MANIFEST)
        self.assertFalse([rel for rel in installer.MANIFEST if "__pycache__" in rel or rel.startswith("factory/jobs")])

    def test_two_installs_of_one_release_are_identical(self):
        one, _ = self.installed()
        two, _ = self.installed()
        self.assertEqual(self.record(one), self.record(two))

    def test_an_upgrade_replaces_what_you_didnt_change_and_keeps_what_you_did(self):
        p, _ = self.installed()
        d = p.dir
        record = self.record(p)
        # Make this copy look like an older release: one file you never touched, at its old content...
        (d / "factory/bin/route").write_text("#!/usr/bin/env python3\nprint('old route')\n")
        record["files"]["factory/bin/route"] = sha(d / "factory/bin/route")
        # ...one you tailored by hand...
        (d / ".claude/agents/intake.md").write_text("my own intake prompt\n")
        # ...one this release adds, one it dropped, and one it dropped that you changed.
        (d / "factory/dashboard.html").unlink()
        del record["files"]["factory/dashboard.html"]
        (d / "factory/bin/retired").write_text("old\n")
        record["files"]["factory/bin/retired"] = sha(d / "factory/bin/retired")
        (d / "factory/bin/old-tool").write_text("old, then edited\n")
        record["files"]["factory/bin/old-tool"] = "sha256:" + hashlib.sha256(b"old\n").hexdigest()
        record["version"] = "0.3.9"
        (d / "factory/install.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        cfg = json.loads((d / "factory/config.json").read_text())
        del cfg["sizing"]
        (d / "factory/config.json").write_text(json.dumps(cfg, indent=2) + "\n")
        self.commit(p)

        res = p.install("--upgrade")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((d / "factory/bin/route").read_text(), (ROOT / "factory/bin/route").read_text())
        self.assertEqual((d / ".claude/agents/intake.md").read_text(), "my own intake prompt\n")
        self.assertIn(".claude/agents/intake.md", res.stdout)
        self.assertIn("diff -u", res.stdout)
        self.assertTrue((d / "factory/dashboard.html").exists())
        self.assertFalse((d / "factory/bin/retired").exists())
        self.assertEqual((d / "factory/bin/old-tool").read_text(), "old, then edited\n")
        self.assertIn("sizing", res.stdout, "config keys this release has and yours lacks are named")
        self.assertNotIn("sizing", json.loads((d / "factory/config.json").read_text()), "config.json is yours")
        after = self.record(p)
        self.assertEqual(after["version"], (ROOT / "factory/VERSION").read_text().strip())
        self.assertEqual(after["files"]["factory/bin/route"], sha(ROOT / "factory/bin/route"))
        self.assertEqual(after["files"][".claude/agents/intake.md"], sha(ROOT / ".claude/agents/intake.md"),
                         "a file you changed keeps the hash of what was installed, so it stays yours")
        self.assertNotIn("factory/bin/retired", after["files"])
        self.assertEqual(p.git(d, "rev-list", "--count", "HEAD").stdout.strip(), "2", "nothing is committed")

    def test_an_upgrade_needs_an_install_record_and_a_clean_tree(self):
        p, _ = self.installed()
        res = p.install("--upgrade")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("uncommitted", res.stderr)
        (p.dir / "factory/install.json").unlink()
        self.commit(p)
        res = p.install("--upgrade")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("install.json", res.stderr)

    def test_an_uninstall_takes_out_what_it_put_in_and_keeps_your_changes(self):
        p, _ = self.installed("--owner", "@alice")
        d = p.dir
        self.commit(p)
        (d / ".claude/agents/builder.md").write_text("tailored\n")
        self.commit(p, "Tailor the builder")
        res = p.install("--uninstall")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse((d / "factory/bin/job").exists())
        self.assertFalse((d / "factory/install.json").exists())
        self.assertFalse((d / "factory/VERSION").exists())
        self.assertEqual((d / ".claude/agents/builder.md").read_text(), "tailored\n")
        self.assertIn(".claude/agents/builder.md", res.stdout)
        for rel in ("factory/config.json", "factory/checks.sh", "factory/project.md"):
            self.assertTrue((d / rel).exists(), f"{rel} is the project's: kept")
        self.assertNotIn("Software factory", (d / "AGENTS.md").read_text() if (d / "AGENTS.md").exists() else "")
        self.assertNotIn("/.worktrees/", (d / ".gitignore").read_text() if (d / ".gitignore").exists() else "")
        self.assertFalse((d / ".github/CODEOWNERS").exists(), "the installer created it; nothing else was in it")
        self.assertFalse((d / "factory/bin").exists(), "empty folders go too")

    def test_codeowners_name_the_factorys_owners(self):
        p = self.project({**NODE, "CODEOWNERS": "* @everyone\n"})
        res = p.install("--owner", "@alice", "--owner", "@acme/factory-owners")
        self.assertEqual(res.returncode, 0, res.stderr)
        text = (p.dir / "CODEOWNERS").read_text()
        self.assertTrue(text.startswith("* @everyone\n"), "the project's own lines stay first")
        self.assertIn("/factory/ @alice @acme/factory-owners\n", text)
        self.assertIn("/.claude/ @alice @acme/factory-owners\n", text)
        self.assertFalse((p.dir / ".github/CODEOWNERS").exists())
        bad = self.project(NODE).install("--owner", "alice")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("--owner", bad.stderr)

    def test_without_owners_the_installer_says_who_should_be_named(self):
        _, res = self.installed()
        self.assertIn("CODEOWNERS", res.stdout)

    def test_doctor_checks_the_install_record(self):
        p, _ = self.installed()
        doctor = lambda: subprocess.run([str(p.dir / "factory/bin/doctor"), "--offline"], cwd=p.dir, env=p.env,
                                        capture_output=True, text=True).stdout.splitlines()
        line = lambda: [l for l in doctor() if "as installed" in l or "since install" in l or "missing:" in l]
        self.assertTrue(line()[0].startswith("ok"), line())
        (p.dir / "factory/bin/route").write_text("edited\n")
        self.assertTrue(any(l.startswith("warn") and "factory/bin/route" in l for l in line()), line())
        (p.dir / "factory/bin/gate").unlink()
        self.assertTrue(any(l.startswith("FAIL") and "factory/bin/gate" in l for l in line()), line())


class DetectChecksTest(unittest.TestCase):
    """The first draft of checks.sh comes from the project's own files."""

    def setUp(self):
        self.installer = load_script(INSTALL, "factory_installer_detect")
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-detect-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def detect(self, files):
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        for rel, text in files.items():
            (d / rel).write_text(text)
        return self.installer.detect_checks(d)[0]

    def test_node_package_managers(self):
        scripts = json.dumps({"scripts": {"test": "vitest run", "typecheck": "tsc --noEmit"}})
        self.assertEqual(self.detect({"package.json": scripts, "pnpm-lock.yaml": ""}),
                         ["pnpm install --frozen-lockfile", "pnpm run typecheck", "pnpm run test"])
        self.assertEqual(self.detect({"package.json": scripts, "yarn.lock": ""})[0], "yarn install --frozen-lockfile")
        self.assertEqual(self.detect({"package.json": scripts})[0], "npm install")

    def test_python(self):
        cmds = self.detect({"pyproject.toml": "[tool.ruff]\nline-length = 100\n[tool.pytest.ini_options]\n",
                            "requirements.txt": "requests==2.32.3\n"})
        self.assertIn('"$venv/bin/python" -m pip install --quiet -r requirements.txt', cmds)
        self.assertIn('"$venv/bin/python" -m ruff check .', cmds)
        self.assertEqual(cmds[-1], '"$venv/bin/python" -m pytest -q')

    def test_go_rust_and_make(self):
        self.assertEqual(self.detect({"go.mod": "module x\n"}), ["go vet ./...", "go test ./..."])
        self.assertEqual(self.detect({"Cargo.toml": "[package]\n"}), ["cargo test --locked"])
        self.assertEqual(self.detect({"Makefile": "test:\n\tpytest\n"}), ["make test"])
        self.assertEqual(self.detect({}), [])


class DocsTest(unittest.TestCase):
    def test_install_md_lists_every_file_the_installer_copies(self):
        installer = load_script(INSTALL, "factory_installer_docs")
        text = (ROOT / "INSTALL.md").read_text()
        missing = [rel for rel in installer.MANIFEST if rel not in text]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
