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
