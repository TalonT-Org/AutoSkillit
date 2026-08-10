"""Tests for exact PR-body provenance enforcement."""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, NEW_SUBDIR_BASENAMES

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

ISSUE_URL = "https://github.com/TalonT-Org/AutoSkillit/issues/4293"
OTHER_ISSUE_URL = "https://github.com/TalonT-Org/AutoSkillit/issues/4294"


def _body_file(tmp_path: Path, content: str, *, name: str = "pr_body_test.md") -> Path:
    path = tmp_path / ".autoskillit" / "temp" / "compose-pr" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _metadata_path(body_path: Path) -> Path:
    return body_path.with_suffix(".metadata.json")


def _ordinary_metadata(
    body_path: Path,
    *,
    closing_issue: int | None = 4293,
    source_issue_url: str | None = ISSUE_URL,
    **overrides: object,
) -> Path:
    metadata: dict[str, object] = {
        "schema_version": 1,
        "body_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
        "closing_issue": closing_issue,
        "source_issue_url": source_issue_url,
    }
    metadata.update(overrides)
    path = _metadata_path(body_path)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def _integration_metadata(
    body_path: Path,
    *,
    source_issue_urls: list[str] | None = None,
    **overrides: object,
) -> Path:
    metadata: dict[str, object] = {
        "schema_version": 1,
        "body_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
        "source_issue_urls": (
            [ISSUE_URL, OTHER_ISSUE_URL] if source_issue_urls is None else source_issue_urls
        ),
    }
    metadata.update(overrides)
    path = _metadata_path(body_path)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def _event(command: str, tool_name: str = "Bash") -> dict[str, object]:
    command_key = "cmd" if tool_name.startswith("mcp__") else "command"
    return {"tool_name": tool_name, "tool_input": {command_key: command}}


def _run_hook(
    event: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    *,
    skill_name: str = "compose-pr",
    headless: bool = True,
) -> str:
    from autoskillit.hooks.guards.compose_pr_body_guard import main  # noqa: PLC0415

    monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", skill_name)
    if headless:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    else:
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    output = io.StringIO()
    with redirect_stdout(output), pytest.raises(SystemExit):
        main()
    return output.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    from autoskillit.hooks.guards.compose_pr_body_guard import (  # noqa: PLC0415
        COMPOSE_PR_BODY_DENY_TRIGGER,
    )

    hook_output = json.loads(output)["hookSpecificOutput"]
    return hook_output["permissionDecision"] == "deny" and hook_output[
        "permissionDecisionReason"
    ].startswith(f"{COMPOSE_PR_BODY_DENY_TRIGGER}: ")


def _ordinary_pair(tmp_path: Path, *, issue_backed: bool = True) -> Path:
    body = _body_file(
        tmp_path,
        f"Summary\n\nCloses {ISSUE_URL}" if issue_backed else "Summary without an issue",
    )
    if issue_backed:
        _ordinary_metadata(body)
    else:
        _ordinary_metadata(body, closing_issue=None, source_issue_url=None)
    return body


def test_scopes_to_headless_pr_skills(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, "Summary without provenance metadata")
    command = f"gh pr create --body-file {body}"

    assert _is_denied(_run_hook(_event(command), monkeypatch))
    assert _run_hook(_event(command), monkeypatch, skill_name="implement-worktree") == ""
    assert _run_hook(_event(command), monkeypatch, headless=False) == ""
    assert _run_hook(_event("git status"), monkeypatch) == ""


@pytest.mark.parametrize("skill_name", ["compose-pr", "open-integration-pr"])
@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --fill",
        "gh pr create --body-file -",
        "gh pr create --body-file /does/not/exist.md",
        'gh pr create --body-file "$UNRESOLVED_BODY"',
    ],
    ids=["fill", "stdin", "missing", "unresolved-variable"],
)
def test_missing_or_unresolvable_body_file_denies(monkeypatch, tmp_path, skill_name, command):
    monkeypatch.chdir(tmp_path)

    assert _is_denied(_run_hook(_event(command), monkeypatch, skill_name=skill_name))


@pytest.mark.parametrize(
    "overrides",
    [
        {"body_sha256": "0" * 64},
        {"closing_issue": 4294},
        {"source_issue_url": OTHER_ISSUE_URL},
        {"schema_version": 2},
        {"extra": "forbidden"},
    ],
    ids=["digest", "number", "url", "schema", "extra-field"],
)
def test_ordinary_issue_pair_requires_exact_digest_number_and_url(
    monkeypatch, tmp_path, overrides
):
    monkeypatch.chdir(tmp_path)
    body = _ordinary_pair(tmp_path)
    command = f"gh pr create --body-file {body}"
    assert _run_hook(_event(command), monkeypatch) == ""

    _ordinary_metadata(body, **overrides)
    assert _is_denied(_run_hook(_event(command), monkeypatch))


def test_ordinary_supported_no_issue_pair_allows(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _ordinary_pair(tmp_path, issue_backed=False)
    assert _run_hook(_event(f"gh pr create --body-file={body}"), monkeypatch) == ""


@pytest.mark.parametrize(
    "metadata_content",
    [None, "not json", "[]", json.dumps({"schema_version": 1})],
)
def test_missing_or_malformed_sibling_metadata_denies(monkeypatch, tmp_path, metadata_content):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, f"Closes {ISSUE_URL}")
    if metadata_content is not None:
        _metadata_path(body).write_text(metadata_content, encoding="utf-8")

    assert _is_denied(_run_hook(_event(f"gh pr create --body-file {body}"), monkeypatch))


def test_unrelated_prep_and_sibling_files_cannot_rescue_body(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, f"Closes {ISSUE_URL}")
    stale = _body_file(tmp_path, f"Closes {ISSUE_URL}", name="stale.md")
    _ordinary_metadata(stale)
    prep = tmp_path / ".autoskillit" / "temp" / "prepare-pr" / "pr_prep_newest.md"
    prep.parent.mkdir(parents=True)
    prep.write_text("- closing_issue: 4293", encoding="utf-8")

    assert _is_denied(_run_hook(_event(f"gh pr create --body-file {body}"), monkeypatch))


@pytest.mark.parametrize(
    "content",
    ["Closes #4293", f"Reference: {ISSUE_URL}", f"Closes {OTHER_ISSUE_URL}"],
    ids=["number-only", "not-closing", "wrong-issue"],
)
def test_ordinary_body_must_contain_exact_closing_url(monkeypatch, tmp_path, content):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, content)
    _ordinary_metadata(body)
    assert _is_denied(_run_hook(_event(f"gh pr create --body-file {body}"), monkeypatch))


@pytest.mark.parametrize(
    "urls",
    [
        [ISSUE_URL],
        [OTHER_ISSUE_URL, ISSUE_URL],
        [ISSUE_URL, ISSUE_URL],
        ["https://example.com/issues/4293"],
    ],
    ids=["missing-url", "unsorted", "duplicate", "foreign-host"],
)
def test_integration_pair_requires_every_sorted_unique_source_url(monkeypatch, tmp_path, urls):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, f"Closes {ISSUE_URL}\nCloses {OTHER_ISSUE_URL}")
    _integration_metadata(body)
    command = f"gh pr create --body-file {body}"
    assert _run_hook(_event(command), monkeypatch, skill_name="open-integration-pr") == ""

    _integration_metadata(body, source_issue_urls=urls)
    assert _is_denied(_run_hook(_event(command), monkeypatch, skill_name="open-integration-pr"))


def test_integration_body_omitting_one_source_url_denies(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, f"Closes {ISSUE_URL}")
    _integration_metadata(body)
    assert _is_denied(
        _run_hook(
            _event(f"gh pr create --body-file {body}"),
            monkeypatch,
            skill_name="open-integration-pr",
        )
    )


def test_integration_metadata_preserves_explicit_empty_sources(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, f"Tracks {ISSUE_URL}\nTracks {OTHER_ISSUE_URL}")
    _integration_metadata(body, source_issue_urls=[])

    assert (
        _run_hook(
            _event(f"gh pr create --body-file {body}"),
            monkeypatch,
            skill_name="open-integration-pr",
        )
        == ""
    )


def test_any_invalid_create_segment_denies_compound_command(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    valid = _ordinary_pair(tmp_path)
    command = f"gh pr create --title invalid; gh pr create --body-file {valid} --title valid"
    assert _is_denied(_run_hook(_event(command), monkeypatch))


def test_all_valid_create_segments_allow(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    first = _ordinary_pair(tmp_path)
    second = _body_file(tmp_path, f"Closes {ISSUE_URL}", name="second.md")
    _ordinary_metadata(second)
    command = f"gh pr create --body-file {first}; gh pr create --body-file={second}"
    assert _run_hook(_event(command), monkeypatch) == ""


def test_variable_loop_and_run_cmd_shape(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _ordinary_pair(tmp_path)
    command = (
        f"PR_CREATE_BODY={body}\n"
        "while true; do\n"
        '  gh pr create --body-file "$PR_CREATE_BODY"\n'
        "  break\n"
        "done"
    )
    event = _event(command, "mcp__autoskillit__local__autoskillit__run_cmd")
    assert _run_hook(event, monkeypatch) == ""


@pytest.mark.parametrize(
    "command_template",
    [
        "while true; do\n  gh pr create --body-file {body}\n  break\ndone",
        "for attempt in 1 2 3; do\n  gh pr create --body-file={body}\ndone",
        "if true; then\n  gh pr create --body-file {body}\nfi",
    ],
)
def test_control_flow_still_enforces_body_provenance(monkeypatch, tmp_path, command_template):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, "Summary without the required closing URL")
    _ordinary_metadata(body)

    assert _is_denied(_run_hook(_event(command_template.format(body=body)), monkeypatch))


def test_variable_body_path_after_case_boundary_is_enforced(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _body_file(tmp_path, "Summary without the required closing URL")
    _ordinary_metadata(body)
    command = (
        f"PR_CREATE_BODY={body}\n"
        "while true; do\n"
        '  case "$ATTEMPT" in\n'
        "    2) break ;;\n"
        "  esac\n"
        '  gh pr create --body-file "$PR_CREATE_BODY"\n'
        "  break\n"
        "done"
    )

    assert _is_denied(_run_hook(_event(command), monkeypatch))


def test_relative_body_path_resolves_from_payload_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = _ordinary_pair(tmp_path)
    relative = body.relative_to(tmp_path)
    assert _run_hook(_event(f"gh pr create --body-file {relative}"), monkeypatch) == ""


def test_echo_only_reference_is_not_a_create(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert _run_hook(_event("echo 'gh pr create --body-file /missing.md'"), monkeypatch) == ""


def test_malformed_hook_json_fails_open(monkeypatch):
    from autoskillit.hooks.guards.compose_pr_body_guard import main  # noqa: PLC0415

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
    monkeypatch.setattr("sys.stdin", io.StringIO("{"))
    with redirect_stdout(io.StringIO()) as output, pytest.raises(SystemExit):
        main()
    assert output.getvalue() == ""


def test_hook_registration_shape():
    matching = [
        hook for hook in HOOK_REGISTRY if "guards/compose_pr_body_guard.py" in hook.scripts
    ]
    assert len(matching) == 1
    assert matching[0].event_type == "PreToolUse"
    assert matching[0].matcher == r"Bash|mcp__.*autoskillit.*__run_cmd"
    assert "compose_pr_body_guard.py" in NEW_SUBDIR_BASENAMES
