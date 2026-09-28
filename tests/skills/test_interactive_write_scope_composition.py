"""Interactive sessions compose real bundled skills' write scopes by union (#5154)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hooks._session_binding import resolve_binding_path
from tests.hooks._interactive_guard_harness import (
    bind,
    bundled_entries,
    bundled_entry,
    make_runtime,
    manifest_entry,
    temp_target,
    write_guard,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def test_investigate_prepare_issue_make_plan_each_write_their_own_directory(
    tmp_path: Path,
) -> None:
    chain = ("investigate", "prepare-issue", "make-plan")
    runtime = make_runtime(tmp_path, bundled_entries(*chain))
    bind(runtime, chain)

    for name in chain:
        outcome = write_guard(runtime, temp_target(runtime, f"{name}/x.md"))
        assert outcome.decision == "allow", (name, outcome)
        assert outcome.reason_code == "in_scope"

    for outside in (runtime.project / "src" / "x.py", temp_target(runtime, "other/x.md")):
        outcome = write_guard(runtime, outside)
        assert outcome.decision == "deny"
        assert outcome.reason_code == "scope_violation"
        for name in chain:
            assert f"{name} →" in outcome.message


@pytest.mark.parametrize(
    "order",
    [("investigate", "implement-worktree"), ("implement-worktree", "investigate")],
)
def test_unrestricted_skill_lifts_a_bounded_session_in_either_order(
    tmp_path: Path, order: tuple[str, str]
) -> None:
    runtime = make_runtime(tmp_path, bundled_entries(*order))
    bind(runtime, order)

    outcome = write_guard(runtime, runtime.project / "src" / "feature.py")

    assert outcome.decision == "allow"
    assert outcome.reason_code == "unrestricted_skill"


def test_foreign_skill_neither_widens_nor_poisons_a_bounded_session(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate"))
    bind(runtime, ("investigate", "code-review"))

    denied = write_guard(runtime, runtime.project / "src" / "x.py")
    allowed = write_guard(runtime, temp_target(runtime, "investigate/r.md"))

    assert (denied.decision, denied.reason_code) == ("deny", "scope_violation")
    assert (allowed.decision, allowed.reason_code) == ("allow", "in_scope")


@pytest.mark.parametrize("names", [("code-review",), ("anthropic-skills:docs",)])
def test_foreign_only_session_has_no_scope(tmp_path: Path, names: tuple[str, ...]) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate"))
    bind(runtime, names)

    outcome = write_guard(runtime, runtime.project / "src" / "x.py")

    assert (outcome.decision, outcome.reason_code) == ("allow", "no_scope")


def test_inherit_skill_abstains(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate", "reload-session"))
    bind(runtime, ("investigate", "reload-session"))
    assert write_guard(runtime, runtime.project / "src" / "x.py").reason_code == "scope_violation"
    assert write_guard(runtime, temp_target(runtime, "investigate/r.md")).decision == "allow"


def test_inherit_skill_alone_has_no_scope(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("reload-session"))
    bind(runtime, ("reload-session",))

    outcome = write_guard(runtime, runtime.project / "src" / "x.py")

    assert (outcome.decision, outcome.reason_code) == ("allow", "no_scope")


def test_disjoint_bounded_skills_keep_the_earlier_skills_directory(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate", "prepare-issue"))
    bind(runtime, ("investigate", "prepare-issue"))

    outcome = write_guard(runtime, temp_target(runtime, "investigate/report.md"))

    assert (outcome.decision, outcome.reason_code) == ("allow", "in_scope")


def test_autoskillit_name_absent_from_manifest_denies_unresolved(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate"))
    bind(runtime, ("investigate", "autoskillit:ghost"))

    outcome = write_guard(runtime, temp_target(runtime, "investigate/r.md"))

    assert (outcome.decision, outcome.reason_code) == ("deny", "unresolved")
    assert "ghost" in outcome.message
    assert "projection manifest" in outcome.message


@pytest.mark.parametrize(
    "flag_content",
    [
        "not-json",
        '{"schema_version": 3, "session_id": "interactive", "loaded_skills": []}',
    ],
)
def test_corrupt_binding_denies_unresolved_with_its_error(
    tmp_path: Path, flag_content: str
) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate"))
    flag = resolve_binding_path(str(runtime.project), "interactive")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(flag_content, encoding="utf-8")

    outcome = write_guard(runtime, runtime.project / "src" / "x.py")

    assert (outcome.decision, outcome.reason_code) == ("deny", "unresolved")
    assert "session binding is invalid" in outcome.message


def test_declared_directory_symlinked_outside_the_project_denies(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate"))
    outside = tmp_path / "outside"
    outside.mkdir()
    temp_target(runtime, "investigate").symlink_to(outside, target_is_directory=True)
    bind(runtime, ("investigate",))

    outcome = write_guard(runtime, temp_target(runtime, "investigate/r.md"))

    assert (outcome.decision, outcome.reason_code) == ("deny", "unresolved")
    assert "declared write scope for skill investigate" in outcome.message
    assert "outside" in outcome.message


def test_malformed_scope_of_a_loaded_skill_denies_unresolved(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, {"investigate": manifest_entry(None)})
    bind(runtime, ("investigate",))

    outcome = write_guard(runtime, temp_target(runtime, "investigate/r.md"))

    assert (outcome.decision, outcome.reason_code) == ("deny", "unresolved")
    assert "'investigate' has invalid write_scope" in outcome.message


def test_malformed_scope_of_an_unloaded_skill_does_not_affect_loaded_skills(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(
        tmp_path,
        {"investigate": bundled_entry("investigate"), "broken": manifest_entry([])},
    )
    bind(runtime, ("investigate",))

    outcome = write_guard(runtime, temp_target(runtime, "investigate/r.md"))

    assert (outcome.decision, outcome.reason_code) == ("allow", "in_scope")
