"""Pre-launch dispatch preview: recipe roster + tool surface display."""

from __future__ import annotations

from pathlib import Path

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

_FLEET_DISPATCH_GREETINGS: list[str] = [
    "Fleet dispatcher online. Ready for dispatch orders.",
    "Welcome to fleet dispatch. What targets are we dispatching to?",
    "Dispatcher standing by. Issue your dispatch orders when ready.",
]

_FLEET_CAMPAIGN_GREETINGS: list[str] = [
    "Campaign ready: {campaign_name}. Let me display the ingredients and get started.",
    "Launching campaign: {campaign_name}. Reviewing ingredients now.",
    "Campaign {campaign_name} loaded. Checking ingredients before dispatch.",
]


def _print_dispatch_preview() -> str:
    """Print the pre-launch summary for fleet dispatch (mirrors cook's pre-launch display).

    Returns the recipe table string (name + description) for system prompt injection.
    """
    from autoskillit.cli.ui._ansi import permissions_warning, supports_color
    from autoskillit.core import load_yaml  # noqa: PLC0415
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

    recipes = list_recipes(
        Path.cwd(), exclude_kinds=frozenset({RecipeKind.CAMPAIGN}), exclude_dispatch_only=True
    ).items
    if recipes:
        name_w = max(len(r.name or "") for r in recipes)
        src_w = max(len(r.source or "") for r in recipes)
        print(f"\n{_B}Available food trucks:{_R}")
        print(f"  {'NAME':<{name_w}}  {'SOURCE':<{src_w}}  DESCRIPTION")
        print(f"  {'-' * name_w}  {'-' * src_w}  {'-' * 11}")
        for r in recipes:
            name = r.name or ""
            src = r.source or ""
            print(f"  {_G}{name:<{name_w}}{_R}  {_D}{src:<{src_w}}{_R}  {r.description}")
        greeting_table = "\n".join(f"{(r.name or ''):<{name_w}}  {r.description}" for r in recipes)
    else:
        print(f"\n{_D}No recipes found.{_R}")
        greeting_table = "(no recipes found)"

    # Show campaign sub-recipes in a separate section
    all_result = list_recipes(Path.cwd(), exclude_dispatch_only=False)
    dispatch_only_recipes = [r for r in all_result.items if r.dispatch_only]
    if dispatch_only_recipes:
        all_campaigns = [r for r in all_result.items if r.kind == RecipeKind.CAMPAIGN]
        campaign_children: dict[str, list[str]] = {}
        for c in all_campaigns:
            if c.content:
                data = load_yaml(c.content)
                children = [
                    d.get("recipe", "") for d in (data.get("dispatches") or []) if d.get("recipe")
                ]
                if children:
                    campaign_children[c.name] = children

        print(f"\n  {_D}Campaign sub-recipes (dispatched by campaigns, not standalone):{_R}")
        for cname, children in sorted(campaign_children.items()):
            child_str = ", ".join(children)
            print(f"    {_G}{cname:<{name_w}}{_R}  {_D}-> {child_str}{_R}")

    print()
    for name, tools in _DISPATCH_TOOL_CATEGORIES:
        tool_list = f"{_D}, {_R}".join(f"{_G}{t}{_R}" for t in tools)
        print(f"  {_Y}{name:>20}{_R}  {tool_list}")
    print()

    print(permissions_warning())
    return greeting_table
