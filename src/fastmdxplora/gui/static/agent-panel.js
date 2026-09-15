/* The agent panel.
 *
 * Two calls: one to choose a model, one to turn a sentence into a config.
 * Both go to endpoints that hand the work to the same `propose_config` the
 * CLI uses, so nothing here decides whether a config is acceptable.
 *
 * The attempts are shown rather than summarised. They are the only visible
 * sign that anything checked the config, and somebody watching a model
 * correct itself learns the config language while they wait -- which is
 * the path off this panel and onto the form, and the right direction for
 * a tool people should outgrow.
 *
 * The key is typed here and sent once. It is never read back: the endpoint
 * reports which provider and model are set and never the secret, so a page
 * that never receives one cannot leak one.
 */
(function () {
  "use strict";

  var providers = [];

  function el(id) { return document.getElementById(id); }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(body || {})
    }).then(function (response) { return response.json(); });
  }

  function describeCurrent(current) {
    if (!current) {
      return "No model chosen yet. Pick one below; nothing else in " +
             "FastMDXplora needs one.";
    }
    var where = current.base_url ? " at " + current.base_url : "";
    return "Asking " + current.provider + ", " + current.model + where + ".";
  }

  function showProvider(id) {
    var spec = providers.filter(function (p) { return p.id === id; })[0];
    if (!spec) return;
    el("agent-url-field").hidden = !spec.needs_url;
    el("agent-model").value = spec.default_model || "";
    el("agent-model").placeholder = spec.default_model || "model name";
    el("agent-key").placeholder =
      "leave blank to read " + spec.environment_variable +
      " from the environment";
    // Somebody choosing the compatible option wants DeepSeek or a local
    // model and does not know the URL. Show the servers it is for rather
    // than a list of vendors, which goes stale.
    var help = (spec.examples || []).map(function (e) {
      return e.label + ": " + e.url + " (" + e.model + ")";
    }).join(" · ");
    el("agent-url-examples").textContent = help;
  }

  function loadModel() {
    return post("/api/agent/model", {}).then(function (data) {
      if (!data.ok) return;
      providers = data.providers || [];
      var select = el("agent-provider");
      select.innerHTML = "";
      providers.forEach(function (spec) {
        var option = document.createElement("option");
        option.value = spec.id;
        option.textContent = spec.label;
        select.appendChild(option);
      });
      if (data.current) select.value = data.current.provider;
      el("agent-model-current").textContent = describeCurrent(data.current);
      showProvider(select.value);
      if (data.current && data.current.model) {
        el("agent-model").value = data.current.model;
      }
      if (data.current && data.current.base_url) {
        el("agent-base-url").value = data.current.base_url;
      }
    });
  }

  function saveModel() {
    var button = el("agent-save-model");
    button.disabled = true;
    post("/api/agent/model", {
      provider: el("agent-provider").value,
      model: el("agent-model").value,
      base_url: el("agent-base-url").value,
      api_key: el("agent-key").value
    }).then(function (data) {
      button.disabled = false;
      if (!data.ok) {
        el("agent-model-current").textContent = data.error;
        return;
      }
      // Cleared rather than left in the field. A key sitting in an input
      // survives a screenshot and a shoulder.
      el("agent-key").value = "";
      el("agent-model-current").textContent = describeCurrent(data.current);
    });
  }

  function renderAttempts(attempts) {
    var box = el("agent-attempts");
    box.innerHTML = "";
    (attempts || []).forEach(function (attempt) {
      if (!attempt.refusal) return;
      var line = document.createElement("div");
      line.className = "agent-attempt";
      line.textContent = "✗ " + attempt.refusal.message;
      box.appendChild(line);
    });
  }

  function propose() {
    var button = el("agent-propose");
    var result = el("agent-result");
    var load = el("agent-load");
    button.disabled = true;
    result.hidden = true;
    load.hidden = true;
    el("agent-attempts").textContent = "Writing a config…";

    post("/api/agent/propose", {
      request: el("agent-request").value,
      agent: el("agent-mode").value
    }).then(function (data) {
      button.disabled = false;
      renderAttempts(data.attempts);
      if (!data.ok) {
        var problem = document.createElement("div");
        problem.className = "agent-attempt";
        problem.textContent = data.error;
        el("agent-attempts").appendChild(problem);
        return;
      }
      var note = document.createElement("div");
      note.className = "agent-attempt ok";
      note.textContent = "✓ Accepted after " + data.cycles + " attempt(s)";
      el("agent-attempts").appendChild(note);
      result.textContent = data.yaml;
      result.hidden = false;
      load.hidden = false;
      load.onclick = function () {
        // Into the form the GUI already has, which is the only thing it
        // does with a config. The agent is one more way to fill it in, not
        // a second path through the software.
        //
        // This reached for a `FastMDX` global and a `loadConfigObject`
        // on it, and neither has ever existed -- the truthiness guard
        // turned that into a silent no-op, which is why the button looked
        // like it worked. The mapping from a config to form state lives on
        // the server, so the config goes there and comes back as state.
        post("/api/load-config", { config: data.config }).then(function (m) {
          if (!m || !m.ok) {
            var failed = document.createElement("div");
            failed.className = "agent-attempt";
            failed.textContent = (m && m.error) || "Could not load it.";
            el("agent-attempts").appendChild(failed);
            return;
          }
          var run = window.FastMDXRun;
          if (!run || !run.applyLoadedState) return;
          run.applyLoadedState(m.state, {
            note: "Loaded the agent's config. Nothing is written until you run it.",
          });
          window.location.hash = "#run";
        });
      };
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("agent-provider")) return;
    loadModel();
    el("agent-provider").addEventListener("change", function () {
      showProvider(this.value);
    });
    el("agent-save-model").addEventListener("click", saveModel);
    el("agent-propose").addEventListener("click", propose);
  });
})();
