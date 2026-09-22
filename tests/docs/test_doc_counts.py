"""Verify documentation names stable relationships and their authorities."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("docs"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "autoskillit"
DOCS_DIR = REPO_ROOT / "docs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _count_doctor_checks() -> int:
    """Count isolated check invocations inside ``_collect_doctor_results``."""
    text = _read(SRC_DIR / "cli" / "doctor" / "__init__.py")
    tree = ast.parse(text)
    collector = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_collect_doctor_results"
        ),
        None,
    )
    assert collector is not None, "_collect_doctor_results not found in cli/doctor/__init__.py"
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_check"
        for node in ast.walk(collector)
    )


def _quota_thresholds_default() -> tuple[float, float]:
    data = load_yaml(SRC_DIR / "config" / "defaults.yaml")
    quota = data.get("quota_guard")
    assert quota is not None, "quota_guard key missing from config/defaults.yaml"
    short = quota.get("short_window_threshold")
    long_ = quota.get("long_window_threshold")
    assert short is not None, "quota_guard.short_window_threshold key missing"
    assert long_ is not None, "quota_guard.long_window_threshold key missing"
    return float(short), float(long_)


def test_configuration_states_quota_thresholds() -> None:
    short, long_ = _quota_thresholds_default()
    assert short == pytest.approx(85.0)
    assert long_ == pytest.approx(95.0)


def test_doctor_check_count_is_59() -> None:
    # Combined-tree canonical count: 48 numbered checks + 10 lettered sub-checks.
    # Check 47 (S2-5): pytest-generation temp-root capacity and orphaned-generation count.
    # Update both tests whenever a new doctor check is added.
    count = _count_doctor_checks()
    assert count == 59, f"Expected 59 doctor checks; found {count}"


def test_hook_docs_name_registry_authority() -> None:
    text = _read(DOCS_DIR / "safety" / "hooks.md")
    assert "HOOK_REGISTRY" in text
    assert "src/autoskillit/hook_registry/" in text


def test_doctor_docs_name_run_doctor_authority() -> None:
    for path in (DOCS_DIR / "cli.md", DOCS_DIR / "installation.md"):
        text = _read(path)
        assert "run_doctor" in text
        assert "cli/doctor/__init__.py" in text


def test_process_issues_is_documented_as_role_derived_not_tiered() -> None:
    for path in (DOCS_DIR / "skills" / "visibility.md", DOCS_DIR / "skills" / "catalog.md"):
        text = _read(path)
        tier2_start = text.index("## Tier 2")
        tier3_start = text.index("## Tier 3", tier2_start)
        assert "`process-issues`" not in text[tier2_start:tier3_start]
        assert re.search(
            r"(?is)(process-issues.{0,200}orchestrat|orchestrat.{0,200}process-issues)",
            text,
        ), f"{path} must document process-issues as role-derived orchestration"


def test_architecture_doc_names_declared_interactive_discovery_route() -> None:
    from autoskillit.execution.backends._codex_discovery import (
        CODEX_MANAGED_HOME_ROUTE,
        CODEX_PROJECTED_HOME_ROUTE,
        CODEX_SKILL_DISCOVERY_CONTRACT,
    )

    text = _read(DOCS_DIR / "execution" / "architecture.md")
    assert CODEX_MANAGED_HOME_ROUTE.name in text
    assert CODEX_PROJECTED_HOME_ROUTE.name in text
    assert "tests/arch/test_skill_discovery_routes.py" in text
    assert CODEX_SKILL_DISCOVERY_CONTRACT.verified_binary in text


def test_catalog_does_not_reference_open_pr() -> None:
    assert "`open-pr`" not in _read(DOCS_DIR / "skills" / "catalog.md")


def test_catalog_lists_all_skills_in_extended_dir() -> None:
    catalog = _read(DOCS_DIR / "skills" / "catalog.md")
    missing = [
        path.name
        for path in sorted((SRC_DIR / "skills_extended").iterdir())
        if path.is_dir() and f"`{path.name}`" not in catalog
    ]
    assert missing == [], f"Skills in skills_extended/ not listed in catalog.md: {missing}"


def test_authoring_keeps_planner_example() -> None:
    assert "`planner`" in _read(DOCS_DIR / "recipes" / "authoring.md")


def test_authoring_recipe_step_fields_match_schema() -> None:
    authoring = _read(DOCS_DIR / "recipes" / "authoring.md")
    for field in ("`name`", "`with_args`", "`on_result`", "`retries`"):
        assert field in authoring
    field_summary_line = next(
        (line for line in authoring.splitlines() if "with_args" in line or "id`," in line),
        None,
    )
    assert field_summary_line is not None
    assert "id`," not in field_summary_line
    assert "params`," not in field_summary_line
    assert "verdict_routes`" not in field_summary_line
    assert "retry`." not in field_summary_line


def test_subsets_lists_required_packs() -> None:
    subsets = _read(DOCS_DIR / "skills" / "subsets.md")
    for pack in ("kitchen-core", "research", "exp-lens", "vis-lens"):
        assert pack in subsets, f"subsets.md missing pack category: {pack}"
