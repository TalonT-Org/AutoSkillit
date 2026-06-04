"""Cross-validate skill_contracts.yaml path input types against SKILL.md content."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"


def _infer_path_kind_from_skillmd(
    input_name: str, skillmd_text: str
) -> Literal["file", "directory", "ambiguous"]:
    if input_name.endswith(("_dir", "_directory")) or input_name == "run_dir":
        return "directory"
    if "worktree" in input_name or input_name == "workspace":
        return "directory"
    if input_name.endswith("_path") and not any(
        kw in input_name for kw in ("dir", "folder", "workspace")
    ):
        return "file"

    placeholder = f"{{{input_name}}}"
    mentions = [m.start() for m in re.finditer(re.escape(input_name), skillmd_text, re.IGNORECASE)]
    mentions.extend(
        m.start() for m in re.finditer(re.escape(placeholder), skillmd_text, re.IGNORECASE)
    )
    if not mentions:
        return "ambiguous"

    dir_re = re.compile(
        r"\b(directory|directories|folder|glob\b.*inside|files\s+in\s+the|files\s+under)\b",
        re.IGNORECASE,
    )
    file_re = re.compile(
        r"\b(read\s+the\s+file|parse\s+the\s+file|load.*\bfile\b|\.md\b|\.yaml\b|\.json\b)\b",
        re.IGNORECASE,
    )

    dir_count = 0
    file_count = 0
    for pos in mentions:
        window = skillmd_text[max(0, pos - 200) : pos + 200]
        dir_count += len(dir_re.findall(window))
        file_count += len(file_re.findall(window))

    if dir_count > 0 and file_count == 0:
        return "directory"
    if file_count > 0 and dir_count == 0:
        return "file"
    return "ambiguous"


def _collect_path_input_params() -> list[tuple[str, str, str]]:
    raw = load_yaml(_CONTRACTS_YAML)
    skills = raw.get("skills", {})
    result = []
    for skill_name, contract in sorted(skills.items()):
        for inp in contract.get("inputs", []):
            if inp.get("type") in ("file_path", "directory_path"):
                result.append((skill_name, inp["name"], inp["type"]))
    return result


_PATH_INPUT_PARAMS = _collect_path_input_params()


def _resolve_skillmd(skill_name: str) -> str | None:
    from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir

    for base in (bundled_skills_dir(), bundled_skills_extended_dir()):
        md = base / skill_name / "SKILL.md"
        if md.is_file():
            return md.read_text()
    return None


@pytest.mark.parametrize(
    "skill_name,input_name,declared_type",
    _PATH_INPUT_PARAMS,
    ids=[f"{s}-{i}" for s, i, _ in _PATH_INPUT_PARAMS],
)
def test_input_type_matches_skillmd(skill_name: str, input_name: str, declared_type: str) -> None:
    """Declared input type in skill_contracts.yaml must match SKILL.md intent."""
    skillmd = _resolve_skillmd(skill_name)
    if skillmd is None:
        pytest.skip(f"No SKILL.md found for {skill_name}")

    inferred = _infer_path_kind_from_skillmd(input_name, skillmd)
    if inferred == "ambiguous":
        pytest.skip(f"Ambiguous inference for {skill_name}.{input_name}")

    expected_type = "file_path" if inferred == "file" else "directory_path"
    assert declared_type == expected_type, (
        f"{skill_name}.{input_name}: declared as {declared_type!r} in skill_contracts.yaml "
        f"but SKILL.md indicates it should be {expected_type!r}"
    )
