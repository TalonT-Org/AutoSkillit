"""Regenerate the standalone hook session-scope authority."""

import autoskillit.hooks  # noqa: F401  (populates the lazy hook registry)
from autoskillit.hook_registry import HOOKS_DIR, render_hook_scope_table


def main() -> None:
    target = HOOKS_DIR / "_runtime" / "_hook_scope_table.py"
    target.write_text(render_hook_scope_table(), encoding="utf-8")


if __name__ == "__main__":
    main()
