"""GitHub App identity for the factory's own scripts.

With `github_app` set in config.json, `job` and `doctor` stop using your personal
gh login. Each command mints a one-hour installation token: the App's private key
signs a short-lived JWT (openssl does the RS256 signature, so no Python package
is needed), and GitHub exchanges it for a token limited to the permissions below
and, when origin is on GitHub, to this one repository. The token goes only to the
scripts' own gh and git subprocesses, never into the agents' environment.

Agents can't read the key through the guard (`*.pem` is a secret file), but code
an agent writes and runs has your user's file access: keep the key outside the
repository, readable only by you, and run the factory in its runtime container
(factory/runtime) so the key is the only credential that code could reach.
"""

import base64
import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# What the factory needs, and nothing more: push branches, comment and label, open and merge PRs,
# post the gate's commit status, and read CI. A token can't hold more than the App was granted.
PERMISSIONS = {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write",
               "checks": "read"}
DEFAULT_KEY = "~/.config/software-factory/github-app.pem"
GITHUB_REMOTE = re.compile(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)[^/]+/([^/]+?)(?:\.git)?/?$")


class AppError(Exception):
    pass


def configured(config):
    return bool((config.get("github_app") or {}).get("app_id"))


def key_path(config):
    return Path(os.path.expanduser((config.get("github_app") or {}).get("private_key") or DEFAULT_KEY))


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id, key):
    """A JWT that proves the App's identity for up to nine minutes (GitHub allows ten), backdated a
    minute for clock drift."""
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    try:
        res = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key)], input=signing_input,
                             capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AppError(f"couldn't run openssl to sign the GitHub App JWT: {e}")
    if res.returncode != 0:
        raise AppError(f"openssl couldn't sign the GitHub App JWT with {key}: "
                       f"{res.stderr.decode(errors='replace').strip()}")
    return f"{header}.{claims}.{b64url(res.stdout)}"


def origin_repo(repo_root):
    """The repository name of a github.com origin (to limit the token to it), or None."""
    res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_root, capture_output=True, text=True)
    m = GITHUB_REMOTE.match(res.stdout.strip()) if res.returncode == 0 else None
    return m.group(1) if m else None


def mint(config, repo_root):
    """GitHub's reply for a new installation token: token, expires_at, permissions and, when the
    token is limited to named repositories, repositories. Raises AppError; never falls back."""
    app = config["github_app"]
    key = key_path(config)
    if not key.is_file():
        raise AppError(f"the GitHub App private key {key} doesn't exist (github_app.private_key)")
    body = {"permissions": PERMISSIONS}
    name = origin_repo(repo_root)
    if name:
        body["repositories"] = [name]
    url = (f"{(app.get('api_url') or 'https://api.github.com').rstrip('/')}"
           f"/app/installations/{int(app['installation_id'])}/access_tokens")
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(), headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {app_jwt(app['app_id'], key)}",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json", "User-Agent": "software-factory"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            reply = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise AppError(f"GitHub didn't issue an installation token for App {app['app_id']} "
                       f"(installation {app['installation_id']}): HTTP {e.code} {detail}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AppError(f"GitHub didn't issue an installation token for App {app['app_id']}: {e}")
    if not isinstance(reply, dict) or not reply.get("token"):
        raise AppError(f"GitHub's reply for App {app['app_id']} had no installation token")
    return reply


def git_options(helper):
    """git -c options that send the token over HTTPS for github.com, whatever origin's URL says:
    the credential helper reads it from FACTORY_GIT_TOKEN, so it never appears in argv."""
    return ["-c", "credential.helper=", "-c", f"credential.helper=!{shlex.quote(str(helper))}",
            "-c", "url.https://github.com/.insteadOf=git@github.com:",
            "-c", "url.https://github.com/.insteadOf=ssh://git@github.com/"]
