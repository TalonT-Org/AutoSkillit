"""Recipe contract types, manifest loading, card generation, and staleness detection.

Re-export facade. Implementation: _contracts_types.py, _contracts_manifest.py,
_contracts_card.py, _contracts_staleness.py.
"""

from autoskillit.core import resolve_skill_name as resolve_skill_name  # noqa: F401
from autoskillit.recipe._contracts_card import (  # noqa: F401
    _generate_recipe_card_for_recipe,
    generate_recipe_card,
    load_recipe_card,
    validate_recipe_cards,
)
from autoskillit.recipe._contracts_manifest import (  # noqa: F401
    classify_step_arg_style,
    compute_skill_hash,
    count_positional_args,
    extract_context_refs,
    extract_input_refs,
    extract_skill_cmd_refs,
    get_callable_contract,
    get_skill_contract,
    get_tool_output_contract,
    load_bundled_manifest,
    resolve_input_specs,
)
from autoskillit.recipe._contracts_staleness import (  # noqa: F401
    check_contract_staleness,
    stale_to_suggestions,
)
from autoskillit.recipe._contracts_types import (  # noqa: F401
    _CONTEXT_REF_RE,
    _TEMPLATE_REF_RE,
    INPUT_REF_RE,
    RESULT_CAPTURE_RE,
    BlockFingerprint,
    DataFlowEntry,
    RecipeCard,
    ResultFieldSpec,
    SkillContract,
    SkillInput,
    SkillOutput,
    StaleItem,
    ToolOutputContractSpec,
    ToolOutputFieldSpec,
)
