"""Contract: config dataclass fields have a production consumer (#4684 Fix C).

Generalizes the ``inert-tracked:#NNNN`` discipline documented in
tests/AGENTS.md § run_skill Parameter-Role Ledgers (precedent:
tests/contracts/test_recipe_step_field_ledger.py, which applies it to
``RecipeStep`` fields) to config dataclass fields. A field is "live" iff
either (a) some production module outside the config/ tree
reads ``.<field_name>`` directly, (b) a method defined on the same
dataclass reads ``self.<field_name>`` and that method itself has an
external call site (indirect consumption — e.g. ``GitHubConfig.
check_label_allowed`` reads ``self.allowed_labels`` and is called from
``tools_issue_labels.py`` et al.), or (c) the field's doc-comment carries
an ``inert-tracked:#NNNN`` annotation citing an open issue.

``__post_init__`` is excluded from (b): it is auto-invoked at construction
for every instance regardless of whether the field's *value* ever reaches
real behavior, so a field read only inside its own dataclass's
``__post_init__`` (typically a self-consistency check against a sibling
field) does not count as "consumed" — see
``RunSkillConfig.natural_exit_grace_seconds``, which validates itself
against ``exit_after_stop_delay_ms`` in ``__post_init__`` but is never
threaded into ``execution/process/__init__.py``'s
``natural_exit_grace_seconds`` parameter at either real call site.

Distinct from tests/contracts/test_config_field_coverage.py, which checks a
different thing (REQ-CONFIG-001: every dataclass field is populated by
_build_subconfig) — a field can be populated and still have zero readers.

Scope: enforces every ``@dataclass``/``@dataclass(frozen=True, slots=True)``
defined in any of the owner-bounded ``config/_dataclasses_<concern>.py``
modules. Reflectively discovered by iterating those modules' ``__all__``
(not by walking the ``_config_dataclasses`` facade, which only re-exports
symbols without retaining their ``__module__`` provenance).
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_CONFIG_DIR = _SRC_ROOT / "config"
_INERT_TRACKED_RE = re.compile(r"inert-tracked:#[1-9]\d*")

# Every owner-bounded dataclass module produced by the config decomposition
# (#4859). Each is its own file and defines dataclasses that this contract
# enforces; the ``_config_dataclasses`` facade re-exports them but does NOT
# own their definitions, so reflective discovery must walk the leaf modules.
_DATACLASS_MODULES: tuple[str, ...] = (
    "autoskillit.config._dataclasses_errors",
    "autoskillit.config._dataclasses_test_gating",
    "autoskillit.config._dataclasses_execution",
    "autoskillit.config._dataclasses_workflow",
    "autoskillit.config._dataclasses_diagnostics",
    "autoskillit.config._dataclasses_github",
    "autoskillit.config._dataclasses_surfaces",
    "autoskillit.config._dataclasses_fleet",
    "autoskillit.config._dataclasses_providers",
)


def _iter_enforced_dataclasses() -> tuple[type, ...]:
    """Reflectively discover every enforced dataclass.

    A dataclass imported into this module from elsewhere would not satisfy
    ``__module__`` equality below and is correctly excluded (its fields are
    that other module's responsibility).
    """
    seen: set[type] = set()
    for module_path in _DATACLASS_MODULES:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        for name in getattr(mod, "__all__", ()) or vars(mod):
            obj = getattr(mod, name, None)
            if (
                isinstance(obj, type)
                and dataclasses.is_dataclass(obj)
                and obj.__module__ == module_path
                and obj not in seen
            ):
                seen.add(obj)
    return tuple(seen)


_ENFORCED_DATACLASSES = _iter_enforced_dataclasses()

# Map cls -> defining module file path. Recomputed once per test run.
_DEFINING_FILES: dict[type, Path] = {
    cls: _CONFIG_DIR / (cls.__module__.rsplit(".", 1)[-1] + ".py") for cls in _ENFORCED_DATACLASSES
}

# Cache of all non-config-dataclass .py file contents, read once per test run.
# We exclude every owner-bounded dataclass module + the facades so the "direct
# reader" detector only matches true production consumers (the composition root
# ``_automation_config.py`` is INCLUDED — its ``skill_visibility_spec`` is a
# production reader of SkillsConfig.tier1/2/3, and ``from_dynaconf`` is a
# production reader of every section's fields).
_OTHER_SRC_TEXT: dict[Path, str] = {
    p: p.read_text(encoding="utf-8")
    for p in _SRC_ROOT.rglob("*.py")
    if p not in _DEFINING_FILES.values()
    and p != _SRC_ROOT / "config" / "_config_dataclasses.py"
    and p != _SRC_ROOT / "config" / "settings.py"
}


def _defining_file(cls: type) -> Path:
    """Return the file that defines ``cls``. Falls back to a stable lookup
    based on ``cls.__module__`` if the class wasn't pre-registered (e.g.
    added after this module loaded — keep the lookup dynamic)."""
    if cls in _DEFINING_FILES:
        return _DEFINING_FILES[cls]
    return _CONFIG_DIR / (cls.__module__.rsplit(".", 1)[-1] + ".py")


def _dataclass_field_names(cls: type) -> list[str]:
    # dataclasses.fields() (unlike raw __dataclass_fields__) correctly excludes
    # ClassVar-annotated pseudo-fields, e.g. RunSkillConfig._EXIT_GRACE_BUFFER_MS.
    return [f.name for f in dataclasses.fields(cls)]


def _field_source_comment(field_name: str, cls: type) -> str:
    """Return the doc-comment line(s) immediately preceding a field's declaration
    within ``cls``'s own body.

    Config dataclass fields document intent with a ``#``-comment block above
    the field, not an inline trailing comment (see force_inactive_agent_teams
    for the pattern) — so this walks backward from the field's assignment
    line collecting contiguous ``#`` lines. Scoped to ``cls``'s ast.ClassDef
    line range (via ``_class_node``), so a same-named field on an earlier
    class in the file cannot be misattributed to a later class's field of
    the same name.
    """
    class_node = _class_node(cls)
    if class_node is None:
        return ""
    lines = _defining_file(cls).read_text(encoding="utf-8").splitlines()
    field_pattern = re.compile(rf"^\s{{4}}{re.escape(field_name)}\s*:")
    start = class_node.lineno - 1
    end = class_node.end_lineno or len(lines)
    for i in range(start, end):
        if field_pattern.match(lines[i]):
            comment_lines: list[str] = []
            j = i - 1
            while j >= start and lines[j].strip().startswith("#"):
                comment_lines.append(lines[j])
                j -= 1
            return "\n".join(reversed(comment_lines))
    return ""


def _field_is_inert_tracked(field_name: str, cls: type) -> bool:
    return bool(_INERT_TRACKED_RE.search(_field_source_comment(field_name, cls)))


def _has_direct_reader(field_name: str) -> bool:
    """True iff some non-config module accesses ``.<field_name>``."""
    pattern = re.compile(rf"\.{re.escape(field_name)}\b")
    return any(pattern.search(text) for text in _OTHER_SRC_TEXT.values())


def _class_node(cls: type) -> ast.ClassDef | None:
    source = _defining_file(cls).read_text(encoding="utf-8")
    tree = ast.parse(source)
    return next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls.__name__),
        None,
    )


def _has_indirect_method_reader(field_name: str, cls: type) -> bool:
    """True iff a non-``__post_init__`` method on ``cls`` reads
    ``self.<field_name>`` and that method has an external call site."""
    class_node = _class_node(cls)
    if class_node is None:
        return False
    source = _defining_file(cls).read_text(encoding="utf-8")
    self_pattern = re.compile(rf"self\.{re.escape(field_name)}\b")
    for item in class_node.body:
        if not isinstance(item, ast.FunctionDef) or item.name == "__post_init__":
            continue
        method_source = ast.get_source_segment(source, item) or ""
        if not self_pattern.search(method_source):
            continue
        call_pattern = re.compile(rf"\.{re.escape(item.name)}\(")
        if any(call_pattern.search(text) for text in _OTHER_SRC_TEXT.values()):
            return True
    return False


def _field_has_consumer(field_name: str, cls: type) -> bool:
    return _has_direct_reader(field_name) or _has_indirect_method_reader(field_name, cls)


def test_inert_tracked_regex_matches_comment_block_shapes() -> None:
    """Verify _INERT_TRACKED_RE against representative matching/non-matching text.

    Only validates the regex itself; the escape-hatch and consumer-detection
    functions (_field_has_consumer, _field_is_inert_tracked) are exercised
    against real fields by
    test_allowed_labels_is_consumed_indirectly_via_a_dataclass_method below.
    """
    comment_block = "# Deliberately unread pending #99999.\n# inert-tracked:#99999"
    assert _INERT_TRACKED_RE.search(comment_block) is not None
    assert _INERT_TRACKED_RE.search("# no marker here") is None


def test_allowed_labels_is_consumed_indirectly_via_a_dataclass_method() -> None:
    """Positive control for the two-hop detector: allowed_labels has no
    external `.allowed_labels` access, only external calls to
    check_label_allowed(...)/check_labels_allowed(...), which read
    self.allowed_labels internally."""
    from autoskillit.config._config_dataclasses import GitHubConfig

    assert not _has_direct_reader("allowed_labels")
    assert _has_indirect_method_reader("allowed_labels", GitHubConfig)


def test_every_config_dataclass_field_has_a_consumer() -> None:
    violations: list[str] = []
    for cls in _ENFORCED_DATACLASSES:
        for field_name in _dataclass_field_names(cls):
            if _field_has_consumer(field_name, cls):
                continue
            if _field_is_inert_tracked(field_name, cls):
                continue
            violations.append(f"{cls.__name__}.{field_name}")
    assert not violations, (
        "Config field(s) with no discoverable production reader (direct or via "
        "an externally-called method on the same dataclass) and no "
        f"inert-tracked:#NNNN annotation: {violations}. Either wire a consumer "
        "or add an `inert-tracked:#NNNN` comment line citing an open issue."
    )
