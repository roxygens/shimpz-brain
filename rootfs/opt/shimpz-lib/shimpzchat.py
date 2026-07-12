"""The brain-run core (Phase D1): drive ONE Claude Code run and stream its progress into a `Sink`.

Lifted verbatim (behaviour-identical) out of `shimpz-gateway` so a NEW client — an OpenAI-compatible
chat API, then Discord/WhatsApp — can reuse the SAME validated run pipeline without re-authoring the
brain logic per channel. The run never names Telegram: it is handed a `conversation_id`, a resolved
`lang` string, and a `shimpzchan.Sink` (the channel's live-progress renderer). Telegram's `LiveCard`
becomes the reference `TelegramSink` in the gateway; a co-resident chat-API service implements the
same `Sink` to stream a run over `/v1/chat/completions`.

Scope = the RUN phase (session continuity + checkpoint/rotate, the stream-json read loop, the status
ticker, streamed words, heartbeat, timeout kill, the Stop handle). The post-run ENDING (final answer
vs. continue/retry, a voice reply) stays in each adapter, driven by the `(final_text, outcome)` that
`run_task` returns — endings are channel-shaped. i18n is delegated to the stateless `shimpzi18n` leaf
(the caller resolves the language and passes it); this module holds no chat->language state.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import time
from pathlib import Path

import shimpzchan
import shimpzenv
import shimpzi18n
import shimpzipc  # the SHARED $SHIMPZ_HOME/ipc protocol: atomic_write
import shimpzprompt  # the shared brain-invocation constants + protocols (also imported by shimpz-run — one source)

log = logging.getLogger("shimpzchat")

# WORKDIR/CLAUDE_TIMEOUT/SHIMPZ_MAX_TURNS/MEMORY_DIR and the system prompts appended on every run are
# OWNED by shimpz-lib/shimpzprompt (declared single source, TESTS.md R79) — aliased here, never redefined.
WORKDIR = shimpzprompt.WORKDIR
CLAUDE_TIMEOUT = shimpzprompt.CLAUDE_TIMEOUT  # seconds of brain stdout silence before we kill the run
SHIMPZ_MAX_TURNS = shimpzprompt.SHIMPZ_MAX_TURNS  # caps one run's turns
MEMORY_DIR = shimpzprompt.MEMORY_DIR
MEMORY_PROTOCOL = shimpzprompt.MEMORY_PROTOCOL

SHIMPZ_HOME = os.environ.get("SHIMPZ_HOME", "/config/.shimpz")
SESS_FILE = Path(SHIMPZ_HOME) / "sessions.json"
CLAUDE_PROJECTS = Path("/config/.claude/projects")
RECENT_DIR = Path(SHIMPZ_HOME) / "recent"  # short rolling per-chat conversation summary
RECENT_TURNS = int(os.environ.get("SHIMPZ_RECENT_TURNS", "6"))
# Long-running tasks: when a run hits its step cap (SHIMPZ_MAX_TURNS), auto-continue this many times
# before falling back to the manual 'Continue?' card — so a big task finishes unattended instead of
# stalling on a button. Bounded on purpose (cost guard): after the auto-continues the card appears.
SHIMPZ_AUTO_CONTINUE = int(os.environ.get("SHIMPZ_AUTO_CONTINUE", "3"))
# Checkpoint threshold: on-disk transcript size at which we summarise-and-rotate. Generous (a long
# research task keeps its continuity across the rotation) yet far below the multi-MB rot/cost zone.
SHIMPZ_CTX_MAX_BYTES = int(os.environ.get("SHIMPZ_CTX_MAX_BYTES", "1500000"))  # checkpoint ~1.5MB
# Thinking budget per brain turn — raised for long-horizon / deep-research reasoning (was a flat 3000,
# below Claude Code's own 'think' tier). Kept env-tunable so cost can be dialled per deployment.
SHIMPZ_THINKING_TOKENS = os.environ.get("SHIMPZ_THINKING_TOKENS", "10000")
CHECKPOINT_TIMEOUT = int(os.environ.get("SHIMPZ_CHECKPOINT_TIMEOUT", "150"))  # seconds for one handoff summary
SHIMPZ_MODEL = os.environ.get("SHIMPZ_MODEL", "claude-sonnet-5")
HEARTBEAT_S = 45  # 'still working…' ping after this long on one action
# A single Telegram message can hold ~4096 chars; above this a final answer can't be edited into the
# live card, so it overflows to a .md file — the ceiling a Sink applies when streaming a preview.
MSG_MAX = int(os.environ.get("SHIMPZ_MSG_MAX_CHARS", "3800"))

# --- per-conversation run state ------------------------------------------------------------------
# A conversation runs ONE brain at a time. RUN_PROCS holds the live process so a channel's Stop
# control can kill it; CANCELLED marks a user-initiated stop so it doesn't read as a crash. SESSIONS
# (populated by _load_sessions() once it is defined, below) maps a conversation to Claude's
# session_id so the next message --resumes it.
RUN_PROCS = {}  # conversation_id -> live brain Process (for the Stop control)
CANCELLED = set()  # conversation_ids the user just Stopped (distinguish from a restart/crash)

# Handoff prompt for a mid-task checkpoint: the brain distils its own state so the fresh session
# continues seamlessly. Plain text (no markdown headers) — it's re-injected as one block.
CHECKPOINT_PROMPT = (
    "STOP and write a HANDOFF NOTE — you are about to continue this SAME task in a fresh context "
    "window and will LOSE everything not in this note. In <=350 words of plain prose (no preamble, no "
    "markdown headings): the GOAL; what is DONE; what is LEFT; the concrete facts you must not lose "
    "(file paths, URLs, selectors, IDs, decisions, where any credentials live); and the IMMEDIATE "
    "next step. Output ONLY the note."
)


# --- sessions (short-term working memory: one --resume session per conversation) -----------------
def _load_sessions():
    # fail-fast: only a missing file means "no sessions yet"; a corrupt sessions file must surface
    # (silently resetting all chats to fresh would be partial-working-with-a-bug), not be swallowed.
    if not SESS_FILE.exists():
        return {}
    return json.loads(SESS_FILE.read_text())


def _save_sessions(d):
    # Atomic (shimpzipc.atomic_write: full temp file + rename). _load_sessions fail-fasts on corrupt JSON
    # (by design), so a crash mid-write would otherwise leave truncated JSON that bricks the gateway
    # into a boot loop on restart.
    try:
        shimpzipc.atomic_write(SESS_FILE, json.dumps(d))
    except OSError as e:
        log.warning("could not persist sessions: %s", e)


SESSIONS = _load_sessions()


def _remember_session(conversation_id, evt):
    """Persist Claude's session_id (from an init/result event) so the next message --resumes it."""
    s = evt.get("session_id")
    if s:
        SESSIONS[conversation_id] = s
        _save_sessions(SESSIONS)


# --- recent window (bridges a fresh session so it still understands 'actually I meant X') --------
def _recent_path(conversation_id):
    return RECENT_DIR / ("{}.md".format(re.sub(r"[^0-9A-Za-z_-]", "_", str(conversation_id))))


def _load_recent(conversation_id):
    p = _recent_path(conversation_id)
    return p.read_text(encoding="utf-8") if p.exists() else ""  # fail-fast: only missing→empty; other errors raise


def _save_recent(conversation_id, user_text, reply):
    """Keep a tiny rolling window of the last RECENT_TURNS exchanges (truncated) for a fresh session.

    Lets a fresh session still understand 'actually I meant X' without replaying the whole
    transcript.
    """
    try:
        RECENT_DIR.mkdir(parents=True, exist_ok=True)
        cur = _load_recent(conversation_id)
        prev = cur.split("\n\n---\n\n") if cur else []
        entry = "**You:** {}\n**Shimpz:** {}".format((user_text or "").strip()[:600], (reply or "").strip()[:600])
        prev.append(entry)
        _recent_path(conversation_id).write_text("\n\n---\n\n".join(prev[-RECENT_TURNS:]), encoding="utf-8")
    except OSError as e:
        log.warning("could not save recent context: %s", e)


def _session_bytes(sid):
    """Size of the transcript Claude reloads on --resume.

    Returns 0 for no session; the byte size when found; and -1 when a session IS set but its
    transcript can't be found/measured — an ANOMALY (a Claude Code on-disk layout change, or an I/O
    error). The caller MUST treat -1 as 'rotate': a silent 0 here would mean the context cap never
    fires → unbounded growth → the exact cost blow-up the cap exists to prevent (fail-loud +
    cost-safe beats fail-silent + unbounded).
    """
    if not sid:
        return 0
    try:
        for p in CLAUDE_PROJECTS.glob(f"**/{sid}.jsonl"):
            return p.stat().st_size
    except OSError as e:
        log.warning(
            "session-size lookup FAILED for %s (%s) — rotating to a fresh session (cost-safe)",
            sid,
            e,
        )
        return -1
    log.warning(
        "no transcript found for session %s — rotating to a fresh session (Claude Code layout change?)",
        sid,
    )
    return -1


def _is_mem_sentinel(txt):
    """shimpz-memguard's protocol tokens — 'NOTHING_TO_SAVE' (nothing worth saving) / 'MEMORY_SAVED' (saved, silently).

    These must NEVER reach the user (or get spoken by TTS, or be stored as Shimpz's reply): the card
    is ONE edited message, so any post-answer memory narration would REPLACE the actual answer on
    screen (R123 — 'tem lead novo?' got answered, then overwritten by 'Pronto, salvei o padrão…').
    Filter them out of the streamed/final text; the real answer streamed earlier stays on the card.
    """
    t = (txt or "").strip().strip(".!*`# ").upper().replace(" ", "_")
    return t in ("NOTHING_TO_SAVE", "NOTHINGTOSAVE", "MEMORY_SAVED", "MEMORYSAVED")


def _kill_tree(proc):
    """SIGKILL the brain's whole process group so a Stop/timeout takes down claude AND its tool children.

    Relies on start_new_session=True at spawn. Falls back to killing just the pid if the group is
    already gone. Never raises — a race where the process already exited is fine.
    """
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)  # pgid == pid because the child is its own session leader
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


def _interrupted(rc):
    """True when the brain process was KILLED by a signal: a gateway restart / supervisor bounce, NOT a failure.

    Signals show up as 143 (128+SIGTERM), 137 (128+SIGKILL), or a negative asyncio returncode.
    """
    return rc in (143, 137) or (rc is not None and rc < 0)


def _looks_logged_out(text):
    """True only on well-known auth-failure phrases — so a normal brain error never nags about /login."""
    t = (text or "").lower()
    return any(
        s in t
        for s in (
            "invalid api key",
            "unauthorized",
            "not logged in",
            "logged out",
            "authentication_error",
            "oauth token",
            "credentials expired",
            "please login",
            "please log in",
            "run claude login",
        )
    )


def _maybe_auth_hint(final_text, err, lang="en"):
    """Append the /login hint to a no-output brain message IFF it looks like an auth failure.

    PURE. Idempotent: never double-appends. The hint is resolved in the run's `lang` (the shimpzchat
    twin of the gateway's chat_id-keyed _maybe_auth_hint, so an API run surfaces it too).
    """
    hint = shimpzi18n.t("login_auth_hint", lang=lang)
    if hint in (final_text or ""):
        return final_text
    if _looks_logged_out(err) or _looks_logged_out(final_text):
        return (final_text or "") + hint
    return final_text


def _no_output_message(rc, err, lang="en"):
    """PURE: the user-facing text when the brain produced NO output (rendered in the run's `lang`).

    A kill-signal exit means the run was interrupted by a restart — say that plainly instead of the
    alarming 'No response from the brain (exit 143)'. Otherwise report the exit + append the /login
    hint when it looks like an auth failure.
    """
    if _interrupted(rc):
        return shimpzi18n.t("restarted_msg", lang=lang)
    txt = shimpzi18n.t("no_response_msg", rc, lang=lang) + (("\n" + err) if (err or "").strip() else "")
    return _maybe_auth_hint(txt, err, lang=lang)


async def _checkpoint_summary(conversation_id, sid, sink=None, lang="en"):
    """Resume the over-cap session ONCE to distil a compact task-state handoff note.

    Returns the note, or None on any failure (the caller then bridges with the recent window / starts
    clean). Deliberately bounded — a small turn cap, minimal thinking, a short timeout: a checkpoint
    must SPEED a long task up (shed a rotting multi-MB transcript), never stall it. Its own claude
    call bypasses run_claude/_prepare_brain_run (no sink streaming, no recursion, no re-checkpoint).
    """
    if sink is not None:
        await sink.status(shimpzi18n.t("checkpointing", lang=lang))
    cmd = [
        shimpzprompt.CLAUDE,
        "-p",
        CHECKPOINT_PROMPT,
        "--model",
        SHIMPZ_MODEL,
        "--resume",
        sid,
        "--output-format",
        "text",
        "--max-turns",
        "2",
        "--dangerously-skip-permissions",
    ]
    env = shimpzenv.brain_env()
    env["SHIMPZ_MEMORY_DIR"] = str(MEMORY_DIR)
    env["SHIMPZ_SESSION_FRESH"] = "0"  # a summary is not a fresh conversation — don't fire recall
    env["MAX_THINKING_TOKENS"] = "1024"  # distilling existing state needs little thinking
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=WORKDIR,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECKPOINT_TIMEOUT)
    except (TimeoutError, OSError) as e:
        log.warning("checkpoint summary failed for %s (%s) — rotating without a handoff note", sid, e)
        if proc is not None:
            _kill_tree(proc)
        return None
    note = out.decode("utf-8", "replace").strip()
    if not note:
        log.warning("checkpoint summary for %s produced no text — rotating without a handoff note", sid)
        return None
    return note


async def _prepare_brain_run(conversation_id, prompt, force_continue, model, max_turns, lang="en", sink=None):
    """Session continuity + argv/env for ONE brain run (see run_claude's CONTINUITY-PER-CONVERSATION rules).

    Checkpoints an over-cap session (summarise + rotate), bridges a fresh one with recent context or
    the handoff note, and assembles the stream-json claude command. Returns (cmd, env).
    """
    mdl = model or SHIMPZ_MODEL
    turns = max_turns or SHIMPZ_MAX_TURNS
    sid = SESSIONS.get(conversation_id)
    handoff = None
    if sid:
        # _session_bytes stats the transcript claude reloads on --resume — measure ONCE, off the loop.
        # Checked on EVERY run (incl. force_continue) so an auto-continuing long task can't balloon the
        # transcript into the rot/cost zone. sz == -1 = "can't measure" → treat as over-cap and rotate.
        sz = await asyncio.to_thread(_session_bytes, sid)
        if sz < 0 or sz > SHIMPZ_CTX_MAX_BYTES:
            log.info("session %s ~%dKB >= cap — checkpoint + rotate", sid, max(sz, 0) // 1024)
            handoff = await _checkpoint_summary(conversation_id, sid, sink, lang)
            SESSIONS.pop(conversation_id, None)
            _save_sessions(SESSIONS)
            sid = None
    fresh = sid is None
    task = prompt
    if fresh and handoff:
        # Mid-task checkpoint: seed the fresh session with the brain's OWN handoff note so it continues
        # the same task seamlessly instead of losing the thread.
        task = (
            f"## Task handoff — you were mid-task; CONTINUE seamlessly from this state:\n{handoff}"
            f"\n\n## Now do this\n{prompt}"
        )
    elif fresh:
        # A new conversation (no prior session to checkpoint): bridge with the short recent window.
        recent = _load_recent(conversation_id)
        if recent.strip():
            task = f"## Recent conversation context (reference)\n{recent.strip()}\n\n## Your task now\n{prompt}"
    cmd = shimpzprompt.brain_cmd(task, mdl, MEMORY_PROTOCOL, turns)
    if sid:
        cmd += ["--resume", sid]
    env = shimpzenv.brain_env()
    env["SHIMPZ_MEMORY_DIR"] = str(MEMORY_DIR)
    # Recall only fires on a fresh session (new conversation / post-checkpoint), not every turn.
    env["SHIMPZ_SESSION_FRESH"] = "1" if fresh else "0"
    env["MAX_THINKING_TOKENS"] = SHIMPZ_THINKING_TOKENS
    return cmd, env


async def _consume_event(evt, conversation_id, sent, say, tick, final_text, subtype, lang):
    """Apply ONE stream-json event: remember session ids, tick the tool-status line, stream new answer text.

    "New" means non-sentinel and not yet seen. Returns the updated (final_text, subtype).
    """
    match evt.get("type"):
        case "system" if evt.get("subtype") == "init":
            _remember_session(conversation_id, evt)
        case "assistant":
            for blk in evt.get("message", {}).get("content", []):
                if blk.get("type") == "text":
                    txt = (blk.get("text") or "").strip()
                    if txt and not _is_mem_sentinel(txt) and txt not in sent:
                        sent.add(txt)
                        final_text = txt
                        await say(txt)
                elif blk.get("type") == "tool_use":
                    await tick(shimpzi18n.status_for(blk.get("name"), blk.get("input"), lang=lang))
        case "result":
            subtype = evt.get("subtype", "success")
            _remember_session(conversation_id, evt)
            r = (evt.get("result") or "").strip()
            if r and not _is_mem_sentinel(r):
                final_text = r
                if r not in sent:
                    sent.add(r)
                    await say(r)
    return final_text, subtype


async def _relay_stream(proc, sink, conversation_id, lang):
    """The stream-json read loop of one brain run: status ticker + streamed words INTO the sink, plus a heartbeat.

    Heartbeats while a long tool call is silent; kills after CLAUDE_TIMEOUT of continuous stdout
    silence. Returns (final_text, subtype).
    """
    last_status = None
    final_text = ""
    subtype = "success"
    sent = set()

    async def tick(label):
        # The sink shows the current action and carries the Stop control (always interruptible).
        nonlocal last_status
        if label == last_status:
            return
        last_status = label
        await sink.status(label)

    async def say(txt):
        # Stream Shimpz's words INTO the sink (the sink applies its own MSG_MAX preview ceiling). A block
        # too big to fit one message is previewed; the definitive full delivery happens in the adapter.
        await sink.stream(txt)

    # Kill only after CLAUDE_TIMEOUT of CONTINUOUS stdout silence; reset on every line received.
    deadline = time.monotonic() + CLAUDE_TIMEOUT
    while True:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=HEARTBEAT_S)
        except TimeoutError:
            # No stdout for HEARTBEAT_S. A single long tool call emits no stream events, so this is
            # the ONLY place the sink can update while the brain is genuinely working — never go dark.
            # If the silence has run past the overall deadline, the brain is stuck → kill it.
            if time.monotonic() >= deadline:
                _kill_tree(proc)
                subtype = "timeout"
                break
            await sink.heartbeat(last_status or "")
            continue
        except ValueError:  # readline re-raises LimitOverrunError as ValueError
            continue  # oversized line (e.g. a big screenshot tool-result) — skip it
        if not line:
            break
        deadline = time.monotonic() + CLAUDE_TIMEOUT  # progress → push the silence deadline out
        try:
            evt = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        final_text, subtype = await _consume_event(evt, conversation_id, sent, say, tick, final_text, subtype, lang)
        # (heartbeat fires from the readline-timeout branch above — see the HEARTBEAT_S wait_for)
    return final_text, subtype


async def run_claude(
    conversation_id, prompt, sink: shimpzchan.Sink, lang, force_continue=False, model=None, max_turns=None
):
    """Run the brain with stream-json and relay LIVE progress into a `sink`, returning (final_text, subtype).

    The sink carries a status ticker per action, Shimpz's words as they arrive, a heartbeat, and the
    run's Stop handle (the brain pid). Default model Sonnet (SHIMPZ_MODEL); `model`/`max_turns` override
    per run.

    CONTINUITY-PER-CONVERSATION: a conversation keeps ONE session (--resume) so Shimpz naturally
    'picks up where it left off' — short-term working context lives in the session, NOT in
    long-term memory. When the transcript passes SHIMPZ_CTX_MAX_BYTES we CHECKPOINT: the brain distils
    a handoff note and we continue in a fresh session seeded with it (bounded cost + no lost thread;
    checked on every run so an auto-continuing task can't balloon). On a FRESH session (new
    conversation or just-checkpointed) the shimpz-recall hook injects long-term memory and we bridge
    with the handoff note or the recent summary; a RESUMED session skips both (context already there).
    force_continue=True ('▶️ Continuar') resumes to finish a task, but still checkpoints if over-cap.
    """
    cmd, env = await _prepare_brain_run(conversation_id, prompt, force_continue, model, max_turns, lang=lang, sink=sink)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=WORKDIR,
            env=env,
            limit=2**24,  # roomy buffer: screenshot results are big
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group so a Stop/timeout kills the WHOLE brain tree (claude + its Bash/chrome/
            # build children), not just the claude pid — otherwise a 🛑 Stop leaves the running action
            # (e.g. an rsync) orphaned to init and it completes invisibly. See _kill_tree().
            start_new_session=True,
        )
    except OSError as e:
        # Never fail silently — the user must SEE that the brain didn't start.
        msg = shimpzi18n.t("couldnt_start_brain", e, lang=lang)
        await sink.start_failed(msg)
        return (msg, "error")

    # Register the live process so a Stop control can kill THIS run, and hand the sink the run's
    # identity (the pid rides on the Stop button so a stale tap for a finished run can't kill this one).
    RUN_PROCS[conversation_id] = proc
    await sink.bind_run(proc.pid)
    await sink.thinking()  # instant feedback before the first tool runs (the sink decides idempotency)

    # Drain stderr CONCURRENTLY. The read loop only awaits stdout; stderr is a PIPE with a ~64KB kernel
    # buffer. If the brain (or a per-turn hook / Node noise) writes >64KB to stderr before the run ends,
    # the child blocks on write(2), stops producing stdout, and the loop would hang until the 900s
    # timeout kills an otherwise-finished run. This reader keeps the last 8KB (enough for the no-output
    # error message) so the pipe never fills.
    stderr_tail = bytearray()

    async def _drain_stderr():
        while True:
            try:
                chunk = await proc.stderr.read(65536)
            except OSError:
                return
            if not chunk:
                return
            stderr_tail.extend(chunk)
            if len(stderr_tail) > 8192:
                del stderr_tail[:-8192]

    stderr_task = asyncio.get_running_loop().create_task(_drain_stderr())

    try:
        final_text, subtype = await _relay_stream(proc, sink, conversation_id, lang)
    finally:
        RUN_PROCS.pop(conversation_id, None)
        with contextlib.suppress(OSError):
            await proc.wait()
        # the child has exited → stderr is at EOF → the drainer returns promptly; bound it and cancel
        # defensively so a stuck reader can never wedge the finally.
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(stderr_task, timeout=2)  # cancels the task itself on timeout

    # User tapped Stop → this run was killed on purpose. Report it as 'cancelled' (a distinct subtype)
    # so the no-output path below doesn't mislabel it a crash and the adapter's ending stays quiet.
    if conversation_id in CANCELLED:
        CANCELLED.discard(conversation_id)
        msg = shimpzi18n.t("stopped_msg", lang=lang)
        await sink.stopped(msg)
        return ("", "cancelled")

    if not final_text and subtype not in ("error_max_turns", "timeout"):
        err = bytes(stderr_tail).decode("utf-8", "replace")[-500:]  # from the concurrent drainer
        rc = proc.returncode
        final_text = _no_output_message(rc, err, lang=lang)
        if _interrupted(rc):
            log.info(
                "brain run interrupted by a signal (rc=%s) — likely a gateway restart",
                rc,
            )
        else:
            log.warning("brain produced no output: rc=%s stderr=%s", rc, err[-200:])
        # NOT streamed — the adapter's ending delivers this (never a silent 'it's off').
    return (final_text, subtype)


async def run_task(conversation_id, prompt, sink: shimpzchan.Sink, lang, **kw):
    """Run the brain for a USER task, AUTO-CONTINUING past its step cap so a long job finishes on its own.

    If a run hits this run's step cap (error_max_turns), auto-continue up to SHIMPZ_AUTO_CONTINUE
    times instead of stalling on a 'Continue?' button every SHIMPZ_MAX_TURNS turns. Any non-cap
    outcome (success/timeout/error) returns at once. If it STILL hits the cap after the
    auto-continues, we return error_max_turns unchanged so the adapter shows the manual Continue/Stop
    affordance — a bounded human checkpoint (cost guard). The auto-continue notice streams through
    the sink so it edits the same live message in place.
    """
    final_text, subtype = await run_claude(conversation_id, prompt, sink, lang, **kw)
    autos = 0
    while subtype == "error_max_turns" and autos < SHIMPZ_AUTO_CONTINUE:
        autos += 1
        await sink.status(shimpzi18n.t("auto_continue_msg", autos, SHIMPZ_AUTO_CONTINUE, lang=lang))
        final_text, subtype = await run_claude(
            conversation_id,
            "Continue exactly from where you left off.",
            sink,
            lang,
            force_continue=True,
        )
    return final_text, subtype


def stop(conversation_id):
    """Kill the live brain run for a conversation + mark it CANCELLED (the channel's Stop control).

    Takes down claude AND its tool children (the action being performed), and flags the stop so the
    run loop reports 'cancelled' rather than a crash. A no-op when nothing is running.
    """
    proc = RUN_PROCS.get(conversation_id)
    if proc:
        _kill_tree(proc)
        CANCELLED.add(conversation_id)
