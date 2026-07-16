# Codex Brain image

Build the Codex target through its build-only Compose profile:

```sh
docker compose --profile capsule-images build shimpz-brain-codex
```

The equivalent direct build is:

```sh
docker build --target shimpz-brain-codex -f brain/Dockerfile -t shimpz-brain-codex:shimpz-local brain
```

The Codex target lives in the same Dockerfile/build graph as the flagship Brain, so it cannot inherit
from a mutable local image tag. It downloads the official versioned standalone Codex package for release
`0.144.3` and verifies the final archive's pinned SHA-256 without consulting release metadata. It inherits
`/init` supervision and the rest of the `shimpz-brain` runtime contract. Runtime
identity is `abc` (uid 1000), `HOME=/config`, `CODEX_HOME=/config/.codex`, and working directory
`/config/workspace`.

The capsule-driver contract is:

- Status: `docker exec --user 1000:1000 CAPSULE shimpz-codex-auth status`
- API-key configure: pipe the unsealed secret to
  `docker exec --interactive --user 1000:1000 CAPSULE shimpz-codex-auth api-key`
- OAuth configure: pipe the unsealed `auth.json` object to the same command with `oauth-cache`.
- Interactive ChatGPT login: run `shimpz-codex-auth device-login` detached, then read the strict
  `device-info` response. Open its official OpenAI URL and enter `user_code` there; Shimpz never asks
  the user to paste that code or an `auth.json`. Poll `status`/`device-result`, or use `device-cancel`.
- New chat: pipe the prompt to
  `docker exec --interactive --user 1000:1000 CAPSULE shimpz-codex-run new text`
- Continue chat: use `shimpz-codex-run resume text`; replace `text` with `json` for JSONL events.

Credential and prompt values must never be placed in `docker exec` argv or container environment.
The driver owns timeout and output-size enforcement. The bypass flag inside the runner is allowed only
because Capsules use the externally enforced isolated runtime; the provider image must not be run as a
general-purpose host agent with that runner.

Codex stores API-key or ChatGPT OAuth state at `/config/.codex/auth.json`, matching OpenAI's documented
file-backed/headless contract. No `OPENAI_API_KEY` is baked into the image. Before releasing a Codex
image-contract change, require `tests/test-codex-brain-live.py` to build and boot the image successfully.
