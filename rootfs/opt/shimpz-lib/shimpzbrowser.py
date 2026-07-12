"""shimpzbrowser — the SHARED HTTP client for browser-agent (SECURITY_ENGINEERING_PLAN.md item 0).

shimpz-input/uiclick/uikey/uitype/uiupload/shimpz-shot/shimpz-cdp/chrome-upload/webread all call
browser-agent (running in the separate `shimpz-browser` container) through this — none of them touch
DISPLAY/CDP/xdotool locally anymore, since the X11 socket and CDP's loopback both live in a
different container now (unreachable any other way). Reuse this — never hand-roll another
browser-agent HTTP client, same rule shimpzcdp.py stated for the CDP client it replaces.

Import from callers with `sys.path.insert(0, os.environ.get("SHIMPZ_LIB", "/opt/shimpz-lib"))` so the
in-container default is `/opt/shimpz-lib` and host-side unit tests point SHIMPZ_LIB at rootfs/opt/shimpz-lib.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

URL = os.environ.get("SHIMPZ_BROWSERAGENT_URL", "http://shimpz-browser:7074")
TOKEN_FILE = os.environ.get("SHIMPZ_BROWSERAGENT_TOKEN_FILE", "/run/shimpz-browseragent/token")


class BrowserAgentError(Exception):
    """A browser-agent call failed — the sidecar's response IS the error message."""


def _token() -> str:
    try:
        with open(TOKEN_FILE) as fh:  # noqa: PTH123 — a fixed, non-user-supplied infra path
            return fh.read().strip()
    except OSError as exc:
        raise BrowserAgentError(f"cannot read browser-agent token ({TOKEN_FILE}): {exc}") from exc


def call_json(method: str, path: str, body: dict | None = None) -> dict:
    """POST/GET a JSON body, return the parsed JSON response."""
    raw, _headers = _call(method, path, body)
    return json.loads(raw)


def call_bytes(method: str, path: str, body: dict | None = None) -> tuple[bytes, dict]:
    """POST/GET, return the RAW response bytes + response headers (screenshot/downloads-fetch)."""
    return _call(method, path, body)


def _call(method: str, path: str, body: dict | None) -> tuple[bytes, dict]:
    token = _token()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 — SHIMPZ_BROWSERAGENT_URL is fixed infra config, never user-controlled
        f"{URL}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — see above
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error", exc.reason)
        except json.JSONDecodeError, ValueError:
            detail = exc.reason
        raise BrowserAgentError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BrowserAgentError(f"{method} {path} -> unreachable: {exc.reason}") from exc


def b64_file(path: str) -> str:
    with open(path, "rb") as fh:  # noqa: PTH123 — caller already validated this is a real local path
        return base64.b64encode(fh.read()).decode()
