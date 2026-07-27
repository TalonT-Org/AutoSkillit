"""Tests for run_doctor surviving a broken/unloadable config.yaml.

Covers the config_loadable ERROR result path (schema-invalid and syntax-invalid
config.yaml layers) and confirms it does not fire when the only "problem" is a
retired-but-healable key. Also exercises _check_config_layers_for_secrets
directly for agreement on both the retired-key-healed and secrets-placement
scenarios.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _write_project_config(tmp_path: Path, content: str) -> None:
    cfg_dir = tmp_path / ".autoskillit"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(content, encoding="utf-8")


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated HOME so tests never touch the developer's real ~/.autoskillit/."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_dir))
    return home_dir


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated project cwd, separate from tmp_home."""
    proj_dir = tmp_path / "project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(proj_dir)
    return proj_dir


def test_run_doctor_survives_never_valid_key_and_reports_error(
    tmp_home: Path,
    project_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A genuinely never-valid key survives load_config's ConfigSchemaError,
    run_doctor() returns normally, and the non-JSON output reports a
    config_loadable ERROR (all other checks still ran against defaults)."""
    from autoskillit.cli.doctor import run_doctor

    _write_project_config(project_dir, "bogus_section:\n  k: v\n")

    run_doctor()

    out = capsys.readouterr().out
    assert "ERROR: Configuration could not be loaded" in out
    assert "ran against built-in" in out


def test_run_doctor_json_reports_config_loadable_error(
    tmp_home: Path,
    project_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Same broken config in JSON output mode: a config_loadable/error result
    is present in the results list."""
    from autoskillit.cli.doctor import run_doctor

    _write_project_config(project_dir, "bogus_section:\n  k: v\n")

    run_doctor(output_json=True)

    payload = json.loads(capsys.readouterr().out)
    results = payload["results"]
    matches = [r for r in results if r["check"] == "config_loadable"]
    assert len(matches) == 1
    assert matches[0]["severity"] == "error"


def test_run_doctor_heals_retired_only_key_no_config_loadable(
    tmp_home: Path,
    project_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A config containing only a retired key (no other schema violation) is
    healed by remap_retired_keys before doctor ever sees a problem — no
    config_loadable result should be emitted at all."""
    from autoskillit.cli.doctor import run_doctor

    _write_project_config(project_dir, "diagnostics:\n  post_run_analysis: true\n")

    run_doctor(output_json=True)

    payload = json.loads(capsys.readouterr().out)
    results = payload["results"]
    matches = [r for r in results if r["check"] == "config_loadable"]
    assert matches == []


def test_run_doctor_survives_malformed_yaml_and_reports_error(
    tmp_home: Path,
    project_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """[SF-5] Genuinely malformed YAML (raises yaml.YAMLError) survives —
    run_doctor() returns rather than raising, and reports the same
    config_loadable ERROR result."""
    from autoskillit.cli.doctor import run_doctor

    # Verified manually to raise yaml.parser.ParserError (a YAMLError subclass)
    # via autoskillit.core.load_yaml before relying on it here.
    _write_project_config(project_dir, "foo:\n  - [unclosed")

    run_doctor(output_json=True)

    payload = json.loads(capsys.readouterr().out)
    results = payload["results"]
    matches = [r for r in results if r["check"] == "config_loadable"]
    assert len(matches) == 1
    assert matches[0]["severity"] == "error"


class TestCheckConfigLayersForSecretsAgreement:
    """Direct tests for _check_config_layers_for_secrets — retired-key healing
    and pre-existing secrets-placement regression coverage."""

    def test_retired_only_key_is_healed_to_ok(self, tmp_home: Path, project_dir: Path) -> None:
        from autoskillit.cli.doctor import Severity
        from autoskillit.cli.doctor._doctor_config import _check_config_layers_for_secrets

        _write_project_config(project_dir, "diagnostics:\n  post_run_analysis: true\n")

        result = _check_config_layers_for_secrets(project_dir=project_dir)

        assert result.severity == Severity.OK
        assert result.check == "config_secrets_placement"

    def test_github_token_in_config_yaml_is_still_an_error(
        self, tmp_home: Path, project_dir: Path
    ) -> None:
        from autoskillit.cli.doctor import Severity
        from autoskillit.cli.doctor._doctor_config import _check_config_layers_for_secrets

        _write_project_config(project_dir, "github:\n  token: sk-not-a-real-token\n")

        result = _check_config_layers_for_secrets(project_dir=project_dir)

        assert result.severity == Severity.ERROR
        assert result.check == "config_secrets_placement"
