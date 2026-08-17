"""Real-contract integration tests for `_check_input_contracts`, parametrized
over every declared path input in `skill_contracts.yaml`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.server._input_contract_test_helpers import (
    _ALL_PATH_INPUT_SPECS,
    _FILE_PATH_LIST_SPECS,
)

if TYPE_CHECKING:
    from typing import Literal

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestInputContractRealContracts:
    """Gate integration tests using real contract declarations."""

    @pytest.mark.parametrize(
        "skill_name,input_name,declared_type",
        _ALL_PATH_INPUT_SPECS,
        ids=[f"{s}-{i}" for s, i, _ in _ALL_PATH_INPUT_SPECS],
    )
    def test_gate_accepts_correct_path_type(
        self, tmp_path, skill_name: str, input_name: str, declared_type: str
    ):
        """Gate must accept the correct filesystem entity for every declared path input."""

        from autoskillit.core import InputSpec
        from autoskillit.server._guards import _check_input_contracts

        narrowed = cast("Literal['file_path', 'directory_path', 'file_path_list']", declared_type)
        if narrowed == "file_path":
            target = tmp_path / "test_input.md"
            target.write_text("test")
            cmd_value = str(target)
        elif narrowed == "directory_path":
            target = tmp_path / "test_input_dir"
            target.mkdir()
            cmd_value = str(target)
        else:
            member_a = tmp_path / "test_input_a.md"
            member_a.write_text("a")
            member_b = tmp_path / "test_input_b.md"
            member_b.write_text("b")
            cmd_value = f"{member_a},{member_b}"
        spec = InputSpec(name=input_name, type=narrowed, required=True, position=0)
        result = _check_input_contracts(
            f"/autoskillit:{skill_name} {cmd_value}",
            str(tmp_path),
            resolver=lambda skill_command, _s=spec: (_s,),
        )
        assert result is None

    @pytest.mark.parametrize(
        "skill_name,input_name,declared_type",
        _ALL_PATH_INPUT_SPECS,
        ids=[f"{s}-{i}" for s, i, _ in _ALL_PATH_INPUT_SPECS],
    )
    def test_gate_rejects_wrong_path_type(
        self, tmp_path, skill_name: str, input_name: str, declared_type: str
    ):
        """Gate must reject the WRONG filesystem entity for every declared path input."""
        import json

        from autoskillit.core import InputSpec
        from autoskillit.server._guards import _check_input_contracts

        narrowed = cast("Literal['file_path', 'directory_path', 'file_path_list']", declared_type)
        if narrowed == "file_path":
            wrong_target = tmp_path / "wrong_dir"
            wrong_target.mkdir()
            cmd_value = str(wrong_target)
        elif narrowed == "directory_path":
            wrong_target = tmp_path / "wrong_file.md"
            wrong_target.write_text("test")
            cmd_value = str(wrong_target)
        else:
            existing = tmp_path / "existing_member.md"
            existing.write_text("ok")
            missing = tmp_path / "missing_member.md"
            cmd_value = f"{existing},{missing}"
        spec = InputSpec(name=input_name, type=narrowed, required=True, position=0)
        result = _check_input_contracts(
            f"/autoskillit:{skill_name} {cmd_value}",
            str(tmp_path),
            resolver=lambda skill_command, _s=spec: (_s,),
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False


class TestFilePathListRealResolver:
    """Real-resolver coverage for declared file_path_list specs."""

    @pytest.mark.parametrize(
        "skill_name,input_name,declared_type",
        _FILE_PATH_LIST_SPECS,
        ids=[f"{s}-{i}" for s, i, _ in _FILE_PATH_LIST_SPECS],
    )
    def test_gate_real_resolver_for_file_path_list_specs(
        self, tmp_path, skill_name: str, input_name: str, declared_type: str
    ) -> None:
        """For every file_path_list spec, the real resolver must validate comma-joined members."""
        from autoskillit.recipe._contracts_manifest import resolve_input_specs
        from autoskillit.server._guards import _check_input_contracts

        assert declared_type == "file_path_list", "parametrize filter precondition"
        specs = resolve_input_specs(f"/autoskillit:{skill_name}")
        target_spec = next((s for s in specs if s.name == input_name), None)
        assert target_spec is not None, (
            f"resolver did not surface {skill_name}.{input_name} (specs={specs!r})"
        )

        cmd_args: list[str] = []
        for spec in specs:
            if spec.position < target_spec.position:
                if spec.type == "file_path":
                    predecessor = tmp_path / f"pre_{spec.name}.md"
                    predecessor.write_text("pre")
                    cmd_args.append(str(predecessor))
                elif spec.type == "directory_path":
                    pred_dir = tmp_path / f"pre_{spec.name}_dir"
                    pred_dir.mkdir()
                    cmd_args.append(str(pred_dir))
                else:
                    a = tmp_path / f"pre_{spec.name}_a.md"
                    a.write_text("a")
                    b = tmp_path / f"pre_{spec.name}_b.md"
                    b.write_text("b")
                    cmd_args.append(f"{a},{b}")
        member_a = tmp_path / f"target_{input_name}_a.md"
        member_a.write_text("a")
        member_b = tmp_path / f"target_{input_name}_b.md"
        member_b.write_text("b")
        cmd_args.append(f"{member_a},{member_b}")

        cmd = f"/autoskillit:{skill_name} {' '.join(cmd_args)}"
        result = _check_input_contracts(cmd, str(tmp_path), resolve_input_specs)
        assert result is None, (
            f"Expected gate acceptance for {skill_name}.{input_name}, got: {result!r}"
        )
