"""Focused coverage for recipe skill-input parsing helpers extracted from _binding.py (#4854)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from autoskillit.core import BindingFailureCode, BoundValue, BoundValueOrigin, BoundValueState
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

if TYPE_CHECKING:
    from autoskillit.recipe._contracts_types import SkillContract

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


def _contract() -> SkillContract | None:
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
    assert bound[0].state is BoundValueState.ABSENT


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
    assert bound[0].state is BoundValueState.ABSENT


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


def _integer_contract() -> SkillContract | None:
    """Build a SkillContract with a single strict-typed integer input."""
    from autoskillit.recipe._contracts_manifest import get_skill_contract

    return get_skill_contract(
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


def test_inline_records_invalid_skill_input_type_failure() -> None:
    """Wrong-typed value should produce an INVALID_SKILL_INPUT_TYPE failure."""
    contract = _integer_contract()
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


def _multi_input_inline_contract() -> SkillContract | None:
    """Build a multi-input SkillContract suitable for inline parsing tests."""
    from autoskillit.recipe._contracts_manifest import get_skill_contract

    return get_skill_contract(
        "dry-walkthrough",
        {
            "skills": {
                "dry-walkthrough": {
                    "inputs": [
                        {"name": "plan_path", "type": "file_path", "required": True},
                        {"name": "issue_url", "type": "string", "required": True},
                    ]
                }
            }
        },
    )


def test_inline_records_dead_skill_input_for_excess_positional() -> None:
    """Excess positional args (more than declared inputs) emit DEAD_SKILL_INPUT."""
    contract = _multi_input_inline_contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:dry-walkthrough /tmp/a https://x /extra",
        effective_command="/autoskillit:dry-walkthrough /tmp/a https://x /extra",
        contract=contract,
    )
    dead_failures = [
        failure for failure in failures if failure.code == BindingFailureCode.DEAD_SKILL_INPUT
    ]
    assert len(dead_failures) == 1
    assert dead_failures[0].name == "arg2"
    assert len(bound) == 2


def test_inline_records_ambiguous_skill_input_for_duplicate_name() -> None:
    """Same input supplied twice (positional then named) emits AMBIGUOUS_SKILL_INPUT."""
    contract = _multi_input_inline_contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:dry-walkthrough /tmp/plan.md plan_path=/tmp/other.md",
        effective_command="/autoskillit:dry-walkthrough /tmp/plan.md plan_path=/tmp/other.md",
        contract=contract,
    )
    ambiguous_failures = [
        failure for failure in failures if failure.code == BindingFailureCode.AMBIGUOUS_SKILL_INPUT
    ]
    assert len(ambiguous_failures) == 1
    assert ambiguous_failures[0].name == "plan_path"
    plan_bound = next(value for value in bound if value.name == "plan_path")
    assert plan_bound.effective_value == "/tmp/plan.md"


def test_inline_records_invalid_skill_command_for_arg_count_mismatch() -> None:
    """Declared and effective commands with different arg counts emit INVALID_SKILL_COMMAND."""
    contract = _multi_input_inline_contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command="/autoskillit:dry-walkthrough /tmp/plan.md https://x",
        effective_command="/autoskillit:dry-walkthrough /tmp/plan.md",
        contract=contract,
    )
    invalid_command_failures = [
        failure for failure in failures if failure.code == BindingFailureCode.INVALID_SKILL_COMMAND
    ]
    assert len(invalid_command_failures) == 1
    assert invalid_command_failures[0].name == "skill_command"
    assert bound == ()


def test_inline_records_invalid_skill_command_for_named_token_misalignment() -> None:
    """Declared/effective named tokens with different names emit INVALID_SKILL_COMMAND."""
    contract = _multi_input_inline_contract()
    assert contract is not None
    bound, failures = _inline_skill_inputs(
        step_name="verify",
        declared_command=(
            "/autoskillit:dry-walkthrough plan_path=/tmp/plan.md issue_url=https://x"
        ),
        effective_command="/autoskillit:dry-walkthrough plan_path=/tmp/plan.md different=foo",
        contract=contract,
    )
    invalid_command_failures = [
        failure for failure in failures if failure.code == BindingFailureCode.INVALID_SKILL_COMMAND
    ]
    assert len(invalid_command_failures) == 1
    assert invalid_command_failures[0].name == "issue_url"
    assert any(
        failure.code == BindingFailureCode.INVALID_SKILL_COMMAND
        and failure.message == "declared and effective named arguments do not align"
        for failure in failures
    )
    # plan_path binds successfully; issue_url stays unbound (absent) because
    # the misalignment triggered continue before assignment.
    names = tuple(value.name for value in bound)
    assert names == ("plan_path", "issue_url")
    issue_bound = next(value for value in bound if value.name == "issue_url")
    assert issue_bound.state is BoundValueState.ABSENT


# ---------------------------------------------------------------------------
# Structured skill-input parsing
# ---------------------------------------------------------------------------


def _multi_input_contract() -> SkillContract | None:
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
    assert plan_bound.state is BoundValueState.ABSENT


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
    assert len(type_failures) == 1
    assert type_failures[0].name == "plan_path"
    plan_bound = next(value for value in bound if value.name == "plan_path")
    assert plan_bound.state is BoundValueState.ABSENT


def _optional_context_contract() -> SkillContract | None:
    """Build a contract with an optional string input declaring an absence_value."""
    from autoskillit.recipe._contracts_manifest import get_skill_contract

    return get_skill_contract(
        "context-optional",
        {
            "skills": {
                "context-optional": {
                    "inputs": [
                        {
                            "name": "worktree_label",
                            "type": "string",
                            "required": False,
                            "absence_value": "(default)",
                        },
                    ]
                }
            }
        },
    )


def test_structured_resolves_exact_context_ref_via_absence_value() -> None:
    """An exact ${{ context.X }} ref matched by optional_context_refs resolves to absence_value."""
    contract = _optional_context_contract()
    assert contract is not None
    declared = {"worktree_label": "${{ context.worktree_path }}"}
    bound, failures = _structured_skill_inputs(
        step_name="verify",
        declared_values=declared,
        effective_values=declared,
        contract=contract,
        hidden_inputs=frozenset(),
        ingredient_values={},
        optional_context_refs=frozenset({"worktree_path"}),
    )
    assert failures == ()
    label_bound = next(value for value in bound if value.name == "worktree_label")
    assert label_bound.declared_value == "${{ context.worktree_path }}"
    assert label_bound.effective_value == "(default)"
    assert label_bound.absence_value == "(default)"


def test_structured_records_replaced_absence_value_for_optional_context_dep() -> None:
    """Non-required context-dependent value retains input absence_value via replace."""
    contract = _optional_context_contract()
    assert contract is not None
    declared = {"worktree_label": "prefix ${{ context.branch }} suffix"}
    bound, failures = _structured_skill_inputs(
        step_name="verify",
        declared_values=declared,
        effective_values=declared,
        contract=contract,
        hidden_inputs=frozenset(),
        ingredient_values={},
        optional_context_refs=frozenset({"branch"}),
    )
    assert failures == ()
    label_bound = next(value for value in bound if value.name == "worktree_label")
    assert label_bound.absence_value == "(default)"
    assert label_bound.effective_value == "prefix ${{ context.branch }} suffix"


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


def test_origin_for_classifies_template_when_ref_embedded_in_text() -> None:
    """Mixed values containing ${{ or {{AUTOSKILLIT_ but not exact refs yield TEMPLATE."""
    origin_mixed, ctx_mixed, inp_mixed, tmpl_mixed = _origin_for(
        "prefix ${{ context.branch }} suffix"
    )
    assert origin_mixed is BoundValueOrigin.TEMPLATE
    assert ctx_mixed == ("branch",)
    assert inp_mixed == ()
    assert tmpl_mixed == ()

    origin_autoskillit, _, _, tmpl_autoskillit = _origin_for("{{AUTOSKILLIT_X}}")
    assert origin_autoskillit is BoundValueOrigin.TEMPLATE
    assert tmpl_autoskillit == ("AUTOSKILLIT_X",)

    origin_dollar, _, _, _ = _origin_for("value with ${{ stray")
    assert origin_dollar is BoundValueOrigin.TEMPLATE


def test_bound_value_propagates_origin() -> None:
    value = _bound_value("plan_path", "/tmp/plan.md", "/tmp/plan.md")
    assert isinstance(value, BoundValue)
    assert value.name == "plan_path"
    assert value.declared_value == "/tmp/plan.md"
    assert value.effective_value == "/tmp/plan.md"
    assert value.origin is BoundValueOrigin.LITERAL


def test_bound_value_propagates_template_origin_with_dependencies() -> None:
    """_bound_value propagates TEMPLATE origin when declared embeds refs in surrounding text."""
    value = _bound_value("plan_path", "prefix ${{ context.branch }} suffix", "prefix main suffix")
    assert value.origin is BoundValueOrigin.TEMPLATE
    assert value.context_dependencies == ("branch",)
    assert value.declared_value == "prefix ${{ context.branch }} suffix"
    assert value.effective_value == "prefix main suffix"


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
