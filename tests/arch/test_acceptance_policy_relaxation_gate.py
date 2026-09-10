# autoskillit: policy-authority -- repository acceptance policy. Humans edit this file;
# automated repair sessions must not. Every approval needs a tracking issue and code-owner review.
"""The acceptance-policy relaxation gate: loosening a registered surface needs approval.

The gate itself is test 17; everything above it proves the classifier has teeth,
because a classifier that reported nothing would let test 17 pass forever.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from tests.arch._acceptance_policy_surfaces import (
    CODEOWNER_REVIEWED_PATHS,
    POLICY_AUTHORITY_PATHS,
    POLICY_AUTHORITY_SENTINEL,
    POLICY_RELAXATION_APPROVALS,
    POLICY_SURFACES,
)
from tests.arch._policy_gate_plumbing import BaseRefContext, require_base_ref_or_skip

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_policy_relaxation.py"


def _load_check_module():
    spec = importlib.util.spec_from_file_location("check_policy_relaxation", _CHECK_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


check = _load_check_module()


def _int_map(default: int | None = None):
    return check.PolicySurface("p.py", "LIMITS", "int_map", default)


def _int_scalar():
    return check.PolicySurface("p.py", "BUDGET", "int_scalar")


def _exemptions():
    return check.PolicySurface("p.py", "EXEMPTIONS", "exemption_map")


def _values(**entries: int) -> dict[str | None, object]:
    return {key: check.SurfaceValue(limit=value) for key, value in entries.items()}


def _reasons(relaxations) -> list[str]:
    return [item.reason for item in relaxations]


# --- 1-4: int_map -----------------------------------------------------------


def test_int_map_increase_is_relaxation() -> None:
    surface = _int_map(default=10)
    found = check.classify(_values(execution=23), _values(execution=24), surface)
    assert len(found) == 1
    assert found[0].key == "execution"
    assert (found[0].before, found[0].after) == ("23", "24")
    assert found[0].reason == "value increased"


def test_int_map_decrease_and_default_removal_are_tightening() -> None:
    surface = _int_map(default=10)
    assert check.classify(_values(execution=24), _values(execution=23), surface) == []
    assert check.classify(_values(execution=23), {}, surface) == []


def test_int_map_removal_below_default_is_relaxation() -> None:
    surface = _int_map(default=10)
    found = check.classify(_values(x=5), {}, surface)
    assert len(found) == 1
    assert (found[0].key, found[0].before, found[0].after) == ("x", "5", "absent")
    assert found[0].reason == "entry removed below default"


def test_int_map_new_key_above_default_is_relaxation() -> None:
    surface = _int_map(default=10)
    assert _reasons(check.classify({}, _values(x=11), surface)) == ["entry added above default"]
    assert check.classify({}, _values(x=10), surface) == []


def test_int_map_removal_without_default_is_relaxation() -> None:
    surface = _int_map(default=None)
    assert _reasons(check.classify(_values(x=5), {}, surface)) == ["entry removed"]
    assert check.classify({}, _values(x=5), surface) == []


# --- 5: int_scalar ----------------------------------------------------------


def test_int_scalar_increase_is_relaxation() -> None:
    surface = _int_scalar()
    before = {None: check.SurfaceValue(limit=155)}
    after = {None: check.SurfaceValue(limit=156)}
    found = check.classify(before, after, surface)
    assert len(found) == 1
    assert found[0].key is None
    assert (found[0].before, found[0].after, found[0].reason) == ("155", "156", "value increased")
    assert check.classify(after, before, surface) == []


# --- 6-8: exemption_map -----------------------------------------------------


def test_exemption_new_entry_is_relaxation() -> None:
    surface = _exemptions()
    entry = {"a.py": check.SurfaceValue(limit=1050)}
    assert _reasons(check.classify({}, entry, surface)) == ["entry added"]
    assert check.classify(entry, {}, surface) == []


def test_exemption_limit_increase_is_relaxation() -> None:
    surface = _exemptions()
    low = {"a.py": check.SurfaceValue(limit=1050)}
    high = {"a.py": check.SurfaceValue(limit=1100)}
    found = check.classify(low, high, surface)
    assert _reasons(found) == ["limit increased"]
    assert found[0].before == "limit=1050 predicate=None"
    assert found[0].after == "limit=1100 predicate=None"
    assert check.classify(high, low, surface) == []


def test_exemption_predicate_added_or_changed_is_relaxation() -> None:
    surface = _exemptions()
    absent = {"a.py": check.SurfaceValue(limit=1050)}
    present = {"a.py": check.SurfaceValue(limit=1050, predicate_source="lambda: True")}
    changed = {"a.py": check.SurfaceValue(limit=1050, predicate_source="lambda: False")}
    assert _reasons(check.classify(absent, present, surface)) == ["predicate added"]
    assert _reasons(check.classify(present, changed, surface)) == ["predicate changed"]
    assert check.classify(present, absent, surface) == []


# --- 8b: the registry gates itself -----------------------------------------


_REGISTRY_SOURCE = """
POLICY_SURFACES = (
    PolicySurface("a.py", "LIMITS", "int_map", default=10),
    PolicySurface("b.py", "BUDGET", "int_scalar"),
)
POLICY_RELAXATION_APPROVALS = ()
POLICY_AUTHORITY_PATHS = ("a.py", "b.py")
CODEOWNER_REVIEWED_PATHS = POLICY_AUTHORITY_PATHS + ("c.yml",)
"""


def _registry(source: str):
    return check.extract_registry(source)


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ('    PolicySurface("b.py", "BUDGET", "int_scalar"),\n', "surface removed"),
        ('"int_scalar")', "surface kind changed"),
        ("default=10", "default raised"),
        ("default_added", "default added"),
        ('POLICY_AUTHORITY_PATHS = ("a.py", "b.py")', "authority path removed"),
    ],
)
def test_surface_registry_relaxations_are_gated(mutation: str, expected_reason: str) -> None:
    base = _registry(_REGISTRY_SOURCE)
    if expected_reason == "surface removed":
        head_source = _REGISTRY_SOURCE.replace(mutation, "")
    elif expected_reason == "surface kind changed":
        head_source = _REGISTRY_SOURCE.replace(mutation, '"int_map")')
    elif expected_reason == "default raised":
        head_source = _REGISTRY_SOURCE.replace(mutation, "default=1000")
    elif expected_reason == "default added":
        head_source = _REGISTRY_SOURCE.replace(
            '"BUDGET", "int_scalar"', '"BUDGET", "int_scalar", default=5'
        )
    else:
        head_source = _REGISTRY_SOURCE.replace(mutation, 'POLICY_AUTHORITY_PATHS = ("a.py",)')
    found = check.classify_registry(base, _registry(head_source))
    assert expected_reason in _reasons(found), _reasons(found)
    assert all(item.path == "tests/arch/_acceptance_policy_surfaces.py" for item in found)


def test_surface_registry_tightening_is_free() -> None:
    base = _registry(_REGISTRY_SOURCE)
    added_row = _REGISTRY_SOURCE.replace(
        "POLICY_SURFACES = (\n",
        'POLICY_SURFACES = (\n    PolicySurface("d.py", "CAP", "int_scalar"),\n',
    )
    lowered = _REGISTRY_SOURCE.replace("default=10", "default=1")
    added_path = _REGISTRY_SOURCE.replace('("a.py", "b.py")', '("a.py", "b.py", "e.py")')
    for head_source in (added_row, lowered, added_path):
        assert check.classify_registry(base, _registry(head_source)) == []


def test_registry_authority_paths_flow_into_reviewed_paths() -> None:
    snapshot = _registry(_REGISTRY_SOURCE)
    assert snapshot.authority_paths == ("a.py", "b.py")
    assert snapshot.reviewed_paths == ("a.py", "b.py", "c.yml")


# --- 9-10: extraction fails closed -----------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "LIMITS = dict(execution=23)",
        "LIMITS = {'execution': 20 + 3}",
        "LIMITS = {NAME: 23}",
        "LIMITS = [1, 2]",
    ],
)
def test_extract_fails_closed_on_unsupported_shape(source: str) -> None:
    with pytest.raises(check.UnsupportedSurfaceShape):
        check.extract_surface_values(source, _int_map(default=10))


def test_extract_scalar_fails_closed_on_computed_value() -> None:
    with pytest.raises(check.UnsupportedSurfaceShape):
        check.extract_surface_values("BUDGET = 700 + 50", _int_scalar())


def test_extract_fails_closed_on_missing_symbol() -> None:
    with pytest.raises(check.SurfaceMissing):
        check.extract_surface_values("OTHER = 1", _int_scalar())


def test_extract_reads_annotated_assignment() -> None:
    values = check.extract_surface_values("BUDGET: int = 156", _int_scalar())
    assert values == {None: check.SurfaceValue(limit=156)}


# --- 11: approvals match exactly -------------------------------------------


def _approval(**overrides):
    fields = {
        "path": "p.py",
        "symbol": "LIMITS",
        "key": "execution",
        "before": "23",
        "after": "24",
        "issue": 4929,
        "approved_by": "Trecek",
    }
    fields.update(overrides)
    return check.PolicyRelaxationApproval(**fields)


def test_exact_approval_clears_relaxation() -> None:
    found = check.classify(_values(execution=23), _values(execution=24), _int_map(default=10))
    assert check.unapproved(found, [_approval()]) == []


@pytest.mark.parametrize(
    "overrides",
    [{"after": "25"}, {"key": "recipe"}, {"before": "22"}, {"symbol": "OTHER"}],
)
def test_inexact_approval_does_not_clear(overrides: dict[str, str]) -> None:
    found = check.classify(_values(execution=23), _values(execution=24), _int_map(default=10))
    assert check.unapproved(found, [_approval(**overrides)]) == found


# --- 12-13: the registry describes this working tree ------------------------


def test_registered_surfaces_extract_from_working_tree() -> None:
    failures: list[str] = []
    for surface in POLICY_SURFACES:
        path = REPO_ROOT / surface.path
        if not path.is_file():
            failures.append(f"{surface.path}: file does not exist")
            continue
        script_surface = check.PolicySurface(
            surface.path, surface.symbol, surface.kind, surface.default
        )
        try:
            check.extract_surface_values(path.read_text(encoding="utf-8"), script_surface)
        except (check.SurfaceMissing, check.UnsupportedSurfaceShape) as exc:
            failures.append(f"{surface.path}::{surface.symbol}: {exc}")
    assert not failures, "Registered surfaces that no longer extract:\n" + "\n".join(failures)


def test_registered_surfaces_are_unique() -> None:
    keys = [(surface.path, surface.symbol) for surface in POLICY_SURFACES]
    assert len(keys) == len(set(keys))


def test_approvals_are_well_formed() -> None:
    registered = {(surface.path, surface.symbol) for surface in POLICY_SURFACES}
    registry_symbols = {"POLICY_SURFACES", "POLICY_AUTHORITY_PATHS", "CODEOWNER_REVIEWED_PATHS"}
    for approval in POLICY_RELAXATION_APPROVALS:
        names_a_surface = (approval.path, approval.symbol) in registered
        names_the_registry = approval.symbol in registry_symbols
        assert names_a_surface or names_the_registry, (
            f"approval names an unregistered surface: {approval.path}::{approval.symbol}"
        )
        assert approval.issue > 0, "an approval must cite a tracking issue"
        assert approval.approved_by, "an approval must name the human who recorded it"


# --- 14-15: the authority files are marked and code-owned -------------------


def test_policy_authority_files_carry_sentinel() -> None:
    missing = []
    for rel in POLICY_AUTHORITY_PATHS:
        path = REPO_ROOT / rel
        if not path.is_file():
            missing.append(f"{rel}: file does not exist")
            continue
        head = path.read_text(encoding="utf-8").splitlines()[:3]
        if not any(POLICY_AUTHORITY_SENTINEL in line for line in head):
            missing.append(f"{rel}: no {POLICY_AUTHORITY_SENTINEL!r} in the first three lines")
    assert not missing, "Policy-authority files without the sentinel:\n" + "\n".join(missing)


def _codeowner_rules() -> list[tuple[str, list[str]]]:
    rules = []
    for line in (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        pattern, *owners = stripped.split()
        rules.append((pattern, owners))
    return rules


def test_codeowners_covers_reviewed_paths() -> None:
    rules = _codeowner_rules()
    uncovered = []
    for rel in CODEOWNER_REVIEWED_PATHS:
        covered = any(
            owners and (pattern.lstrip("/") == rel or fnmatch.fnmatch(rel, pattern.lstrip("/")))
            for pattern, owners in rules
        )
        if not covered:
            uncovered.append(rel)
    assert not uncovered, "CODEOWNERS does not require review for:\n" + "\n".join(uncovered)


# --- 16: the gate speaks to the session that trips it -----------------------

_TMP_SURFACES = """POLICY_SURFACES = (
    PolicySurface("counts.py", "LIMITS", "int_map", default=10),
)
POLICY_RELAXATION_APPROVALS = ({approvals})
POLICY_AUTHORITY_PATHS = ("counts.py",)
CODEOWNER_REVIEWED_PATHS = POLICY_AUTHORITY_PATHS + ()
"""

_TMP_APPROVAL = (
    'PolicyRelaxationApproval("counts.py", "LIMITS", "execution", "23", "24", 4929, "Trecek"),'
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests" / "arch").mkdir(parents=True)
    _git(repo.parent, "init", "-q", "repo")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "gate")
    (repo / check.SURFACES_PATH).write_text(_TMP_SURFACES.format(approvals=""), encoding="utf-8")
    (repo / "counts.py").write_text('LIMITS = {"execution": 23}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


def test_gate_emits_human_required_marker(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    repo = _seed_repo(tmp_path)
    (repo / "counts.py").write_text('LIMITS = {"execution": 24}\n', encoding="utf-8")

    assert check.main(["--base", "HEAD", "--repo-root", str(repo)]) == 1
    out = capsys.readouterr().out
    marker = [line for line in out.splitlines() if line.startswith(check.HUMAN_REQUIRED_MARKER)]
    assert len(marker) == 1
    assert "counts.py::LIMITS[execution]" in marker[0]
    assert "23 -> 24" in marker[0]
    assert "PolicyRelaxationApproval" in out
    assert check.SURFACES_PATH in out

    (repo / check.SURFACES_PATH).write_text(
        _TMP_SURFACES.format(approvals=_TMP_APPROVAL), encoding="utf-8"
    )
    assert check.main(["--base", "HEAD", "--repo-root", str(repo)]) == 0


def test_preexisting_approval_cannot_authorize_a_repeat(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    (repo / check.SURFACES_PATH).write_text(
        _TMP_SURFACES.format(approvals=_TMP_APPROVAL), encoding="utf-8"
    )
    (repo / "counts.py").write_text('LIMITS = {"execution": 24}\n', encoding="utf-8")
    assert check.main(["--base", "HEAD", "--repo-root", str(repo)]) == 0

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "approved raise")
    (repo / "counts.py").write_text('LIMITS = {"execution": 23}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "tighten back")

    (repo / "counts.py").write_text('LIMITS = {"execution": 24}\n', encoding="utf-8")
    assert check.main(["--base", "HEAD", "--repo-root", str(repo)]) == 1


def test_gate_fails_closed_when_a_registered_surface_vanishes(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    (repo / "counts.py").unlink()
    assert check.main(["--base", "HEAD", "--repo-root", str(repo)]) == 1


# --- 17: the gate itself ----------------------------------------------------


def test_no_registered_surface_relaxed_against_base(resolved_test_base: BaseRefContext) -> None:
    """No registered policy value may be loosened without a recorded approval."""
    base_ref = require_base_ref_or_skip(resolved_test_base)
    base_rev = check.merge_base(REPO_ROOT, base_ref)
    assert base_rev is not None, f"could not resolve a merge base against {base_ref!r}"
    diagnostics = check.evaluate(REPO_ROOT, base_rev, check._working_tree_reader(REPO_ROOT))
    assert not diagnostics, "\n".join(diagnostics)


# --- 18: the stashed base survives the autouse scrub ------------------------


def test_probe_stashed_base_survives_autouse_scrub(resolved_test_base: BaseRefContext) -> None:
    """Run standalone by test_stashed_base_end_to_end; skips in a bare local run."""
    if resolved_test_base.base_ref is None:
        pytest.skip("no base ref configured for this session")
    assert "AUTOSKILLIT_TEST_BASE_REF" not in os.environ
    assert resolved_test_base.base_ref is not None


def test_stashed_base_end_to_end(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stash must outlive the autouse fixture that deletes the env var."""
    monkeypatch.setenv("AUTOSKILLIT_TEST_BASE_REF", "HEAD")
    monkeypatch.delenv("AUTOSKILLIT_TEST_FILTER", raising=False)
    result = pytester.runpytest_subprocess(
        f"{Path(__file__).resolve()}::test_probe_stashed_base_survives_autouse_scrub",
        "--rootdir",
        str(REPO_ROOT),
        "-c",
        str(REPO_ROOT / "pyproject.toml"),
        "-p",
        "no:xdist",
        "-p",
        "no:cacheprovider",
    )
    result.assert_outcomes(passed=1)
