"""The setup command (`./install <project>`): it copies the factory into a project, drafts
factory/checks.sh from the project's own toolchain, and refuses anything it could get wrong.
Standard library only; each test builds a throwaway project with a local bare origin.

    python3 -m unittest discover -s tests -v
"""

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
