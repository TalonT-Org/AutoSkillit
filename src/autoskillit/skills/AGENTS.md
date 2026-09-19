# skills/

IL-2 skills layer — three lifecycle/bootstrap skills that gate every
orchestrator session: `open-kitchen`, `sous-chef`, and `close-kitchen`.

This package holds only the always-loaded kernel skills. The bulk of the
skill catalog lives in `skills_extended/` (138 bundled skills, of which
only `reload-session` carries `disable-model-invocation: true`; see
`docs/skills/catalog.md` for the canonical accounting).

## Architecture Notes

The three skills are tightly coupled by the **open-kitchen → sous-chef
auto-load invariant**: every orchestrator session that calls
`/autoskillit:open-kitchen` immediately receives `sous-chef` as a
bootstrap document injected into context. `sous-chef` is **internal** —
not user-invocable, not exposed as a slash command, and not subject to
the categories/visibility conventions applied to user-invocable skills.

- `open-kitchen/` — Human-only entry point (`disable-model-invocation: true`).
  Calls the `open_kitchen` MCP tool to reveal the 24 kitchen-tagged tools.
  See `open-kitchen/SKILL.md`.
- `sous-chef/` — Internal bootstrap document; injected by `open_kitchen`
  into every orchestrator session. Carries `uses_capabilities: [github_api_write,
  open_kitchen, run_skill, test_check]` and the canonical sibling-skill
  manifest. See `sous-chef/SKILL.md`.
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
