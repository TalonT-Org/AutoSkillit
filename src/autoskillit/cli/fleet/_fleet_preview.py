"""Pre-launch dispatch preview: recipe roster + tool surface display."""

from __future__ import annotations

from pathlib import Path


def _build_dispatch_recipe_table() -> str:
    """Build a compact NAME — DESCRIPTION table of standard recipes for greeting injection."""
    from autoskillit.recipe import RecipeKind, list_recipes

    recipes = list_recipes(Path.cwd(), exclude_kinds=frozenset({RecipeKind.CAMPAIGN})).items
    if not recipes:
        return "(no recipes found)"
    name_w = max(len(r.name) for r in recipes)
    lines = [f"{r.name:<{name_w}}  {r.description}" for r in recipes]
    return "\n".join(lines)


def _print_dispatch_preview(cfg: object) -> None:
    """Print the pre-launch summary for fleet dispatch (mirrors cook's pre-launch display)."""
    from autoskillit.cli.ui._ansi import permissions_warning, supports_color
    from autoskillit.recipe import RecipeKind, list_recipes

    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    from autoskillit import __version__

    print(
        f"{_B}{_C}AUTOSKILLIT {__version__}{_R}"
        f" {_D}Fleet dispatcher. Ad-hoc food truck coordination.{_R}"
    )

    recipes = list_recipes(Path.cwd(), exclude_kinds=frozenset({RecipeKind.CAMPAIGN})).items
    if recipes:
        name_w = max(len(r.name) for r in recipes)
        src_w = max(len(r.source) for r in recipes)
        print(f"\n{_B}Available food trucks:{_R}")
        print(f"  {'NAME':<{name_w}}  {'SOURCE':<{src_w}}  DESCRIPTION")
        print(f"  {'-' * name_w}  {'-' * src_w}  {'-' * 11}")
        for r in recipes:
            print(f"  {_G}{r.name:<{name_w}}{_R}  {_D}{r.source:<{src_w}}{_R}  {r.description}")
    else:
        print(f"\n{_D}No recipes found.{_R}")

    _DISPATCH_TOOL_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
        ("Dispatch", ("dispatch_food_truck",)),
        ("Cleanup", ("batch_cleanup_clones",)),
        (
            "Telemetry",
            ("get_pipeline_report", "get_token_summary", "get_timing_summary", "get_quota_events"),
        ),
        ("Recipes", ("list_recipes", "load_recipe")),
        ("GitHub", ("fetch_github_issue", "get_issue_title")),
    ]
    print()
    for name, tools in _DISPATCH_TOOL_CATEGORIES:
        tool_list = f"{_D}, {_R}".join(f"{_G}{t}{_R}" for t in tools)
        print(f"  {_Y}{name:>20}{_R}  {tool_list}")
    print()

    print(permissions_warning())
