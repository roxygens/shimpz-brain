from __future__ import annotations

import secrets
import tempfile
import unittest
from pathlib import Path

import agent_runtime
import runtime_api
from fastapi.testclient import TestClient

TOKEN = secrets.token_hex(24)
SECRET = secrets.token_urlsafe(32)


def body(**updates):
    value = {
        "thread_id": "capsule:hello-pulse:conversation-1",
        "team_name": "  Greeting Crew  ",
        "assistants": [
            {
                "id": "hello-pulse",
                "rules": "Return a friendly greeting.",
                "powers": [
                    {
                        "id": "hello",
                        "summary": "Return a greeting.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        "approval": "none",
                    }
                ],
            },
            {
                "id": "backup-greeter",
                "rules": "Provide a backup greeting.",
                "powers": [
                    {
                        "id": "hello",
                        "summary": "Return a backup greeting.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        "approval": "once",
                    }
                ],
            },
        ],
        "provider": {"provider": "openai", "model": "gpt-test", "api_key": SECRET},
        "message": "Hello",
    }
    value.update(updates)
    return value


class FakeRuntime:
    def __init__(self, result=None, error=None):
        self.result = result or agent_runtime.TurnResult(status="completed", reply="Hello.")
        self.error = error
        self.calls = []

    def start(self, context, message):
        self.calls.append(("start", context, message))
        if self.error:
            raise self.error
        return self.result

    def resume(self, context, results):
        self.calls.append(("resume", context, results))
        if self.error:
            raise self.error
        return self.result


def client(runtime):
    app = runtime_api.create_app(runtime=runtime, token_reader=lambda: TOKEN)
    return TestClient(app)


class RuntimeApiTests(unittest.TestCase):
    def test_health_is_small_and_does_not_require_a_secret(self):
        response = client(FakeRuntime()).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "runtime": "langgraph"})

    def test_turn_endpoint_requires_the_private_runtime_token(self):
        api = client(FakeRuntime())

        self.assertEqual(api.post("/v1/turns", json=body()).status_code, 401)
        self.assertEqual(
            api.post("/v1/turns", json=body(), headers={"Authorization": "Bearer wrong"}).status_code,
            401,
        )

    def test_start_passes_provider_secret_in_memory_but_never_returns_it(self):
        runtime = FakeRuntime()
        response = client(runtime).post(
            "/v1/turns",
            json=body(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "completed", "reply": "Hello.", "powers": []})
        context = runtime.calls[0][1]
        self.assertEqual(context.provider.api_key, SECRET)
        self.assertEqual(context.team_name, "Greeting Crew")
        self.assertEqual([assistant.id for assistant in context.assistants], ["hello-pulse", "backup-greeter"])
        self.assertEqual([assistant.powers[0].id for assistant in context.assistants], ["hello", "hello"])
        self.assertNotIn(SECRET, response.text)

    def test_power_request_contains_only_controller_action_data(self):
        runtime = FakeRuntime(
            result=agent_runtime.TurnResult(
                status="power-required",
                powers=(
                    agent_runtime.PowerRequest(
                        interrupt_id="interrupt-1",
                        assistant_id="hello-pulse",
                        power="hello",
                        input={"name": "Ada"},
                        approval="each-run",
                    ),
                ),
            )
        )
        response = client(runtime).post(
            "/v1/turns",
            json=body(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "power-required",
                "reply": "",
                "powers": [
                    {
                        "interrupt_id": "interrupt-1",
                        "assistant_id": "hello-pulse",
                        "power": "hello",
                        "input": {"name": "Ada"},
                        "approval": "each-run",
                    }
                ],
            },
        )

    def test_resume_accepts_only_explicit_interrupt_results(self):
        runtime = FakeRuntime()
        payload = body(message=None)
        payload.pop("message")
        payload["results"] = {"interrupt-1": {"message": "Hello, Ada."}}
        response = client(runtime).post(
            "/v1/turns/resume",
            json=payload,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(runtime.calls[0][0], "resume")
        self.assertEqual(runtime.calls[0][2], {"interrupt-1": {"message": "Hello, Ada."}})

    def test_extra_fields_and_invalid_provider_fail_closed(self):
        api = client(FakeRuntime())
        invalid = body(unexpected_command="forbidden")
        response = api.post("/v1/turns", json=invalid, headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(response.status_code, 422)

        invalid = body()
        invalid["provider"]["provider"] = "codex"
        response = api.post("/v1/turns", json=invalid, headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(response.status_code, 422)

        invalid = body()
        invalid["assistants"][0]["unexpected"] = "forbidden"
        response = api.post("/v1/turns", json=invalid, headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(response.status_code, 422)

    def test_malformed_team_names_fail_at_the_closed_http_contract(self):
        api = client(FakeRuntime())

        for team_name in ("", "   ", "Bad\nName", "Bad\x7fName", "x" * 81):
            with self.subTest(team_name=team_name):
                response = api.post(
                    "/v1/turns",
                    json=body(team_name=team_name),
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                self.assertEqual(response.status_code, 422)

    def test_provider_error_is_generic_and_never_echoes_credential(self):
        runtime = FakeRuntime(error=agent_runtime.ProviderRequestError(f"provider rejected {SECRET}"))
        response = client(runtime).post(
            "/v1/turns",
            json=body(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"detail": "Model provider request failed"})
        self.assertNotIn(SECRET, response.text)

    def test_sqlite_checkpoints_are_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "checkpoints.sqlite3"
            runtime = runtime_api._sqlite_runtime(path)

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
