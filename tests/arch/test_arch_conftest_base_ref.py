"""tests/arch/conftest.py must deselect against the stashed base ref.

Its collection hook once called git_changed_files with no base_ref, leaving the
resolution to an env var the autouse scrub deletes before every test body.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import tests.arch.conftest as arch_conftest
from tests.arch._policy_gate_plumbing import TEST_BASE_KEY, BaseRefContext

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_arch_deselection_uses_stashed_base_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "conservative")
    calls: list[dict[str, object]] = []

    def _record(**kwargs: object) -> None:
        calls.append(kwargs)
        return None

    monkeypatch.setattr(arch_conftest, "git_changed_files", _record)

    stash = pytest.Stash()
    stash[TEST_BASE_KEY] = BaseRefContext("explicit-base", False)
    arch_conftest.pytest_collection_modifyitems(SimpleNamespace(stash=stash), items=[])

    assert calls == [{"cwd": arch_conftest._PROJECT_ROOT, "base_ref": "explicit-base"}]
