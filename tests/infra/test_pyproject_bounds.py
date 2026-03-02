import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _get_deps() -> dict:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = {d.split(">=")[0].split("<")[0].split(",")[0].strip(): d
           for d in data["project"]["optional-dependencies"]["dev"]}
    return dev


def test_packaging_lower_bound():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    pkg = next(d for d in deps if d.startswith("packaging"))
    # Must require >=25.0
    assert ">=25.0" in pkg


def test_pytest_lower_bound():
    deps = _get_deps()
    assert ">=9.0.0" in deps["pytest"]


def test_pytest_asyncio_lower_bound():
    deps = _get_deps()
    assert ">=1.0.0" in deps["pytest-asyncio"]


def test_pytest_timeout_no_restrictive_upper_bound():
    deps = _get_deps()
    spec = deps["pytest-timeout"]
    # Old spec was <2.4 — must not be present
    assert "<2.4" not in spec


def test_ruff_lower_bound():
    deps = _get_deps()
    assert ">=0.15.0" in deps["ruff"]
