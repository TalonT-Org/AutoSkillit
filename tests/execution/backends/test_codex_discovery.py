"""Pinned Codex skill-discovery parsing and catalog-attestation contracts."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from autoskillit.execution.backends import _codex_discovery as discovery
from autoskillit.execution.backends import _codex_discovery_attestation as attestation
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
    route = discovery.CODEX_MANAGED_HOME_ROUTE
    catalog_dir = route.catalog_dir(session_home)
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
    discovery_root = route.discovery_root(session_home)
    assert discovery_root is not None
    discovery_root.symlink_to(route.alias_target, target_is_directory=True)
    return catalog_dir, tuple(expected_entries)


def _loader_output(name: str, catalog_dir: Path) -> str:
    discovery_root = _discovery_root(catalog_dir)
    return _loader_output_at_root(name, catalog_dir, discovery_root).replace(
        str(discovery_root / ".system"),
        str(catalog_dir / ".system"),
    )


def _discovery_root(catalog_dir: Path) -> Path:
    discovery_root = discovery.CODEX_MANAGED_HOME_ROUTE.discovery_root(catalog_dir.parent.parent)
    assert discovery_root is not None
    return discovery_root


def _loader_output_at_root(name: str, catalog_dir: Path, discovery_root: Path) -> str:
    document = _fixture_document(name)
    fixture_version = "v0130" if "v0130" in name else "v0153"
    source_root = _FIXTURE_ROOT / fixture_version / "home" / "skills"
    return _with_skills_text(
        document,
        _skills_text(document).replace(str(source_root), str(discovery_root)),
    )


def _loader_output_with_extra_root(name: str, catalog_dir: Path, extra_root: Path) -> str:
    document = json.loads(_loader_output(name, catalog_dir))
    assert isinstance(document, list)
    return _with_skills_text(
        document,
        _skills_text(document).replace(
            "### Available skills",
            f"- `r9` = `{extra_root}`\n### Available skills",
        ),
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


@pytest.mark.parametrize(
    "use_managed_alias",
    [False, True],
    ids=["direct-catalog-root", "managed-alias-root"],
)
def test_attest_catalog_discovery_accepts_real_loader_fixture_at_expected_root(
    tmp_path: Path,
    use_managed_alias: bool,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    expected_discovery_root = _discovery_root(catalog_dir) if use_managed_alias else catalog_dir
    route = (
        discovery.CODEX_MANAGED_HOME_ROUTE
        if use_managed_alias
        else discovery.CODEX_PROJECTED_HOME_ROUTE
    )
    output = _loader_output_at_root(
        "discovery_prompt_input_v0153.json",
        catalog_dir,
        expected_discovery_root,
    )
    if use_managed_alias:
        output = output.replace(
            str(expected_discovery_root / ".system"), str(catalog_dir / ".system")
        )
    command, env = _install_prompt_stub(
        tmp_path,
        output,
    )

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=expected_discovery_root,
        route=route,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    assert errors == ()
    assert (catalog_dir / ".system" / "native" / "SKILL.md").is_file()


def test_attest_catalog_discovery_rejects_foreign_managed_root(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    scope = catalog_dir.parents[2]
    other_home = scope / "other-home"
    other_catalog = other_home / "add-dir" / "skills"
    other_catalog.mkdir(parents=True)
    foreign_root = other_home / "skills"
    foreign_root.symlink_to(other_catalog, target_is_directory=True)
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output_with_extra_root(
            "discovery_prompt_input_v0153.json",
            catalog_dir,
            foreign_root,
        ),
    )

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
        managed_root_scope=scope,
    ).errors

    assert len(errors) == 1
    assert "root-policy rejects additional managed root" in errors[0]


def test_attest_catalog_discovery_rejects_scope_root_itself(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    scope = catalog_dir.parents[2]
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output_with_extra_root("discovery_prompt_input_v0153.json", catalog_dir, scope),
    )

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
        managed_root_scope=scope,
    ).errors

    assert len(errors) == 1
    assert "root-policy rejects additional managed root" in errors[0]


def test_attest_catalog_discovery_accepts_system_cache_under_expected_root(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output("discovery_prompt_input_v0153.json", catalog_dir),
    )

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
        managed_root_scope=catalog_dir.parents[2],
    ).errors

    assert errors == ()


def test_attest_catalog_discovery_ignores_roots_outside_scope(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    outside_root = tmp_path.parent / "outside" / ".agents" / "skills"
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output_with_extra_root(
            "discovery_prompt_input_v0153.json",
            catalog_dir,
            outside_root,
        ),
    )

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
        managed_root_scope=catalog_dir.parents[2],
    ).errors

    assert errors == ()


def test_attest_catalog_discovery_reports_missing_expected_name_with_context(
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

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    diagnostic = "\n".join(errors)
    assert "missing expected names ['beta']" in diagnostic
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

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    diagnostic = "\n".join(errors)
    assert "unreadable: FileNotFoundError:" in diagnostic
    assert str(tmp_path / "missing" / "beta" / "SKILL.md") in diagnostic


@pytest.mark.parametrize(
    "root_failure",
    ["missing", "wrong", "duplicate"],
)
def test_attest_catalog_discovery_rejects_invalid_explicit_discovery_root(
    tmp_path: Path,
    root_failure: str,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    if root_failure == "missing":
        expected_discovery_root = catalog_dir
        output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir)
        expected_fragment = "roots do not contain expected discovery root"
    elif root_failure == "wrong":
        expected_discovery_root = tmp_path / "wrong-root"
        expected_discovery_root.mkdir()
        output = _loader_output_at_root(
            "discovery_prompt_input_v0153.json",
            catalog_dir,
            expected_discovery_root,
        )
        expected_fragment = "expected root does not resolve to the catalog"
    else:
        expected_discovery_root = catalog_dir
        document = json.loads(
            _loader_output_at_root(
                "discovery_prompt_input_v0153.json",
                catalog_dir,
                expected_discovery_root,
            )
        )
        assert isinstance(document, list)
        output = _with_skills_text(
            document,
            _skills_text(document).replace(
                "### Available skills",
                f"- `r9` = `{expected_discovery_root}`\n### Available skills",
            ),
        )
        expected_fragment = "roots contain duplicate expected discovery root"
    command, env = _install_prompt_stub(tmp_path, output)

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=expected_discovery_root,
        route=discovery.CODEX_PROJECTED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    diagnostic = "\n".join(errors)
    assert expected_fragment in diagnostic
    assert str(expected_discovery_root) in diagnostic
    assert "roots=" in diagnostic
    assert f"catalog={catalog_dir}" in diagnostic
    assert "version=0.153.4" in diagnostic
    assert all(len(error) <= 2_000 for error in errors)


def test_attest_catalog_discovery_requires_absolute_explicit_root_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)

    def probe_must_not_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("relative explicit root should fail before probing Codex")

    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", probe_must_not_run)

    errors = attestation.attest(
        probe_command=("codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=Path("skills"),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    assert len(errors) == 1
    assert "expected root must be absolute" in errors[0]


def test_attest_catalog_discovery_rejects_symlinked_catalog_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    catalog_alias = tmp_path / "catalog-alias"
    catalog_alias.symlink_to(catalog_dir, target_is_directory=True)

    def probe_must_not_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("symlinked catalog should fail before probing Codex")

    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", probe_must_not_run)

    errors = attestation.attest(
        probe_command=("codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_alias,
        expected_discovery_root=catalog_alias,
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    assert len(errors) == 1
    assert "managed catalog must be a canonical real directory" in errors[0]


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

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    assert any("misplaced expected paths" in error and "alpha=" in error for error in errors)


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

    errors = attestation.attest(
        probe_command=("codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    diagnostic = "\n".join(errors)
    assert expected_fragment in diagnostic
    assert f"catalog={catalog_dir}" in diagnostic
    assert "version=0.153.4" in diagnostic
    assert "timeout_seconds=30.0" in diagnostic


def test_attest_catalog_discovery_rejects_missing_managed_path_before_probe(
    tmp_path: Path,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    (catalog_dir / "beta" / "SKILL.md").unlink()

    errors = attestation.attest(
        probe_command=("missing-codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    assert any("catalog validation failed" in error for error in errors)
    assert any("beta/SKILL.md" in error for error in errors)


def test_attest_catalog_discovery_rejects_in_probe_managed_catalog_edit(tmp_path: Path) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    command, env = _install_prompt_stub(
        tmp_path,
        _loader_output("discovery_prompt_input_v0130.json", catalog_dir),
        mutate_path=catalog_dir / "alpha" / "SKILL.md",
    )

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

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

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    ).errors

    diagnostic = "\n".join(errors)
    assert "could not revalidate the managed catalog: OSError: catalog read failed" in diagnostic
    assert "mutated the managed catalog" not in diagnostic


def test_discovery_routes_and_contract_pin_verified_upstream_revision() -> None:
    from autoskillit.core import UpstreamSupportStatus

    managed_route = discovery.CODEX_MANAGED_HOME_ROUTE
    projected_route = discovery.CODEX_PROJECTED_HOME_ROUTE

    assert managed_route.discovery_root_relpath == "skills"
    assert managed_route.catalog_relpath == "add-dir/skills"
    assert managed_route.upstream_status is UpstreamSupportStatus.DEPRECATED
    assert managed_route.tracking_issue == 4717
    assert projected_route.catalog_relpath == "skills"
    assert projected_route.discovery_root_relpath == "skills"


def test_select_interactive_discovery_route(tmp_path: Path) -> None:
    from dataclasses import replace

    from autoskillit.core import PluginLoadMode
    from tests.execution.backends._plugin_binding import plugin_binding

    projected_binding = replace(
        plugin_binding(tmp_path / "projected-plugin"),
        load_mode=PluginLoadMode.PROJECTED_HOME,
    )

    assert (
        discovery.select_interactive_discovery_route(
            generated_home=tmp_path / "generated-home",
            plugin_binding=projected_binding,
        )
        is discovery.CODEX_MANAGED_HOME_ROUTE
    )
    assert (
        discovery.select_interactive_discovery_route(
            generated_home=None,
            plugin_binding=projected_binding,
        )
        is discovery.CODEX_PROJECTED_HOME_ROUTE
    )
    assert (
        discovery.select_interactive_discovery_route(generated_home=None, plugin_binding=None)
        is None
    )


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

    attestation.attest(
        probe_command=("bound-codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=_discovery_root(catalog_dir),
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
        timeout_seconds=12.5,
    )

    assert captured_timeouts == [11.5, 12.5]
    assert captured_stream_limits == [
        probes._CODEX_PROBE_STREAM_LIMIT,
        discovery._CODEX_DISCOVERY_STREAM_LIMIT,
    ]


@pytest.mark.parametrize("reported_primary", ("alias", "canonical"))
def test_managed_attestation_accepts_one_exact_alias_or_canonical_primary(
    tmp_path: Path,
    reported_primary: str,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    alias_root = _discovery_root(catalog_dir)
    primary = alias_root if reported_primary == "alias" else catalog_dir
    output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir)
    output = output.replace(str(alias_root / ".system"), str(catalog_dir / ".system"))
    output = output.replace(str(alias_root), str(primary))
    command, env = _install_prompt_stub(tmp_path, output)

    result = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=alias_root,
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
        managed_root_scope=tmp_path,
    )

    assert result.errors == ()
    assert result.pre_spawn_check is not None
    result.pre_spawn_check()


@pytest.mark.parametrize(
    "alias_state",
    (
        "missing",
        "dangling",
        "directory",
        "wrong-target",
        "absolute-token",
        "wrong-relative-token",
    ),
)
def test_managed_attestation_rejects_invalid_alias_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_state: str,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    alias_root = _discovery_root(catalog_dir)
    alias_root.unlink()
    if alias_state == "missing":
        pass
    elif alias_state == "dangling":
        alias_root.symlink_to("missing-catalog", target_is_directory=True)
    elif alias_state == "directory":
        alias_root.mkdir()
    elif alias_state == "wrong-target":
        wrong_target = tmp_path / "wrong-target"
        wrong_target.mkdir()
        alias_root.symlink_to(wrong_target, target_is_directory=True)
    elif alias_state == "absolute-token":
        alias_root.symlink_to(catalog_dir, target_is_directory=True)
    else:
        alias_root.symlink_to("./add-dir/skills", target_is_directory=True)

    def probe_must_not_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid managed alias must fail before prompt-input")

    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", probe_must_not_run)

    result = attestation.attest(
        probe_command=("codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=alias_root,
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    )

    assert result.errors
    assert result.pre_spawn_check is None
    assert any(str(alias_root) in message for message in result.errors)


@pytest.mark.parametrize(
    "case",
    (
        "missing-primary",
        "both-primary-spellings",
        "duplicate-alias-primary",
        "duplicate-canonical-primary",
        "non-exact-alias",
        "non-exact-alias-dot",
        "system-descendant",
        "additional-alias-to-catalog",
    ),
)
def test_managed_attestation_rejects_root_policy_variants(
    tmp_path: Path,
    case: str,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    alias_root = _discovery_root(catalog_dir)
    output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir)
    if case == "missing-primary":
        output = output.replace(str(alias_root), str(tmp_path / "other-root"), 1)
    elif case == "both-primary-spellings":
        output = _loader_output_with_extra_root(
            "discovery_prompt_input_v0153.json", catalog_dir, catalog_dir
        )
    elif case == "duplicate-alias-primary":
        output = _loader_output_with_extra_root(
            "discovery_prompt_input_v0153.json", catalog_dir, alias_root
        )
    elif case == "duplicate-canonical-primary":
        document = json.loads(output)
        assert isinstance(document, list)
        text = _skills_text(document).replace(str(alias_root), str(catalog_dir), 1)
        output = _with_skills_text(
            document,
            text.replace(
                "### Available skills",
                f"- `r9` = `{catalog_dir}`\n### Available skills",
            ),
        )
    elif case == "non-exact-alias":
        output = output.replace(str(alias_root), f"{alias_root}/", 1)
    elif case == "non-exact-alias-dot":
        output = output.replace(str(alias_root), f"{alias_root}/.", 1)
    elif case == "system-descendant":
        output = output.replace(str(catalog_dir / ".system"), str(catalog_dir / ".system/child"))
    else:
        extra_alias = tmp_path / "additional-alias"
        extra_alias.symlink_to(catalog_dir, target_is_directory=True)
        output = _loader_output_with_extra_root(
            "discovery_prompt_input_v0153.json", catalog_dir, extra_alias
        )
    command, env = _install_prompt_stub(tmp_path, output)

    errors = attestation.attest(
        probe_command=command,
        env=env,
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=alias_root,
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
        managed_root_scope=tmp_path,
    ).errors

    assert any("root-policy" in error for error in errors)


@pytest.mark.parametrize("probe_result", ("success", "nonzero"))
def test_managed_attestation_rejects_same_token_alias_replacement_after_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_result: str,
) -> None:
    catalog_dir, expected_entries = _catalog(tmp_path)
    alias_root = _discovery_root(catalog_dir)
    original_inode = alias_root.lstat().st_ino
    replacement = tmp_path / "replacement-alias"
    output = _loader_output("discovery_prompt_input_v0153.json", catalog_dir)

    def replace_alias(*_args: object, **_kwargs: object) -> probes._BoundedProbeResult:
        replacement.symlink_to("add-dir/skills", target_is_directory=True)
        os.replace(replacement, alias_root)
        if probe_result == "nonzero":
            return probes._BoundedProbeResult(23, b"", b"expected failure")
        return probes._BoundedProbeResult(0, output.encode(), b"")

    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", replace_alias)
    result = attestation.attest(
        probe_command=("codex", "debug", "prompt-input"),
        env={},
        cwd=str(tmp_path),
        catalog_dir=catalog_dir,
        expected_discovery_root=alias_root,
        route=discovery.CODEX_MANAGED_HOME_ROUTE,
        expected_entries=expected_entries,
        version="0.153.4",
    )
    assert alias_root.lstat().st_ino != original_inode
    assert any("mutated the managed alias" in error for error in result.errors)
    if probe_result == "nonzero":
        assert any("exited with status 23" in error for error in result.errors)
