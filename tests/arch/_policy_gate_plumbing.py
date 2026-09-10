# autoskillit: policy-authority -- repository acceptance policy. Humans edit this file;
# automated repair sessions must not. Every approval needs a tracking issue and code-owner review.
"""Base-ref resolution and the run/skip decision for the diff-scoped policy gates.

Every decision that determines whether a gate actually runs lives here rather than
in the candidate-writable conftest modules, so a change that silences a gate is
confined to one sentinel-bearing file.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from scripts.ci_target_policy import BASE_SHA_EVENTS
from tests._test_filter import resolve_test_base_ref_from_env


@dataclasses.dataclass(frozen=True)
class BaseRefContext:
    """The base ref a gate compares against, and whether skipping is permitted.

    Named outside pytest's ``Test*`` collection pattern on purpose: an imported
    ``Test``-prefixed class raises PytestCollectionWarning in every importer.
    """

    base_ref: str | None
    gate_required: bool

    @classmethod
    def from_env(cls, explicit: str | None) -> BaseRefContext:
        return cls(
            resolve_test_base_ref_from_env(explicit),
            os.environ.get("GITHUB_EVENT_NAME") in BASE_SHA_EVENTS,
        )


TEST_BASE_KEY = pytest.StashKey[BaseRefContext]()


def require_base_ref_or_skip(ctx: BaseRefContext) -> str:
    """Return the base ref, skipping locally but failing on a CI review event."""
    if ctx.base_ref is None:
        if ctx.gate_required:
            pytest.fail(
                "no base ref on a pull_request/merge_group event; the diff-scoped "
                "gates must run on every reviewed change"
            )
        pytest.skip("no base ref resolved (AUTOSKILLIT_TEST_BASE_REF/GITHUB_BASE_REF unset)")
    return ctx.base_ref
