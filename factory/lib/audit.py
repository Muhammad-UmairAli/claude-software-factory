"""The factory's tamper-evident audit log.

Every job event and every guard decision is also appended here, as one JSON
line per record, in factory/jobs/audit/. Each record carries the hash of the
one before it, so editing or deleting a record breaks the chain from that point
on, and `job audit` (and doctor) say where. The current file rotates by size
into audit-<first seq>.jsonl; rotated files older than the retention period are
deleted, and the chain records each deletion so verification still starts
cleanly. Cutting records off the end can't be seen locally: ship the log to a
collector (`job audit ship`) so a copy outside this machine anchors it.

Standard library only. Appends take an exclusive lock, so the guard (one
process per tool call, several agents at once) and job never interleave.
"""

import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

GENESIS = "0" * 64
CURRENT = "audit.jsonl"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(record):
    """The record's hash: sha256 over its canonical JSON without the hash field."""
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def last_line(path):
    """The last non-empty line of a file, read from its end, or None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            lines = [l for l in f.read().splitlines() if l.strip()]
    except OSError:
        return None
    return lines[-1].decode(errors="replace") if lines else None


class Log:
    def __init__(self, folder, config=None):
        cfg = (config or {}).get("audit") or {}
        self.folder = Path(folder)
        self.max_bytes = int(float(cfg.get("max_mb", 10)) * 1_000_000)
        self.keep_days = float(cfg.get("keep_days", 365))

    def files(self):
        """Rotated files oldest first, then the current one."""
        rotated = sorted(self.folder.glob("audit-*.jsonl"))
        current = self.folder / CURRENT
        return rotated + ([current] if current.exists() else [])

    def head(self):
        """(seq, hash) of the newest record, or (0, GENESIS)."""
        for path in reversed(self.files()):
            line = last_line(path)
            if line:
                try:
                    rec = json.loads(line)
                    return int(rec["seq"]), str(rec["hash"])
                except (ValueError, KeyError, TypeError):
                    raise ValueError(f"the last record of {path.name} isn't readable; run `job audit`")
        return 0, GENESIS

    def append(self, source, event, **fields):
        """Chain one record onto the log and return it."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.folder / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            seq, prev = self.head()
            current = self.folder / CURRENT
            if current.exists() and current.stat().st_size >= self.max_bytes:
                self._rotate(current)
                seq, prev = self.head()
            fields = {k: v for k, v in fields.items() if k not in {"seq", "ts", "source", "event", "prev", "hash"}}
            record = {"seq": seq + 1, "ts": now(), "source": source, "event": event, **fields, "prev": prev}
            record["hash"] = digest(record)
            with open(current, "a") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")
            return record

    def _rotate(self, current):
        """Move the full current file aside, then delete rotated files past the retention period, chaining
        a record that names the last hash each deleted file ended on."""
        first = json.loads(current.open().readline())["seq"]
        current.rename(self.folder / f"audit-{first:012d}.jsonl")
        cutoff = time.time() - self.keep_days * 86400
        rotated = sorted(self.folder.glob("audit-*.jsonl"))
        for path in rotated[:-1]:  # never the file just rotated, so the chain keeps a starting point
            if path.stat().st_mtime < cutoff:
                end = json.loads(last_line(path) or "{}")
                path.unlink()
                seq, prev = self.head()
                record = {"seq": seq + 1, "ts": now(), "source": "audit", "event": "pruned", "file": path.name,
                          "last_seq": end.get("seq"), "last_hash": end.get("hash"), "prev": prev}
                record["hash"] = digest(record)
                with open(self.folder / CURRENT, "a") as f:
                    f.write(json.dumps(record, sort_keys=True) + "\n")

    def records(self):
        """Every record, oldest first, with its file name; unreadable lines come back as None."""
        for path in self.files():
            with open(path, errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if line.strip():
                        try:
                            yield path.name, n, json.loads(line)
                        except ValueError:
                            yield path.name, n, None

    def verify(self):
        """(records checked, problem or None). The chain starts from GENESIS, or right after files that
        retention deleted, which only counts when a `pruned` record names the hash they ended on."""
        count, prev, expect, first, pruned = 0, None, None, None, set()
        for name, n, rec in self.records():
            where = f"{name} line {n}"
            if not isinstance(rec, dict) or "hash" not in rec or "prev" not in rec or "seq" not in rec:
                return count, f"{where} isn't a readable audit record"
            if rec["hash"] != digest(rec):
                return count, f"record {rec['seq']} ({where}) was changed after it was written"
            if prev is None:
                first = (rec["prev"], rec["seq"], where)
            elif rec["prev"] != prev or rec["seq"] != expect:
                return count, (f"the chain breaks before record {rec['seq']} ({where}): records were removed or "
                               "reordered")
            if rec.get("event") == "pruned":
                pruned.add(rec.get("last_hash"))
            prev, expect = rec["hash"], rec["seq"] + 1
            count += 1
        if first and first[0] != GENESIS and first[0] not in pruned:
            return count, (f"records before {first[1]} ({first[2]}) are missing, and the log records no "
                           "retention that removed them")
        return count, None

    def trend(self, days=14):
        """{date: {event: count}} for the last `days` days, newest first."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        out = {}
        for _, _, rec in self.records():
            if isinstance(rec, dict) and str(rec.get("ts", ""))[:10] >= since:
                key = rec.get("event") if rec.get("source") != "guard" else f"guard {rec.get('decision', '?')}"
                day = out.setdefault(rec["ts"][:10], {})
                day[key] = day.get(key, 0) + 1
        return dict(sorted(out.items(), reverse=True))
