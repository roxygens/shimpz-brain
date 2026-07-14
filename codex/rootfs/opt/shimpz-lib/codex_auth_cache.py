"""Atomically install an opaque Codex OAuth auth.json from stdin without logging its contents."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

limit = 1024 * 1024
raw = sys.stdin.buffer.read(limit + 1)
if not raw or len(raw) > limit:
    raise SystemExit("invalid OAuth cache size")
try:
    payload = json.loads(raw)
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("OAuth cache must be valid JSON") from exc
if not isinstance(payload, dict):
    raise SystemExit("OAuth cache must be a JSON object")

home = Path(sys.argv[1])
home.mkdir(parents=True, exist_ok=True, mode=0o700)
home.chmod(0o700)
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
fd, temporary = tempfile.mkstemp(prefix=".auth.json.", dir=home)
temporary_path = Path(temporary)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    temporary_path.replace(home / "auth.json")
    directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    with suppress(FileNotFoundError):
        temporary_path.unlink()
