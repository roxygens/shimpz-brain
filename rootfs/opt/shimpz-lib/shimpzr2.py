"""shimpzr2 — the SHARED HTTP client for r2-driver (SECURITY_ENGINEERING_PLAN.md item 7).

r2send/r2ls/r2get all call r2-driver (holding the R2 credentials in the separate
`r2-driver` container) through this — none of them touch rclone or the R2 secret locally
anymore. Reuse this — never hand-roll another r2-driver HTTP client, same rule shimpzbrowser.py
states for the browser-agent client.

Uses http.client (not urllib) so upload/download STREAM the file to/from disk in bounded chunks — a
multi-GB R2 object never sits fully in memory on this side either.

Import from callers with `sys.path.insert(0, os.environ.get("SHIMPZ_LIB", "/opt/shimpz-lib"))`.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

URL = os.environ.get("SHIMPZ_R2DRIVER_URL", "http://r2-driver:7075")
TOKEN_FILE = os.environ.get("SHIMPZ_R2DRIVER_TOKEN_FILE", "/run/shimpz-r2driver/token")
_CHUNK = 1024 * 1024


class R2DriverError(Exception):
    """An r2-driver call failed — the sidecar's response IS the error message."""


def _token() -> str:
    try:
        return Path(TOKEN_FILE).read_text().strip()
    except OSError as exc:
        raise R2DriverError(f"cannot read r2-driver token ({TOKEN_FILE}): {exc}") from exc


def _conn() -> tuple[http.client.HTTPConnection, str]:
    parts = urlsplit(URL)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=600)
    return conn, f"Bearer {_token()}"


def _fail(resp: http.client.HTTPResponse, method: str, path: str) -> R2DriverError:
    body = resp.read()
    try:
        detail = json.loads(body).get("error", resp.reason)
    except json.JSONDecodeError, ValueError:
        detail = resp.reason
    return R2DriverError(f"{method} {path} -> HTTP {resp.status}: {detail}")


def upload(local_path: str, filename: str, expire: str | None = None) -> dict:
    """Stream a local file up to R2. Returns {key, link, size}."""
    size = Path(local_path).stat().st_size
    conn, auth = _conn()
    try:
        conn.putrequest("POST", "/v1/r2/upload")
        conn.putheader("Authorization", auth)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.putheader("X-R2-Filename", filename)
        if expire:
            conn.putheader("X-R2-Expire", expire)
        conn.endheaders()
        with Path(local_path).open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                conn.send(chunk)
        resp = conn.getresponse()
        if resp.status != 200:
            raise _fail(resp, "POST", "/v1/r2/upload")
        return json.loads(resp.read())
    finally:
        conn.close()


def list_prefix(prefix: str = "") -> dict:
    """List objects under `prefix`. Returns {prefix, entries: [...]}."""
    conn, auth = _conn()
    try:
        from urllib.parse import quote

        conn.request("GET", f"/v1/r2/list?prefix={quote(prefix, safe='')}", headers={"Authorization": auth})
        resp = conn.getresponse()
        if resp.status != 200:
            raise _fail(resp, "GET", "/v1/r2/list")
        return json.loads(resp.read())
    finally:
        conn.close()


def download(key: str, dest_path: str) -> int:
    """Stream an R2 object down to `dest_path`. Returns the byte count."""
    from urllib.parse import quote

    conn, auth = _conn()
    try:
        conn.request("GET", f"/v1/r2/get?key={quote(key, safe='')}", headers={"Authorization": auth})
        resp = conn.getresponse()
        if resp.status != 200:
            raise _fail(resp, "GET", "/v1/r2/get")
        written = 0
        with Path(dest_path).open("wb") as fh:
            while chunk := resp.read(_CHUNK):
                fh.write(chunk)
                written += len(chunk)
        return written
    finally:
        conn.close()
