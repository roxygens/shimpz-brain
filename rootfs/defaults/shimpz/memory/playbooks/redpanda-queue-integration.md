---
task: integrate systems / queues / retries / continuous worker loops via the streaming bus
triggers: fila, queue, integrar, integração, evento, event, stream, kafka, redpanda, worker, consumidor, retry, reprocessar, dead letter, dlq, pubsub, mensageria, processar em fila, serviço em loop, background job contínuo, shimpz-bus
updated: 2026-06-30
---
# Integration / queues / workers — the ONE pattern: the streaming bus (Redpanda, Kafka API)

There is a **single backbone** for connecting one system to another, processing queues, retrying, and
running continuous worker loops: **Redpanda** (Kafka-API) at **`redpanda:9092`** (`SHIMPZ_BUS_BROKERS`),
plus the **`shimpzbus`** standard library every backend service installs. Never hand-roll a queue, never
poll a DB in a `while True`, never use cron for this, and **never build a service before checking the
registry** (`shimpz-bus discover <kw>`) — reuse what already exists.

## The standard library — vendored per project
Every service gets the SAME three patterns from one lib. `shimpz-new`/`shimpz-bus new-worker` already vendor
it at `backend/vendor/shimpzbus` — the app deploys as its OWN container that mounts only the project, so
the dependency MUST live inside the project (a brain path like `/opt/shimpz-lib/shimpzbus` does not exist
there). Adding it to an older project by hand:
```bash
cp -r /opt/shimpz-lib/shimpzbus backend/vendor/shimpzbus   # then in pyproject:
# dependencies=["shimpzbus"] + [tool.uv.sources] shimpzbus={path="vendor/shimpzbus"}  (relative to backend/)
```
```python
import shimpzbus
shimpzbus.publish(topic, event, key=)          # EVENT (async, default)
shimpzbus.run_worker(topic, handler, publishes=[...])  # CONSUME forever: retry+DLQ+at-least-once; publishes=/consumes= → full contract in registry
shimpzbus.call("service", "/route", "POST", {})  # REQUEST/REPLY: discover + HTTP + retry
shimpzbus.register(name, desc, kind=, http=, publishes=[], consumes=[])  # announce on startup
shimpzbus.discover("keyword")                    # filter services by name/desc/topics (a grep)
```

## Which pattern (follow strictly)
- **Event / pub-sub (DEFAULT)** — "X happened", fan-out, decouple, queue, retry. `publish` ↔ `run_worker`.
  Prefer this; it keeps services independent and resilient.
- **Request/reply (HTTP)** — only when you need a synchronous answer NOW. `shimpzbus.call("service", …)`
  discovers the service's endpoint and calls it with retries + timeout. Don't couple synchronously if an
  event would do.
- **Command vs event** — name commands as `<do>` (e.g. `charge.order`) and events as `<happened>`
  (e.g. `order.created`). Events are facts; many can consume them.

## Service discovery — how A finds B that already exists (do this BEFORE building)
Every service **self-announces** (a FastAPI service calls `shimpzbus.register(...)` on startup; a worker is
auto-registered by `run_worker`). Manifests are plain files in `/config/workspace/registry/` (Shimpz can
`ls`/grep them). To find one:
```bash
shimpz-bus services                 # everything registered (name, kind, http, topics)
shimpz-bus discover pagamento       # is there already a payments service? REUSE it, don't duplicate
```
```python
hits = shimpzbus.discover("fatura")   # in code, before wiring an integration
```
**Rule: before creating a service or a topic, `shimpz-bus discover` first.** If it exists, integrate via its
events (subscribe to what it `publishes`) or its HTTP (`shimpzbus.call`). Only build new if nothing matches.

## When to use what
- **Time-scheduled** ("every 5 min", "daily at 9h") → **cron** (`crontab -e`, runs `uv run …`).
- **Event-driven / queue / retry / continuous processing / integrate A→B** → **the bus** (this playbook).
- One-shot script → just a script. Web app/API → FastAPI (`dev-bootstrap`).

## The pattern (publish → worker, with retry + dead-letter)
**Producer side** (system A emits an event):
```python
import shimpzbus
shimpzbus.publish("orders", {"id": 42, "amount": 100}, key="42")   # durable, idempotent
```
**Consumer side** (system B reacts) — a SUPERVISED loop, retries, and a dead-letter topic:
```python
import shimpzbus
def handle(event):
    # integrate: call an API (httpx), write the DB, publish a follow-up event…
    # RAISE on failure → it retries; after N retries the message goes to 'orders.dlq'
    ...
shimpzbus.run_worker("orders", handle)   # at-least-once; commits offset only after success/DLQ
```

## Scaffold + deploy (one command each)
```bash
shimpz-bus new-worker orders-worker orders     # creates projects/orders-worker/backend/{worker.py,pyproject} (deps=shimpzbus)
cd /config/workspace/projects/orders-worker/backend && uv sync   # installs the shimpzbus lib + deps
# SUPERVISED loop (the job cron can't do) — never a bare process, never cron:
DIR=/config/workspace/projects/orders-worker/backend
shimpz-app deploy orders-worker 3190 -- uv run --project $DIR python $DIR/worker.py
# ALWAYS `uv run --project <backend-dir>` so the supervised process uses THE PROJECT's venv (shimpz-app
# clears the inherited /lsiopy VIRTUAL_ENV; without --project from the wrong CWD → ModuleNotFoundError).
# SHIMPZ_BUS_BROKERS is already in the container env. Port (3190) is unused by a worker — it just supervises.
```
The worker runs forever under supervisor (survives restarts). Scale by deploying more instances with
the SAME consumer group (partitions split across them). Add partitions for parallelism: `shimpz-bus create orders -p 6`.

## Ops / debug (the `shimpz-bus` CLI)
```bash
shimpz-bus health                       # cluster reachable?
shimpz-bus topics                       # list topics (incl. the *.dlq dead-letters)
shimpz-bus create <topic> [-p N]        # create with N partitions
shimpz-bus produce <topic> '{"k":1}'    # publish a test event
shimpz-bus tail <topic>                 # print recent messages
shimpz-bus tail orders.dlq             # inspect failures (then fix + re-publish)
```

## Rules
- Retries + **dead-letter** are built in (`run_worker`) — never silently drop a failed message; it lands
  in `<topic>.dlq` with the error. Inspect with `shimpz-bus tail <topic>.dlq`, fix, re-publish.
- Events are **JSON**; include a stable `key` for ordering/partitioning when it matters.
- At-least-once → make handlers **idempotent** (safe to process the same event twice).
- The worker engine is **FastStream** (battle-tested, aiokafka) — loop/offsets/ack/lifecycle aren't
  hand-rolled; `publish` is a sync idempotent producer. Everything **Kafka-API**, portable to real Kafka.
- A worker is a **supervised** process (`shimpz-app`), never a loose `&`/`nohup`, never cron.

## Production / limitations (discovered in chaos-testing — read before trusting)
- **`publish` is delivery-confirmed**: if the broker is down/unreachable it **raises an exception** (it
  doesn't silently lose the message). In a worker, let it propagate (it falls into retry+DLQ); in an API, return 503 and
  re-enqueue — **never** tell the user "ok" before `publish` returns without error.
- **Poison-pill (inherent limitation of at-least-once):** the DLQ only catches **exceptions**. A handler that KILLS
  the process (`os._exit`, segfault, OOM, `kill -9` repeated on the same offset) never commits nor goes to the DLQ
  → the partition enters an **infinite crash-loop**. Rule: a handler **never** takes down the process — catch
  your own errors (becomes retry→DLQ). If an app keeps restarting nonstop (`shimpz-app list` in a loop), it's a
  poison-pill: stop the worker, skip the offset (recreate the topic or reset the group) and fix the handler.
- **Request/reply (`shimpzbus.call`) in the hot path:** a dead/slow dependency costs ~`timeout`×`retries`
  on the path (it doesn't hang forever, but it delays). On a critical path prefer an **event**; if you need a
  call, use a short `timeout` and few `retries`. (Hot-path throughput is limited by the synchronous call.)
- **Git:** the automatic `.gitignore` only excludes known build dirs (`node_modules`, `.venv`, builds) +
  `.env`. Large data/binaries (dumps, `*.sqlite`, `*.parquet`, media) are NOT excluded → add them
  to the project's `.gitignore` so the repo doesn't bloat.
