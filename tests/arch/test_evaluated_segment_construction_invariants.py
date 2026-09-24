"""Lock in EvaluatedSegment construction invariants from PR #5071 resolve-review.

The sole sanctioned projection in ``_interpreters._iter_evaluated_segments``
already guarantees non-empty tokens, aligned token metadata, and
tokens/provenance parity. These tests pin those invariants at construction.
"""

from __future__ import annotations

import pytest

from autoskillit.hooks._classification._interpreters import (
    all_evaluated_segments_with_provenance,
)
from autoskillit.hooks._classification._tokenizer import (
    ArgvToken,
    EvaluatedSegment,
    _CommandSegment,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_evaluated_segment_rejects_empty_token_list() -> None:
    """Empty tokens would let a malformed segment reach downstream consumers."""
    with pytest.raises(ValueError, match="non-empty token list"):
        EvaluatedSegment([], None, [], None, ())


@pytest.mark.parametrize("field", ["redirect_syntax", "argv_tokens"])
def test_evaluated_segment_rejects_misaligned_metadata(field: str) -> None:
    """Every shell metadata entry must refer to one token in the segment."""
    redirects = [False, False]
    argv = [
        ArgvToken("git", False, "git"),
        ArgvToken("status", False, "status"),
    ]
    if field == "redirect_syntax":
        redirects.pop()
    else:
        argv.pop()
    with pytest.raises(ValueError, match=f"{field} must align with tokens"):
        EvaluatedSegment(["git", "status"], None, redirects, argv, ())


def test_evaluated_segment_rejects_tokens_provenance_mismatch() -> None:
    """When provenance is set, tokens must equal provenance.tokens."""
    provenance = _CommandSegment(
        tokens=["git", "status"],
        redirect_syntax=[False, False],
        argv_tokens=[
            ArgvToken(text="git", fully_single_quoted=False, raw_span="git"),
            ArgvToken(text="status", fully_single_quoted=False, raw_span="status"),
        ],
    )
    with pytest.raises(ValueError, match="must match provenance.tokens"):
        EvaluatedSegment(
            ["git", "show"],
            provenance,
            provenance.redirect_syntax,
            provenance.argv_tokens,
            provenance.subshell_path,
        )


def test_evaluated_segment_accepts_matching_provenance() -> None:
    """The happy path: tokens equal provenance.tokens."""
    provenance = _CommandSegment(
        tokens=["git", "status"],
        redirect_syntax=[False, False],
        argv_tokens=[
            ArgvToken(text="git", fully_single_quoted=False, raw_span="git"),
            ArgvToken(text="status", fully_single_quoted=False, raw_span="status"),
        ],
    )
    segment = EvaluatedSegment(
        ["git", "status"],
        provenance,
        provenance.redirect_syntax,
        provenance.argv_tokens,
        provenance.subshell_path,
    )
    assert segment.tokens == provenance.tokens
    assert segment.redirect_syntax == provenance.redirect_syntax
    assert segment.argv_tokens == provenance.argv_tokens
    assert segment.subshell_path == provenance.subshell_path


def test_evaluated_segment_accepts_provenance_none() -> None:
    """Literal Python argv has no submitted source or shell-lexed argv metadata."""
    segment = EvaluatedSegment(["echo", "hello"], None, [False, False], None, (-1,))
    assert segment.cwd_override is None


@pytest.mark.parametrize("interpreter, child_path", [("eval", ()), ("bash -c", (-1,))])
def test_payloads_follow_consumer_with_correct_scope(
    interpreter: str, child_path: tuple[int, ...]
) -> None:
    segments = all_evaluated_segments_with_provenance(f"{interpreter} 'cd /tmp'; pwd")
    assert segments is not None
    assert [segment.tokens for segment in segments] == [
        [*interpreter.split(), "cd /tmp"],
        ["cd", "/tmp"],
        ["pwd"],
    ]
    assert [segment.subshell_path for segment in segments] == [(), child_path, ()]
    assert segments[1].provenance is None
    assert [token.text for token in segments[1].argv_tokens or []] == ["cd", "/tmp"]


def test_shell_payload_keeps_redirect_metadata_and_composes_local_subshell() -> None:
    segments = all_evaluated_segments_with_provenance("bash -c '(echo hi > out); pwd'; echo tail")
    assert segments is not None
    assert [segment.tokens for segment in segments] == [
        ["bash", "-c", "(echo hi > out); pwd"],
        ["echo", "hi", ">", "out"],
        ["pwd"],
        ["echo", "tail"],
    ]
    assert segments[1].redirect_syntax == [False, False, True, False]
    assert [token.text for token in segments[1].argv_tokens or []] == segments[1].tokens
    assert segments[1].subshell_path is not None
    assert segments[1].subshell_path[:1] == (-1,)
    assert len(segments[1].subshell_path) == 2
    assert segments[2].subshell_path == (-1,)
    assert segments[3].subshell_path == ()


@pytest.mark.parametrize("shell", [False, True])
def test_python_subprocess_metadata_reflects_shell_lexing(shell: bool) -> None:
    call = (
        'subprocess.run("echo hi > out", shell=True, cwd="/tmp/work")'
        if shell
        else 'subprocess.run(["echo", "hi", ">", "out"], cwd="/tmp/work")'
    )
    segments = all_evaluated_segments_with_provenance(f"python -c 'import subprocess; {call}'")
    assert segments is not None
    assert [segment.tokens for segment in segments] == [
        ["python", "-c", f"import subprocess; {call}"],
        ["echo", "hi", ">", "out"],
    ]
    assert segments[1].redirect_syntax == [False, False, shell, False]
    assert segments[1].provenance is None
    assert segments[1].cwd_override == "/tmp/work"
    assert segments[1].subshell_path is not None
    assert len(segments[1].subshell_path) == 2
    if shell:
        assert [token.text for token in segments[1].argv_tokens or []] == segments[1].tokens
    else:
        assert segments[1].argv_tokens is None
