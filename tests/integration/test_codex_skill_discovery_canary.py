"""Opt-in real-loader canary for Codex's managed interactive skill catalog."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pytest
from packaging.version import Version

from autoskillit.core import (
    CmdSpec,
    ManagedSessionHome,
    SkillExecutionRole,
    normalize_codex_cli_version,
    pkg_root,
)
from autoskillit.execution.backends._codex_discovery import (
    CODEX_SKILL_DISCOVERY_CONTRACT,
    attest_catalog_discovery,
    parse_skills_instructions,
    probe_codex_version,
)
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.workspace import (
    DefaultSessionSkillManager,
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillsDirectoryProvider,
    compile_session_skill_catalog,
)
from tests.execution.backends._live_codex_parent import CODEX_LIVE_PROCESS_ENV_ALLOWLIST

pytestmark = [pytest.mark.large, pytest.mark.canary]

_CANARY_ENV = "AUTOSKILLIT_CODEX_DISCOVERY_CANARY"
_BINARY_ENV = "AUTOSKILLIT_CODEX_CANARY_BINARY"
_EXPECTED_VERSION_ENV = "AUTOSKILLIT_CODEX_CANARY_EXPECTED_VERSION"
_PROBE_TIMEOUT_SECONDS = 30
_OUTPUT_CAP = 64 * 1024


@dataclass(frozen=True, slots=True)
class _SelectedCodex:
    binary: Path
    raw_version: str
    normalized_version: str


def _selected_codex() -> _SelectedCodex:
    if os.environ.get(_CANARY_ENV) != "1":
        pytest.skip(f"set {_CANARY_ENV}=1 to run the Codex discovery canary")
    if os.name != "posix":
        pytest.skip("Codex discovery canary requires POSIX managed-home symlinks")

    requested = os.environ.get(_BINARY_ENV, "")
    if requested:
        binary = Path(requested)
        if not binary.is_absolute():
            pytest.fail(f"{_BINARY_ENV} must be an absolute executable path: {requested!r}")
    else:
        resolved = shutil.which("codex")
        if resolved is None:
            pytest.fail("Codex discovery canary requested but the Codex CLI is not present")
        binary = Path(resolved).resolve()

    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.fail(
            f"Codex discovery canary requested with a missing or non-executable binary: {binary}"
        )

    raw_version, normalized_version, errors = probe_codex_version(
        executable=str(binary),
        env=os.environ,
        cwd=str(Path.cwd()),
        timeout_seconds=_PROBE_TIMEOUT_SECONDS,
    )
    if errors:
        pytest.fail("; ".join(errors))
    assert raw_version
    assert normalized_version

    minimum_version = CodexBackend().capabilities.min_version
    if Version(normalized_version) < Version(minimum_version):
        pytest.fail(
            "Codex discovery canary requested with an unsupported version: "
            f"raw={raw_version!r}; normalized={normalized_version!r}; "
            f"minimum={minimum_version!r}"
        )
    expected = os.environ.get(_EXPECTED_VERSION_ENV, "")
    if expected:
        try:
            expected_normalized = normalize_codex_cli_version(expected)
        except ValueError as exc:
            pytest.fail(f"{_EXPECTED_VERSION_ENV} is not a normalized Codex version: {exc}")
        if normalized_version != expected_normalized:
            pytest.fail(
                "Codex discovery canary selected the wrong Codex version: "
                f"raw={raw_version!r}; normalized={normalized_version!r}; "
                f"expected={expected_normalized!r}"
            )
    return _SelectedCodex(binary, raw_version, normalized_version)


@pytest.fixture
def selected_codex() -> _SelectedCodex:
    return _selected_codex()


def _write_profile_skill(
    source_skills: Path,
    name: str,
    *,
    body: str = "Follow this profile contract.\n",
    frontmatter: str = "",
) -> None:
    skill_md = source_skills / name / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(
        f"---\nname: {name}\ndescription: Canary profile fixture.\n{frontmatter}---\n{body}",
        encoding="utf-8",
    )


def _profile_source(tmp_path: Path, names: Sequence[str]) -> Path:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text(
        'cli_auth_credentials_store = "keyring"\n', encoding="utf-8"
    )
    for name in names:
        _write_profile_skill(source_home / "skills", name)
    return source_home


def _managed_catalog(
    *,
    tmp_path: Path,
    source_home: Path,
    session_id: str,
    bundled_names: frozenset[str],
):
    project = tmp_path / "project"
    project.mkdir()
    manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=project,
        persistent_roots={"codex": tmp_path / "persistent-sessions"},
    )
    backend = CodexBackend(source_codex_home=source_home)
    catalog = DefaultSkillResolver().list_effective(project, SkillExecutionRole.SESSION)
    admitted_catalog = EffectiveSkillCatalog(
        skills=tuple(skill for skill in catalog.skills if skill.name in bundled_names),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = manager._provider.catalog_projection_context(
        admitted_catalog,
        project,
        backend=backend,
        durable_scripts_root=pkg_root(),
    )
    compilation = compile_session_skill_catalog(admitted_catalog, backend)
    return project, backend, manager.managed_session(session_id, compilation, context)


def _expected_entries(catalog: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (entry.name, f"{entry.name}/SKILL.md")
        for entry in sorted(catalog.iterdir())
        if (entry / "SKILL.md").is_file()
    )


def _prompt_input_command(spec, binary: Path) -> tuple[str, ...]:
    command = [str(binary)]
    index = 1
    while index < len(spec.cmd):
        token = spec.cmd[index]
        if token in {"--profile", "-c"}:
            command.extend((token, spec.cmd[index + 1]))
            index += 2
            continue
        index += 1
    command.extend(CODEX_SKILL_DISCOVERY_CONTRACT.prompt_probe)
    return tuple(command)


def _isolated_child_env(spec_env: Mapping[str, str], generated_home: Path) -> dict[str, str]:
    env = {
        key: value for key, value in spec_env.items() if key in CODEX_LIVE_PROCESS_ENV_ALLOWLIST
    }
    env.update(
        {
            "HOME": str(generated_home),
            "CODEX_HOME": str(generated_home),
            "CODEX_SQLITE_HOME": str(generated_home),
            "XDG_CONFIG_HOME": str(generated_home / ".config"),
            "XDG_DATA_HOME": str(generated_home / ".local" / "share"),
        }
    )
    return env


def _run_prompt_input(
    *,
    spec,
    binary: Path,
    generated_home: Path,
    project: Path,
) -> str:
    assert Path(spec.env["CODEX_HOME"]) == generated_home
    assert Path(spec.env["CODEX_SQLITE_HOME"]) == generated_home
    result = subprocess.run(
        _prompt_input_command(spec, binary),
        cwd=Path(spec.cwd) if spec.cwd else project,
        env=_isolated_child_env(spec.env, generated_home),
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-_OUTPUT_CAP:]
    return result.stdout


def _write_diagnostics(
    project: Path,
    selected: _SelectedCodex,
    **extra: object,
) -> Path:
    diagnostics = project / ".autoskillit" / "temp" / uuid.uuid4().hex[:16]
    diagnostics.mkdir(parents=True)
    output = {
        "binary": str(selected.binary),
        "raw_codex_version": selected.raw_version,
        "normalized_codex_version": selected.normalized_version,
        **extra,
    }
    path = diagnostics / "codex-skill-discovery.json"
    path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _interactive_spec(
    backend: CodexBackend,
    managed: ManagedSessionHome,
    project: Path,
) -> CmdSpec:
    return backend.build_interactive_cmd(
        generated_home=managed.generated_home,
        add_dirs=(managed.skills_dir,),
        project_root=project,
    )


def test_installed_codex_discovers_the_session_catalog_through_the_legacy_alias(
    tmp_path: Path,
    selected_codex: _SelectedCodex,
) -> None:
    source_home = _profile_source(tmp_path, ("profile-only",))
    _write_profile_skill(
        source_home / "skills",
        "make-arch-diag",
        body="PROFILE_COPY_SENTINEL\n",
    )
    _write_profile_skill(
        source_home / "skills",
        "join-required",
        frontmatter=("semantic_version: 1\nsemantic_requirements:\n  join:\n    required: true\n"),
    )
    project, backend, managed_session = _managed_catalog(
        tmp_path=tmp_path,
        source_home=source_home,
        session_id="legacy-alias",
        bundled_names=frozenset({"make-arch-diag"}),
    )

    with managed_session as managed:
        catalog = Path(managed.skills_dir.path) / "skills"
        expected_entries = _expected_entries(catalog)
        assert {name for name, _ in expected_entries} == {"make-arch-diag", "profile-only"}
        assert "PROFILE_COPY_SENTINEL" in (catalog / "make-arch-diag" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert {
            record["skill"]: record["operation"]
            for record in managed.unavailability_payload["unavailable"]
        } == {"join-required": "required_join"}

        spec = _interactive_spec(backend, managed, project)
        discovered = parse_skills_instructions(
            _run_prompt_input(
                spec=spec,
                binary=selected_codex.binary,
                generated_home=managed.generated_home,
                project=project,
            )
        )
        legacy_root = managed.generated_home / CODEX_SKILL_DISCOVERY_CONTRACT.legacy_root_relpath
        assert legacy_root.is_symlink()
        assert legacy_root.resolve() == catalog.resolve()
        assert legacy_root in discovered.roots
        assert "join-required" not in discovered.names
        for name, _ in expected_entries:
            assert name in discovered.names
            assert (
                discovered.paths[name].resolve(strict=True)
                == (catalog / name / "SKILL.md").resolve()
            )

        diagnostic = _write_diagnostics(
            project,
            selected_codex,
            catalog=str(catalog),
            legacy_root=str(legacy_root),
            discovered_names=sorted(discovered.names),
        )
        assert diagnostic.is_file()


def test_two_concurrent_session_homes_do_not_share_catalogs(
    tmp_path: Path,
    selected_codex: _SelectedCodex,
) -> None:
    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    alpha_root.mkdir()
    beta_root.mkdir()
    alpha_project, alpha_backend, alpha_session = _managed_catalog(
        tmp_path=alpha_root,
        source_home=_profile_source(alpha_root, ("alpha-only",)),
        session_id="alpha-session",
        bundled_names=frozenset(),
    )
    beta_project, beta_backend, beta_session = _managed_catalog(
        tmp_path=beta_root,
        source_home=_profile_source(beta_root, ("beta-only",)),
        session_id="beta-session",
        bundled_names=frozenset(),
    )

    with ExitStack() as sessions:
        alpha = sessions.enter_context(alpha_session)
        beta = sessions.enter_context(beta_session)
        alpha_discovered = parse_skills_instructions(
            _run_prompt_input(
                spec=_interactive_spec(alpha_backend, alpha, alpha_project),
                binary=selected_codex.binary,
                generated_home=alpha.generated_home,
                project=alpha_project,
            )
        )
        beta_discovered = parse_skills_instructions(
            _run_prompt_input(
                spec=_interactive_spec(beta_backend, beta, beta_project),
                binary=selected_codex.binary,
                generated_home=beta.generated_home,
                project=beta_project,
            )
        )

        assert "alpha-only" in alpha_discovered.names
        assert "beta-only" not in alpha_discovered.names
        assert "beta-only" in beta_discovered.names
        assert "alpha-only" not in beta_discovered.names
        _write_diagnostics(
            alpha_project,
            selected_codex,
            alpha_catalog=str(Path(alpha.skills_dir.path) / "skills"),
            beta_catalog=str(Path(beta.skills_dir.path) / "skills"),
        )


def test_prelaunch_attestation_matches_the_real_loader(
    tmp_path: Path,
    selected_codex: _SelectedCodex,
) -> None:
    source_home = _profile_source(tmp_path, ("profile-only",))
    project, backend, managed_session = _managed_catalog(
        tmp_path=tmp_path,
        source_home=source_home,
        session_id="attestation",
        bundled_names=frozenset({"make-arch-diag"}),
    )

    with managed_session as managed:
        catalog = Path(managed.skills_dir.path) / "skills"
        expected_entries = _expected_entries(catalog)
        spec = _interactive_spec(backend, managed, project)
        probe_command = _prompt_input_command(spec, selected_codex.binary)
        env = _isolated_child_env(spec.env, managed.generated_home)
        assert (
            attest_catalog_discovery(
                probe_command=probe_command,
                env=env,
                cwd=spec.cwd or str(project),
                catalog_dir=catalog,
                expected_entries=expected_entries,
                version=selected_codex.normalized_version,
                timeout_seconds=_PROBE_TIMEOUT_SECONDS,
            )
            == []
        )

        removed_name, _ = expected_entries[0]
        (catalog / removed_name).rename(catalog / f"{removed_name}-removed")
        errors = attest_catalog_discovery(
            probe_command=probe_command,
            env=env,
            cwd=spec.cwd or str(project),
            catalog_dir=catalog,
            expected_entries=expected_entries,
            version=selected_codex.normalized_version,
            timeout_seconds=_PROBE_TIMEOUT_SECONDS,
        )
        assert errors
        assert removed_name in "\n".join(errors)
        _write_diagnostics(
            project,
            selected_codex,
            catalog=str(catalog),
            attestation_errors=errors,
        )
