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

from __future__ import annotations

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
from typing import Any, Optional, Union

Config = dict[str, Any]

# What the factory needs, and nothing more: push branches, comment and label, open and merge PRs,
# post the gate's commit status, and read CI. A token can't hold more than the App was granted.
PERMISSIONS = {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write",
               "checks": "read"}
DEFAULT_KEY = "~/.config/software-factory/github-app.pem"
GITHUB_REMOTE = re.compile(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)[^/]+/([^/]+?)(?:\.git)?/?$")


class AppError(Exception):
    pass


def configured(config: Config) -> bool:
    return bool((config.get("github_app") or {}).get("app_id"))


def key_path(config: Config) -> Path:
    return Path(os.path.expanduser((config.get("github_app") or {}).get("private_key") or DEFAULT_KEY))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: Any, key: Path) -> str:
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


def origin_repo(repo_root: Union[str, Path]) -> Optional[str]:
    """The repository name of a github.com origin (to limit the token to it), or None."""
    res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_root, capture_output=True, text=True)
    m = GITHUB_REMOTE.match(res.stdout.strip()) if res.returncode == 0 else None
    return m.group(1) if m else None


def app_call(config: Config, method: str, path: str, what: str, body: Optional[dict[str, Any]] = None) -> Any:
    """GitHub's JSON reply to an API call made as the App itself (signed with the JWT). `what` says
    what GitHub didn't do when it fails, as in "GitHub didn't <what>"."""
    app = config["github_app"]
    key = key_path(config)
    if not key.is_file():
        raise AppError(f"the GitHub App private key {key} doesn't exist (github_app.private_key)")
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {app_jwt(app['app_id'], key)}",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "software-factory"}
    data: Optional[bytes] = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    url = f"{(app.get('api_url') or 'https://api.github.com').rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method, data=data, headers=headers),
                                    timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise AppError(f"GitHub didn't {what}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AppError(f"GitHub didn't {what}: {e}")


def mint(config: Config, repo_root: Union[str, Path]) -> dict[str, Any]:
    """GitHub's reply for a new installation token: token, expires_at, permissions and, when the
    token is limited to named repositories, repositories. Raises AppError; never falls back."""
    app = config["github_app"]
    body: dict[str, Any] = {"permissions": PERMISSIONS}
    name = origin_repo(repo_root)
    if name:
        body["repositories"] = [name]
    reply = app_call(config, "POST", f"/app/installations/{int(app['installation_id'])}/access_tokens",
                     f"issue an installation token for App {app['app_id']} (installation {app['installation_id']})",
                     body)
    if not isinstance(reply, dict) or not reply.get("token"):
        raise AppError(f"GitHub's reply for App {app['app_id']} had no installation token")
    return reply


SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62})")


def bot_slug(config: Config) -> str:
    """The App's slug, which names the bot user its comments come from: github_app.slug when set,
    else GitHub's GET /app. GitHub's GraphQL API shows that bot as <slug>, its REST API as <slug>[bot]."""
    app = config["github_app"]
    slug = app.get("slug") or (app_call(config, "GET", "/app", f"describe App {app['app_id']}") or {}).get("slug")
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise AppError(f"couldn't tell the slug of App {app['app_id']}; set github_app.slug in config.json")
    return slug


def git_options(helper: Union[str, Path]) -> list[str]:
    """git -c options that send the token over HTTPS for github.com, whatever origin's URL says:
    the credential helper reads it from FACTORY_GIT_TOKEN, so it never appears in argv."""
    return ["-c", "credential.helper=", "-c", f"credential.helper=!{shlex.quote(str(helper))}",
            "-c", "url.https://github.com/.insteadOf=git@github.com:",
            "-c", "url.https://github.com/.insteadOf=ssh://git@github.com/"]
