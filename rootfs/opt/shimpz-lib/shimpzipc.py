"""shimpzipc — the SINGLE definition of Shimpz's request/response IPC over `$SHIMPZ_HOME/ipc`.

The blocking CLIs (shimpz-ask, shimpz-approve, shimpz-captcha) drop a `<rid>.req` file; the gateway
(or shimpz-run's `--auto` autopilot) owns the Telegram poll, shows the buttons, and writes back a
`<rid>.resp`. This module is that protocol in one place so each CLI no longer reimplements the
write/poll/cleanup loop. SHIMPZ_IPC_DIR overrides the dir so a debug harness can intercept.
"""

import contextlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

CHAT_TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_PENDING_SCAN_ENTRIES = 64
MAX_REQUEST_BYTES = 64 * 1024


def _open_ipc_dir(ipc_dir):
    return os.open(ipc_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)


def _read_request_at(directory, name):
    """Return `(payload, invalid_regular)` without following or blocking on tenant file types."""
    try:
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_REQUEST_BYTES:
            return None, False
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory,
        )
        try:
            after = os.fstat(descriptor)
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_nlink != 1
                or after.st_size > MAX_REQUEST_BYTES
                or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            ):
                return None, False
            raw = os.read(descriptor, MAX_REQUEST_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError:
        return None, False
    if len(raw) > MAX_REQUEST_BYTES:
        return None, False
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
    except ValueError, TypeError, RecursionError:
        return None, True
    return payload, False


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
    try:
        directory = _open_ipc_dir(ipc_dir)
    except OSError:
        return
    try:
        names = []
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_PENDING_SCAN_ENTRIES:
                    break
                if entry.name.endswith(".req"):
                    names.append(entry.name)
        for name in sorted(names):
            payload, invalid = _read_request_at(directory, name)
            if payload is None:
                if invalid:
                    with contextlib.suppress(OSError):
                        os.rename(
                            name,
                            f"{name[:-4]}.bad",
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                        )
                continue
            req = Path(ipc_dir) / name
            payload_rid = payload.get("id")
            rid = payload_rid if isinstance(payload_rid, str) and RID_RE.fullmatch(payload_rid) else req.stem
            if not RID_RE.fullmatch(rid):
                continue
            yield rid, req, payload
    finally:
        os.close(directory)


def pending_for_chat(ipc_dir, chat_token):
    """Yield only requests owned by one currently active durable chat token."""
    if not CHAT_TOKEN_RE.fullmatch(chat_token):
        return
    for rid, req, payload in pending(ipc_dir):
        if payload.get("_chat_token") == chat_token:
            yield rid, req, payload


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


def answer_for_chat(ipc_dir, rid, payload, chat_token):
    """Answer only an exact pending request owned by the active durable chat token."""
    if not CHAT_TOKEN_RE.fullmatch(chat_token) or not RID_RE.fullmatch(rid):
        return False
    try:
        directory = _open_ipc_dir(ipc_dir)
    except OSError:
        return False
    try:
        request_payload, _invalid = _read_request_at(directory, f"{rid}.req")
    finally:
        os.close(directory)
    if request_payload is None or request_payload.get("_chat_token") != chat_token:
        return False
    req = Path(ipc_dir) / f"{rid}.req"
    wrote = answer(ipc_dir, rid, payload)
    if wrote:
        mark_sent(req)
    return wrote


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
    chat_token = os.environ.get("SHIMPZ_CHAT_TOKEN", "")
    token_field = {"_chat_token": chat_token} if CHAT_TOKEN_RE.fullmatch(chat_token) else {}
    atomic_write(ipc / f"{rid}.req", json.dumps({"id": rid, **token_field, **payload}))
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
