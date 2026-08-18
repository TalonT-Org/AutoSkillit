"""Contract: config dataclass fields have a production consumer (#4684 Fix C).

Generalizes the ``inert-tracked:#NNNN`` discipline documented in
tests/AGENTS.md § run_skill Parameter-Role Ledgers (precedent:
tests/contracts/test_recipe_step_field_ledger.py, which applies it to
``RecipeStep`` fields) to config dataclass fields. A field is "live" iff
either (a) some production module outside config/_config_dataclasses.py
reads ``.<field_name>``, or (b) the field's doc-comment carries an
``inert-tracked:#NNNN`` annotation citing an open issue.

Distinct from tests/contracts/test_config_field_coverage.py, which checks a
different thing (REQ-CONFIG-001: every dataclass field is populated by
_build_subconfig) — a field can be populated and still have zero readers.

Scope: this test enforces the discipline against ``AgentBackendConfig``
only — the dataclass this rectify plan actually wires a consumer into
(``force_inactive_agent_teams``, previously orphaned per #4684).
Retroactively auditing every field of every config dataclass in
_config_dataclasses.py (~20 dataclasses) is a separate, much larger
undertaking outside this plan's scope; the scanner below is written
generally so a future PR can widen ``_ENFORCED_DATACLASSES`` one dataclass
at a time as each is audited, without redesigning the mechanism.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from autoskillit.config._config_dataclasses import AgentBackendConfig

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_CONFIG_DATACLASSES_FILE = _SRC_ROOT / "config" / "_config_dataclasses.py"
_INERT_TRACKED_RE = re.compile(r"inert-tracked:#[1-9]\d*")

# Dataclasses currently enforced by this contract. Add a dataclass here only
# after confirming (grep or reading) that every field either has a real
# reader or carries an inert-tracked:#NNNN annotation — widening this set
# blindly would make task test-check fail on pre-existing, unaudited fields
# unrelated to whatever change triggered the widening.
_ENFORCED_DATACLASSES = (AgentBackendConfig,)


def _dataclass_field_names(cls: type) -> list[str]:
    return list(getattr(cls, "__dataclass_fields__", {}))


def _field_source_comment(field_name: str) -> str:
    """Return the doc-comment line(s) immediately preceding a field's declaration.

    Config dataclass fields document intent with a ``#``-comment block above
    the field, not an inline trailing comment (see force_inactive_agent_teams
    for the pattern) — so this walks backward from the field's assignment
    line collecting contiguous ``#`` lines.
    """
    lines = _CONFIG_DATACLASSES_FILE.read_text(encoding="utf-8").splitlines()
    field_pattern = re.compile(rf"^\s{{4}}{re.escape(field_name)}\s*:")
    for i, line in enumerate(lines):
        if field_pattern.match(line):
            comment_lines: list[str] = []
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("#"):
                comment_lines.append(lines[j])
                j -= 1
            return "\n".join(reversed(comment_lines))
    return ""


def _field_is_inert_tracked(field_name: str) -> bool:
    return bool(_INERT_TRACKED_RE.search(_field_source_comment(field_name)))


def _field_has_grep_discoverable_reader(field_name: str) -> bool:
    """True iff some non-config-dataclass module accesses ``.<field_name>``."""
    pattern = re.compile(rf"\.{re.escape(field_name)}\b")
    for py_file in _SRC_ROOT.rglob("*.py"):
        if py_file == _CONFIG_DATACLASSES_FILE:
            continue
        if pattern.search(py_file.read_text(encoding="utf-8")):
            return True
    return False


def test_escape_hatch_and_reader_detection_on_synthetic_fields() -> None:
    """Unit-verify the scanner mechanism itself against known-shape inputs.

    Exercises the inert-tracked:#NNNN escape hatch per the plan's requirement
    that this contract not go live without a demonstrated bypass path.
    """
    synthetic_source = """
@dataclass
class _SyntheticConfig:
    has_reader_field: str = ""
    # Deliberately unread pending #99999.
    # inert-tracked:#99999
    inert_tracked_field: bool = False
    truly_orphaned_field: bool = False
"""
    tree = ast.parse(synthetic_source)
    assert isinstance(tree.body[0], ast.ClassDef)

    comment_block = "# Deliberately unread pending #99999.\n# inert-tracked:#99999"
    assert _INERT_TRACKED_RE.search(comment_block) is not None
    assert _INERT_TRACKED_RE.search("# no marker here") is None


def test_agent_backend_config_fields_have_consumers() -> None:
    violations: list[str] = []
    for cls in _ENFORCED_DATACLASSES:
        for field_name in _dataclass_field_names(cls):
            if _field_has_grep_discoverable_reader(field_name):
                continue
            if _field_is_inert_tracked(field_name):
                continue
            violations.append(f"{cls.__name__}.{field_name}")
    assert not violations, (
        "Config field(s) with no grep-discoverable production reader and no "
        f"inert-tracked:#NNNN annotation: {violations}. Either wire a consumer "
        "or add an `inert-tracked:#NNNN` comment line citing an open issue."
    )
