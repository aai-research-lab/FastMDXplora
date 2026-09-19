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

  /* The little markdown a reply may carry -- bold, italic, code, a link --
   * and nothing else. Escaped first, so the model cannot put markup in
   * the page; then the four patterns, in an order that keeps code spans
   * from being reinterpreted. Paragraphs are blank-line separated. */
  function prose(text) {
    var s = String(text || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    s = s.replace(/`([^`\n]+)`/g, function (_, c) { return "<code>" + c + "</code>"; });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    /* Italic needs a non-space just inside each star, so "a * b * c" --
     * an arithmetic asterisk with spaces -- stays as typed. */
    s = s.replace(/(^|[^*])\*(\S(?:[^*\n]*\S)?)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/\bhttps?:\/\/[^\s<)]+/g, function (u) {
      return '<a href="' + u + '" target="_blank" rel="noopener">' + u + "</a>";
    });
    return s;
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
  /* The conversation, as the Agent sees it: what was said each way. And
   * the last config it wrote, so "make it 5 ns" is a change to it rather
   * than a study from nothing. */
  var history = [];
  var currentConfig = null;
  /* The transcript, as it can be replayed: every entry carries enough to
   * draw it again without asking the model. Saved to the workspace after
   * each exchange, restored when the page opens. The thread used to live
   * only in the browser's memory, and a refresh emptied it. */
  var transcript = [];

  function persist() {
    post("/api/agent/conversation", { entries: transcript }).catch(function () {});
  }

  function tools(msg, items) {
    var bar = document.createElement("div");
    bar.className = "agent-msg-tools";
    items.forEach(function (it) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = it.label;
      b.title = it.title || it.label;
      b.addEventListener("click", it.run);
      bar.appendChild(b);
    });
    msg.appendChild(bar);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text);
    }
  }

  /* Remove this message and everything after it from the thread and the
   * history, so an edit or a retry starts from here. */
  function cutFrom(msg) {
    var thread = el("agent-thread");
    var nodes = Array.prototype.slice.call(thread.children);
    var at = nodes.indexOf(msg);
    if (at === -1) return;
    var userIndex = nodes.slice(0, at + 1).filter(function (n) {
      return n.classList.contains("agent-msg-user");
    }).length - 1;
    nodes.slice(at).forEach(function (n) { thread.removeChild(n); });
    var seen = -1;
    for (var i = 0; i < history.length; i++) {
      if (history[i].role === "user") seen += 1;
      if (seen === userIndex) { history.length = i; break; }
    }
    var seenT = -1;
    for (var k = 0; k < transcript.length; k++) {
      if (transcript[k].role === "user") seenT += 1;
      if (seenT === userIndex) { transcript.length = k; break; }
    }
    persist();
    pending = null;
    stopPending = null;
    lastReply = null;
    currentConfig = null;
    for (var j = history.length - 1; j >= 0; j--) {
      var text = history[j].text || "";
      if (history[j].role === "agent" && text.indexOf("Wrote a config:\n") === 0) {
        currentConfig = text.slice("Wrote a config:\n".length);
        break;
      }
    }
  }

  function say(text) {
    var msg = document.createElement("div");
    msg.className = "agent-msg agent-msg-user";
    var body = document.createElement("div");
    body.textContent = text;
    msg.appendChild(body);
    tools(msg, [
      { label: "Copy", run: function () { copyText(text); } },
      { label: "Edit", title: "Edit this message in place and send it again",
        run: function () {
          /* In place, as every assistant a person has used does it: the
           * bubble becomes editable, Enter sends, Escape puts it back.
           * Copying the text down into the composer was a detour. */
          if (body.isContentEditable) return;
          var before = body.textContent;
          body.contentEditable = "true";
          body.classList.add("editing");
          body.focus();
          var range = document.createRange();
          range.selectNodeContents(body);
          range.collapse(false);
          var sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          function done(send) {
            body.contentEditable = "false";
            body.classList.remove("editing");
            body.removeEventListener("keydown", onKey);
            body.removeEventListener("blur", onBlur);
            var edited = body.textContent.trim();
            if (!send || !edited) {
              body.textContent = before;
              return;
            }
            cutFrom(msg);
            el("agent-request").value = edited;
            draft();
          }
          function onKey(e) {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); done(true); }
            if (e.key === "Escape") { e.preventDefault(); done(false); }
          }
          function onBlur() { done(false); }
          body.addEventListener("keydown", onKey);
          body.addEventListener("blur", onBlur);
        } },
      { label: "Retry", title: "Send this again from here",
        run: function () {
          cutFrom(msg);
          el("agent-request").value = text;
          draft();
        } }
    ]);
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
    tools(node, [
      { label: "Copy", title: "Copy this reply",
        run: function () {
          var result = part("result");
          var answer = node.querySelector(".agent-answer");
          copyText(answer ? answer.textContent
                   : (result && result.textContent) || part("attempts").textContent);
        } }
    ]);
    return { node: node, part: part };
  }

  function scrollToEnd() {
    /* The last message into view, whichever ancestor scrolls. Setting
     * scrollTop on the thread assumed the thread was the scroll
     * container, and when it was not, nothing moved. After a frame, so
     * the reply just appended has a height. */
    var thread = el("agent-thread");
    var column = thread.closest(".main") || document.scrollingElement;
    function toEnd() {
      /* Both: the thread when it is the scroller, the column when the
       * thread has grown to fit and the column is. Whichever overflows,
       * setting scrollTop past its end is harmless on the other. */
      thread.scrollTop = thread.scrollHeight;
      if (column) column.scrollTop = column.scrollHeight;
    }
    /* Once after the next paint, and again after the reply has finished
     * laying out -- the actions row appears and the config renders after
     * the first frame, and a scroll taken before that stopped a message
     * short, which is what was reported. */
    requestAnimationFrame(toEnd);
    setTimeout(toEnd, 120);
    setTimeout(toEnd, 400);
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
    history.push({ role: "user", text: typed });
    transcript.push({ role: "user", text: typed });
    area.value = "";
    autosize(area);
    var r = reply();
    var box = r.part("attempts");
    if (stopPending) {
      confirmStop(typed, box);
      scrollToEnd();
      return;
    }
    note(box, "Thinking\u2026");
    scrollToEnd();
    el("agent-propose").disabled = true;

    post("/api/agent/propose", {
      request: request,
      agent: el("agent-mode").value,
      history: history.slice(0, -1),
      current_config: currentConfig
    }).then(function (data) {
      el("agent-propose").disabled = false;
      box.innerHTML = "";
      (data.attempts || []).forEach(function (attempt) {
        if (attempt.refusal) note(box, "Refused: " + attempt.refusal.message);
      });
      if (data.action) {
        /* An instruction, carried out through the same door the button
         * uses. The thread says what was done, so nothing happens
         * silently. */
        history.push({ role: "agent", text: "DO: " + data.action });
        transcript.push({ role: "agent", kind: "action", action: data.action, where: data.where || "" });
        persist();
        act(data.action, data.where || "", box, r);
        scrollToEnd();
        return;
      }
      if (data.answer) {
        /* A question, answered. No config, no actions. */
        var p = document.createElement("div");
        p.className = "agent-answer";
        p.innerHTML = prose(data.answer);
        box.appendChild(p);
        history.push({ role: "agent", text: data.answer });
        transcript.push({ role: "agent", kind: "answer", text: data.answer });
        persist();
        area.focus();
        scrollToEnd();
        return;
      }
      if (data.question) {
        note(box, data.question);
        history.push({ role: "agent", text: data.question });
        transcript.push({ role: "agent", kind: "question", text: data.question });
        persist();
        pending = request;
        area.focus();
        scrollToEnd();
        return;
      }
      if (!data.ok) {
        note(box, IN_THE_GUI[data.code] || data.error);
        transcript.push({ role: "agent", kind: "error", text: IN_THE_GUI[data.code] || data.error });
        persist();
        if (IN_THE_GUI[data.code]) openSettings();
        scrollToEnd();
        return;
      }
      note(box, data.cycles === 1
        ? "Accepted first time."
        : "Accepted after " + data.cycles + " attempts.", true);
      history.push({ role: "agent", text: "Wrote a config:\n" + data.yaml });
      currentConfig = data.yaml;
      transcript.push({ role: "agent", kind: "config", yaml: data.yaml, config: data.config,
                        cycles: data.cycles, attempts: (data.attempts || []).map(function (a) {
                          return a.refusal ? { refusal: { message: a.refusal.message } } : {};
                        }) });
      persist();
      wireActions(r, data, box);
      scrollToEnd();
    }).catch(function () {
      el("agent-propose").disabled = false;
      box.innerHTML = "";
      note(box, "The Agent did not answer. Check Settings, then try again.");
    });
  }

  /* ---- Acting -------------------------------------------------------- */

  /* The last reply that carried a config, so "run it" has something to
   * run. And a stop that is waiting for a "yes". */
  var lastReply = null;
  var stopPending = null;

  function act(action, where, box, r) {
    if (action === "run") {
      if (!lastReply) {
        note(box, "Nothing to run yet. Describe a study first.");
        return;
      }
      var runBtn = lastReply.part("run");
      if (runBtn.disabled) {
        /* Already pressed, by hand or by a word. Clicking a disabled
         * button does nothing, and "Starting the run" over nothing was
         * a lie -- reported after Run here had been pressed first. */
        note(box, "It is already running.");
        return;
      }
      note(box, "Starting the run.", true);
      runBtn.click();
      return;
    }
    if (action === "stop") {
      /* Irreversible, so it is confirmed in the thread. The next message
       * that says yes stops it; anything else is taken as no. */
      stopPending = true;
      note(box, "Stop the run" + (where ? " at " + where : "") + "? Say yes.");
      return;
    }
    if (action.indexOf("open ") === 0) {
      var page = action.slice(5);
      var target = page === "builder" ? "run" : page;
      note(box, "Opening the " + page + ".", true);
      if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
        window.FastMDXDashboard.navigate(target);
      } else {
        window.location.hash = "#" + target;
      }
      return;
    }
    if (action === "show config") {
      if (!lastReply) { note(box, "No config yet."); return; }
      var result = lastReply.part("result");
      if (result.hidden) lastReply.part("show").click();
      note(box, "Shown above.", true);
      return;
    }
    if (action === "download config") {
      if (!lastReply) { note(box, "No config yet."); return; }
      lastReply.part("download").click();
      note(box, "Downloading.", true);
      return;
    }
    note(box, "I do not know how to " + action + ".");
  }

  function confirmStop(typed, box) {
    var yes = /^\s*(yes|y|yes please|do it|stop it|confirm)\s*\.?\s*$/i.test(typed);
    stopPending = null;
    if (!yes) {
      note(box, "Not stopped.");
      return;
    }
    fetch("/api/explore/stop", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var stopped = d && d.ok !== false;
        note(box, stopped ? "Stopped the run." : (d && d.error) || "Could not stop it.", true);
        history.push({ role: "agent", text: stopped ? "Stopped the run." : "Could not stop the run." });
        if (stopped && lastReply) {
          /* The same config can run again; each launch gets its own
           * timestamped folder, so the stopped run's output stays. */
          var again = lastReply.part("run");
          again.textContent = "Run again";
          again.disabled = false;
        }
      })
      .catch(function () { note(box, "Could not reach the server to stop it."); });
  }

  function wireActions(r, data, box) {
    lastReply = r;
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
          /* The conversation that launched a study belongs with it. Without
           * this the thread would vanish from view the moment the page
           * switched to the new study's empty list. */
          if (started.output) {
            post("/api/agent/conversation/attach", { study: started.output }).then(function (m) {
              if (m && m.moved) note(box, "This conversation now belongs to the new study.", true);
            }).catch(function () {});
          }
        } else {
          runBtn.disabled = false;
          noteEl.textContent = started.error;
        }
      });
    };
  }

  /* Draw a saved thread again. Each entry renders the way it rendered
   * the first time; a config gets its actions back, wired to the stored
   * config, so Run here on a restored thread runs what was written. */
  function restore() {
    fetch("/api/agent/conversation").then(function (r) { return r.json(); }).then(function (d) {
      replay((d && d.entries) || []);
    }).catch(function () {});
  }

  function replay(entries) {
      if (!entries.length) return;
      entries.forEach(function (e) {
        if (e.role === "user") {
          say(e.text || "");
          history.push({ role: "user", text: e.text || "" });
          transcript.push({ role: "user", text: e.text || "" });
          return;
        }
        var r = reply();
        var box = r.part("attempts");
        if (e.kind === "config") {
          (e.attempts || []).forEach(function (a) {
            if (a.refusal) note(box, "Refused: " + a.refusal.message);
          });
          note(box, e.cycles === 1 ? "Accepted first time."
               : "Accepted after " + (e.cycles || "several") + " attempts.", true);
          history.push({ role: "agent", text: "Wrote a config:\n" + e.yaml });
          currentConfig = e.yaml;
          wireActions(r, { yaml: e.yaml, config: e.config, cycles: e.cycles }, box);
        } else if (e.kind === "answer") {
          var p = document.createElement("div");
          p.className = "agent-answer";
          p.innerHTML = prose(e.text);
          box.appendChild(p);
          history.push({ role: "agent", text: e.text });
        } else if (e.kind === "question") {
          note(box, e.text);
          history.push({ role: "agent", text: e.text });
        } else if (e.kind === "action") {
          note(box, "Did: " + e.action + ".", true);
          history.push({ role: "agent", text: "DO: " + e.action });
        } else {
          note(box, e.text || "");
        }
        transcript.push(e);
      });
      scrollToEnd();
  }

  document.addEventListener("DOMContentLoaded", function () {
    restore();
    /* Start fresh: the thread on screen is already saved and stays in
     * the list. Nothing is lost, so nothing is confirmed. */
    function resetThread() {
      el("agent-thread").innerHTML = "";
      history = []; transcript = []; currentConfig = null; pending = null;
      stopPending = null; lastReply = null;
    }
    var fresh = el("agent-new");
    if (fresh) {
      fresh.addEventListener("click", function () {
        post("/api/agent/conversation/new", {}).then(function () {
          resetThread();
          hideList();
          el("agent-request").focus();
        }).catch(function () {});
      });
    }

    /* The list of conversations, newest first. Click a title to reopen
     * it; the cross deletes that one, and only that one, after asking. */
    var list = el("agent-conv-list");
    function hideList() { if (list) list.hidden = true; }
    function showList() {
      fetch("/api/agent/conversations").then(function (r) { return r.json(); }).then(function (d) {
        list.innerHTML = "";
        var groups = (d && d.groups) || [];
        var any = false;
        groups.forEach(function (g) {
          if (!g.conversations.length && !g.loaded) return;
          var head = document.createElement("div");
          head.className = "agent-conv-group" + (g.loaded ? " loaded" : "");
          head.textContent = g.label + (g.loaded ? "  \u00b7 loaded" : "");
          list.appendChild(head);
          if (!g.conversations.length) {
            var e = document.createElement("div");
            e.className = "agent-conv-empty";
            e.textContent = "No conversations yet.";
            list.appendChild(e);
          }
          g.conversations.forEach(function (c) {
            any = true;
            var row = document.createElement("div");
            row.className = "agent-conv-row" + (c.current && g.loaded ? " current" : "");
            var title = document.createElement("span");
            title.className = "title";
            title.textContent = c.title + (c.current && g.loaded ? "  (open)" : "");
            title.title = c.entries + " messages" + (g.loaded ? "" : " \u00b7 opens this study");
            title.addEventListener("click", function () {
              post("/api/agent/conversation/open", { id: c.id, study: g.study }).then(function (o) {
                if (!o || !o.ok) { window.alert((o && o.error) || "Could not open it."); return; }
                if (o.loaded_study && !g.loaded) {
                  /* Another study: the page reloads so every panel reads it,
                   * and the conversation is current there on return. */
                  location.reload();
                  return;
                }
                resetThread();
                hideList();
                replay(o.entries || []);
              });
            });
            var when = document.createElement("span");
            when.className = "when";
            when.textContent = c.started;
            var del = document.createElement("button");
            del.className = "del";
            del.type = "button";
            del.title = "Delete this conversation";
            del.textContent = "\u2715";
            del.addEventListener("click", function () {
              if (!window.confirm("Delete \u201c" + c.title + "\u201d? This cannot be undone.")) return;
              post("/api/agent/conversation/delete", { id: c.id, study: g.study }).then(function () {
                if (c.current && g.loaded) resetThread();
                showList();
              });
            });
            row.appendChild(title); row.appendChild(when); row.appendChild(del);
            list.appendChild(row);
          });
        });
        if (!any && !groups.length) {
          var none = document.createElement("div");
          none.className = "agent-conv-empty";
          none.textContent = "No conversations yet.";
          list.appendChild(none);
        }
        list.hidden = false;
      }).catch(function () {});
    }
    var convs = el("agent-conversations");
    if (convs && list) {
      convs.addEventListener("click", function () {
        if (list.hidden) showList(); else hideList();
      });
    }
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
