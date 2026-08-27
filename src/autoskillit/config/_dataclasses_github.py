"""GitHub integration dataclasses.

Owns: ``GitHubConfig`` (with all helper methods — label/state lookups, lifecycle
metadata resolution, allowed-label gating) and ``ReportBugConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autoskillit.core import LABEL_LIFECYCLE_REGISTRY, IssueLabelState


@dataclass
class GitHubConfig:
    token: str | None = None
    default_repo: str | None = None
    review_comment_cap: int = 50
    in_progress_label: str = "in-progress"
    staged_label: str = "staged"
    fail_label: str = "fail"
    queued_label: str = "queued"
    allowed_labels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if (
            isinstance(self.review_comment_cap, bool)
            or not isinstance(self.review_comment_cap, int)
            or self.review_comment_cap <= 0
        ):
            raise ValueError("github.review_comment_cap must be a positive integer")

    def check_label_allowed(self, label: str) -> str | None:
        """Return None if label is permitted, or an error message string if not.

        When allowed_labels is empty, all labels are permitted (unrestricted/opt-out mode).
        Lifecycle labels (QUEUED, IN_PROGRESS, STAGED, FAIL) are always permitted.
        """
        if not self.allowed_labels:
            return None
        if self.state_for_label(label) is not None:
            return None
        if label not in self.allowed_labels:
            allowed_sorted = sorted(self.allowed_labels)
            return (
                f"Label '{label}' is not in the configured allowed labels. "
                f"Allowed: {allowed_sorted}. "
                f"Add '{label}' to github.allowed_labels in your config to permit it."
            )
        return None

    def label_for_state(self, state: IssueLabelState) -> str:
        _map: dict[IssueLabelState, str] = {
            IssueLabelState.QUEUED: self.queued_label,
            IssueLabelState.IN_PROGRESS: self.in_progress_label,
            IssueLabelState.STAGED: self.staged_label,
            IssueLabelState.FAIL: self.fail_label,
        }
        if state not in _map:
            raise ValueError(f"No label configured for state {state!r}")
        return _map[state]

    def state_for_label(self, label: str) -> IssueLabelState | None:
        for state in IssueLabelState:
            if self.label_for_state(state) == label:
                return state
        return None

    def labels_for_states(self, states: frozenset[IssueLabelState]) -> list[str]:
        return [self.label_for_state(s) for s in states]

    def resolve_label_metadata(self, label: str) -> tuple[str, str, list[str]]:
        """Return (color, description, remove_labels) for a lifecycle label.

        Uses the registry when label maps to a lifecycle state; falls back to
        IN_PROGRESS defaults for custom labels not in the registry.
        """

        state = self.state_for_label(label)
        if state is not None:
            label_def = LABEL_LIFECYCLE_REGISTRY[state]
            return (
                label_def.color,
                label_def.description,
                self.labels_for_states(label_def.removes_on_entry),
            )
        return (
            "fbca04",
            "Issue is actively being processed by a pipeline session",
            [self.fail_label],
        )

    def all_lifecycle_labels(self) -> list[str]:
        return [self.label_for_state(s) for s in IssueLabelState]

    def check_labels_allowed(self, labels: list[str]) -> str | None:
        """Return None if all labels are permitted, or an error message for the first violation.

        When allowed_labels is empty, all labels are permitted (unrestricted/opt-out mode).
        """
        for label in labels:
            if err := self.check_label_allowed(label):
                return err
        return None


@dataclass
class ReportBugConfig:
    timeout: int = 600
    model: str | None = None
    report_dir: str | None = None  # None = resolved temp dir + /bug-reports/
    github_filing: bool = True
    github_labels: list[str] = field(default_factory=lambda: ["autoreported", "bug"])
