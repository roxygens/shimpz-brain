from __future__ import annotations

import unittest
from collections.abc import Sequence
from typing import Any, ClassVar

import agent_runtime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver


class ToolAwareFakeModel(FakeMessagesListChatModel):
    bound_tools: ClassVar[list[str]] = []

    def bind_tools(self, tools: Sequence[Any], **_kwargs: Any):
        type(self).bound_tools = [tool.name for tool in tools]
        return self


def power(
    power_id: str = "hello",
    *,
    approval: str = "none",
) -> agent_runtime.PowerDefinition:
    return agent_runtime.PowerDefinition(
        id=power_id,
        summary=f"Run {power_id}.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "maxLength": 80}},
            "additionalProperties": False,
        },
        approval=approval,
    )


def context(*powers: agent_runtime.PowerDefinition, thread_id: str = "cap:hello:thread-1"):
    return agent_runtime.TurnContext(
        thread_id=thread_id,
        assistant_id="hello-pulse",
        rules="Be friendly and use only the declared Powers.",
        powers=tuple(powers or (power(),)),
        provider=agent_runtime.ProviderConfig(provider="openai", model="gpt-test", api_key="secret-test-key"),
    )


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        ToolAwareFakeModel.bound_tools = []

    def test_returns_a_direct_reply_without_executing_any_power(self):
        model = ToolAwareFakeModel(responses=[AIMessage(content="Hello, Captain.")])
        runtime = agent_runtime.AgentRuntime(InMemorySaver(), model_factory=lambda _config: model)

        result = runtime.start(context(), "Say hello")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reply, "Hello, Captain.")
        self.assertEqual(result.powers, ())

    def test_power_suspends_before_execution_and_resumes_with_controller_result(self):
        model = ToolAwareFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "hello",
                            "args": {"name": "Ada"},
                            "id": "provider-call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="The Assistant returned: Hello, Ada."),
            ]
        )
        runtime = agent_runtime.AgentRuntime(InMemorySaver(), model_factory=lambda _config: model)
        turn = context(power(approval="each-run"))

        suspended = runtime.start(turn, "Greet Ada")

        self.assertEqual(suspended.status, "power-required")
        self.assertEqual(len(suspended.powers), 1)
        request = suspended.powers[0]
        self.assertEqual(request.power, "hello")
        self.assertEqual(request.input, {"name": "Ada"})
        self.assertEqual(request.approval, "each-run")

        completed = runtime.resume(turn, {request.interrupt_id: {"message": "Hello, Ada."}})

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.reply, "The Assistant returned: Hello, Ada.")

    def test_model_receives_only_the_selected_assistants_declared_powers(self):
        model = ToolAwareFakeModel(responses=[AIMessage(content="Done")])
        runtime = agent_runtime.AgentRuntime(InMemorySaver(), model_factory=lambda _config: model)

        runtime.start(context(power("hello"), power("campaign.read")), "What can you do?")

        self.assertEqual(ToolAwareFakeModel.bound_tools, ["hello", "campaign.read"])

    def test_conversations_are_isolated_by_thread(self):
        model = ToolAwareFakeModel(responses=[AIMessage(content="First capsule"), AIMessage(content="Second capsule")])
        saver = InMemorySaver()
        runtime = agent_runtime.AgentRuntime(saver, model_factory=lambda _config: model)

        runtime.start(context(thread_id="cap-a:hello:one"), "A")
        runtime.start(context(thread_id="cap-b:hello:one"), "B")

        first = saver.get({"configurable": {"thread_id": "cap-a:hello:one"}})
        second = saver.get({"configurable": {"thread_id": "cap-b:hello:one"}})
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(
            first["channel_values"]["messages"][0].content,
            second["channel_values"]["messages"][0].content,
        )

    def test_invalid_or_duplicate_power_contract_fails_closed(self):
        with self.assertRaisesRegex(agent_runtime.RuntimeContractError, "invalid Power id"):
            power("../shell")
        with self.assertRaisesRegex(agent_runtime.RuntimeContractError, "duplicate Power"):
            context(power("hello"), power("hello"))
        with self.assertRaisesRegex(agent_runtime.RuntimeContractError, "must describe an object"):
            agent_runtime.PowerDefinition(
                id="hello",
                summary="Hello",
                input_schema={"type": "string"},
            )

    def test_provider_failures_do_not_expose_the_secret(self):
        class FailedModelFactory:
            def __call__(self, config):
                raise RuntimeError(f"provider rejected {config.api_key}")

        runtime = agent_runtime.AgentRuntime(InMemorySaver(), model_factory=FailedModelFactory())

        with self.assertRaisesRegex(agent_runtime.ProviderRequestError, "model provider request failed") as raised:
            runtime.start(context(), "Hello")
        self.assertNotIn("secret-test-key", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
