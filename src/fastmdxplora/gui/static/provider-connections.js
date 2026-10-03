(function () {
  "use strict";
  var timer = null, generation = 0, busy = false, pendingLogin = false, accounts = [], loginPopup = null, openedAuthorization = "";
  function providerName(provider) { return provider === "claude" ? "Claude" : provider === "kimi" ? "Kimi" : provider === "gemini" ? "Gemini" : "ChatGPT"; }
  function showKimiAuthorization(value) {
    var url = new URL(value);
    if (url.protocol !== "https:" || ["www.kimi.com", "www.kimi.ai", "auth.kimi.com", "auth.kimi.ai", "kimi.com", "kimi.ai"].indexOf(url.hostname) < 0 || (url.port && url.port !== "443") || url.username || url.password || url.hash) throw new Error("The Kimi sign-in address is invalid.");
    if (openedAuthorization !== url.href && loginPopup && !loginPopup.closed) loginPopup.location.replace(url.href);
    openedAuthorization = url.href;
    el("agent-connect-browser").href=url.href; el("agent-connect-browser").hidden=false;
  }
  function el(id) { return document.getElementById(id); }
  function status(text) { el("agent-connection-status").textContent = text || ""; }
  function updateConnect() {
    var provider=el("agent-subscription-provider").value;
    el("agent-subscription-provider").disabled=busy || pendingLogin;
    el("agent-subscription-connect").disabled=busy || pendingLogin || !provider;
    el("agent-subscription-connect").textContent=provider ? "Connect with " + providerName(provider) : "Connect provider";
    var help={
      "openai-chatgpt":"Sign in with ChatGPT to use eligible account models. Tokens use this computer's OS-protected storage.",
      claude:"Requires the official Claude Code client and a personal Pro or Max plan. Its separate profile stores the login; managed device policies are not supported.",
      kimi:"Requires the official Kimi Code client. A separate profile owns device sign-in and renewal; all coding tools are disabled.",
      gemini:"Requires official Gemini CLI 0.62.0 and existing Code Assist enrollment. A separate profile uses the client's encrypted file storage. Automatic credit overages are disabled."
    };
    el("agent-subscription-help").textContent=help[provider] || "Your existing app logins are not copied. Subscription failures do not switch to API billing.";
  }
  function post(payload) {
    return fetch("/api/agent/connections", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)})
      .then(function(r) { return r.json(); }).then(function(data) { if (!data.ok) throw new Error(data.error || "Connection unavailable."); return data; });
  }
  function loadModels() {
    var selected = el("agent-connected-account").value, current = ++generation;
    el("agent-connected-model").replaceChildren(); el("agent-connected-use").disabled = true;
    el("agent-connected-model-help").textContent = "";
    el("agent-connected-refresh").disabled = !selected;
    el("agent-connected-check").disabled = !selected;
    el("agent-connected-disconnect").disabled = !selected;
    el("agent-connected-reconnect").disabled = !selected || pendingLogin;
    if (!selected) return Promise.resolve();
    return post({action:"models", account:selected}).then(function(data) {
      if (generation !== current || el("agent-connected-account").value !== selected) return;
      (data.models || []).forEach(function(row) { var option=document.createElement("option"); option.value=row.id; option.textContent=row.label; el("agent-connected-model").appendChild(option); });
      var saved=accounts.filter(function(row){return row.id === selected;})[0];
      el("agent-connected-check").hidden = !saved || saved.provider !== "openai-chatgpt";
      if (saved && saved.provider === "openai-chatgpt") {
        var missing = [];
        [{id:"gpt-6.1-sol", label:"GPT-6.1 Sol"}, {id:"gpt-6-sol", label:"GPT-6 Sol"}, {id:"gpt-6-luna", label:"GPT-6 Luna"}].forEach(function(row) {
          if ((data.models || []).some(function(available){return available.id === row.id;})) return;
          var option = document.createElement("option"); option.value = row.id;
          option.textContent = row.label + " — unavailable in this account's catalog"; option.disabled = true;
          el("agent-connected-model").appendChild(option); missing.push(row.label);
        });
        el("agent-connected-model-help").textContent = missing.length
          ? "Not listed: " + missing.join(", ") + ". Check model access sends a short test to each model through your subscription."
          : "Models available to your connected ChatGPT account.";
      }
      if(saved && Array.from(el("agent-connected-model").options).some(function(option){return !option.disabled && option.value === saved.model;})) el("agent-connected-model").value=saved.model;
      el("agent-connected-use").disabled = !el("agent-connected-model").value || el("agent-connected-model").selectedOptions[0].disabled;
    }).catch(function(error) { if (generation === current) status(error.message); });
  }
  function refresh() {
    return post({action:"status"}).then(function(data) {
      var select = el("agent-connected-account"), previous = select.value;
      var signature = JSON.stringify(data.accounts || []);
      accounts=data.accounts || [];
      status(data.message);
      if (select.dataset.signature !== signature) {
        select.dataset.signature=signature; select.replaceChildren();
        var blank=document.createElement("option"); blank.value=""; blank.textContent="Choose a connected account"; select.appendChild(blank);
        (data.accounts || []).forEach(function(row) { var option=document.createElement("option"); option.value=row.id; option.textContent=providerName(row.provider) + " · " + row.label + (row.connected ? "" : " — reconnect required"); select.appendChild(option); });
        select.value=previous || data.active || "";
        if (select.selectedIndex < 0) select.value="";
        loadModels();
      }
      var pending=data.status === "pending" || data.status === "verifying";
      pendingLogin=pending;
      if (pending && data.authorization_url) showKimiAuthorization(data.authorization_url);
      el("agent-connect-cancel").hidden=!pending;
      updateConnect();
      el("agent-connected-reconnect").disabled=pending || busy || !select.value;
      if (!pending) {
        el("agent-connect-browser").hidden=true;
        if (loginPopup && !openedAuthorization && !loginPopup.closed) loginPopup.close();
        loginPopup=null; openedAuthorization="";
      }
      if (data.selection === "subscription") {
        var active=(data.accounts || []).filter(function(row) {return row.id === data.active;})[0];
        window.dispatchEvent(new CustomEvent("agent:connection-changed", {detail:{current:active && active.connected ? {model:active.model} : null}}));
        el("agent-model-current").textContent=active ? providerName(active.provider) + " subscription · " + active.label + " · " + active.model : "Select a connected subscription account.";
      }
      clearTimeout(timer); timer=pending ? setTimeout(refresh, 2000) : null;
    }).catch(function(error) { status(error.message); clearTimeout(timer); timer=null; });
  }
  function connect(provider, account) {
    if (busy || pendingLogin) return;
    busy=true;
    updateConnect();
    var popup=provider === "kimi" || provider === "openai-chatgpt" ? window.open("about:blank", "_blank") : null;
    if (popup) popup.opener=null;
    if (provider === "kimi") { loginPopup=popup; openedAuthorization=""; }
    var payload={action:"connect", provider:provider};
    if(account) payload.account=account;
    post(payload).then(function(data) {
      if (!data.browser_managed) {
        var url=new URL(data.url);
        if (url.origin !== "https://auth.openai.com" || url.pathname !== "/api/accounts/authorize") throw new Error("The provider sign-in address is invalid.");
        if (popup) popup.location.replace(url.href);
        el("agent-connect-browser").href=url.href; el("agent-connect-browser").hidden=false;
      }
      return refresh();
    }).catch(function(error) { if(popup) popup.close(); status(error.message); }).finally(function() {
      busy=false; updateConnect();
      el("agent-connected-reconnect").disabled=pendingLogin || !el("agent-connected-account").value;
    });
  }
  document.addEventListener("DOMContentLoaded", function() {
    el("agent-subscription-provider").addEventListener("change", updateConnect);
    el("agent-subscription-connect").addEventListener("click", function() {
      var provider=el("agent-subscription-provider").value;
      if (["openai-chatgpt", "claude", "kimi", "gemini"].indexOf(provider)>=0) connect(provider);
    });
    updateConnect();
    el("agent-connected-reconnect").addEventListener("click", function() {
      var selected=accounts.filter(function(row){return row.id === el("agent-connected-account").value;})[0];
      if(selected) connect(selected.provider, selected.id);
    });
    el("agent-connect-cancel").addEventListener("click", function() { post({action:"cancel"}).then(refresh).catch(function(error){status(error.message);}); });
    el("agent-connected-account").addEventListener("change", loadModels);
    el("agent-connected-use").addEventListener("click", function() { post({action:"select", account:el("agent-connected-account").value, model:el("agent-connected-model").value}).then(refresh).catch(function(error){status(error.message);}); });
    el("agent-connected-refresh").addEventListener("click", loadModels);
    el("agent-connected-check").addEventListener("click", function() {
      el("agent-connected-check").disabled = true; status("Checking the three GPT-6 models through your subscription…");
      post({action:"check-model-access", account:el("agent-connected-account").value}).then(function(data) {
        status("Verified: " + (data.verified.join(", ") || "none") + (data.unavailable.length ? ". Not verified: " + data.unavailable.join(", ") : ""));
        return loadModels();
      }).catch(function(error){status(error.message);}).finally(function(){el("agent-connected-check").disabled=false;});
    });
    el("agent-connected-disconnect").addEventListener("click", function() { post({action:"disconnect", account:el("agent-connected-account").value}).then(refresh).catch(function(error){status(error.message);}); });
    window.addEventListener("agent:settings-open", refresh);
    window.addEventListener("agent:engine-loaded", refresh);
    el("agent-settings-close").addEventListener("click", function(){clearTimeout(timer); timer=null;});
    refresh();
  });
})();
