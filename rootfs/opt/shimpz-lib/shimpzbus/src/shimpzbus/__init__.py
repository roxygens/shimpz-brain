"""shimpzbus — Shimpz's ONE standard library for inter-service communication.

Install it in any backend service with `uv add shimpzbus` (it lives at /opt/shimpz-lib/shimpzbus). It gives
every service the SAME three patterns, so services never reinvent integration and always find each
other:

  EVENTS (async, the default — decouple, fan-out, queue, retry)
    shimpzbus.publish("order.created", {"id": 42})          # emit an event (durable, idempotent)
    shimpzbus.run_worker("order.created", handle)           # consume forever: retry + DLQ + at-least-once
    async for e in shimpzbus.stream("order.created"): ...   # LIVE tail for in-app push (the ws gateway)

  REQUEST/REPLY (sync — when you need an answer now)
    data = shimpzbus.call("billing", "/charge", "POST", {"id": 42})   # discover + HTTP + retry

  DISCOVERY (how a service finds another that already does the job — NEVER reinvent)
    shimpzbus.register("billing", "charges customers", kind="api", http="127.0.0.1:3101",
                    publishes=["invoice.issued"], consumes=["order.created"])   # on startup
    shimpzbus.discover("charg")      # -> [ {name, description, http, publishes, consumes}, ... ]

Engine: the consumer is built on **FastStream** (Kafka, aiokafka) — a battle-tested streaming
framework — so the worker loop, offsets, lifecycle, retries and at-least-once are not hand-rolled.
`publish` uses a sync idempotent confluent-kafka producer (callable from anywhere, including inside a
worker handler). Brokers from $SHIMPZ_BUS_BROKERS (default redpanda:9092). Registry in $SHIMPZ_REGISTRY
(default /config/workspace/registry/<name>.json) — plain files so Shimpz can `ls`/grep it too.
`run_worker` auto-registers the worker, so consumers self-announce. Rule of thumb:
event by default; HTTP request/reply only when you truly need a synchronous answer.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

BROKERS = os.environ.get("SHIMPZ_BUS_BROKERS", "redpanda:9092")
REGISTRY = Path(os.environ.get("SHIMPZ_REGISTRY", "/config/workspace/registry"))
# Per-project SASL/SCRAM credentials (SECURITY_ENGINEERING_PLAN.md item 3 — don't hand every app
# an unauthenticated broker): `shimpz-bus provision <project>` creates a SASL user +
# ACL scoped to that project's OWN `<project>.*` topics. Unset (the default) means PLAINTEXT,
# UNCHANGED from before this existed — nothing currently using shimpzbus without these env vars
# breaks; a project opts in by putting the two vars shimpz-bus provision prints into its own .env.
SASL_USERNAME = os.environ.get("SHIMPZ_BUS_SASL_USERNAME")
SASL_PASSWORD = os.environ.get("SHIMPZ_BUS_SASL_PASSWORD")
SASL_MECHANISM = os.environ.get("SHIMPZ_BUS_SASL_MECHANISM", "SCRAM-SHA-256")
_SASL_ON = bool(SASL_USERNAME and SASL_PASSWORD)


def _confluent_sasl_config():
    """confluent-kafka's dot-notation config fragment — {} (no-op) when SASL isn't configured."""
    if not _SASL_ON:
        return {}
    return {
        "security.protocol": "SASL_PLAINTEXT",
        "sasl.mechanism": SASL_MECHANISM,
        "sasl.username": SASL_USERNAME,
        "sasl.password": SASL_PASSWORD,
    }


def _aiokafka_sasl_kwargs():
    """Aiokafka's underscore-notation kwargs — {} (no-op) when SASL isn't configured."""
    if not _SASL_ON:
        return {}
    return {
        "security_protocol": "SASL_PLAINTEXT",
        "sasl_mechanism": SASL_MECHANISM,
        "sasl_plain_username": SASL_USERNAME,
        "sasl_plain_password": SASL_PASSWORD,
    }


def _faststream_security():
    """FastStream's own Security object.

    NOT plain kwargs — verified against its real installed API: KafkaBroker takes `security=`, a
    `faststream.security.SASLScram256(username, password)` instance, not `sasl_mechanism=`/
    `sasl_plain_username=` like aiokafka itself. None when SASL isn't configured — FastStream's
    default (no `security=`) is plain, unchanged.
    """
    if not _SASL_ON:
        return None
    from faststream.security import SASLScram256, SASLScram512

    cls = {"SCRAM-SHA-256": SASLScram256, "SCRAM-SHA-512": SASLScram512}.get(SASL_MECHANISM, SASLScram256)
    return cls(username=SASL_USERNAME, password=SASL_PASSWORD)


log = logging.getLogger("shimpzbus")

# ── EVENT BUS ────────────────────────────────────────────────────────────────────────────────
# publish: a sync, idempotent confluent-kafka producer. It stays on confluent (not FastStream) on
# purpose: publish must be callable from ANY sync context — including from inside a worker handler
# that runs in FastStream's threadpool, where there is no running event loop to await broker.publish.
# A loop-free, thread-safe sync producer is simpler and more robust there. The CONSUMER is FastStream.
_producer = None
_producer_lock = threading.Lock()


def _prod():
    global _producer
    with _producer_lock:
        if _producer is None:
            from confluent_kafka import Producer

            _producer = Producer(
                {
                    "bootstrap.servers": BROKERS,
                    "enable.idempotence": True,
                    "linger.ms": 5,
                    "acks": "all",
                    **_confluent_sasl_config(),
                }
            )
    return _producer


def publish(topic, value, key=None, timeout=15.0):
    """Emit a JSON event to `topic`, **delivery-CONFIRMED**.

    Raises if the broker doesn't acknowledge the message within `timeout` (broker down / unreachable /
    oversized) — so a caller NEVER gets a false success and silently loses the event. Idempotent producer
    + acks=all. Sync; callable anywhere. In a worker handler a raise propagates into run_worker's
    retry+DLQ; in an API, surface 503 and retry.
    """
    p = _prod()
    k = key.encode() if isinstance(key, str) else key
    res = {}  # per-call delivery result (closure → concurrency-safe with a shared producer)
    p.produce(
        topic,
        key=k,
        value=json.dumps(value, default=str).encode(),
        on_delivery=lambda err, msg: res.__setitem__("err", err),
    )
    p.flush(timeout)  # block until this (and other queued) messages are acked or time out
    if "err" not in res:
        raise RuntimeError(
            f"publish to {topic!r} NOT confirmed within {timeout}s — broker unreachable, message NOT sent"
        )
    if res["err"] is not None:
        raise RuntimeError("publish to {!r} failed: {}".format(topic, res["err"]))


def run_worker(
    topic,
    handler,
    name=None,
    group=None,
    retries=3,
    backoff=1.0,
    dlq=None,
    description=None,
    publishes=(),
    consumes=None,
    register_self=True,
):
    """Consume `topic` forever, calling handler(event_dict).

    Exception → retry up to `retries` (linear backoff); still failing → route to `<topic>.dlq` with the
    error and move on. At-least-once (the offset is committed only AFTER the handler succeeds or the
    message is routed to DLQ). Auto-registers this worker in the registry — pass `publishes=[...]` (and
    `consumes=[...]` if it reads more than `topic`) so it self-announces its full contract for discovery.
    Blocks until killed — deploy SUPERVISED via `shimpz-app` (never cron).

    Built on FastStream (aiokafka): the user `handler` is plain sync and runs off the event loop in a
    threadpool, so blocking work + the sync `publish` (DLQ / follow-up events) compose cleanly. Must run
    as the main thread of its process (FastStream installs signal handlers) — which is exactly how
    `shimpz-app` supervises it.
    """
    import asyncio

    from faststream import AckPolicy, FastStream
    from faststream.kafka import KafkaBroker, KafkaMessage

    name = name or os.environ.get("SHIMPZ_SERVICE") or (topic.replace(".", "-") + "-worker")
    group = group or (topic + "-workers")
    dlq = dlq or (topic + ".dlq")
    if register_self:
        # FAIL-FAST: if a worker can't announce itself, that's a real bug (broken registry/disk) — let
        # it crash at boot instead of running undiscoverable. Pass register_self=False when the worker
        # runs in its OWN app container: the registry is mounted READ-ONLY there, and registration is
        # the DEPLOY's job (shimpz-app runs the project's register.py on the brain — trusted plane).
        register(
            name,
            description or (f"consumes {topic}"),
            kind="worker",
            consumes=list(consumes) if consumes is not None else [topic],
            publishes=list(publishes),
        )

    broker = KafkaBroker(BROKERS, security=_faststream_security())

    async def _consume(msg: KafkaMessage):
        raw = msg.body
        text = bytes(raw).decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        kb = getattr(msg.raw_message, "key", None)
        key = kb.decode("utf-8", "replace") if isinstance(kb, (bytes, bytearray)) else kb
        # FAIL-FAST: a message that isn't a JSON object is unprocessable — dead-letter it directly.
        # NEVER fabricate a fake event (no {"_raw": ...}) and hand it to the handler as if it were valid.
        try:
            event = json.loads(text)
            if not isinstance(event, dict):
                raise ValueError("event is not a JSON object")
        except ValueError as e:  # json.JSONDecodeError is a ValueError subclass
            # publish() is sync and blocks up to 15s on flush — run it OFF the event loop (like the
            # handler-failure DLQ path does), else a slow/unreachable broker here blocks aiokafka's
            # heartbeat on the same loop → session timeout → rebalance → redelivery storm.
            await asyncio.to_thread(
                publish,
                dlq,
                {
                    "topic": topic,
                    "key": key,
                    "raw": text[:2000],
                    "error": f"unparseable: {e!r}",
                },
                key=key,
            )
            log.exception("unparseable message -> DLQ %s", dlq)
            await msg.ack()
            return

        def _process():
            # `retries` total attempts with linear backoff, then dead-letter (same semantics as before).
            last_err = None
            for attempt in range(1, retries + 1):
                try:
                    handler(event)
                except Exception as e:  # noqa: BLE001 — retry boundary: an arbitrary user handler may raise anything; log + retry/DLQ, never crash the consumer
                    last_err = e
                    log.warning("handler fail %d/%d: %s", attempt, retries, e)
                    if attempt < retries:
                        time.sleep(backoff * attempt)
                else:
                    return
            publish(
                dlq,
                {
                    "topic": topic,
                    "key": key,
                    "event": event,
                    "error": f"handler failed after {retries} attempts: {last_err!r}",
                },
                key=key,
            )
            log.error("message -> DLQ %s", dlq)

        await asyncio.to_thread(_process)  # run blocking sync handler off the loop
        await msg.ack()  # at-least-once: commit only after success or DLQ

    broker.subscriber(topic, group_id=group, ack_policy=AckPolicy.MANUAL, auto_offset_reset="earliest")(_consume)
    log.info("shimpzbus worker up: name=%s topic=%s group=%s dlq=%s", name, topic, group, dlq)
    asyncio.run(FastStream(broker).run())  # app.run() is a coroutine; blocks until killed


async def stream(topic, group=None, start="latest"):
    """Async-iterate events from `topic` — the in-app consumer for a FastAPI lifespan task.

    This is the WS-GATEWAY primitive: a FastAPI app can consume the bus INSIDE its own event loop
    (a lifespan task) and fan events out to connected browsers — `run_worker` can't live there (it
    blocks the main thread and installs signal handlers). Semantics differ from run_worker ON
    PURPOSE: no consumer group by default (every gateway instance sees every event; pass `group` to
    shard + commit offsets instead of fan-out), and an unparseable message is logged LOUDLY and
    skipped (a push stream has no DLQ contract — the durable consumer dead-letters it). Broker errors
    PROPAGATE (fail-fast) — the caller owns the reconnect policy. Cancellation-safe: the consumer is
    stopped on exit/cancel, never leaked.

    `start` is the offset reset for a group's FIRST run: "latest" (default — a live tail; a
    reconnecting browser wants NOW, not history) or "earliest" (a DURABLE in-process consumer that
    must not miss an event — a cross-project relay processing every lead, incl. any published while
    it was down; pair with a persistent `group` so committed offsets carry across restarts).
    """
    if start not in ("latest", "earliest"):
        raise ValueError(f"stream start must be 'latest' or 'earliest', got {start!r}")
    from aiokafka import AIOKafkaConsumer  # ships with faststream[kafka], same engine as run_worker

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=BROKERS,
        group_id=group,
        auto_offset_reset=start,
        enable_auto_commit=group is not None,  # groupless live tail has no offsets to commit
        **_aiokafka_sasl_kwargs(),
    )
    await consumer.start()
    try:
        async for msg in consumer:
            raw = msg.value or b""
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
                if not isinstance(event, dict):
                    raise ValueError("event is not a JSON object")
            except ValueError:  # json.JSONDecodeError is a ValueError subclass
                log.exception("stream: unparseable message on %s — skipped (DLQ is run_worker's job)", topic)
                continue
            yield event
    finally:
        await consumer.stop()


# ── SERVICE REGISTRY / DISCOVERY ─────────────────────────────────────────────────────────────
def register(name, description, kind="api", http=None, publishes=(), consumes=(), internal=False):
    """Announce this service so others (and Shimpz) can discover it.

    Runs at DEPLOY time on the brain (shimpz-app invokes the project's register.py after the app proves
    healthy): apps live in their own containers where the registry is mounted READ-ONLY, so only the
    trusted plane writes manifests. `http` is `host:port` — for a containerized app that is
    `app_<name>:<port>` (its Docker DNS name on its own network). Writes a plain JSON manifest;
    `publishes`/`consumes` topics are the feature-granular contract.

    `internal=True` marks the MICROSERVICE plane (Round 128): the service exists ONLY for other
    services (shimpzbus.call / topics) and `shimpz-publish` hard-refuses to ever expose it on a domain —
    the only publishable surfaces are a project's front and its own backend riding behind that
    front. Default False keeps existing project-tier registrations unchanged.

    A registration is a PROMISE others build on (`shimpzbus.call` resolves it blindly), so an `http`
    with no real port is refused: a `host:0` placeholder once advertised a service that was never
    deployed and anything discovering it got a dead endpoint. Pass `http=None` when the service
    honestly has no HTTP surface (a pure bus worker).
    """
    if http is not None:
        _port = str(http).rsplit(":", 1)[-1]
        if not _port.isdigit() or int(_port) == 0:
            raise ValueError(
                f"register({name!r}): http={http!r} has no real port — pass http='127.0.0.1:<port>' "
                "of the LIVE endpoint, or http=None for a bus-only worker (never a placeholder)"
            )
    REGISTRY.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            "name": name,
            "description": description,
            "kind": kind,
            "http": http,
            "internal": bool(internal),
            "publishes": list(publishes),
            "consumes": list(consumes),
            "registered_at": int(time.time()),
        },
        ensure_ascii=False,
        indent=2,
    )
    # Atomic write (temp + os.replace): services() is deliberately fail-fast on unparseable JSON, so a
    # plain write_text (truncate-then-write) lets a concurrent reader catch a torn file and crash on a
    # transient, self-healing condition. A rename is all-or-nothing on the same filesystem.
    final = REGISTRY / f"{name}.json"
    tmp = REGISTRY / f".{name}.json.{os.getpid()}.tmp"
    tmp.write_text(body)
    tmp.replace(final)
    log.info("registered service '%s' (%s)", name, kind)


def services():
    """All registered services (list of manifest dicts).

    FAIL-FAST: a corrupt manifest raises (loud + names the file) instead of being silently skipped — a
    service silently vanishing from discovery is exactly the kind of partial-working-with-a-bug we
    refuse.
    """
    if not REGISTRY.exists():
        return []
    out = []
    for p in sorted(REGISTRY.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception as e:
            raise RuntimeError(f"corrupt registry manifest {p}: {e}") from e
    return out


def discover(query=None):
    """Find services. With `query`, match against name/description/topics (case-insensitive).

    ALWAYS call this before building a new service — reuse what already exists.
    """
    out = services()
    if query:
        toks = [t for t in query.lower().split() if t]

        def blob(s):
            return (
                s.get("name", "")
                + " "
                + s.get("description", "")
                + " "
                + " ".join(s.get("publishes", []) + s.get("consumes", []))
            ).lower()

        out = [s for s in out if all(t in blob(s) for t in toks)]  # token-AND: word order / phrasing tolerant
    return out


# ── REQUEST/REPLY (HTTP, discovered) ─────────────────────────────────────────────────────────
def call(service, path="/", method="GET", json_body=None, retries=3, timeout=10.0):
    """Synchronously call another service's HTTP API by NAME (discovered from the registry), with retries + timeout.

    Use only when you need an answer now; otherwise emit an event.
    """
    # Fail LOUD on the classic mistake: call(service, path, {payload}) — the dict lands in the `method`
    # position and httpx does method.upper() → the cryptic "'dict' object has no attribute 'upper'". Point
    # at the real fix instead of letting it fail deep inside httpx.
    if not isinstance(method, str):
        raise TypeError(
            "shimpzbus.call(service, path, method, json_body): `method` must be an HTTP method string like "
            f"'GET'/'POST' — got {type(method).__name__}. Did you pass the JSON payload in the method position? Use "
            f"call({service!r}, {path!r}, 'POST', json_body=...)."
        )
    # Resolve the target BEFORE importing the HTTP stack: an unknown service is a caller error that must
    # fail fast (and dependency-free) — no reason to load httpx just to discover there's nothing
    # to call. Both the TypeError guard above and this LookupError fire before any I/O dependency.
    svc = next((s for s in services() if s.get("name") == service and s.get("http")), None)
    if not svc:
        raise LookupError(f"no service '{service}' with an http endpoint registered (try shimpzbus.discover())")
    import httpx

    url = "http://{}{}".format(svc["http"], path if path.startswith("/") else "/" + path)

    # ponytail: plain retry loop (exp backoff 1s,2s,…, cap 8s) — was tenacity, its only consumer here
    for attempt in range(1, retries + 1):
        try:
            r = httpx.request(method, url, json=json_body, timeout=timeout)
            r.raise_for_status()
            return r.json() if "application/json" in r.headers.get("content-type", "") else r.text
        except httpx.HTTPError:
            if attempt == retries:
                raise
            time.sleep(min(0.5 * 2**attempt, 8))
    return None  # unreachable (retries >= 1 always returns or raises)
