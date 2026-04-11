# Project-Local Skill Overrides

## What Are Overrides?

Project-local overrides let you install customized versions of bundled AutoSkillit skills
alongside a project. For example, you can create a `review-pr` skill tuned to your team's
conventions, or a `make-plan` that enforces project-specific planning constraints.

Overrides live in `.claude/skills/` or `.autoskillit/skills/` within your project and take
precedence over the bundled skill of the same name.

## Creating an Override

Create a directory matching the bundled skill name and add a `SKILL.md` file:

```
.claude/skills/
└── review-pr/
    └── SKILL.md    ← your customized instructions
```

Example: to override `review-pr`, copy the bundled `SKILL.md` from
`src/autoskillit/skills_extended/review-pr/SKILL.md` as a starting point, then modify it
to add your team's review guidelines.

## Name-Matching Behavior

When a project-local skill matches a bundled skill by name:

1. **Bundled `/autoskillit:review-pr` is disabled for this project** — it is excluded from
   the ephemeral session directory during `init_session()`. The bundled skill is not deleted
   from the package; it is simply not copied into the session's skill directory.
2. **Project-local `/review-pr` takes precedence** — Claude Code discovers it via native
   skill scanning of `.claude/skills/`.
3. **Other projects are unaffected** — the bundled skill continues to work normally in
   any project without a local override.

## Namespace Coexistence

`/autoskillit:review-pr` (namespaced) and `/review-pr` (bare) are distinct Claude Code
slash commands. They coexist without collision:

| Command | Resolves to |
|---------|------------|
| `/review-pr` | Project-local override in `.claude/skills/review-pr/SKILL.md` |
| `/autoskillit:review-pr` | Bundled skill (disabled when override exists; see below) |

When a project-local override exists, the bundled skill is excluded from the ephemeral
session directory. If a recipe uses `skill_command: "/autoskillit:review-pr"`, recipe
validation will warn that the bundled skill is suppressed by a project-local override.
Use `skill_command: "/review-pr"` to reference the project-local version explicitly.

## Headless Session Resolution

`run_skill` passes `--add-dir <cwd>` to headless sessions in addition to
`--add-dir skills_extended/`, so Claude Code discovers project-local skills in
`.claude/skills/`. The same name-matching logic applies: a project-local skill named
`review-pr` suppresses the bundled `/autoskillit:review-pr` in the headless session.

## Checking Active Overrides

Compare your project-local skills against the bundled skill list:

```bash
# List project-local skills
ls .claude/skills/

# List bundled skills (Tier 2+3)
autoskillit skills list
```

Any name appearing in both lists is overriding its bundled counterpart.

## Recipe Validation Warnings

`validate_recipe` inspects the current project for local overrides and warns when a recipe
references a skill that has a project-local override:

```
WARNING: skill_command "/autoskillit:review-pr" references a bundled skill that is
suppressed by a project-local override in .claude/skills/review-pr/.
Consider using "/review-pr" to reference the project-local version explicitly.
```

This is a warning, not an error — the override may be intentional (e.g. you want the
bundled version for recipes and the project-local version for interactive use). The
warning ensures the difference is visible at validation time.
