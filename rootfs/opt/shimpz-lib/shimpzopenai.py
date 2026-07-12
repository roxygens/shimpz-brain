"""shimpzopenai — the SHARED HTTP client for openai-driver (SECURITY_ENGINEERING_PLAN.md item 7).

imagegen and the gateway's voice (STT/TTS) call openai-driver (holding the OpenAI key in the
separate `openai-driver` container) through this — neither reads the key anymore. Reuse this;
never hand-roll another openai-driver client, same rule shimpzr2.py/shimpzbrowser.py state.

`available()` reports whether the sidecar is wired (token file readable) — the gateway uses it exactly
where it used to check "is OPENAI_KEY set" to enable/disable voice.

Import from callers with `sys.path.insert(0, os.environ.get("SHIMPZ_LIB", "/opt/shimpz-lib"))`.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

URL = os.environ.get("SHIMPZ_OPENAIDRIVER_URL", "http://openai-driver:7076")
TOKEN_FILE = os.environ.get("SHIMPZ_OPENAIDRIVER_TOKEN_FILE", "/run/shimpz-openaidriver/token")


class OpenAIDriverError(Exception):
    """An openai-driver call failed — the sidecar's response IS the error message."""


def available() -> bool:
    """True iff the sidecar token is readable — the gateway's 'is voice configured' check."""
    try:
        return bool(Path(TOKEN_FILE).read_text().strip())
    except OSError:
        return False


def _token() -> str:
    try:
        return Path(TOKEN_FILE).read_text().strip()
    except OSError as exc:
        raise OpenAIDriverError(f"cannot read openai-driver token ({TOKEN_FILE}): {exc}") from exc


def _conn() -> tuple[http.client.HTTPConnection, str]:
    parts = urlsplit(URL)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=180)
    return conn, f"Bearer {_token()}"


def _fail(resp: http.client.HTTPResponse, path: str) -> OpenAIDriverError:
    body = resp.read()
    try:
        detail = json.loads(body).get("error", resp.reason)
    except json.JSONDecodeError, ValueError:
        detail = resp.reason
    return OpenAIDriverError(f"POST {path} -> HTTP {resp.status}: {detail}")


def _post_json_get_bytes(path: str, payload: dict) -> bytes:
    data = json.dumps(payload).encode()
    conn, auth = _conn()
    try:
        conn.request("POST", path, body=data, headers={"Authorization": auth, "Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise _fail(resp, path)
        return resp.read()
    finally:
        conn.close()


def image(prompt: str, size: str = "1024x1024", quality: str = "low", model: str = "gpt-image-2") -> bytes:
    """Generate one image; returns the raw PNG bytes."""
    return _post_json_get_bytes(
        "/v1/openai/image", {"prompt": prompt, "size": size, "quality": quality, "model": model}
    )


def speech(text: str, model: str = "gpt-4o-mini-tts", voice: str = "onyx", response_format: str = "opus") -> bytes:
    """Synthesize `text` to speech; returns the raw audio bytes."""
    return _post_json_get_bytes(
        "/v1/openai/speech", {"text": text, "model": model, "voice": voice, "response_format": response_format}
    )


def transcribe(audio_path: str, model: str = "gpt-4o-transcribe") -> str:
    """Transcribe an audio file to text (streams the bytes up to the sidecar)."""
    p = Path(audio_path)
    size = p.stat().st_size
    conn, auth = _conn()
    try:
        conn.putrequest("POST", "/v1/openai/transcribe")
        conn.putheader("Authorization", auth)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.putheader("X-Filename", p.name)
        conn.putheader("X-Model", model)
        conn.endheaders()
        with p.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                conn.send(chunk)
        resp = conn.getresponse()
        if resp.status != 200:
            raise _fail(resp, "/v1/openai/transcribe")
        return json.loads(resp.read()).get("text", "")
    finally:
        conn.close()
