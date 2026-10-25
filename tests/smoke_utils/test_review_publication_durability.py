from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from autoskillit.smoke_utils import (
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
)
from tests.smoke_utils._review_publication_fixtures import (
    _prepared_publication,
    _publish,
    _write_server_receipt,
)

pytestmark = [pytest.mark.medium]


def _prepared_local_experimental_publication() -> dict[str, Any]:
    return prepare_experimental_review_publication(
        raw_ledger={"candidate_records": [{"candidate_id": "candidate-1"}]},
        survivors=[
            {
                "candidate_id": "candidate-1",
                "disposition_id": "disposition-1",
                "file": "src/app.py",
                "line": 42,
            }
        ],
        snapshot={
            "head_sha": "head",
            "base_sha": "base",
            "merge_base_sha": "merge",
            "diff_sha256": "diff",
        },
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=True,
    )


def test_write_temp_bytes_closes_descriptor_when_fdopen_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    real_close = os.close
    opened_fd = -1
    closed_fds: list[int] = []

    def failing_fdopen(fd: int, mode: str) -> None:
        nonlocal opened_fd
        opened_fd = fd
        raise OSError("injected fdopen failure")

    def recording_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(publication_module.os, "fdopen", failing_fdopen)
    monkeypatch.setattr(publication_module.os, "close", recording_close)

    with pytest.raises(OSError, match="injected fdopen failure"):
        publication_module._write_temp_bytes(tmp_path, "artifact.json", b"payload")

    assert closed_fds == [opened_fd]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("mode", "marker_name"),
    [("local", "local_findings"), ("github", "github_review_binding")],
)
def test_experimental_publication_executes_same_directory_marker_last_renames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    marker_name: str,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    if mode == "github":
        _write_server_receipt(tmp_path / "review-output")
        publication = _prepared_publication("github")
    else:
        publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    real_replace = os.replace
    rename_calls: list[tuple[Path, Path]] = []
    events: list[tuple[str, Path]] = []
    real_file_sync = publication_module.fsync_file
    real_directory_sync = publication_module.fsync_directory

    def recording_file_sync(path: Path) -> None:
        events.append(("file_sync", path))
        real_file_sync(path)

    def recording_directory_sync(path: Path) -> None:
        events.append(("directory_sync", path))
        real_directory_sync(path)

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        source_path = Path(source)
        destination_path = Path(destination)
        rename_calls.append((source_path, destination_path))
        events.append(("replace", destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(publication_module, "fsync_file", recording_file_sync)
    monkeypatch.setattr(publication_module, "fsync_directory", recording_directory_sync)
    monkeypatch.setattr(publication_module.os, "replace", recording_replace)
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(output_dir),
        pr_number="42" if mode == "github" else "17",
    )

    identifier = "42" if mode == "github" else "17"
    expected_names = [
        f"raw_findings_{identifier}.json",
        f"diff_context_{identifier}.json",
        f"{marker_name}_{identifier}.json",
    ]
    assert [destination.name for _, destination in rename_calls] == expected_names
    assert all(
        source.parent == destination.parent == output_dir for source, destination in rename_calls
    )
    assert all(source.name.endswith(".tmp") for source, _ in rename_calls)
    first_final_change = next(index for index, (kind, _) in enumerate(events) if kind == "replace")
    assert all(
        index < first_final_change for index, (kind, _) in enumerate(events) if kind == "file_sync"
    )
    for _, destination in rename_calls:
        replacement_index = next(
            index
            for index, (kind, path) in enumerate(events)
            if kind == "replace" and path == destination
        )
        assert events[replacement_index + 1] == ("directory_sync", output_dir)
    assert all(path.parent == output_dir for kind, path in events if kind == "file_sync")
    assert list(result["published_paths"]) == [
        "raw_findings",
        "diff_context",
        marker_name,
    ]
    for artifact_name, path in result["published_paths"].items():
        if artifact_name in publication["artifacts"]:
            assert json.loads(Path(path).read_text()) == publication["artifacts"][artifact_name]
    assert result["publication_records"][-1]["artifact"] == marker_name
    assert not list(output_dir.glob(".*.tmp"))


@pytest.mark.parametrize("state", ["complete", "stale"])
def test_github_publication_invalidates_old_markers_before_payload_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: str,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    output_dir = tmp_path / "review-output"
    _, receipt_path = _write_server_receipt(output_dir)
    receipt_bytes = receipt_path.read_bytes()
    receipt_inode = receipt_path.stat().st_ino
    _publish(output_dir, "github", written_at="t0")
    marker_paths = {
        output_dir / "local_findings_42.json",
        output_dir / "github_review_binding_42.json",
    }
    if state == "stale":
        (output_dir / "local_findings_42.json").write_text("prior local marker")

    events: list[tuple[str, Path]] = []
    marker_unlinks = 0
    invalidation_synced = False
    real_unlink = Path.unlink
    real_replace = os.replace
    real_directory_sync = publication_module.fsync_directory
    payload_names = {"raw_findings_42.json", "diff_context_42.json"}

    def recording_unlink(path: Path, missing_ok: bool = False) -> None:
        nonlocal marker_unlinks
        if path in marker_paths:
            marker_unlinks += 1
            events.append(("unlink_marker", path))
        elif path.name in payload_names:
            assert all(not marker.exists() for marker in marker_paths)
            assert invalidation_synced
            events.append(("unlink_payload", path))
        real_unlink(path, missing_ok=missing_ok)

    def recording_directory_sync(path: Path) -> None:
        nonlocal invalidation_synced
        real_directory_sync(path)
        events.append(("directory_sync", path))
        if marker_unlinks and all(not marker.exists() for marker in marker_paths):
            invalidation_synced = True

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        destination_path = Path(destination)
        if destination_path.name in payload_names:
            assert all(not marker.exists() for marker in marker_paths)
            assert invalidation_synced
            events.append(("replace_payload", destination_path))
        else:
            events.append(("replace_marker", destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(Path, "unlink", recording_unlink)
    monkeypatch.setattr(publication_module, "fsync_directory", recording_directory_sync)
    monkeypatch.setattr(publication_module.os, "replace", recording_replace)
    result = publish_experimental_review_artifacts(
        publication=_prepared_publication(
            "github",
            written_at="t1",
            fresh=state == "complete",
        ),
        output_dir=str(output_dir),
        pr_number="42",
    )

    first_payload_change = next(
        index
        for index, (kind, _) in enumerate(events)
        if kind in {"replace_payload", "unlink_payload"}
    )
    assert any(kind == "directory_sync" for kind, _ in events[:first_payload_change])
    assert all(
        index < first_payload_change
        for index, (kind, _) in enumerate(events)
        if kind == "unlink_marker"
    )
    if state == "complete":
        marker_change = next(
            index for index, (kind, _) in enumerate(events) if kind == "replace_marker"
        )
        assert all(
            index < marker_change
            for index, (kind, _) in enumerate(events)
            if kind in {"replace_payload", "unlink_payload"}
        )
        assert events[marker_change + 1] == ("directory_sync", output_dir)
        assert result["publication_records"][-1]["artifact"] == "github_review_binding"
        assert (output_dir / "github_review_binding_42.json").exists()
    else:
        assert not any(kind == "replace_marker" for kind, _ in events)
        assert result["publication_records"][-1]["artifact"] == "raw_findings"
        assert not any(marker.exists() for marker in marker_paths)
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode


@pytest.mark.parametrize(
    ("failure_point", "directory_sync_index"),
    [
        ("staged_file", None),
        ("marker_invalidation", 0),
        ("payload_rename", 1),
        ("final_marker", 3),
    ],
)
def test_publication_sync_failure_restores_previous_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
    directory_sync_index: int | None,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    output_dir = tmp_path / "review-output"
    _, receipt_path = _write_server_receipt(output_dir)
    receipt_bytes = receipt_path.read_bytes()
    receipt_inode = receipt_path.stat().st_ino
    _publish(output_dir, "github", written_at="t0")
    artifact_paths = {
        output_dir / "raw_findings_42.json",
        output_dir / "diff_context_42.json",
        output_dir / "github_review_binding_42.json",
    }
    previous = {path: path.read_bytes() for path in artifact_paths}
    binding_path = output_dir / "github_review_binding_42.json"
    payload_paths = {
        output_dir / "raw_findings_42.json",
        output_dir / "diff_context_42.json",
    }
    events: list[tuple[str, Path, bytes | None]] = []
    file_sync_failed = False
    directory_sync_failed = False
    directory_sync_calls = 0
    real_file_sync = publication_module.fsync_file
    real_directory_sync = publication_module.fsync_directory
    real_replace = os.replace
    real_unlink = Path.unlink

    def recording_file_sync(path: Path) -> None:
        nonlocal file_sync_failed
        events.append(("file_sync", path, None))
        if failure_point == "staged_file" and not file_sync_failed:
            file_sync_failed = True
            raise OSError("injected staged file sync failure")
        real_file_sync(path)

    def recording_directory_sync(path: Path) -> None:
        nonlocal directory_sync_calls, directory_sync_failed
        current_index = directory_sync_calls
        directory_sync_calls += 1
        if current_index == directory_sync_index and not directory_sync_failed:
            directory_sync_failed = True
            events.append(("directory_sync_failure", path, None))
            raise OSError("injected directory sync failure")
        real_directory_sync(path)
        events.append(("directory_sync", path, None))

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        source_path = Path(source)
        destination_path = Path(destination)
        events.append(("replace", destination_path, source_path.read_bytes()))
        real_replace(source, destination)

    def recording_unlink(path: Path, missing_ok: bool = False) -> None:
        if path in artifact_paths:
            events.append(("unlink", path, None))
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(publication_module, "fsync_file", recording_file_sync)
    monkeypatch.setattr(publication_module, "fsync_directory", recording_directory_sync)
    monkeypatch.setattr(publication_module.os, "replace", recording_replace)
    monkeypatch.setattr(Path, "unlink", recording_unlink)

    with pytest.raises(RuntimeError, match="publication failed"):
        publish_experimental_review_artifacts(
            publication=_prepared_publication("github", written_at="t1"),
            output_dir=str(output_dir),
            pr_number="42",
        )

    assert {path: path.read_bytes() for path in artifact_paths} == previous
    marker_restore_indices = [
        index
        for index, (kind, path, content) in enumerate(events)
        if kind == "replace" and path == binding_path and content == previous[binding_path]
    ]
    if failure_point == "staged_file":
        assert marker_restore_indices == []
        assert not any(kind in {"replace", "unlink"} for kind, _, _ in events)
    else:
        marker_restore_index = marker_restore_indices[-1]
        changed_payloads = {
            path
            for index, (kind, path, content) in enumerate(events)
            if kind == "replace"
            and path in payload_paths
            and content != previous[path]
            and index < marker_restore_index
        }
        for path in changed_payloads:
            changed_index = next(
                index
                for index, (kind, event_path, content) in enumerate(events)
                if kind == "replace" and event_path == path and content != previous[path]
            )
            restore_index = next(
                index
                for index, (kind, event_path, content) in enumerate(events)
                if kind == "replace"
                and event_path == path
                and content == previous[path]
                and index > changed_index
                and index < marker_restore_index
            )
            assert any(
                kind == "directory_sync"
                and event_path == output_dir
                and restore_index < index < marker_restore_index
                for index, (kind, event_path, _) in enumerate(events)
            )
    assert not list(output_dir.glob(".*.tmp"))
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode


@pytest.mark.parametrize("restore_failure", ["file", "directory"])
def test_sync_failure_during_restoration_leaves_commit_markers_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    restore_failure: str,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    output_dir = tmp_path / "review-output"
    _, receipt_path = _write_server_receipt(output_dir)
    receipt_bytes = receipt_path.read_bytes()
    receipt_inode = receipt_path.stat().st_ino
    _publish(output_dir, "github", written_at="t0")
    raw_path = output_dir / "raw_findings_42.json"
    diff_path = output_dir / "diff_context_42.json"
    binding_path = output_dir / "github_review_binding_42.json"
    previous_raw = raw_path.read_bytes()
    previous_diff = diff_path.read_bytes()
    publication_sync_failed = False
    restoration_payload_replaced = False
    restoration_sync_failed = False
    directory_sync_calls = 0
    real_file_sync = publication_module.fsync_file
    real_directory_sync = publication_module.fsync_directory
    real_replace = os.replace

    def failing_file_sync(path: Path) -> None:
        nonlocal restoration_sync_failed
        if restore_failure == "file" and publication_sync_failed and not restoration_sync_failed:
            restoration_sync_failed = True
            raise OSError("injected restoration file sync failure")
        real_file_sync(path)

    def failing_directory_sync(path: Path) -> None:
        nonlocal directory_sync_calls, publication_sync_failed, restoration_sync_failed
        current_index = directory_sync_calls
        directory_sync_calls += 1
        if current_index == 1 and not publication_sync_failed:
            publication_sync_failed = True
            raise OSError("injected publication sync failure")
        if (
            restore_failure == "directory"
            and restoration_payload_replaced
            and not restoration_sync_failed
        ):
            restoration_sync_failed = True
            raise OSError("injected restoration directory sync failure")
        real_directory_sync(path)

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        nonlocal restoration_payload_replaced
        source_path = Path(source)
        destination_path = Path(destination)
        content = source_path.read_bytes()
        if destination_path == raw_path and content == previous_raw and publication_sync_failed:
            restoration_payload_replaced = True
        real_replace(source, destination)

    monkeypatch.setattr(publication_module, "fsync_file", failing_file_sync)
    monkeypatch.setattr(publication_module, "fsync_directory", failing_directory_sync)
    monkeypatch.setattr(publication_module.os, "replace", recording_replace)

    with pytest.raises(ExceptionGroup) as exc_info:
        publish_experimental_review_artifacts(
            publication=_prepared_publication("github", written_at="t1"),
            output_dir=str(output_dir),
            pr_number="42",
        )

    expected_restoration_error = (
        "injected restoration file sync failure"
        if restore_failure == "file"
        else "injected restoration directory sync failure"
    )
    assert [str(error) for error in exc_info.value.exceptions] == [
        "injected publication sync failure",
        expected_restoration_error,
    ]
    assert publication_sync_failed
    assert restoration_sync_failed
    assert not binding_path.exists()
    assert not (output_dir / "local_findings_42.json").exists()
    assert diff_path.read_bytes() == previous_diff
    assert not list(output_dir.glob(".*.tmp"))
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode


@pytest.mark.parametrize("failure_index", [0, 1, 2])
def test_experimental_publication_rolls_back_each_write_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_index: int,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_18.json",
        output_dir / "diff_context_18.json",
        output_dir / "local_findings_18.json",
        output_dir / "github_review_binding_18.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    original_write = publication_module._write_temp_bytes
    write_index = 0

    def failing_write(directory: Path, final_name: str, content: bytes) -> Path:
        nonlocal write_index
        current_index = write_index
        write_index += 1
        if current_index == failure_index:
            raise OSError("injected write failure")
        return original_write(directory, final_name, content)

    monkeypatch.setattr(publication_module, "_write_temp_bytes", failing_write)
    with pytest.raises(RuntimeError, match="publication failed"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="18",
        )

    assert [path.read_text() for path in final_paths] == ["old-0", "old-1", "old-2", "old-3"]
    assert not list(output_dir.glob(".*.tmp"))


@pytest.mark.parametrize("failure_index", [0, 1, 2])
def test_experimental_publication_rolls_back_each_rename_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_index: int,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_19.json",
        output_dir / "diff_context_19.json",
        output_dir / "local_findings_19.json",
        output_dir / "github_review_binding_19.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    real_replace = os.replace
    rename_index = 0
    failure_injected = False

    def failing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        nonlocal failure_injected, rename_index
        if not failure_injected:
            current_index = rename_index
            rename_index += 1
            if current_index == failure_index:
                failure_injected = True
                raise OSError("injected rename failure")
        real_replace(source, destination)

    monkeypatch.setattr(publication_module.os, "replace", failing_replace)
    with pytest.raises(RuntimeError, match="publication failed"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="19",
        )

    assert [path.read_text() for path in final_paths] == ["old-0", "old-1", "old-2", "old-3"]
    assert not list(output_dir.glob(".*.tmp"))


def test_experimental_publication_preserves_publication_and_rollback_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_20.json",
        output_dir / "diff_context_20.json",
        output_dir / "local_findings_20.json",
        output_dir / "github_review_binding_20.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    real_replace = os.replace
    replace_count = 0

    def failing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("injected publication failure")
        if replace_count == 3:
            raise OSError("injected rollback failure")
        real_replace(source, destination)

    monkeypatch.setattr(publication_module.os, "replace", failing_replace)
    with pytest.raises(ExceptionGroup) as exc_info:
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="20",
        )

    errors = exc_info.value.exceptions
    assert [str(error) for error in errors] == [
        "injected publication failure",
        "injected rollback failure",
    ]
    assert final_paths[1].read_text() == "old-1"
    assert not final_paths[2].exists()
    assert not final_paths[3].exists()
