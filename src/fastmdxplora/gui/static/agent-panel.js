/* The Agent panel.
 *
 * The subject of this page is the FastMDXplora Agent. Which language model
 * it drafts with is an engine setting -- chosen once, then out of the way.
 * An earlier version led with "Asking anthropic, claude-sonnet-4-6", which
 * put another company's product in the primary status line of this one.
 *
 * Help is progressive. The mode note says one short thing about the mode
 * actually selected rather than explaining all three at once, and the key
 * field says where to get a key for the provider actually chosen. A
 * paragraph covering every case is a paragraph nobody reads.
 *
 * The refusals are shown rather than summarised: they are the only visible
 * sign that anything checked the config, and somebody watching the Agent
 * correct itself learns the config language while they wait.
 */
(function () {
  "use strict";

  var providers = [];

  /* One line per mode, said where the mode is chosen: what stands between
   * it and a bad study. That is the only thing a reader needs at the
   * moment of choosing. */
  var MODE_NOTES = {
    assisted: "Drafted and shown to you. Nothing runs until you say so.",
    autonomous: "Runs without being shown to you first, so a ceiling is " +
                "required \u2014 it is the only thing left that can stop it.",
    unvalidated: "Works outside the schema, so nothing checks the method. " +
                 "Every figure it produces is stamped."
  };

  /* Where a key comes from, for the provider actually chosen. Said here
   * because this is where somebody needs it; a GUI that tells you to run
   * a command is a GUI that has given up. */
  var KEY_HELP = {
    anthropic: "Get one at platform.claude.com, under Settings \u2192 API " +
               "keys. A developer account \u2014 a Claude subscription is " +
               "not the same thing.",
    openai: "Get one at platform.openai.com, under API keys.",
    compatible: "A local server usually needs none \u2014 leave this blank."
  };

  /* Refusals written for the command line, said the way this surface can
   * act on. `environment.model.unset` tells a terminal user to run
   * `fastmdx agent set`; there is a Settings button here instead. */
  var IN_THE_GUI = {
    "environment.model.unset":
      "No model set yet. Open Settings and choose one.",
    "environment.credentials.absent":
      "API key required. Open Settings and paste one."
  };

  function el(id) { return document.getElementById(id); }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(body || {})
    }).then(function (response) { return response.json(); });
  }

  function note(box, text, ok) {
    var line = document.createElement("div");
    line.className = "agent-attempt" + (ok ? " ok" : "");
    line.textContent = text;
    box.appendChild(line);
    return line;
  }

  /* Ready or not, and nothing else. The model is recorded on the study --
   * `agent_model` in the config and the manifest -- which is where a
   * reader needs it. It does not belong in the chrome. */
  function describeEngine(current) {
    /* Inside the settings dialog, where somebody is choosing. It was on
     * the page itself, as "Ready. Drafting with <model>." -- a status line
     * about an engine, on a page about a study. */
    return current ? current.model : "Nothing set yet.";
  }

  var OTHER = "__other__";

  /* The models a provider is known to have, plus a way to name one that is
   * not on the list. A closed list would lock out a model released next
   * month; free text alone made somebody type a string exactly right with
   * nothing to check it against, and left the previous provider's model
   * sitting there when they switched. */
  function fillModels(spec, chosen) {
    var select = el("agent-model");
    select.innerHTML = "";
    (spec.models || []).forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
    var other = document.createElement("option");
    other.value = OTHER;
    /* Said outright rather than as "Other…". Somebody looking for a model
     * the list does not have needs to see that typing one is possible;
     * reported as "I still can't choose any model, only prelisted ones"
     * while this option was sitting in the dropdown saying "Other…". */
    other.textContent = "Type a model name\u2026";
    select.appendChild(other);

    var known = (spec.models || []).indexOf(chosen) !== -1;
    select.value = known ? chosen : OTHER;
    if (!known && chosen) el("agent-model-other").value = chosen;
    modelChanged();
  }

  function modelChanged() {
    var typing = el("agent-model").value === OTHER;
    el("agent-model-other-field").hidden = !typing;
    if (typing) el("agent-model-other").focus();
  }

  function chosenModel() {
    return el("agent-model").value === OTHER
      ? el("agent-model-other").value.trim()
      : el("agent-model").value;
  }

  function showProvider(id, chosen) {
    var spec = providers.filter(function (p) { return p.id === id; })[0];
    if (!spec) return;
    el("agent-url-field").hidden = !spec.needs_url;
    /* The model follows the provider. It used to be filled only when the
     * field was empty, so switching provider left the previous one's model
     * in place -- OpenAI selected and claude-sonnet-4-6 still showing. */
    fillModels(spec, chosen || spec.default_model || "");
    el("agent-key-help").textContent =
      (KEY_HELP[id] || "") + " Stored in a file of its own, readable only " +
      "by you, and never written into a config, a manifest or a log.";
    el("agent-url-examples").textContent =
      (spec.examples || []).map(function (e) {
        return e.label + " \u2014 " + e.url;
      }).join("  \u00b7  ");
  }

  function engineIsSet(current) {
    el("agent-model-current").textContent = describeEngine(current);
  }

  function openSettings() { el("agent-settings").hidden = false; }
  function closeSettings() { el("agent-settings").hidden = true; }

  function loadEngine() {
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
      if (data.current) {
        select.value = data.current.provider;
        el("agent-base-url").value = data.current.base_url || "";
      }
      showProvider(select.value, data.current && data.current.model);
      engineIsSet(data.current);
    });
  }

  function saveEngine() {
    var button = el("agent-save-model");
    button.disabled = true;
    post("/api/agent/model", {
      provider: el("agent-provider").value,
      model: chosenModel(),
      base_url: el("agent-base-url").value,
      api_key: el("agent-key").value
    }).then(function (data) {
      button.disabled = false;
      if (!data.ok) {
        el("agent-model-current").textContent = data.error;
        return;
      }
      /* Cleared rather than left in the field: a key sitting in an input
       * survives a screenshot and a shoulder. */
      el("agent-key").value = "";
      if (data.models && data.models.length) {
        /* What the provider says it has, which is only askable once there
         * is a key. Until then the dropdown shows the written fallback. */
        var spec = providers.filter(function (p) {
          return p.id === el("agent-provider").value;
        })[0];
        if (spec) spec.models = data.models;
      }
      engineIsSet(data.current);
      closeSettings();
    });
  }

  function draft() {
    var button = el("agent-propose");
    var box = el("agent-attempts");
    button.disabled = true;
    el("agent-result").hidden = true;
    el("agent-actions").hidden = true;
    box.innerHTML = "";
    note(box, "Writing\u2026");

    post("/api/agent/propose", {
      request: el("agent-request").value,
      agent: el("agent-mode").value
    }).then(function (data) {
      button.disabled = false;
      box.innerHTML = "";
      (data.attempts || []).forEach(function (attempt) {
        if (attempt.refusal) note(box, "Refused: " + attempt.refusal.message);
      });
      if (data.question) {
        /* Not a refusal and not a failure: the Agent needs something only
         * you can give it. Put the question where the answer goes. */
        note(box, data.question);
        el("agent-request").focus();
        return;
      }
      if (!data.ok) {
        note(box, IN_THE_GUI[data.code] || data.error);
        if (IN_THE_GUI[data.code]) openSettings();
        return;
      }
      note(box, data.cycles === 1
        ? "Accepted first time."
        : "Accepted after " + data.cycles + " attempts.", true);
      el("agent-download").onclick = function () { download(data.yaml); };
      el("agent-result").textContent = data.yaml;
      el("agent-result").hidden = false;
      el("agent-actions").hidden = false;
      el("agent-run").hidden = (el("agent-mode").value === "assisted");
      el("agent-load").onclick = function () {
        /* An earlier version reached for a `FastMDX` global and a
         * `loadConfigObject` on it, and neither has ever existed -- the
         * truthiness guard turned that into a silent no-op, which is why
         * the button looked like it worked. The mapping from a config to
         * form state lives on the server, so the config goes there and
         * comes back as state. */
        post("/api/load-config", { config: data.config }).then(function (m) {
          if (!m || !m.ok) {
            note(box, (m && m.error) || "Could not open it in the builder.");
            return;
          }
          var run = window.FastMDXRun;
          if (!run || !run.applyLoadedState) return;
          run.applyLoadedState(m.state, {
            note: "Opened the Agent's config. Nothing is written until you run it."
          });
          window.location.hash = "#run";
        });
      };
      el("agent-run").onclick = function () { start(data.config, box); };
    });
  }

  /* The config, as a file, without a round trip. It is already here. */
  function download(yaml) {
    var blob = new Blob([yaml], {type: "text/yaml"});
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "study.yml";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
  }

  function start(config, box) {
    var button = el("agent-run");
    button.disabled = true;
    post("/api/agent/run", {
      config: config,
      budget_hours: el("agent-budget").value
    }).then(function (started) {
      button.disabled = false;
      note(box, started.ok
        ? "Started. Watch it on the Exploration page."
        : started.error, started.ok);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("agent-provider")) return;
    loadEngine();

    el("agent-provider").addEventListener("change", function () {
      showProvider(this.value);
    });
    el("agent-model").addEventListener("change", modelChanged);
    el("agent-save-model").addEventListener("click", saveEngine);
    el("agent-settings-open").addEventListener("click", openSettings);
    el("agent-settings-close").addEventListener("click", closeSettings);
    el("agent-propose").addEventListener("click", draft);

    function modeChanged() {
      var mode = el("agent-mode").value;
      el("agent-mode-note").textContent = MODE_NOTES[mode] || "";
      el("agent-budget-note").textContent = mode === "autonomous"
        ? "Required in this mode. Checked after setup, where the cost is "
          + "first known."
        : "Optional. Stops a study that would cost more than you meant.";
    }
    el("agent-mode").addEventListener("change", modeChanged);
    modeChanged();
  });
})();
