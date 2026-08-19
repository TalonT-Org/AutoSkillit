"""CLI contracts for explicit retained Codex attempt reconciliation."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_codex_attempts_command_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    import autoskillit.cli.ops as ops_pkg
    from autoskillit import cli

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(ops_pkg, "run_codex_attempts", lambda **kwargs: calls.append(kwargs))

    cli.codex_attempts(
        discard_view="0123456789abcdef-1",
        reason="reviewed",
        output_json=True,
    )

    assert calls == [
        {
            "discard_view": "0123456789abcdef-1",
            "reason": "reviewed",
            "output_json": True,
        }
    ]


def test_codex_attempts_lists_without_recovery_and_renders_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import autoskillit.execution as execution
    from autoskillit.cli.ops import run_codex_attempts

    class Store:
        def __init__(self, *, log_dir: object) -> None:
            assert log_dir == "log-root"

        def list_retained_attempt_views(self) -> tuple[dict[str, object], ...]:
            return (
                {
                    "view_id": "0123456789abcdef-1",
                    "state": "finalizing",
                    "eligible": True,
                    "detail": "retained schema-v1 unknown with empty staged roots",
                },
            )

        def discard_attempt_view(self, _view_id: str, _reason: str) -> dict[str, object]:
            raise AssertionError("read-only listing must not discard or recover")

    monkeypatch.setattr("autoskillit.core.default_log_dir", lambda: "log-root")
    monkeypatch.setattr(execution, "CodexSessionStore", Store)

    run_codex_attempts(output_json=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["reconciled"] is None
    assert payload["views"][0]["eligible"] is True


@pytest.mark.parametrize(
    ("discard_view", "reason", "message"),
    [
        ("0123456789abcdef-1", None, "requires --reason"),
        (None, "reviewed", "requires --discard-view"),
    ],
)
def test_codex_attempts_requires_view_and_reason_together(
    discard_view: str | None,
    reason: str | None,
    message: str,
) -> None:
    from autoskillit.cli.ops import run_codex_attempts

    with pytest.raises(ValueError, match=message):
        run_codex_attempts(discard_view=discard_view, reason=reason)
