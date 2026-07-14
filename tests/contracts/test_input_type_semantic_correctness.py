"""Cross-validate skill_contracts.yaml path input types against SKILL.md content."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"

# Documented-name aliases for contract inputs whose SKILL.md argument name differs
# from the contract input name. The classifier searches both the contract input name
# and any documented alias so SKILL.md language is matched correctly.
_CONTRACT_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("audit-impl", "all_plan_paths"): ("plans_input",),
    ("audit-impl", "all_conflict_report_paths"): ("conflict_report_paths",),
    ("open-integration-pr", "conflict_report_paths"): ("conflict_report_paths",),
    ("prepare-pr", "plan_paths"): ("plan_paths",),
}


def _aliases_for(skill_name: str, input_name: str) -> tuple[str, ...]:
    return _CONTRACT_ALIASES.get((skill_name, input_name), ())


_LIST_SHAPE_SUFFIXES = ("_paths", "_path_list")
_LIST_SHAPE_PREFIXES = ("all_",)


def _is_list_shaped_name(name: str) -> bool:
    return name.startswith(_LIST_SHAPE_PREFIXES) or name.endswith(_LIST_SHAPE_SUFFIXES)


_LIST_COLLECTION_RE = re.compile(
    r"\b("
    r"comma[\s-]+separated|comma[\s-]+joined|comma[\s-]+joined|list\s+of|"
    r"multiple|accumulated|each\s+path|one\s+per|separated\s+by|"
    r"paths?\s+are\s+supplied|paths?\s+passed\s+in"
    r")\b",
    re.IGNORECASE,
)


def _infer_path_kind_from_skillmd(
    input_name: str,
    skillmd_text: str,
    *,
    aliases: tuple[str, ...] = (),
) -> Literal["file", "directory", "list", "ambiguous"]:
    """Classify the path kind of a contract input from SKILL.md prose.

    Returns ``"list"`` when the contract input name is list-shaped
    (``all_`` prefix or ``_paths``/``_path_list`` suffix) and the SKILL.md
    mention windows around the contract name and any aliases contain collection
    language such as ``comma-separated`` or ``list of``. Falls through to the
    existing directory/file inference otherwise.
    """
    if _is_list_shaped_name(input_name):
        search_names = (input_name, *aliases)
        placeholder_variants = [f"{{{n}}}" for n in search_names]
        mentions: list[int] = []
        for name in search_names:
            mentions.extend(
                m.start() for m in re.finditer(re.escape(name), skillmd_text, re.IGNORECASE)
            )
        for placeholder in placeholder_variants:
            mentions.extend(
                m.start() for m in re.finditer(re.escape(placeholder), skillmd_text, re.IGNORECASE)
            )
        if mentions:
            for pos in mentions:
                window = skillmd_text[max(0, pos - 200) : pos + 200]
                if _LIST_COLLECTION_RE.search(window):
                    return "list"

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
            if inp.get("type") in ("file_path", "directory_path", "file_path_list"):
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


def _declared_type_to_inferred(declared_type: str) -> str:
    return {
        "file_path": "file",
        "directory_path": "directory",
        "file_path_list": "list",
    }[declared_type]


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

    aliases = _aliases_for(skill_name, input_name)
    inferred = _infer_path_kind_from_skillmd(input_name, skillmd, aliases=aliases)
    if inferred == "ambiguous":
        # Ambiguous scalar inference is tolerable; ambiguous list inference on a
        # declared list input is not — covered by
        # test_file_path_list_yaml_entries_have_list_inference.
        if declared_type == "file_path_list":
            pytest.fail(
                f"{skill_name}.{input_name}: declared as file_path_list but classifier "
                f"could not infer 'list' from SKILL.md"
            )
        pytest.skip(f"Ambiguous inference for {skill_name}.{input_name}")

    expected_type = (
        declared_type
        if inferred == _declared_type_to_inferred(declared_type)
        else (
            "file_path_list"
            if inferred == "list"
            else "file_path"
            if inferred == "file"
            else "directory_path"
        )
    )
    assert declared_type == expected_type, (
        f"{skill_name}.{input_name}: declared as {declared_type!r} in skill_contracts.yaml "
        f"but SKILL.md indicates it should be {expected_type!r}"
    )


@pytest.mark.parametrize(
    "skill_name,input_name,declared_type",
    [(s, i, t) for (s, i, t) in _PATH_INPUT_PARAMS if t == "file_path_list"],
    ids=[f"{s}-{i}" for (s, i, t) in _PATH_INPUT_PARAMS if t == "file_path_list"],
)
def test_file_path_list_yaml_entries_have_list_inference(
    skill_name: str, input_name: str, declared_type: str
) -> None:
    """Every declared file_path_list input must infer 'list' from its SKILL.md."""
    assert declared_type == "file_path_list", "parametrize filter precondition"
    skillmd = _resolve_skillmd(skill_name)
    if skillmd is None:
        pytest.skip(f"No SKILL.md found for {skill_name}")

    aliases = _aliases_for(skill_name, input_name)
    inferred = _infer_path_kind_from_skillmd(input_name, skillmd, aliases=aliases)
    assert inferred == "list", (
        f"{skill_name}.{input_name}: declared as file_path_list but classifier "
        f"inferred {inferred!r} from SKILL.md (aliases={aliases!r})"
    )


def test_collect_path_input_params_includes_file_path_list() -> None:
    """_collect_path_input_params() must surface file_path_list inputs and be nonempty."""
    params = _collect_path_input_params()
    list_types = [t for (_, _, t) in params if t == "file_path_list"]
    assert list_types, "Expected at least one file_path_list entry in the YAML manifest"
    assert len(list_types) >= 4, (
        f"Expected at least four list inputs after Step 2/3 contract changes "
        f"(audit-impl.all_plan_paths, audit-impl.all_conflict_report_paths, "
        f"prepare-pr.plan_paths, open-integration-pr.conflict_report_paths); "
        f"got {len(list_types)}"
    )


@pytest.mark.parametrize(
    "skill_name,input_name,aliases",
    [(s, i, a) for (s, i), a in _CONTRACT_ALIASES.items()],
    ids=[f"{s}-{i}" for (s, i) in _CONTRACT_ALIASES],
)
def test_contract_alias_is_mentioned_in_skillmd(
    skill_name: str, input_name: str, aliases: tuple[str, ...]
) -> None:
    """Every configured alias must actually appear in the corresponding SKILL.md."""
    skillmd = _resolve_skillmd(skill_name)
    if skillmd is None:
        pytest.skip(f"No SKILL.md found for {skill_name}")

    for alias in aliases:
        assert re.search(re.escape(alias), skillmd, re.IGNORECASE), (
            f"Alias {alias!r} for {skill_name}.{input_name} not found in SKILL.md — "
            f"remove the alias entry or fix the SKILL.md prose"
        )


@pytest.mark.parametrize(
    "input_name,skillmd_text,expected",
    [
        (
            "all_plan_paths",
            "A comma-separated list of `.md` plan file paths. Pass them as one positional.",
            "list",
        ),
        (
            "all_conflict_report_paths",
            "Comma-joined absolute paths to conflict resolution reports.",
            "list",
        ),
        (
            "conflict_report_paths",
            "Comma-separated list of absolute paths to conflict resolution report files.",
            "list",
        ),
        (
            "all_diagram_paths",
            "Comma-separated diagram paths produced by arch-lens skills.",
            "list",
        ),
        (
            "report_paths",
            "Multiple accumulated paths accumulated over the run.",
            "list",
        ),
    ],
)
def test_list_bucket_inference_unit_cases(
    input_name: str, skillmd_text: str, expected: str
) -> None:
    """Unit cases for the semantic classifier's 'list' bucket."""
    inferred = _infer_path_kind_from_skillmd(input_name, skillmd_text)
    assert inferred == expected
