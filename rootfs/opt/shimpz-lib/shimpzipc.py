"""shimpzipc — the SINGLE definition of Shimpz's request/response IPC over `$SHIMPZ_HOME/ipc`.

The blocking CLIs (shimpz-ask, shimpz-approve, shimpz-captcha) drop a `<rid>.req` file; the gateway
(or shimpz-run's `--auto` autopilot) owns the Telegram poll, shows the buttons, and writes back a
`<rid>.resp`. This module is that protocol in one place so each CLI no longer reimplements the
write/poll/cleanup loop. SHIMPZ_IPC_DIR overrides the dir so a debug harness can intercept.
"""

import contextlib
import json
import os
import time
import uuid
from pathlib import Path


def atomic_write(path, text):
    """Write `text` to `path` atomically: a full temp file in the same dir, then a single rename.

    The peer polls these files every second, so a plain write_text can be caught mid-write — a torn
    `.req` read gets the file renamed `.bad` (destroyed forever) and a torn `.resp` read yields a
    wrong/None value. A rename is atomic on the same filesystem, so a reader sees all-or-nothing.
    """
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def pending(ipc_dir):
    """RESPONDER side: yield `(rid, req_path, payload)` for each queued `*.req` in `ipc_dir` (sorted).

    A file that isn't valid JSON is renamed `.bad` and skipped (never yielded). The gateway's
    ipc_watcher and shimpz-run's autopilot both iterate this instead of re-implementing the glob/parse/
    .bad scaffolding. The caller renders/answers the request, then `mark_sent(req_path)`.
    """
    for req in sorted(Path(ipc_dir).glob("*.req")):
        try:
            payload = json.loads(req.read_text())
            # Valid JSON that isn't an OBJECT ([], "x", 42) would raise AttributeError at
            # payload.get() below — INSIDE this generator. The gateway's watcher catches it but
            # never quarantines the file, so it re-poisons every 1s pass and (glob is sorted)
            # starves every later-sorting rid forever. Quarantine it like a parse failure.
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
        except ValueError, OSError:  # JSONDecodeError is a ValueError
            with contextlib.suppress(OSError):
                req.rename(req.with_suffix(".bad"))
            continue
        yield (payload.get("id") or req.stem), req, payload


def answer(ipc_dir, rid, payload):
    """RESPONDER side: write the `<rid>.resp` the blocked requester (see `request`) is polling for.

    Only writes if the request is STILL PENDING (a `.req`/`.sent` exists) → a late/stale tap for a
    requester that already timed out and cleaned up does NOT leave an orphan `.resp` piling up in the
    dir. Returns True iff it wrote.
    """
    ipc = Path(ipc_dir)
    if not ((ipc / f"{rid}.sent").exists() or (ipc / f"{rid}.req").exists()):
        return False
    atomic_write(ipc / f"{rid}.resp", json.dumps(payload))
    return True


def mark_sent(req_path):
    """Rename a handled `<rid>.req` → `.sent` (best-effort; a vanished file is fine).

    The responder marks a request sent BEFORE it delivers it (see the gateway's ipc_watcher), so a
    crash/restart mid-delivery can never re-deliver it — dequeue is at-most-once. `pending()` globs
    only `*.req`, so once this rename lands the request is out of the queue.
    """
    with contextlib.suppress(OSError):
        Path(req_path).rename(Path(req_path).with_suffix(".sent"))


def mark_unsent(req_path):
    """Undo mark_sent: rename `<rid>.sent` back to `.req` so `pending()` retries it next pass.

    For a TRANSIENT delivery failure (the send raised, e.g. a Telegram hiccup) AFTER the request was
    dequeued — requeue it so a genuinely-undelivered request isn't silently lost, WITHOUT the
    re-delivery a crash-window would cause. Best-effort; a missing/vanished `.sent` is a no-op.
    """
    with contextlib.suppress(OSError):
        Path(req_path).with_suffix(".sent").rename(Path(req_path))


def _ipc_dir():
    home = os.environ.get("SHIMPZ_HOME", "/config/.shimpz")
    d = Path(os.environ.get("SHIMPZ_IPC_DIR") or (Path(home) / "ipc"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def request(payload, timeout, key):
    """Drop a request, block until the gateway answers (or `timeout` seconds pass), and return.

    `(responded, value)` — `responded` is True iff a `.resp` arrived, `value` is its `key` field
    (None if absent/garbled). `payload` must NOT carry 'id'; we assign one. The rid's files are
    always cleaned up. Callers map the outcome to exit codes (a response vs a timeout differ).
    """
    ipc = _ipc_dir()
    rid = uuid.uuid4().hex[:12]
    atomic_write(ipc / f"{rid}.req", json.dumps({"id": rid, **payload}))
    resp = ipc / f"{rid}.resp"
    responded, value = False, None
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if resp.exists():
                responded = True
                try:
                    value = json.loads(resp.read_text()).get(key)
                except json.JSONDecodeError, OSError:
                    value = None
                break
            time.sleep(1)
    finally:
        for suffix in (".req", ".sent", ".await", ".resp"):
            (ipc / f"{rid}{suffix}").unlink(missing_ok=True)
    return responded, value
