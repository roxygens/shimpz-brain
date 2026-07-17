"""Provider-neutral LangGraph runtime with no Power execution authority.

The runtime can reason, remember a conversation and request a declared Power.  A Power
request always suspends the graph before any side effect.  The Capsule Controller remains
the only component allowed to validate approvals, execute the Power and resume the graph
with its bounded result.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.types import Command, interrupt
from pydantic import SecretStr

PROVIDERS = frozenset({"anthropic", "openai"})
APPROVALS = frozenset({"none", "once", "each-run"})
POWER_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
MAX_POWERS = 64
MAX_RULES_CHARS = 64 * 1024
MAX_MESSAGE_CHARS = 64 * 1024
MAX_SCHEMA_BYTES = 64 * 1024
MAX_REPLY_CHARS = 64 * 1024
DEFAULT_RECURSION_LIMIT = 12


class RuntimeContractError(ValueError):
    """Trusted orchestration input or persisted output violated the closed contract."""


class ProviderRequestError(RuntimeError):
    """A provider call failed without exposing provider response or credential material."""


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: Literal["anthropic", "openai"]
    model: str
    api_key: str

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise RuntimeContractError("unsupported model provider")
        if IDENTIFIER_RE.fullmatch(self.model) is None:
            raise RuntimeContractError("invalid model identifier")
        if not self.api_key or len(self.api_key) > 16 * 1024 or "\0" in self.api_key:
            raise RuntimeContractError("invalid model provider credential")


@dataclass(frozen=True, slots=True)
class PowerDefinition:
    id: str
    summary: str
    input_schema: Mapping[str, Any]
    approval: Literal["none", "once", "each-run"] = "none"

    def __post_init__(self) -> None:
        if POWER_ID_RE.fullmatch(self.id) is None:
            raise RuntimeContractError("invalid Power id")
        if not self.summary.strip() or len(self.summary) > 2_000:
            raise RuntimeContractError("invalid Power summary")
        if self.approval not in APPROVALS:
            raise RuntimeContractError("invalid Power approval policy")
        if self.input_schema.get("type") != "object":
            raise RuntimeContractError("Power input schema must describe an object")
        try:
            encoded = json.dumps(self.input_schema, separators=(",", ":"), sort_keys=True).encode()
        except (TypeError, ValueError) as exc:
            raise RuntimeContractError("Power input schema is not JSON") from exc
        if len(encoded) > MAX_SCHEMA_BYTES:
            raise RuntimeContractError("Power input schema is too large")


@dataclass(frozen=True, slots=True)
class TurnContext:
    thread_id: str
    assistant_id: str
    rules: str
    powers: tuple[PowerDefinition, ...]
    provider: ProviderConfig

    def __post_init__(self) -> None:
        if IDENTIFIER_RE.fullmatch(self.thread_id) is None:
            raise RuntimeContractError("invalid conversation thread")
        if POWER_ID_RE.fullmatch(self.assistant_id) is None:
            raise RuntimeContractError("invalid Assistant id")
        if not self.rules.strip() or len(self.rules) > MAX_RULES_CHARS:
            raise RuntimeContractError("invalid Assistant Rules")
        if not 1 <= len(self.powers) <= MAX_POWERS:
            raise RuntimeContractError("an Assistant must expose 1 to 64 Powers")
        ids = [power.id for power in self.powers]
        if len(ids) != len(set(ids)):
            raise RuntimeContractError("duplicate Power id")


@dataclass(frozen=True, slots=True)
class PowerRequest:
    interrupt_id: str
    power: str
    input: Mapping[str, Any]
    approval: Literal["none", "once", "each-run"]


@dataclass(frozen=True, slots=True)
class TurnResult:
    status: Literal["completed", "power-required"]
    reply: str = ""
    powers: tuple[PowerRequest, ...] = ()


class Checkpointer(Protocol):
    """The LangGraph checkpointer surface accepted by ``create_agent``."""


ModelFactory = Callable[[ProviderConfig], BaseChatModel]


def provider_model(config: ProviderConfig) -> BaseChatModel:
    """Create one direct provider client; the API key is never put in graph state."""
    secret = SecretStr(config.api_key)
    common = {
        "model": config.model,
        "api_key": secret,
        "timeout": 60.0,
        "max_retries": 2,
    }
    if config.provider == "openai":
        return ChatOpenAI(**common)
    if config.provider == "anthropic":
        return ChatAnthropic(**common)
    raise RuntimeContractError("unsupported model provider")


def _request_power(power: PowerDefinition) -> StructuredTool:
    """Build a tool that can only suspend the graph with a typed Power request."""

    def suspend_for_controller(**payload: Any) -> Any:
        return interrupt(
            {
                "kind": "power",
                "power": power.id,
                "input": payload,
                "approval": power.approval,
            }
        )

    return StructuredTool.from_function(
        suspend_for_controller,
        name=power.id,
        description=power.summary,
        args_schema=dict(power.input_schema),
        infer_schema=False,
    )


def _system_prompt(context: TurnContext) -> str:
    return (
        "You are the Brain for exactly one installed Shimpz Assistant. "
        "You may answer directly or request only one of the declared Powers. "
        "A Power result is the sole source of truth for whether an action happened. "
        "Never claim an action succeeded before receiving its result. "
        "Never request secrets, shell access, filesystem access, code execution, dependencies, "
        "undeclared tools, or another Assistant.\n\n"
        f"Assistant: {context.assistant_id}\n"
        f"Rules:\n{context.rules}"
    )


def _message_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    text: list[str] = []
    for block in value:
        if isinstance(block, str):
            text.append(block)
        elif isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str):
            text.append(str(block["text"]))
    return "\n".join(text)


def _result(state: Mapping[str, Any]) -> TurnResult:
    pending = state.get("__interrupt__")
    if pending:
        requests: list[PowerRequest] = []
        if not isinstance(pending, Sequence):
            raise RuntimeContractError("invalid suspended graph state")
        for item in pending:
            value = getattr(item, "value", None)
            interrupt_id = getattr(item, "id", None)
            if (
                not isinstance(value, Mapping)
                or value.get("kind") != "power"
                or not isinstance(interrupt_id, str)
                or not interrupt_id
                or POWER_ID_RE.fullmatch(str(value.get("power", ""))) is None
                or not isinstance(value.get("input"), Mapping)
                or value.get("approval") not in APPROVALS
            ):
                raise RuntimeContractError("invalid Power suspension")
            requests.append(
                PowerRequest(
                    interrupt_id=interrupt_id,
                    power=str(value["power"]),
                    input=dict(value["input"]),
                    approval=value["approval"],
                )
            )
        if not requests:
            raise RuntimeContractError("empty graph suspension")
        return TurnResult(status="power-required", powers=tuple(requests))

    messages = state.get("messages")
    if not isinstance(messages, Sequence):
        raise RuntimeContractError("graph completed without messages")
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            reply = _message_content(message.content).strip()
            if not reply:
                continue
            return TurnResult(status="completed", reply=reply[:MAX_REPLY_CHARS])
    raise RuntimeContractError("graph completed without an Assistant reply")


class AgentRuntime:
    """Compile short-lived provider clients over one durable, provider-neutral graph state."""

    def __init__(self, checkpointer: Checkpointer, *, model_factory: ModelFactory = provider_model) -> None:
        self._checkpointer = checkpointer
        self._model_factory = model_factory

    @staticmethod
    def _config(context: TurnContext) -> dict[str, object]:
        return {
            "configurable": {"thread_id": context.thread_id},
            "recursion_limit": DEFAULT_RECURSION_LIMIT,
        }

    def _agent(self, context: TurnContext):
        model = self._model_factory(context.provider)
        tools = [_request_power(power) for power in context.powers]
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=_system_prompt(context),
            checkpointer=self._checkpointer,
        )

    def start(self, context: TurnContext, message: str) -> TurnResult:
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
            raise RuntimeContractError("invalid chat message")
        try:
            state = self._agent(context).invoke(
                {"messages": [{"role": "user", "content": message}]},
                config=self._config(context),
            )
        except RuntimeContractError:
            raise
        except Exception as exc:
            raise ProviderRequestError("model provider request failed") from exc
        return _result(state)

    def resume(self, context: TurnContext, results: Mapping[str, object]) -> TurnResult:
        if not results or not all(isinstance(key, str) and key for key in results):
            raise RuntimeContractError("invalid Power resume results")
        try:
            state = self._agent(context).invoke(
                Command(resume=dict(results)),
                config=self._config(context),
            )
        except RuntimeContractError:
            raise
        except Exception as exc:
            raise ProviderRequestError("model provider request failed") from exc
        return _result(state)
