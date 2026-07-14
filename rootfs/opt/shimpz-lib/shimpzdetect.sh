# shellcheck shell=bash
# shimpzdetect.sh — helpers shared by the deploy/publish CLIs (shimpz-app, shimpz-publish, and
# shimpz-unpublish). Sourced as:
#
#     . "${SHIMPZ_LIB:-/opt/shimpz-lib}/shimpzdetect.sh"
#
# In the container /opt/shimpz-lib is the real path (rootfs/ is COPY'd wholesale); the host test
# harness (tests/lib/shimpztest.sh) exports SHIMPZ_LIB pointing at the repo's rootfs copy — the same
# seam the python libs here already use. Definitions only: no side effects at source time, no `set`
# changes, so every caller keeps its own -e/-u mode.

# Owner notification for every fail-open degrade (best-effort, never blocks a deploy/publish): a
# gate that degrades where nobody looks is no gate — R80 proved stderr alone is invisible in practice.
_owner_note(){ shimpz-tg notify "$1" >/dev/null 2>&1 || echo "  (owner notify failed for: $1)" >&2; }

# Python anywhere outside dep/build trees (a real `app/api/v1/main.py` is deep, so NO depth limit —
# a depth cap would silently classify a backend as static and ship/deploy it unaudited).
_haspy(){ [ -n "$(find "$1" \( -name .venv -o -name venv -o -name node_modules -o -name __pycache__ \
                 -o -name .git -o -name build \) -prune -o -name '*.py' -print -quit 2>/dev/null)" ]; }

# R90: a SvelteKit config exempts ONLY a genuinely static front — one WITH server-executable files
# (+server.* endpoints, +page.server.*/+layout.server.* loads/actions, hooks.server.*) is a real
# node server (adapter-node style) and must reach the judge like any other backend. Generated trees
# (.svelte-kit/, build/) are pruned so only the app's OWN source counts. Pure find, no network.
_svelte_server_files(){
  local prune="( -name .venv -o -name venv -o -name node_modules -o -name __pycache__ -o -name .git -o -name build -o -name .svelte-kit ) -prune -o"
  # shellcheck disable=SC2086  # $prune is a fixed find-expression, split on purpose
  [ -n "$(find "$1" $prune \( -name '+server.*' -o -name '+page.server.*' -o -name '+layout.server.*' -o -name 'hooks.server.*' \) -print -quit 2>/dev/null)" ]
}

# A dir is a BACKEND (→ security judgment) if it has .py — OR a non-Python SERVER (R89): go.mod /
# Cargo.toml / deno.json(c), or a package.json WITHOUT a SvelteKit config (the standard front is
# SvelteKit and is served as the web tier; a bare node server is a backend and must not skip the
# judge by simply not being Python — before R89 it deployed/published with NO security audit at all).
# ONE rule for shimpz-app AND shimpz-publish by construction (this file).
_isbackend(){
  _haspy "$1" && return 0
  local prune="( -name .venv -o -name venv -o -name node_modules -o -name __pycache__ -o -name .git -o -name build ) -prune -o"
  # shellcheck disable=SC2086  # $prune is a fixed find-expression, split on purpose
  [ -n "$(find "$1" $prune \( -name go.mod -o -name Cargo.toml -o -name deno.json -o -name deno.jsonc \) -print -quit 2>/dev/null)" ] && return 0
  # shellcheck disable=SC2086
  if [ -n "$(find "$1" $prune -name package.json -print -quit 2>/dev/null)" ]; then
    if [ -z "$(find "$1" $prune -name 'svelte.config.*' -print -quit 2>/dev/null)" ]; then
      return 0            # a node project that is not SvelteKit = a bare node server
    fi
    _svelte_server_files "$1" && return 0   # SvelteKit WITH server routes = a real server (R90)
  fi
  return 1
}

# backend dir → project root (shimpz-new scaffolds the API under <project>/backend/)
_projroot(){ case "$1" in */backend) dirname "$1" ;; *) printf '%s' "$1" ;; esac; }

# The canonical proj_<name> sanitizer used by shimpz-app's DSN gate and the server-side drivers. They
# MUST agree, or the gate would accept a credential outside the app's exact resource namespace.
_sanitize_proj(){ printf '%s' "$1" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9_' '_' | sed 's/^_*//;s/_*$//'; }

# NOTE: the old _tunnel_cfg/_zone_for (raw `cf GET` calls + client-side JSON manipulation) were
# removed here — SECURITY_ENGINEERING_PLAN.md item 3 moved that logic server-side into
# cf-driver (see cfdriver.sh's _cfdriver_zone_for/_cfdriver_ingress_upsert/_cfdriver_ingress_delete).
# shimpz-publish/shimpz-unpublish now call cfdriver.sh instead of manipulating Cloudflare's raw API here.
