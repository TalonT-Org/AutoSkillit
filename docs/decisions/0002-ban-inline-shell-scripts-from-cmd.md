# ADR-0002: Ban Inline Shell Scripts from Recipe cmd Fields

**Status:** Accepted
**Date:** 2026-05-01
**Issue:** [#1584](https://github.com/TalonT-Org/AutoSkillit/issues/1584)

## Context

- Recipe YAML files are declarative workflow graphs defining *what* happens and in *what order*.
- PR #1583 introduced a 12-line bash script with variable assignments, arithmetic, file I/O, and control flow directly in a `cmd:` field.
- A systematic scan reveals 61 `run_cmd` steps across 6 recipe files containing inline shell scripts.
- Inline scripts make recipes unreadable, prevent reuse, defeat syntax highlighting, and make it trivially easy for automated PRs to introduce more.

## Decision

> **Code belongs in code files. Recipes are purely declarative.**

- **Python logic** → `.py` file, invoked via `run_python` callable.
- **Bash logic** → `.sh` file in `scripts/recipe/`, invoked via `run_cmd cmd: bash scripts/recipe/foo.sh`.
- **Recipe YAML** → declarative only. Names the thing to call, passes parameters. Zero logic.

A recipe step's `cmd:` field must never contain shell control flow (`if/then/else/fi`, `for/do/done`, `while/do/done`, `case/esac`), variable assignments beyond simple parameter passing, loops, or embedded Python (`python3 -c`).

## Rationale

- Recipes should be auditable at a glance — scanning step names and their targets tells the story.
- Shell scripts in `.sh` files get syntax highlighting, linting (shellcheck), and version control diffs.
- Python callables get type checking, testing, and IDE support.
- Deduplication becomes trivial — shared logic lives in one callable/script, referenced by name.
- The lint rule prevents regression automatically.

## Enforcement

- **Lint rule:** `inline-script-in-cmd` in `src/autoskillit/recipe/rules_inline_script.py`
- **Lint rule:** `inline-python-in-cmd` in the same module (catches `python3 -c`)
- **Validation:** `validate_recipe` → `run_semantic_rules` fires on every recipe load
- **Existing violations:** Grandfathered via `_INLINE_SCRIPT_ALLOWLIST` frozen set until externalized
- **Test command:** `task test-all` (includes recipe validation)

## Scope

- All recipe YAML files under `src/autoskillit/recipes/`
- All sub-recipe YAML files under `src/autoskillit/recipes/sub-recipes/`
- The `cmd` field of any `run_cmd` step in any recipe
