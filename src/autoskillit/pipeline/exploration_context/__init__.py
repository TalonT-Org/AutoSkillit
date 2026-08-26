"""Stable public surface for the exploration broker context store.

This package is the public facade.  Private ``_``-prefixed shards hold
the implementation details; every public name re-exported here is
also listed in ``__all__`` so ``from autoskillit.pipeline.exploration_context
import <name>`` works for any name the pre-decomposition monolithic
``exploration_context.py`` advertised.

Each shard that logs owns its own ``logger = get_logger(__name__)`
so log records retain the originating module's ``__name__`` — that is
why no ``logger`` is re-exported here.
"""

from autoskillit.core import (
    CapabilityResolution,
    CapabilityResolutionStatus,
    ExplorationContextStoreProtocol,
)
from autoskillit.pipeline.exploration_context_durable import (
    EXPLORATION_AUTHORITY_PATH_ENV,
    EXPLORATION_CAPABILITY_ENV,
    EXPLORATION_PRINCIPAL_ROLE,
    EXPLORATION_ROLE_ENV,
    EXPLORATION_SESSION_ENV,
)

from ._constants import EXPLORER_INELIGIBLE_SESSION_TYPES, EXPLORER_ROLE_NAMES
from ._eligibility import (
    exploration_auto_provision_eligible,
    is_explorer_binding_eligible,
)
from ._failure_codes import (
    EXPLORATION_STORE_FAILURE_CODES,
    resolve_exploration_store_failure_code,
)
from ._store import OwnerBoundExplorationContextStore
from ._types import (
    ExplorationContext,
    ExplorationLaunchBinding,
    ExplorationServiceProtocol,
)

__all__ = [
    "CapabilityResolution",
    "CapabilityResolutionStatus",
    "EXPLORATION_STORE_FAILURE_CODES",
    "EXPLORER_ROLE_NAMES",
    "EXPLORER_INELIGIBLE_SESSION_TYPES",
    "EXPLORATION_AUTHORITY_PATH_ENV",
    "EXPLORATION_CAPABILITY_ENV",
    "EXPLORATION_PRINCIPAL_ROLE",
    "EXPLORATION_ROLE_ENV",
    "EXPLORATION_SESSION_ENV",
    "ExplorationLaunchBinding",
    "ExplorationContext",
    "ExplorationContextStoreProtocol",
    "ExplorationServiceProtocol",
    "OwnerBoundExplorationContextStore",
    "exploration_auto_provision_eligible",
    "is_explorer_binding_eligible",
    "resolve_exploration_store_failure_code",
]
