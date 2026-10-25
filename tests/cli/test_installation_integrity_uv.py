"""Medium, offline coverage of installation integrity using a real uv-installed wheel."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import autoskillit.cli.doctor._doctor_install as _integrity
from tests.cli._installation_integrity_helpers import CLEAN, record_hash
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_uv_tool_install_verifies_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wheel = tmp_path / "dist" / "autoskillit-1.0.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    dist_info = "autoskillit-1.0.0.dist-info"
    members = {
        "autoskillit/__init__.py": b"def main() -> int:\n    return 0\n",
        f"{dist_info}/METADATA": b"Metadata-Version: 2.1\nName: autoskillit\nVersion: 1.0.0\n",
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: autoskillit-tests\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist_info}/entry_points.txt": b"[console_scripts]\nautoskillit = autoskillit:main\n",
    }
    rows = [f"{name},{record_hash(content)},{len(content)}" for name, content in members.items()]
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr(f"{dist_info}/RECORD", "\n".join([*rows, f"{dist_info}/RECORD,,"]) + "\n")

    home = tmp_path / "home"
    tool_dir = home / ".local" / "share" / "uv" / "tools"
    result = subprocess.run(
        [
            "uv",
            "tool",
            "install",
            "--offline",
            "--no-cache",
            "--python",
            sys.executable,
            str(wheel),
        ],
        env={
            **production_interpreter_env(),
            "UV_TOOL_DIR": str(tool_dir),
            "UV_TOOL_BIN_DIR": str(tool_dir.parent / ".tools-bin"),
            "UV_NO_CONFIG": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = next(
        (tool_dir / "autoskillit").glob(
            "lib/python*/site-packages/autoskillit-1.0.0.dist-info/RECORD"
        )
    )
    assert any(row.startswith("../") for row in record.read_text().splitlines())

    monkeypatch.setattr(_integrity, "_active_record_paths", lambda: iter(()))
    findings = _integrity.verify_installations(home=home)
    assert findings == [CLEAN], "\n".join(finding.message for finding in findings)
