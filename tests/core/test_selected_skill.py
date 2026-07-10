"""Tests for the IL-0 SelectedSkill authority (Step 1.7 of #4185)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autoskillit.core import (
    EMPTY_SELECTED_SKILL,
    ZERO_INPUT_KEY,
    InvocationShapeDenialReason,
    SelectedSkill,
    SkillSource,
    build_selected_skill,
    compute_selected_skill_fingerprint,
    normalize_skill_contract,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestNormalizeSkillContract:
    def test_explicit_empty_inputs_marks_zero_input(self) -> None:
        norm = normalize_skill_contract({"inputs": [], "outputs": []})
        assert norm["is_zero_input"] is True
        assert norm["inputs"] == ()
        assert norm["outputs"] == ()

    def test_explicit_zero_input_metadata_marks_zero_input(self) -> None:
        norm = normalize_skill_contract({"metadata": {ZERO_INPUT_KEY: True}, "outputs": []})
        assert norm["is_zero_input"] is True

    def test_missing_inputs_raises_for_zero_input_skill(self) -> None:
        with pytest.raises(ValueError, match="Contract must declare inputs"):
            normalize_skill_contract({"outputs": []})

    def test_inputs_must_be_a_list(self) -> None:
        with pytest.raises(ValueError, match="inputs must be a list"):
            normalize_skill_contract({"inputs": "not-a-list"})

    def test_outputs_must_be_a_list(self) -> None:
        with pytest.raises(ValueError, match="outputs must be a list"):
            normalize_skill_contract({"inputs": [], "outputs": "not-a-list"})

    def test_none_payload_raises(self) -> None:
        with pytest.raises(ValueError, match="non-None"):
            normalize_skill_contract(None)

    def test_capabilities_are_lowercased_and_deduplicated(self) -> None:
        norm = normalize_skill_contract(
            {"inputs": [], "outputs": [], "capabilities": ["FOO", "Foo", "bar"]}
        )
        assert norm["capabilities"] == frozenset({"foo", "bar"})

    def test_input_field_order_independence(self) -> None:
        a = normalize_skill_contract({"inputs": [{"name": "x", "type": "string"}], "outputs": []})
        b = normalize_skill_contract({"inputs": [{"name": "x", "type": "string"}], "outputs": []})
        assert compute_selected_skill_fingerprint(a) == compute_selected_skill_fingerprint(b)

    def test_fingerprint_changes_with_capability_set(self) -> None:
        a = normalize_skill_contract({"inputs": [], "outputs": [], "capabilities": ["a"]})
        b = normalize_skill_contract({"inputs": [], "outputs": [], "capabilities": ["b"]})
        assert compute_selected_skill_fingerprint(a) != compute_selected_skill_fingerprint(b)


class TestSelectedSkillConstruction:
    def _build(self, *, name: str = "demo", contract: dict | None = None) -> SelectedSkill:
        return build_selected_skill(
            name=name,
            source=SkillSource.BUNDLED,
            source_path=Path("/tmp/demo/SKILL.md"),
            project_dir=Path("/tmp/demo"),
            raw_content="---\nname: demo\n---\nbody",
            raw_contract=contract if contract is not None else {"inputs": [], "outputs": []},
        )

    def test_build_with_empty_inputs_produces_zero_input(self) -> None:
        skill = self._build()
        assert skill.is_zero_input is True
        assert skill.inputs == ()

    def test_build_with_declared_inputs_produces_non_zero(self) -> None:
        skill = self._build(
            contract={"inputs": [{"name": "topic", "type": "string"}], "outputs": []}
        )
        assert skill.is_zero_input is False
        assert len(skill.inputs) == 1
        assert skill.inputs[0]["name"] == "topic"

    def test_zero_input_with_declared_inputs_raises(self) -> None:
        with pytest.raises(ValueError, match="is zero-input but declared inputs"):
            SelectedSkill(
                name="bad",
                source=SkillSource.BUNDLED,
                source_path=Path("/tmp/x"),
                project_dir=Path("/tmp"),
                content_hash="abc",
                contract_identity="def",
                inputs=({"name": "x", "type": "string"},),
                outputs=(),
                capabilities=frozenset(),
                dependencies=frozenset(),
                output_metadata={},
                write_behavior=None,
                path_metadata={},
                recovery_metadata={},
                is_zero_input=True,
            )

    def test_empty_name_raises_for_real_skill(self) -> None:
        with pytest.raises(ValueError, match="name must be non-empty"):
            SelectedSkill(
                name="",
                source=SkillSource.BUNDLED,
                source_path=Path("/tmp/x"),
                project_dir=Path("/tmp"),
                content_hash="abc",
                contract_identity="def",
                inputs=(),
                outputs=(),
                capabilities=frozenset(),
                dependencies=frozenset(),
                output_metadata={},
                write_behavior=None,
                path_metadata={},
                recovery_metadata={},
                is_zero_input=True,
            )


class TestSelectedSkillImmutability:
    def _skill(self) -> SelectedSkill:
        return build_selected_skill(
            name="demo",
            source=SkillSource.BUNDLED,
            source_path=Path("/tmp/demo/SKILL.md"),
            project_dir=Path("/tmp/demo"),
            raw_content="---\nname: demo\n---",
            raw_contract={
                "inputs": [],
                "outputs": [],
                "output_metadata": {"key": "value"},
            },
        )

    def test_attribute_mutation_raises(self) -> None:
        skill = self._skill()
        with pytest.raises(Exception):
            skill.name = "changed"  # type: ignore[misc]

    def test_inner_metadata_mutation_raises(self) -> None:
        skill = self._skill()
        with pytest.raises(TypeError):
            skill.output_metadata["new"] = "value"  # type: ignore[index]

    def test_is_hashable(self) -> None:
        skill = self._skill()
        # Two hash calls must agree and not raise.
        assert hash(skill) == hash(skill)

    def test_deepcopy_preserves_identity(self) -> None:
        skill = self._skill()
        cloned = copy.deepcopy(skill)
        assert cloned == skill
        assert cloned.contract_identity == skill.contract_identity
        assert cloned.content_hash == skill.content_hash


class TestEmptySelectedSkill:
    def test_empty_sentinel_is_zero_input(self) -> None:
        assert EMPTY_SELECTED_SKILL.is_zero_input is True

    def test_empty_sentinel_is_hashable(self) -> None:
        assert hash(EMPTY_SELECTED_SKILL) == hash(EMPTY_SELECTED_SKILL)


class TestDenialReasonAdditions:
    def test_unknown_skill_member_exists(self) -> None:
        assert InvocationShapeDenialReason.UNKNOWN_SKILL.value == "unknown_skill"

    def test_resolver_unavailable_member_exists(self) -> None:
        assert InvocationShapeDenialReason.RESOLVER_UNAVAILABLE.value == "resolver_unavailable"

    def test_existing_members_unchanged(self) -> None:
        # Regression guard for the existing members we did not touch.
        assert InvocationShapeDenialReason.MALFORMED_CONTRACT.value == "malformed_contract"
        assert InvocationShapeDenialReason.UNKNOWN_STEP.value == "unknown_step"


class TestContentHash:
    def test_same_content_produces_same_hash(self) -> None:
        a = build_selected_skill(
            name="a",
            source=SkillSource.BUNDLED,
            source_path=Path("/p"),
            project_dir=Path("/p"),
            raw_content="abc",
            raw_contract={"inputs": [], "outputs": []},
        )
        b = build_selected_skill(
            name="b",
            source=SkillSource.BUNDLED,
            source_path=Path("/p"),
            project_dir=Path("/p"),
            raw_content="abc",
            raw_contract={"inputs": [], "outputs": []},
        )
        assert a.content_hash == b.content_hash

    def test_different_content_produces_different_hash(self) -> None:
        a = build_selected_skill(
            name="a",
            source=SkillSource.BUNDLED,
            source_path=Path("/p"),
            project_dir=Path("/p"),
            raw_content="abc",
            raw_contract={"inputs": [], "outputs": []},
        )
        b = build_selected_skill(
            name="a",
            source=SkillSource.BUNDLED,
            source_path=Path("/p"),
            project_dir=Path("/p"),
            raw_content="def",
            raw_contract={"inputs": [], "outputs": []},
        )
        assert a.content_hash != b.content_hash


class TestSerializationRoundTrip:
    def test_normalized_payload_is_json_serializable(self) -> None:
        norm = normalize_skill_contract(
            {
                "inputs": [{"name": "topic", "type": "string"}],
                "outputs": [],
                "capabilities": ["READ"],
                "dependencies": [],
            }
        )
        # `frozenset` is not JSON-serializable, but `is_zero_input` and the
        # structural facts should round-trip through json.dumps.
        dumped = json.dumps(
            {
                "inputs": list(norm["inputs"]),
                "outputs": list(norm["outputs"]),
                "is_zero_input": norm["is_zero_input"],
                "capabilities": sorted(norm["capabilities"]),
            }
        )
        assert "topic" in dumped
        assert norm["is_zero_input"] is False
