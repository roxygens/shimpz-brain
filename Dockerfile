# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# check=skip=SecretsUsedInArgOrEnv ; false positive: the *_TOKEN_GID/*_TOKEN_* ARGs here hold numeric group IDs and file paths, never secret values
#
# Shimpz — autonomous agent, brain = Claude Code, driven over Telegram (voice + text).
#
# SECURITY_ENGINEERING_PLAN.md item 0: this is the BRAIN ONLY now — the desktop, Chrome's live
# browsing session (CDP :9222), XTEST, and every credential that session needed all moved to a
# separate `shimpz-browser` container. This container drives the browser exclusively through
# browser-agent's narrow, audited HTTP API (see rootfs/opt/shimpz-lib/shimpzbrowser.py) — it never
# touches DISPLAY/CDP directly and holds no browser-session credential (IPRoyal proxy, etc).
#
# Base is still the LinuxServer KasmVNC image for its supervised Ubuntu/userspace foundation. The
# desktop longruns are explicitly removed from the final Brain's active s6 user bundle below: Docker
# EXPOSE metadata is not an access-control boundary, and Capsule Apps share a private core bridge with
# their Brain. The separately built shimpz-browser remains the only image that runs the desktop stack.
FROM lscr.io/linuxserver/baseimage-kasmvnc:ubuntunoble@sha256:c6d902c207d6e4a66ca41ce36dda549cd58a5bc8d353468b24b0cf780eb528c8 AS shimpz-brain-base
ARG SOURCE_DATE_EPOCH=0

LABEL org.opencontainers.image.title="shimpz-brain" \
      org.opencontainers.image.description="Shimpz agent — Claude Code brain, driven over Telegram; drives the browser via browser-agent in the separate shimpz-browser container"

ENV DEBIAN_FRONTEND=noninteractive \
    SHIMPZ_HOME=/config/.shimpz

# --- VERSION MANIFEST (single source of truth; bump deliberately) ------------------------
# Every third-party binary/runtime is PINNED so a rebuild is reproducible and a bad upstream
# release can never silently change Shimpz's behavior (the #1 thing this project's fail-fast ethos
# forbids). These are the known-good versions captured from the running container. To upgrade
# one, bump it here (or pass --build-arg NAME=ver) — never a floating "latest"/"current".
ARG CLAUDE_VERSION=2.1.196
ARG PYTHON_VERSION=3.14.6
ARG UV_VERSION=0.11.25
ARG RUFF_VERSION=0.15.20
ARG RCLONE_VERSION=1.74.3
ARG CADDY_VERSION=2.11.4
ARG NODE_VERSION=24.18.0
ARG PNPM_VERSION=11.9.0
ARG GOOGLE_CHROME_VERSION=150.0.7871.114-1
ARG EMBEDDING_MODEL_REVISION=73908c3438cf03b6a01bcb9611d62b23d0726f08
ARG UBUNTU_SNAPSHOT=20260623T000000Z

# --- SUPPLY-CHAIN INTEGRITY (SECURITY_ENGINEERING_PLAN.md item 8) ------------------------
# A pinned VERSION alone is reproducibility, not integrity: an artifact URL can serve different
# bytes for the SAME version tag without anything here noticing. Every download below is verified against
# a SHA256 captured HERE, cross-checked against the vendor's own published checksum file where one
# exists (confirmed exact match for node/rclone; the rest have no separate checksum artifact to
# diff against, so the hash captured here IS the baseline — a rebuild that gets different bytes
# for the SAME version now FAILS LOUD instead of silently running altered code). Bump the hash
# alongside the version, deliberately, when you bump either.
ARG CLAUDE_BINARY_SHA256=eb933c6dd5534db89b83ba09009d5c0932bd1395f7e3bb0f34ba37eec37bbade
ARG UV_INSTALL_SHA256=ca2de1bca2913ba30ce88658b6d90a663c627ecac378803aa58084a9adb35a46
ARG RCLONE_SHA256=dbee7ccd7a5d617e4ed4cd4555c16669b511abfe8d31164f61be35ac9e999bd2
ARG RUFF_SHA256=df8e74862d4cd4fdac11faf3048789896ff9898a0cacb98497df20d0a1cc7bb4
ARG NODE_SHA256=55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742
ARG CADDY_SHA256=527fbf917c39189a1e3b31d34fa955601680b2d5c8055d2a87b8b9588dec7bb9

# The KasmVNC base is Ubuntu Noble, not Debian. Replace every inherited APT source (including the
# unused Docker repository) with Canonical's timestamped Ubuntu archive before any apt-get invocation.
RUN set -eux; \
    find /etc/apt/sources.list.d -maxdepth 1 -type f \( -name '*.list' -o -name '*.sources' \) -delete; \
    printf '%s\n' \
        "deb https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble main restricted universe multiverse" \
        "deb https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-updates main restricted universe multiverse" \
        "deb https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-security main restricted universe multiverse" \
        > /etc/apt/sources.list; \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99shimpz-snapshot; \
    test "$(grep -Fc "https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT}" /etc/apt/sources.list)" -eq 3; \
    ! grep -RqsE 'https?://(archive|security)\.ubuntu\.com|https?://download\.docker\.com' \
        /etc/apt/sources.list /etc/apt/sources.list.d

# This host's IPv6 egress is broken; dual-stack endpoints (Cloudflare R2, npm registry,
# downloads.rclone.org, …) were resolving to dead IPv6 → TLS handshake failures.
# Prefer IPv4 globally in getaddrinfo.
RUN sed -i 's/^#\(precedence ::ffff:0:0\/96  100\)/\1/' /etc/gai.conf || \
    echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf && \
    # sed exits 0 even when the pattern doesn't match (e.g. a base-image reformat of that line), which
    # would leave the IPv4-preference silently unset and resurrect the dead-IPv6 TLS failures. Assert it.
    grep -q '^precedence ::ffff:0:0/96' /etc/gai.conf

# shimpz-driver's bearer token (SECURITY_ENGINEERING_PLAN.md item 1) lives on a volume shared
# with `shimpz-brain`, group-readable by a dedicated GID so `abc` (this container's agent user, UID 1000 —
# NOT driver's own UID 10001 in the other image) can read it without being its owner. Real live bug
# this fixes: the token used to be 0400 owned by driver alone, unreadable by `abc` at all. The GID
# MUST match drivers/apps/Dockerfile's own groupadd exactly — both sides agree on a fixed number,
# no runtime lookup. `usermod -aG` ADDS a supplementary group; it survives LSIO's own PUID/PGID
# remap of abc's primary uid/gid at container boot (that remap doesn't touch /etc/group members).
ARG SHIMPZ_DRIVER_TOKEN_GID=10002
RUN groupadd -g "${SHIMPZ_DRIVER_TOKEN_GID}" shimpzdriver-token && usermod -aG shimpzdriver-token abc

# Same pattern, second sidecar: cf-driver's own bearer token (SECURITY_ENGINEERING_PLAN.md
# item 3) — a DIFFERENT GID from shimpzdriver-token above, so the two sidecars' tokens are never
# readable via each other's group. MUST match drivers/cf/Dockerfile's own groupadd exactly.
ARG SHIMPZ_CFDRIVER_TOKEN_GID=10003
RUN groupadd -g "${SHIMPZ_CFDRIVER_TOKEN_GID}" shimpzcfdriver-token && usermod -aG shimpzcfdriver-token abc

# The platform brain deliberately has no pg-driver group/token. Database provisioning is a
# capsule-driver control-plane operation and tenant access uses the database's exact scoped role.
# Bus remains a separate named-operation sidecar for the platform development brain.
ARG SHIMPZ_BUSDRIVER_TOKEN_GID=10005
RUN groupadd -g "${SHIMPZ_BUSDRIVER_TOKEN_GID}" shimpzbusdriver-token && usermod -aG shimpzbusdriver-token abc

# Fifth sidecar's token: browser-agent (SECURITY_ENGINEERING_PLAN.md item 0 — Chrome/KasmVNC/XTEST/
# CDP moved to their own `shimpz-browser` container; `shimpz-brain` calls its restricted API instead of
# touching DISPLAY/CDP directly). MUST match shimpz-browser/Dockerfile's own groupadd exactly.
ARG SHIMPZ_BROWSERAGENT_TOKEN_GID=10006
RUN groupadd -g "${SHIMPZ_BROWSERAGENT_TOKEN_GID}" shimpzbrowseragent-token && usermod -aG shimpzbrowseragent-token abc

# Sixth sidecar's token: r2-driver (SECURITY_ENGINEERING_PLAN.md item 7 — the R2 credentials
# moved to their own container; `shimpz-brain` calls its restricted upload/list/get API instead of holding the
# R2 secret). A DISTINCT GID again, so no sidecar's token is readable via another's group. MUST match
# drivers/r2/Dockerfile's own groupadd exactly.
ARG SHIMPZ_R2DRIVER_TOKEN_GID=10007
RUN groupadd -g "${SHIMPZ_R2DRIVER_TOKEN_GID}" shimpzr2driver-token && usermod -aG shimpzr2driver-token abc

# Seventh sidecar's token: openai-driver (SECURITY_ENGINEERING_PLAN.md item 7 — the OpenAI key
# moved to its own container; imagegen + the gateway's voice call its restricted API instead of
# holding OPENAI_API_KEY). A DISTINCT GID again. MUST match drivers/openai/Dockerfile's groupadd.
ARG SHIMPZ_OPENAIDRIVER_TOKEN_GID=10008
RUN groupadd -g "${SHIMPZ_OPENAIDRIVER_TOKEN_GID}" shimpzopenaidriver-token && usermod -aG shimpzopenaidriver-token abc

# Eighth sidecar's token: pay-driver (Shimpz L3 — the ONLY holder of the ShimpzPay merchant
# credential; apps charge via its bearer-gated API, never a processor directly). A DISTINCT GID again.
# MUST match drivers/pay/Dockerfile's groupadd.
ARG SHIMPZ_PAYDRIVER_TOKEN_GID=10009
RUN groupadd -g "${SHIMPZ_PAYDRIVER_TOKEN_GID}" shimpzpaydriver-token && usermod -aG shimpzpaydriver-token abc

# --- SECURITY: strip the base image's root-escalation path for the agent user ---
# The LSIO base adds `abc` to the `sudo` group AND ships `/etc/sudoers`'s `%sudo ALL=(ALL:ALL)
# NOPASSWD: ALL` — so the ONLY barrier between the agent user and passwordless root was the single
# `no-new-privileges` flag. Remove abc from `sudo` (and the vestigial `docker` group — no socket is
# mounted here; deploys go through shimpz-driver's audited API) AND comment the NOPASSWD grant, so
# root stays unreachable to `abc` even if NNP is ever dropped. The agent never needs either (sudo is
# blocked by NNP regardless, and it has no docker socket). Re-asserted at boot in 10-shimpz-init.sh in
# case a base-image init stage re-adds the membership. See the security audit / SECURITY_ENGINEERING_PLAN.
RUN gpasswd -d abc sudo 2>/dev/null || true; \
    gpasswd -d abc docker 2>/dev/null || true; \
    sed -i 's/^%sudo/# shimpz-disabled: %sudo/' /etc/sudoers

# --- System deps: document toolbelt + a NARROW, headless-only, --no-sandbox Chrome for
# dev-preview screenshots (shimpz-screenshot) and PDF generation (html2pdf-chrome) ---
# SECURITY_ENGINEERING_PLAN.md item 0: the DESKTOP/live-browsing Chrome (headful, CDP on :9222,
# real cookies/sessions, the actual risk this split addresses) moved entirely to `shimpz-browser` —
# along with xterm/xdotool/at-spi2-core/Mesa GL/gost/imagemagick, none of which this container
# needs anymore. google-chrome-stable itself STAYS here for a completely different, narrower
# purpose: shimpz-screenshot/html2pdf-chrome each spawn their OWN throwaway `--headless=new
# --no-sandbox` instance with no persistent profile, rendering only the agent's OWN local dev
# builds/generated HTML — never the open web, never a logged-in session, never elevated caps.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git build-essential python3-dev python3-venv libffi-dev \
        # FULL font coverage (CJK + emoji) so a dev-preview screenshot/PDF doesn't render
        # a slice of the agent's own generated content as tofu boxes.
        fonts-noto fonts-noto-color-emoji fonts-liberation fonts-noto-cjk \
        # media / extraction / document toolbelt the agent reaches for
        ripgrep ffmpeg jq lsof \
        unzip zip p7zip-full poppler-utils tesseract-ocr pandoc \
        # PROFESSIONAL PDFs: WeasyPrint (HTML/CSS->PDF, no JS = deterministic, never
        # blank) + clean pro fonts (Inter sans, IBM Plex mono). Drives md2pdf/html2pdf.
        weasyprint fonts-inter fonts-ibm-plex && \
    # rclone = the agent's S3 client for Cloudflare R2 (upload / link / backup). Official
    # latest binary instead of apt's (Ubuntu Noble freezes rclone years behind upstream).
    curl -fsSL "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-amd64.zip" -o /tmp/rclone.zip && \
    echo "${RCLONE_SHA256}  /tmp/rclone.zip" | sha256sum -c - && \
    unzip -j /tmp/rclone.zip '*/rclone' -d /usr/local/bin/ && \
    chmod +x /usr/local/bin/rclone && rm -f /tmp/rclone.zip && \
    # --- Google Chrome Stable (official repo) ---
    install -d -m 0755 /etc/apt/keyrings && \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y --no-install-recommends \
        "google-chrome-stable=${GOOGLE_CHROME_VERSION}" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /var/lib/apt/periodic/* /var/cache/apt/* /var/cache/fontconfig/* \
        /var/cache/ldconfig/aux-cache /var/cache/man/* /var/log/apt/* \
        /var/log/alternatives.log /var/log/dpkg.log

# --- Shimpz's brain: Claude Code CLI (standalone binary — no npm) baked into the image ---
# A standalone binary; runtime config/credentials live in $HOME/.claude (= /config/.claude
# on the persistent volume). Auth is via your Claude subscription (interactive `claude`
# login, once). The gateway (rootfs) drives it headless per message.
# Pin the brain to a known-good build. The runtime
# auto-updater is OFF (DISABLE_AUTOUPDATER below) so the pin actually holds — an always-on
# brain silently updating itself is exactly the drift the fail-fast ethos forbids. Bump the
# brain deliberately: change CLAUDE_VERSION and rebuild.
# The versioned final binary itself is hash-checked locally. No mutable installer or release metadata
# participates in the build, and no downloaded bytes execute before their recorded digest passes.
RUN install -d -m 0755 /opt/cc/.local/bin /opt/cc/.local/share/claude/versions && \
    curl --proto '=https' --tlsv1.2 -fsSL \
        "https://downloads.claude.ai/claude-code-releases/${CLAUDE_VERSION}/linux-x64/claude" \
        -o "/opt/cc/.local/share/claude/versions/${CLAUDE_VERSION}" && \
    echo "${CLAUDE_BINARY_SHA256}  /opt/cc/.local/share/claude/versions/${CLAUDE_VERSION}" | sha256sum -c - && \
    chmod 0755 "/opt/cc/.local/share/claude/versions/${CLAUDE_VERSION}" && \
    ln -s "/opt/cc/.local/share/claude/versions/${CLAUDE_VERSION}" /opt/cc/.local/bin/claude && \
    ln -s /opt/cc/.local/bin/claude /usr/local/bin/claude && \
    test "$(HOME=/tmp claude --version)" = "${CLAUDE_VERSION} (Claude Code)"
ENV DISABLE_AUTOUPDATER=1

# --- uv: the Python package/runtime manager (infra venv below + the CODE SHIMPZ WRITES) ---
# Fast, reproducible per-project envs (`uv init` / `uv add` / `uv run`). Standalone binary in
# /usr/local/bin (on PATH for the abc user). Shimpz uses it for its OWN code under workspace/projects;
# the build uses it to provision the infra interpreter + venv (next block), which Shimpz must not touch.
# Downloaded to a file and hash-checked BEFORE execution — never `curl | sh`.
RUN curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o /tmp/uv-install.sh && \
    echo "${UV_INSTALL_SHA256}  /tmp/uv-install.sh" | sha256sum -c - && \
    env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh /tmp/uv-install.sh && \
    rm -f /tmp/uv-install.sh && \
    rm -rf /root/.cache/uv && \
    uv --version

# --- Tools venv: Python deps the helper CLIs + the Telegram gateway need ---
# Standalone from any agent framework. webread/chrome-upload run on this interpreter;
# the gateway uses python-telegram-bot + the Claude Code SDK shell-out (voice STT/TTS goes
# through openai-driver via shimpzopenai.py — plain http.client, no openai SDK here).
# The interpreter is a uv-managed CPython ${PYTHON_VERSION} in /opt/uv-python — NOT the distro
# python3 (noble ships 3.12) — because ALL infra Python in this repo is written to 3.14 idioms.
# uv.lock binds the complete transitive graph and every registry artifact hash; --frozen makes a
# dependency edit without a deliberately regenerated lock fail the image build.
COPY pyproject.toml uv.lock /opt/shimpz-runtime/
RUN UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python install "${PYTHON_VERSION}" && \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python uv venv --python "${PYTHON_VERSION}" /opt/venv && \
    UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --project /opt/shimpz-runtime \
        --frozen --no-dev --no-install-project --python /opt/venv/bin/python && \
    /opt/venv/bin/python --version && \
    /opt/venv/bin/python -c 'import confluent_kafka, model2vec, numpy, requests, telegram, trafilatura' && \
    rm -rf /root/.cache/uv

# --- Local semantic-recall embeddings (R121): a small MULTILINGUAL static-embedding model
# (model2vec — no torch/onnx, py3.14-safe), baked so recall works offline and deterministically.
# The markdown files stay the memory; this model only RANKS them (shimpz-lib/shimpzemb.py, used by the
# shimpz-recall hook). ~500MB on disk, loads ~3s, embeds the whole store in ~50ms on CPU. Its own
# layer so package-list churn above doesn't re-download the model. snapshot_download resolves the
# full immutable Hugging Face commit recorded above; Model2Vec then loads only that local snapshot. ---
RUN HF_HOME=/tmp/hf /opt/venv/bin/python -c "from huggingface_hub import snapshot_download; from model2vec import StaticModel; \
snapshot = snapshot_download(repo_id='minishlab/potion-multilingual-128M', revision='${EMBEDDING_MODEL_REVISION}'); \
StaticModel.from_pretrained(snapshot).save_pretrained('/opt/shimpz-emb/potion-multilingual-128M')" && \
    rm -rf /tmp/hf && \
    chmod -R a+rX /opt/shimpz-emb && \
    find /opt/shimpz-emb -depth -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + && \
    /opt/venv/bin/python -c "from model2vec import StaticModel; \
v = StaticModel.from_pretrained('/opt/shimpz-emb/potion-multilingual-128M').encode(['sanity']); \
assert v.shape == (1, 256), v.shape" && \
    setpriv --reuid=nobody --regid=nogroup --clear-groups \
        /opt/venv/bin/python -c "from safetensors import safe_open; \
safe_open('/opt/shimpz-emb/potion-multilingual-128M/model.safetensors', framework='numpy')"
# (the setpriv line proves a NON-ROOT user can read the baked weights — save_pretrained preserves the
# HF cache blob's 0600 mode, which a root-only sanity check can't see; the recall hook runs as abc)

# --- ruff: the DETERMINISTIC code-standards gate (shimpz-stdgate runs `ruff check` every turn). Standalone
# static binary in /usr/local/bin (on PATH for abc), same pattern as rclone/gost/caddy. Pinned. ---
RUN curl -LsSf "https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-x86_64-unknown-linux-gnu.tar.gz" -o /tmp/ruff.tar.gz && \
    echo "${RUFF_SHA256}  /tmp/ruff.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/ruff.tar.gz -C /tmp && \
    install -m 0755 /tmp/ruff-*/ruff /usr/local/bin/ruff && \
    rm -rf /tmp/ruff.tar.gz /tmp/ruff-x86_64-unknown-linux-gnu && \
    ruff --version

# --- Node 24 + pnpm: frontend toolchain for Shimpz's SvelteKit/Vite projects ---
# CRITICAL: do NOT replace the base image's system Node. KasmVNC's `kclient` (the desktop web
# frontend) loads native addons (pulseaudio2 → libnode.so.109) built against the base Node 20;
# installing Node 24 over it via apt deletes libnode.so.109 → kclient crash-loops → desktop 502.
# So Node 24 lives in /opt/node24 (its own prefix); the system Node stays for kclient. Shimpz gets
# /opt/node24/bin on PATH via the gateway/shimpz-run env + a login profile.d.
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz && \
    echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - && \
    mkdir -p /opt/node24 && tar -xJf /tmp/node.tar.xz -C /opt/node24 --strip-components=1 && \
    rm -f /tmp/node.tar.xz && \
    export PATH=/opt/node24/bin:$PATH && \
    npm install -g "pnpm@${PNPM_VERSION}" && \
    printf 'export PATH=/opt/node24/bin:$PATH\n' > /etc/profile.d/node24.sh && \
    node --version && pnpm --version && \
    rm -rf /root/.npm

# --- Deploy/runtime tooling (binaries baked now; WIRED to run in Phase 2): ---
# cron = scheduled jobs/checks; supervisor = where Shimpz registers app services (one port each);
# caddy = internal reverse proxy (hostname → port) that sits behind the single Cloudflare Tunnel.
RUN apt-get update && apt-get install -y --no-install-recommends cron supervisor && \
    curl -4 -fsSL "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_amd64.tar.gz" -o /tmp/caddy.tar.gz && \
    echo "${CADDY_SHA256}  /tmp/caddy.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/caddy.tar.gz -C /tmp caddy && install -m 0755 /tmp/caddy /usr/local/bin/caddy && rm -f /tmp/caddy /tmp/caddy.tar.gz && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /var/lib/apt/periodic/* /var/cache/apt/* /var/cache/fontconfig/* \
        /var/cache/ldconfig/aux-cache /var/cache/man/* /var/log/apt/* \
        /var/log/alternatives.log /var/log/dpkg.log && \
    caddy version

# --- Overlay our s6 services, headless autostart and one-shot init ---
COPY rootfs/ /

# SECURITY_ENGINEERING_PLAN.md item 0 / ADR-0002: this is a headless Brain, not a desktop container.
# The pinned base enables these inherited longruns through the active `user` bundle; in particular,
# svc-kasmvnc listens without authentication on all interfaces. Remove the complete desktop chain,
# including svc-docker (which depends on svc-de and would pull every listener back into the bundle),
# and fail the build if an upstream rename would make this hardening silently incomplete. Browser owns
# its own copy of the base and deliberately keeps these services.
RUN set -eux; \
    for service in svc-kasmvnc svc-kclient svc-nginx svc-de svc-pulseaudio svc-docker; do \
        entry="/etc/s6-overlay/s6-rc.d/user/contents.d/${service}"; \
        test -e "$entry" || test -L "$entry"; \
        rm -f "$entry"; \
        test ! -e "$entry" && test ! -L "$entry"; \
    done

# Make scripts executable (COPY does not always preserve the bit across hosts). Glob the whole bin
# dir instead of enumerating every file: everything in /usr/local/bin is a command (must be +x), so a
# newly added CLI can never be silently forgotten here (the old per-file list dropped the +x bit on
# anything not added to it → a non-executable command that only fails at runtime). The symlinks below
# don't need +x of their own (chmod follows to the already-+x target).
RUN chmod +x /usr/local/bin/* \
             /custom-cont-init.d/*.sh \
             /etc/s6-overlay/s6-rc.d/svc-shimpz-headless/run \
             /defaults/autostart 2>/dev/null || true; \
    # uikey/uitype are the same HTTP-client wrapper as uiclick (branches on argv[0]).
    ln -sf uiclick /usr/local/bin/uikey && \
    ln -sf uiclick /usr/local/bin/uitype && \
    # shimpz-captcha is shimpz-approve in "captcha" mode (branches on argv[0]).
    ln -sf shimpz-approve /usr/local/bin/shimpz-captcha

# SECURITY_ENGINEERING_PLAN.md item 0: no EXPOSE and no inherited desktop longruns. KasmVNC, kclient,
# nginx, the desktop environment and PulseAudio are absent from this image's active s6 user bundle;
# Chrome/CDP and the human desktop live only in `shimpz-browser`.

# Codex is a target in this SAME immutable build graph, not `FROM shimpz-brain:shimpz-local`.
# Building this target therefore cannot silently consume a mutable local tag with unrelated bytes.
FROM shimpz-brain-base AS shimpz-brain-codex
ARG SOURCE_DATE_EPOCH=0

LABEL org.opencontainers.image.title="shimpz-brain-codex" \
      org.opencontainers.image.description="Shimpz Capsule brain powered by the pinned OpenAI Codex CLI"

ARG CODEX_VERSION=0.144.3
ARG CODEX_PACKAGE_SHA256=1c3c1f1f636da56a197ce0b5084d44b86f58f0fb32983278fa55c2544d221af4
ARG CODEX_TARGET=x86_64-unknown-linux-musl

USER root
RUN release="/opt/codex/packages/standalone/releases/${CODEX_VERSION}-${CODEX_TARGET}" && \
    install -d -m 0755 "$release" /opt/codex/packages/standalone && \
    curl --proto '=https' --tlsv1.2 -fsSL \
        "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-package-${CODEX_TARGET}.tar.gz" \
        -o /tmp/codex-package.tar.gz && \
    echo "${CODEX_PACKAGE_SHA256}  /tmp/codex-package.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/codex-package.tar.gz -C "$release" && \
    rm -f /tmp/codex-package.tar.gz && \
    ln -s "$release" /opt/codex/packages/standalone/current && \
    ln -s /opt/codex/packages/standalone/current/bin/codex /usr/local/bin/codex && \
    ln -s /opt/codex/packages/standalone/current/bin/codex-code-mode-host \
        /usr/local/bin/codex-code-mode-host && \
    test "$(codex --version)" = "codex-cli ${CODEX_VERSION}" && \
    codex-code-mode-host --help >/dev/null

COPY codex/rootfs/ /
RUN chmod 0755 \
        /custom-cont-init.d/11-shimpz-codex-init.sh \
        /usr/local/bin/shimpz-codex-auth \
        /usr/local/bin/shimpz-codex-run

ENV PUID=1000 \
    PGID=1000 \
    HOME=/config \
    CODEX_HOME=/config/.codex \
    SHIMPZ_BRAIN_PROVIDER=codex
WORKDIR /config/workspace

# Keep the no-target build contract as the flagship Claude Brain. Release/CI selects the Codex
# target explicitly, while both outputs inherit the exact same content-addressed base stage above.
FROM shimpz-brain-base AS shimpz-brain
ARG SOURCE_DATE_EPOCH=0
