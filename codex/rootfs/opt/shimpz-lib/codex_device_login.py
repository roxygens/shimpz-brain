"""Bounded bridge for Codex's official headless device authorization flow.

The bridge never accepts credentials.  It starts ``codex login --device-auth``, exposes only the
official verification URL and short-lived user code through mode-0600 files, then independently
checks Codex's stable Shimpz auth status.  Provider output is never relayed or persisted.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

LOGIN_DIR = Path(os.environ.get("SHIMPZ_CODEX_LOGIN_DIR", "/config/.shimpz/codex-login"))
CODEX_BIN = os.environ.get("SHIMPZ_CODEX_BIN", "/usr/local/bin/codex")
AUTH_BIN = os.environ.get("SHIMPZ_CODEX_AUTH_BIN", "/usr/local/bin/shimpz-codex-auth")
try:
    _configured_timeout = int(os.environ.get("SHIMPZ_CODEX_LOGIN_TIMEOUT", "600"))
except ValueError:
    _configured_timeout = 600
TIMEOUT = min(max(_configured_timeout, 30), 900)
MAX_OUTPUT_BYTES = 256 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_STATE_BYTES = 4 * 1024
ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|[\x00-\x08\x0b-\x1f\x7f]")
URL_RE = re.compile(r"https://[^\s'\"<>]+")
USER_CODE_RE = re.compile(r"^[A-Z0-9]{4,8}(?:-[A-Z0-9]{4,8}){1,2}$")


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _safe_read(path: Path, limit: int = MAX_STATE_BYTES) -> str:
    """Read one small, owner-private regular file without following links or blocking on FIFOs."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > limit:
            raise ValueError("invalid state file")
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) > limit:
        raise ValueError("invalid state file")
    return raw.decode("utf-8")


def _official_url(candidate: str) -> str | None:
    value = candidate.rstrip(".,)]}>")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "auth.openai.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path.rstrip("/") != "/codex/device"
    ):
        return None
    return value


def _parse_line(raw: bytes) -> tuple[str | None, str | None]:
    text = ANSI_RE.sub(b"", raw).decode("utf-8", "replace").strip()
    url = None
    for candidate in URL_RE.findall(text):
        url = _official_url(candidate)
        if url:
            break
    code = None
    for token in text.split():
        candidate = token.strip(".,()[]{}<>").upper()
        if USER_CODE_RE.fullmatch(candidate):
            code = candidate
            break
    return url, code


def _result(state: str, message: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"state": state}
    if message:
        payload["message"] = message[:200]
    return payload


def _write_result(state: str, message: str | None = None) -> None:
    if state in {"succeeded", "failed", "cancelled", "timeout"}:
        for name in ("url", "user_code"):
            (LOGIN_DIR / name).unlink(missing_ok=True)
    _atomic_write(LOGIN_DIR / "result", json.dumps(_result(state, message), separators=(",", ":")))


def _acquire_lock() -> int | None:
    LOGIN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    LOGIN_DIR.chmod(0o700)
    descriptor = os.open(LOGIN_DIR / ".lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def _terminate(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=2)


def _authenticated() -> bool:
    try:
        completed = subprocess.run(
            [AUTH_BIN, "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
        payload = json.loads(completed.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return (
        completed.returncode == 0
        and isinstance(payload, dict)
        and set(payload) == {"provider", "configured", "auth_type"}
        and payload.get("provider") == "codex"
        and payload.get("configured") is True
        and payload.get("auth_type") == "oauth"
    )


def run() -> int:
    lock = _acquire_lock()
    if lock is None:
        print("Codex device login is already in progress", file=sys.stderr)
        return 1
    child: subprocess.Popen[bytes] | None = None
    try:
        for name in ("url", "user_code", "result", "cancel"):
            (LOGIN_DIR / name).unlink(missing_ok=True)
        _write_result("starting")
        try:
            child = subprocess.Popen(
                [CODEX_BIN, "login", "--device-auth"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError:
            _write_result("failed", "Codex device login could not start")
            return 1
        assert child.stdout is not None
        output_fd = child.stdout.fileno()
        os.set_blocking(output_fd, False)
        started = time.monotonic()
        buffered = b""
        total = 0
        url = code = None
        failure = None
        while child.poll() is None:
            if (LOGIN_DIR / "cancel").exists():
                failure = _result("cancelled")
                break
            if time.monotonic() - started >= TIMEOUT:
                failure = _result("timeout", "Codex device login expired; start again")
                break
            readable, _, _ = select.select([output_fd], [], [], 0.2)
            if not readable:
                continue
            try:
                chunk = os.read(output_fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_OUTPUT_BYTES:
                failure = _result("failed", "Codex device login returned too much output")
                break
            buffered += chunk
            if len(buffered) > MAX_LINE_BYTES and b"\n" not in buffered:
                failure = _result("failed", "Codex device login returned an oversized line")
                break
            while b"\n" in buffered:
                line, buffered = buffered.split(b"\n", 1)
                if len(line) > MAX_LINE_BYTES:
                    failure = _result("failed", "Codex device login returned an oversized line")
                    break
                found_url, found_code = _parse_line(line)
                url = url or found_url
                code = code or found_code
                if url and code and not (LOGIN_DIR / "url").exists():
                    _atomic_write(LOGIN_DIR / "url", url)
                    _atomic_write(LOGIN_DIR / "user_code", code)
                    _write_result("waiting")
            if failure:
                break
        if failure:
            _terminate(child)
            _write_result(str(failure["state"]), failure.get("message"))
            return 1
        returncode = child.wait()
        if returncode == 0 and url and code and _authenticated():
            _write_result("succeeded")
            return 0
        _write_result("failed", "Codex did not confirm the device login")
        return 1
    finally:
        if child is not None:
            _terminate(child)
        (LOGIN_DIR / "cancel").unlink(missing_ok=True)
        os.close(lock)


def info() -> int:
    try:
        state = json.loads(_safe_read(LOGIN_DIR / "result"))
        if not isinstance(state, dict) or set(state) - {"state", "message"} or state.get("state") != "waiting":
            raise ValueError("device login is not waiting")
        url = _safe_read(LOGIN_DIR / "url").strip()
        user_code = _safe_read(LOGIN_DIR / "user_code").strip().upper()
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        print('{"pending":true}')
        return 0
    if _official_url(url) != url or USER_CODE_RE.fullmatch(user_code) is None:
        print('{"pending":true}')
        return 0
    print(json.dumps({"pending": False, "url": url, "user_code": user_code}, separators=(",", ":")))
    return 0


def result() -> int:
    try:
        payload = json.loads(_safe_read(LOGIN_DIR / "result"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        payload = {"state": "idle"}
    allowed_states = {"idle", "starting", "waiting", "succeeded", "failed", "cancelled", "timeout"}
    if not isinstance(payload, dict) or payload.get("state") not in allowed_states:
        payload = {"state": "failed", "message": "Codex device login state is invalid"}
    clean = {"state": payload["state"]}
    message = payload.get("message")
    if isinstance(message, str) and message:
        clean["message"] = message[:200]
    print(json.dumps(clean, separators=(",", ":")))
    return 0


def cancel() -> int:
    lock = _acquire_lock()
    if lock is not None:
        os.close(lock)
        print('{"cancelled":false}')
        return 0
    _atomic_write(LOGIN_DIR / "cancel", "cancel\n")
    deadline = time.monotonic() + 7
    while time.monotonic() < deadline:
        lock = _acquire_lock()
        if lock is not None:
            os.close(lock)
            print('{"cancelled":true}')
            return 0
        time.sleep(0.1)
    print('{"cancelled":false}')
    return 1


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        return run()
    if argv == ["info"]:
        return info()
    if argv == ["result"]:
        return result()
    if argv == ["cancel"]:
        return cancel()
    print("usage: codex_device_login.py run|info|result|cancel", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
