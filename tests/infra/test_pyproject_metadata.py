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
