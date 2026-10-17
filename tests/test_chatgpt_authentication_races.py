"""Synthetic OAuth operations must not restore superseded session state."""
from __future__ import annotations

import hashlib
import multiprocessing
import threading
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest

from tests.test_agent_provider_authentication import (
    _account,
    _enable_account,
)
from tests.test_agent_provider_authentication import (
    chatgpt_vault as chatgpt_vault,
)


def _mock_revocation(monkeypatch, oauth):
    monkeypatch.setattr(oauth, "_fetch_json", lambda *_a, **_kw: {
        "revocation_endpoint": oauth.ISSUER + "/api/accounts/oauth/revoke"})

    class Reply(BytesIO):
        status = 200

    monkeypatch.setattr(oauth, "_open_https", lambda *_a, **_kw: Reply(b""))


def _blocked_refresh(monkeypatch, oauth):
    entered, release, done = (threading.Event() for _ in range(3))
    outcomes = []

    def exchange(_form):
        entered.set()
        if not release.wait(15):
            raise oauth.SignInError("Synthetic refresh was not released")
        return {"access_token": "new-synthetic-access",
                "refresh_token": "rotated-synthetic-refresh",
                "expires_in": 3600, "scope": oauth.SCOPES, "token_type": "Bearer"}

    def run():
        try:
            outcomes.append(oauth.access_token())
        except oauth.SignInError:
            outcomes.append("superseded")
        finally:
            done.set()

    monkeypatch.setattr(oauth, "_exchange", exchange)
    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(10)
    return release, done, outcomes, worker


def test_disconnect_cannot_be_undone_by_a_refresh_already_in_flight(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=0))
    _mock_revocation(monkeypatch, oauth)
    release, done, outcomes, worker = _blocked_refresh(monkeypatch, oauth)
    try:
        assert oauth.disconnect()["ok"]
        disconnected = oauth._read_store()
        assert disconnected["active_client_id"] == ""
        assert not disconnected["accounts"][0].get("refresh_token")
    finally:
        release.set()
        worker.join(15)
    assert done.is_set()
    assert oauth._read_store() == disconnected
    assert outcomes == ["superseded"]


def test_account_switch_during_refresh_keeps_the_new_selection(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=0))
    store = oauth._read_store()
    store["accounts"].append(dict(_account(expires_at=9999999999), client_id="oaiapp_other"))
    oauth._write_store(store)
    release, done, outcomes, worker = _blocked_refresh(monkeypatch, oauth)
    selected, switched = threading.Event(), []
    real_write = oauth._write_store

    def watch_selection(record):
        real_write(record)
        if record.get("active_client_id") == "oaiapp_other":
            selected.set()

    monkeypatch.setattr(oauth, "_write_store", watch_selection)
    switcher = threading.Thread(
        target=lambda: switched.append(oauth.activate(client_id="oaiapp_other")))
    switcher.start()
    try:
        assert selected.wait(10)
    finally:
        release.set()
        worker.join(15)
        switcher.join(15)
    assert done.is_set() and not switcher.is_alive()
    final = oauth._read_store()
    assert final["active_client_id"] == "oaiapp_other"
    assert switched[0]["ok"]
    assert outcomes == ["superseded"]
    assert oauth._account(final, "oaiapp_test")["refresh_token"] == "rotated-synthetic-refresh"


class _SyntheticDiskVault:
    """A shared, synthetic vault for testing real interprocess coordination."""

    def __init__(self, root):
        self.root = Path(root) / "synthetic-vault"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, service, user):
        return self.root / hashlib.sha256((service + "\0" + user).encode()).hexdigest()

    def get_password(self, service, user):
        try:
            return self._path(service, user).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def set_password(self, service, user, value):
        self._path(service, user).write_text(value, encoding="utf-8")

    def delete_password(self, service, user):
        self._path(service, user).unlink()


def _refresh_in_another_process(root, entered, release, results):
    from fastmdxplora.agent import openai_chatgpt as oauth

    oauth.user_config_dir = lambda: Path(root)
    oauth._vault = lambda: _SyntheticDiskVault(root)

    def exchange(_form):
        entered.set()
        if not release.wait(30):
            raise oauth.SignInError("Synthetic refresh release timed out")
        return {"access_token": "new-synthetic-access",
                "refresh_token": "rotated-synthetic-refresh", "expires_in": 3600,
                "scope": oauth.SCOPES, "token_type": "Bearer"}

    oauth._exchange = exchange
    try:
        results.put(oauth.access_token())
    except oauth.SignInError:
        results.put("superseded")


@pytest.mark.parametrize("action", ["disconnect", "switch"])
def test_another_dashboard_process_cannot_restore_old_authentication(
        monkeypatch, tmp_path, action):
    from fastmdxplora.agent import openai_chatgpt as oauth

    monkeypatch.setattr(oauth, "user_config_dir", lambda: tmp_path)
    monkeypatch.setattr(oauth, "_vault", lambda: _SyntheticDiskVault(tmp_path))
    _enable_account(oauth, _account(expires_at=0))
    store = oauth._read_store()
    store["accounts"].append(dict(_account(expires_at=9999999999), client_id="oaiapp_other"))
    oauth._write_store(store)
    _mock_revocation(monkeypatch, oauth)
    context = multiprocessing.get_context("spawn")
    entered, release, results = context.Event(), context.Event(), context.Queue()
    worker = context.Process(target=_refresh_in_another_process,
                             args=(str(tmp_path), entered, release, results))
    worker.start()
    try:
        assert entered.wait(20)
        if action == "disconnect":
            assert oauth.disconnect()["ok"]
        else:
            assert oauth.activate(client_id="oaiapp_other")["ok"]
        selected = oauth._read_store()["active_client_id"]
        release.set()
        worker.join(20)
        assert not worker.is_alive()
        assert worker.exitcode == 0
        assert results.get(timeout=5) == "superseded"
        final = oauth._read_store()
        assert final["active_client_id"] == selected
        account = oauth._account(final, "oaiapp_test")
        if action == "disconnect":
            assert not account.get("refresh_token")
        else:
            assert account["refresh_token"] == "rotated-synthetic-refresh"
    finally:
        release.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(10)
        results.close()
        results.join_thread()


def _callback_in_another_process(root, entered, release, results):
    from fastmdxplora.agent import openai_chatgpt as oauth

    oauth.user_config_dir = lambda: Path(root)
    oauth._vault = lambda: _SyntheticDiskVault(root)
    urls = []
    oauth.webbrowser.open = lambda url, **_kw: urls.append(url) or True
    oauth._verify_id_token = lambda *_a, **_kw: {"sub": "synthetic-subject"}

    def exchange(_form):
        entered.set()
        if not release.wait(30):
            raise oauth.SignInError("Synthetic callback release timed out")
        return {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                "id_token": "synthetic-identity", "expires_in": 3600, "scope": oauth.SCOPES}

    oauth._exchange = exchange
    connected = oauth.connect(add_account=True)
    assert connected["ok"], connected.get("message")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
    callback = query["redirect_uri"][0] + "?" + urllib.parse.urlencode({
        "state": query["state"][0], "code": "synthetic-code", "client_id": "oaiapp_pending"})
    # This is the browser's loopback request, not a request through the
    # workstation's proxy. Keep the real HTTP listener and callback checks.
    loopback = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with loopback.open(callback, timeout=40) as response:
            results.put(response.status)
    except urllib.error.HTTPError as exc:
        assert entered.is_set(), (
            f"Synthetic callback was rejected before token exchange: {exc.code} "
            f"{exc.read().decode('utf-8', errors='replace')}")
        results.put(exc.code)
    finally:
        for state in list(oauth._pending):
            oauth._finish_attempt(state)


@pytest.mark.parametrize("action", ["cancel", "switch"])
def test_another_dashboard_can_cancel_or_supersede_an_in_flight_callback(
        monkeypatch, tmp_path, action):
    from fastmdxplora.agent import openai_chatgpt as oauth

    monkeypatch.setattr(oauth, "user_config_dir", lambda: tmp_path)
    monkeypatch.setattr(oauth, "_vault", lambda: _SyntheticDiskVault(tmp_path))
    # An inherited proxy must not keep the simulated browser away from the
    # local callback. Spawned workers inherit these environment variables.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("no_proxy", "")
    _enable_account(oauth, _account(expires_at=9999999999))
    store = oauth._read_store()
    store["accounts"].append(dict(_account(expires_at=9999999999), client_id="oaiapp_other"))
    oauth._write_store(store)
    _mock_revocation(monkeypatch, oauth)
    context = multiprocessing.get_context("spawn")
    entered, release, results = context.Event(), context.Event(), context.Queue()
    worker = context.Process(target=_callback_in_another_process,
                             args=(str(tmp_path), entered, release, results))
    worker.start()
    try:
        assert entered.wait(20), f"Callback worker exit code: {worker.exitcode}"
        if action == "cancel":
            assert oauth.disconnect()["ok"]
        else:
            assert oauth.activate(client_id="oaiapp_other")["ok"]
        selected = oauth._read_store()["active_client_id"]
        release.set()
        worker.join(20)
        assert not worker.is_alive() and worker.exitcode == 0
        assert results.get(timeout=5) == 400
        final = oauth._read_store()
        assert final["active_client_id"] == selected
        assert oauth._account(final, "oaiapp_pending") is None
        assert not final["pending_operation"]
    finally:
        release.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(10)
        results.close()
        results.join_thread()


def test_loopback_sign_in_does_not_resolve_the_machine_name(monkeypatch, chatgpt_vault):
    import socket

    oauth, _vault = chatgpt_vault
    urls = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url, **_kw: urls.append(url) or True)

    def no_hostname_lookup(*_args):
        raise AssertionError("A numeric loopback listener must not resolve a hostname")

    monkeypatch.setattr(socket, "getfqdn", no_hostname_lookup)
    try:
        assert oauth.connect()["ok"]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
        redirect = query["redirect_uri"][0]
        assert urllib.parse.urlsplit(redirect).hostname == "127.0.0.1"
        callback = redirect + "?" + urllib.parse.urlencode({
            "state": query["state"][0], "error": "access_denied"})
        loopback = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with pytest.raises(urllib.error.HTTPError) as caught:
            loopback.open(callback, timeout=5)
        assert caught.value.code == 400
        assert not oauth._pending
        assert not oauth._read_store()["pending_operation"]
    finally:
        for state in list(oauth._pending):
            oauth._finish_attempt(state)


def test_a_failed_activation_cannot_roll_back_a_newer_account_choice(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=9999999999))
    store = oauth._read_store()
    store["accounts"] += [dict(_account(expires_at=0), client_id="oaiapp_other"),
                          dict(_account(expires_at=9999999999), client_id="oaiapp_newest")]
    oauth._write_store(store)
    entered, release = threading.Event(), threading.Event()

    def exchange(_form):
        entered.set()
        if not release.wait(15):
            raise oauth.SignInError("Synthetic refresh was not released")
        raise oauth.InvalidGrant("Synthetic expired grant")

    monkeypatch.setattr(oauth, "_exchange", exchange)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(oauth.activate(client_id="oaiapp_other")))
    worker.start()
    try:
        assert entered.wait(10)
        assert oauth.activate(client_id="oaiapp_newest")["ok"]
    finally:
        release.set()
        worker.join(15)
    assert not worker.is_alive() and results[0]["ok"] is False
    final = oauth._read_store()
    assert final["active_client_id"] == "oaiapp_newest"
    assert not oauth._account(final, "oaiapp_other").get("refresh_token")


def test_a_callback_commit_cannot_be_overwritten_by_an_older_refresh(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=0))
    release, done, outcomes, worker = _blocked_refresh(monkeypatch, oauth)
    blocked = oauth._exchange
    urls = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url, **_kw: urls.append(url) or True)
    monkeypatch.setattr(oauth, "_verify_id_token", lambda *_a, **_kw: {"sub": "subject-test"})

    def exchange(form):
        if form["grant_type"] == "refresh_token":
            return blocked(form)
        return {"access_token": "callback-synthetic-access",
                "refresh_token": "callback-synthetic-refresh", "id_token": "synthetic-identity",
                "expires_in": 3600, "scope": oauth.SCOPES}

    monkeypatch.setattr(oauth, "_exchange", exchange)
    try:
        assert oauth.connect(client_id="oaiapp_test")["ok"]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
        callback = query["redirect_uri"][0] + "?" + urllib.parse.urlencode({
            "state": query["state"][0], "code": "synthetic-code"})
        with urllib.request.urlopen(callback, timeout=10) as response:
            assert response.status == 200
    finally:
        release.set()
        worker.join(15)
    assert done.is_set() and outcomes == ["superseded"]
    final = oauth._read_store()
    assert final["active_client_id"] == "oaiapp_test"
    assert final["accounts"][0]["refresh_token"] == "callback-synthetic-refresh"
    assert final["accounts"][0]["session_id"]


def test_a_callback_in_flight_cannot_be_consumed_twice(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    opened, exchanges, outcomes = [], [], []
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url, **_kw: opened.append(url) or True)
    monkeypatch.setattr(oauth, "_verify_id_token", lambda *_a, **_kw: {"sub": "synthetic-sub"})

    def exchange(form):
        exchanges.append(form)
        entered.set()
        if not release.wait(15):
            raise oauth.SignInError("Synthetic callback was not released")
        return {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                "id_token": "synthetic-identity", "expires_in": 3600, "scope": oauth.SCOPES}

    monkeypatch.setattr(oauth, "_exchange", exchange)
    assert oauth.connect()["ok"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query)
    state = query["state"][0]
    callback = query["redirect_uri"][0] + "?" + urllib.parse.urlencode({
        "state": state, "code": "synthetic-code", "client_id": "oaiapp_synthetic"})

    def consume():
        with urllib.request.urlopen(callback, timeout=20) as response:
            outcomes.append(response.status)

    worker = threading.Thread(target=consume)
    worker.start()
    try:
        assert entered.wait(10)
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(callback, timeout=5)
        assert caught.value.code == 409
        assert len(exchanges) == 1
    finally:
        release.set()
        worker.join(20)
        oauth._finish_attempt(state)
    assert not worker.is_alive() and outcomes == [200]
    assert state not in oauth._pending
    assert oauth._read_store()["active_client_id"] == "oaiapp_synthetic"
