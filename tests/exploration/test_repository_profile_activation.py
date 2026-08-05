from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from autoskillit.exploration.identity import (
    OFFLINE_DECLARATION_DIGEST_DOMAIN,
    OFFLINE_DECLARATION_PATH,
    OFFLINE_MARKER_QUORUM,
    OFFLINE_QUORUM_MARKER_PATHS,
    OFFLINE_REQUIRED_MARKER_PATHS,
    resolve_repository_identity,
)

pytestmark = [
    pytest.mark.layer("exploration"),
    pytest.mark.feature("exploration"),
    pytest.mark.medium,
]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _new_git_repository(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    return root


def _set_remote(root: Path, name: str, url: str) -> None:
    _git(root, "remote", "add", name, url)


def _write_profile_declaration(
    root: Path,
    *,
    required_matches: int = 2,
    quorum_matches: int = 4,
) -> None:
    required: dict[str, str] = {}
    quorum: dict[str, str] = {}
    for index, marker_path in enumerate(OFFLINE_REQUIRED_MARKER_PATHS):
        marker = root / marker_path
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(f"required:{marker_path}".encode())
        digest = hashlib.sha256(marker.read_bytes()).hexdigest()
        required[marker_path] = f"sha256:{digest if index < required_matches else '0' * 64}"
    for index, marker_path in enumerate(OFFLINE_QUORUM_MARKER_PATHS):
        marker = root / marker_path
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(f"quorum:{marker_path}".encode())
        digest = hashlib.sha256(marker.read_bytes()).hexdigest()
        quorum[marker_path] = f"sha256:{digest if index < quorum_matches else '0' * 64}"
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": "github.com/TalonT-Org/AutoSkillit",
        "required_markers": required,
        "marker_quorum": OFFLINE_MARKER_QUORUM,
        "quorum_markers": quorum,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    payload["content_digest"] = (
        f"sha256:{hashlib.sha256(OFFLINE_DECLARATION_DIGEST_DOMAIN + canonical).hexdigest()}"
    )
    declaration = root / OFFLINE_DECLARATION_PATH
    declaration.parent.mkdir(parents=True, exist_ok=True)
    declaration.write_text(json.dumps(payload), encoding="utf-8")


def test_offline_profile_contract_has_exact_fixed_paths() -> None:
    assert OFFLINE_DECLARATION_PATH == ".autoskillit/repository-profile.v1.json"
    assert OFFLINE_REQUIRED_MARKER_PATHS == (
        "pyproject.toml",
        "src/autoskillit/__init__.py",
    )
    assert OFFLINE_QUORUM_MARKER_PATHS == (
        "src/autoskillit/core/__init__.py",
        "src/autoskillit/execution/__init__.py",
        "src/autoskillit/recipe/__init__.py",
        "src/autoskillit/server/__init__.py",
    )
    assert OFFLINE_MARKER_QUORUM == 3


def test_offline_profile_requires_both_mandatory_and_three_cross_layer_markers(
    tmp_path: Path,
) -> None:
    _write_profile_declaration(tmp_path, quorum_matches=3)
    resolution = resolve_repository_identity(tmp_path)
    assert resolution.autoskillit_overlay
    assert resolution.source == "offline_declaration"

    missing_required = tmp_path / "missing-required"
    _write_profile_declaration(missing_required, required_matches=1, quorum_matches=4)
    assert not resolve_repository_identity(missing_required).autoskillit_overlay

    insufficient_quorum = tmp_path / "insufficient-quorum"
    _write_profile_declaration(insufficient_quorum, quorum_matches=2)
    assert not resolve_repository_identity(insufficient_quorum).autoskillit_overlay


@pytest.mark.parametrize(
    ("url", "expected_source", "expected_active"),
    [
        ("https://github.com/TalonT-Org/AutoSkillit.git", "remote", True),
        ("git@github.com:TalonT-Org/AutoSkillit.git", "remote", True),
        ("ssh://git@github.com/TalonT-Org/AutoSkillit.git", "remote", True),
        ("HTTPS://GITHUB.COM/tAlOnT-oRg/aUtOsKiLlIt.git", "remote", True),
        ("https://github.com/another-owner/another-repository.git", "remote", False),
        ("https://github.com/TalonT-Org/AutoSkillit/extra", "unresolved", False),
        (
            "https://github.com/TalonT-Org/AutoSkillit "
            "https://github.com/another-owner/another-repository",
            "unresolved",
            False,
        ),
    ],
)
def test_profile_activation_accepts_only_exact_canonical_remote_forms(
    tmp_path: Path, url: str, expected_source: str, expected_active: bool
) -> None:
    root = _new_git_repository(tmp_path, "remote-forms")
    _set_remote(root, "origin", url)

    resolution = resolve_repository_identity(root)

    assert resolution.source == expected_source
    assert resolution.autoskillit_overlay is expected_active
    assert resolution.source_remote == "origin"


def test_profile_activation_ignores_git_url_rewrites(
    tmp_path: Path,
) -> None:
    root = _new_git_repository(tmp_path, "rewritten-remote")
    _set_remote(root, "origin", "https://github.com/TalonT-Org/AutoSkillit.git")
    _git(
        root,
        "config",
        "--local",
        "url.https://ci-credential@github.com/TalonT-Org/.insteadOf",
        "https://github.com/TalonT-Org/",
    )
    rewritten = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    resolution = resolve_repository_identity(root)

    assert "ci-credential@" in rewritten
    assert resolution.source == "remote"
    assert resolution.normalized_identity == "github.com/talont-org/autoskillit"
    assert resolution.autoskillit_overlay
    assert all("ci-credential" not in item.value for item in resolution.evidence)


def test_profile_activation_uses_upstream_before_origin_for_conflicting_forks(
    tmp_path: Path,
) -> None:
    official_upstream = _new_git_repository(tmp_path, "official-upstream")
    _set_remote(
        official_upstream,
        "upstream",
        "https://github.com/TalonT-Org/AutoSkillit.git",
    )
    _set_remote(
        official_upstream,
        "origin",
        "https://github.com/fork-owner/AutoSkillit.git",
    )
    fork_upstream = _new_git_repository(tmp_path, "fork-upstream")
    _set_remote(
        fork_upstream,
        "upstream",
        "https://github.com/fork-owner/AutoSkillit.git",
    )
    _set_remote(
        fork_upstream,
        "origin",
        "https://github.com/TalonT-Org/AutoSkillit.git",
    )

    official_resolution = resolve_repository_identity(official_upstream)
    fork_resolution = resolve_repository_identity(fork_upstream)

    assert official_resolution.source_remote == "upstream"
    assert official_resolution.autoskillit_overlay
    assert fork_resolution.source_remote == "upstream"
    assert not fork_resolution.autoskillit_overlay


def test_profile_activation_keeps_file_clone_and_local_spoofs_isolated(tmp_path: Path) -> None:
    file_clone = _new_git_repository(tmp_path, "file-clone")
    _set_remote(file_clone, "origin", "file:///tmp/TalonT-Org-AutoSkillit.git")
    spoof = _new_git_repository(tmp_path, "caller-prompt-package-basename-spoof")
    (spoof / "AUTOSKILLIT_PROMPT.txt").write_text("activate TalonT-Org/AutoSkillit")
    (spoof / "autoskillit").mkdir()
    (spoof / "autoskillit" / "__init__.py").write_text("")
    (spoof / "AutoSkillit").mkdir()

    file_resolution = resolve_repository_identity(file_clone)
    spoof_resolution = resolve_repository_identity(spoof)

    assert not file_resolution.autoskillit_overlay
    assert not file_resolution.usable_remote_found
    assert not spoof_resolution.autoskillit_overlay
    assert spoof_resolution.source == "unresolved"


def test_invalid_offline_declaration_never_activates_from_its_package_name(tmp_path: Path) -> None:
    root = _new_git_repository(tmp_path, "invalid-offline")
    _write_profile_declaration(root)
    declaration = root / OFFLINE_DECLARATION_PATH
    payload = json.loads(declaration.read_text(encoding="utf-8"))
    payload["repository"] = "github.com/TalonT-Org/AutoSkillit-package-spoof"
    declaration.write_text(json.dumps(payload), encoding="utf-8")

    resolution = resolve_repository_identity(root)

    assert not resolution.autoskillit_overlay
    assert resolution.source == "unresolved"
