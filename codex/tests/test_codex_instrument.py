from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "rootfs" / "usr" / "local" / "bin" / "shimpz-codex-run"


def load_script():
    loader = importlib.machinery.SourceFileLoader("shimpz_codex_run_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CodexInstrumentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script()

    def test_command_is_stateless_and_disables_every_agent_surface(self) -> None:
        catalog_path = self.module.PROVIDER_WORKDIR / "instrument" / "model-catalog.json"
        command = self.module._command("gpt-test", catalog_path)
        joined = " ".join(command)

        self.assertNotIn("dangerously", joined)
        self.assertNotIn("resume", command)
        self.assertNotIn("--search", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(Path(command[command.index("--cd") + 1]), self.module.PROVIDER_WORKDIR)
        self.assertEqual(command[command.index("--output-schema") + 1], str(self.module.DECISION_SCHEMA_PATH))

        disabled = {command[index + 1] for index, value in enumerate(command[:-1]) if value == "--disable"}
        self.assertEqual(disabled, set(self.module.DISABLED_FEATURES))
        self.assertTrue(
            {
                "apps",
                "browser_use",
                "code_mode",
                "computer_use",
                "goals",
                "hooks",
                "image_generation",
                "multi_agent",
                "plugins",
                "search_tool",
                "shell_snapshot",
                "shell_tool",
                "standalone_web_search",
                "tool_suggest",
                "unified_exec",
            }.issubset(disabled)
        )
        overrides = {command[index + 1] for index, value in enumerate(command[:-1]) if value == "--config"}
        self.assertIn('default_permissions="instrument"', overrides)
        self.assertIn("permissions.instrument={}", overrides)
        self.assertIn('web_search="disabled"', overrides)
        self.assertIn("mcp_servers={}", overrides)
        self.assertIn("plugins={}", overrides)
        self.assertIn("skills={}", overrides)
        self.assertIn("orchestrator.mcp.enabled=false", overrides)
        self.assertIn("orchestrator.skills.enabled=false", overrides)
        self.assertIn("include_environment_context=false", overrides)

    def test_catalog_forces_text_only_model_without_tools_or_agent_prompt(self) -> None:
        catalog = self.module._model_catalog("gpt-test", "instrument only")
        model = catalog["models"][0]

        self.assertEqual(model["slug"], "gpt-test")
        self.assertEqual(model["base_instructions"], "instrument only")
        self.assertEqual(model["shell_type"], "disabled")
        self.assertIsNone(model["apply_patch_tool_type"])
        self.assertFalse(model["supports_search_tool"])
        self.assertEqual(model["experimental_supported_tools"], [])
        self.assertEqual(model["input_modalities"], ["text"])
        self.assertFalse(model["supports_parallel_tool_calls"])
        self.assertFalse(model["include_skills_usage_instructions"])
        self.assertIsNone(model["multi_agent_version"])

    def test_provider_environment_drops_capsule_secrets_and_uses_fresh_home(self) -> None:
        home = self.module.PROVIDER_WORKDIR / "instrument"
        child = self.module._provider_environment(
            {
                "DATABASE_URL": "postgresql://must-not-leak",
                "SHIMPZ_DRIVER_TOKEN": "must-not-leak",
                "ANTHROPIC_API_KEY": "must-not-leak",
                "OPENAI_API_KEY": "provider-key",
                "HTTPS_PROXY": "http://egress-proxy:8888",
                "HOME": "/config/workspace",
                "CODEX_HOME": "/config/.codex",
            },
            home,
        )

        self.assertNotIn("DATABASE_URL", child)
        self.assertNotIn("SHIMPZ_DRIVER_TOKEN", child)
        self.assertNotIn("ANTHROPIC_API_KEY", child)
        self.assertEqual(child["OPENAI_API_KEY"], "provider-key")
        self.assertEqual(child["HTTPS_PROXY"], "http://egress-proxy:8888")
        self.assertEqual(child["CODEX_HOME"], str(home))
        self.assertEqual(child["HOME"], str(home))
        self.assertEqual(child["PATH"], self.module.FIXED_PATH)

    def test_only_exact_structured_decisions_are_accepted(self) -> None:
        direct = '{"message":"Hello","kind":"message","input":"{}","power":""}'
        self.assertEqual(
            self.module._decision(direct),
            '{"kind":"message","message":"Hello","power":"","input":"{}"}',
        )
        power = (
            '{"kind":"power","message":"","power":"hello",'
            '"input":"{ \\"name\\": \\"Ada\\", \\"details\\": {\\"z\\": 2, \\"a\\": 1} }"}'
        )
        normalized = json.loads(self.module._decision(power))
        self.assertEqual(normalized["power"], "hello")
        self.assertEqual(normalized["input"], '{"details":{"a":1,"z":2},"name":"Ada"}')
        for invalid in (
            '{"kind":"message","message":"Hello","power":"","input":"{}","extra":true}',
            '{"kind":"message","message":"","power":"","input":"{}"}',
            '{"kind":"message","message":"Hello","power":"shell","input":"{}"}',
            '{"kind":"message","message":"Hello","power":"","input":"[]"}',
            '{"kind":"message","message":"Hello","power":"","input":"{\\"x\\":NaN}"}',
            '{"kind":"message","message":"Hello","power":"","input":"{\\"x\\":1,\\"x\\":2}"}',
            '{"kind":"power","message":"","power":"../shell","input":"{}"}',
            '{"kind":"power","message":"not empty","power":"hello","input":"{}"}',
        ):
            with self.subTest(invalid=invalid), self.assertRaises(self.module.ProviderOutputError):
                self.module._decision(invalid)

    def test_tool_events_fail_closed_before_reaching_the_controller(self) -> None:
        safe = b'{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}\n'
        self.assertEqual(self.module._event(safe)["type"], "item.completed")
        provider_error = b'{"type":"item.completed","item":{"type":"error","message":"offline"}}\n'
        self.assertEqual(self.module._event(provider_error)["type"], "item.completed")
        for item_type in ("command_execution", "file_change", "web_search", "mcp_tool_call", "tool_call"):
            event = json.dumps({"type": "item.completed", "item": {"type": item_type}}).encode()
            with self.subTest(item_type=item_type), self.assertRaises(self.module.ProviderOutputError):
                self.module._event(event)

    def test_stream_event_exposes_only_the_canonical_decision(self) -> None:
        original = b'{"type":"item.completed","item":{"type":"agent_message","text":"provider output"}}\n'
        canonical = '{"kind":"message","message":"Hello","power":"","input":"{}"}'

        normalized = json.loads(self.module._canonical_event_line(original, canonical))

        self.assertEqual(normalized["item"]["text"], canonical)

    def test_auth_is_copied_as_opaque_private_runtime_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source-auth.json"
            destination = root / "instrument" / "auth.json"
            destination.parent.mkdir(mode=0o700)
            source.write_text('{"tokens":{"access_token":"opaque"}}', encoding="utf-8")
            source.chmod(0o600)
            with mock.patch.object(self.module, "AUTH_PATH", source):
                self.module._copy_private_auth(destination)

            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

            unsafe = root / "unsafe-auth.json"
            unsafe.write_text("{}", encoding="utf-8")
            unsafe.chmod(0o644)
            with mock.patch.object(self.module, "AUTH_PATH", unsafe), self.assertRaises(OSError):
                self.module._copy_private_auth(root / "rejected-auth.json")

    def test_each_turn_uses_a_fresh_private_home_and_authoritative_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "instruments"
            missing_auth = root / "missing-auth.json"
            assets = {
                self.module.INSTRUMENT_PROMPT_PATH: b"instrument only\n",
                self.module.DECISION_SCHEMA_PATH: b'{"type":"object"}',
            }
            with (
                mock.patch.object(self.module, "INSTRUMENT_PARENT", parent),
                mock.patch.object(self.module, "AUTH_PATH", missing_auth),
                mock.patch.object(self.module, "_read_image_asset", side_effect=lambda path: assets[path]),
                self.module._instrument_home("gpt-test") as (home, catalog_path),
            ):
                self.assertTrue(home.is_dir())
                self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(catalog_path.stat().st_mode), 0o600)
                catalog = json.loads(catalog_path.read_text(encoding="ascii"))
                self.assertEqual(catalog, self.module._model_catalog("gpt-test", "instrument only"))
                first_home = home

            self.assertFalse(first_home.exists())
            self.assertEqual(list(parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
