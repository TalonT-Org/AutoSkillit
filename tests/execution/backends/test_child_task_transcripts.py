"""Backend-owned views of delegated child transcripts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import ChildTaskTranscript
from autoskillit.execution.backends import CompositeSessionLocator
from autoskillit.execution.backends._claude.child_task import parse_claude_child_task
from autoskillit.execution.backends._codex.child_task import parse_codex_child_task
from autoskillit.execution.backends._codex_parse import (
    extract_codex_child_turn_usage,
    extract_codex_turn_usage,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _claude_user(content: object) -> dict[str, object]:
    return {"type": "user", "message": {"content": content}}


def _claude_assistant(
    *,
    message_id: str | None = "message-1",
    content: list[dict[str, str]] | None = None,
    stop_reason: str | None = None,
    model: str = "claude-sonnet",
) -> dict[str, object]:
    message: dict[str, object] = {"content": content or [], "model": model}
    if message_id is not None:
        message["id"] = message_id
    if stop_reason is not None:
        message["stop_reason"] = stop_reason
    return {"type": "assistant", "message": message}


def _text(value: str, block_type: str = "text") -> dict[str, str]:
    return {"type": block_type, "text": value}


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _codex_event(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {"type": event_type, "payload": payload}


def _codex_meta(child_id: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"id": child_id, **extra}
    return _codex_event("session_meta", payload)


def _write_codex(path: Path, child_id: str, events: list[dict[str, object]]) -> Path:
    return _write_jsonl(path, [_codex_meta(child_id), *events])


def _codex_usage_snapshot(cumulative_total: int, timestamp: str) -> dict[str, object]:
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 13,
                },
                "total_token_usage": {"total_tokens": cumulative_total},
            },
        },
    }


@pytest.mark.parametrize(
    "prompt",
    [
        "Review the deletion regression.",
        [{"type": "text", "text": "Review the deletion regression."}],
    ],
)
@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence"])
def test_claude_parser_reads_first_prompt_and_natural_final_message(
    tmp_path: Path, prompt: object, stop_reason: str
) -> None:
    path = _write_jsonl(
        tmp_path / "agent-child1.jsonl",
        [
            _claude_user(prompt),
            {"type": "attachment", "content": "ignored"},
            _claude_user("later prompt is ignored"),
            _claude_assistant(content=[_text("[")], stop_reason=stop_reason),
        ],
    )

    result = parse_claude_child_task(path, "child1")

    assert result is not None
    assert result.terminal is True
    assert result.final_text == "["
    assert result.output_limit_stops == 0
    assert result.assignment_prompt == "Review the deletion regression."


def test_claude_parser_keeps_output_limit_count_across_resume(tmp_path: Path) -> None:
    path = _write_jsonl(
        tmp_path / "agent-child2.jsonl",
        [
            _claude_user("Inspect bugs."),
            _claude_assistant(
                message_id="partial", content=[_text("truncated")], stop_reason="max_tokens"
            ),
            {
                "type": "user",
                "isMeta": True,
                "message": {"content": [{"type": "text", "text": "resume"}]},
            },
            _claude_assistant(message_id="final", content=[_text("[]")], stop_reason="end_turn"),
        ],
    )

    result = parse_claude_child_task(path, "child2")

    assert result is not None
    assert result.terminal is True
    assert result.final_text == "[]"
    assert result.output_limit_stops == 1


@pytest.mark.parametrize("trailing_type", ["tool_use", "tool_result"])
def test_claude_parser_does_not_call_tool_use_a_natural_final_turn(
    tmp_path: Path, trailing_type: str
) -> None:
    records = [
        _claude_user("Do the work."),
        _claude_assistant(
            content=[_text("calling tool")],
            stop_reason="tool_use" if trailing_type == "tool_use" else "end_turn",
        ),
    ]
    if trailing_type == "tool_result":
        records.append({"type": "user", "message": {"content": [{"type": "tool_result"}]}})
    path = _write_jsonl(tmp_path / f"agent-{trailing_type}.jsonl", records)

    result = parse_claude_child_task(path, trailing_type)

    assert result is not None
    assert result.terminal is False


def test_claude_parser_final_thinking_only_group_has_no_text(tmp_path: Path) -> None:
    path = _write_jsonl(
        tmp_path / "agent-thinking.jsonl",
        [
            _claude_user("Think."),
            _claude_assistant(
                content=[_text("private reasoning", "thinking")], stop_reason="end_turn"
            ),
        ],
    )

    result = parse_claude_child_task(path, "thinking")

    assert result is not None
    assert result.final_text is None


def test_claude_parser_marks_synthetic_final_group_nonterminal(tmp_path: Path) -> None:
    path = _write_jsonl(
        tmp_path / "agent-synthetic.jsonl",
        [
            _claude_user("Finish."),
            _claude_assistant(content=[_text("synthetic")], model="<synthetic>"),
        ],
    )

    result = parse_claude_child_task(path, "synthetic")

    assert result is not None
    assert result.terminal is False
    assert result.final_stop_reason == "synthetic"


@pytest.mark.parametrize("stop_reason", ["refusal", "pause_turn", "tool_use", None])
def test_claude_parser_requires_a_natural_stop_reason(
    tmp_path: Path, stop_reason: str | None
) -> None:
    path = _write_jsonl(
        tmp_path / "agent-stop.jsonl",
        [
            _claude_user("Finish."),
            _claude_assistant(content=[_text("answer")], stop_reason=stop_reason),
        ],
    )

    result = parse_claude_child_task(path, "stop")

    assert result is not None
    assert result.terminal is False


def test_claude_parser_keeps_idless_assistant_record_in_its_own_group(tmp_path: Path) -> None:
    path = _write_jsonl(
        tmp_path / "agent-idless.jsonl",
        [
            _claude_user("Finish."),
            _claude_assistant(content=[_text("earlier")]),
            _claude_assistant(message_id=None, content=[_text("final")], stop_reason="end_turn"),
        ],
    )

    result = parse_claude_child_task(path, "idless")

    assert result is not None
    assert result.final_text == "final"


def test_claude_parser_joins_text_records_with_the_same_message_id(tmp_path: Path) -> None:
    path = _write_jsonl(
        tmp_path / "agent-split.jsonl",
        [
            _claude_user("Finish."),
            _claude_assistant(
                message_id="same",
                content=[_text("private thought", "thinking"), _text("first")],
            ),
            _claude_assistant(
                message_id="same", content=[_text("second")], stop_reason="end_turn"
            ),
        ],
    )

    result = parse_claude_child_task(path, "split")

    assert result is not None
    assert result.final_text == "first\nsecond"
    assert result.terminal is True


def test_claude_parser_reads_sibling_assignment_label(tmp_path: Path) -> None:
    path = _write_jsonl(
        tmp_path / "agent-labeled.jsonl",
        [
            _claude_user("Prompt."),
            _claude_assistant(content=[_text("done")], stop_reason="end_turn"),
        ],
    )
    path.with_name("agent-labeled.meta.json").write_text(
        json.dumps({"description": "review-pr-auditor-arch"}), encoding="utf-8"
    )

    result = parse_claude_child_task(path, "labeled")

    assert result is not None
    assert result.assignment_label == "review-pr-auditor-arch"


def test_claude_parser_enforces_read_limit_when_stat_underreports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.execution.backends._claude.child_task as child_task_module

    path = tmp_path / "agent-large.jsonl"
    path.write_bytes(b"x" * 20)
    monkeypatch.setattr(child_task_module, "CLAUDE_CHILD_TRANSCRIPT_MAX_BYTES", 8)
    original_stat = Path.stat

    def underreported_stat(candidate: Path, *args: object, **kwargs: object) -> object:
        if candidate == path:
            return SimpleNamespace(st_size=0)
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", underreported_stat)

    assert parse_claude_child_task(path, "large") is None


def _write_home_child(home: Path, child_id: str, project: str = "one/two") -> Path:
    return _write_jsonl(
        home / ".claude" / "projects" / project / "subagents" / f"agent-{child_id}.jsonl",
        [
            _claude_user("Prompt."),
            _claude_assistant(content=[_text("done")], stop_reason="end_turn"),
        ],
    )


def test_claude_locator_resolves_across_projects_using_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.backends import ClaudeSessionLocator

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    expected = _write_home_child(tmp_path, "child-home", project="nested/project")

    result = ClaudeSessionLocator().read_child_task("child-home")

    assert result is not None
    assert result.transcript_locator == str(expected)


def test_claude_locator_returns_none_for_missing_and_ambiguous_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.backends import ClaudeSessionLocator

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _write_home_child(tmp_path, "ambiguous", project="project-a/nested")
    _write_home_child(tmp_path, "ambiguous", project="project-b/nested")
    locator = ClaudeSessionLocator()

    assert locator.read_child_task("missing") is None
    assert locator.read_child_task("ambiguous") is None


@pytest.mark.parametrize("child_id", ["a*", "../x", ""])
def test_claude_locator_rejects_glob_unsafe_child_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_id: str
) -> None:
    from autoskillit.execution.backends import ClaudeSessionLocator

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert ClaudeSessionLocator().read_child_task(child_id) is None


def test_codex_parser_reads_prompt_label_and_last_complete_message(tmp_path: Path) -> None:
    child_id = "codex-child"
    path = _write_jsonl(
        tmp_path / "rollout.jsonl",
        [
            _codex_meta(
                child_id,
                source={"subagent": {"thread_spawn": {"agent_path": "parent/arch"}}},
            ),
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [_text("Review ", "input_text"), _text("the diff", "text")],
                },
            ),
            _codex_event(
                "response_item",
                {"type": "agent_message", "message": "encrypted content is unavailable"},
            ),
            _codex_event("event_msg", {"type": "task_started"}),
            _codex_event("event_msg", {"type": "task_complete", "last_agent_message": "[]"}),
        ],
    )

    result = parse_codex_child_task(path, child_id)

    assert result is not None
    assert result.assignment_prompt == "Review \nthe diff"
    assert result.assignment_label == "arch"
    assert result.terminal is True
    assert result.final_text == "[]"
    assert result.final_stop_reason == "task_complete"
    assert result.output_limit_stops == 0


@pytest.mark.parametrize(
    ("extra_meta", "expected_label"),
    [
        ({"agent_path": "fallback/child-label"}, "child-label"),
        (
            {
                "source": {"subagent": {"thread_spawn": {"agent_path": "parent/nested-label"}}},
                "agent_path": "fallback/ignored",
            },
            "nested-label",
        ),
    ],
)
def test_codex_parser_resolves_assignment_label_paths(
    tmp_path: Path, extra_meta: dict[str, object], expected_label: str
) -> None:
    child_id = "path-child"
    path = _write_jsonl(
        tmp_path / "rollout.jsonl",
        [
            _codex_meta(child_id, **extra_meta),
            _codex_event("event_msg", {"type": "task_complete", "last_agent_message": "done"}),
        ],
    )

    result = parse_codex_child_task(path, child_id)

    assert result is not None
    assert result.assignment_label == expected_label


@pytest.mark.parametrize("last_type", ["task_started", "turn_aborted"])
def test_codex_parser_marks_noncomplete_last_lifecycle_nonterminal(
    tmp_path: Path, last_type: str
) -> None:
    child_id = f"codex-{last_type}"
    path = _write_codex(
        tmp_path / "rollout.jsonl",
        child_id,
        [
            _codex_event("event_msg", {"type": "task_complete", "last_agent_message": "done"}),
            _codex_event("event_msg", {"type": last_type}),
        ],
    )

    result = parse_codex_child_task(path, child_id)

    assert result is not None
    assert result.terminal is False
    assert result.final_stop_reason == last_type


def test_codex_parser_skips_partial_final_jsonl_line(tmp_path: Path) -> None:
    child_id = "partial-child"
    path = tmp_path / "partial.jsonl"
    path.write_text(
        json.dumps(_codex_meta(child_id))
        + "\n"
        + json.dumps(_codex_event("event_msg", {"type": "task_started"}))
        + '\n{"type":"event_msg","payload":{"type":"task_complete"',
        encoding="utf-8",
    )

    result = parse_codex_child_task(path, child_id)

    assert result is not None
    assert result.terminal is False
    assert result.final_stop_reason == "task_started"


def test_codex_parser_rejects_mismatched_session_meta_id(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "wrong-id.jsonl", [_codex_meta("other-child")])

    assert parse_codex_child_task(path, "expected-child") is None


def test_codex_locator_uses_located_child_rollout_and_returns_none_on_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.backends.codex import CodexSessionLocator

    child_id = "located-child"
    path = _write_codex(
        tmp_path / "rollout.jsonl",
        child_id,
        [_codex_event("event_msg", {"type": "task_complete", "last_agent_message": "done"})],
    )
    monkeypatch.setattr(CodexSessionLocator, "locate_session", lambda self, sid: path)
    locator = CodexSessionLocator(store_root=tmp_path)

    assert locator.read_child_task(child_id) == parse_codex_child_task(path, child_id)

    monkeypatch.setattr(CodexSessionLocator, "locate_session", lambda self, sid: None)
    assert locator.read_child_task(child_id) is None


class _ReadTaskLocator:
    def __init__(self, result: ChildTaskTranscript | None = None, error: Exception | None = None):
        self.result = result
        self.error = error

    def list_sessions(self, cwd: str) -> tuple[()]:
        return ()

    def locate_session(self, session_id: str) -> Path | None:
        return None

    def project_log_dir(self, cwd: str) -> Path:
        return Path("/stub")

    def session_log_path(self, cwd: str, session_id: str) -> Path | None:
        return None

    def read_child_task(self, child_id: str) -> ChildTaskTranscript | None:
        if self.error is not None:
            raise self.error
        return self.result


def _child_view(child_id: str) -> ChildTaskTranscript:
    return ChildTaskTranscript(
        child_id=child_id,
        transcript_locator="/child.jsonl",
        assignment_prompt="",
        assignment_label="",
        terminal=False,
        final_text=None,
        final_stop_reason="",
        output_limit_stops=0,
    )


def test_composite_locator_returns_first_child_transcript() -> None:
    result = _child_view("child")
    locator = CompositeSessionLocator((_ReadTaskLocator(), _ReadTaskLocator(result)))

    assert locator.read_child_task("child") == result


def test_composite_locator_preserves_transcript_read_errors() -> None:
    locator = CompositeSessionLocator((_ReadTaskLocator(error=OSError("unreadable")),))

    with pytest.raises(OSError, match="unreadable"):
        locator.read_child_task("child")


def test_codex_child_usage_scans_whole_verified_rollout_and_deduplicates_snapshots(
    tmp_path: Path,
) -> None:
    child_thread_id = "child-thread"
    path = _write_codex(
        tmp_path / "child-rollout.jsonl",
        child_thread_id,
        [
            {"type": "turn_context", "payload": {"model": "codex-child-model"}},
            _codex_usage_snapshot(13, "2026-09-01T10:00:01Z"),
            _codex_usage_snapshot(13, "2026-09-01T10:00:02Z"),
        ],
    )
    locator = SimpleNamespace(locate_session=lambda _thread_id: path)

    dedicated = extract_codex_child_turn_usage(
        path,
        child_thread_id,
        provider_used="codex",
    )
    interval = extract_codex_turn_usage(
        locator,
        child_thread_id,
        "2026-09-01T10:00:00Z",
        "2026-09-01T10:00:03Z",
        provider_used="codex",
    )

    assert dedicated == interval
    assert len(dedicated) == 1
    assert dedicated[0]["model"] == "codex-child-model"
    assert dedicated[0]["input_tokens"] == 10


def test_codex_child_usage_rejects_mismatched_rollout_identity(tmp_path: Path) -> None:
    path = _write_codex(tmp_path / "child-rollout.jsonl", "actual-child", [])

    assert (
        extract_codex_child_turn_usage(
            path,
            "different-child",
            provider_used="codex",
        )
        == []
    )
