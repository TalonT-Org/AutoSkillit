"""Contract tests for tools_recipe.py MCP tool docstrings."""

from __future__ import annotations


def test_load_recipe_instructs_step_name_exact_yaml_key():
    """
    The load_recipe docstring must explicitly instruct that step_name
    must match the YAML step key exactly, with no disambiguation suffixes.
    """
    import inspect

    from autoskillit.server.tools_recipe import load_recipe

    doc = inspect.getdoc(load_recipe) or ""
    assert "must match the yaml step key exactly" in doc.lower(), (
        "tools_recipe.load_recipe must instruct orchestrators that step_name "
        "must be the exact YAML key with no disambiguation suffixes appended"
    )


def test_load_recipe_does_not_instruct_get_token_summary_pre_staging():
    """
    The load_recipe docstring must not instruct the orchestrator to call
    get_token_summary and write the result to a token_summary.md file.
    That path is obsolete — skills self-retrieve with cwd_filter.
    Orchestrator-side pre-staging bypasses pipeline scoping and uses
    the contaminated server-side singleton.
    """
    import inspect

    from autoskillit.server.tools_recipe import load_recipe

    doc = inspect.getdoc(load_recipe) or ""
    # Check that the active invocation form is absent. After Step 2b, the replacement
    # instruction says "Do NOT call get_token_summary" (which still contains the name
    # but not the call form), so we match on the function-call syntax "get_token_summary("
    # to distinguish instruction from prohibition.
    assert "get_token_summary(" not in doc, (
        "tools_recipe.load_recipe must not instruct orchestrators to call "
        "get_token_summary — use skill self-retrieval"
    )
