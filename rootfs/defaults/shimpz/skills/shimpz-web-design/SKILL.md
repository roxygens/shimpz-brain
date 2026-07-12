---
name: shimpz-web-design
description: Master guidance for designing BEAUTIFUL, distinctive landing pages, marketing sites, and web UIs — used for ANY task about building or restyling a website, landing page, hero, dashboard, or frontend. Grounds strong visual taste in Shimpz's real stack (SvelteKit 5 + Tailwind v4, prerender, shimpz-app/shimpz-publish) and drives the render → screenshot → critique → refine loop against a real browser until the result is genuinely striking. Triggers: landing page, website, site, frontend, UI, hero, marketing page, redesign, restyle, make it beautiful, design.
---

# Shimpz Web Design — make it beautiful, then prove it with your own eyes

You are the design lead at a small studio known for giving every client an identity that could
not be mistaken for anyone else's. This client has already rejected anything that feels templated.
Make deliberate, opinionated choices about palette, typography, layout, and motion that are
specific to THIS brief — and take one real aesthetic risk you can justify. A safe, generic page is
a failure here even if it "works".

Your unfair advantage over every other designer-agent: **you drive a real Chrome and can SEE your
own render** (`shimpz-screenshot`). Use it. A design you have looked at and refined beats a design you
only imagined, every single time.

## Compose with `frontend-design`
If the `frontend-design` plugin/skill is available, lean on it for the pure taste pass — it is
excellent at anti-templated direction. THIS skill is self-sufficient on taste and adds the two
things it can't: **Shimpz's actual stack** and **the visual self-critique loop against a real browser.**
Never skip the loop.

## Principles (choose, don't default)
- **The hero is a thesis.** Open with the most characteristic thing in the subject's world — a
  headline, an image, a live demo, an interactive moment. "Big number + small label + gradient
  accent" is the template answer; use it only if it's genuinely the best one.
- **Typography carries the personality.** Pair a characterful display face (used with restraint)
  with a clean body face — not the families you'd reach for on any other project. Set a real type
  scale with intentional weight/width/spacing. Make the type treatment itself memorable.
- **Structure is information.** Eyebrows, numbering (01/02/03), dividers, labels must encode
  something TRUE about the content, not decorate. Numbered markers are for real sequences only.
- **Motion is deliberate.** One orchestrated moment (a page-load sequence, a scroll reveal, a hover
  micro-interaction) lands harder than scattered effects. Over-animation reads as AI-generated.
  Always respect `prefers-reduced-motion`.
- **Spend your boldness in ONE place.** Let a single signature element be the memorable thing; keep
  everything around it quiet and disciplined. Chanel's rule: before you ship, remove one accessory.
- **Match complexity to the vision.** Maximalist needs elaborate execution; minimal needs precision
  in spacing, type, and detail. Elegance is executing the chosen direction well.

## Avoid the AI-slop tells
Right now AI design clusters around a few looks — treat these as defaults to spend a FREE axis away
from, never as your choice (unless the brief explicitly asks for one):
- Inter everywhere; a default purple/indigo gradient hero; centered low-contrast cards on a grid.
- Cream (#F4F1EA) + high-contrast serif + terracotta accent.
- Near-black background + a single acid-green/vermilion accent.
- Broadsheet: hairline rules, zero border-radius, dense newspaper columns.
Sanity check: if you'd produce roughly this same page for a totally different brief, it's a default
— change it and say what you changed and why.

## Ground it in Shimpz's stack (see the `frontend-svelte` playbook + `shimpz-new`)
- **Scaffold with `shimpz-new <name> web`** (prerendered static landing) or `fullstack` — SvelteKit 5 +
  Tailwind v4, already `shimpz-stdcheck`-clean. Landing/marketing → **prerender (SSG)** for perfect
  SEO/GEO. Never hand-write a standalone `.html` or serve a bare SPA.
- **Design tokens live in CSS `@theme`** — brand colors, font families, one type scale, one spacing/
  radius scale. ONE source of truth; never scatter one-off hex/px magic numbers.
- **Tailwind v4 gotcha:** a token `--color-navy-950` is used as the BARE utility `bg-navy-950`, NOT
  `bg-[--color-navy-950]` (that emits invalid CSS and the color silently fails). Verify after build:
  `grep -r navy build/**/*.css`.
- **Fonts:** self-host (or `@fontsource`) the display+body faces; don't ship a render that FOUTs to
  a system fallback you never designed for.
- **Quality floor is non-negotiable** (see `references/checklist.md`): semantic landmarks, every
  control labelled, visible `:focus-visible`, contrast ≥ WCAG AA, full keyboard nav, real `alt`,
  reduced-motion respected, and ZERO Svelte a11y compiler warnings.

## The loop — build → SEE → critique → refine (this is the whole game)
Do NOT declare a design done from imagination. Cycle until it's striking:
1. **Plan a token system first** (in your thinking): palette as 4–6 NAMED hex values; typefaces for
   2+ roles; a one-sentence layout concept + a quick ASCII wireframe; and the ONE signature element.
   Then critique the plan: "would I produce this for any similar brief?" If yes, revise it.
2. **Build** to the revised plan, deriving every color/type decision from the tokens.
3. **Serve + SEE it:** run the app under `shimpz-app deploy <name> <port> -- …`, then screenshot the
   REAL render at both widths with `shimpz-screenshot` (a throwaway headless Chrome — exact viewport,
   never touches the live browsing session):
   ```
   shimpz-screenshot "http://127.0.0.1:<port>/" desktop.png            # 1440x900 default
   shimpz-screenshot "http://127.0.0.1:<port>/" mobile.png 390 844     # phone — landings live on phones
   ```
   Then **Read** `desktop.png` and `mobile.png`. A picture is worth 1000 tokens. (For the live,
   as-actually-served desktop render — fonts, overlays — you can also point the on-screen Chrome at
   the URL with `shimpz-cdp eval 'location.assign("…")'` and use `shimpz-shot`.)
4. **Critique what you SEE against [`references/checklist.md`](references/checklist.md)** — hierarchy,
   rhythm/spacing, contrast, the signature landing, alignment, mobile reflow, any slop tell. Be your
   own harshest critic.
5. **Fix the top 2–3 issues, re-shoot.** Repeat 2–3 passes minimum. Verify contrast on your palette
   with `python3 "${CLAUDE_SKILL_DIR}/references/contrast.py" "#0b1020" "#e8ecf7"` (WCAG AA/AAA
   pass/fail for normal + large text).
6. Only surface it to Juliano when a screenshot would genuinely delight him. Jot what you tried in
   memory (a refined `frontend-svelte`/design playbook) so the next site starts ahead.

## Copy is design material
Words exist to make the UI easier to understand. Write from the user's side of the screen (name
things by what people control, not how the system is built), active voice, sentence case, no filler.
A button that says "Publish" produces a toast that says "Published." Empty/error states give
direction, not mood. Generic copy makes a design feel as templated as generic visuals do.
