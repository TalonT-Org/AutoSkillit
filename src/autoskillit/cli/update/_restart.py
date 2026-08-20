"""Process restart contract for post-upgrade re-exec."""

from __future__ import annotations

import os
import sys
from typing import NoReturn


def perform_restart() -> NoReturn:
    os.environ["AUTOSKILLIT_SKIP_UPDATE_CHECK"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)
