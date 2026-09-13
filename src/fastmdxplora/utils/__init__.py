"""Internal utilities shared across phases.

Re-exports the logging API and the session-presenter API. Internal modules
import from ``fastmdxplora.utils.logging`` / ``.presenter`` directly;
users typically do not need this layer unless they want to customize
console output or the structural session layout.
"""

from fastmdxplora.utils.logging import (
    attach_file_logger,
    get_logger,
    set_level,
    own_the_console,
    release_the_console,
    setup_console,
)
from fastmdxplora.utils.native_output import suppress_native_output
from fastmdxplora.utils.presenter import (
    SessionPresenter,
    get_presenter,
    reset_presenter,
)

__all__ = [
    "SessionPresenter",
    "attach_file_logger",
    "get_logger",
    "get_presenter",
    "reset_presenter",
    "set_level",
    "own_the_console",
    "release_the_console",
    "setup_console",
    "suppress_native_output",
]
