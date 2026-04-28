"""Tests verifying import-linter contract documentation.

REQ-ARCH-007: IL-003 must document the pipeline → config exception inline.
"""

from __future__ import annotations

from pathlib import Path


def test_il003_pipeline_config_exception_documented() -> None:
    """REQ-ARCH-007: IL-003 must contain an inline `# EXCEPTION` comment
    explaining why `autoskillit.config` is omitted from its forbidden_modules
    list (pipeline.context holds AutomationConfig as the DI wiring point).
    Validates that the exception is captured in source rather than tribal."""
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    raw = pyproject_path.read_text()
    lines = raw.splitlines()
    in_block = False
    block_lines: list[str] = []
    for line in lines:
        if 'name = "L1 pipeline does not import L2 or L3"' in line:
            in_block = True
        if in_block:
            block_lines.append(line)
            if line.strip().startswith("[[") and len(block_lines) > 1:
                block_lines.pop()
                break
    block = "\n".join(block_lines)
    assert "# EXCEPTION" in block or "# Exception" in block, (
        "IL-003 must inline-document the pipeline → config exception. "
        "Add a `# EXCEPTION: pipeline.context owns AutomationConfig` "
        "comment above forbidden_modules in the IL-003 contract."
    )
    fm_section = block.split("forbidden_modules", 1)[1].split("]", 1)[0]
    assert '"autoskillit.config"' not in fm_section, (
        "IL-003 forbidden_modules must continue to omit autoskillit.config."
    )


def test_il_contract_count_is_guarded() -> None:
    """All 9 IL-* contracts must be present in pyproject.toml.

    Silently removing a contract from pyproject.toml would cause lint-imports
    to stop enforcing that layer boundary with no pytest signal. This test
    catches that drift.

    If you add a new contract: update the expected_count below and add its
    IL-NNN comment tag. If you remove a contract: restore it or obtain explicit
    sign-off and update this test.
    """
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    raw = pyproject_path.read_text()

    expected_count = 9
    actual_count = raw.count("[[tool.importlinter.contracts]]")
    assert actual_count == expected_count, (
        f"Expected {expected_count} importlinter contracts in pyproject.toml, "
        f"found {actual_count}. Update this count when adding/removing contracts."
    )

    expected_ids = [f"IL-{str(i).zfill(3)}" for i in range(1, expected_count + 1)]
    missing = [il_id for il_id in expected_ids if il_id not in raw]
    assert not missing, (
        f"Import-linter contract ID tags missing from pyproject.toml: {missing}. "
        "Each contract block must carry its IL-NNN comment tag."
    )
