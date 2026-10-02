"""Documented ChatGPT-plan OAuth and tool-free Responses transport.

No borrowed client IDs, private ChatGPT endpoints, CLI credential scraping, or
fallback to billed API keys. Network exceptions never expose provider bodies.
"""
from __future__ import annotations

import json
import http.client
import re
import secrets
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from fastmdxplora.agent.oauth_transactions import RESOURCE, TOKEN_URL, AuthorizationGrant

ISSUER = "https://auth.openai.com"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
MAX_BYTES = 8_000_000


class ConnectionError(RuntimeError):
    """Sanitized connection failure, safe to return through the dashboard."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionError("The provider returned an unexpected redirect. Reconnect using its normal sign-in.")


def _open(request, *, timeout=30):
    host = urlsplit(request.full_url)
    try:
        port = host.port
    except ValueError:
        raise ConnectionError("The provider endpoint is invalid.") from None
    if (host.scheme != "https" or host.hostname not in {"auth.openai.com", "api.openai.com"}
            or host.username or host.password or host.fragment or port not in {None, 443}):
        raise ConnectionError("The provider endpoint is invalid.")
    try:
        return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            message = "The provider session expired or was refused. Reconnect before sending another request."
        elif exc.code in {403, 429}:
            message = "The provider refused access or reached its usage limit. Check the selected account and plan usage."
        else:
            message = "The provider request failed. Retry later or reconnect through the dashboard."
        raise ConnectionError(message) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise ConnectionError("The provider's TLS certificate could not be verified. Check the computer clock and trusted certificate setup; sign-in remains disabled until verification succeeds.") from None
        raise ConnectionError("The provider could not be reached. Check the connection and try again.") from None
    except (OSError, TimeoutError):
        raise ConnectionError("The provider could not be reached. Check the connection and try again.") from None


def _read(response, size, *, line=False):
    try:
        return response.readline(size) if line else response.read(size)
    except (OSError, TimeoutError, http.client.HTTPException):
        raise ConnectionError("The provider connection was interrupted. Retry the explanation when ready.") from None


def request_json(url, *, form=None, token=None, opener=_open):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urlencode(form).encode("utf-8")
    with opener(urllib.request.Request(url, data=data, headers=headers)) as response:
        raw = _read(response, MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ConnectionError("The provider response exceeded its size limit.")
    try:
        answer = json.loads(raw)
    except (ValueError, TypeError):
        raise ConnectionError("The provider returned an invalid response.") from None
    if not isinstance(answer, dict):
        raise ConnectionError("The provider returned an invalid response.")
    return answer


def discovery(*, fetch=request_json):
    answer = fetch(DISCOVERY)
    jwks = urlsplit(str(answer.get("jwks_uri", "")))
    if (answer.get("issuer") != ISSUER or answer.get("token_endpoint") != TOKEN_URL
            or jwks.scheme != "https" or jwks.hostname != "auth.openai.com"
            or jwks.username or jwks.password or jwks.fragment or jwks.port not in {None, 443}):
        raise ConnectionError("The provider's authentication metadata could not be verified.")
    return answer


def verify_identity(token, client_id, *, nonce=None, fetch=request_json):
    try:
        import jwt
    except ImportError:
        raise ConnectionError("Install fastmdxplora[agent] to enable verified browser sign-in.") from None
    try:
        if not isinstance(token, str) or len(token) > 32_768:
            raise ValueError()
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "ES256"} or not isinstance(header.get("kid"), str):
            raise ValueError()
        metadata = discovery(fetch=fetch)
        keys = fetch(metadata["jwks_uri"]).get("keys", [])
        matching = [key for key in keys if key.get("kid") == header["kid"] and key.get("use", "sig") == "sig"
                    and key.get("alg", algorithm) == algorithm]
        if len(matching) != 1:
            raise ValueError()
        key = jwt.PyJWK.from_dict(matching[0], algorithm=algorithm).key
        required = ["iss", "aud", "exp", "iat", "sub"] + (["nonce"] if nonce is not None else [])
        claims = jwt.decode(token, key, algorithms=[algorithm], audience=client_id, issuer=ISSUER,
                            options={"require": required}, leeway=30)
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError()
        if nonce is not None and (not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce)):
            raise ValueError()
        return claims
    except ConnectionError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, jwt.PyJWTError):
        raise ConnectionError("The sign-in identity could not be verified. Start a new sign-in.") from None


def _validated_tokens(answer):
    if (str(answer.get("token_type", "")).lower() != "bearer" or not isinstance(answer.get("access_token"), str)
            or not answer["access_token"] or len(answer["access_token"]) > 32_768):
        raise ConnectionError("The provider did not issue a valid access session.")
    scopes = answer.get("scope", "")
    if not isinstance(scopes, str) or "chatgpt.tokens.use.direct" not in scopes.split():
        raise ConnectionError("ChatGPT plan access was not granted. Enable it in the provider authorization screen.")
    try:
        duration = float(answer["expires_in"])
    except (ValueError, TypeError, KeyError):
        raise ConnectionError("The provider did not issue a valid session lifetime.") from None
    if not 0 < duration <= 86400:
        raise ConnectionError("The provider did not issue a valid session lifetime.")
    refresh = answer.get("refresh_token")
    if not isinstance(refresh, str) or not refresh or len(refresh) > 32_768:
        raise ConnectionError("The provider did not issue a renewable session. Start a new sign-in.")
    return {"access_token": answer["access_token"], "refresh_token": refresh,
            "scopes": scopes.split(), "expires_at": time.time() + duration,
            "saved_at": time.time()}


def exchange(grant: AuthorizationGrant, *, fetch=request_json):
    answer = fetch(TOKEN_URL, form=grant.token_form())
    tokens = _validated_tokens(answer)
    identity = verify_identity(answer.get("id_token"), grant.client_id, nonce=grant.nonce, fetch=fetch)
    return {**tokens, "id_token": answer["id_token"], "client_id": grant.client_id,
            "subject": identity["sub"], "issuer": ISSUER, "email": str(identity.get("email", ""))[:320]}


def refresh(account, *, fetch=request_json):
    answer = fetch(TOKEN_URL, form={"grant_type": "refresh_token", "client_id": account["client_id"],
                                  "refresh_token": account["refresh_token"], "resource": RESOURCE})
    tokens = _validated_tokens(answer)
    retained = account["id_token"]
    if answer.get("id_token"):
        identity = verify_identity(answer["id_token"], account["client_id"], fetch=fetch)
        if identity["sub"] != account["subject"]:
            raise ConnectionError("The refreshed identity changed. Reconnect the selected account.")
        retained = answer["id_token"]
    return {**account, **tokens, "id_token": retained}


def models(token, *, fetch=request_json):
    rows = fetch(RESOURCE + "/models", token=token).get("models")
    if not isinstance(rows, list):
        raise ConnectionError("The account's model catalog is unavailable. Reconnect or retry later.")
    return [{"id": row["slug"], "label": str(row.get("display_name", row["slug"]))[:200]}
            for row in rows if isinstance(row, dict) and row.get("visibility") == "list"
            and isinstance(row.get("slug"), str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", row["slug"])]


def revoke(account, *, fetch=request_json, opener=_open):
    endpoint = discovery(fetch=fetch).get("revocation_endpoint", "")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "auth.openai.com" or parsed.username or parsed.password:
        raise ConnectionError("The provider's sign-out endpoint could not be verified.")
    form = {"token": account["refresh_token"], "token_type_hint": "refresh_token", "client_id": account["client_id"]}
    request = urllib.request.Request(endpoint, data=urlencode(form).encode(),
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    with opener(request) as response:
        if response.status != 200:
            raise ConnectionError("Remote sign-out was not confirmed.")


def complete(token, model, prompt, on_text=None, *, opener=_open):
    payload = {"model": model, "input": [{"role": "user", "content": prompt}],
               "store": False, "stream": True, "tools": [], "tool_choice": "none"}
    data = json.dumps(payload).encode("utf-8")
    if len(data) > 1_000_000:
        raise ConnectionError("The selected context is too large. Narrow the selection and try again.")
    request = urllib.request.Request(RESOURCE + "/responses", data=data,
                                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                             "Accept": "text/event-stream"})
    parts, event_lines, consumed = [], [], 0
    with opener(request, timeout=120) as response:
        while True:
            raw = _read(response, 1_000_001, line=True)
            consumed += len(raw)
            if len(raw) > 1_000_000 or consumed > MAX_BYTES:
                raise ConnectionError("The explanation exceeded its response limit.")
            if not raw:
                raise ConnectionError("The explanation stream ended before completion. Retry when ready.")
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    event_lines.append(line[5:].lstrip())
                if line or not event_lines:
                    continue
                event = json.loads("\n".join(event_lines))
                event_lines = []
            except (ValueError, UnicodeError):
                raise ConnectionError("The provider returned an invalid explanation stream.") from None
            if not isinstance(event, dict):
                raise ConnectionError("The provider returned an invalid explanation event.")
            kind = event.get("type")
            if kind in {"error", "response.failed", "response.incomplete"}:
                raise ConnectionError("The provider could not complete the explanation. Check plan usage or retry later.")
            if kind == "response.output_text.delta":
                piece = event.get("delta")
                if not isinstance(piece, str):
                    raise ConnectionError("The provider returned invalid explanation text.")
                parts.append(piece)
                if on_text:
                    on_text(piece)
            if kind == "response.completed":
                result = event.get("response", {})
                if not isinstance(result, dict) or not isinstance(result.get("output", []), list):
                    raise ConnectionError("The provider returned an invalid completion event.")
                if result.get("status") != "completed" or result.get("error"):
                    raise ConnectionError("The provider did not finish the explanation.")
                if any(not isinstance(row, dict) or row.get("type") not in {"message", "reasoning"} for row in result.get("output", [])):
                    raise ConnectionError("The provider returned a tool action, which this Agent cannot execute.")
                if not parts:
                    raise ConnectionError("The provider completed without explanation text.")
                return "".join(parts)
