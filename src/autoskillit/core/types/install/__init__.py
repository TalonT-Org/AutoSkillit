"""Installation and managed-home contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._type_install import *  # noqa: F403
from ._type_install import __all__ as _install_all
from ._type_managed_home import *  # noqa: F403
from ._type_managed_home import __all__ as _managed_home_all
from ._type_plugin_source import *  # noqa: F403
from ._type_plugin_source import __all__ as _plugin_source_all
from ._type_retirement_backstops import *  # noqa: F403
from ._type_retirement_backstops import __all__ as _retirement_backstops_all

if not TYPE_CHECKING:
    __all__ = _install_all + _managed_home_all + _plugin_source_all + _retirement_backstops_all
