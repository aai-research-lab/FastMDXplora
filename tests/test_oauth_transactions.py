import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from fastmdxplora.agent.oauth_transactions import AuthorizationAttempt, AuthorizationError

HOST = "urn:uuid:631a9487-aad4-4665-824c-d60510e717ac"
CALLBACK = "http://127.0.0.1:1455/auth/callback"


def attempt(**kwargs):
    return AuthorizationAttempt(CALLBACK, HOST, **kwargs)


def callback(pending, **updates):
    params = parse_qs(urlsplit(pending.authorization_url()).query)
    values = {"state": params["state"][0], "code": "fixture-code", "client_id": "oaiapp_fixture"}
    values.update(updates)
    return urlencode(values)


def test_pkce_is_bound_to_single_use_registration():
    pending = attempt()
    params = parse_qs(urlsplit(pending.authorization_url()).query)
    query = callback(pending)
    grant = pending.consume_callback(query)
    digest = base64.urlsafe_b64encode(hashlib.sha256(grant.verifier.encode()).digest()).decode().rstrip("=")
    assert digest == params["code_challenge"][0]
    assert grant.nonce == params["nonce"][0]
    assert params["agent_name_hint"] == ["FastMDXplora"]
    assert grant.token_form()["client_id"] == "oaiapp_fixture"
    assert grant.token_form()["resource"] == "https://api.openai.com/v1"
    assert "fixture-code" not in repr(grant)
    assert grant.verifier not in repr(grant)
    with pytest.raises(AuthorizationError, match="already completed"):
        pending.consume_callback(query)


def test_wrong_state_does_not_consume_legitimate_attempt():
    pending = attempt()
    with pytest.raises(AuthorizationError, match="does not match"):
        pending.consume_callback(callback(pending, state="unrelated"))
    assert pending.consume_callback(callback(pending)).client_id == "oaiapp_fixture"


@pytest.mark.parametrize("field", ["state", "code", "client_id", "error"])
def test_duplicate_callback_fields_refused(field):
    pending = attempt()
    with pytest.raises(AuthorizationError, match="ambiguous"):
        pending.consume_callback(callback(pending, **{field: "one"}) + "&" + field + "=two")


def test_denial_consumes_attempt_and_sanitizes_provider_error():
    pending = attempt()
    query = callback(pending, error="private-provider-error")
    with pytest.raises(AuthorizationError) as refusal:
        pending.consume_callback(query)
    assert "private-provider-error" not in str(refusal.value)
    with pytest.raises(AuthorizationError, match="already completed"):
        pending.consume_callback(query)


def test_returning_client_is_not_replaced():
    pending = attempt(client_id="oaiapp_existing")
    assert "agent_name_hint" not in parse_qs(urlsplit(pending.authorization_url()).query)
    with pytest.raises(AuthorizationError, match="different"):
        pending.consume_callback(callback(pending))
    query = parse_qs(callback(pending))
    del query["client_id"]
    assert pending.consume_callback(urlencode(query, doseq=True)).client_id == "oaiapp_existing"


def test_new_registration_requires_issued_client():
    pending = attempt()
    query = parse_qs(callback(pending))
    del query["client_id"]
    with pytest.raises(AuthorizationError, match="registration"):
        pending.consume_callback(urlencode(query, doseq=True))


@pytest.mark.parametrize("uri", ["https://example.org/auth/callback", "http://localhost:1455/auth/callback",
                                 "http://127.0.0.1:1455/other", CALLBACK + "?code=x",
                                 "http://user@127.0.0.1:1455/auth/callback"])
def test_callback_is_fixed_loopback(uri):
    with pytest.raises(AuthorizationError):
        AuthorizationAttempt(uri, HOST)


def test_cancel_and_expiry_clear_secrets():
    for expired in (False, True):
        pending = attempt()
        query = callback(pending)
        if expired:
            pending._created -= 601
        else:
            pending.cancel()
        with pytest.raises(AuthorizationError):
            pending.consume_callback(query)
        assert not pending._verifier and not pending._nonce and not pending._state
