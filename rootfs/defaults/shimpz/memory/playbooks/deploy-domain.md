---
task: publish an app on its own domain (container deploy → reverse-proxy → DNS → public/private)
triggers: deploy, publicar, dominio, domain, subir online, colocar no ar, landing no ar, hospedar, dns, cloudflare, porta, expose, go live
updated: 2026-07-04
---
# Deploy a domain — container deploy → Caddy route → DNS → public/private

Every web app Shimpz builds runs in its OWN container (via **shimpz-driver** — `shimpz-brain` itself never
touches Docker or Cloudflare directly), is reverse-proxied by **shimpz-caddy** on hostname, and exposed
through the **single Cloudflare Tunnel**. The origin is never published to the host — all inbound
traffic comes back through the tunnel. Stack/build = the `dev-bootstrap` (backend) and
`frontend-svelte` (landing) playbooks; THIS is only the going-live.

## 0. ASK FIRST — public or private?  (never assume)
```
choice=$(shimpz-ask "Is this domain public or private?" "🌐 Public (anyone can access)" "🔒 Private (only you, via Cloudflare login)")
```
- **Landing page / marketing site → 🌐 Public.**
- **Dashboard / admin / internal tool → 🔒 Private** (Cloudflare Access: identity + one-time PIN).
- If unsure which port/name/domain, `shimpz-ask` that too. Don't guess paid or published actions.

## 0b. Security gate (automatic) — a PUBLIC backend is audited before it goes live
When you publish a fullstack/api app **public** (`shimpz-publish <fqdn> <web> public <api>`), shimpz-publish
runs an independent **Opus auditor (`shimpz-secaudit`)** over the backend source FIRST — threat-modeling
the exposed surface for BOLA/IDOR (a handler loading a row by client id without an ownership check),
unauthenticated private/user data, injection, mass assignment, leaked secrets. SAFE → it publishes.
Not SAFE → it pauses and asks Juliano via `shimpz-approve` (showing the findings); deny/timeout ABORTS
before anything is exposed. A public marketing/content API with no private data is SAFE (no false
alarm over 'no login'). So: get object-level authz right BEFORE publishing. Private scope is NOT
audited (Cloudflare Access already restricts it to Juliano). Kill-switch: `SHIMPZ_SECAUDIT=0`.

## 1. Pick a port (one per app)
App ports live in **3100–3999** (3000/3001 = KasmVNC, 9222 = CDP, 5432 = Postgres, 8080 = Caddy,
2019 = Caddy admin). Any free one in range works — it's only reachable inside the app's own
container network, never the host, so collisions with anything else on the machine don't matter.

## 2. Deploy the app in its own container  (`shimpz-app`)
`shimpz-app` talks ONLY to shimpz-driver (the one container holding docker.sock) — `shimpz-brain` never
creates containers directly. The app binds `0.0.0.0:$PORT` INSIDE its own container (shimpz-caddy
reaches it by container name over its own isolated network, never the host or another app).
```
# backend (FastAPI):
shimpz-app deploy <name> $port -- uv run uvicorn app:app --host 0.0.0.0 --port $port
# static landing (SvelteKit prerender → build/):  serve the built dir
shimpz-app deploy <name> $port -- python3 -m shimpz_static $port --directory build
```
`shimpz-app list` / `shimpz-app logs <name>` / `shimpz-app restart <name>` / `shimpz-app rm <name>`. Redeploy is
transactional (blue-green): a broken candidate never takes down the currently-live container — just
re-run `shimpz-app deploy` with the fix; the old version keeps serving until the new one proves healthy.
**Don't run the app manually** to "test" it — deploy with `shimpz-app`, then test with `curl` against
the real container; debug with `shimpz-app logs <name>`.

## 3. Route the hostname  (`shimpz-route`)
```
shimpz-route add <fqdn> $port      # e.g. shimpz-route add loja.zyon.network 3101  → live immediately
```
Routes only ever target `app_<name>` containers shimpz-driver manages — never `shimpz-brain` itself.
shimpz-caddy serves plain HTTP on :8080; TLS is terminated at Cloudflare. `shimpz-route list` / `del`.

## ⚡ One-shot: `shimpz-publish <fqdn> <port> [public|private]`
After steps 1–2 (port + deployed app), `shimpz-publish` does ingress + DNS + Caddy route + Access in
one go, preserving existing tunnel rules and rolling back EVERY step this run touched if any later
step fails (never leaves a domain half-wired). Prefer it over doing 3–5 by hand:
```
shimpz-app deploy <name> <port> -- <cmd...>      # step 2 first
shimpz-publish <fqdn> <port> public              # ingress + DNS + route + public, idempotent
# fullstack (front + API on one domain): the front fetches relative /api, Caddy routes it to the backend
shimpz-publish <fqdn> <web-port> public <api-port>
shimpz-unpublish <fqdn>                          # full teardown: ingress + DNS + route + Access (inverse)
```
Each step prints OK/FAIL. If the DNS step says `Zone:DNS:Edit`, cf-driver's token lacks DNS
records edit — everything else is applied; tell Juliano to add **Zone → DNS → Edit** to the
Cloudflare token, then re-run. There is no manual step 4/5 breakdown anymore (see below) — always
use `shimpz-publish`/`shimpz-unpublish`.

## 4/5. Tunnel ingress + DNS + public/private — ALWAYS via `shimpz-publish`/`shimpz-unpublish`, never by hand
`shimpz-brain` does NOT hold a Cloudflare token and cannot call the Cloudflare API directly
(SECURITY_ENGINEERING_PLAN.md item 3 — a prompt-injected or compromised agent must never be able to
rewrite arbitrary DNS/Tunnel/Access state). The token lives ONLY in **cf-driver**, a separate
sidecar exposing a few narrow, named, audited operations (resolve a zone, upsert/remove ONE tunnel-
ingress rule, upsert/remove ONE DNS record, create/remove/restore ONE Access app) — `shimpz-publish`/
`shimpz-unpublish` are the ONLY things that call it. There is no general-purpose Cloudflare client
anymore, and no "drop to raw API for a one-off op" escape hatch. If you need a Cloudflare capability
`shimpz-publish`/`shimpz-unpublish` don't cover, that's a real gap — tell Juliano, don't try to work around
it (there's nothing to work around it WITH; the old `cf` helper was removed on purpose).

## 6. Verify + report
```
curl -sS -H "Host: <fqdn>" http://shimpz-caddy:8080/ -o /dev/null -w '%{http_code}\n'   # local Caddy hit
```
Then tell Juliano the live URL, whether it's public/private, and the port — short and concrete.

## Notes / gotchas
- **No raw Cloudflare access from `shimpz-brain` at all.** Every Cloudflare-touching action goes through
  `shimpz-publish`/`shimpz-unpublish` only. Toggling public/private later without a full re-publish isn't
  currently a separate command — re-run `shimpz-publish <fqdn> <port> <new-scope>` (idempotent).
- **Bind `0.0.0.0` inside the app's own container** — the app's own network isolation (not a
  loopback bind) is what keeps it unreachable from anywhere except shimpz-caddy and, if declared,
  its own database/bus.
- **One app = one container = one Caddy route.** Tear down the DOMAIN with
  **`shimpz-unpublish <fqdn>`** (removes ingress + DNS + route + Access in one go); then `shimpz-app rm <name>`
  for the container and `shimpz-db drop <name>` for its database. Never leave an orphan ingress rule.
