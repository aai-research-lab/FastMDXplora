"""Account login must never become an ambient login or plaintext consent."""
import asyncio
import threading

import pytest

from fastmdxplora.agent.copilot_login import authorization_url, browser_login
from fastmdxplora.agent.openai_plan import ConnectionError


class Flow:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = []
        self.urls = []
        self.cancelled = threading.Event()

    async def begin(self, provider):
        self.calls.append(("begin", provider))
        return {"flowId": "fixture-flow", "step": next(self.steps)}

    async def advance(self, flow):
        self.calls.append(("advance", flow))
        return next(self.steps)

    async def cancel(self, flow):
        self.calls.append(("cancel", flow))

    def run(self, **kwargs):
        return asyncio.run(browser_login(begin=self.begin, advance=self.advance,
                                         cancel=self.cancel, publish_url=self.urls.append,
                                         cancelled=self.cancelled, poll_interval=0, **kwargs))


def terminal(status="completed", **kwargs):
    return {"kind": "completed", "result": {"status": status, "host": "github.com",
                                             "login": "PrinceOte", **kwargs}}


def test_no_login_without_a_deliberate_request():
    flow = Flow([terminal()])
    with pytest.raises(ConnectionError, match="Connect button"):
        flow.run()
    assert flow.calls == []


def test_browser_flow_publishes_only_verified_url_and_returns_identity():
    url = "https://github.com/login/device"
    flow = Flow([{"kind": "open-url", "url": url}, {"kind": "open-url", "url": url},
                 {"kind": "awaiting"}, terminal()])
    assert flow.run(user_requested=True) == {"host": "github.com", "login": "PrinceOte",
                                              "subject": "github.com/princeote"}
    assert flow.urls == [url]
    assert not any(name == "cancel" for name, _ in flow.calls)


@pytest.mark.parametrize("url", ["http://github.com/login/device", "https://github.com.evil/login/device",
    "https://user@github.com/login/device", "https://github.com:444/login/device",
    "https://github.com/login/device#token", "https://github.com/settings/tokens",
    "https://github.com/login/device\n", "https://github.com:bad/login/device"])
def test_untrusted_browser_addresses_are_refused(url):
    with pytest.raises(ConnectionError):
        authorization_url(url)


@pytest.mark.parametrize("step", [terminal("needs-plaintext-consent"), terminal("declined"),
    terminal(host="enterprise.example"), terminal(login="bad/name"), {"kind": "input-required"},
    {"kind": "needs-interaction"}, {"kind": "future-step"}, {"kind": "error", "message": "secret"}])
def test_unsupported_or_plaintext_result_cancels_native_flow_without_echoing_diagnostics(step):
    flow = Flow([step])
    with pytest.raises(ConnectionError) as error:
        flow.run(user_requested=True)
    assert "secret" not in str(error.value)
    assert flow.calls[-1] == ("cancel", "fixture-flow")


def test_cancellation_interrupts_an_inflight_advance_and_cancels_native_flow():
    flow = Flow([{"kind": "awaiting"}])

    async def blocked_advance(_):
        flow.cancelled.set()
        await asyncio.sleep(60)

    async def run():
        with pytest.raises(ConnectionError, match="cancelled"):
            await asyncio.wait_for(browser_login(begin=flow.begin, advance=blocked_advance,
                cancel=flow.cancel, publish_url=flow.urls.append, cancelled=flow.cancelled,
                user_requested=True, poll_interval=0), timeout=2)

    asyncio.run(run())
    assert flow.calls[-1] == ("cancel", "fixture-flow")


def test_timed_out_flow_is_cancelled():
    flow = Flow([{"kind": "awaiting"}])

    async def blocked_advance(_):
        await asyncio.sleep(60)

    async def run():
        with pytest.raises(ConnectionError, match="timed out"):
            await browser_login(begin=flow.begin, advance=blocked_advance, cancel=flow.cancel,
                publish_url=flow.urls.append, cancelled=flow.cancelled, user_requested=True,
                poll_interval=0, timeout=0.02)

    asyncio.run(run())
    assert flow.calls[-1] == ("cancel", "fixture-flow")
