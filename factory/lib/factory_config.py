"""factory/config.json, read the same way by every factory script (SE-1).

`read` returns the file as written, for doctor to check. `load` adds the defaults
for keys that older configs don't have, for the scripts that act on it. Neither
keeps state: a script passes its own factory folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Config = dict[str, Any]


def read(factory: Path) -> Config:
    """config.json as written. Raises OSError or ValueError when it's missing or isn't a JSON object."""
    data = json.loads((factory / "config.json").read_text())
    if not isinstance(data, dict):
        raise ValueError("factory/config.json isn't a JSON object")
    return data


def load(factory: Path) -> Config:
    """config.json, with defaults for keys that older configs don't have."""
    cfg = read(factory)
    cfg.setdefault("concurrency", 1)
    cfg.setdefault("max_rounds", 3)
    cfg.setdefault("max_cost_usd_per_job", None)
    cfg.setdefault("auto_merge", {"enabled": False, "max_risk": "low"})
    cfg.setdefault("issue_authors", "collaborators")
    cfg.setdefault("protected_paths", [])
    cfg.setdefault("escalate_paths", [])
    cfg.setdefault("sensitive_paths", [])
    cfg.setdefault("hold_paths", [])
    cfg.setdefault("github_app", {})
    cfg["audit"] = {"max_mb": 10, "keep_days": 365, "forward_url": None,
                    "forward_token_env": "FACTORY_AUDIT_TOKEN", **(cfg.get("audit") or {})}
    cfg.setdefault("models", {})
    cfg.setdefault("labels", {})
    cfg["labels"].setdefault("approved", "factory:approved")
    cfg.setdefault("stale_after_days", 7)
    cfg.setdefault("prices_owner", None)
    # How an unattended merge waits for the repository's CI on the gated commit.
    cfg["ci"] = {"required_checks": [], "wait_minutes": 30, "grace_seconds": 60, "poll_seconds": 15,
                 **(cfg.get("ci") or {})}
    return cfg
