#!/usr/bin/env python3
"""A stand-in for the `gh` CLI, for the factory's tests.

State lives in the JSON file named by $FAKE_GH_STATE: issues, PRs,
collaborators, and a log of every call (`calls`). Only the subcommands the
factory scripts use are implemented; anything else exits 1 so a test notices.
"""

import json
import os
import sys


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def main(argv):
    path = os.environ["FAKE_GH_STATE"]
    with open(path) as f:
        state = json.load(f)
    state["calls"].append(argv)
    # Which credential each call used: GH_TOKEN when a script sets it, else your own gh login.
    state.setdefault("tokens", []).append(os.environ.get("GH_TOKEN", ""))
    out, code = "", 0
    cmd = argv[:2]

    if any(" ".join(argv).startswith(prefix) for prefix in state.get("fail", [])):
        # A test asked this call to fail, as a network error would.
        sys.stderr.write(f"fake gh: injected failure for {argv}\n")
        code = 1
    elif cmd == ["api", "user"]:
        out = "operator\n"
    elif argv[:3] == ["api", "-i", "user"]:
        # Classic and OAuth tokens carry X-OAuth-Scopes; fine-grained and app tokens don't.
        scopes = state.get("token_scopes", "")
        out = "HTTP/2.0 200 OK\nContent-Type: application/json\n" + (f"X-Oauth-Scopes: {scopes}\n" if scopes else "")
        out += '\n{"login": "operator"}\n'
    elif cmd == ["auth", "status"]:
        out = "Logged in to github.com\n"
    elif cmd == ["label", "list"]:
        out = json.dumps([{"name": n} for n in ("factory", "factory:needs-info", "factory:needs-human",
                                                  "factory:approved")])
    elif argv[0] == "api" and argv[1].endswith("/permission"):
        login = argv[1].split("/")[-2]
        if login in state["collaborators"]:
            out = json.dumps({"permission": "write", "role_name": "write"})
        else:
            code = 1
    elif argv[0] == "api" and "/protection" in argv[1]:
        sys.stderr.write(state.get("protection_error", ""))
        code = 1
    elif argv[:3] == ["api", "-X", "POST"] and "/statuses/" in argv[3]:
        # A commit status: kept per commit, as GitHub shows them on the PR.
        sha = argv[3].rsplit("/", 1)[1]
        fields = dict(a.split("=", 1) for a in argv[4:] if "=" in a)
        state.setdefault("statuses", {}).setdefault(sha, []).append(fields)
        out = "{}"
    elif argv[0] == "api" and "/check-runs" in argv[1]:
        # The repository's CI: the same check runs on every commit, as a test sets them.
        runs = state.get("check_runs", [])
        out = json.dumps({"total_count": len(runs), "check_runs": runs})
    elif argv[0] == "api" and argv[1].split("?")[0].endswith("/status"):
        sha = argv[1].split("?")[0].split("/")[-2]
        out = json.dumps({"statuses": state.get("statuses", {}).get(sha, [])})
    elif cmd == ["issue", "view"]:
        out = json.dumps(state["issues"][argv[2]])
    elif cmd == ["issue", "list"]:
        out = json.dumps([{"number": i["number"], "title": i["title"], "author": i["author"]}
                          for i in state["issues"].values() if i["state"] == "OPEN"])
    elif cmd == ["issue", "comment"]:
        issue = state["issues"][argv[2]]
        issue["comments"].append({"author": {"login": "operator"}, "body": opt(argv, "--body"),
                                  "createdAt": "2026-01-01T00:00:00Z"})
    elif cmd == ["issue", "edit"]:
        labels = state["issues"][argv[2]]["labels"]
        for name in (opt(argv, "--remove-label") or "").split(","):
            labels[:] = [l for l in labels if l["name"] != name]
        add = opt(argv, "--add-label")
        if add:
            labels.append({"name": add})
    elif cmd == ["pr", "list"]:
        head = opt(argv, "--head")
        out = json.dumps([{"number": p["number"], "url": p["url"]} for p in state["prs"].values()
                          if p["head"] == head and p["state"] == "OPEN"])
    elif cmd == ["pr", "create"]:
        n = str(100 + len(state["prs"]))
        state["prs"][n] = {"number": int(n), "url": f"https://example.test/pull/{n}", "state": "OPEN",
                           "isDraft": "--draft" in argv, "head": opt(argv, "--head"),
                           "title": opt(argv, "--title"), "body": opt(argv, "--body")}
        out = state["prs"][n]["url"] + "\n"
    elif cmd == ["pr", "view"]:
        pr = state["prs"][argv[2]]
        fields = opt(argv, "--json").split(",")
        out = json.dumps({k: pr[k] for k in fields})
    elif cmd == ["pr", "comment"]:
        state["prs"][argv[2]].setdefault("comments", []).append(opt(argv, "--body"))
    elif cmd == ["pr", "ready"]:
        state["prs"][argv[2]]["isDraft"] = False
    elif cmd == ["pr", "merge"]:
        state["prs"][argv[2]]["state"] = "MERGED"
    elif cmd == ["pr", "close"]:
        state["prs"][argv[2]]["state"] = "CLOSED"
    else:
        sys.stderr.write(f"fake gh: unsupported {argv}\n")
        code = 1

    with open(path, "w") as f:
        json.dump(state, f)
    sys.stdout.write(out)
    sys.exit(code)


if __name__ == "__main__":
    main(sys.argv[1:])
