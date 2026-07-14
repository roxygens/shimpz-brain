#!/usr/bin/with-contenv bash
# shellcheck shell=bash  # s6-overlay's `with-contenv` wrapper isn't a shell shellcheck recognizes
# One-shot init (runs as root before services start). Seeds the persistent SHIMPZ_HOME on
# first boot and fixes ownership so the unprivileged desktop user (abc / PUID) owns its
# config, logs and workspace.
set -euo pipefail

NH="${SHIMPZ_HOME:-/config/.shimpz}"
mkdir -p "$NH" "$NH/logs"
capsule_mode=0
[ -n "${SHIMPZ_CAPSULE_ID:-}" ] && capsule_mode=1
if [ "$capsule_mode" -eq 1 ]; then
    # Root-only, container-lifecycle chat admission state. It must never live below /config: the
    # hostile tenant owns that persistent tree and could rename or pre-create any apparent lock.
    install -d -o root -g root -m 0700 /run/shimpz-chat
fi

# SECURITY (re-assert the Dockerfile hardening at boot): keep the agent user OUT of `sudo`/`docker`
# even if a base-image init stage re-adds it. The LSIO base grants `%sudo NOPASSWD: ALL`, so sudo-group
# membership = passwordless root gated only by no-new-privileges; root must stay unreachable to `abc`.
gpasswd -d abc sudo 2>/dev/null || true
gpasswd -d abc docker 2>/dev/null || true
sed -i 's/^%sudo/# shimpz-disabled: %sudo/' /etc/sudoers 2>/dev/null || true

# The image-owned `/defaults/autostart` is launched directly by svc-shimpz-headless. It is never copied
# into persistent /config, so an old openbox session file cannot pin startup logic across upgrades.

# Profile .env carries the secrets Shimpz + the helper CLIs read. Idempotent UPSERT from the
# container environment — a one-shot seed wouldn't propagate keys added after first boot,
# since /config is persistent.
touch "$NH/.env"; chmod 600 "$NH/.env"
upsert() {  # upsert KEY VALUE  (no-op when VALUE is empty)
    local k="$1" v="${2:-}"
    [ -n "$v" ] || return 0
    # Rewrite WITHOUT interpolating VALUE into a sed expression — a value containing #, &, \ or / would
    # otherwise corrupt $NH/.env silently (the old `sed s#..#..#` did). Fail-fast: no silent corruption.
    # Drop any existing line for this key, then append the fresh value verbatim (printf %s = literal).
    { grep -v "^${k}=" "$NH/.env" 2>/dev/null || true; printf '%s=%s\n' "$k" "$v"; } > "$NH/.env.tmp"
    mv "$NH/.env.tmp" "$NH/.env"; chmod 600 "$NH/.env"
}
# Brain-provider credentials are account-scoped and are written only into the matching Capsule's
# private /config. ANTHROPIC_API_KEY, OPENAI_API_KEY and VOICE_TOOLS_OPENAI_KEY are therefore NOT
# seeded here. Platform voice/image calls use the separate openai-driver container.
upsert TELEGRAM_BOT_TOKEN      "${TELEGRAM_BOT_TOKEN:-}"
upsert TELEGRAM_ALLOWED_USERS  "${TELEGRAM_ALLOWED_USERS:-}"
# SHIMPZ_CF_TOKEN/SHIMPZ_CF_ACCOUNT are NOT seeded here (SECURITY_ENGINEERING_PLAN.md item 3) — they live
# only in cf-driver's own env now, never `shimpz-brain`'s. GITHUB_TOKEN was removed too (item 2):
# unused — no script ever read it, and no ~/.git-credentials-based auth depended on it.
# IPROYAL_PROXY_*/CHROME_EXTRA_ARGS/CDP_URL are NOT seeded here either (item 0) — Chrome and its
# proxy credential moved entirely to the separate `shimpz-browser` container.

# SELF-HEAL (SECURITY_ENGINEERING_PLAN.md item 8): $NH/.env is a WHITELIST — the brain legitimately holds
# ONLY the keys upsert-ed just above. Anything else that ever lands here (a secret an OLDER image seeded
# before it moved to a sidecar — items 0/3/7 — or ANY FUTURE migration) is stripped on every boot; left
# in place it is fully readable by the brain's Bash tool, the exact prompt-injection exposure each split
# removes. Deriving the scrub from the allow-set (not a hardcoded denylist) means a newly-migrated secret
# is covered AUTOMATICALLY — closing the "remember to add it to the scrub list" gap. Safe because
# 10-shimpz-init.sh is the SOLE writer of this file (shimpzenv.py + the gateway/CLIs only ever read it).
# Confirmed live: SHIMPZ_CF_TOKEN (item 3) was still in $NH/.env long after it left compose. Idempotent.
SHIMPZ_BRAIN_KEYS="TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USERS"
[ "$capsule_mode" -eq 1 ] && SHIMPZ_BRAIN_KEYS=""
# Snapshot the present key names FIRST, then scrub — never rewrite the file while iterating over it.
_present_keys="$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$NH/.env" 2>/dev/null || true)"
for _key in $_present_keys; do
    case " $SHIMPZ_BRAIN_KEYS " in *" $_key "*) continue ;; esac  # a legitimate brain key — keep it
    { grep -v "^${_key}=" "$NH/.env" || true; } > "$NH/.env.tmp"
    mv "$NH/.env.tmp" "$NH/.env"
    echo "[shimpz-init] scrubbed non-brain secret ${_key} from \$SHIMPZ_HOME/.env"
done
chmod 600 "$NH/.env"

# Cron daemon keepalive: the LSIO `svc-cron` service ONLY launches the cron daemon if a crontab
# already exists when it starts. Without this, a `crontab -e` job Shimpz adds later silently never runs
# (the daemon isn't up). Seed a keepalive entry for the runtime user (this oneshot runs before
# svc-cron), so crond always starts and picks up Shimpz's scheduled jobs.
if command -v crontab >/dev/null 2>&1; then
    if [ -z "$(crontab -u abc -l 2>/dev/null)" ]; then
        printf '%s\n' '# shimpz-brain: keepalive so later crontab jobs actually execute' \
            | crontab -u abc - 2>/dev/null && echo "[shimpz-init] seeded cron keepalive (abc)"
    fi
fi

# Shimpz's working dir (generated PDFs, uploads, drafts). Owned by the runtime user so the
# agent can write to it. registry/ must EXIST before any app deploy: the driver bind-mounts
# it READ-ONLY into every app container (docker refuses a bind whose host source is missing);
# writes happen only at deploy time on the brain (shimpz-app runs the project's register.py).
mkdir -p /config/workspace /config/workspace/projects /config/workspace/out /config/workspace/registry
instructions=/defaults/shimpz/CLAUDE.md
memory_index=/defaults/shimpz/memory/MEMORY.md
settings=/defaults/shimpz/settings.json
if [ -n "${SHIMPZ_CAPSULE_ID:-}" ]; then
    capsule_mode=1
    instructions=/defaults/shimpz/CAPSULE.md
    memory_index=/defaults/shimpz/CAPSULE_MEMORY.md
    settings=/defaults/shimpz/CAPSULE_SETTINGS.json
fi
# A public tenant Capsule must never inherit the platform operator's identity, preferences, projects,
# or operational playbooks. Both providers read this tenant-safe file from the private workspace.
if [ -f "$instructions" ]; then
    cp "$instructions" /config/workspace/CLAUDE.md
fi
chown -R "${PUID:-1000}:${PGID:-1000}" /config/workspace

# --- Infinity memory --------------------------------------------------------------------
# Long-term memory store — one file per project (projects/<slug>.md) plus a small cross-project
# set (procedural playbooks + semantic facts) — that survives the per-task session reset. Seed the
# index ONLY if absent — never clobber learnings Shimpz accumulated.
MEMDIR="$NH/memory"
mkdir -p "$MEMDIR/playbooks" "$MEMDIR/facts" "$MEMDIR/projects" "$NH/recent"
tenant_migration="$NH/.capsule-tenant-safe-v1"
if [ "$capsule_mode" -eq 1 ] && [ ! -e "$tenant_migration" ]; then
    # Older Capsule volumes were seeded from the operator image. Remove only the shipped namespaces
    # once, then seed the tenant-safe index below; unrelated Captain-created memory remains intact.
    rm -f "$MEMDIR/MEMORY.md"
    for namespace in playbooks facts projects; do
        if [ -d "/defaults/shimpz/memory/$namespace" ]; then
            for shipped in "/defaults/shimpz/memory/$namespace"/*; do
                [ -e "$shipped" ] || continue
                shipped_name="$(basename "$shipped")"
                rm -rf -- "${MEMDIR:?}/$namespace/$shipped_name"
            done
        fi
    done
    rm -rf /config/.claude/skills "$NH/caddy" "$NH/supervisor"
    touch "$tenant_migration"
fi
if [ ! -f "$MEMDIR/MEMORY.md" ] && [ -f "$memory_index" ]; then
    cp "$memory_index" "$MEMDIR/MEMORY.md"
    echo "[shimpz-init] seeded memory index"
fi

# The shared platform image contains the operator channel and its private defaults. A tenant Capsule
# has no use for those surfaces: remove them before its provider starts so even a hostile prompt cannot
# inspect an identity, domain, Telegram channel, platform playbook or platform-only deployment tool.
if [ "$capsule_mode" -eq 1 ]; then
    rm -rf /defaults/shimpz/CLAUDE.md /defaults/shimpz/memory /defaults/shimpz/skills \
        /defaults/shimpz/caddy /defaults/shimpz/supervisor
    rm -f /opt/shimpz-lib/shimpzaudit.py /opt/shimpz-lib/shimpzprompt.py \
        /usr/local/bin/shimpz-app /usr/local/bin/shimpz-approve /usr/local/bin/shimpz-depaudit \
        /usr/local/bin/shimpz-gateway /usr/local/bin/shimpz-logaudit /usr/local/bin/shimpz-project-sync \
        /usr/local/bin/shimpz-publish /usr/local/bin/shimpz-run /usr/local/bin/shimpz-secaudit \
        /usr/local/bin/shimpz-stdgate /usr/local/bin/shimpz-tempcred /usr/local/bin/shimpz-tg \
        /usr/local/bin/shimpz-unpublish
fi
# Seed starter playbooks (golden-path conventions) — only if not already present, so a refined
# copy is never clobbered. These define the project structure so Shimpz never reinvents it.
if [ "$capsule_mode" -eq 0 ] && [ -d /defaults/shimpz/memory/playbooks ]; then
    # CORE convention playbooks are repo-authoritative: ALWAYS refresh them from /defaults so the
    # battle-tested standard (structure, stack, deploy) reaches Shimpz via recall — otherwise a stale
    # Shimpz-refined copy in /config silently overrides the repo. Shimpz's OWN playbooks (anything not in
    # this list) are seed-if-absent so its accumulated learnings persist.
    CORE_PB="dev-bootstrap.md frontend-svelte.md deploy-domain.md"
    # R106 rename migration (deploy-dominio → deploy-domain, integracao-fila-redpanda →
    # redpanda-queue-integration): drop the old-named copies from the volume, or recall would keep
    # surfacing a stale duplicate alongside the renamed playbook.
    rm -f "$MEMDIR/playbooks/deploy-dominio.md" "$MEMDIR/playbooks/integracao-fila-redpanda.md"
    for pb in /defaults/shimpz/memory/playbooks/*.md; do
        [ -e "$pb" ] || continue
        base="$(basename "$pb")"; dst="$MEMDIR/playbooks/$base"
        case " $CORE_PB " in
            *" $base "*) cp "$pb" "$dst" ;;     # core → always authoritative
            *) [ -f "$dst" ] || cp "$pb" "$dst" ;;
        esac
    done
fi

# --- Claude Code skills (repo-authoritative design/UI skills) ---------------------------
# Shimpz IS a Claude Code instance; a personal skill lives at $HOME/.claude/skills/<name>/SKILL.md
# and auto-triggers when a prompt matches its description. The dir MUST exist before the brain's
# first `claude -p` (else headless directory-watching isn't armed) — cont-init runs before the
# gateway, so seeding here satisfies that. These skills are repo-maintained → ALWAYS refresh from
# /defaults (like the CORE playbooks) so a stale /config copy can't shadow the shipped standard.
if [ "$capsule_mode" -eq 0 ] && [ -d /defaults/shimpz/skills ]; then
    mkdir -p /config/.claude/skills
    for sk in /defaults/shimpz/skills/*/; do
        [ -d "$sk" ] || continue
        name="$(basename "$sk")"
        rm -rf "/config/.claude/skills/$name"
        cp -a "$sk" "/config/.claude/skills/$name"
    done
    chown -R "${PUID:-1000}:${PGID:-1000}" /config/.claude/skills
    echo "[shimpz-init] refreshed Claude Code skills"
fi

# --- App hosting: Caddy (reverse proxy) + supervisor (app process manager) --------------
# Shimpz brings up its web apps under supervisor (one port each) and routes hostnames through
# Caddy (behind the single Cloudflare Tunnel). Seed the BASE configs only if absent — Shimpz
# adds apps/routes at runtime (supervisor/apps/*.conf, caddy/sites/*.caddy); never clobber them.
if [ "$capsule_mode" -eq 0 ]; then
    mkdir -p "$NH/supervisor/apps" "$NH/caddy/sites"
    if [ ! -f "$NH/supervisor/supervisord.conf" ] && [ -f /defaults/shimpz/supervisor/supervisord.conf ]; then
        cp /defaults/shimpz/supervisor/supervisord.conf "$NH/supervisor/supervisord.conf"
        echo "[shimpz-init] seeded supervisord.conf"
    fi
    if [ ! -f "$NH/caddy/Caddyfile" ] && [ -f /defaults/shimpz/caddy/Caddyfile ]; then
        cp /defaults/shimpz/caddy/Caddyfile "$NH/caddy/Caddyfile"
        echo "[shimpz-init] seeded Caddyfile"
    fi
fi

# Claude Code settings: hooks (mandatory recall + write-back) + model + auto-memory dir.
# Merge our authoritative keys into any existing settings so the desktop user's theme etc. survive.
if [ -f "$settings" ]; then
    mkdir -p /config/.claude
    SHIMPZ_SETTINGS="$settings" /opt/venv/bin/python3 <<'PY' || true
import json, os
dst = "/config/.claude/settings.json"
defaults = json.load(open(os.environ["SHIMPZ_SETTINGS"]))
cur = {}
if os.path.isfile(dst):
    try: cur = json.load(open(dst))
    except Exception: cur = {}
cur.update(defaults)            # model, autoMemoryDirectory, hooks are ours to own
json.dump(cur, open(dst, "w"), indent=2)
PY
    echo "[shimpz-init] merged Claude Code settings (hooks + memory)"
fi

# Per-project git defaults for Shimpz: branch `main` + the Shimpz identity. This makes `uv init` (which
# runs its own `git init` before the Stop hook ever sees the project) create `main`, and lets Shimpz's
# own milestone `git commit`s carry the right author. The Stop hook `shimpz-project-sync` still
# auto-commits every project regardless. Idempotent; file must be owned by abc (root writes it here).
HOME=/config git config --global init.defaultBranch main 2>/dev/null || true
if [ "$capsule_mode" -eq 1 ]; then
    HOME=/config git config --global user.name "Shimpz Capsule" 2>/dev/null || true
    HOME=/config git config --global user.email "capsule@shimpz.local" 2>/dev/null || true
else
    HOME=/config git config --global user.name  "${SHIMPZ_GIT_NAME:-Shimpz}" 2>/dev/null || true
    HOME=/config git config --global user.email "${SHIMPZ_GIT_EMAIL:-shimpz-brain@roxygens.com}" 2>/dev/null || true
fi
chown "${PUID:-1000}:${PGID:-1000}" /config/.gitconfig 2>/dev/null || true

# Ponytail: "lazy senior dev" ruleset (YAGNI / stdlib-first / minimal code) — enforced via its
# own harness hooks, so it applies headless. Fewer tokens, more assertive output. Idempotent,
# best-effort (skips offline). Installs into the persistent /config/.claude so it survives.
if [ "$capsule_mode" -eq 0 ] && [ "${SHIMPZ_PONYTAIL:-1}" != "0" ]; then
    if ! HOME=/config /usr/local/bin/claude plugin list 2>/dev/null | grep -qi ponytail; then
        HOME=/config /usr/local/bin/claude plugin marketplace add DietrichGebert/ponytail >/dev/null 2>&1 \
          && HOME=/config /usr/local/bin/claude plugin install ponytail@ponytail >/dev/null 2>&1 \
          && echo "[shimpz-init] installed ponytail plugin" \
          || echo "[shimpz-init] ponytail install skipped (offline?)"
    fi
fi

# Frontend Design: Anthropic's official skill that kills the "AI slop" look (generic Inter fonts,
# default purple gradients, low-contrast cards, no hierarchy/brand). It auto-triggers whenever Shimpz
# builds any UI and pushes distinctive, production-grade design. Idempotent, best-effort (skips offline).
if [ "$capsule_mode" -eq 0 ] && [ "${SHIMPZ_FRONTEND_DESIGN:-1}" != "0" ]; then
    if ! HOME=/config /usr/local/bin/claude plugin list 2>/dev/null | grep -qi frontend-design; then
        HOME=/config /usr/local/bin/claude plugin marketplace add anthropics/claude-code >/dev/null 2>&1 \
          && HOME=/config /usr/local/bin/claude plugin install frontend-design@claude-code-plugins >/dev/null 2>&1 \
          && echo "[shimpz-init] installed frontend-design plugin" \
          || echo "[shimpz-init] frontend-design install skipped (offline?)"
    fi
fi

# Garden the memory once a day (prune stale entries / sentinels). Self-throttled; safe each boot.
SHIMPZ_MEMORY_DIR="$MEMDIR" /opt/venv/bin/python3 /usr/local/bin/shimpz-mem-gc 2>/dev/null || true

chown -R "${PUID:-1000}:${PGID:-1000}" /config/.claude "$NH"
# Self-heal ownership of the runtime user's TOOL CACHES. If anything ever runs as root in the
# container (e.g. an operator `docker exec` for debugging), it can leave root-owned files in
# these caches that then break `npx`/`uv`/`claude` for the abc user (EACCES). Cheap to re-assert.
for d in /config/.npm /config/.cache /config/.config/uv /config/.config/pnpm /config/.local; do
    [ -e "$d" ] && chown -R "${PUID:-1000}:${PGID:-1000}" "$d" 2>/dev/null || true
done
echo "[shimpz-init] done"
