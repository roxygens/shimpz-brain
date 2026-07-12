# Design critique checklist — run this against the SCREENSHOT, not the code

Open `desktop.png` and `mobile.png` and score each. Anything ✗ is the next fix. Be harsh; a design
that passes every line is the floor, not the ceiling.

## First impression (the 2-second test)
- [ ] Does it look like it was designed for THIS subject — not a template you'd use for anything?
- [ ] Is there ONE clear signature element the eye lands on and remembers?
- [ ] Does it avoid the slop tells (Inter everywhere, purple gradient hero, generic centered cards,
      cream+serif+terracotta, near-black+acid, broadsheet hairlines)?

## Hierarchy & composition
- [ ] Clear focal point; the eye knows where to go first, second, third.
- [ ] One type scale, used consistently — display / heading / body / caption are visibly distinct.
- [ ] Generous, intentional whitespace; nothing cramped, nothing floating in a void.
- [ ] Alignment is deliberate (a real grid); no accidental 1–3px misalignments.
- [ ] Vertical rhythm consistent between sections (no CSS specificity collisions eating padding).

## Color & type
- [ ] Palette is 4–6 deliberate, named tokens — no random one-off hexes.
- [ ] Body text contrast ≥ WCAG AA (4.5:1); large text ≥ 3:1. Verify with `contrast.py`.
- [ ] Display + body faces are a deliberate PAIR, self-hosted, no FOUT to a system fallback.
- [ ] Accent color is used with restraint, on purpose, not sprinkled.

## Motion & polish
- [ ] Motion serves the content (one orchestrated moment > scattered effects); not over-animated.
- [ ] `prefers-reduced-motion` respected.
- [ ] Hover/focus states feel considered, not default browser blue.

## Mobile (landings live on phones)
- [ ] Reflows cleanly at 390px — no horizontal scroll, no clipped hero, tap targets ≥ 44px.
- [ ] Type stays readable; the signature element still lands on a small screen.

## Accessibility floor (non-negotiable)
- [ ] Semantic landmarks: `<header> <nav> <main> <footer>`.
- [ ] Every control labelled; visible `:focus-visible`; full keyboard nav.
- [ ] Real `alt` on meaningful images; decorative images `alt=""`.
- [ ] ZERO Svelte a11y compiler warnings.

## Copy
- [ ] Written from the user's side (what they control, not how it's built); active voice; sentence case.
- [ ] Action labels are consistent through the flow ("Publish" → toast "Published").
- [ ] Empty/error states give direction, not mood.

## The Chanel pass
- [ ] Remove one accessory. Is there any decoration that doesn't serve the brief? Cut it.
