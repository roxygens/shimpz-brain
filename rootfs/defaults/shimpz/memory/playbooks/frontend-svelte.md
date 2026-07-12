---
task: build a frontend / UI / landing page
triggers: frontend, ui, tela, screen, landing, landing page, site, svelte, sveltekit, vite, seo, geo, dashboard
updated: 2026-06-29
---
# Frontend — the golden path: SvelteKit (Svelte 5 + Vite)

> **NON-NEGOTIABLE (even for "quick"/"simple"):** any web page goes through SvelteKit. **NEVER**
> hand-write a standalone `.html`, **NEVER** serve a raw HTML file with `python -m http.server`,
> **NEVER** a bare SPA. Serve the built site supervised with `shimpz-app`, expose with `shimpz-publish`
> (Caddy). The standard IS the fast path — `pnpm create` → edit `+page.svelte` → `pnpm build`.

Any UI = **SvelteKit** (Svelte 5 + Vite) via **pnpm** (Node 24, on PATH). Project in
`/config/workspace/projects/<name>/`. Never ship a bare client-only SPA for a landing page — it
breaks SEO/GEO (content isn't in the HTML).

## Pick the mode
- **Landing page / marketing / public content** → **prerender (SSG)** with `@sveltejs/adapter-static`
  → static HTML, fastest, perfect SEO, readable by AI answer engines (GEO).
- **App / dashboard / internal tool** → SvelteKit SSR (default `adapter-node`) or SPA mode as needed.

## Create it
**Prefer `shimpz-new`** — it scaffolds the whole compliant front (SvelteKit 5 + Tailwind v4 + relative `/api`
proxy), already `shimpz-stdcheck`-clean, via the official `sv` CLI:
```bash
shimpz-new <name> web          # frontend-only static landing (prerendered)
shimpz-new <name> fullstack    # front + FastAPI backend wired at /api
```
Only do it by hand if you need something `shimpz-new` doesn't cover:
```bash
cd /config/workspace/projects
pnpm create svelte@latest <name>     # choose: Skeleton, TypeScript
cd <name> && pnpm install
pnpm add -D @sveltejs/adapter-static # for landing pages (prerender)
pnpm dev        # dev server
pnpm build      # production build
```
For a landing page, set `adapter-static` in `svelte.config.js` and `export const prerender = true`
in the root `+layout.ts` (or per-page).

## Styling + design system + accessibility (mandatory)
**Tailwind CSS v4** is the styling layer (latest, Vite-native — no `tailwind.config.js`):
```bash
pnpm add -D tailwindcss @tailwindcss/vite      # or: pnpm dlx sv add tailwindcss
```
- `vite.config.ts`: add the `tailwindcss()` plugin. `src/app.css`: `@import "tailwindcss";`.
- **Design tokens live in CSS `@theme`** (brand colors, font families, spacing/radius scale) — one
  source of truth, used via utilities. NEVER scatter one-off hex/px magic numbers.
- **Consistent design system:** one type scale + one spacing scale + a small set of reusable Svelte
  components (Button, Card, Section, …). Pick ONE deliberate aesthetic direction up front.
- **The `shimpz-web-design` skill is baked in and auto-triggers** when you build UI (seeded to
  `~/.claude/skills/` on boot) — FOLLOW it: it drives distinctive direction (typography with intent,
  real hierarchy, considered color/contrast/space), the **avoid-AI-slop** guardrails, AND the
  render → `shimpz-shot` → critique → refine loop against your real Chrome until the result is striking.
  (If the optional `frontend-design` plugin is also present, `shimpz-web-design` composes with it.)
- **Accessibility (a11y) — non-negotiable:** semantic landmarks (`<header>/<nav>/<main>/<footer>`),
  every control labelled, visible `:focus-visible`, color contrast ≥ WCAG AA, full keyboard nav, real
  `alt`, respect `prefers-reduced-motion`. Svelte's a11y compiler warnings must be zero.
- **Tailwind v4 gotcha:** tokens declared in `@theme` (e.g. `--color-navy-950`) are used as the BARE
  utility `bg-navy-950` — NOT `bg-[--color-navy-950]` (that emits invalid CSS without `var()` and the
  color silently fails). Verify after build: `grep navy build/**/*.css`.

## Talking to a backend (fullstack)
If the page calls an API, fetch a **relative** path `/api/...` — NEVER hardcode `http://127.0.0.1:<port>`
or any host (it works in dev but breaks the moment it's published, because the user's browser would hit
*their* localhost). Deploy with `shimpz-publish <fqdn> <web-port> public <api-port>` (see `deploy-domain`):
Caddy serves the front and routes `/api/*` → the FastAPI backend (strip_prefix). Same code local + live.

## SEO + GEO checklist (mandatory for landing pages)
GEO = being readable/citable by AI answer engines — same foundation as SEO but the HTML must be
server-rendered and semantically structured. For every landing page:
- **Prerender** (content is in the HTML, not behind JS).
- `<svelte:head>`: unique `<title>`, meta description, **Open Graph** + **Twitter** cards.
- **JSON-LD / schema.org** structured data (`Organization`, `Article`, `FAQPage`, `Product`) — this
  is what generative engines consume most to understand and cite the page.
- Semantic HTML: a single `<h1>`, ordered headings, `<article>`/`<section>`, descriptive `alt`.
- `sitemap.xml` + `robots.txt`. Clear, factual, authoritative copy.
- Fast: static + optimized images → green Core Web Vitals.

## Where files go
- App in `projects/<name>/`. Built static output you want to ship lives in the project's `build/`.
- Secrets (API keys for the frontend's backend calls) follow the same .env rule as `dev-bootstrap`.

## Deploy (Phase 2)
Standing the site up on a domain (its own port → Caddy route → Cloudflare DNS, public or private
via Access) is the `deploy-domain` playbook — and Shimpz asks public/private first via `shimpz-ask`.

Keep code minimal (ponytail).
