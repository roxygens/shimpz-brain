from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "rootfs" / "usr" / "local" / "bin" / "shimpz-chat-exec"
SCHEMA = ROOT / "rootfs" / "usr" / "local" / "share" / "shimpz-chat" / "decision.schema.json"
PROMPT = ROOT / "rootfs" / "usr" / "local" / "share" / "shimpz-chat" / "instrument-prompt.txt"


def load_script():
    loader = importlib.machinery.SourceFileLoader("shimpz_chat_exec_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ChatInstrumentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script()

    def test_claude_argv_is_rebuilt_without_agent_authority(self) -> None:
        original = [
            "sh",
            "-c",
            'exec "$@"',
            "shimpz-brain",
            "timeout",
            "180",
            "claude",
            "-p",
            "--continue",
            "--dangerously-skip-permissions",
            "--model",
            "claude-sonnet-5",
            "--output-format",
            "stream-json",
        ]
        assets = {
            self.module.DECISION_SCHEMA_PATH: SCHEMA.read_text(encoding="utf-8"),
            self.module.INSTRUMENT_PROMPT_PATH: PROMPT.read_text(encoding="utf-8"),
        }
        with mock.patch.object(self.module, "_read_image_asset", side_effect=lambda path: assets[path]):
            hardened = self.module._instrument_claude_command(original)

        joined = " ".join(hardened)
        self.assertNotIn("dangerously", joined)
        self.assertNotIn("--continue", hardened)
        self.assertIn("--safe-mode", hardened)
        self.assertIn("--disable-slash-commands", hardened)
        self.assertIn("--strict-mcp-config", hardened)
        self.assertEqual(hardened[hardened.index("--tools") + 1], "")
        self.assertEqual(json.loads(hardened[hardened.index("--mcp-config") + 1]), {"mcpServers": {}})
        self.assertEqual(
            json.loads(hardened[hardened.index("--json-schema") + 1]),
            json.loads(SCHEMA.read_text(encoding="utf-8")),
        )
        self.assertIn("no authority to use a shell", hardened[hardened.index("--system-prompt") + 1])

    def test_provider_environment_drops_capsule_and_service_secrets(self) -> None:
        source = {
            "DATABASE_URL": "postgresql://must-not-leak",
            "SHIMPZ_DRIVER_TOKEN": "must-not-leak",
            "TELEGRAM_BOT_TOKEN": "must-not-leak",
            "ANTHROPIC_API_KEY": "provider-key",
            "HTTPS_PROXY": "http://egress-proxy:8888",
            "PATH": "/hostile/path",
            "HOME": "/config/workspace",
        }
        child = self.module._provider_environment(source)

        self.assertNotIn("DATABASE_URL", child)
        self.assertNotIn("SHIMPZ_DRIVER_TOKEN", child)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", child)
        self.assertEqual(child["ANTHROPIC_API_KEY"], "provider-key")
        self.assertEqual(child["HTTPS_PROXY"], "http://egress-proxy:8888")
        self.assertEqual(child["PATH"], self.module.FIXED_PATH)
        self.assertEqual(child["HOME"], "/config")
        self.assertEqual(child["TMPDIR"], "/tmp")  # noqa: S108

    def test_image_assets_reject_writable_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prompt.txt"
            path.write_text("unsafe", encoding="utf-8")
            path.chmod(0o666)
            with self.assertRaises(OSError):
                self.module._read_image_asset(path)


if __name__ == "__main__":
    unittest.main()
