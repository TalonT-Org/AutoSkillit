"""Architectural invariant: every config field must be consumed in production.

The config ledger proves a key *parses*. Nothing proved a key was ever *read*, and
three fields were parsed, validated, and dead from birth —
`force_claude_agent_teams_inactive` among them, which is why a documented opt-in
could not be opted into. This is the missing half, modeled on
`tests/arch/test_capability_consumption.py`.

**Known limitation — this is a floor, not a proof.** The scan matches bare
attribute names with no owner-type inference, exactly as the capability scan
does. `BackendCapabilities` gets away with that because its field names are long
and distinctive; config field names collide across classes. At the time of
writing: `timeout` (4 classes), `command`, `enabled`, `recipe_overrides`,
`step_overrides` (3 each), and `idle_output_timeout` (2). A dead field sharing a
name with a live field on another config class passes here undetected. Tightening
this to owner-aware resolution would catch that; until then, do not read a green
run as evidence that every field is individually consumed.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import date
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@dataclasses.dataclass(frozen=True)
class ForwardDeclaredField:
    """Structured forward-declaration for a config field without a consumer."""

    issue: int
    rationale: str
    added_date: date


_STALENESS_THRESHOLD_DAYS = 180

_FORWARD_DECLARED: dict[str, ForwardDeclaredField] = {
    "context_window": ForwardDeclaredField(
        issue=4685,
        rationale=(
            "ProviderProfileDef.context_window is parsed and range-validated but "
            "_profile_to_env projects every other profile field and skips it"
        ),
        added_date=date(2026, 8, 17),
    ),
    "natural_exit_grace_seconds": ForwardDeclaredField(
        issue=4686,
        rationale=(
            "RunSkillConfig.natural_exit_grace_seconds is validated against "
            "exit_after_stop_delay_ms but never threaded to run_managed_async's "
            "identically-named parameter, so the drain window always uses 3.0"
        ),
        added_date=date(2026, 8, 17),
    ),
}


def _config_dataclasses() -> list[type]:
    """Every dataclass declared in the config definition module.

    Enumerated from the module directly rather than walking AutomationConfig's
    field tree: ProviderProfileDef is frozen and is synthesized at call time
    inside ProvidersConfig.resolved_profiles, so it is never a declared field
    type and a tree walk misses it — along with one of the dead fields.
    """
    from autoskillit.config import _config_dataclasses as module

    return [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type)
        and dataclasses.is_dataclass(obj)
        and obj.__module__ == module.__name__
    ]


def _config_field_names() -> frozenset[str]:
    """All config field names. dataclasses.fields() already excludes ClassVar."""
    return frozenset(
        field.name for cls in _config_dataclasses() for field in dataclasses.fields(cls)
    )


def _definition_file() -> Path:
    from autoskillit.config import _config_dataclasses as module

    return Path(inspect.getfile(module)).resolve()


def _post_init_descendants(tree: ast.AST) -> set[int]:
    """Node ids living inside a ``__post_init__`` body."""
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != "__post_init__":
                continue
            for descendant in ast.walk(node):
                skipped.add(id(descendant))
    return skipped


def _collect_attribute_reads(src_root: Path, field_names: frozenset[str]) -> dict[str, list[str]]:
    """Scan src/ for .field_name attribute access that constitutes consumption.

    The whole config *package* is scanned, not excluded. Several live fields
    reach production only through legitimate in-package adapters —
    SkillsConfig.tier1/2/3 via AutomationConfig.skill_visibility_spec(),
    DiagnosticsConfig.pipeline_health / ReviewConfig.local_review_rounds /
    PlanConfig.adversarial_review_level via ingredient_defaults — and excluding
    the package would report every one of them as dead.

    Within the definition file, only ``__post_init__`` bodies are skipped: a
    field that merely validates itself does nothing for anyone. Other methods
    there are real accessors — GitHubConfig.allowed_labels is read solely by
    check_label_allowed(), which six production call sites depend on, so
    excluding the file wholesale would flag a live field.
    """
    reads: dict[str, list[str]] = {name: [] for name in field_names}
    definition_file = _definition_file()
    for py_file in src_root.rglob("*.py"):
        relpath = str(py_file.relative_to(src_root))
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        skipped = _post_init_descendants(tree) if py_file.resolve() == definition_file else set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in field_names
                and id(node) not in skipped
            ):
                reads[node.attr].append(f"{relpath}:{node.lineno}")
    return reads


def test_config_dataclass_discovery_is_not_empty() -> None:
    """A silently empty enumeration would make the consumption test vacuous."""
    classes = _config_dataclasses()
    names = {cls.__name__ for cls in classes}

    assert len(classes) > 20
    assert "ProviderProfileDef" in names, (
        "frozen, call-time-synthesized classes must be reached — one dead field lives here"
    )
    assert "AgentBackendConfig" in names
    assert len(_config_field_names()) > 90


def test_all_config_fields_have_production_consumers() -> None:
    from autoskillit.core import paths

    reads = _collect_attribute_reads(paths.pkg_root(), _config_field_names())

    unconsumed = {
        name for name, sites in reads.items() if not sites and name not in _FORWARD_DECLARED
    }
    assert not unconsumed, (
        f"Config fields with zero production read sites "
        f"(add a consumer, retire the key via RETIRED_CONFIG_KEYS, or add to "
        f"_FORWARD_DECLARED as ForwardDeclaredField(issue=NNNN, rationale='...', "
        f"added_date=date(YYYY, M, D))): {sorted(unconsumed)}"
    )


def test_force_claude_agent_teams_inactive_is_consumed() -> None:
    """The field this contract was written for: it must never go dead again."""
    from autoskillit.core import paths

    reads = _collect_attribute_reads(
        paths.pkg_root(), frozenset({"force_claude_agent_teams_inactive"})
    )
    sites = reads["force_claude_agent_teams_inactive"]

    assert "force_claude_agent_teams_inactive" not in _FORWARD_DECLARED
    assert sites, "the agent-teams opt-in must be read by production code"


def test_forward_declared_has_linked_issues() -> None:
    invalid = {
        field: entry.issue for field, entry in _FORWARD_DECLARED.items() if entry.issue <= 0
    }
    assert not invalid, (
        f"_FORWARD_DECLARED entries with invalid issue number (need positive int): {invalid}"
    )


def test_forward_declared_fields_have_no_consumers() -> None:
    """A forward-declared field that gained a consumer must lose its exemption."""
    from autoskillit.core import paths

    reads = _collect_attribute_reads(paths.pkg_root(), _config_field_names())

    stale = {name: sites for name, sites in reads.items() if name in _FORWARD_DECLARED and sites}
    assert not stale, (
        f"_FORWARD_DECLARED entries that now have production consumers "
        f"(remove from _FORWARD_DECLARED and close the tracking issue): {stale}"
    )


def test_forward_declared_fields_exist_on_a_config_dataclass() -> None:
    unknown = frozenset(_FORWARD_DECLARED.keys()) - _config_field_names()
    assert not unknown, f"_FORWARD_DECLARED keys that are not config fields: {sorted(unknown)}"


def test_forward_declared_entries_not_stale() -> None:
    """Time-bomb: a forward declaration older than 180 days needs re-justification."""
    today = date.today()
    stale = [
        f"{name} (added={entry.added_date}, age={(today - entry.added_date).days}d, "
        f"issue=#{entry.issue})"
        for name, entry in _FORWARD_DECLARED.items()
        if (today - entry.added_date).days > _STALENESS_THRESHOLD_DAYS
    ]
    assert not stale, (
        f"_FORWARD_DECLARED entries older than {_STALENESS_THRESHOLD_DAYS} days "
        f"(wire a consumer, retire the key, or update added_date with a fresh "
        f"tracking issue): {stale}"
    )
