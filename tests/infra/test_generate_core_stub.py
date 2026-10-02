"""Tests for scripts/generate_core_stub.py (core/__init__.pyi generator and freshness gate)."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from tests._helpers import load_core_stub_generator

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


@pytest.fixture
def make_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a synthetic core package under tmp_path and point the generator at it."""
    module = load_core_stub_generator()
    assert module.RUFF.is_file(), f"no locked ruff at {module.RUFF}"

    def _make(
        sources: dict[str, str],
        export_modules: tuple[str, ...],
        private: str = "{}",
        withheld: frozenset[str] = frozenset(),
    ) -> ModuleType:
        (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["I"]\n')
        core = tmp_path / "core"
        core.mkdir(exist_ok=True)
        (core / "__init__.py").write_text(f"_PRIVATE_REEXPORTS = {private}\n")
        for rel, text in sources.items():
            path = core / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(module, "CORE_DIR", core)
        monkeypatch.setattr(module, "STUB_PATH", core / "__init__.pyi")
        monkeypatch.setattr(module, "EXPORT_MODULES", export_modules)
        monkeypatch.setattr(module, "GATEWAY_WITHHELD", withheld)
        return module

    return _make


def _imports(text: str) -> dict[str, str]:
    return {
        alias.asname or alias.name: "." * node.level + (node.module or "")
        for node in ast.parse(text).body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def test_render_orders_by_ruff_not_by_source_order(make_core, tmp_path: Path) -> None:
    gen = make_core(
        {
            "launch.py": (
                '__all__ = ["LaunchAdapterResult", "launch_evidence_digest", '
                '"LaunchContractError", "LaunchResolver"]\n'
            )
        },
        (".launch",),
    )
    rendered = gen.render_stub()

    order = [line.split(" import ")[1].split(" as ")[0] for line in rendered.splitlines()[2:]]
    assert order.index("launch_evidence_digest") > order.index("LaunchResolver")
    lint = subprocess.run(
        [str(gen.RUFF), "check", "--select", "I", "--stdin-filename", str(gen.STUB_PATH), "-"],
        input=rendered,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert lint.returncode == 0, lint.stdout


def test_exports_come_from_all_only(make_core) -> None:
    gen = make_core(
        {"mod.py": '__all__ = ["Public", "_hidden"]\n\n\ndef extra() -> None: ...\n'},
        (".mod",),
    )
    assert _imports(gen.render_stub()) == {"Public": ".mod"}


def test_private_reexports_rendered_from_declared_module(make_core) -> None:
    gen = make_core(
        {"mod.py": '__all__ = ["Public"]\n_Lock = object()\n'},
        (".mod",),
        private='{"_Lock": ".mod"}',
    )
    assert "from .mod import _Lock as _Lock\n" in gen.render_stub()


_AGGREGATOR = (
    "from typing import TYPE_CHECKING\n\n"
    "if not TYPE_CHECKING:\n"
    "    from .{a} import __all__ as _a_all\n"
    "    from .{b} import __all__ as _b_all\n\n"
    "    __all__ = _a_all + _b_all\n"
)


def test_concatenated_all_resolves_through_all_aliases(make_core) -> None:
    gen = make_core(
        {
            "pkg/__init__.py": _AGGREGATOR.format(a="sub", b="_top"),
            "pkg/_top.py": '__all__ = ["Top"]\n',
            "pkg/sub/__init__.py": _AGGREGATOR.format(a="_leaf", b="_leaf2"),
            "pkg/sub/_leaf.py": '__all__ = ["Leaf"]\n',
            "pkg/sub/_leaf2.py": '__all__ = ("leaf_two",)\n',
        },
        (".pkg",),
    )
    assert _imports(gen.render_stub()) == {"Leaf": ".pkg", "leaf_two": ".pkg", "Top": ".pkg"}


@pytest.mark.parametrize(
    ("sources", "private", "named_file"),
    [
        ({"mod.py": "def f() -> None: ...\n"}, "{}", "mod.py"),
        ({"mod.py": "__all__ = compute()\n"}, "{}", "mod.py"),
        ({"mod.py": '__all__ = ["A"]\n'}, 'frozenset({"_x"})', "__init__.py"),
    ],
    ids=["no-all", "computed-all", "non-literal-private-map"],
)
def test_unresolvable_all_is_a_violation(
    make_core, sources: dict[str, str], private: str, named_file: str
) -> None:
    gen = make_core(sources, (".mod",), private=private)
    violations = gen.check()
    assert len(violations) == 1
    assert named_file in violations[0]


@pytest.mark.parametrize(
    ("sources", "export_modules"),
    [
        (
            {
                "pkg/__init__.py": _AGGREGATOR.format(a="_a", b="_b"),
                "pkg/_a.py": '__all__ = ["Shared"]\n',
                "pkg/_b.py": '__all__ = ["Other"]\n',
            },
            (".pkg", ".pkg._a"),
        ),
        (
            {
                "owner.py": '__all__ = ["Shared"]\n',
                "reexporter.py": 'from .owner import Shared\n\n__all__ = ["Shared"]\n',
            },
            (".owner", ".reexporter"),
        ),
    ],
    ids=["ancestor-aggregate", "sibling-reexport"],
)
def test_duplicate_export_is_a_violation(
    make_core, sources: dict[str, str], export_modules: tuple[str, ...]
) -> None:
    gen = make_core(sources, export_modules)
    violations = gen.check()
    assert len(violations) == 1
    assert all(module in violations[0] for module in export_modules)


def test_stale_stub_reports_symbols_and_remedy(make_core) -> None:
    gen = make_core({"mod.py": '__all__ = ["A", "B"]\n'}, (".mod",))
    gen.STUB_PATH.write_text("from .mod import A as A\nfrom .mod import Extra as Extra\n")

    [violation] = gen.check()
    assert "+ B" in violation
    assert "- Extra" in violation
    assert "task sync-core-stub" in violation
    assert gen.main(["--check"]) == 1

    assert gen.main([]) == 0
    assert gen.check() == []
    assert gen.main(["--check"]) == 0


def test_invalid_stub_syntax_is_a_stale_violation(make_core, capsys) -> None:
    gen = make_core({"mod.py": '__all__ = ["A"]\n'}, (".mod",))
    gen.STUB_PATH.write_text("from .mod import (\n")

    [violation] = gen.check()
    assert f"{gen.STUB_PATH} is stale:" in violation
    assert "invalid Python syntax at line 1:" in violation
    assert "task sync-core-stub" in violation
    assert gen.main(["--check"]) == 1
    assert capsys.readouterr().err == violation + "\n"


def test_order_only_drift_is_reported(make_core) -> None:
    gen = make_core({"mod.py": '__all__ = ["A", "B"]\n'}, (".mod",))
    lines = gen.render_stub().splitlines(keepends=True)
    lines[-2], lines[-1] = lines[-1], lines[-2]
    gen.STUB_PATH.write_text("".join(lines))

    [violation] = gen.check()
    assert "ordering/format only" in violation


def test_write_mode_is_idempotent(make_core) -> None:
    gen = make_core({"mod.py": '__all__ = ["A", "b"]\n'}, (".mod",))
    assert gen.main([]) == 0
    first = gen.STUB_PATH.read_bytes()
    assert gen.main([]) == 0
    assert gen.STUB_PATH.read_bytes() == first


def test_gateway_withheld_name_is_not_rendered(make_core) -> None:
    gen = make_core(
        {"mod.py": '__all__ = ["A", "Withheld"]\n'}, (".mod",), withheld=frozenset({"Withheld"})
    )
    assert _imports(gen.render_stub()) == {"A": ".mod"}


@pytest.mark.parametrize(
    ("private", "withheld", "reason"),
    [
        ("{}", "Unreached", "reached by no export module"),
        ('{"A": ".mod"}', "A", "also in _PRIVATE_REEXPORTS"),
    ],
    ids=["unreached", "also-private"],
)
def test_gateway_withheld_misdeclaration_is_a_violation(
    make_core, private: str, withheld: str, reason: str
) -> None:
    gen = make_core(
        {"mod.py": '__all__ = ["A", "B"]\n'},
        (".mod",),
        private=private,
        withheld=frozenset({withheld}),
    )
    [violation] = gen.check()
    assert repr(withheld) in violation
    assert reason in violation
