"""Tests for IssueLabelState, LabelDef, LABEL_LIFECYCLE_REGISTRY, and LABEL_TRANSITIONS."""

from __future__ import annotations

import pytest

from autoskillit.core.types import (
    LABEL_LIFECYCLE_REGISTRY,
    LABEL_TRANSITIONS,
    IssueLabelState,
    validate_label_transition,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestIssueLabelState:
    def test_all_members_have_registry_entry(self):
        """Every IssueLabelState member exists as a key in LABEL_LIFECYCLE_REGISTRY."""
        for state in IssueLabelState:
            assert state in LABEL_LIFECYCLE_REGISTRY

    def test_registry_has_no_extra_keys(self):
        """Every key in LABEL_LIFECYCLE_REGISTRY is a valid IssueLabelState member."""
        for key in LABEL_LIFECYCLE_REGISTRY:
            assert isinstance(key, IssueLabelState)

    def test_queued_value(self):
        assert IssueLabelState.QUEUED == "queued"

    def test_in_progress_value(self):
        assert IssueLabelState.IN_PROGRESS == "in-progress"

    def test_staged_value(self):
        assert IssueLabelState.STAGED == "staged"

    def test_fail_value(self):
        assert IssueLabelState.FAIL == "fail"


class TestLabelDef:
    def test_all_defs_have_nonempty_color(self):
        """Every LabelDef in the registry has a truthy .color string."""
        for defn in LABEL_LIFECYCLE_REGISTRY.values():
            assert defn.color

    def test_all_defs_have_nonempty_description(self):
        """Every LabelDef in the registry has a truthy .description string."""
        for defn in LABEL_LIFECYCLE_REGISTRY.values():
            assert defn.description

    def test_removes_on_entry_reference_valid_states(self):
        """Every state in every LabelDef.removes_on_entry is a valid IssueLabelState member."""
        for defn in LABEL_LIFECYCLE_REGISTRY.values():
            for removed in defn.removes_on_entry:
                assert isinstance(removed, IssueLabelState)

    def test_queued_color(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.QUEUED].color == "c2e0c6"

    def test_in_progress_color(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.IN_PROGRESS].color == "fbca04"

    def test_staged_color(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.STAGED].color == "0075ca"

    def test_fail_color(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.FAIL].color == "d73a4a"

    def test_in_progress_removes_queued_and_fail(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.IN_PROGRESS].removes_on_entry == frozenset(
            {IssueLabelState.QUEUED, IssueLabelState.FAIL}
        )

    def test_queued_removes_fail(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.QUEUED].removes_on_entry == frozenset(
            {IssueLabelState.FAIL}
        )

    def test_staged_removes_in_progress_fail_and_queued(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.STAGED].removes_on_entry == frozenset(
            {IssueLabelState.IN_PROGRESS, IssueLabelState.FAIL, IssueLabelState.QUEUED}
        )

    def test_fail_removes_in_progress_and_queued(self):
        assert LABEL_LIFECYCLE_REGISTRY[IssueLabelState.FAIL].removes_on_entry == frozenset(
            {IssueLabelState.IN_PROGRESS, IssueLabelState.QUEUED}
        )


class TestLabelTransitions:
    def test_unlabeled_to_queued(self):
        """validate_label_transition accepts unlabeled -> queued."""
        validate_label_transition(None, IssueLabelState.QUEUED)

    def test_unlabeled_to_in_progress(self):
        """validate_label_transition accepts unlabeled -> in-progress."""
        validate_label_transition(None, IssueLabelState.IN_PROGRESS)

    def test_queued_to_in_progress(self):
        """validate_label_transition accepts queued -> in-progress."""
        validate_label_transition(IssueLabelState.QUEUED, IssueLabelState.IN_PROGRESS)

    def test_queued_to_none(self):
        """validate_label_transition accepts queued -> None (release/unlabel)."""
        validate_label_transition(IssueLabelState.QUEUED, None)

    def test_in_progress_to_staged(self):
        """validate_label_transition accepts in-progress -> staged."""
        validate_label_transition(IssueLabelState.IN_PROGRESS, IssueLabelState.STAGED)

    def test_in_progress_to_fail(self):
        """validate_label_transition accepts in-progress -> fail."""
        validate_label_transition(IssueLabelState.IN_PROGRESS, IssueLabelState.FAIL)

    def test_in_progress_to_none(self):
        """validate_label_transition accepts in-progress -> None (bare removal)."""
        validate_label_transition(IssueLabelState.IN_PROGRESS, None)

    def test_fail_to_queued(self):
        """validate_label_transition accepts fail -> queued (retry reclaim)."""
        validate_label_transition(IssueLabelState.FAIL, IssueLabelState.QUEUED)

    def test_fail_to_in_progress(self):
        """validate_label_transition accepts fail -> in-progress (retry reclaim)."""
        validate_label_transition(IssueLabelState.FAIL, IssueLabelState.IN_PROGRESS)

    def test_invalid_in_progress_to_queued(self):
        """validate_label_transition rejects in-progress -> queued."""
        with pytest.raises(ValueError, match="Invalid label transition"):
            validate_label_transition(IssueLabelState.IN_PROGRESS, IssueLabelState.QUEUED)

    def test_invalid_staged_to_in_progress(self):
        """validate_label_transition rejects staged -> in-progress."""
        with pytest.raises(ValueError, match="Invalid label transition"):
            validate_label_transition(IssueLabelState.STAGED, IssueLabelState.IN_PROGRESS)

    def test_invalid_staged_to_queued(self):
        """validate_label_transition rejects staged -> queued."""
        with pytest.raises(ValueError, match="Invalid label transition"):
            validate_label_transition(IssueLabelState.STAGED, IssueLabelState.QUEUED)

    def test_invalid_queued_to_staged(self):
        """validate_label_transition rejects queued -> staged."""
        with pytest.raises(ValueError, match="Invalid label transition"):
            validate_label_transition(IssueLabelState.QUEUED, IssueLabelState.STAGED)

    def test_invalid_queued_to_fail(self):
        """validate_label_transition rejects queued -> fail."""
        with pytest.raises(ValueError, match="Invalid label transition"):
            validate_label_transition(IssueLabelState.QUEUED, IssueLabelState.FAIL)

    def test_every_enum_member_in_transition_table(self):
        """Every IssueLabelState member appears as a key in LABEL_TRANSITIONS."""
        for state in IssueLabelState:
            assert state in LABEL_TRANSITIONS

    def test_none_in_transition_table(self):
        """None (unlabeled) appears as a key in LABEL_TRANSITIONS."""
        assert None in LABEL_TRANSITIONS
