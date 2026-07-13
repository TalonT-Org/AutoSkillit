"""Tests for ClosureAuthoritySpec dataclass validation."""

from __future__ import annotations

import pytest

from autoskillit.core.types import (
    ClosureAuthoritySpec,
    closure_authority_spec_from_args,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


_GOOD_HASH = "sha256:" + "a" * 64


class TestClosureAuthoritySpec:
    def test_requires_both_path_and_hash(self) -> None:
        with pytest.raises(ValueError, match="authority_path"):
            ClosureAuthoritySpec(authority_path="", authority_hash=_GOOD_HASH)
        with pytest.raises(ValueError, match="authority_hash"):
            ClosureAuthoritySpec(authority_path="/abs/file", authority_hash="")

    def test_rejects_malformed_hash(self) -> None:
        with pytest.raises(ValueError, match="authority_hash"):
            ClosureAuthoritySpec(
                authority_path="/abs/file",
                authority_hash="a" * 64,
            )
        with pytest.raises(ValueError, match="authority_hash"):
            ClosureAuthoritySpec(
                authority_path="/abs/file",
                authority_hash="sha256:" + "A" * 64,
            )
        with pytest.raises(ValueError, match="authority_hash"):
            ClosureAuthoritySpec(
                authority_path="/abs/file",
                authority_hash="sha256:" + "g" * 64,
            )
        with pytest.raises(ValueError, match="authority_hash"):
            ClosureAuthoritySpec(
                authority_path="/abs/file",
                authority_hash="sha256:" + "a" * 63,
            )

    def test_rejects_nonabsolute_path(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            ClosureAuthoritySpec(authority_path="relative/path", authority_hash=_GOOD_HASH)

    def test_frozen_and_slots(self) -> None:
        spec = ClosureAuthoritySpec(authority_path="/abs/file", authority_hash=_GOOD_HASH)
        with pytest.raises(AttributeError):
            spec.authority_path = "/other"  # type: ignore[misc]
        assert hasattr(spec, "__slots__") or not hasattr(spec, "__dict__")


class TestFactory:
    def test_returns_none_when_both_absent(self) -> None:
        assert closure_authority_spec_from_args(None, None) is None
        assert closure_authority_spec_from_args("", "") is None

    def test_raises_on_xor_path_only(self) -> None:
        with pytest.raises(ValueError, match="both"):
            closure_authority_spec_from_args("/abs/file", None)

    def test_raises_on_xor_hash_only(self) -> None:
        with pytest.raises(ValueError, match="both"):
            closure_authority_spec_from_args(None, _GOOD_HASH)

    def test_constructs_when_both_present(self) -> None:
        spec = closure_authority_spec_from_args("/abs/file", _GOOD_HASH)
        assert spec is not None
        assert spec.authority_path == "/abs/file"
        assert spec.authority_hash == _GOOD_HASH

    def test_threads_optional_plan_and_ref_fields(self) -> None:
        spec = closure_authority_spec_from_args(
            "/abs/file",
            _GOOD_HASH,
            plan_paths=("/a/plan.md", "/b/plan.md"),
            base_sha="main",
            diff_sha="abc123",
            target_sha="branch-x",
        )
        assert spec is not None
        assert spec.plan_paths == ("/a/plan.md", "/b/plan.md")
        assert spec.base_sha == "main"
        assert spec.diff_sha == "abc123"
        assert spec.target_sha == "branch-x"
