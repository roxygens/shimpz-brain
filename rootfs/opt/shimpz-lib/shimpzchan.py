"""The channel abstraction seam (Phase D0): a brain run emits progress to a `Sink`; a channel implements it.

Today `shimpz-gateway` drives one brain run and renders it straight into a Telegram `LiveCard`. To let a
new client (an OpenAI-compatible chat API, then Discord/WhatsApp) reuse the SAME validated run pipeline
without per-channel brain code, Phase D1 lifts the run core into `shimpzchat.py`, driven by this `Sink` —
so the run logic never names Telegram. Telegram's `LiveCard` becomes the reference `Sink`; the chat API
implements the same `Sink` to stream a run over `/v1/chat/completions`.

Scope of the Sink = the RUN phase only (status ticker, streamed words, heartbeat, the run's stop-handle).
The post-run ENDING (final answer vs. continue/retry, and a voice reply) stays in each adapter, driven by
the `(final_text, outcome)` that `run_task` returns — because endings are channel-shaped (Telegram inline
keyboards + a voice note vs. an OpenAI `finish_reason`). This module is DEFINITIONS ONLY (no I/O, no deps
beyond typing) so both the gateway and a co-resident chat-API service can import it freely.
"""

from typing import Protocol, runtime_checkable

# Run outcomes — identical to shimpz-gateway's `subtype`. run_task returns one of these so the adapter's
# ending renders the right affordance (a resumable step-limit, a retryable timeout, a clean success, a
# user Stop). Kept as constants (not an enum) so they compare `==` to the raw stream-json subtype string.
OK = "success"
ERROR_MAX_TURNS = "error_max_turns"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
ERROR = "error"
OUTCOMES = frozenset({OK, ERROR_MAX_TURNS, TIMEOUT, CANCELLED, ERROR})


@runtime_checkable
class Sink(Protocol):
    """What a channel implements so a brain run can render its LIVE progress into it.

    Every method is async and MUST be best-effort — an implementation may never raise into the run loop
    (Telegram's LiveCard swallows `TelegramError`; an API sink swallows a client disconnect). The run
    calls these in roughly this order per turn: `bind_run` once → `thinking` (fresh turn) → interleaved
    `status`/`stream`/`heartbeat` → and, on the two out-of-band endings, `start_failed` or `stopped`.
    """

    async def bind_run(self, pid: int) -> None:
        """Bind the run's stop-handle (the brain process pid) so a Stop control can kill THIS run.

        Called once, right after the subprocess starts, before any status/stream. The pid is the run's
        identity — Telegram rides it on the Stop button so a stale tap for a finished run can't kill a
        newly-started one.
        """
        ...

    async def thinking(self) -> None:
        """First feedback on a fresh turn, before any tool runs. Idempotent by the sink's discretion.

        Telegram shows "🧠 thinking…" only if the card hasn't been sent yet; a sink is free to no-op if
        it has already emitted something for this turn.
        """
        ...

    async def status(self, label: str) -> None:
        """The current action/tool status line, replacing the previous one (Telegram edits the card)."""
        ...

    async def stream(self, text: str) -> None:
        """Shimpz's answer words as they arrive. May be called repeatedly; each call supersedes the last."""
        ...

    async def heartbeat(self, label: str) -> None:
        """A 'still working' pulse during a long SILENT tool call, so the channel never goes dark."""
        ...

    async def start_failed(self, message: str) -> None:
        """The brain subprocess couldn't even start — surface it loudly (never a silent 'it's off')."""
        ...

    async def stopped(self, message: str) -> None:
        """The user cancelled this run (Stop): it was killed on purpose — report it quietly, no error."""
        ...


class SpySink:
    """A recording `Sink` for tests: captures the ordered sequence of calls, drives no real channel.

    Satisfies the `Sink` protocol structurally (asserted in test-shimpzchan.py). Phase D1's `test-shimpzchat.py`
    drives a brain run against one of these and asserts the exact event sequence — the same run the
    Telegram `LiveCard` renders, with a fake channel underneath.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.pid: int | None = None

    async def bind_run(self, pid: int) -> None:
        self.pid = pid
        self.events.append(("bind_run", pid))

    async def thinking(self) -> None:
        self.events.append(("thinking", None))

    async def status(self, label: str) -> None:
        self.events.append(("status", label))

    async def stream(self, text: str) -> None:
        self.events.append(("stream", text))

    async def heartbeat(self, label: str) -> None:
        self.events.append(("heartbeat", label))

    async def start_failed(self, message: str) -> None:
        self.events.append(("start_failed", message))

    async def stopped(self, message: str) -> None:
        self.events.append(("stopped", message))

    def texts(self) -> list[str]:
        """Just the streamed answer texts, in order — the common assertion target."""
        return [arg for name, arg in self.events if name == "stream"]
