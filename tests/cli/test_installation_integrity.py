"""Focused coverage for read-only installation-integrity diagnostics."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import autoskillit.cli.doctor._doctor_install as _integrity
import autoskillit.cli.update._update_checks as _update_checks
from autoskillit.cli.install._install_info import InstallInfo, InstallType
from autoskillit.core import Severity
from tests.cli._installation_integrity_helpers import CLEAN, record_hash, record_text

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _write_install(
    root: Path,
    *,
    content: bytes = b"VERSION = '1.0'\n",
    layout: tuple[str, ...] = ("lib", "python3.14", "site-packages"),
) -> Path:
    env = root / "autoskillit"
    site_packages = env.joinpath(*layout)
    package = site_packages / "autoskillit"
    package.mkdir(parents=True)
    script_parts = (
        ("Scripts", "autoskillit.exe")
        if layout == ("Lib", "site-packages")
        else ("bin", "autoskillit")
    )
    script = env.joinpath(*script_parts)
    script.parent.mkdir(parents=True)
    files = {package / "__init__.py": content, script: b"#!/bin/sh\nexec python -m autoskillit\n"}
    for path, data in files.items():
        path.write_bytes(data)
    record = site_packages / "autoskillit-1.0.0.dist-info" / "RECORD"
    record.parent.mkdir()
    rows = [
        f"{Path(os.path.relpath(path, site_packages)).as_posix()},{record_hash(data)},{len(data)}"
        for path, data in files.items()
    ]
    record.write_text(record_text(rows, record.parent.name))
    return package


def _record(package: Path) -> Path:
    return package.parent / "autoskillit-1.0.0.dist-info" / "RECORD"


def _findings(*roots: Path) -> list:
    return _integrity.verify_installations(roots=roots)


@pytest.mark.parametrize(
    "layout",
    [
        ("lib", "python3.14", "site-packages"),
        ("lib64", "python3.14", "site-packages"),
        ("lib", "python3.14", "dist-packages"),
        ("Lib", "site-packages"),
    ],
)
def test_clean_install_reports_integrity_ok(tmp_path: Path, layout: tuple[str, ...]) -> None:
    _write_install(tmp_path, layout=layout)

    assert _findings(tmp_path) == [CLEAN]


def test_console_script_mutation_detected(tmp_path: Path) -> None:
    _write_install(tmp_path)
    script = tmp_path / "autoskillit" / "bin" / "autoskillit"
    script.write_bytes(b"mutated script")

    assert any(
        finding.check == "installation_record_mismatch"
        and str(script.resolve()) in finding.message
        for finding in _findings(tmp_path)
    )


@pytest.mark.parametrize("case", ["parent_escape", "sibling_prefix", "absolute"])
def test_record_row_outside_installation_is_invalid(tmp_path: Path, case: str) -> None:
    package = _write_install(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    sibling = tmp_path / "autoskillit-evil" / "x.py"
    sibling.parent.mkdir()
    sibling.write_bytes(b"x")
    row = {
        "parent_escape": "../../../../outside.txt",
        "sibling_prefix": "../../../../autoskillit-evil/x.py",
        "absolute": str(outside.resolve()),
    }[case]
    record = _record(package)
    record.write_text(record.read_text() + f"{row},{record_hash(b'x')},1\n")

    assert any(
        finding.check == "installation_record_invalid" and repr(row) in finding.message
        for finding in _findings(tmp_path)
    )


@pytest.mark.parametrize(
    "case",
    [
        "absolute_script",
        "redundant_dotdot",
        pytest.param(
            "double_slash",
            marks=pytest.mark.skipif(os.name == "nt", reason="POSIX double-leading slash"),
        ),
    ],
)
def test_equivalent_spellings_inside_installation_stay_clean(tmp_path: Path, case: str) -> None:
    package = _write_install(tmp_path)
    record = _record(package)
    old, new = (
        ("../../../bin/autoskillit", str((tmp_path / "autoskillit/bin/autoskillit").resolve()))
        if case == "absolute_script"
        else ("autoskillit/__init__.py", "autoskillit/../autoskillit/__init__.py")
    )
    if case == "double_slash":
        old = "../../../bin/autoskillit"
        new = "/" + str((tmp_path / "autoskillit/bin/autoskillit").resolve())
    record.write_text(record.read_text().replace(old, new))

    assert _findings(tmp_path) == [CLEAN]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("outside", "installation_record_invalid"),
        ("loop", "installation_record_unreadable_entry"),
    ],
)
def test_record_symlink_target_is_checked(tmp_path: Path, target: str, expected: str) -> None:
    package = _write_install(tmp_path)
    link = package / "linked.py"
    if target == "outside":
        destination = tmp_path / "outside.py"
        destination.write_bytes(b"x")
    else:
        destination = link
    link.symlink_to(destination)
    record = _record(package)
    record.write_text(record.read_text() + f"autoskillit/linked.py,{record_hash(b'x')},1\n")

    assert any(finding.check == expected for finding in _findings(tmp_path))


def test_unrecognized_layout_is_bounded_to_itself(tmp_path: Path) -> None:
    package = _write_install(tmp_path, layout=("archive",))

    assert any(
        finding.check == "installation_record_invalid"
        and f"outside {package.parent.resolve()}" in finding.message
        for finding in _findings(tmp_path)
    )


@pytest.mark.parametrize(
    ("site_packages", "expected"),
    [
        ("/e/lib/python3.13/site-packages", "/e"),
        ("/lib/python3.13/site-packages", "/lib/python3.13/site-packages"),
        ("/Lib/site-packages", "/Lib/site-packages"),
        ("/e/lib64/python3.13/site-packages", "/e"),
        ("/e/lib/python3.13t/site-packages", "/e"),
        ("/e/Lib/site-packages", "/e"),
        ("/e/lib/python3.13/dist-packages", "/e"),
        ("/e/share/python3.13/site-packages", "/e/share/python3.13/site-packages"),
        ("/e/lib/pythonjunk/site-packages", "/e/lib/pythonjunk/site-packages"),
        ("/c/archive-v0/abc", "/c/archive-v0/abc"),
        pytest.param(
            "C:/e/LIB/SITE-PACKAGES",
            "C:/e",
            marks=pytest.mark.skipif(os.name != "nt", reason="Native Windows path casing"),
        ),
    ],
)
def test_scheme_base(site_packages: str, expected: str) -> None:
    assert _integrity._scheme_base(Path(site_packages)) == Path(expected)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("nul_path", "installation_record_unreadable_entry"),
        ("variable_length_digest", "installation_record_unsupported_algorithm"),
        ("unknown_digest", "installation_record_unsupported_algorithm"),
    ],
)
def test_read_and_digest_failures_are_distinguished(
    tmp_path: Path, case: str, expected: str
) -> None:
    package = _write_install(tmp_path)
    record = _record(package)
    rows = record.read_text()
    if case == "nul_path":
        rows += f"autoskillit/a\x00b.py,{record_hash(b'x')},1\n"
    else:
        algorithm = "shake_128" if case == "variable_length_digest" else "not_a_hash_algorithm"
        rows = rows.replace(
            record_hash((package / "__init__.py").read_bytes()), f"{algorithm}=AAAA"
        )
    record.write_text(rows)

    assert any(finding.check == expected for finding in _findings(tmp_path))


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
    assert "Installation integrity failure" in rendered
    assert "Run `autoskillit install`" in rendered
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
    assert result.check == "source_version_drift"
    assert "Installation integrity failure" in result.message
    assert "Run `autoskillit install`" in result.message
    assert not calls
