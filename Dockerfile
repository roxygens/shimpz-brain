# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# check=skip=SecretsUsedInArgOrEnv ; SHIMPZ_BRAIN_RUNTIME_TOKEN_GID is a numeric group id, never a credential

FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS builder
ARG SOURCE_DATE_EPOCH=0

# uv is fetched from its immutable versioned endpoint and verified before execution.
ARG UV_VERSION=0.11.25
ARG UV_INSTALL_SHA256=ca2de1bca2913ba30ce88658b6d90a663c627ecac378803aa58084a9adb35a46
ARG DEBIAN_SNAPSHOT=20260623T000000Z

RUN set -eux; \
    . /etc/os-release; \
    archive_keyring="$(find /usr/share/keyrings -maxdepth 1 -type f -name 'debian-archive-keyring.*' -print -quit)"; \
    test -n "$archive_keyring"; \
    rm -f /etc/apt/sources.list; \
    find /etc/apt/sources.list.d -maxdepth 1 -type f -delete; \
    printf '%s\n' \
        "deb [signed-by=${archive_keyring}] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} ${VERSION_CODENAME} main" \
        "deb [signed-by=${archive_keyring}] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} ${VERSION_CODENAME}-updates main" \
        "deb [signed-by=${archive_keyring}] https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT} ${VERSION_CODENAME}-security main" \
        > /etc/apt/sources.list.d/debian-snapshot.list; \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99shimpz-snapshot; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    curl --proto '=https' --tlsv1.2 -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o /tmp/uv-install.sh; \
    echo "${UV_INSTALL_SHA256}  /tmp/uv-install.sh" | sha256sum -c -; \
    env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh /tmp/uv-install.sh; \
    rm -f /tmp/uv-install.sh; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /var/lib/apt/periodic/* /var/cache/apt/* /var/cache/fontconfig/* \
        /var/cache/ldconfig/aux-cache /var/cache/man/* /var/log/apt/* /var/log/alternatives.log \
        /var/log/dpkg.log; \
    uv --version

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN export UV_PROJECT_ENVIRONMENT=/opt/venv UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy; \
    uv sync --frozen --no-install-project --no-dev --python 3.14 \
    && find /opt/venv -type f -name '*.pyc' -delete \
    && /opt/venv/bin/python -m compileall -q -f --invalidation-mode checked-hash /opt/venv \
    && rm -rf "${UV_CACHE_DIR}" /root/.cache/uv

FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1
ARG SOURCE_DATE_EPOCH=0
ARG SHIMPZ_BRAIN_RUNTIME_TOKEN_GID=10016

LABEL org.opencontainers.image.title="shimpz-brain" \
      org.opencontainers.image.description="Provider-neutral Shimpz Brain runtime powered by LangGraph"

RUN groupadd -g 10001 brainruntime \
    && groupadd -g "${SHIMPZ_BRAIN_RUNTIME_TOKEN_GID}" shimpzbrain-runtime-token \
    && useradd -u 10001 -g brainruntime -G shimpzbrain-runtime-token -M -s /usr/sbin/nologin brainruntime \
    && mkdir -p /app /run/shimpz-brain-runtime /var/lib/shimpz-brain-runtime \
    && chown brainruntime:shimpzbrain-runtime-token /run/shimpz-brain-runtime \
    && chmod 0750 /run/shimpz-brain-runtime \
    && chown brainruntime:brainruntime /var/lib/shimpz-brain-runtime \
    && chmod 0700 /var/lib/shimpz-brain-runtime

COPY --from=builder /opt/venv /opt/venv
COPY --chown=brainruntime:brainruntime agent_runtime.py runtime_api.py /app/

ENV LANGCHAIN_TRACING_V2=false \
    LANGSMITH_TRACING=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHIMPZ_BRAIN_RUNTIME_STATE=/var/lib/shimpz-brain-runtime/checkpoints.sqlite3 \
    SHIMPZ_BRAIN_RUNTIME_TOKEN_FILE=/run/shimpz-brain-runtime/token

WORKDIR /app
USER brainruntime
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=4s --start-period=5s --retries=5 \
    CMD ["/opt/venv/bin/python", "-c", "import urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3); assert response.status == 200"]
ENTRYPOINT ["/opt/venv/bin/uvicorn", "runtime_api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--no-server-header", "--no-proxy-headers"]
