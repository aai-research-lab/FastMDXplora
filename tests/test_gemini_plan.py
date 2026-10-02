import json
import subprocess
from pathlib import Path

import pytest

from fastmdxplora.agent.gemini_plan import GeminiPlan, executable
from fastmdxplora.agent.openai_plan import ConnectionError


def test_profile_does_not_inherit_other_accounts_or_billing(tmp_path, monkeypatch):
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
                "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_ACCESS_TOKEN", "NODE_OPTIONS"):
        monkeypatch.setenv(key, "foreign-fixture")
    env = GeminiPlan(tmp_path).environment()
    assert "foreign-fixture" not in json.dumps(env)
    assert env["GEMINI_FORCE_FILE_STORAGE"] == "true"
    assert env["GEMINI_FORCE_ENCRYPTED_FILE_STORAGE"] == "true"
    assert env["GEMINI_CLI_HOME"] == env["USERPROFILE"] == str(tmp_path)


def test_native_generator_has_no_tools_or_automatic_credit_overage(tmp_path):
    try:
        node, entry = executable()
    except ConnectionError:
        pytest.skip("Verified official Gemini client is not installed")
    adapter = GeminiPlan(tmp_path)
    probe = adapter.invoke("probe")
    assert Path(probe["profile"]).resolve().is_relative_to(tmp_path.resolve())
    bridge = Path(__import__("fastmdxplora.agent.gemini_plan", fromlist=["__file__"]).__file__).with_name("gemini_bridge.mjs")
    script = tmp_path / "fixture.mjs"
    script.write_text('''
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {nativeCore,requestFor,validateReply}=await import(pathToFileURL(process.argv[2]));
const core=await nativeCore(process.argv[3]);
const server=new core.CodeAssistServer({},'fixture-project',{},'fixture-session');
const requests=[];
server.requestPost=async (method,body)=>{
 requests.push({method,body});
 return {response:{candidates:[{finishReason:'STOP',content:{parts:[{text:'Fixture explanation.'}]}}]}};
};
const result=await server.generateContent(requestFor('gemini-fixture','$(untrusted) study text'),'fixture-question');
assert.equal(validateReply(result),'Fixture explanation.');
assert.equal(requests[0].method,'generateContent');
assert.deepEqual(requests[0].body.request.tools,[]);
assert.equal(requests[0].body.request.toolConfig.functionCallingConfig.mode,'NONE');
assert.equal(requests[0].body.enabled_credit_types,undefined);
assert.equal(requests[0].body.request.contents[0].parts[0].text,'$(untrusted) study text');
assert.throws(()=>validateReply({candidates:[{finishReason:'STOP',content:{parts:[{functionCall:{name:'simulate'}}]}}]}));
assert.throws(()=>validateReply({candidates:[{finishReason:'MAX_TOKENS',content:{parts:[{text:'partial'}]}}]}));
console.log('verified');
''', encoding="utf-8")
    result = subprocess.run([node, str(script), str(bridge), entry], env=adapter.environment(),
                            cwd=tmp_path / "work", capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "verified" in result.stdout


def test_reply_account_and_model_must_match(tmp_path, monkeypatch):
    adapter = GeminiPlan(tmp_path)
    monkeypatch.setattr(adapter, "invoke", lambda *a, **kw: {
        "identity": {"subject": "wrong"}, "model": "gemini-fixture", "text": "fixture"})
    with pytest.raises(ConnectionError, match="changed"):
        adapter.complete("question", "expected", "gemini-fixture")
