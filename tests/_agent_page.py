"""The Agent's page in a browser test, once it can be sent to.

Enter before the page has read which study is open and its conversation is
refused by design (CI-774 L12): the message stays in the box, and the
thread gets nothing. A test that types and presses Enter straight after
`goto`, a reload or a switch of study raced the page's first reads, and
lost on a loaded machine. Every test that sends to the Agent waits here
first.
"""

from __future__ import annotations

READY = "() => window.FastMDXAgentPanel && !window.FastMDXAgentPanel.waits"


def ready(page, timeout: float = 30000) -> None:
    """Once the Agent's Send is offered: the page has read the study open
    and its conversation (`FastMDXAgentPanel.waits` is empty)."""
    page.wait_for_function(READY, timeout=timeout)
