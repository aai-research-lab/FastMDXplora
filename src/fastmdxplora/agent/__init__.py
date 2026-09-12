"""Driving the package from a program that writes configs.

Separate from the rest of the package on purpose, and the direction of
the dependency is the point: everything here imports from core, and
nothing in core imports from here. Core defines what a valid study is and
refuses what is not; this proposes studies and is refused like any other
caller.

That boundary is what makes the claim checkable. If the safety came from
this module's prompting, a caller bypassing it would lose the safety. It
does not: a hostile client calling `validate_config` directly is refused
in exactly the same way, by the same code, with the same message. This
module has no privileges.

Nothing here imports a model client or handles a key. A caller supplies a
function taking a prompt and returning text.

Installed with `pip install fastmdxplora[agent]`; the core package does
not require it.
"""

from fastmdxplora.agent.queue import Budget, Job, Queue
from fastmdxplora.agent.propose import (
    Attempt,
    Completion,
    Proposal,
    propose_config,
    prompt_for,
    repair_prompt_for,
)

__all__ = [
    "Attempt",
    "Budget",
    "Job",
    "Queue",
    "Completion",
    "Proposal",
    "propose_config",
    "prompt_for",
    "repair_prompt_for",
]
