import io
import json
import time

import pytest

from fastmdxplora.agent import openai_plan as plan
from fastmdxplora.agent.oauth_transactions import AuthorizationGrant


@pytest.fixture
def signed_provider():
    jwt = pytest.importorskip("jwt")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid="fixture", use="sig", alg="RS256")
    claims = {"iss": plan.ISSUER, "aud": "oaiapp_fixture", "sub": "fixture-subject",
              "iat": int(time.time()), "exp": int(time.time()) + 3600, "nonce": "fixture-nonce"}

    def sign(**updates):
        return jwt.encode({**claims, **updates}, key, algorithm="RS256", headers={"kid": "fixture"})

    def fetch(url, **kwargs):
        if url == plan.DISCOVERY:
            return {"issuer": plan.ISSUER, "token_endpoint": plan.TOKEN_URL,
                    "jwks_uri": plan.ISSUER + "/jwks"}
        if url.endswith("/jwks"):
            return {"keys": [public]}
        if url == plan.TOKEN_URL:
            return {"access_token": "fixture-access", "refresh_token": "fixture-refresh",
                    "token_type": "Bearer", "scope": "openid chatgpt.tokens.use.direct",
                    "expires_in": 3600, "id_token": sign()}
        raise AssertionError("unexpected endpoint")
    return sign, fetch


def test_exchange_validates_signed_identity_before_returning_session(signed_provider):
    _, fetch = signed_provider
    grant = AuthorizationGrant("oaiapp_fixture", "fixture-code", "fixture-verifier", "fixture-nonce", "http://127.0.0.1:1455/auth/callback")
    record = plan.exchange(grant, fetch=fetch)
    assert record["subject"] == "fixture-subject"
    assert record["client_id"] == "oaiapp_fixture"
    assert record["expires_at"] > time.time()


@pytest.mark.parametrize("bad", [{"iss": "https://other.example"}, {"aud": "oaiapp_other"},
                                 {"nonce": "other"}, {"exp": 1}, {"sub": ""}])
def test_identity_mismatch_or_expiry_is_refused(signed_provider, bad):
    sign, fetch = signed_provider
    with pytest.raises(plan.ConnectionError, match="verified"):
        plan.verify_identity(sign(**bad), "oaiapp_fixture", nonce="fixture-nonce", fetch=fetch)


def test_untrusted_jwks_location_is_refused(signed_provider):
    sign, _ = signed_provider
    def fetch(url, **kwargs):
        return {"issuer": plan.ISSUER, "token_endpoint": plan.TOKEN_URL,
                "jwks_uri": "https://other.example/jwks"}
    with pytest.raises(plan.ConnectionError, match="metadata"):
        plan.verify_identity(sign(), "oaiapp_fixture", fetch=fetch)


def test_refresh_cannot_replace_account_identity(signed_provider):
    sign, fetch = signed_provider
    account = {"client_id": "oaiapp_fixture", "refresh_token": "old-refresh",
               "id_token": sign(), "subject": "different-subject"}
    with pytest.raises(plan.ConnectionError, match="identity changed"):
        plan.refresh(account, fetch=fetch)


def test_plan_scope_required_even_with_valid_id_token(signed_provider):
    _, fetch = signed_provider
    def no_scope(url, **kwargs):
        answer = fetch(url, **kwargs)
        if url == plan.TOKEN_URL:
            answer["scope"] = "openid profile"
        return answer
    grant = AuthorizationGrant("oaiapp_fixture", "code", "verifier", "fixture-nonce", "http://127.0.0.1:1455/auth/callback")
    with pytest.raises(plan.ConnectionError, match="not granted"):
        plan.exchange(grant, fetch=no_scope)


def stream_opener(events, requests):
    def opener(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events))
    return opener


def test_completion_has_no_tools_and_waits_for_terminal_event():
    requests, emitted = [], []
    events = [{"type": "response.output_text.delta", "delta": "Explanation"},
              {"type": "response.completed", "response": {"status": "completed", "output": [{"type": "message"}]}}]
    result = plan.complete("fixture-token", "fixture-model", "question", emitted.append,
                           opener=stream_opener(events, requests))
    body = json.loads(requests[0].data)
    assert body["tools"] == [] and body["tool_choice"] == "none"
    assert body["store"] is False and body["stream"] is True
    assert requests[0].full_url == "https://api.openai.com/v1/responses"
    assert result == "Explanation" and emitted == ["Explanation"]


@pytest.mark.parametrize("terminal", [None, {"type": "response.failed"}, {"type": "response.incomplete"},
                                      {"type": "response.completed", "response": {"status": "completed", "output": [{"type": "function_call"}]}}])
def test_partial_text_is_not_success(terminal):
    events = [{"type": "response.output_text.delta", "delta": "partial"}]
    if terminal:
        events.append(terminal)
    with pytest.raises(plan.ConnectionError):
        plan.complete("fixture-token", "fixture-model", "question", opener=stream_opener(events, []))


def test_models_only_offer_visible_valid_catalog_entries():
    def fetch(url, **kwargs):
        return {"models": [{"slug": "model-1", "display_name": "Model one", "visibility": "list"},
                           {"slug": "hidden", "visibility": "hidden"}, {"slug": "bad\nvalue", "visibility": "list"}]}
    assert plan.models("fixture-token", fetch=fetch) == [{"id": "model-1", "label": "Model one"}]


def test_redirects_cannot_forward_credentials():
    with pytest.raises(plan.ConnectionError, match="redirect"):
        plan._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.example")
