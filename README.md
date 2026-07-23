# Shimpz Brain runtime

This repository contains the isolated, provider-neutral reasoning runtime for Shimpz Teams. It uses
LangGraph to select installed Assistant Powers and suspends before every external action. The Team
controller remains authoritative for Assistant inventory, credentials, approvals, Power execution,
result validation, cancellation, and audit; the runtime never executes a Power itself.

The authenticated API is intentionally small:

- `GET /health` reports process/runtime health without exposing state or credentials;
- `POST /v1/turns` starts one turn from controller-supplied Team/Assistant context;
- `POST /v1/turns/resume` resumes a suspended turn with controller-brokered Power results; and
- `POST /v1/threads/delete` deletes one exact conversation checkpoint during Team teardown.

All POST endpoints require the private bearer mounted read-only at
`/run/shimpz-brain-runtime/token`. Conversation checkpoints live at
`/var/lib/shimpz-brain-runtime/checkpoints.sqlite3`. Provider API keys are operation-scoped request
inputs: they are excluded from checkpoint state, responses, and logs.

The image uses CPython 3.14, one non-root Uvicorn worker, a read-only root filesystem, dropped
capabilities, and no direct Docker socket or internet network. Provider traffic can leave only through
the audited egress proxy attached to the runtime's dedicated egress pair. LangSmith tracing and access
logging are disabled by default.

`agent_runtime.py` owns the model/tool state machine, `runtime_api.py` owns the HTTP/auth boundary, and
their contracts live in `tests/`.
