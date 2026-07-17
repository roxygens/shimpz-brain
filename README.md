# shimpz-brain

The isolated, provider-neutral Brain runtime for a Shimpz Capsule. It uses LangGraph to reason over
exactly one Assistant's declared Powers and suspends before every Power request; the Capsule
Controller remains responsible for authorization, approval, execution and audit.

`docker build .` produces a small Python 3.14 image running `runtime_api:app` as a non-root user. The
runtime exposes:

- `GET /health` without authentication for container health checks.
- `POST /v1/turns` to start or continue a conversation.
- `POST /v1/turns/resume` to supply Controller-brokered Power results.

Both POST endpoints require the private bearer token mounted at
`/run/shimpz-brain-runtime/token`. Conversation checkpoints are stored at
`/var/lib/shimpz-brain-runtime/checkpoints.sqlite3`. The image starts one Uvicorn worker without
access logging and disables LangSmith tracing by default.

---
Part of the **[Shimpz](https://github.com/roxygens/shimpz)** stack.
