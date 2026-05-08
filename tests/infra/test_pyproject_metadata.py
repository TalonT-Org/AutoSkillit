"""Verify pyproject.toml contains required public release metadata."""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent.parent / "pyproject.toml"


def _project():
    return tomllib.loads(PYPROJECT.read_text())


def test_pyproject_has_license():
    p = _project()["project"]
    assert "license" in p, "pyproject.toml must have a [project] license field"


def test_pyproject_has_authors():
    p = _project()["project"]
    assert "authors" in p and p["authors"], "pyproject.toml must have authors"


def test_pyproject_has_readme():
    p = _project()["project"]
    assert p.get("readme") == "README.md"


def test_pyproject_has_classifiers():
    p = _project()["project"]
    assert "classifiers" in p and len(p["classifiers"]) > 0


def test_pyproject_has_sdist_target():
    data = _project()
    sdist = (
        data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("sdist", {})
    )
    assert "include" in sdist, "[tool.hatch.build.targets.sdist] must list included paths"


def test_markdown_it_py_in_dependencies() -> None:
    """REQ-R741-A05 — markdown-it-py must be a declared runtime dependency."""
    deps: list[str] = _project()["project"]["dependencies"]
    assert any("markdown-it-py" in d for d in deps), (
        "markdown-it-py not found in [project].dependencies in pyproject.toml"
    )


def test_api_simulator_dev_dep_is_linux_only() -> None:
    """api-simulator must carry sys_platform == 'linux' so it is not installed on macOS/Windows."""
    deps: list[str] = _project()["project"]["optional-dependencies"]["dev"]
    api_sim = next((d for d in deps if d.startswith("api-simulator")), None)
    assert api_sim is not None, "api-simulator not found in dev dependencies"
    assert "sys_platform" in api_sim and "linux" in api_sim, (
        f"api-simulator dev dep missing sys_platform == 'linux' marker: {api_sim!r}"
    )
