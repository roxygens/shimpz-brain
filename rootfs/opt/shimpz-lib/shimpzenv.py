"""shimpzenv — the single source of truth for loading Shimpz's `$SHIMPZ_HOME/.env` into the process env.

The real process environment always wins — the .env only fills what compose/systemd didn't already set.
"""

import os
from pathlib import Path


def load(home=None):
    """Merge `$SHIMPZ_HOME/.env` into os.environ, without overriding values already in the environment.

    A missing .env is fine (returns 0); a present-but-unreadable one raises (fail-fast).
    Returns the number of keys filled from the file.
    """
    envf = Path(home or os.environ.get("SHIMPZ_HOME", "/config/.shimpz")) / ".env"
    if not envf.exists():
        return 0
    n = 0
    for line in envf.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k not in os.environ:
            os.environ[k] = v.strip()
            n += 1
    return n


def brain_env():
    """The environment for spawning Shimpz's brain and its helpers (`claude -p`, shimpz-login).

    HOME is the agent user's /config; Node 24 is first on PATH and is the image's only Node runtime.
    No DISPLAY: the brain container has no X server — UI tools are HTTP clients of browser-agent.
    """
    env = dict(os.environ)
    env["HOME"] = "/config"
    env["PATH"] = "/opt/node24/bin:" + env.get("PATH", "")
    return env
