"""Features subapp: list and inspect feature gate state."""

from __future__ import annotations

import sys
from pathlib import Path

from cyclopts import App

features_app = App(name="features", help="Feature gate inspection.")


@features_app.command(name="list")
def features_list() -> None:
    """List all registered features with their effective state."""
    from autoskillit.config import load_config
    from autoskillit.core import (
        FEATURE_REGISTRY,
        FeatureLifecycle,
        TerminalColumn,
        _render_terminal_table,
        is_feature_enabled,
    )

    cfg = load_config(Path.cwd())

    columns = [
        TerminalColumn("FEATURE", 20, "<"),
        TerminalColumn("TIER", 5, ">"),
        TerminalColumn("LIFECYCLE", 14, "<"),
        TerminalColumn("DEFAULT", 9, "<"),
        TerminalColumn("EFFECTIVE", 18, "<"),
        TerminalColumn("SOURCE", 14, "<"),
    ]

    rows = []
    for name, defn in sorted(FEATURE_REGISTRY.items()):
        effective = is_feature_enabled(
            name, cfg.features, experimental_enabled=cfg.experimental_enabled
        )
        if (
            defn.lifecycle == FeatureLifecycle.EXPERIMENTAL
            and effective
            and name not in cfg.features
        ):
            effective_str = "on (experimental)"
            source = "experimental"
        elif name in cfg.features and not effective:
            effective_str = "off (override)"
            source = "config"
        elif name in cfg.features:
            effective_str = str(effective).lower()
            source = "config"
        else:
            effective_str = str(effective).lower()
            source = "default"
        rows.append(
            (
                name,
                str(defn.tier),
                str(defn.lifecycle),
                str(defn.default_enabled).lower(),
                effective_str,
                source,
            )
        )

    print(_render_terminal_table(columns, rows))


@features_app.command(name="status")
def features_status(name: str) -> None:
    """Show detailed state for a single feature."""
    from autoskillit.config import load_config
    from autoskillit.core import FEATURE_REGISTRY, FeatureLifecycle, is_feature_enabled

    if name not in FEATURE_REGISTRY:
        known = sorted(FEATURE_REGISTRY.keys())
        print(
            f"Unknown feature: {name!r}\nKnown features: {', '.join(known)}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    defn = FEATURE_REGISTRY[name]
    cfg = load_config(Path.cwd())
    effective = is_feature_enabled(
        name, cfg.features, experimental_enabled=cfg.experimental_enabled
    )

    if defn.lifecycle == FeatureLifecycle.EXPERIMENTAL and effective and name not in cfg.features:
        source = "on by experimental_enabled blanket"
    elif name in cfg.features:
        source = "overridden by config"
    else:
        source = "default"
    enabled_str = f"{'true' if effective else 'false'} ({source})"

    tool_tags = ", ".join(sorted(defn.tool_tags)) if defn.tool_tags else "(none)"
    skill_cats = ", ".join(sorted(defn.skill_categories)) if defn.skill_categories else "(none)"
    depends = ", ".join(sorted(defn.depends_on)) if defn.depends_on else "(none)"

    print(f"Feature: {name}")
    print(f"  Lifecycle:    {defn.lifecycle}")
    print(f"  Tier:         {defn.tier}")
    print(f"  Enabled:      {enabled_str}")
    print(f"  Package:      {defn.import_package or '(none)'}")
    print(f"  Tool tags:    {tool_tags}")
    print(f"  Skill cats:   {skill_cats}")
    print(f"  Since:        {f'v{defn.since_version}' if defn.since_version else '(none)'}")
    print(f"  Depends on:   {depends}")
    print(f"  Sunset date:  {defn.sunset_date or '(none)'}")
