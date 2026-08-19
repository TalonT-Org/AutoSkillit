"""Contract tests for the production ambient-env read surface (tests/_ambient_env_surface.py).

These are the TDD anchors for the ambient-state-contamination rectification:
they assert the AST scanner actually finds real production env-var touch
points (T1, T4-T6 prove the scanner's immunity against unregistered/dynamic/
wholesale-forwarding reads), that the disposition/forwarding registries stay
in exact sync with what the scanner reports against the real tree (T2, T3,
T7), that every disposition carries a substantive rationale (T10), that any
future ``pytest.mark.ambient_env`` opt-out only ever names a var this module
already classifies as scrub (T11), and that the new registry provably
subsumes the legacy ``_clear_private_env`` fixture's two source sets (V4).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core.types._type_constants_env import AUTOSKILLIT_PRIVATE_ENV_VARS
from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS
from tests._ambient_env_surface import (
    AMBIENT_ENV_DISPOSITIONS,
    DYNAMIC_READ_EXEMPTIONS,
    FORWARDING_SITES,
    production_env_read_surface,
)
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.small]


def test_execpath_is_in_production_read_surface() -> None:
    surface = production_env_read_surface(SRC_ROOT)
    assert "CLAUDE_CODE_EXECPATH" in surface.names


def test_every_surface_var_has_a_disposition() -> None:
    surface = production_env_read_surface(SRC_ROOT)
    undeclared = surface.names - set(AMBIENT_ENV_DISPOSITIONS)
    assert not undeclared, (
        f"Surface vars missing an AMBIENT_ENV_DISPOSITIONS entry: {sorted(undeclared)}"
    )


def test_no_orphan_dispositions() -> None:
    surface = production_env_read_surface(SRC_ROOT)
    orphans = set(AMBIENT_ENV_DISPOSITIONS) - surface.names
    assert not orphans, (
        f"Stale AMBIENT_ENV_DISPOSITIONS entries no longer in the surface: {sorted(orphans)}"
    )


def test_unknown_production_env_read_is_caught(tmp_path: Path) -> None:
    """Adversarial mirror: a brand-new os.environ.get() call is never silently invisible."""
    module = tmp_path / "synthetic_novel_read.py"
    module.write_text(
        "import os\n\n\ndef read() -> str | None:\n"
        '    return os.environ.get("SYNTHETIC_NOVEL_VAR_DO_NOT_REGISTER")\n'
    )
    surface = production_env_read_surface(tmp_path)
    assert "SYNTHETIC_NOVEL_VAR_DO_NOT_REGISTER" in surface.names


def test_scanner_reports_unresolved_dynamic_reads(tmp_path: Path) -> None:
    module = tmp_path / "synthetic_unresolved.py"
    module.write_text(
        "import os\n\n\ndef read(some_runtime_name: str) -> str | None:\n"
        "    return os.environ.get(some_runtime_name)\n"
    )
    surface = production_env_read_surface(tmp_path)
    matches = [u for u in surface.unresolved if u.file == "synthetic_unresolved.py"]
    assert matches, surface.unresolved
    assert matches[0].line == 5
    assert matches[0].expression == "some_runtime_name"


_KEYWORD_ONLY_READ_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "getenv_kwonly",
        "import os\n\n\ndef read() -> str | None:\n"
        '    return os.getenv(key="KWONLY_GETENV_SYNTHETIC_VAR", default="x")\n',
        "KWONLY_GETENV_SYNTHETIC_VAR",
    ),
    (
        "environ_pop_kwonly",
        "import os\n\n\ndef read() -> str | None:\n"
        '    return os.environ.pop(key="KWONLY_POP_SYNTHETIC_VAR", default=None)\n',
        "KWONLY_POP_SYNTHETIC_VAR",
    ),
    (
        "environ_setdefault_kwonly",
        "import os\n\n\ndef read() -> str | None:\n"
        '    return os.environ.setdefault(key="KWONLY_SETDEFAULT_SYNTHETIC_VAR", default="x")\n',
        "KWONLY_SETDEFAULT_SYNTHETIC_VAR",
    ),
)


@pytest.mark.parametrize(
    "name,source,expected_var",
    _KEYWORD_ONLY_READ_CASES,
    ids=[c[0] for c in _KEYWORD_ONLY_READ_CASES],
)
def test_scanner_resolves_all_keyword_argument_env_reads(
    tmp_path: Path, name: str, source: str, expected_var: str
) -> None:
    """R1 completeness: os.getenv/os.environ.pop/.setdefault called with an
    all-keyword ``key=``/``default=`` form must resolve into surface.names, never
    vanish silently (R6 -- must fail loudly, never silently under-report)."""
    module = tmp_path / f"synthetic_{name}.py"
    module.write_text(source)
    surface = production_env_read_surface(tmp_path)
    assert expected_var in surface.names, (
        f"{name}: {expected_var!r} not resolved into surface.names "
        f"(names={sorted(surface.names)}, unresolved={surface.unresolved})"
    )


_T6_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "dict_call",
        "import os\n\n\ndef make() -> dict[str, str]:\n    return dict(os.environ)\n",
        "",
    ),
    (
        "dict_splat",
        "import os\n\n\ndef make() -> dict[str, str]:\n    return {**os.environ}\n",
        "",
    ),
    (
        "dict_splat_merge",
        "import os\n\n\ndef make(other: dict[str, str]) -> dict[str, str]:\n"
        "    return {**os.environ, **other}\n",
        "",
    ),
    (
        "environ_copy",
        "import os\n\n\ndef make() -> dict[str, str]:\n    return os.environ.copy()\n",
        "",
    ),
    (
        "comprehension_with_exclusion",
        'import os\n\n_EXCLUDED = frozenset({"FOO"})\n\n\n'
        "def make() -> dict[str, str]:\n"
        "    return {k: v for k, v in os.environ.items() if k not in _EXCLUDED}\n",
        "_EXCLUDED",
    ),
    (
        "mapping_proxy_ifexp",
        "import os\nfrom types import MappingProxyType\n\n\n"
        "def make(x: dict[str, str] | None) -> dict[str, str]:\n"
        "    return MappingProxyType(dict(os.environ if x is None else x))\n",
        "",
    ),
    (
        "binop_union",
        "import os\n\n\ndef make(other: dict[str, str]) -> dict[str, str]:\n"
        "    return os.environ | other\n",
        "",
    ),
    (
        "subprocess_env_kw",
        "import os\nimport subprocess\n\n\ndef run() -> None:\n"
        '    subprocess.run(["true"], env=os.environ)\n',
        "",
    ),
    (
        "helper_child_env_kw",
        "import os\n\n\ndef helper(**kwargs: object) -> None: ...\n\n\n"
        "def call() -> None:\n    helper(child_env=os.environ)\n",
        "",
    ),
)


@pytest.mark.parametrize(
    "name,source,expected_exclusion", _T6_CASES, ids=[c[0] for c in _T6_CASES]
)
def test_scanner_reports_wholesale_forwarding_sites(
    tmp_path: Path, name: str, source: str, expected_exclusion: str
) -> None:
    module = tmp_path / f"synthetic_{name}.py"
    module.write_text(source)
    surface = production_env_read_surface(tmp_path)
    sites = [s for s in surface.forwarding_sites if s.file == f"synthetic_{name}.py"]
    assert sites, f"{name}: expected a forwarding site, got none from {surface.forwarding_sites}"
    if expected_exclusion:
        assert any(s.exclusion_set == expected_exclusion for s in sites), sites


def test_every_forwarding_site_is_declared() -> None:
    surface = production_env_read_surface(SRC_ROOT)
    undeclared = {f"{s.file}:{s.line}" for s in surface.forwarding_sites} - set(FORWARDING_SITES)
    assert not undeclared, (
        f"Forwarding sites missing a FORWARDING_SITES entry: {sorted(undeclared)}"
    )


def test_no_orphan_forwarding_sites() -> None:
    surface = production_env_read_surface(SRC_ROOT)
    live = {f"{s.file}:{s.line}" for s in surface.forwarding_sites}
    orphans = set(FORWARDING_SITES) - live
    assert not orphans, (
        f"Stale FORWARDING_SITES entries no longer in the surface: {sorted(orphans)}"
    )


def test_every_unresolved_read_is_declared() -> None:
    surface = production_env_read_surface(SRC_ROOT)
    undeclared = {f"{u.file}:{u.line}" for u in surface.unresolved} - set(DYNAMIC_READ_EXEMPTIONS)
    assert not undeclared, (
        f"Unresolved dynamic reads missing a DYNAMIC_READ_EXEMPTIONS entry: {sorted(undeclared)}"
    )


def test_no_orphan_dynamic_read_exemptions() -> None:
    surface = production_env_read_surface(SRC_ROOT)
    live = {f"{u.file}:{u.line}" for u in surface.unresolved}
    orphans = set(DYNAMIC_READ_EXEMPTIONS) - live
    assert not orphans, (
        f"Stale DYNAMIC_READ_EXEMPTIONS entries no longer in the surface: {sorted(orphans)}"
    )


def test_production_env_surface_scan_parses_all_source_files() -> None:
    """Fail loudly, never silently under-report: the AST scanner above must be able
    to parse every src/autoskillit/**/*.py file it scans."""
    surface = production_env_read_surface(SRC_ROOT)
    assert not surface.unparseable_files, (
        "production_env_read_surface could not parse these files, so it silently "
        "skipped scanning them: " + ", ".join(sorted(surface.unparseable_files))
    )


def test_justifications_are_substantive() -> None:
    """Every justification must be at least 40 characters."""
    short = {
        var: len(entry.justification)
        for var, entry in AMBIENT_ENV_DISPOSITIONS.items()
        if len(entry.justification) < 40
    }
    assert not short, f"AMBIENT_ENV_DISPOSITIONS justifications too short: {short}"

    short_forwarding = {
        site: len(justification)
        for site, justification in FORWARDING_SITES.items()
        if len(justification) < 40
    }
    assert not short_forwarding, f"FORWARDING_SITES justifications too short: {short_forwarding}"

    short_dynamic = {
        site: len(justification)
        for site, justification in DYNAMIC_READ_EXEMPTIONS.items()
        if len(justification) < 40
    }
    assert not short_dynamic, f"DYNAMIC_READ_EXEMPTIONS justifications too short: {short_dynamic}"


def _ambient_env_marker_names(tests_root: Path) -> tuple[set[str], list[str]]:
    """Return (marker names, unparseable file paths) across every tests/**/*.py file."""
    names: set[str] = set()
    unparseable: list[str] = []
    for path in tests_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            unparseable.append(path.relative_to(tests_root.parent).as_posix())
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "ambient_env"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "mark"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "pytest"
            ):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value)
    return names, unparseable


def test_ambient_env_markers_reference_scrubbed_surface_vars() -> None:
    """No pytest.mark.ambient_env(...) usages exist yet -- this passes vacuously today.

    Once a later step introduces the marker, every name it references must
    already be a "scrub"-disposition surface var, not an unregistered or
    preserve-disposition one.
    """
    tests_root = Path(__file__).resolve().parent.parent
    referenced, _unparseable = _ambient_env_marker_names(tests_root)
    undeclared = {
        name
        for name in referenced
        if name not in AMBIENT_ENV_DISPOSITIONS
        or AMBIENT_ENV_DISPOSITIONS[name].disposition != "scrub"
    }
    assert not undeclared, (
        f"pytest.mark.ambient_env names not declared as scrub: {sorted(undeclared)}"
    )


def test_ambient_env_marker_scan_parses_all_test_files() -> None:
    """Fail loudly, never silently under-report: the pytest.mark.ambient_env scanner
    above must be able to parse every tests/**/*.py file it scans."""
    tests_root = Path(__file__).resolve().parent.parent
    _names, unparseable = _ambient_env_marker_names(tests_root)
    assert not unparseable, (
        "The pytest.mark.ambient_env scanner could not parse these files, so it "
        "silently skipped scanning them: " + ", ".join(sorted(unparseable))
    )


def test_scrub_disposition_is_superset_of_legacy_fixture_sets() -> None:
    """V4 subsumption check: the new registry must never scrub less than the old fixture did."""
    scrubbed = {
        var for var, entry in AMBIENT_ENV_DISPOSITIONS.items() if entry.disposition == "scrub"
    }
    assert AUTOSKILLIT_PRIVATE_ENV_VARS | _HEADLESS_EXCLUSIVE_VARS <= scrubbed
