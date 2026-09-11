"""Pinned Codex skill-discovery parsing and catalog-attestation contracts."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.execution.backends import _codex_discovery as discovery
from autoskillit.execution.backends import _codex_probes as probes
from tests.fixtures.codex import fixture_path

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_FIXTURE_ROOT = Path("/opt/autoskillit-fixtures/codex/discovery")
_FIXTURES = ("discovery_prompt_input_v0130.json", "discovery_prompt_input_v0153.json")


def _skills_text(document: list[object]) -> str:
    item = document[0]
    assert isinstance(item, dict)
    content = item["content"]
    assert isinstance(content, list)
    part = content[0]
    assert isinstance(part, dict)
    text = part["text"]
    assert isinstance(text, str)
    return text


def _with_skills_text(document: list[object], text: str) -> str:
    item = document[0]
    assert isinstance(item, dict)
    content = item["content"]
    assert isinstance(content, list)
    part = content[0]
    assert isinstance(part, dict)
    part["text"] = text
    return json.dumps(document)


def _fixture_document(name: str) -> list[object]:
    document = json.loads(fixture_path(name).read_text(encoding="utf-8"))
    assert isinstance(document, list)
    return document


def _catalog(
    tmp_path: Path,
    entries: tuple[str, ...] = ("alpha", "beta"),
) -> tuple[Path, tuple[tuple[str, str], ...]]:
    session_home = tmp_path / "session-home"
    catalog_dir = session_home / "add-dir" / "skills"
    catalog_dir.mkdir(parents=True)
    expected_entries: list[tuple[str, str]] = []
    for name in entries:
        skill_path = catalog_dir / name / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        expected_entries.append((name, f"{name}/SKILL.md"))
    native_skill = catalog_dir / ".system" / "native" / "SKILL.md"
    native_skill.parent.mkdir(parents=True)
    native_skill.write_text("native", encoding="utf-8")
    (session_home / "skills").symlink_to(catalog_dir, target_is_directory=True)
    return catalog_dir, tuple(expected_entries)


def _loader_output(name: str, catalog_dir: Path) -> str:
    document = _fixture_document(name)
    fixture_version = "v0130" if "v0130" in name else "v0153"
    source_root = _FIXTURE_ROOT / fixture_version / "home" / "skills"
    legacy_root = catalog_dir.parent.parent / "skills"
    return _with_skills_text(
        document,
        _skills_text(document).replace(str(source_root), str(legacy_root)),
    )


def _install_prompt_stub(
    tmp_path: Path,
    output: str,
    *,
    mutate_path: Path | None = None,
) -> tuple[tuple[str, ...], Mapping[str, str]]:
    output_path = tmp_path / "prompt-input.json"
    output_path.write_text(output, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "codex-prompt-input"
    commands = []
    if mutate_path is not None:
        commands.append(f"printf 'mutated' > '{mutate_path}'")
    commands.append(f"/bin/cat '{output_path}'")
    executable.write_text("#!/bin/sh\n" + "\n".join(commands) + "\n", encoding="utf-8")
    executable.chmod(0o755)
    return (
        ("codex-prompt-input", "debug", "prompt-input"),
        {"PATH": str(bin_dir)},
    )


@pytest.mark.parametrize(
    ("fixture_name", "expected_version"),
    [
        ("discovery_prompt_input_v0130.json", "v0130"),
        ("discovery_prompt_input_v0153.json", "v0153"),
    ],
)
def test_parse_skills_instructions_reads_pinned_real_loader_shapes(
    fixture_name: str,
    expected_version: str,
) -> None:
    parsed = discovery.parse_skills_instructions(
        fixture_path(fixture_name).read_text(encoding="utf-8")
    )
    root = _FIXTURE_ROOT / expected_version / "home" / "skills"

    assert parsed.names == {"alpha", "beta", "system-native"}
    assert parsed.roots == (root, root / ".system")
    assert parsed.paths == {
        "alpha": root / "alpha" / "SKILL.md",
        "beta": root / "beta" / "SKILL.md",
        "system-native": root / ".system" / "system-native" / "SKILL.md",
    }


@pytest.mark.parametrize("fixture_name", _FIXTURES)
def test_parse_skills_instructions_tolerates_json_and_rendering_whitespace(
    fixture_name: str,
) -> None:
    document = _fixture_document(fixture_name)
    text = _skills_text(document).replace("\n", "\r\n")
    text = text.replace("### Available skills", "\r\n\r\n### Available skills")
    reordered = [
        {
            "content": [{"text": text, "type": "input_text"}],
            "role": "developer",
            "type": "message",
        }
    ]

    parsed = discovery.parse_skills_instructions(json.dumps(reordered, indent=2))

    assert parsed.names == {"alpha", "beta", "system-native"}


@pytest.mark.parametrize(
    ("prompt_input", "message"),
    [
        ("{not json", "malformed JSON"),
        (json.dumps({"content": []}), "response-item list"),
        (json.dumps([{"role": "developer", "content": "wrong"}]), "malformed content"),
        (json.dumps([]), "<skills_instructions> block"),
    ],
)
def test_parse_skills_instructions_rejects_malformed_json_and_envelope(
    prompt_input: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        discovery.parse_skills_instructions(prompt_input)


@pytest.mark.parametrize(
    ("needle", "replacement", "message"),
    [
        (
            "### Skill roots",
            "### Roots",
            "Skill roots or Available skills heading",
        ),
        (
            "### Skill roots",
            "### Skills",
            "Skill roots or Available skills heading",
        ),
        (
            "- `r0` = `/opt/autoskillit-fixtures/codex/discovery/v0153/home/skills`",
            "- `r0` = `relative/skills`",
            "skill root is not absolute",
        ),
        (
            "- `r1` = `/opt/autoskillit-fixtures/codex/discovery/v0153/home/skills/.system`",
            "- `r0` = `/opt/autoskillit-fixtures/codex/discovery/v0153/home/skills`",
            "duplicate skill-root alias",
        ),
        (
            "(file: `r0/beta/SKILL.md`)",
            "(file: `r9/beta/SKILL.md`)",
            "unknown skill-root alias",
        ),
        (
            "(file: `r0/beta/SKILL.md`)",
            "(file: `relative/beta/SKILL.md`)",
            "skill path is not absolute",
        ),
        (
            "(file: `r0/beta/SKILL.md`)",
            "(file: `r0/../outside/SKILL.md`)",
            "skill path escapes skill root",
        ),
        (
            "- beta:",
            "- alpha: duplicate fixture skill (file: `r0/alpha/SKILL.md`)\n- beta:",
            "duplicate skill name",
        ),
    ],
)
def test_parse_skills_instructions_rejects_structural_drift(
    needle: str,
    replacement: str,
    message: str,
) -> None:
    document = _fixture_document("discovery_prompt_input_v0153.json")
    text = _skills_text(document).replace(needle, replacement)

    with pytest.raises(ValueError, match=message):
        discovery.parse_skills_instructions(_with_skills_text(document, text))


def test_parse_skills_instructions_rejects_paths_outside_available_section() -> None:
    document = _fixture_document("discovery_prompt_input_v0153.json")
    text = _skills_text(document).replace(
        "### Available skills",
        "(file: `r0/alpha/SKILL.md`)\n### Available skills",
    )

    with pytest.raises(ValueError, match="outside Available skills"):
        discovery.parse_skills_instructions(_with_skills_text(document, text))


def test_parse_skills_instructions_rejects_duplicate_names_and_truncated_entries() -> None:
    document = _fixture_document("discovery_prompt_input_v0153.json")
    duplicated = _skills_text(document).replace(
        "- beta: another managed fixture skill",
        "- alpha: duplicate managed fixture skill",
    )

    with pytest.raises(ValueError, match="duplicate skill name"):
        discovery.parse_skills_instructions(_with_skills_text(document, duplicated))

    document = _fixture_document("discovery_prompt_input_v0153.json")
    truncated = _skills_text(document).replace("(file: `r0/beta/SKILL.md`)", "")
    with pytest.raises(ValueError, match="has no path"):
        discovery.parse_skills_instructions(_with_skills_text(document, truncated))


def test_attest_catalog_discovery_accepts_real_loader_fixture_and_ignores_native_entries(
    tmp_path: Path,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output("discovery_prompt_input_v0153.json", catalog_dir),
    )

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    assert errors == []
    assert (catalog_dir / ".system" / "native" / "SKILL.md").is_file()


def test_attest_catalog_discovery_reports_missing_managed_name_with_context(
    tmp_path: Path,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    document = _fixture_document("discovery_prompt_input_v0153.json")
    missing_beta = _skills_text(document).replace(
        "- beta: another managed fixture skill\n  (file: `r0/beta/SKILL.md`)\n",
        "",
    )
    command, env = _install_prompt_stub(
        tmp_path,
        _with_skills_text(
            document,
            missing_beta.replace(
                str(_FIXTURE_ROOT / "v0153" / "home" / "skills"),
                str(catalog_dir.parent.parent / "skills"),
            ),
        ),
    )

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    diagnostic = "\n".join(errors)
    assert "missing managed names ['beta']" in diagnostic
    assert "roots=" in diagnostic
    assert f"catalog={catalog_dir}" in diagnostic
    assert "version=0.153.4" in diagnostic


def test_attest_catalog_discovery_preserves_unreadable_path_diagnostic(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir).replace(
        "r0/beta/SKILL.md",
        f"{tmp_path}/missing/beta/SKILL.md",
    )
    command, env = _install_prompt_stub(tmp_path, output)

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    diagnostic = "\n".join(errors)
    assert "unreadable: FileNotFoundError:" in diagnostic
    assert str(tmp_path / "missing" / "beta" / "SKILL.md") in diagnostic


def test_attest_catalog_discovery_reports_duplicate_legacy_root(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    legacy_root = catalog_dir.parent.parent / "skills"
    output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir).replace(
        "### Available skills",
        f"- `r9` = `{legacy_root}`\n### Available skills",
    )
    command, env = _install_prompt_stub(tmp_path, output)

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    assert any("roots contain duplicate legacy root" in error for error in errors)


def test_legacy_root_derivation_consumes_catalog_layout_depth(tmp_path: Path) -> None:
    contract = replace(
        discovery.CODEX_SKILL_DISCOVERY_CONTRACT,
        catalog_relpath="nested/add-dir/skills",
    )

    legacy_root = discovery._legacy_root_for_catalog(
        tmp_path / contract.catalog_relpath,
        contract,
    )

    assert legacy_root == tmp_path / contract.legacy_root_relpath


def test_attest_catalog_discovery_rejects_catalog_absent_from_roots(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir).replace(
        str(catalog_dir.parent.parent / "skills"),
        str(tmp_path / "unrelated-skills"),
    )
    command, env = _install_prompt_stub(tmp_path, output)

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    diagnostic = "\n".join(errors)
    assert "roots do not contain legacy root" in diagnostic
    assert f"catalog={catalog_dir}" in diagnostic
    assert "version=0.153.4" in diagnostic


def test_attest_catalog_discovery_same_name_native_skill_does_not_satisfy_managed_entry(
    tmp_path: Path,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    native_alpha = catalog_dir / ".system" / "alpha" / "SKILL.md"
    native_alpha.parent.mkdir()
    native_alpha.write_text("native alpha", encoding="utf-8")
    output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir).replace(
        "r0/alpha/SKILL.md",
        "r1/alpha/SKILL.md",
    )
    command, env = _install_prompt_stub(tmp_path, output)

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    assert any("misplaced managed paths" in error and "alpha=" in error for error in errors)


@pytest.mark.parametrize(
    ("result", "expected_fragment"),
    [
        (
            probes._BoundedProbeResult(None, b"", b"", failure="timed out"),
            "timed out",
        ),
        (
            probes._BoundedProbeResult(
                None,
                b"",
                b"",
                failure="stdout exceeded 65536 bytes",
            ),
            "stdout exceeded",
        ),
        (probes._BoundedProbeResult(23, b"", b"failure"), "status 23"),
        (probes._BoundedProbeResult(0, b"not json", b""), "parse failed"),
    ],
)
def test_attest_catalog_discovery_reports_bounded_probe_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: probes._BoundedProbeResult,
    expected_fragment: str,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", lambda *_args, **_kwargs: result)

    errors = discovery.attest_catalog_discovery(
        probe_command=("codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    diagnostic = "\n".join(errors)
    assert expected_fragment in diagnostic
    assert f"catalog={catalog_dir}" in diagnostic
    assert "version=0.153.4" in diagnostic


def test_attest_catalog_discovery_rejects_missing_managed_path_before_probe(
    tmp_path: Path,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    (catalog_dir / "beta" / "SKILL.md").unlink()

    errors = discovery.attest_catalog_discovery(
        probe_command=("missing-codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    assert any("catalog validation failed" in error for error in errors)
    assert any("beta/SKILL.md" in error for error in errors)


def test_attest_catalog_discovery_rejects_in_probe_managed_catalog_edit(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output("discovery_prompt_input_v0130.json", catalog_dir),
        mutate_path=catalog_dir / "alpha" / "SKILL.md",
    )

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    assert any("mutated the managed catalog" in error for error in errors)


def test_attest_catalog_discovery_distinguishes_revalidation_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output("discovery_prompt_input_v0153.json", catalog_dir),
    )
    original_fingerprint = discovery._fingerprint_managed_files
    calls = 0

    def fingerprint(expected_paths: Mapping[str, Path]) -> tuple[tuple[object, ...], ...]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("catalog read failed")
        return original_fingerprint(expected_paths)

    monkeypatch.setattr(discovery, "_fingerprint_managed_files", fingerprint)

    errors = discovery.attest_catalog_discovery(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    diagnostic = "\n".join(errors)
    assert "could not revalidate the managed catalog: OSError: catalog read failed" in diagnostic
    assert "mutated the managed catalog" not in diagnostic


def test_discovery_contract_pins_verified_upstream_revision() -> None:
    contract = discovery.CODEX_SKILL_DISCOVERY_CONTRACT

    assert contract.legacy_root_relpath == "skills"
    assert contract.catalog_relpath == "add-dir/skills"
    assert contract.upstream_revision == "646f7c0a91b8e327d263335da68ae8ef212895ce"
    assert contract.upstream_legacy_root_citation == "codex-rs/ext/skills/src/host_roots.rs:94-113"
    assert contract.verified_binary == "codex-cli 0.153.4"


def test_discovery_probes_forward_explicit_timeouts_to_bounded_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    captured_timeouts: list[float] = []
    captured_stream_limits: list[int] = []

    def run_probe(*_args: object, **kwargs: object) -> probes._BoundedProbeResult:
        timeout = kwargs["timeout_seconds"]
        assert isinstance(timeout, float)
        captured_timeouts.append(timeout)
        stream_limit = kwargs.get("stream_limit_bytes", probes._CODEX_PROBE_STREAM_LIMIT)
        assert isinstance(stream_limit, int)
        captured_stream_limits.append(stream_limit)
        return probes._BoundedProbeResult(0, b"codex-cli 0.153.4\n", b"")

    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", run_probe)

    raw, normalized, errors = discovery.probe_codex_version(
        executable="bound-codex",
        env={},
        cwd=str(tmp_path),
        timeout_seconds=11.5,
    )
    assert (raw, normalized, errors) == ("codex-cli 0.153.4", "0.153.4", [])

    discovery.attest_catalog_discovery(
        probe_command=("bound-codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_entries=expected_entries,
        version="0.153.4",
        timeout_seconds=12.5,
    )

    assert captured_timeouts == [11.5, 12.5]
    assert captured_stream_limits == [
        probes._CODEX_PROBE_STREAM_LIMIT,
        discovery._CODEX_DISCOVERY_STREAM_LIMIT,
    ]
    timeout_parameter = inspect.signature(probes._run_bounded_codex_probe).parameters[
        "timeout_seconds"
    ]
    assert timeout_parameter.default == probes._CODEX_PROBE_TIMEOUT_SECONDS
