"""Authenticated HTTP boundary for the isolated Shimpz LangGraph runtime."""

from __future__ import annotations

import hmac
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import agent_runtime
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, ConfigDict, Field, SecretStr

TOKEN_FILE = Path(os.environ.get("SHIMPZ_BRAIN_RUNTIME_TOKEN_FILE", "/run/shimpz-brain-runtime/token"))
STATE_PATH = Path(os.environ.get("SHIMPZ_BRAIN_RUNTIME_STATE", "/var/lib/shimpz-brain-runtime/checkpoints.sqlite3"))
MAX_TOKEN_BYTES = 4 * 1024


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "openai"]
    model: str = Field(min_length=1, max_length=128)
    api_key: SecretStr = Field(min_length=1, max_length=16 * 1024)


class PowerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2_000)
    input_schema: dict[str, Any]
    approval: Literal["none", "once", "each-run"] = "none"


class TurnContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=256)
    assistant_id: str = Field(min_length=1, max_length=128)
    rules: str = Field(min_length=1, max_length=agent_runtime.MAX_RULES_CHARS)
    powers: list[PowerInput] = Field(min_length=1, max_length=agent_runtime.MAX_POWERS)
    provider: ProviderInput

    def runtime_context(self) -> agent_runtime.TurnContext:
        return agent_runtime.TurnContext(
            thread_id=self.thread_id,
            assistant_id=self.assistant_id,
            rules=self.rules,
            powers=tuple(
                agent_runtime.PowerDefinition(
                    id=power.id,
                    summary=power.summary,
                    input_schema=power.input_schema,
                    approval=power.approval,
                )
                for power in self.powers
            ),
            provider=agent_runtime.ProviderConfig(
                provider=self.provider.provider,
                model=self.provider.model,
                api_key=self.provider.api_key.get_secret_value(),
            ),
        )


class StartTurnInput(TurnContextInput):
    message: str = Field(min_length=1, max_length=agent_runtime.MAX_MESSAGE_CHARS)


class ResumeTurnInput(TurnContextInput):
    results: dict[str, Any] = Field(min_length=1, max_length=agent_runtime.MAX_POWERS)


class RuntimeLike:
    """Structural documentation for the injected runtime used by the API and tests."""

    def start(self, context: agent_runtime.TurnContext, message: str) -> agent_runtime.TurnResult: ...

    def resume(
        self,
        context: agent_runtime.TurnContext,
        results: Mapping[str, object],
    ) -> agent_runtime.TurnResult: ...


TokenReader = Callable[[], str]


def _token_from_file() -> str:
    try:
        raw = TOKEN_FILE.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Brain runtime authentication is unavailable") from exc
    if not 1 <= len(raw) <= MAX_TOKEN_BYTES:
        raise HTTPException(status_code=503, detail="Brain runtime authentication is unavailable")
    try:
        token = raw.decode().strip()
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=503, detail="Brain runtime authentication is unavailable") from exc
    if not token:
        raise HTTPException(status_code=503, detail="Brain runtime authentication is unavailable")
    return token


def _sqlite_runtime(path: Path = STATE_PATH) -> agent_runtime.AgentRuntime:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    connection = sqlite3.connect(path, check_same_thread=False)
    if path.exists():
        path.chmod(0o600)
    connection.execute("PRAGMA secure_delete=ON")
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    return agent_runtime.AgentRuntime(checkpointer)


def _response(result: agent_runtime.TurnResult) -> dict[str, object]:
    return {
        "status": result.status,
        "reply": result.reply,
        "powers": [
            {
                "interrupt_id": request.interrupt_id,
                "power": request.power,
                "input": dict(request.input),
                "approval": request.approval,
            }
            for request in result.powers
        ],
    }


def create_app(
    *,
    runtime: RuntimeLike | None = None,
    token_reader: TokenReader = _token_from_file,
) -> FastAPI:
    owns_runtime = runtime is None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        if owns_runtime and application.state.runtime is not None:
            close = getattr(application.state.runtime, "close", None)
            if callable(close):
                close()

    app = FastAPI(
        title="Shimpz Brain Runtime",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.runtime_lock = threading.Lock()

    def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = token_reader()
        prefix = "Bearer "
        supplied = authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def current_runtime() -> RuntimeLike:
        if app.state.runtime is not None:
            return app.state.runtime
        with app.state.runtime_lock:
            if app.state.runtime is None:
                app.state.runtime = _sqlite_runtime()
        return app.state.runtime

    @app.exception_handler(agent_runtime.RuntimeContractError)
    async def contract_error(_request, exc: agent_runtime.RuntimeContractError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(agent_runtime.ProviderRequestError)
    async def provider_error(_request, _exc: agent_runtime.ProviderRequestError):
        return JSONResponse(status_code=502, content={"detail": "Model provider request failed"})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "runtime": "langgraph"}

    @app.post("/v1/turns", dependencies=[Depends(require_auth)])
    def start_turn(body: StartTurnInput) -> dict[str, object]:
        return _response(current_runtime().start(body.runtime_context(), body.message))

    @app.post("/v1/turns/resume", dependencies=[Depends(require_auth)])
    def resume_turn(body: ResumeTurnInput) -> dict[str, object]:
        return _response(current_runtime().resume(body.runtime_context(), body.results))

    return app


app = create_app()
