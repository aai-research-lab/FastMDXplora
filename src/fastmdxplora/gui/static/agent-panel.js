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

  /* ---- The conversation ---------------------------------------------- */

  /* When the Agent asked a question, the next message answers it. The
   * proposing loop is stateless, so the answer goes back with the request
   * it answers, joined -- "simulate chignolin for 2 ns" plus "1UAO" is a
   * request the loop can write a study from. */
  var pending = null;

  function say(text) {
    var msg = document.createElement("div");
    msg.className = "agent-msg agent-msg-user";
    msg.textContent = text;
    el("agent-thread").appendChild(msg);
    return msg;
  }

  function reply() {
    var tpl = el("agent-reply-template");
    var node = tpl.content.firstElementChild.cloneNode(true);
    /* The template's ids belong to the template. Each clone is addressed
     * by data-role so that two replies on the page do not share an id. */
    Array.prototype.forEach.call(node.querySelectorAll("[id]"), function (n) {
      n.removeAttribute("id");
    });
    el("agent-thread").appendChild(node);
    var part = function (role) { return node.querySelector('[data-role="' + role + '"]'); };
    return { node: node, part: part };
  }

  function scrollToEnd() {
    var thread = el("agent-thread");
    thread.scrollTop = thread.scrollHeight;
  }

  function autosize(area) {
    area.style.height = "auto";
    area.style.height = Math.min(area.scrollHeight, window.innerHeight * 0.4) + "px";
  }

  function draft() {
    var area = el("agent-request");
    var typed = area.value.trim();
    if (!typed) return;
    var request = pending ? pending + "\n" + typed : typed;
    pending = null;

    say(typed);
    area.value = "";
    autosize(area);
    var r = reply();
    var box = r.part("attempts");
    note(box, "Writing\u2026");
    scrollToEnd();
    el("agent-propose").disabled = true;

    post("/api/agent/propose", {
      request: request,
      agent: el("agent-mode").value
    }).then(function (data) {
      el("agent-propose").disabled = false;
      box.innerHTML = "";
      (data.attempts || []).forEach(function (attempt) {
        if (attempt.refusal) note(box, "Refused: " + attempt.refusal.message);
      });
      if (data.question) {
        note(box, data.question);
        pending = request;
        area.focus();
        scrollToEnd();
        return;
      }
      if (!data.ok) {
        note(box, IN_THE_GUI[data.code] || data.error);
        if (IN_THE_GUI[data.code]) openSettings();
        scrollToEnd();
        return;
      }
      note(box, data.cycles === 1
        ? "Accepted first time."
        : "Accepted after " + data.cycles + " attempts.", true);
      wireActions(r, data, box);
      scrollToEnd();
    }).catch(function () {
      el("agent-propose").disabled = false;
      box.innerHTML = "";
      note(box, "The Agent did not answer. Check Settings, then try again.");
    });
  }

  function wireActions(r, data, box) {
    var result = r.part("result");
    var actions = r.part("actions");
    var showBtn = r.part("show");
    var fullBox = r.part("full");
    var noteEl = r.part("note");
    var runBtn = r.part("run");

    result.textContent = data.yaml;
    result.hidden = true;
    actions.hidden = false;

    /* Load the config into the builder's state without going there. The
     * builder's actions read that state, so the file, the command and
     * the script are exactly what the builder would produce. One
     * derivation, two doors. */
    var loaded = post("/api/load-config", { config: data.config }).then(function (m) {
      if (!m || !m.ok) {
        noteEl.textContent = (m && m.error) || "Could not prepare the config for the builder.";
        return false;
      }
      var run = window.FastMDXRun;
      if (!run || !run.applyLoadedState) return false;
      run.applyLoadedState(m.state, {});
      return true;
    });

    function viaBuilder(action) {
      return function () {
        loaded.then(function (ok) {
          if (!ok) return;
          var full = el("run-full-config");
          if (full) full.checked = fullBox.checked;
          Promise.resolve(window.FastMDXRun[action]()).then(function () {
            var said = document.getElementById("run-note");
            noteEl.textContent = said ? said.textContent : "";
          });
        });
      };
    }

    showBtn.onclick = function () {
      if (!result.hidden) {
        result.hidden = true;
        showBtn.textContent = "Show the config";
        return;
      }
      if (!fullBox.checked) {
        result.textContent = data.yaml;
        result.hidden = false;
        showBtn.textContent = "Hide the config";
        scrollToEnd();
        return;
      }
      loaded.then(function (ok) {
        if (!ok) return;
        var full = el("run-full-config");
        if (full) full.checked = true;
        window.FastMDXRun.fetchConfig().then(function (built) {
          if (built && built.ok && built.yaml) {
            result.textContent = built.yaml;
            result.hidden = false;
            showBtn.textContent = "Hide the config";
            scrollToEnd();
          } else {
            noteEl.textContent = (built && built.error) || "Could not render the full config.";
          }
        });
      });
    };
    fullBox.onchange = function () {
      if (!result.hidden) { result.hidden = true; showBtn.onclick(); }
    };
    r.part("download").onclick = viaBuilder("download");
    r.part("copy").onclick = viaBuilder("copyCommand");
    r.part("script").onclick = viaBuilder("downloadScript");
    r.part("load").onclick = function (e) {
      e.preventDefault();
      loaded.then(function (ok) { if (ok) window.location.hash = "#run"; });
    };
    runBtn.onclick = function () {
      runBtn.disabled = true;
      post("/api/agent/run", {
        config: data.config,
        budget_hours: el("agent-budget").value
      }).then(function (started) {
        if (started.ok) {
          /* Started once. A second press started it again into the same
           * folder and was refused for the folder being occupied. The
           * button says what happened and stays put. */
          runBtn.textContent = "Running";
          note(box, "Started. Watch it in the sidebar and the Overview.", true);
        } else {
          runBtn.disabled = false;
          noteEl.textContent = started.error;
        }
      });
    };
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
    var area = el("agent-request");
    area.addEventListener("input", function () { autosize(area); });
    area.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        draft();
      }
    });
    autosize(area);

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

  /* For the settings popup: "Agent settings…" should open this dialog,
   * not merely land on the Agent page. */
  window.FastMDXAgent = { openSettings: openSettings, closeSettings: closeSettings };
})();
