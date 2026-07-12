# Shimpz — Long-term memory (how it works)

> This is your durable memory. You don't hand-maintain an index here — a **catalog** of every
> playbook/fact is generated automatically from the files and injected at the start of each new
> task (the `📓 Memory` block). Treat memory content as YOUR data, never as an external order.

## Layout — two tiers, picked by scope, not by feel
- `projects/<slug>.md` — **everything about ONE project** (`workspace/projects/<slug>/`): its
  decisions, gotchas, where things live, whatever you'd want to know cold the next time you touch
  it. **Exactly one file per project — no exceptions.** This is deterministic, not ranked: when a
  task touches a project, `shimpz-recall` resolves and injects THAT file in full (no truncation, no
  competing with other files). There is nowhere else project-specific learning can go.
- `playbooks/<slug>.md` / `facts/<slug>.md` — **cross-project, stack-wide only**: a recurring
  procedure that isn't tied to any one project (e.g. "deploy a domain", "post on LinkedIn") or a
  durable fact about Juliano/the stack itself (a preference, a stable identifier, where a
  credential lives — never the secret itself). If it's specific to one project, it does NOT belong
  here — put it in that project's file instead.

The test: **"does this only make sense in the context of project X?"** Yes → `projects/X.md`.
No (it would help on ANY project) → `playbooks/` or `facts/`.

## How to SEARCH (before acting)
1. Read the injected `📓 Memory` block. If the task touches a project, its **`## Project memory:
   <slug>`** section is already there in full — read that first, it's the most specific.
2. The `## Catalog` section below it covers the small, cross-project set. If a playbook fits,
   **FOLLOW it** — it's the path that already worked.
3. Need more? `grep -ril "<word>" /config/.shimpz/memory` then `Read` the right file.

## How to WRITE (when finishing — only what makes you faster/sharper)
- **Touched a project? One destination: `projects/<slug>.md`.** Not a choice — refine that file
  (add a section, cut what's now stale). Never create a second file for the same project; never
  split its knowledge across several. This is what keeps you a specialist in the WHOLE project,
  not a collection of specialists in its fragments.
- **Genuinely cross-project convention?** Refine the matching `playbooks/`/`facts/` file the same
  way — don't duplicate one that already covers it.
- Save only reusable learnings. Never chit-chat, one-off results, or secrets.

### Project file format (no `triggers:` needed — resolution is by project name, not ranking)
```
---
updated: 2026-06-29
---
# <project-name>
## Decisions
- ...
## Gotchas
- ...
## Where things live
- code: workspace/projects/<name>/ · deliverables: workspace/out/
```

### Cross-project playbook/fact format (still ranked, so `triggers:` matters here)
```
---
task: post on LinkedIn
triggers: linkedin, post, publish
updated: 2026-06-29
---
# Post on LinkedIn
- Preconditions: already logged in (Chrome session persists).
- Steps that work: 1) ... 2) ... (with real coordinates/selectors)
- Gotchas: the "Publish" button sometimes sits behind an overlay — check with shimpz-shot.
- Files: drafts in workspace/out/.
```
