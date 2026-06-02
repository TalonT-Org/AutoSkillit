"""Contract tests: select-vis-lenses SKILL.md experiment type vocabulary and token emission."""

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.small]

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "select-vis-lenses"
    / "SKILL.md"
)
EXPERIMENT_TYPES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "recipes"
    / "experiment-types"
)
CONTRACTS_YAML = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "recipe"
    / "skill_contracts.yaml"
)


def _read_skill() -> str:
    return SKILL_PATH.read_text()


def _extract_lens_table_section(text: str) -> str:
    """Extract the Tier B lens selection table section from SKILL.md."""
    m = re.search(
        r"Experiment-type table[^\n]*\n(\|[^\n]*\n)+",
        text,
        re.IGNORECASE,
    )
    assert m, "Tier B experiment-type table not found in select-vis-lenses SKILL.md"
    return m.group(0).lower()


class TestSelectVisLensesExperimentTypes:
    def test_experiment_types_use_canonical_names(self) -> None:
        """Tier B lens selection table must reference all 12 canonical experiment types."""
        registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
        assert len(registry_types) > 0, "experiment-types registry is empty"

        table_section = _extract_lens_table_section(_read_skill())
        for rtype in registry_types:
            assert rtype in table_section, (
                f"select-vis-lenses Tier B lens selection table does not reference "
                f"canonical experiment type '{rtype}' from the registry"
            )

    def test_experiment_type_table_has_default_row(self) -> None:
        """Tier B table must include a (default) fallback row."""
        table_section = _extract_lens_table_section(_read_skill())
        assert "(default)" in table_section, "select-vis-lenses Tier B table missing (default) row"


class TestSelectVisLensesTokenEmission:
    _EXPECTED_TOKENS = {
        "selected_lenses",
        "lens_context_paths",
        "disambiguation_rule_applied",
        "tier_c_lens",
        "methodology_tradition",
    }
    _PROHIBITED_TOKENS = {
        "visualization_plan_path",
        "report_plan_path",
        "visualization_plan_trace_path",
        "classification_timestamp",
    }

    def test_emits_exactly_five_tokens(self) -> None:
        """Step 3 must emit exactly the five specified tokens — no more, no fewer."""
        content = _read_skill()
        step3_match = re.search(r"### Step 3.*?(?=### Step \d|$)", content, re.DOTALL)
        assert step3_match, "Step 3 section not found in select-vis-lenses SKILL.md"
        step3_text = step3_match.group(0)
        for token in self._EXPECTED_TOKENS:
            assert token in step3_text, (
                f"select-vis-lenses Step 3 missing required token '{token}'"
            )
        token_lines = re.findall(r"^\w[\w_]* =", step3_text, re.MULTILINE)
        assert len(token_lines) == len(self._EXPECTED_TOKENS), (
            f"Step 3 must emit exactly {len(self._EXPECTED_TOKENS)} tokens, "
            f"found {len(token_lines)}: {token_lines}"
        )

    def test_does_not_emit_prohibited_tokens(self) -> None:
        """Step 3 must not emit tokens belonging to downstream skills."""
        content = _read_skill()
        step3_match = re.search(r"### Step 3.*?(?=### Step \d|$)", content, re.DOTALL)
        assert step3_match, "Step 3 section not found"
        step3_text = step3_match.group(0)
        for token in self._PROHIBITED_TOKENS:
            assert f"{token} =" not in step3_text, (
                f"select-vis-lenses Step 3 must not emit '{token}' (belongs to downstream skill)"
            )


class TestSelectVisLensesOutputDirectory:
    def test_output_dir_uses_select_vis_lenses(self) -> None:
        """All output paths must reference select-vis-lenses/, not plan-visualization/."""
        content = _read_skill()
        assert "select-vis-lenses/" in content
        temp_refs = re.findall(r"\{\{AUTOSKILLIT_TEMP\}\}/([a-z0-9-]+)/", content)
        for ref in temp_refs:
            assert ref == "select-vis-lenses", (
                f"SKILL.md references '{{{{AUTOSKILLIT_TEMP}}}}/{ref}/' "
                f"but should only reference select-vis-lenses/"
            )


def test_skill_path_exists() -> None:
    """select-vis-lenses/SKILL.md must exist at the expected path."""
    assert SKILL_PATH.exists(), f"Expected SKILL.md at {SKILL_PATH}"


def test_tier_b_experiment_type_table_section_present() -> None:
    """SKILL.md must contain the Tier B experiment-type lookup table heading and rows."""
    table_text = _extract_lens_table_section(SKILL_PATH.read_text())
    data_rows = [
        ln
        for ln in table_text.splitlines()
        if ln.strip().startswith("|") and "---" not in ln and "experiment" not in ln.lower()
    ]
    assert len(data_rows) >= 1, "Tier B experiment-type table must contain at least one data row"


def test_required_output_tokens_present() -> None:
    """SKILL.md must mention all five structured output tokens."""
    text = SKILL_PATH.read_text()
    for token in (
        "selected_lenses",
        "lens_context_paths",
        "disambiguation_rule_applied",
        "tier_c_lens",
        "methodology_tradition",
    ):
        assert token in text, f"select-vis-lenses SKILL.md missing required output token '{token}'"


def test_write_behavior_always_declared() -> None:
    """skill_contracts.yaml must declare write_behavior='always' for select-vis-lenses."""
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"].get("select-vis-lenses")
    assert entry is not None, "select-vis-lenses not found in skill_contracts.yaml"
    assert entry.get("write_behavior") == "always", (
        f"Expected write_behavior='always', got '{entry.get('write_behavior')}'"
    )
