"""What the factory's agents and orchestrator cost, read from Claude Code's transcripts (MON-3).

Each subagent's messages, with model and token usage, are in
<config>/projects/<project>/<session>/subagents/agent-<id>.jsonl. Every factory
prompt starts with `Job folder: <path>`, which ties a transcript to its job
across sessions. A session that ran such agents is a factory run: its own
transcript, <session>.jsonl next to that folder, is the orchestrator's, and its
cost is shared evenly by the jobs it worked on. Other sessions aren't counted.

Transcripts only grow, so one whose size and mtime match the cache isn't read
again; a change to the prices empties the cache. The transcript format isn't a
documented interface: what doesn't parse is skipped, never allowed to block a
state change, and counted as `unreadable`, so a cost reads as possibly low
instead of quietly low. Unknown models are reported. Nothing here keeps state:
the caller passes its folders, prices and the job-folder pattern.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

Prices = dict[str, Any]
Entry = dict[str, Any]
NO_USAGE_LINES = 5  # a transcript this long with no usage record at all has changed format


def transcripts_dir(repo: Path, config_dir: Optional[str]) -> Path:
    """Where Claude Code keeps this repository's transcripts."""
    root = Path(config_dir) if config_dir else Path.home() / ".claude"
    return root / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(repo))


def prompt_text(line: str) -> str:
    content = json.loads(line)["message"]["content"]
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


def model_prices(model: Optional[str], prices: Prices) -> Optional[dict[str, Any]]:
    """Prices for a transcript's model id; tolerates a dated snapshot suffix."""
    found = prices.get(model or "") or prices.get(re.sub(r"-\d{8}$", "", model or ""))
    return found if isinstance(found, dict) else None


def message_cost(usage: dict[str, Any], p: dict[str, Any]) -> float:
    """USD for one assistant message's usage at price table p (per million tokens)."""
    prompt = (usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
              + usage.get("cache_read_input_tokens", 0))
    tier = p.get("long_prompt")
    if tier and prompt > tier["over_tokens"]:
        p = tier
    split = usage.get("cache_creation") or {}
    write_1h = split.get("ephemeral_1h_input_tokens", 0)
    write_5m = usage.get("cache_creation_input_tokens", 0) - write_1h
    return float(usage.get("input_tokens", 0) * p["input"]
                 + usage.get("output_tokens", 0) * p["output"]
                 + write_5m * p["cache_write_5m"]
                 + write_1h * p["cache_write_1h"]
                 + usage.get("cache_read_input_tokens", 0) * p["cache_read"]) / 1e6


def still_good(entry: Any, st: os.stat_result) -> bool:
    """A cache entry for a file with this size and mtime, in the shape this version writes."""
    return (isinstance(entry, dict) and entry.get("mtime_ns") == st.st_mtime_ns and entry.get("size") == st.st_size
            and (entry.get("job") is None or isinstance(entry.get("job"), str))
            and isinstance(entry.get("usd"), (int, float)) and isinstance(entry.get("unpriced"), list)
            and isinstance(entry.get("unreadable"), int))


def transcript_cost(path: Path, prices: Prices, job_folder: re.Pattern[str], linked: bool = True) -> Optional[Entry]:
    """{"job", "usd", "unpriced", "unreadable"} for one transcript, or None when the file can't be read.

    A subagent's transcript (linked) counts only when its prompt names a job folder: job is None
    otherwise. `unreadable` counts the complete lines that aren't JSON objects and the usage records in
    a shape this version doesn't know, plus one when a transcript of NO_USAGE_LINES or more lines has no
    usage record at all. A last line without its newline is still being written: it isn't counted, and
    the next scan reads the grown file again."""
    usage_by_message: dict[Any, tuple[Any, Any]] = {}
    unreadable, lines = 0, 0
    m: Optional[re.Match[str]] = None
    try:
        with path.open(errors="replace") as f:
            if linked:
                first = f.readline()
                try:
                    m = job_folder.search(prompt_text(first)) if first else None
                except (ValueError, KeyError, TypeError, AttributeError):
                    m = None
                if not m:
                    return {"job": None, "usd": 0.0, "unpriced": [], "unreadable": 0}
            for n, line in enumerate(f):
                if not line.endswith("\n"):
                    break
                lines += 1
                try:
                    entry = json.loads(line)
                except ValueError:
                    unreadable += 1
                    continue
                if not isinstance(entry, dict):
                    unreadable += 1
                    continue
                msg = entry.get("message")
                if entry.get("type") == "assistant" and isinstance(msg, dict) and msg.get("usage"):
                    usage_by_message[msg.get("id") or n] = (msg.get("model"), msg["usage"])
    except OSError:
        return None
    if lines >= NO_USAGE_LINES and not usage_by_message:
        unreadable += 1
    usd = 0.0
    unpriced: set[str] = set()
    for model, u in usage_by_message.values():
        try:
            tokens = (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                      + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0))
            if tokens == 0:
                continue
            p = model_prices(model, prices)
            if p is None:
                unpriced.add(str(model))
                continue
            usd += message_cost(u, p)
        except (AttributeError, TypeError, ValueError, KeyError):
            unreadable += 1  # a usage record in a shape this version doesn't know
    return {"job": m.group(1) if m else None, "usd": usd, "unpriced": sorted(unpriced), "unreadable": unreadable}


def scan(root: Path, prices: Prices, cache_path: Path, job_folder: re.Pattern[str]) -> dict[str, Any]:
    """{"jobs": {job id: {"usd", "unpriced", "unreadable", "orchestrator"}}, "runs": {session: {"usd", "jobs"}}}
    from the transcripts under root, reading only what changed since cache_path was written."""
    key = hashlib.sha256(json.dumps(prices, sort_keys=True).encode()).hexdigest()
    try:
        cache = json.loads(cache_path.read_text())
        cached = cache["files"] if cache.get("prices") == key else {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        cached = {}
    if not isinstance(cached, dict):
        cached = {}
    files: dict[str, Entry] = {}
    jobs: dict[str, dict[str, Any]] = {}
    sessions: dict[str, set[str]] = {}

    def read(path: Path, linked: bool) -> Optional[Entry]:
        try:
            st = path.stat()
        except OSError:
            return None
        entry = cached.get(str(path))
        if isinstance(entry, dict) and still_good(entry, st):
            files[str(path)] = entry
            return entry
        fresh = transcript_cost(path, prices, job_folder, linked)
        if fresh is None:
            return None  # unreadable now; not cached, so the next scan tries again
        fresh.update(mtime_ns=st.st_mtime_ns, size=st.st_size)
        files[str(path)] = fresh
        return fresh

    def add(job_id: str, usd: float = 0.0, unpriced: Any = (), unreadable: int = 0, orchestrator: float = 0.0) -> None:
        c = jobs.setdefault(job_id, {"usd": 0.0, "unpriced": set(), "unreadable": 0, "orchestrator": 0.0})
        c["usd"] += usd
        c["unpriced"] |= set(unpriced)
        c["unreadable"] += unreadable
        c["orchestrator"] += orchestrator

    for path in root.glob("*/subagents/agent-*.jsonl"):
        entry = read(path, linked=True)
        if entry and entry.get("job"):
            add(entry["job"], entry["usd"], entry["unpriced"], entry["unreadable"])
            sessions.setdefault(path.parent.parent.name, set()).add(entry["job"])
    runs: dict[str, dict[str, Any]] = {}
    for session, ids in sorted(sessions.items()):
        run = read(root / f"{session}.jsonl", linked=False)
        if run is None:
            continue
        runs[session] = {"usd": run["usd"], "jobs": sorted(ids)}
        for job_id in ids:
            add(job_id, unpriced=run["unpriced"], unreadable=run["unreadable"], orchestrator=run["usd"] / len(ids))
    if files != cached:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"prices": key, "files": files}))
            tmp.replace(cache_path)
        except OSError:
            pass  # a cache that can't be written only costs the next command a full scan
    return {"jobs": jobs, "runs": runs}


def warning(job_id: str, unreadable: int) -> Optional[str]:
    """The line that says a job's cost may be low, or None."""
    if not unreadable:
        return None
    return (f"the cost of {job_id} may be low: {unreadable} transcript record{'s' if unreadable != 1 else ''} "
            "couldn't be read. Claude Code's transcript format may have changed; check the cost against your "
            "usage before trusting max_cost_usd_per_job")
