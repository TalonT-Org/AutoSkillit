# skills/

The lifecycle/bootstrap skills that gate every orchestrator session are
`open-kitchen`, `sous-chef`, and `close-kitchen`. The `skills/` directory
holds only the always-loaded kernel skills; the bulk of the skill catalog
lives in `skills_extended/`. Authoritative accounting lives in
`docs/skills/catalog.md`.

`skills/` is a Markdown-only skill-catalog directory (no Python package,
so it is not assigned to any import-linter IL layer); `skills_extended/`
shares the same shape.

## Architecture Notes

These skills are tightly coupled by the **open-kitchen → sous-chef
auto-load invariant**: every orchestrator session that calls
`/autoskillit:open-kitchen` immediately receives `sous-chef` as a
bootstrap document injected into context. `sous-chef` is **internal** —
not user-invocable, not exposed as a slash command, and not subject to
the categories/visibility conventions applied to user-invocable skills.

- `open-kitchen/` — Human-only entry point (`disable-model-invocation: true`).
  Calls the `open_kitchen` MCP tool to reveal the kitchen-tagged tools.
  See `open-kitchen/SKILL.md`.
- `sous-chef/` — Internal bootstrap document; injected by `open_kitchen`
  into every orchestrator session. See `sous-chef/SKILL.md`.
- `close-kitchen/` — Human-only exit point. Calls `close_kitchen` to
  hide the kitchen tools. See `close-kitchen/SKILL.md`.

`disable-model-invocation: true` is the standard mechanism preventing
model-initiated invocation of `open-kitchen` and `close-kitchen`. Do not
remove this flag without an explicit architectural decision.

## Multi-concern files

Files with no registration support their folder; files with several
registrations group one related concern.

- `open-kitchen/SKILL.md` — Skill: Human-only MCP kitchen reveal.
- `sous-chef/SKILL.md` — Skill: Internal orchestrator bootstrap document.
- `close-kitchen/SKILL.md` — Skill: Human-only MCP kitchen teardown.
