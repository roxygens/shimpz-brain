from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "rootfs" / "opt" / "shimpz-lib" / "codex_device_login.py"


class CodexDeviceLoginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.codex = self.root / "codex"
        self.auth = self.root / "auth"
        self.codex.write_text(
            "#!/bin/sh\n"
            "printf '\\033[94mhttps://auth.openai.com/codex/device\\033[0m\\n'\n"
            "printf '\\033[94mAB12-CDE34\\033[0m\\n'\n"
            "sleep \"${FAKE_CODEX_SLEEP:-0.1}\"\n",
            encoding="utf-8",
        )
        self.auth.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' '{\"provider\":\"codex\",\"configured\":true,\"auth_type\":\"oauth\"}'\n",
            encoding="utf-8",
        )
        self.codex.chmod(0o700)
        self.auth.chmod(0o700)
        self.environment = {
            **os.environ,
            "SHIMPZ_CODEX_LOGIN_DIR": str(self.state),
            "SHIMPZ_CODEX_BIN": str(self.codex),
            "SHIMPZ_CODEX_AUTH_BIN": str(self.auth),
            "SHIMPZ_CODEX_LOGIN_TIMEOUT": "30",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def command(self, operation: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), operation],
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )

    def wait_for_info(self) -> dict[str, object]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.command("info")
            payload = json.loads(response.stdout)
            if payload.get("pending") is False:
                return payload
            time.sleep(0.05)
        self.fail("device information did not become available")

    def test_success_exposes_only_official_device_information(self) -> None:
        completed = self.command("run")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(self.command("info").stdout), {"pending": True})
        self.assertEqual(json.loads(self.command("result").stdout), {"state": "succeeded"})
        self.assertFalse((self.state / "url").exists())
        self.assertFalse((self.state / "user_code").exists())
        self.assertEqual(stat.S_IMODE((self.state / "result").stat().st_mode), 0o600)
        self.assertNotIn("AB12-CDE34", completed.stdout + completed.stderr)

    def test_unofficial_url_is_never_exposed(self) -> None:
        self.codex.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'https://evil.example/codex/device' 'AB12-CDE34'\n",
            encoding="utf-8",
        )
        completed = self.command("run")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(self.command("info").stdout), {"pending": True})
        self.assertEqual(json.loads(self.command("result").stdout)["state"], "failed")

    def test_concurrent_run_is_rejected_and_cancel_is_bounded(self) -> None:
        running = subprocess.Popen(
            [sys.executable, str(SCRIPT), "run"],
            env={**self.environment, "FAKE_CODEX_SLEEP": "30"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(self.wait_for_info()["user_code"], "AB12-CDE34")
            duplicate = self.command("run")
            self.assertEqual(duplicate.returncode, 1)
            cancelled = self.command("cancel")
            self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
            self.assertEqual(json.loads(cancelled.stdout), {"cancelled": True})
            _stdout, _stderr = running.communicate(timeout=8)
            self.assertEqual(json.loads(self.command("result").stdout), {"state": "cancelled"})
            self.assertEqual(json.loads(self.command("info").stdout), {"pending": True})
        finally:
            if running.poll() is None:
                running.kill()
                running.wait(timeout=2)

    def test_state_reads_do_not_follow_symlinks(self) -> None:
        self.state.mkdir(mode=0o700)
        secret = self.root / "auth.json"
        secret.write_text('{"token":"must-not-leak"}', encoding="utf-8")
        (self.state / "url").symlink_to(secret)
        (self.state / "user_code").write_text("AB12-CDE34", encoding="utf-8")
        response = self.command("info")
        self.assertEqual(json.loads(response.stdout), {"pending": True})
        self.assertNotIn("must-not-leak", response.stdout + response.stderr)


if __name__ == "__main__":
    unittest.main()
