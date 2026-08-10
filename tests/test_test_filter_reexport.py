"""Re-export closure tests (R1–R2), split from test_test_filter.py (1,000-line guard)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests._test_filter import _expand_reexport_closure

pytestmark = [pytest.mark.medium]


class TestReexportClosure:
    def test_reexport_closure_direct_init(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "sub.py").write_text("x = 1\n")
        (pkg / "__init__.py").write_text("from .sub import x\n")

        result = _expand_reexport_closure({"pkg/sub.py"}, tmp_path)
        assert "pkg/__init__.py" in result
        assert "pkg/sub.py" in result

    def test_reexport_closure_no_match(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "other.py").write_text("y = 2\n")
        (pkg / "__init__.py").write_text("from .sub import x\n")

        result = _expand_reexport_closure({"pkg/other.py"}, tmp_path)
        assert "pkg/__init__.py" not in result
        assert "pkg/other.py" in result

    def test_reexport_closure_relative_src_root_terminates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a relative src_root walks up to Path('.'), which is its
        own parent — the ancestor walk must stop at that fixed point instead of
        looping forever (observed live via build_test_scope(tests_root='tests'))."""
        pkg = tmp_path / "src" / "autoskillit" / "execution"
        pkg.mkdir(parents=True)
        (pkg / "testing.py").write_text("x = 1\n")
        (pkg / "__init__.py").write_text("from .testing import x\n")
        monkeypatch.chdir(tmp_path)

        outcome: dict[str, set[str]] = {}

        def run() -> None:
            outcome["result"] = _expand_reexport_closure(
                {"src/autoskillit/execution/testing.py"}, Path(".")
            )

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=30)
        assert "result" in outcome, "_expand_reexport_closure did not terminate"
        assert "src/autoskillit/execution/__init__.py" in outcome["result"]
