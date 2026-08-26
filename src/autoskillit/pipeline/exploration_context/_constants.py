"""Bounded-length caps, role/session exception sets, and the shared-source-identity hash domain.

These constants are private to the ``exploration_context`` package.  The
five ``_MAX_*`` caps govern input validation across the store and its
launch adapter; ``EXPLORER_ROLE_NAMES`` and
``EXPLORER_INELIGIBLE_SESSION_TYPES`` are public re-aggregations of
bundled-core definitions.

Each shard in this package owns its own ``logger = get_logger(__name__)`
so log records retain the originating module's ``__name__`` — that is
why no logger is declared here.
"""

from autoskillit.core import BUNDLED_EXPLORER_ROLES, SessionType

_MAX_CAPABILITY_LENGTH = 128
_MAX_TTL_SECONDS = 300.0
_MAX_ACTIVE_LEASES = 256
_MAX_SOURCE_IDENTITY_LENGTH = 1_024
_SHARED_SOURCE_IDENTITY_DOMAIN = b"autoskillit.exploration.shared-source.v1\x00"

# These names are an intentionally narrow launch adapter contract.  Codex may
# preserve them while materializing an explorer child, but never mint or alter
# their authority.
EXPLORER_ROLE_NAMES = BUNDLED_EXPLORER_ROLES
EXPLORER_INELIGIBLE_SESSION_TYPES = frozenset({SessionType.ORCHESTRATOR, SessionType.FLEET})
