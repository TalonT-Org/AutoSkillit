"""Opt-in real-loader canary for Codex's managed interactive skill catalog."""

from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path

import pytest

from autoskillit.core import (
    CmdSpec,
    ManagedSessionHome,
    OutputFormat,
    SkillExecutionRole,
    pkg_root,
)

# Reuse the production JSON-RPC frame encoder rather than duplicating it here;
# the app-server's own non-standard framing convention omits the "jsonrpc"
# member (see CodexAppServerDriver), which this encoder already does.
from autoskillit.execution.backends._codex.app_server import _encode as _jsonrpc_line
from autoskillit.execution.backends._codex_discovery import (
    CODEX_SKILL_DISCOVERY_CONTRACT,
    attest_catalog_discovery,
    parse_skills_instructions,
)
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.execution.process import run_managed_async
from autoskillit.workspace import (
    DefaultSessionSkillManager,
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillsDirectoryProvider,
    compile_session_skill_catalog,
)
from tests.execution.backends._live_codex_parent import CODEX_LIVE_PROCESS_ENV_ALLOWLIST
from tests.integration._codex_canary_helpers import SelectedCodex as _SelectedCodex
from tests.integration._codex_canary_helpers import select_canary_codex

pytestmark = [pytest.mark.large, pytest.mark.canary]

_CANARY_ENV = "AUTOSKILLIT_CODEX_DISCOVERY_CANARY"
_PROBE_TIMEOUT_SECONDS = 30
_APP_SERVER_TIMEOUT_SECONDS = 60
_OUTPUT_CAP = 64 * 1024


def _selected_codex() -> _SelectedCodex:
    return select_canary_codex(canary_env=_CANARY_ENV)


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


def _app_server_command(spec: CmdSpec, binary: Path) -> tuple[str, ...]:
    """Replace the bare ``codex`` argv[0] with the resolved canary binary.

    Unlike ``_prompt_input_command``, ``build_skill_session_cmd`` never puts
    ``--profile``/``-c`` flags that depend on rewriting into ``spec.cmd`` for
    an app-server launch (profile selection flows through env/prompt only),
    so every remaining token is passed through unchanged.
    """
    assert spec.cmd and spec.cmd[0] == "codex", spec.cmd
    return (str(binary), *spec.cmd[1:])


class _RegisteredRootsProbeDriver:
    """Hand-rolled ``LineDriver`` proving the registered-roots property: a
    catalog registered via ``skills/extraRoots/set`` shows up in
    ``skills/list``, and clearing it makes those names disappear again
    (mirrors ``codex-rs/app-server/tests/suite/v2/skills_list.rs:936-1004``).

    Deliberately not ``CodexAppServerDriver`` — that state machine always
    proceeds through ``thread/start``/``turn/start`` to a full model turn,
    whereas this handshake needs a second ``extraRoots/set`` + ``skills/list``
    round trip and must stop before any thread/turn request. Wire
    conventions (no top-level ``jsonrpc`` member, exact camelCase field
    names, id sequencing) match ``CodexAppServerDriver`` exactly — see
    ``tests/execution/backends/test_codex_app_server_driver.py``.
    """

    _INITIALIZE_ID = 1
    _EXTRA_ROOTS_SET_ID = 2
    _SKILLS_LIST_FIRST_ID = 3
    _EXTRA_ROOTS_CLEAR_ID = 4
    _SKILLS_LIST_SECOND_ID = 5

    def __init__(
        self, *, session_home: str, catalog_root: str, cwd: str, client_version: str
    ) -> None:
        self.session_home = session_home
        self.catalog_root = catalog_root
        self.cwd = cwd
        self.client_version = client_version
        self._phase = "initialize"
        self.finished = False
        self.failure: str | None = None
        self.first_rows: dict[str, str | None] = {}
        self.second_names: frozenset[str] = frozenset()

    # -- LineDriver protocol -------------------------------------------------

    def initial_lines(self) -> tuple[str, ...]:
        return (
            _jsonrpc_line(
                {
                    "id": self._INITIALIZE_ID,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "autoskillit-canary",
                            "title": "AutoSkillit Canary",
                            "version": self.client_version,
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                }
            ),
        )

    def on_line(self, line: str) -> tuple[str, ...]:
        if self.failure is not None or self.finished:
            return ()
        stripped = line.strip()
        if not stripped:
            return ()
        try:
            obj = json.loads(stripped)
        except ValueError:
            self._fail(f"malformed JSON-RPC frame: {line!r}")
            return ()
        if not isinstance(obj, dict):
            self._fail(f"malformed JSON-RPC frame (not an object): {line!r}")
            return ()
        method = obj.get("method")
        if method is not None and "id" in obj:
            response = _jsonrpc_line(
                {
                    "id": obj.get("id"),
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )
            self._fail(f"unsupported server request {method!r} (id={obj.get('id')!r})")
            return (response,)
        if method is not None:
            return ()  # unrelated notification; ignored
        return self._handle_response(obj)

    # -- inbound dispatch -----------------------------------------------------

    def _handle_response(self, obj: Mapping[str, object]) -> tuple[str, ...]:
        phase_requests: dict[
            str, tuple[int, Callable[[Mapping[str, object]], tuple[str, ...]]]
        ] = {
            "initialize": (self._INITIALIZE_ID, self._accept_initialize),
            "extra_roots_set": (self._EXTRA_ROOTS_SET_ID, self._accept_extra_roots_set),
            "skills_list_first": (self._SKILLS_LIST_FIRST_ID, self._accept_skills_list_first),
            "extra_roots_clear": (self._EXTRA_ROOTS_CLEAR_ID, self._accept_extra_roots_clear),
            "skills_list_second": (self._SKILLS_LIST_SECOND_ID, self._accept_skills_list_second),
        }
        pending = phase_requests.get(self._phase)
        if pending is None:
            self._fail(f"unexpected response after completion handshake: {obj!r}")
            return ()
        expected_id, accept = pending
        response_id = obj.get("id")
        if response_id != expected_id:
            self._fail(
                f"unexpected response id={response_id!r}, expected id={expected_id!r} "
                f"for phase={self._phase!r}"
            )
            return ()
        if "error" in obj:
            self._fail(
                f"server error for id={expected_id} phase={self._phase!r}: {obj.get('error')!r}"
            )
            return ()
        if "result" not in obj:
            self._fail(f"malformed response (neither result nor error present): {obj!r}")
            return ()
        return accept(obj["result"])

    def _accept_initialize(self, result: Mapping[str, object]) -> tuple[str, ...]:
        codex_home = result.get("codexHome")
        if codex_home != self.session_home:
            self._fail(
                f"initialize codexHome {codex_home!r} does not match session home "
                f"{self.session_home!r}"
            )
            return ()
        self._phase = "extra_roots_set"
        return (
            _jsonrpc_line({"method": "initialized"}),
            _jsonrpc_line(
                {
                    "id": self._EXTRA_ROOTS_SET_ID,
                    "method": "skills/extraRoots/set",
                    "params": {"extraRoots": [self.catalog_root]},
                }
            ),
        )

    def _accept_extra_roots_set(self, result: Mapping[str, object]) -> tuple[str, ...]:
        del result  # presence of a (non-error) result is the acknowledgement
        self._phase = "skills_list_first"
        return (self._skills_list_request(self._SKILLS_LIST_FIRST_ID),)

    def _accept_skills_list_first(self, result: Mapping[str, object]) -> tuple[str, ...]:
        rows = self._extract_rows(result)
        if rows is None:
            return ()
        self.first_rows = rows
        self._phase = "extra_roots_clear"
        return (
            _jsonrpc_line(
                {
                    "id": self._EXTRA_ROOTS_CLEAR_ID,
                    "method": "skills/extraRoots/set",
                    "params": {"extraRoots": []},
                }
            ),
        )

    def _accept_extra_roots_clear(self, result: Mapping[str, object]) -> tuple[str, ...]:
        del result
        self._phase = "skills_list_second"
        return (self._skills_list_request(self._SKILLS_LIST_SECOND_ID),)

    def _accept_skills_list_second(self, result: Mapping[str, object]) -> tuple[str, ...]:
        rows = self._extract_rows(result)
        if rows is None:
            return ()
        self.second_names = frozenset(rows)
        self.finished = True
        return ()

    def _skills_list_request(self, request_id: int) -> str:
        return _jsonrpc_line(
            {
                "id": request_id,
                "method": "skills/list",
                "params": {"cwds": [self.cwd], "forceReload": True},
            }
        )

    def _extract_rows(self, result: Mapping[str, object]) -> dict[str, str | None] | None:
        # The real app-server (codex-cli 0.153.4) nests skills/list's
        # per-cwd entries under "data" -- confirmed live against the
        # installed binary; matches CodexAppServerDriver's own
        # _accept_skills_list (app_server.py).
        entries = result.get("data") or []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("cwd") == self.cwd:
                errors = entry.get("errors") or []
                if errors:
                    self._fail(
                        f"skills/list reported loader errors for cwd {self.cwd!r}: {errors!r}"
                    )
                    return None
                return {
                    row["name"]: row.get("path")
                    for row in entry.get("skills") or []
                    if isinstance(row, dict) and isinstance(row.get("name"), str)
                }
        self._fail(f"skills/list result missing an entry for cwd {self.cwd!r}")
        return None

    def _fail(self, diagnostic: str) -> None:
        self.failure = diagnostic


async def _run_registered_roots_probe_for_spec(
    *,
    spec: CmdSpec,
    managed: ManagedSessionHome,
    project: Path,
    binary: Path,
) -> _RegisteredRootsProbeDriver:
    """Drive the registered-roots probe over any already-built app-server
    ``CmdSpec`` -- shared by every launch path (skill-session, food-truck,
    resume) that produces a ``CodexAppServerPlan``, since the probe only
    exercises ``initialize`` + ``skills/extraRoots/set`` + ``skills/list``
    and never depends on which builder produced the plan."""
    assert spec.app_server_plan is not None
    plan = spec.app_server_plan
    assert Path(spec.env["CODEX_HOME"]) == managed.generated_home
    assert Path(spec.env["CODEX_SQLITE_HOME"]) == managed.generated_home

    driver = _RegisteredRootsProbeDriver(
        session_home=plan.session_home,
        catalog_root=plan.catalog_root,
        cwd=plan.cwd,
        client_version=plan.client_version,
    )
    await run_managed_async(
        list(_app_server_command(spec, binary)),
        cwd=Path(spec.cwd) if spec.cwd else project,
        env=_isolated_child_env(spec.env, managed.generated_home),
        timeout=_APP_SERVER_TIMEOUT_SECONDS,
        line_driver=driver,
    )
    assert driver.failure is None, driver.failure
    assert driver.finished, "app-server handshake never completed"
    return driver


async def _run_registered_roots_probe(
    *,
    backend: CodexBackend,
    managed: ManagedSessionHome,
    project: Path,
    binary: Path,
    skill_command: str,
) -> _RegisteredRootsProbeDriver:
    spec = backend.build_skill_session_cmd(
        skill_command=skill_command,
        cwd=str(project),
        completion_marker="%%DONE%%",
        model=None,
        plugin_binding=None,
        output_format=OutputFormat.JSON,
        add_dirs=(managed.skills_dir,),
    )
    return await _run_registered_roots_probe_for_spec(
        spec=spec, managed=managed, project=project, binary=binary
    )


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


async def test_installed_app_server_registers_the_session_catalog(
    tmp_path: Path,
    selected_codex: _SelectedCodex,
) -> None:
    """The `skills/extraRoots/set` + `skills/list` registered-roots property
    over the real `codex app-server --listen stdio://` transport: a
    registered catalog root is discoverable, and clearing it makes those
    names disappear again — the legacy `skills` alias plays no part."""
    source_home = _profile_source(tmp_path, ("registered-roots-probe",))
    project, backend, managed_session = _managed_catalog(
        tmp_path=tmp_path,
        source_home=source_home,
        session_id="app-server-roots",
        bundled_names=frozenset({"registered-roots-probe"}),
    )

    with managed_session as managed:
        catalog = Path(managed.skills_dir.path) / "skills"
        expected_entries = _expected_entries(catalog)

        legacy_root = managed.generated_home / CODEX_SKILL_DISCOVERY_CONTRACT.legacy_root_relpath
        assert legacy_root.is_symlink()
        legacy_root.unlink()
        assert catalog.is_dir()

        driver = await _run_registered_roots_probe(
            backend=backend,
            managed=managed,
            project=project,
            binary=selected_codex.binary,
            skill_command="/registered-roots-probe",
        )

        expected_names = {name for name, _ in expected_entries}
        assert expected_names <= set(driver.first_rows)
        for name, relative_path in expected_entries:
            expected_path = str(Path(driver.catalog_root) / relative_path)
            assert driver.first_rows[name] == expected_path

        assert not (expected_names & driver.second_names)

        diagnostic = _write_diagnostics(
            project,
            selected_codex,
            catalog=str(catalog),
            legacy_root=str(legacy_root),
            registered_names=sorted(driver.first_rows),
            cleared_names=sorted(driver.second_names),
        )
        assert diagnostic.is_file()


async def test_installed_food_truck_app_server_registers_the_session_catalog(
    tmp_path: Path,
    selected_codex: _SelectedCodex,
) -> None:
    """The same registered-roots property proven above by
    ``test_installed_app_server_registers_the_session_catalog`` for the
    skill-session launch path holds for the food-truck orchestrator launch
    path (``build_food_truck_cmd``) too: both builders bind the same
    managed catalog into a ``CodexAppServerPlan`` over the identical
    ``_codex_app_server_base`` transport, so a catalog root registered via
    ``skills/extraRoots/set`` is discoverable and clearing it makes those
    names disappear again — again with no dependency on the legacy
    ``skills`` alias."""
    source_home = _profile_source(tmp_path, ("registered-roots-food-truck-probe",))
    project, backend, managed_session = _managed_catalog(
        tmp_path=tmp_path,
        source_home=source_home,
        session_id="app-server-roots-food-truck",
        bundled_names=frozenset({"registered-roots-food-truck-probe"}),
    )

    with managed_session as managed:
        catalog = Path(managed.skills_dir.path) / "skills"
        expected_entries = _expected_entries(catalog)

        legacy_root = managed.generated_home / CODEX_SKILL_DISCOVERY_CONTRACT.legacy_root_relpath
        assert legacy_root.is_symlink()
        legacy_root.unlink()
        assert catalog.is_dir()

        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="registered-roots food-truck probe",
            plugin_binding=None,
            cwd=str(project),
            completion_marker="%%DONE%%",
            managed_skill_catalog=managed.skills_dir,
        )
        driver = await _run_registered_roots_probe_for_spec(
            spec=spec,
            managed=managed,
            project=project,
            binary=selected_codex.binary,
        )

        expected_names = {name for name, _ in expected_entries}
        assert expected_names <= set(driver.first_rows)
        for name, relative_path in expected_entries:
            expected_path = str(Path(driver.catalog_root) / relative_path)
            assert driver.first_rows[name] == expected_path

        assert not (expected_names & driver.second_names)

        diagnostic = _write_diagnostics(
            project,
            selected_codex,
            catalog=str(catalog),
            legacy_root=str(legacy_root),
            registered_names=sorted(driver.first_rows),
            cleared_names=sorted(driver.second_names),
            launch_path="food_truck",
        )
        assert diagnostic.is_file()


async def test_two_app_server_sessions_are_isolated(
    tmp_path: Path,
    selected_codex: _SelectedCodex,
) -> None:
    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    alpha_root.mkdir()
    beta_root.mkdir()
    alpha_project, alpha_backend, alpha_session = _managed_catalog(
        tmp_path=alpha_root,
        source_home=_profile_source(alpha_root, ("alpha-app-server-only",)),
        session_id="alpha-app-server",
        bundled_names=frozenset({"alpha-app-server-only"}),
    )
    beta_project, beta_backend, beta_session = _managed_catalog(
        tmp_path=beta_root,
        source_home=_profile_source(beta_root, ("beta-app-server-only",)),
        session_id="beta-app-server",
        bundled_names=frozenset({"beta-app-server-only"}),
    )

    with ExitStack() as sessions:
        alpha = sessions.enter_context(alpha_session)
        beta = sessions.enter_context(beta_session)

        alpha_driver = await _run_registered_roots_probe(
            backend=alpha_backend,
            managed=alpha,
            project=alpha_project,
            binary=selected_codex.binary,
            skill_command="/alpha-app-server-only",
        )
        beta_driver = await _run_registered_roots_probe(
            backend=beta_backend,
            managed=beta,
            project=beta_project,
            binary=selected_codex.binary,
            skill_command="/beta-app-server-only",
        )

        assert "alpha-app-server-only" in alpha_driver.first_rows
        assert "beta-app-server-only" not in alpha_driver.first_rows
        assert "beta-app-server-only" in beta_driver.first_rows
        assert "alpha-app-server-only" not in beta_driver.first_rows
        _write_diagnostics(
            alpha_project,
            selected_codex,
            alpha_catalog=str(Path(alpha.skills_dir.path) / "skills"),
            beta_catalog=str(Path(beta.skills_dir.path) / "skills"),
            alpha_registered_names=sorted(alpha_driver.first_rows),
            beta_registered_names=sorted(beta_driver.first_rows),
        )
