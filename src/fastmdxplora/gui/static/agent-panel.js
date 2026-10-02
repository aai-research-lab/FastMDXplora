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
                "required: it is the only thing left that can stop it.",
    unvalidated: "Works outside the schema, so nothing checks the method. " +
                 "Every figure it produces is stamped."
  };

  /* Where a key comes from, for the provider actually chosen. Said here
   * because this is where somebody needs it; a GUI that tells you to run
   * a command is a GUI that has given up. */
  var KEY_HELP = {
    anthropic: "Get one at platform.claude.com, under Settings \u2192 API " +
               "keys. A developer account \u2014 a chat subscription is " +
               "not the same thing.",
    openai: "Get one at platform.openai.com, under API keys.",
    compatible: "A local server usually needs none \u2014 leave this blank."
  };

  /* Refusals written for the command line, said the way this surface can
   * act on. `environment.model.unset` tells a terminal user to run
   * `fastmdx agent model`; there is a Settings button here instead. */
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
   * and nothing else. Escaped first, quotes included, so the model cannot
   * put markup in the page or close the link's attribute; then the four
   * patterns, in an order that keeps code spans from being reinterpreted.
   * A link ends at a quote, as it would in prose, and a full stop or
   * comma after it is the sentence's: "save https://x/BNZ_ideal.sdf." was
   * linked to "BNZ_ideal.sdf.", which is not there. Paragraphs are
   * blank-line separated. */
  function prose(text) {
    var s = String(text || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    s = s.replace(/`([^`\n]+)`/g, function (_, c) { return "<code>" + c + "</code>"; });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    /* Italic needs a non-space just inside each star, so "a * b * c" --
     * an arithmetic asterisk with spaces -- stays as typed. */
    s = s.replace(/(^|[^*])\*(\S(?:[^*\n]*\S)?)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/\bhttps?:\/\/(?:(?!&quot;|&#39;|&lt;|&gt;)[^\s<)])+/g, function (u) {
      var tail = "";
      // An escaped character (&amp;) ends in a semicolon that is the URL's.
      while (/[.,;:!?]$/.test(u) && !/&[a-z0-9#]+;$/i.test(u)) {
        tail = u.slice(-1) + tail;
        u = u.slice(0, -1);
      }
      return '<a href="' + u + '" target="_blank" rel="noopener">' + u + "</a>" + tail;
    });
    return s;
  }

  /* What an answer drew on: each analysis it names, with the mean the
   * study recorded for it, opening its figure on the Analysis page. The
   * value is the record's, so a number in the prose can be read against
   * it. Built as elements, never as markup: a label is data. */
  function cite(box, cites) {
    if (!cites || !cites.length) return null;
    var row = document.createElement("div");
    row.className = "agent-cites";
    var lead = document.createElement("span");
    lead.className = "agent-cites-lead";
    lead.textContent = "From the study";
    row.appendChild(lead);
    cites.forEach(function (c) {
      if (!c || !c.analysis) return;
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "agent-cite" + (c.withheld ? " is-withheld" : "");
      chip.setAttribute("data-analysis", c.analysis);
      var name = document.createElement("span");
      name.className = "agent-cite-name";
      name.textContent = c.label || c.analysis;
      chip.appendChild(name);
      var value = document.createElement("span");
      value.className = "agent-cite-value";
      value.textContent = c.value
        ? c.value + (c.withheld ? ", not determined" : "")
        : "figure";
      chip.appendChild(value);
      chip.title = (c.withheld ? c.withheld + " " : "") +
        "Open the " + (c.label || c.analysis) + " figure on the Analysis page.";
      chip.addEventListener("click", function () {
        var board = window.FastMDXDashboard;
        if (board && board.showAnalysis) board.showAnalysis(c.analysis);
        else window.location.hash = "#analysis";
      });
      row.appendChild(chip);
    });
    box.appendChild(row);
    return row;
  }

  /* What the Agent looked at with the software's own tools before it
   * answered: each look, what it asked and what the software said, folded
   * under one line so the answer stays first. The words are the software's,
   * put in as text: a tool's answer quotes a structure file's names. */
  var LOOKED = {
    inspect_structure: "Inspected the structure",
    preview_setup: "Previewed what setup builds",
    check_config: "Checked the config",
    check_selection: "Checked a selection",
    read_study: "Read another study's record"
  };

  function looked(box, looks) {
    if (!looks || !looks.length) return null;
    var fold = document.createElement("details");
    fold.className = "agent-looks";
    var head = document.createElement("summary");
    head.textContent = "Checked with the software: " + looks.map(function (l) {
      return (LOOKED[l.tool] || l.tool).toLowerCase() + (l.ok ? "" : " (refused)");
    }).join("; ");
    fold.appendChild(head);
    looks.forEach(function (l) {
      var item = document.createElement("div");
      item.className = "agent-look" + (l.ok ? "" : " is-refused");
      item.setAttribute("data-tool", l.tool || "");
      var name = document.createElement("div");
      name.className = "agent-look-name";
      var asked = l.asked && typeof l.asked === "object"
        ? Object.keys(l.asked).map(function (k) { return k + ": " + l.asked[k]; }).join(", ")
        : "";
      name.textContent = (LOOKED[l.tool] || l.tool) + (asked ? " (" + asked + ")" : "");
      item.appendChild(name);
      var said = document.createElement("pre");
      said.className = "agent-look-said";
      said.textContent = l.said || "";
      item.appendChild(said);
      fold.appendChild(item);
    });
    box.appendChild(fold);
    return fold;
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
    engineChosen = !!current;
    syncStart();
  }

  /* What can be asked, shown while the thread is empty: the study's own
   * questions when one is open, and studies to start. A suggestion fills
   * the composer; it does not send. */
  var engineChosen = true;
  var studyOpen = false;
  function syncStart() {
    var start = el("agent-start");
    var thread = el("agent-thread");
    if (!start || !thread) return;
    start.hidden = thread.childElementCount > 0;
    var engine = el("agent-start-engine");
    if (engine) engine.hidden = engineChosen;
    var study = el("agent-start-study");
    if (study) study.hidden = !studyOpen;
    var note = el("agent-start-note");
    var mode = el("agent-mode");
    if (note && mode) {
      note.textContent = "It drafts a config and the software checks it. "
        + (MODE_NOTES[mode.value] || "");
    }
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
  /* Which conversation this is and where it lives, from the last save or
   * restore, so a launch can name it exactly when moving it. */
  var convId = null;
  var convStudy = null;
  /* Files attached to the next message: what read_attachment returned. */
  var pendingFiles = [];

  function fmtSize(n) {
    return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
  }

  function renderChips() {
    var host = el("agent-attachments");
    if (!host) return;
    host.innerHTML = "";
    host.hidden = pendingFiles.length === 0;
    pendingFiles.forEach(function (f, i) {
      var chip = document.createElement("span");
      chip.className = "agent-chip";
      var name = document.createElement("span");
      name.className = "name"; name.textContent = f.name; name.title = f.path;
      var size = document.createElement("span");
      size.className = "size"; size.textContent = fmtSize(f.size) + (f.truncated ? " \u00b7 cut" : "");
      var rm = document.createElement("button");
      rm.className = "rm"; rm.type = "button"; rm.textContent = "\u2715"; rm.title = "Remove";
      rm.addEventListener("click", function () { pendingFiles.splice(i, 1); renderChips(); });
      chip.appendChild(name); chip.appendChild(size); chip.appendChild(rm);
      host.appendChild(chip);
    });
  }

  function attachFile(path) {
    if (!path) return;
    post("/api/agent/attachment", { path: path }).then(function (d) {
      if (!d || !d.ok) { window.alert((d && d.error) || "Could not attach that file."); return; }
      if (pendingFiles.some(function (f) { return f.path === d.path; })) return;
      if (pendingFiles.length >= 6) { window.alert("Six files at most on one message."); return; }
      pendingFiles.push(d);
      renderChips();
      el("agent-request").focus();
    }).catch(function () { window.alert("Could not reach the server."); });
  }

  function persist() {
    return post("/api/agent/conversation", { entries: transcript }).then(function (d) {
      if (d && d.ok) {
        convId = d.id || convId;
        if (d.study !== undefined) convStudy = d.study;
      }
      return d;
    }).catch(function () {});
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
    runPending = null;
    fixPending = null;
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

  function say(text, attached) {
    var msg = document.createElement("div");
    msg.className = "agent-msg agent-msg-user";
    var body = document.createElement("div");
    body.textContent = text;
    if (attached && attached.length) {
      var list = document.createElement("div");
      list.className = "attached";
      list.textContent = "\u{1F4CE} " + attached.map(function (a) { return a.name; }).join(", ");
      list.title = attached.map(function (a) { return a.path + " (" + a.sha256 + ")"; }).join("\n");
      body.appendChild(list);
    }
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
    // scrollHeight leaves the border out, and the box is border-box, so
    // setting it alone made every line two pixels short -- the text was
    // clipped and the box jittered as it grew. The border is added back.
    area.style.height = "auto";
    var border = area.offsetHeight - area.clientHeight;
    area.style.height = Math.min(area.scrollHeight + border, window.innerHeight * 0.4) + "px";
  }

  function draft() {
    var area = el("agent-request");
    var typed = area.value.trim();
    if (!typed || writing) return;
    var request = pending ? pending + "\n" + typed : typed;
    pending = null;

    var files = pendingFiles.slice();
    pendingFiles = [];
    renderChips();
    var record = files.map(function (f) {
      return { name: f.name, path: f.path, size: f.size, sha256: f.sha256, truncated: !!f.truncated };
    });
    say(typed, record);
    var historyText = typed + (record.length ? "\n[attached: " + record.map(function (a) { return a.name; }).join(", ") + "]" : "");
    history.push({ role: "user", text: historyText });
    transcript.push(record.length ? { role: "user", text: typed, attachments: record } : { role: "user", text: typed });
    area.value = "";
    autosize(area);
    var r = reply();
    var box = r.part("attempts");
    if (stopPending) {
      confirmStop(typed, box);
      scrollToEnd();
      return;
    }
    if (runPending) {
      confirmRun(typed, box);
      scrollToEnd();
      return;
    }
    if (fixPending) {
      confirmFix(typed, box);
      scrollToEnd();
      return;
    }
    note(box, "Thinking\u2026");
    scrollToEnd();

    propose({
      request: request,
      agent: el("agent-mode").value,
      history: history.slice(0, -1),
      current_config: currentConfig,
      attachments: files.map(function (f) { return { name: f.name, text: f.text, truncated: !!f.truncated }; })
    }, box).then(function (data) {
      box.innerHTML = "";
      (data.attempts || []).forEach(function (attempt) {
        if (attempt.refusal) note(box, "Refused: " + attempt.refusal.message);
      });
      looked(box, data.looks);
      if (data.action) {
        /* An instruction, carried out through the same door the button
         * uses. The thread says what was done, so nothing happens
         * silently. */
        history.push({ role: "agent", text: "DO: " + data.action });
        /* A run the person's own message did not plainly ask for is
         * asked about first. The server says which from what they typed;
         * without its word, ask. */
        var confirmRunFirst = data.action === "run" && data.confirm !== false;
        if (data.action === "run the fix" || data.action === "rerun windows") {
          /* Asked, not done, and not kept as waiting: a reloaded thread
           * asks the Agent again rather than run a fix it no longer shows. */
          transcript.push({ role: "agent", kind: "question",
                            text: data.fix ? fixQuestion(data.fix)
                                           : data.refused || "Nothing here to run." });
        } else if (confirmRunFirst) {
          transcript.push({ role: "agent", kind: "question", text: RUN_QUESTION });
        } else if (data.action === "stop") {
          /* Asked, not done. A stop is recorded when it is confirmed, so
           * a reloaded thread never says "Did: stop" about a run that was
           * never stopped -- which it did, and the person's "yes" then
           * went to the model as a new message. */
          transcript.push({ role: "agent", kind: "question",
                            text: "Stop the run" + (data.where ? " at " + data.where : "") + "? Say yes." });
        } else {
          transcript.push({ role: "agent", kind: "action", action: data.action, where: data.where || "" });
        }
        persist();
        act(data.action, data.where || "", box, r, confirmRunFirst, data.fix || null,
            data.refused || "");
        scrollToEnd();
        return;
      }
      if (data.answer) {
        /* A question, answered. No config, no actions. */
        var p = document.createElement("div");
        p.className = "agent-answer";
        p.innerHTML = prose(data.answer);
        box.appendChild(p);
        cite(box, data.cites);
        history.push({ role: "agent", text: data.answer });
        transcript.push({ role: "agent", kind: "answer", text: data.answer,
                          cites: data.cites || [], looks: data.looks || [] });
        persist();
        area.focus();
        scrollToEnd();
        return;
      }
      if (data.question) {
        note(box, data.question);
        history.push({ role: "agent", text: data.question });
        transcript.push({ role: "agent", kind: "question", text: data.question,
                          looks: data.looks || [] });
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
                        plan: data.plan || [], looks: data.looks || [],
                        cycles: data.cycles, attempts: (data.attempts || []).map(function (a) {
                          return a.refusal ? { refusal: { message: a.refusal.message } } : {};
                        }) });
      persist();
      wireActions(r, data, box);
      scrollToEnd();
    }).catch(function (error) {
      box.innerHTML = "";
      if (error && error.name === "AbortError") {
        /* Stopped by the person: what it had written is not kept, and the
         * next message goes on from their last. */
        note(box, "Stopped before it finished.");
        history.push({ role: "agent", text: "(stopped before answering)" });
        transcript.push({ role: "agent", kind: "answer", text: "Stopped before it finished." });
        persist();
        return;
      }
      note(box, "The Agent did not answer. Check Settings, then try again.");
    });
  }

  /* ---- The reply as it is written ------------------------------------ */

  /* The server sends the reply as the model writes it, one event a line
   * (`/api/agent/propose-stream`): a `begin` each time the model is asked,
   * its text in pieces, each look as it is taken, and the answer at the
   * end, the same as `/api/agent/propose` gives. The send button stops it
   * while it is written. A browser without streams asks for it whole. */
  var writing = null;

  function writingState(on) {
    var button = el("agent-propose");
    button.classList.toggle("is-writing", on);
    button.setAttribute("aria-label", on ? "Stop" : "Send");
    button.title = on ? "Stop the reply being written"
      : "Send (Enter). Shift+Enter for a new line.";
    button.textContent = on ? "\u25a0" : "\u2191";
  }

  /* What the model is writing, as a person reads it: a look is said as
   * one, the reply's own marker is left off. */
  function writtenSoFar(raw) {
    if (/^\s*USE:/.test(raw)) return "Looking with the software\u2026";
    return raw.replace(/^\s*(SAY|ASK):\s*/, "");
  }

  function propose(body, box) {
    if (!window.ReadableStream || !window.AbortController || !window.TextDecoder) {
      writingState(true);
      return post("/api/agent/propose", body).finally(function () { writingState(false); });
    }
    writing = new AbortController();
    writingState(true);
    var shown = null;
    var raw = "";
    var answer = null;
    function handle(line) {
      if (!line.trim()) return;
      var event;
      try { event = JSON.parse(line); } catch (e) { return; }
      if (event.type === "begin") {
        raw = "";
        if (!shown) {
          box.innerHTML = "";
          shown = document.createElement("div");
          shown.className = "agent-writing";
          box.appendChild(shown);
        }
        shown.textContent = "";
      } else if (event.type === "text" && shown) {
        raw += event.text || "";
        shown.textContent = writtenSoFar(raw);
        scrollToEnd();
      } else if (event.type === "look" && event.look) {
        var said = document.createElement("div");
        said.className = "agent-writing-look";
        said.textContent = "Looked: " + (LOOKED[event.look.tool] || event.look.tool);
        box.insertBefore(said, shown);
      } else if (event.type === "done") {
        answer = event.answer;
      }
    }
    return fetch("/api/agent/propose-stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body || {}),
      signal: writing.signal
    }).then(function (response) {
      // A refusal before the reply began (a request from beyond this
      // machine, a malformed body) is one JSON answer, not a stream.
      if (!/ndjson/.test(response.headers.get("content-type") || "")) return response.json();
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      function pump() {
        return reader.read().then(function (part) {
          if (part.done) {
            if (buffer) handle(buffer);
            return answer || { ok: false, error: "The Agent did not answer." };
          }
          buffer += decoder.decode(part.value, { stream: true });
          var lines = buffer.split("\n");
          buffer = lines.pop();
          lines.forEach(handle);
          return pump();
        });
      }
      return pump();
    }).finally(function () {
      writing = null;
      writingState(false);
    });
  }

  /* ---- Acting -------------------------------------------------------- */

  /* The last reply that carried a config, so "run it" has something to
   * run. And a stop, or a run, that is waiting for a "yes". */
  var lastReply = null;
  var stopPending = null;
  var runPending = null;
  var fixPending = null;
  var RUN_QUESTION = "Run the study above? Say yes.";

  /* Yes, or the verb being confirmed: "stop it" confirms a stop and not
   * a run, and "run it" a run and not a stop. */
  function saidYes(typed, verb) {
    return new RegExp("^\\s*(yes|y|yes please|do it|" + verb + " it|confirm)\\s*\\.?\\s*$", "i")
      .test(typed);
  }

  function act(action, where, box, r, confirmFirst, fix, refused) {
    if (action === "rerun windows") {
      /* The windows and values the person named, checked by the server
       * against the study; asked with the price, or said why not. */
      if (!fix) {
        note(box, refused || "Those windows cannot be run again here.");
        return;
      }
      fixPending = fix;
      note(box, fixQuestion(fix));
      return;
    }
    if (action === "run the fix") {
      /* The fix is the study's record's, not the reply's: its command and
       * its price, shown before anything runs. Always asked. */
      if (!fix) {
        note(box, "Nothing here is a fix this software runs for you; the Overview " +
                  "says what would fix it.");
        return;
      }
      fixPending = fix;
      note(box, fixQuestion(fix));
      return;
    }
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
      if (confirmFirst) {
        /* The reply came from a model, which reads attached files and can
         * be wrong or be told what to say. Starting work on this machine
         * waits for the person, as a stop does. */
        runPending = true;
        note(box, RUN_QUESTION);
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

  function fixQuestion(fix) {
    /* Windows named by the person are said as what will run, with the
     * values; a fix from the record as its command. */
    var what = fix.request ? String(fix.fix || "").replace(/\.$/, "") : "Run " + fix.command;
    return what + "?" +
      (fix.price_said ? " It costs " + fix.price_said + "." : "") + " Say yes.";
  }

  function confirmFix(typed, box) {
    var fix = fixPending;
    fixPending = null;
    if (!saidYes(typed, "run")) {
      note(box, "Not run.");
      transcript.push({ role: "agent", kind: "answer", text: "Not run." });
      persist();
      return;
    }
    fetch("/api/fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fix.request || { index: fix.index })
    }).then(function (res) { return res.json(); }).then(function (d) {
      var started = d && d.ok;
      var said = started ? "Started: " + fix.command + "." : (d && d.error) || "Could not start it.";
      note(box, said, started);
      history.push({ role: "agent", text: said });
      transcript.push(started ? { role: "agent", kind: "action",
                                  action: fix.request ? "rerun windows" : "run the fix", where: "" }
                              : { role: "agent", kind: "error", text: said });
      persist();
      if (started && window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
        window.FastMDXDashboard.navigate("overview");
      }
    }).catch(function () { note(box, "Could not reach the server to run it."); });
  }

  function confirmRun(typed, box) {
    runPending = null;
    if (!saidYes(typed, "run")) {
      note(box, "Not run.");
      transcript.push({ role: "agent", kind: "answer", text: "Not run." });
      persist();
      return;
    }
    transcript.push({ role: "agent", kind: "action", action: "run", where: "" });
    history.push({ role: "agent", text: "DO: run" });
    persist();
    act("run", "", box, null, false);
  }

  function confirmStop(typed, box) {
    var yes = saidYes(typed, "stop");
    stopPending = null;
    if (!yes) {
      note(box, "Not stopped.");
      transcript.push({ role: "agent", kind: "answer", text: "Not stopped." });
      persist();
      return;
    }
    fetch("/api/explore/stop", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var stopped = d && d.ok !== false;
        note(box, stopped ? "Stopped the run." : (d && d.error) || "Could not stop it.", true);
        history.push({ role: "agent", text: stopped ? "Stopped the run." : "Could not stop the run." });
        transcript.push(stopped ? { role: "agent", kind: "action", action: "stop", where: "" }
                                : { role: "agent", kind: "error", text: "Could not stop the run." });
        persist();
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

  /* What the config will do, said in lines rather than left in the YAML:
   * the values the run will take, with the ones it takes by default marked,
   * so a two-nanosecond run nobody asked for is seen before it is run. */
  function addPlanLines(host, lines) {
    if (!host) return;
    lines.forEach(function (line) {
      var term = document.createElement("dt");
      term.textContent = line.label;
      term.className = "agent-plan-cost";
      var value = document.createElement("dd");
      value.textContent = line.value;
      value.className = "agent-plan-cost";
      host.appendChild(term);
      host.appendChild(value);
    });
    host.hidden = false;
  }

  function showPlan(host, plan) {
    if (!host) return;
    host.innerHTML = "";
    var lines = Array.isArray(plan) ? plan : [];
    lines.forEach(function (line) {
      var term = document.createElement("dt");
      term.textContent = line.label;
      var value = document.createElement("dd");
      value.textContent = line.value;
      if (line["default"]) {
        var tag = document.createElement("span");
        tag.className = "agent-plan-default";
        tag.textContent = "default";
        tag.title = "Not set in the config: the value the run takes when nothing is said.";
        value.appendChild(document.createTextNode(" "));
        value.appendChild(tag);
      }
      host.appendChild(term);
      host.appendChild(value);
    });
    host.hidden = !lines.length;
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
    showPlan(r.part("plan"), data.plan);

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
      // Waits for the builder's schema where the reply arrived first.
      return Promise.resolve(run.applyLoadedState(m.state, {})).then(function () {
        return true;
      });
    });

    /* What the proposal would build and how long it would take here, added
     * to its plan once the builder holds it: the cost read before the run,
     * not learned from setup's log. A moment after the reply, because a
     * structure named by its identifier is fetched to be inspected. */
    loaded.then(function (ok) {
      var run = window.FastMDXRun;
      if (!ok || !run || !run.previewCost) return;
      var wants = (data.config && data.config.include_phase) || null;
      if (wants && wants.indexOf("setup") < 0) return;
      run.previewCost().then(function (cost) {
        if (!cost) return;
        addPlanLines(r.part("plan"), [
          { label: "System", value: cost.size },
          { label: "Time here", value: cost.time },
        ]);
      });
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
      /* The thread is saved before the launch, and the launch is told
       * which conversation to move and where it is now. The launch
       * switches the loaded study before the move runs, so "the current
       * conversation" is the new study's -- none -- and the first version
       * moved nothing; the next save then started a fresh thread in the
       * new study from whatever the browser held, minus a race. */
      var fromStudy = convStudy;
      persist().then(function () {
        return post("/api/agent/run", {
          config: data.config,
          budget_hours: el("agent-budget").value
        });
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
            post("/api/agent/conversation/attach", {
              study: started.output, id: convId, from_study: fromStudy
            }).then(function (m) {
              if (m && m.ok && m.id) { convId = m.id; convStudy = m.study || started.output; }
              if (m && m.moved) note(box, "This conversation now belongs to the new study.", true);
            }).catch(function () {});
          }
        } else {
          runBtn.disabled = false;
          noteEl.textContent = started.error;
          runFix(r.part("fix"), started, data);
        }
      });
    };
  }

  /* A refused run with what would fix it (remedies.py): the fix, and the
   * command that does it where there is one. Nothing has run, so there is
   * no price. Where the fix is a setting of the
   * study, the Agent is offered it: pressing the button sends the refusal
   * into the thread as the person's next message, and the Agent rewrites
   * the config it wrote. A choice only the person can make, a budget, an
   * install and a machine's state are said and not handed on: the
   * software does not choose them, so neither does the model. Built as
   * elements; a refusal's text is data. */
  function runFix(host, started, data) {
    if (!host) return;
    host.innerHTML = "";
    var remedy = started && started.remedy;
    host.hidden = !remedy;
    if (!remedy) return;
    var fix = document.createElement("div");
    fix.className = "fix-fix";
    fix.innerHTML = prose("Fix: " + (remedy.fix || ""));
    host.appendChild(fix);
    if (remedy.command) {
      var cmd = document.createElement("pre");
      cmd.className = "fix-command";
      cmd.textContent = remedy.command;
      host.appendChild(cmd);
    }
    var theStudys = (remedy.settings || []).length && !remedy.decision &&
      String(remedy.code || "").indexOf("environment.") !== 0;
    if (!theStudys) return;
    var ask = document.createElement("button");
    ask.type = "button";
    ask.className = "ctl-btn agent-fix-ask";
    ask.textContent = "Ask the Agent to fix it";
    ask.addEventListener("click", function () {
      ask.disabled = true;
      /* A new instruction, so nothing still waiting for a yes takes it. */
      pending = null; stopPending = null; runPending = null; fixPending = null;
      currentConfig = data.yaml;
      var area = el("agent-request");
      area.value = "Running it was refused: " + (started.error || remedy.why || "") +
        "\nWhat would fix it: " + (remedy.fix || "") + "\nChange the config so it runs.";
      draft();
    });
    host.appendChild(ask);
  }

  /* Draw a saved thread again. Each entry renders the way it rendered
   * the first time; a config gets its actions back, wired to the stored
   * config, so Run here on a restored thread runs what was written. */
  function restore() {
    fetch("/api/agent/conversation").then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) { convId = d.id || null; convStudy = d.study || null; }
      replay((d && d.entries) || []);
    }).catch(function () {});
  }

  function replay(entries) {
      if (!entries.length) return;
      entries.forEach(function (e) {
        if (e.role === "user") {
          say(e.text || "", e.attachments || []);
          var htext = (e.text || "") + ((e.attachments || []).length
            ? "\n[attached: " + e.attachments.map(function (a) { return a.name; }).join(", ") + "]" : "");
          history.push({ role: "user", text: htext });
          transcript.push(e);
          return;
        }
        var r = reply();
        var box = r.part("attempts");
        looked(box, e.looks);
        if (e.kind === "config") {
          (e.attempts || []).forEach(function (a) {
            if (a.refusal) note(box, "Refused: " + a.refusal.message);
          });
          note(box, e.cycles === 1 ? "Accepted first time."
               : "Accepted after " + (e.cycles || "several") + " attempts.", true);
          history.push({ role: "agent", text: "Wrote a config:\n" + e.yaml });
          currentConfig = e.yaml;
          wireActions(r, { yaml: e.yaml, config: e.config, cycles: e.cycles,
                           plan: e.plan || [] }, box);
        } else if (e.kind === "answer") {
          var p = document.createElement("div");
          p.className = "agent-answer";
          p.innerHTML = prose(e.text);
          box.appendChild(p);
          cite(box, e.cites);
          history.push({ role: "agent", text: e.text });
        } else if (e.kind === "question") {
          note(box, e.text);
          history.push({ role: "agent", text: e.text });
          // A stop confirmation that was the last thing said is still
          // waiting for its yes after a reload.
          stopPending = /^Stop the run.*\? Say yes\.$/.test(e.text || "") ? true : null;
          runPending = e.text === RUN_QUESTION ? true : null;
        } else if (e.kind === "action") {
          note(box, e.action === "stop" ? "Stopped the run." : "Did: " + e.action + ".", true);
          history.push({ role: "agent", text: "DO: " + e.action });
        } else {
          note(box, e.text || "");
        }
        transcript.push(e);
      });
      // Only a stop question that is the final entry keeps its pending
      // state; anything said after it answered or superseded it.
      var last = entries[entries.length - 1];
      if (!(last && last.kind === "question" && /^Stop the run.*\? Say yes\.$/.test(last.text || ""))) {
        stopPending = null;
      }
      if (!(last && last.kind === "question" && last.text === RUN_QUESTION)) {
        runPending = null;
      }
      scrollToEnd();
  }

  document.addEventListener("DOMContentLoaded", function () {
    restore();
    /* Start fresh: the thread on screen is already saved and stays in
     * the list. Nothing is lost, so nothing is confirmed. */
    function resetThread() {
      el("agent-thread").innerHTML = "";
      history = []; transcript = []; currentConfig = null; pending = null;
      stopPending = null; runPending = null; fixPending = null; lastReply = null;
    }
    var fresh = el("agent-new");
    if (fresh) {
      fresh.addEventListener("click", function () {
        post("/api/agent/conversation/new", {}).then(function (d) {
          if (d && d.ok) convId = d.id || null;
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
                convId = o.id || null; convStudy = o.study || null;
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
    el("agent-propose").addEventListener("click", function () {
      if (writing) writing.abort();
      else draft();
    });
    var plus = el("agent-attach");
    var attachPath = el("agent-attach-path");
    /* The workspace, from the app state, so the picker can open there
     * for a thread about no study. */
    var workspaceRoot = "";
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("app-state", function (s) {
        workspaceRoot = (s && s.exploration_root) || workspaceRoot;
      });
    }
    if (plus && attachPath && window.FastMDXPicker) {
      plus.addEventListener("click", function () {
        /* Open where this conversation lives: the study's own folder for
         * a thread about a study, the workspace for a general one. */
        window.FastMDXPicker.open({ into: "agent-attach-path", mode: "file",
                                    start: convStudy || workspaceRoot || "" });
      });
      attachPath.addEventListener("change", function () {
        var p = attachPath.value.trim();
        attachPath.value = "";
        attachFile(p);
      });
    }
    var area = el("agent-request");
    area.addEventListener("input", function () { autosize(area); });
    area.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        // A reply being written is stopped by the button, not by Enter.
        if (!writing) draft();
      }
    });
    autosize(area);

    function modeChanged() {
      var mode = el("agent-mode").value;
      el("agent-mode-note").textContent = MODE_NOTES[mode] || "";
      var footer = el("agent-footer-mode");
      if (footer) footer.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
      el("agent-budget-note").textContent = mode === "autonomous"
        ? "Required in this mode. Checked after setup, where the cost is "
          + "first known."
        : "Optional. Stops a study that would cost more than you meant.";
    }
    el("agent-mode").addEventListener("change", modeChanged);
    el("agent-mode").addEventListener("change", syncStart);
    modeChanged();

    Array.prototype.forEach.call(document.querySelectorAll(".agent-starter"), function (b) {
      b.addEventListener("click", function () {
        area.value = b.getAttribute("data-prompt") || b.textContent;
        autosize(area);
        area.focus();
        area.setSelectionRange(area.value.length, area.value.length);
      });
    });
    var chooseEngine = el("agent-start-settings");
    if (chooseEngine) chooseEngine.addEventListener("click", openSettings);
    if (window.MutationObserver) {
      new MutationObserver(syncStart).observe(el("agent-thread"), { childList: true });
    }
    /* A study to ask about is one that has run: a folder is served as the
     * active run before anything is in it. */
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      var ran = { status: false, results: false };
      var served = false;
      var decide = function () {
        studyOpen = served && (ran.status || ran.results);
        syncStart();
      };
      window.FastMDXDashboard.on("app-state", function (s) {
        served = !!(s && s.active_run);
        decide();
      });
      window.FastMDXDashboard.on("status-updated", function (u) {
        var status = (u && u.status) || {};
        ran.status = !!(status.stage || status.current_step != null);
        decide();
      });
      window.FastMDXDashboard.on("results-updated", function (r) {
        ran.results = !!(r && (r.has_analysis || r.has_report));
        decide();
      });
    }
    syncStart();
  });

  /* For the settings popup: "Agent settings…" should open this dialog,
   * not merely land on the Agent page. */
  window.FastMDXAgent = { openSettings: openSettings, closeSettings: closeSettings };
})();
