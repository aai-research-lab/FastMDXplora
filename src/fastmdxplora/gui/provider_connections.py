"""Local dashboard provider sessions, separate from studies and API-key choices."""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from fastmdxplora.agent import openai_plan
from fastmdxplora.agent.claude_plan import ClaudePlan, unmanaged_host
from fastmdxplora.agent.claude_plan import executable as claude_executable
from fastmdxplora.agent.credential_vault import CredentialVault, VaultError
from fastmdxplora.agent.gemini_plan import GeminiPlan
from fastmdxplora.agent.gemini_plan import executable as gemini_executable
from fastmdxplora.agent.kimi_plan import KimiPlan
from fastmdxplora.agent.kimi_plan import executable as kimi_executable
from fastmdxplora.agent.oauth_transactions import AuthorizationAttempt, AuthorizationError
from fastmdxplora.agent.reasoning import decorate, validate

OPENAI_PUBLISHED = {
    "gpt-6-astra": "GPT-6 Astra", "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-sol": "GPT-6 Sol", "gpt-6-luna": "GPT-6 Luna",
}


class ProviderConnections:
    def __init__(self, *, vault=None, exchange=None, catalog=None):
        self.vault = vault or CredentialVault()
        self.exchange = exchange or openai_plan.exchange
        self.catalog = catalog or openai_plan.models
        self.lock = threading.RLock()
        self.pending = None
        self.client_pending = None
        self.listener = None
        self.timer = None
        self.status = "idle"
        self.message = ""

    def snapshot(self):
        with self.lock, self.vault.locked():
            record = self.vault.read()
            return {"ok": True, "status": self.status, "message": self.message,
                    "authorization_url": (self.client_pending or {}).get("authorization_url"),
                    "selection": record.get("selection", "api"), "active": record.get("active"),
                    "accounts": [{"id": account["id"], "provider": account.get("provider", "openai-chatgpt"),
                                  "label": account.get("email") or "ChatGPT account",
                                  "model": account.get("model", ""),
                                  "reasoning": account.get("reasoning", "default"),
                                  "reasoning_by_model": account.get("reasoning_by_model", {}),
                                  "connected": bool(account.get("refresh_token") or account.get("client_connected"))}
                                 for account in record["accounts"]]}

    def _claude(self, account):
        profile = account.get("client_profile")
        if not isinstance(profile, str) or len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise openai_plan.ConnectionError("The app's Claude profile is unavailable. Add a new connection.")
        return ClaudePlan(self.vault.root / "clients/claude" / profile)

    def _kimi(self, account):
        profile = account.get("client_profile")
        if not isinstance(profile, str) or len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise openai_plan.ConnectionError("The app's Kimi profile is unavailable. Add a new connection.")
        return KimiPlan(self.vault.root / "clients/kimi" / profile)

    def begin_claude(self, account_id=None):
        return self.begin_client("claude", account_id)

    def begin_kimi(self, account_id=None):
        return self.begin_client("kimi", account_id)

    def _gemini(self, account):
        profile = account.get("client_profile")
        if not isinstance(profile, str) or len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise openai_plan.ConnectionError("The app's Gemini profile is unavailable. Add a new connection.")
        return GeminiPlan(self.vault.root / "clients/gemini" / profile)

    def _client(self, provider, account):
        return {"claude": self._claude, "kimi": self._kimi, "gemini": self._gemini}[provider](account)

    def begin_client(self, provider, account_id=None):
        if provider == "claude":
            claude_executable()
            unmanaged_host()
        elif provider == "kimi":
            kimi_executable()
        elif provider == "gemini":
            gemini_executable()
        else:
            raise openai_plan.ConnectionError("This subscription client is unavailable.")
        label = {"claude": "Claude", "kimi": "Kimi", "gemini": "Gemini"}[provider]
        with self.lock:
            self._cancel_pending()
            with self.vault.locked():
                record = self.vault.read()
            previous = next((item for item in record["accounts"] if item["id"] == account_id), None)
            if account_id and (not previous or previous.get("provider") != provider):
                raise openai_plan.ConnectionError(f"Select a {label} connection to reconnect it.")
            pending = {"client_profile": previous["client_profile"] if previous else uuid.uuid4().hex,
                       "cancelled": threading.Event()}
            self.client_pending = pending
            self.status, self.message = "pending", f"Complete {label} subscription sign-in in the provider's browser window."

            def finish():
                try:
                    client = self._client(provider, pending)
                    def authorize(url):
                        with self.lock:
                            if self.client_pending is pending and not pending["cancelled"].is_set():
                                pending["authorization_url"] = url
                    identity = (client.login(pending["cancelled"]) if provider != "kimi"
                                else client.login(pending["cancelled"], authorize))
                    with self.lock:
                        if self.client_pending is not pending or pending["cancelled"].is_set():
                            return
                        if previous and identity["subject"] != previous["subject"]:
                            raise openai_plan.ConnectionError(f"{label} returned a different account. Add it as a separate connection.")
                        with self.vault.locked():
                            record = self.vault.read()
                            account = dict(previous or {})
                            account.update(identity, id=(previous or {}).get("id", provider + "-" + pending["client_profile"]),
                                           provider=provider, client_profile=pending["client_profile"],
                                           client_connected=True, model=account.get("model", "provider-default" if provider == "claude" else ""))
                            record["accounts"] = [item for item in record["accounts"] if item["id"] != account["id"]] + [account]
                            if len(record["accounts"]) > 20:
                                raise openai_plan.ConnectionError("The connection limit was reached.")
                            self.vault.write(record)
                        self.client_pending = None
                        self.status, self.message = "connected", f"{label} connected. Select the account and model to use it."
                except Exception as exc:
                    with self.lock:
                        if self.client_pending is pending:
                            self.client_pending = None
                            self.status = "error"
                            self.message = str(exc) if isinstance(exc, (openai_plan.ConnectionError, VaultError)) else f"{label} sign-in could not be verified. Retry in Settings."
            threading.Thread(target=finish, daemon=True).start()
            return {"ok": True, "status": "pending", "browser_managed": True}

    def begin(self, account_id=None):
        with self.lock:
            self._cancel_pending()
            with self.vault.locked():
                host = self.vault.host_id()
                record = self.vault.read()
            account = next((item for item in record["accounts"] if item["id"] == account_id), None)
            if account_id and account is None:
                raise openai_plan.ConnectionError("The selected account is unavailable. Reload connections.")
            if account and account.get("provider", "openai-chatgpt") != "openai-chatgpt":
                raise openai_plan.ConnectionError("Select a ChatGPT connection to reconnect it.")
            service = self

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass  # Callback URLs contain single-use secrets and must not be logged.

                def do_GET(self):
                    expected_host = f"127.0.0.1:{self.server.server_port}"
                    parsed = urlsplit(self.path)
                    if self.headers.get("Host") != expected_host or parsed.path != "/auth/callback":
                        self.send_error(404)
                        return
                    attempt = None
                    try:
                        with service.lock:
                            if service.listener is not self.server or service.pending is None:
                                raise AuthorizationError("This sign-in attempt is no longer active.")
                            attempt = service.pending
                            grant = attempt.consume_callback(parsed.query)
                            service.status = "verifying"
                        threading.Thread(target=service._finish, args=(attempt, grant, account), daemon=True).start()
                        text = "Authorization received. Return to FastMDXplora to check the connection."
                    except AuthorizationError as exc:
                        text = str(exc)
                        with service.lock:
                            service.message = text
                            if attempt is not None and service.pending is attempt and attempt.consumed:
                                service.status = "error"
                                service._cancel_pending()
                    body = ("<!doctype html><title>FastMDXplora sign-in</title><p>" + text + "</p>").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            listener = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
            listener.daemon_threads = True
            try:
                pending = AuthorizationAttempt(f"http://127.0.0.1:{listener.server_port}/auth/callback", host,
                                               account["client_id"] if account else "dynamic_agent_client")
            except Exception:
                listener.server_close()
                raise
            self.listener, self.pending = listener, pending
            self.status, self.message = "pending", "Complete sign-in in the provider's browser window."
            threading.Thread(target=listener.serve_forever, daemon=True).start()
            self.timer = threading.Timer(600, self._expire, args=(pending,))
            self.timer.daemon = True
            self.timer.start()
            return {"ok": True, "url": pending.authorization_url(), "status": "pending"}

    def _finish(self, attempt, grant, previous):
        try:
            account = self.exchange(grant)
            if previous and (account["client_id"] != previous["client_id"] or account["subject"] != previous["subject"]):
                raise openai_plan.ConnectionError("Sign-in returned a different account. Add it as a separate account.")
            identity = hashlib.sha256((account["client_id"] + "\0" + account["subject"]).encode()).hexdigest()[:32]
            with self.lock:
                if self.pending is not attempt:
                    return  # Cancelled/new attempt: never replace the active account.
                with self.vault.locked():
                    record = self.vault.read()
                    existing = next((item for item in record["accounts"] if item["id"] == identity), None)
                    account.update(id=identity, model=(existing or {}).get("model", ""))
                    record["accounts"] = [item for item in record["accounts"] if item["id"] != identity] + [account]
                    if len(record["accounts"]) > 20:
                        raise openai_plan.ConnectionError("The account limit was reached. Disconnect an unused account first.")
                    self.vault.write(record)
                self.status, self.message = "connected", "Connected. Select an account and model to use it."
                self._cancel_pending()
        except (openai_plan.ConnectionError, VaultError) as exc:
            with self.lock:
                if self.pending is attempt:
                    self.status, self.message = "error", str(exc)
                    self._cancel_pending()
        except Exception:
            with self.lock:
                if self.pending is attempt:
                    self.status, self.message = "error", "Sign-in could not be verified or saved. Start a new sign-in."
                    self._cancel_pending()

    def _expire(self, pending):
        with self.lock:
            if self.pending is pending:
                self.status, self.message = "expired", "Sign-in expired. Start a new sign-in."
                self._cancel_pending()

    def _cancel_pending(self):
        if self.client_pending:
            self.client_pending["cancelled"].set()
            self.client_pending = None
        if self.pending:
            self.pending.cancel()
        if self.timer:
            self.timer.cancel()
        self.timer = self.pending = None
        if self.listener:
            listener, self.listener = self.listener, None
            # Avoid holding the service lock while a callback needs it to finish.
            def stop():
                listener.shutdown()
                listener.server_close()
            threading.Thread(target=stop, daemon=True).start()

    def cancel(self):
        with self.lock:
            self._cancel_pending()
            self.status, self.message = "cancelled", "Sign-in cancelled."
        return self.snapshot()

    def _account(self, record, account_id):
        account = next((item for item in record["accounts"] if item["id"] == account_id), None)
        if account and account.get("provider") in {"claude", "kimi", "gemini"}:
            client = self._client(account["provider"], account)
            if not account.get("client_connected"):
                raise openai_plan.ConnectionError("Reconnect the selected subscription account before asking the Agent.")
            if client.identity()["subject"] != account["subject"]:
                raise openai_plan.ConnectionError("The subscription account changed. Reconnect and select it before asking.")
            return account
        if not account or not account.get("refresh_token"):
            raise openai_plan.ConnectionError("Reconnect the selected account before asking the Agent.")
        if account.get("expires_at", 0) <= time.time() + 60:
            renewed = openai_plan.refresh(account)
            account.clear()
            account.update(renewed)
            self.vault.write(record)
        return account

    def model_list(self, account_id):
        with self.vault.locked():
            record = self.vault.read()
            account = self._account(record, account_id)
            if account.get("provider") in {"claude", "kimi", "gemini"}:
                return {"ok": True, "models": decorate(account["provider"], self._client(account["provider"], account).models(account["subject"]))}
        return {"ok": True, "models": decorate("openai-chatgpt", self._openai_models(account))}

    def _openai_models(self, account):
        choices = self.catalog(account["access_token"])
        listed = {row["id"] for row in choices}
        for model, label in OPENAI_PUBLISHED.items():
            if model not in listed:
                checked_at = account.get("verified_models", {}).get(model, 0)
                choices.append({"id": model, "label": label,
                                "access_status": "verified" if time.time() - checked_at < 3600 else "unchecked"})
        return choices

    def check_model_access(self, account_id):
        """Explicit, tool-free inference checks for the requested published models."""
        verified, unavailable = [], []
        for model in OPENAI_PUBLISHED:
            with self.vault.locked():
                record = self.vault.read()
                account = self._account(record, account_id)
                if account.get("provider", "openai-chatgpt") != "openai-chatgpt":
                    raise openai_plan.ConnectionError("Choose a ChatGPT subscription account.")
                token = account["access_token"]
            try:
                openai_plan.complete(token, model, "Reply exactly: Model access verified.")
            except openai_plan.ConnectionError:
                with self.vault.locked():
                    record = self.vault.read()
                    account = self._account(record, account_id)
                    account.get("verified_models", {}).pop(model, None)
                    self.vault.write(record)
                unavailable.append(model)
                continue
            with self.vault.locked():
                record = self.vault.read()
                account = self._account(record, account_id)
                account.setdefault("verified_models", {})[model] = time.time()
                self.vault.write(record)
            verified.append(model)
        return {"ok": True, "verified": verified, "unavailable": unavailable}

    def select(self, account_id, model, reasoning="default"):
        with self.vault.locked():
            record = self.vault.read()
            account = self._account(record, account_id)
            choices = (self._client(account["provider"], account).models(account["subject"]) if account.get("provider") in {"claude", "kimi", "gemini"}
                       else self._openai_models(account))
            if model not in {item["id"] for item in choices}:
                raise openai_plan.ConnectionError("Choose a model from the selected account's current catalog.")
            row = next(row for row in decorate(account.get("provider", "openai-chatgpt"), choices) if row["id"] == model)
            validate(reasoning, row["reasoning_levels"])
            if account.get("provider", "openai-chatgpt") == "openai-chatgpt" and row.get("access_status") == "unchecked":
                # A missing catalog entry is not an entitlement denial. Check
                # this published choice before changing the saved selection.
                openai_plan.complete(account["access_token"], model,
                                     "Reply exactly: Model access verified.", reasoning=reasoning)
                account.setdefault("verified_models", {})[model] = time.time()
            account["model"] = model
            account["reasoning"] = reasoning
            account.setdefault("reasoning_by_model", {})[model] = reasoning
            record.update(active=account_id, selection="subscription")
            self.vault.write(record)
        return self.snapshot()

    def select_api(self):
        with self.vault.locked():
            record = self.vault.read()
            record["selection"] = "api"
            self.vault.write(record)

    def disconnect(self, account_id):
        with self.vault.locked():
            record = self.vault.read()
            account = next((item for item in record["accounts"] if item["id"] == account_id), None)
            if account is None:
                raise openai_plan.ConnectionError("The selected account is unavailable.")
            confirmed = True
            if account.get("provider") in {"claude", "kimi", "gemini"}:
                try:
                    client = self._client(account["provider"], account)
                    client.logout()
                    if account["provider"] == "gemini":
                        confirmed = False  # Native cache removal does not revoke Google's grant.
                except openai_plan.ConnectionError:
                    confirmed = False
                account["client_connected"] = False
            if account.get("refresh_token"):
                try:
                    openai_plan.revoke(account)
                except openai_plan.ConnectionError:
                    confirmed = False
            for key in ("access_token", "refresh_token", "id_token", "scopes", "expires_at", "saved_at"):
                account.pop(key, None)
            self.vault.write(record)
        with self.lock:
            self.status = "disconnected"
            self.message = ("Disconnected." if confirmed else
                            "Disconnected locally. Provider logout or revocation was not confirmed; check the provider's account controls.")
        return self.snapshot()

    def completion(self, *, cancelled=None):
        with self.vault.locked():
            record = self.vault.read()
            if record.get("selection") != "subscription":
                return None
            account = next((item for item in record["accounts"] if item["id"] == record.get("active")), None)
            if not account or not account.get("model"):
                raise openai_plan.ConnectionError("Select a connected account and model in Settings.")
            identity, model = account["id"], account["model"]
            reasoning = account.get("reasoning", "default")
            provider = account.get("provider", "openai-chatgpt")

        def complete(prompt, on_text=None):
            if cancelled is not None and cancelled.is_set():
                raise openai_plan.ConnectionError("The explanation was cancelled.")
            with self.vault.locked():
                current = self.vault.read()
                if current.get("selection") != "subscription" or current.get("active") != identity:
                    raise openai_plan.ConnectionError("The selected account changed. Send a new request.")
                chosen = self._account(current, identity)
                token = chosen.get("access_token")
            def guard(piece):
                if cancelled is not None and cancelled.is_set():
                    raise openai_plan.ConnectionError("The explanation was cancelled.")
                with self.vault.locked():
                    latest = self.vault.read()
                    selected = next((item for item in latest["accounts"] if item["id"] == identity), None)
                    if (latest.get("selection") != "subscription" or latest.get("active") != identity
                            or not selected or not (selected.get("refresh_token") or selected.get("client_connected"))
                            or selected.get("model") != model or selected.get("reasoning", "default") != reasoning):
                        raise openai_plan.ConnectionError("The account or model changed. Send a new request.")
                if on_text:
                    on_text(piece)
            guard("")
            if provider == "claude":
                options = {"cancelled": cancelled} if cancelled is not None else {}
                if model != "provider-default":
                    options["model"] = model
                if reasoning != "default":
                    options["reasoning"] = reasoning
                result = self._claude(chosen).complete(prompt, chosen["subject"], **options)
            elif provider in {"kimi", "gemini"}:
                options = {"cancelled": cancelled} if cancelled is not None else {}
                if reasoning != "default":
                    options["reasoning"] = reasoning
                result = self._client(provider, chosen).complete(prompt, chosen["subject"], model, **options)
            else:
                options = {"reasoning": reasoning} if reasoning != "default" else {}
                result = openai_plan.complete(token, model, prompt, guard, **options)
            guard("")
            return result
        complete.streams = provider not in {"claude", "kimi", "gemini"}
        complete.model_record = {"provider": provider, "model": model}
        if reasoning != "default":
            complete.model_record["reasoning"] = reasoning
        return complete


_SERVICE_LOCK = threading.Lock()


def service_for(runtime):
    with _SERVICE_LOCK:
        if not hasattr(runtime, "_provider_connections"):
            runtime._provider_connections = ProviderConnections()
        return runtime._provider_connections


def connections_endpoint(runtime, payload):
    service = service_for(runtime)
    try:
        action = payload.get("action", "status")
        if action == "status":
            return service.snapshot()
        if action == "connect" and payload.get("provider", "openai-chatgpt") == "openai-chatgpt":
            return service.begin(payload.get("account"))
        if action == "connect" and payload.get("provider") == "claude":
            return service.begin_claude(payload.get("account"))
        if action == "connect" and payload.get("provider") == "kimi":
            return service.begin_kimi(payload.get("account"))
        if action == "connect" and payload.get("provider") == "gemini":
            return service.begin_client("gemini", payload.get("account"))
        if action == "cancel":
            return service.cancel()
        if action == "models":
            return service.model_list(payload.get("account"))
        if action == "check-model-access":
            return service.check_model_access(payload.get("account"))
        if action == "select":
            return service.select(payload.get("account"), payload.get("model"), payload.get("reasoning", "default"))
        if action == "disconnect":
            return service.disconnect(payload.get("account"))
        return {"ok": False, "error": "This connection action is unavailable."}
    except (openai_plan.ConnectionError, AuthorizationError, VaultError) as exc:
        return {"ok": False, "error": str(exc)}
