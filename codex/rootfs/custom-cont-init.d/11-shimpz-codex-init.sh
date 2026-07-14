#!/usr/bin/with-contenv bash
# shellcheck shell=bash  # s6-overlay's `with-contenv` wrapper is not a shell ShellCheck recognizes.
# Seed the persistent Codex home after the shared Shimpz init has prepared /config/workspace.
set -euo pipefail

uid="${PUID:-1000}"
gid="${PGID:-1000}"
codex_home="${CODEX_HOME:-/config/.codex}"
workspace=/config/workspace

prepare_owned_dir() {
    local path=$1
    local mode=$2

    if [[ -L "$path" || ( -e "$path" && ! -d "$path" ) ]]; then
        echo "[shimpz-codex-init] refusing non-directory state at $path" >&2
        return 1
    fi
    mkdir -p -- "$path"
    # A Capsule deliberately has CAP_CHOWN but not CAP_FOWNER. GNU install's combined -o/-g/-m
    # operation changes a fresh directory to uid 1000 before applying its mode, so the following
    # chmod is denied. Temporarily own only the directory as root, apply the mode, then make the
    # tenant uid the final owner. This also makes restarts repair drift without adding a capability.
    chown 0:0 -- "$path"
    chmod "$mode" -- "$path"
    chown "$uid:$gid" -- "$path"
}

prepare_owned_dir "$codex_home" 0700
prepare_owned_dir "$workspace" 0755

# File-backed auth is intentional in a headless Capsule: D8 writes the account-owned credential into
# this private volume. Updates are centrally managed by rebuilding the pinned provider image.
if [ ! -e "$codex_home/config.toml" ]; then
    cat > "$codex_home/config.toml" <<'EOF'
check_for_update_on_startup = false
cli_auth_credentials_store = "file"
EOF
fi
chmod 0600 "$codex_home/config.toml"
chown "$uid:$gid" "$codex_home/config.toml"

# Codex consumes AGENTS.md. The shared init has already selected the platform or tenant-safe
# CLAUDE.md; copy that selected contract instead of ever reaching around it to the operator persona.
if [ -f "$workspace/CLAUDE.md" ]; then
    cp "$workspace/CLAUDE.md" "$workspace/AGENTS.md"
    chmod 0644 "$workspace/AGENTS.md"
    chown "$uid:$gid" "$workspace/AGENTS.md"
fi

echo "[shimpz-codex-init] ready"
