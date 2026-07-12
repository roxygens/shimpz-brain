---
task: bootstrap a new code project (backend / API / fullstack with DB)
triggers: novo projeto, new project, bootstrap, script, api, backend, fastapi, python, uv, banco, database, postgres, cadastro, signup, fullstack, crud, auth
updated: 2026-06-29
---
# Project bootstrap — reference: layout, stack table, exact commands

> CLAUDE.md's dev-rules already state the golden path (`shimpz-new` first, isolated DB, ask-first
> security, supervised deploy) as the single source of truth — this playbook doesn't restate it.
> What's here is what CLAUDE.md doesn't carry: the exact file tree, the stack table, and
> copy-paste commands/snippets.

```bash
shimpz-new <name> [fullstack|api|web|script]    # default: fullstack — scaffolds the whole skeleton
```

## Canonical layout (use this exactly — keep it tidy)
```
projects/<name>/         # ← THIS folder is its OWN git repo (one repo per project)
├── .git/                # auto: `git init` + auto-commit on every Stop (shimpz-project-sync)
├── .gitignore           # excludes .env, node_modules, .venv, builds (auto-seeded if absent)
├── README.md            # what it is + how to run
├── .env                 # secrets incl. DATABASE_URL (gitignored — NEVER committed)
├── .env.example         # committed, no values
├── backend/             # FastAPI + uv  (uv init here, --vcs none: no nested repo)
│   ├── pyproject.toml
│   └── app/
│       ├── main.py      # FastAPI() app + routers; `uv run uvicorn app.main:app`
│       ├── config.py    # pydantic-settings (reads ../.env)
│       ├── db.py        # SQLAlchemy 2.0 engine + Session (psycopg3)
│       ├── models.py    # ORM models (declarative)
│       ├── schemas.py   # pydantic v2 request/response models
│       └── routers/     # one module per resource (e.g. signup.py)
└── frontend/            # SvelteKit (Svelte 5 + Vite + Tailwind v4) — see frontend-svelte
```
Backend-only project? Drop `frontend/`. Script-only? Just the folder + `app/`/`main.py`.

## Git commands (shimpz-project-sync auto-commits every Stop — these are for a milestone commit or a remote)
```bash
git -C /config/workspace/projects/<name> add -A && git -C /config/workspace/projects/<name> commit -m "feat: signup flow"
git -C /config/workspace/projects/<name> remote add origin <url>   # opt in a project to a remote (ask first)
```
`uv init --vcs none` for the backend (no nested repo); `rm -rf frontend/.git` if a SvelteKit scaffold drops one.

## Isolated database — commands (`shimpz-db`, never the infra `shimpz-brain` DB)
```bash
shimpz-db create <name>        # creates proj_<name>, prints its DATABASE_URL → paste into .env
shimpz-db psql <name>          # psql shell;  shimpz-db list / shimpz-db url <name> / shimpz-db drop <name>
```

## Standard stack (choose from here — never guess, never raw drivers)
| Need | Lib |
|---|---|
| service / API / webhook | **FastAPI** + **uvicorn** |
| HTTP client | **httpx** (async+sync) — not requests |
| models / validation | **pydantic v2** + **pydantic-settings** (typed config from .env) |
| Postgres / ORM | **SQLAlchemy 2.0** + **psycopg3** — NEVER raw `psycopg.connect` in handlers |
| migrations | **alembic** (`alembic init`, autogenerate) — schema in code, versioned |
| password hashing / auth | **pwdlib[argon2]** (or passlib argon2); JWT via **pyjwt** |
| retries | **tenacity** · CLI | **typer** · scheduler | **APScheduler** (in-process) · tests | **pytest** |
```bash
cd projects/<name>/backend && uv init --vcs none . && \   # --vcs none: no nested git repo
uv add fastapi uvicorn "sqlalchemy>=2" "psycopg[binary]" pydantic pydantic-settings httpx alembic && \
uv add --dev pytest
```

## Secrets / config (always)
Per-project `.env` (gitignored) + `.env.example` (committed, no values). Read via pydantic-settings:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env")
    database_url: str
settings = Settings()
```
NEVER hardcode. Shared infra creds arrive as env vars — read, don't copy.

## Security checklist — once the shimpz-ask decision (CLAUDE.md) says "needs auth"
```bash
need=$(shimpz-ask "Does this backend handle private/user data that must be protected?" \
  "🌐 No — public/marketing, open read API is fine" "🔒 Yes — needs auth + per-user access control")
```
- **🌐 Public/marketing → no auth.** Just don't expose write/admin/mutating endpoints without a key, and
  don't return anything that isn't meant to be public.
- **🔒 Private/user data → these are mandatory:**
  - **AuthN:** hash passwords with **argon2** (`pwdlib[argon2]`); issue short-lived **JWT** (`pyjwt`, set
    `exp`); verify on every protected route via a single `Depends(get_current_user)`.
  - **AuthZ — object-level, the #1 API bug:** every handler that loads a row by id MUST check the row
    belongs to the caller (filter `WHERE owner_id = current_user.id`). Never trust a client-supplied id to
    be theirs (OWASP API #1 — BOLA/IDOR).
  - **Rate-limit** login / signup / token endpoints (e.g. `slowapi`) — brute-force guard.
  - **Cookies (if you set any):** `httponly=True, secure=True, samesite="lax"`.
- **Secrets are GENERATED, never literals** (any project, auth or not):
  `SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')` → into `.env` (gitignored).
  NEVER a placeholder like `"secret"`/`"changeme"` in code.
- **CORS:** the canonical fullstack shape is same-origin (`/api` via Caddy) → you need NO CORS at all. If
  you ever add `CORSMiddleware`, list explicit origins — **never** `allow_origins=["*"]` with
  `allow_credentials=True` (that combination leaks credentials).
- **Prod hygiene:** no `--reload`, no `debug=True`, don't echo stack traces to clients (keep FastAPI's
  opaque default 500). Commit `uv.lock` so installs are reproducible.

## Run + deploy — exact commands (the gates themselves are in CLAUDE.md)
- **DATABASE_URL for the supervised app:** the infra Postgres DSN is `SHIMPZ_PG_DSN` (used by `shimpz-db`),
  NOT `DATABASE_URL` — so nothing global shadows your project. The app reads `DATABASE_URL` from its
  own `.env` (pydantic-settings). For the SUPERVISED process, pass it explicitly so it's bulletproof
  regardless of CWD/env_file (the DSN is the project's **least-privilege role**, not the superuser, so
  inlining it here is low-risk):
  ```bash
  shimpz-app deploy <name>-api <port> -- env DATABASE_URL="$(shimpz-db url <name>)" \
      uv run --project backend uvicorn app.main:app --host 127.0.0.1 --port <port> --app-dir backend
  ```
  Port 3100–3999. (Run `uv run` against the `backend/` project.)
- **Schema = alembic migrations**, not `Base.metadata.create_all` (dev-only): `uv run alembic init
  alembic`, `alembic revision --autogenerate -m "init"`, `alembic upgrade head` (run on deploy).
- Frontend: SvelteKit calls the API at a **relative `/api/...`** (never a hardcoded host/port). Build →
  `shimpz-app deploy <name>-web <port> -- python3 -m shimpz_static <port> --directory build`
  (structured JSON access logs; `http.server` is REFUSED by the driver — its stderr access
  lines land level=error in VictoriaLogs).
- Expose a fullstack app in ONE shot: **`shimpz-publish <fqdn> <web-port> public <api-port>`** — Caddy
  serves the front and routes `/api/*` → the backend (strip_prefix), so the front's relative `/api`
  calls just work, local and live. Tear down with **`shimpz-unpublish <fqdn>`** (+ `shimpz-app rm`, `shimpz-db drop`).
- Recurring jobs → a **cron** entry running `uv run …` (self-heals on crash); long-running → `shimpz-app`.

Keep code minimal (ponytail): YAGNI → reuse → stdlib → only then new code. But the STRUCTURE above
is non-negotiable — a tidy, isolated project is the fast path and what makes Shimpz reliable long-term.
