"""Focused coverage for read-only installation-integrity diagnostics."""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

import pytest

import autoskillit.cli.doctor._doctor_install as _integrity
import autoskillit.cli.update._update_checks as _update_checks
from autoskillit.cli.install._install_info import InstallInfo, InstallType
from autoskillit.core import Severity

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _record_hash(content: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
    return f"sha256={encoded.decode()}"


def _write_install(root: Path, *, content: bytes = b"VERSION = '1.0'\n") -> Path:
    site_packages = root / "autoskillit" / "lib" / "python3.14" / "site-packages"
    package = site_packages / "autoskillit"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_bytes(content)
    record = site_packages / "autoskillit-1.0.0.dist-info" / "RECORD"
    record.parent.mkdir()
    record.write_text(f"autoskillit/__init__.py,{_record_hash(content)},{len(content)}\n")
    return package


def _findings(*roots: Path) -> list:
    return _integrity.verify_installations(roots=roots)


def test_record_mismatch_detected(tmp_path: Path) -> None:
    package = _write_install(tmp_path / "install")
    (package / "__init__.py").write_text("VERSION = 'mutated'\n")

    findings = _findings(tmp_path / "install")

    assert any(finding.check == "installation_record_mismatch" for finding in findings)


def test_untracked_addition_detected_and_runtime_bytecode_ignored(tmp_path: Path) -> None:
    package = _write_install(tmp_path / "install")
    (package / "_run_aggregate.py").write_text("unexpected = True\n")
    pycache = package / "__pycache__"
    pycache.mkdir()
    (pycache / "module.cpython-314.pyc").write_bytes(b"bytecode")

    findings = _findings(tmp_path / "install")

    checks = [finding.check for finding in findings]
    assert "installation_untracked_file" in checks
    assert checks.count("installation_untracked_file") == 1

    untracked = next(
        finding for finding in findings if finding.check == "installation_untracked_file"
    )
    assert "_run_aggregate.py" in untracked.message
    assert "module.cpython-314.pyc" not in untracked.message
    for finding in findings:
        assert "module.cpython-314.pyc" not in finding.message


def test_hardlinked_cache_entry_reports_both_paths(tmp_path: Path) -> None:
    installation = tmp_path / "install"
    installed_package = _write_install(installation)
    cache = tmp_path / "cache" / "archive-v0"
    cached_package = _write_install(cache)
    cached_module = cached_package / "__init__.py"
    cached_module.unlink()
    os.link(installed_package / "__init__.py", cached_module)

    findings = _findings(installation, cache)

    hardlinks = [
        finding.message for finding in findings if finding.check == "installation_hardlink"
    ]
    assert len(hardlinks) == 1
    assert str(installed_package / "__init__.py") in hardlinks[0]
    assert str(cached_module) in hardlinks[0]


def test_generation_and_legacy_uv_tool_installations_are_enumerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_integrity, "_active_record_paths", lambda: iter(()))
    generation = tmp_path / ".autoskillit" / "plugin-generations" / "autoskillit" / "1.0" / "abc"
    legacy_uv = tmp_path / ".local" / "share" / "uv" / "tools" / "autoskillit"
    generation_package = _write_install(generation)
    legacy_package = _write_install(legacy_uv)
    (generation_package / "__init__.py").write_bytes(b"generation mutation")
    (legacy_package / "__init__.py").write_bytes(b"legacy mutation")

    findings = _integrity.verify_installations(home=tmp_path)

    messages = "\n".join(finding.message for finding in findings)
    assert str(generation_package / "__init__.py") in messages
    assert str(legacy_package / "__init__.py") in messages


def test_missing_version_is_integrity_failure_before_update_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="stable",
        url="https://example.test/autoskillit.git",
        editable_source=None,
    )
    monkeypatch.delenv("CI", raising=False)
    # isatty must report True so the function does not early-exit, but we
    # leave sys.stdout itself in place so pytest's capsys fixture can capture
    # the printed output.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(_update_checks, "detect_install", lambda: info)
    calls: list[object] = []
    monkeypatch.setattr(
        _update_checks, "resolve_target_identity", lambda *args: calls.append(args)
    )
    import autoskillit as package

    monkeypatch.delattr(package, "__version__")

    _update_checks.run_update_checks(home=tmp_path)

    rendered = capsys.readouterr().out
    assert "integrity failure" in rendered.lower()
    assert not calls


def test_doctor_missing_version_is_integrity_failure_before_network_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.cli.doctor import _check_source_version_drift

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="stable",
        url="https://example.test/autoskillit.git",
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli.install._install_info.detect_install", lambda: info)
    calls: list[object] = []
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks_source.resolve_target_identity",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    import autoskillit as package

    monkeypatch.delattr(package, "__version__")

    result = _check_source_version_drift(home=tmp_path)

    assert result.severity is Severity.ERROR
    assert "integrity failure" in result.message.lower()
    assert not calls
