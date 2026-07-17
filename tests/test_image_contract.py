from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BrainImageContractTests(unittest.TestCase):
    def test_image_runs_only_the_non_root_http_runtime(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.14-slim@sha256:", dockerfile)
        self.assertIn("USER brainruntime", dockerfile)
        self.assertIn("HEALTHCHECK --interval=10s", dockerfile)
        self.assertIn("socket.create_connection", dockerfile)
        self.assertIn("GET /health HTTP/1.0", dockerfile)
        self.assertIn("HTTP/1.1 200 OK", dockerfile)
        self.assertNotIn("urllib.request", dockerfile)
        self.assertIn('"runtime_api:app"', dockerfile)
        self.assertIn('"--workers", "1"', dockerfile)
        self.assertIn('"--no-access-log"', dockerfile)
        self.assertNotIn("COPY rootfs", dockerfile)
        self.assertNotIn("COPY codex", dockerfile)

    def test_runtime_paths_and_tracing_defaults_are_explicit(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("SHIMPZ_BRAIN_RUNTIME_TOKEN_GID=10016", dockerfile)
        self.assertIn("SHIMPZ_BRAIN_RUNTIME_TOKEN_FILE=/run/shimpz-brain-runtime/token", dockerfile)
        self.assertIn(
            "SHIMPZ_BRAIN_RUNTIME_STATE=/var/lib/shimpz-brain-runtime/checkpoints.sqlite3",
            dockerfile,
        )
        self.assertIn("LANGSMITH_TRACING=false", dockerfile)


if __name__ == "__main__":
    unittest.main()
