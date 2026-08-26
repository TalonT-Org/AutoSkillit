"""Focused coverage for recipe skill-input parsing helpers extracted from _binding.py (#4854)."""

from __future__ import annotations

import pytest

from autoskillit.core import BindingFailureCode, BoundValue, BoundValueOrigin
from autoskillit.recipe._binding_input import (
    _bound_value,
    _inline_skill_inputs,
    _is_scalar,
    _origin_for,
    _resolve_hidden_value,
    _split_named_token,
    _structured_skill_inputs,
    _tokenize_skill_command,
    _unquote,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------


def test_tokenize_basic_words() -> None:
    assert _tokenize_skill_command("foo bar baz") == ("foo", "bar", "baz")


def test_tokenize_preserves_template_refs_as_atomic_tokens() -> None:
    assert _tokenize_skill_command("cmd ${{ inputs.x }} --flag") == (
        "cmd",
        "${{ inputs.x }}",
        "--flag",
    )


def test_tokenize_handles_single_quoted_value() -> None:
    # Tokenizer keeps the quotes; _unquote strips them in the parser stage.
    assert _tokenize_skill_command("cmd 'arg with space'") == ("cmd", "'arg with space'")


def test_tokenize_handles_double_quoted_value() -> None:
    # Tokenizer keeps the quotes; _unquote strips them in the parser stage.
    assert _tokenize_skill_command('cmd "arg with space"') == ("cmd", '"arg with space"')


def test_tokenize_unterminated_single_quote_raises() -> None:
    with pytest.raises(ValueError, match="unterminated quoted skill argument"):
        _tokenize_skill_command("cmd 'oops")


def test_tokenize_unterminated_double_quote_raises() -> None:
    with pytest.raises(ValueError, match="unterminated quoted skill argument"):
        _tokenize_skill_command('cmd "oops')


def test_tokenize_empty_string_returns_empty_tuple() -> None:
    assert _tokenize_skill_command("") == ()


# ---------------------------------------------------------------------------
# Quoting
# ---------------------------------------------------------------------------


def test_unquote_strips_matched_single_quotes() -> None:
    assert _unquote("'foo'") == "foo"


def test_unquote_strips_matched_double_quotes() -> None:
    assert _unquote('"foo"') == "foo"


def test_unquote_leaves_unmatched_quotes_unchanged() -> None:
    assert _unquote("'foo") == "'foo"
    assert _unquote('foo"') == 'foo"'


# ---------------------------------------------------------------------------
# Named-token recognition
# ---------------------------------------------------------------------------


def test_split_recognizes_simple_named() -> None:
    assert _split_named_token("name=value") == ("name", "value")


def test_split_recognizes_named_with_unquoted_value() -> None:
    assert _split_named_token("name=value with spaces") == ("name", "value with spaces")


def test_split_recognizes_named_with_quoted_value() -> None:
    assert _split_named_token("name='quoted'") == ("name", "quoted")


def test_split_returns_none_for_positional() -> None:
    assert _split_named_token("value") is None


def test_split_returns_none_for_invalid_identifier() -> None:
    assert _split_named_token("1bad=value") is None


def test_split_handles_underscore_prefix() -> None:
    assert _split_named_token("_name=value") == ("_name", "value")


# ---------------------------------------------------------------------------
# Inline skill-input parsing
# ---------------------------------------------------------------------------


def _contract() -> object:
    """Build a minimal single-input SkillContract via get_skill_contract."""
    from autoskillit.recipe._contracts_manifest import get_skill_contract

    return get_skill_contract(
        "dry-walkthrough",
        {
            "skills": {
                "dry-walkthrough": {
                    "inputs": [
                        {"name": "plan_path", "type": "file_path", "required": True},
                    ]
                }
            }
        },
    )


def test_inline_matches_named_to_contract_input() -> None:
    contract = _contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:dry-walkthrough plan_path=/tmp/plan.md",
        effective_command="/autoskillit:dry-walkthrough plan_path=/tmp/plan.md",
        contract=contract,
    )
    assert failures == ()
    assert len(bound) == 1
    assert bound[0].name == "plan_path"
    assert bound[0].effective_value == "/tmp/plan.md"


def test_inline_records_unknown_input_failure() -> None:
    contract = _contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:dry-walkthrough unknown=foo",
        effective_command="/autoskillit:dry-walkthrough unknown=foo",
        contract=contract,
    )
    assert any(failure.code == BindingFailureCode.UNKNOWN_SKILL_INPUT for failure in failures)
    assert len(bound) == 1
    assert bound[0].state.name == "ABSENT"


def test_inline_records_missing_required_input_failure() -> None:
    contract = _contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:dry-walkthrough",
        effective_command="/autoskillit:dry-walkthrough",
        contract=contract,
    )
    assert any(failure.code == BindingFailureCode.MISSING_SKILL_INPUT for failure in failures)
    assert len(bound) == 1
    assert bound[0].state.name == "ABSENT"


def test_inline_handles_slash_command_free_form_tail() -> None:
    """Single-input contract should preserve multiword prose tail as one value."""
    contract = _contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:dry-walkthrough the quick brown fox",
        effective_command="/autoskillit:dry-walkthrough the quick brown fox",
        contract=contract,
    )
    assert failures == ()
    assert len(bound) == 1
    assert bound[0].effective_value == "the quick brown fox"


def test_inline_records_invalid_skill_input_type_failure() -> None:
    """Wrong-typed value should produce an INVALID_SKILL_INPUT_TYPE failure."""
    from autoskillit.recipe._contracts_manifest import get_skill_contract

    contract = get_skill_contract(
        "strict-typed",
        {
            "skills": {
                "strict-typed": {
                    "inputs": [
                        {"name": "count", "type": "integer", "required": True},
                    ]
                }
            }
        },
    )
    assert contract is not None
    # Pass a non-integer string for an integer-typed input.
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:strict-typed count=not_an_int",
        effective_command="/autoskillit:strict-typed count=not_an_int",
        contract=contract,
    )
    assert any(failure.code == BindingFailureCode.INVALID_SKILL_INPUT_TYPE for failure in failures)
    assert len(bound) == 1


# ---------------------------------------------------------------------------
# Structured skill-input parsing
# ---------------------------------------------------------------------------


def _multi_input_contract() -> object:
    """Build a SkillContract with multiple typed inputs."""
    from autoskillit.recipe._contracts_manifest import get_skill_contract

    return get_skill_contract(
        "dry-walkthrough",
        {
            "skills": {
                "dry-walkthrough": {
                    "inputs": [
                        {"name": "plan_path", "type": "file_path", "required": True},
                        {"name": "issue_url", "type": "string", "required": True},
                        {"name": "enabled", "type": "boolean"},
                    ]
                }
            }
        },
    )


def test_structured_matches_keys_to_contract_inputs() -> None:
    contract = _multi_input_contract()
    assert contract is not None
    declared = {"plan_path": "/tmp/plan.md", "issue_url": "https://x/y", "enabled": True}
    bound, failures = _structured_skill_inputs(
        step_name="verify",
        declared_values=declared,
        effective_values=declared,
        contract=contract,
        hidden_inputs=frozenset(),
        ingredient_values={},
        optional_context_refs=frozenset(),
    )
    assert failures == ()
    assert len(bound) == 3
    names = tuple(value.name for value in bound)
    assert names == ("plan_path", "issue_url", "enabled")


def test_structured_applies_hidden_ingredient_substitution() -> None:
    contract = _multi_input_contract()
    assert contract is not None
    declared = {"plan_path": "${{ inputs.api_key }}", "issue_url": "https://x/y"}
    bound, failures = _structured_skill_inputs(
        step_name="verify",
        declared_values=declared,
        effective_values={"plan_path": "${{ inputs.api_key }}", "issue_url": "https://x/y"},
        contract=contract,
        hidden_inputs=frozenset({"api_key"}),
        ingredient_values={"api_key": "secret-token"},
        optional_context_refs=frozenset(),
    )
    assert failures == ()
    plan_bound = next(value for value in bound if value.name == "plan_path")
    assert plan_bound.effective_value == "secret-token"


def test_structured_records_unknown_input_failure() -> None:
    contract = _multi_input_contract()
    assert contract is not None
    declared = {"plan_path": "/tmp/x", "issue_url": "https://x/y", "phantom": "v"}
    bound, failures = _structured_skill_inputs(
        step_name="verify",
        declared_values=declared,
        effective_values=declared,
        contract=contract,
        hidden_inputs=frozenset(),
        ingredient_values={},
        optional_context_refs=frozenset(),
    )
    unknown_failures = [
        failure for failure in failures if failure.code == BindingFailureCode.UNKNOWN_SKILL_INPUT
    ]
    assert any(failure.name == "phantom" for failure in unknown_failures)
    assert len(bound) == 3


def test_structured_records_missing_required_input_failure() -> None:
    contract = _multi_input_contract()
    assert contract is not None
    declared = {"issue_url": "https://x/y"}  # plan_path missing (required)
    bound, failures = _structured_skill_inputs(
        step_name="verify",
        declared_values=declared,
        effective_values=declared,
        contract=contract,
        hidden_inputs=frozenset(),
        ingredient_values={},
        optional_context_refs=frozenset(),
    )
    missing_failures = [
        failure for failure in failures if failure.code == BindingFailureCode.MISSING_SKILL_INPUT
    ]
    assert any(failure.name == "plan_path" for failure in missing_failures)
    plan_bound = next(value for value in bound if value.name == "plan_path")
    assert plan_bound.state.name == "ABSENT"


def test_structured_records_invalid_skill_input_type_failure() -> None:
    contract = _multi_input_contract()
    assert contract is not None
    # plan_path is file_path; pass a non-scalar value (list) which fails _is_scalar.
    declared = {"plan_path": [1, 2, 3], "issue_url": "https://x/y"}
    bound, failures = _structured_skill_inputs(
        step_name="verify",
        declared_values=declared,
        effective_values=declared,
        contract=contract,
        hidden_inputs=frozenset(),
        ingredient_values={},
        optional_context_refs=frozenset(),
    )
    type_failures = [
        failure
        for failure in failures
        if failure.code == BindingFailureCode.INVALID_SKILL_INPUT_TYPE
    ]
    assert type_failures
    plan_bound = next(value for value in bound if value.name == "plan_path")
    assert plan_bound.state.name == "ABSENT"


# ---------------------------------------------------------------------------
# Provenance helpers (smoke tests)
# ---------------------------------------------------------------------------


def test_is_scalar_typeguard_accepts_str_int_bool() -> None:
    assert _is_scalar("hello")
    assert _is_scalar(42)
    assert _is_scalar(True)


def test_is_scalar_rejects_none_and_floats() -> None:
    assert not _is_scalar(None)
    assert not _is_scalar(3.14)
    assert not _is_scalar([])
    assert not _is_scalar({})


def test_origin_for_classifies_literal_context_input_template() -> None:
    origin_lit, ctx_lit, inp_lit, tmpl_lit = _origin_for("hello")
    assert origin_lit is BoundValueOrigin.LITERAL
    assert ctx_lit == () and inp_lit == () and tmpl_lit == ()

    origin_ctx, ctx_ctx, _, _ = _origin_for("${{ context.worktree_path }}")
    assert origin_ctx is BoundValueOrigin.CONTEXT
    assert ctx_ctx == ("worktree_path",)

    origin_inp, _, inp_inp, _ = _origin_for("${{ inputs.branch }}")
    assert origin_inp is BoundValueOrigin.RECIPE_INPUT
    assert inp_inp == ("branch",)


def test_bound_value_propagates_origin() -> None:
    value = _bound_value("plan_path", "/tmp/plan.md", "/tmp/plan.md")
    assert isinstance(value, BoundValue)
    assert value.name == "plan_path"
    assert value.declared_value == "/tmp/plan.md"
    assert value.effective_value == "/tmp/plan.md"
    assert value.origin is BoundValueOrigin.LITERAL


def test_resolve_hidden_value_substitutes_matching_ref() -> None:
    resolved = _resolve_hidden_value(
        "${{ inputs.api_key }}",
        "${{ inputs.api_key }}",
        hidden_inputs=frozenset({"api_key"}),
        ingredient_values={"api_key": "secret"},
    )
    assert resolved == "secret"


def test_resolve_hidden_value_passthrough_when_not_hidden() -> None:
    resolved = _resolve_hidden_value(
        "${{ inputs.api_key }}",
        "${{ inputs.api_key }}",
        hidden_inputs=frozenset(),
        ingredient_values={"api_key": "secret"},
    )
    assert resolved == "${{ inputs.api_key }}"


def test_resolve_hidden_value_non_string_declared_passes_through() -> None:
    resolved = _resolve_hidden_value(
        42,
        "anything",
        hidden_inputs=frozenset(),
        ingredient_values={},
    )
    assert resolved == "anything"
