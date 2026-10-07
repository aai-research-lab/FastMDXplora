/* The Agent panel.
 *
 * The subject of this page is the FastMDXplora Agent. Which AI model
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
   * moment of choosing. The Agent says it of itself. */
  var MODE_NOTES = {
    assisted: "I show you each one first. Nothing runs until you say so.",
    autonomous: "I run it without showing it to you first, so a ceiling is " +
                "required: it is the only thing left that can stop me.",
    unvalidated: "I work outside the schema, so nothing validates the method. " +
                 "Every figure I produce is stamped."
  };

  /* Where a key comes from, for the provider actually chosen. Said here
   * because this is where somebody needs it; a GUI that tells you to run
   * a command is a GUI that has given up. */
  var KEY_HELP = {
    anthropic: "Get one at platform.claude.com, under Settings \u2192 API " +
               "keys. A developer account: a chat subscription is " +
               "not the same thing.",
    openai: "Get one at platform.openai.com, under API keys.",
    compatible: "A local server usually needs none; leave this blank."
  };

  /* Refusals written for the command line, said the way this surface can
   * act on. `environment.model.unset` tells a terminal user to run
   * `fastmdx agent model`; there is a Settings button here instead. */
  var IN_THE_GUI = {
    "environment.model.unset":
      "I am not set up yet. Open Settings to set me up.",
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
   * and nothing else. Escaped first, quotes included, so the AI model cannot
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

  /* A scene an answer proposes, to show what it is about: said in words,
   * named, and written with the study only when the button is pressed
   * (POST /api/scenes); the Agent itself writes nothing. Once written it can
   * be shown in the Viewer. Built as elements: every part is data. */
  var SHOWN_AS = { ballAndStick: "ball and stick", spacefill: "spheres" };

  function sceneSaid(scene) {
    var parts = [];
    if (scene.frame != null) parts.push("frame " + scene.frame);
    if (scene.representation) parts.push(SHOWN_AS[scene.representation] || scene.representation);
    if (scene.colour) {
      parts.push("coloured by " + String(scene.colour).replace(/^result:/, "").replace(/_/g, " "));
    }
    if (scene.superposed) parts.push("superposed on the " + scene.superposed);
    if (scene.highlight) {
      parts.push(scene.highlight + " highlighted" + (scene.labels ? " and labelled" : ""));
    }
    return parts.join(", ") || "the study as it opens";
  }

  function sceneLigands() {
    var state = window.FastMDXMoleculeViewer && window.FastMDXMoleculeViewer.STATE;
    var names = state && state.structureInfo && Array.isArray(state.structureInfo.ligand_resnames)
      ? state.structureInfo.ligand_resnames.filter(Boolean) : [];
    return names.slice(0, 20);
  }

  function sceneCard(box, scene, entry) {
    if (!scene) return null;
    var card = document.createElement("div");
    card.className = "agent-scene";
    var lead = document.createElement("p");
    lead.className = "agent-scene-said";
    lead.textContent = "A scene to show this: " + sceneSaid(scene) + ".";
    card.appendChild(lead);
    var row = document.createElement("div");
    row.className = "agent-scene-row";
    var name = document.createElement("input");
    name.type = "text";
    name.maxLength = 60;
    name.setAttribute("aria-label", "Name of the scene");
    name.value = entry.scene_written || scene.name || "From the Agent";
    var write = document.createElement("button");
    write.type = "button";
    write.className = "chip-btn";
    write.textContent = "Write this scene";
    write.title = "Write it with the study as a scene file (scenes/, MolViewSpec) and show it in the Viewer";
    var show = document.createElement("button");
    show.type = "button";
    show.className = "chip-btn";
    show.textContent = "Show it in the Viewer";
    show.hidden = !entry.scene_written;
    var said = document.createElement("span");
    said.className = "agent-scene-status muted small";
    said.setAttribute("role", "status");
    row.append(name, write, show);
    card.append(row, said);
    function shown(written) {
      window.location.hash = "#viewer";
      var views = window.FastMDXViewerViews;
      if (!views || !views.loadScenes || !views.showScene) return;
      // Once the Viewer has a molecule: opened from here, it has none yet.
      var tries = 0;
      (function whenReady() {
        var viewer = window.FastMDXMoleculeViewer;
        if (viewer && viewer.STATE && viewer.STATE.engine && viewer.STATE.model) {
          views.loadScenes(written).then(function () { return views.showScene(written); });
        } else if (tries++ < 240) {
          window.setTimeout(whenReady, 250);
        }
      }());
    }
    if (entry.scene_written) {
      write.disabled = true;
      write.textContent = "Written";
      name.disabled = true;
      said.textContent = "Written as scenes/" + entry.scene_written + ".mvsx.";
    }
    show.addEventListener("click", function () { shown(entry.scene_written); });
    write.addEventListener("click", function () {
      var named = name.value.trim();
      write.disabled = true;
      said.textContent = "Writing the scene\u2026";
      var view = {};
      ["frame", "representation", "colour", "superposed"].forEach(function (key) {
        if (scene[key] != null) view[key] = scene[key];
      });
      fetch("/api/scenes", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: named, view: view, highlight: scene.highlight || "",
                               labels: !!scene.labels, ligands: sceneLigands() }),
      }).then(function (r) {
        return r.json().catch(function () { return { ok: false, reason: "HTTP " + r.status }; });
      }).catch(function () { return { ok: false, reason: "The server did not answer." }; })
        .then(function (done) {
          if (!done || !done.ok) {
            write.disabled = false;
            said.textContent = "The scene was not written: " + ((done && done.reason) || "no reason given");
            return;
          }
          entry.scene_written = done.name;
          persist();
          write.textContent = "Written";
          name.disabled = true;
          show.hidden = false;
          said.textContent = "Written as scenes/" + done.name + ".mvsx"
            + (done.notes && done.notes.length ? ". " + done.notes.join(" ") : ".");
          shown(done.name);
        });
    });
    box.appendChild(card);
    return card;
  }

  /* What the Agent looked at with the software's own tools before it
   * answered, folded under one line so the answer stays first: each look
   * in words (`agent/tools.py` LOOK_WORDS, sent as `label`), what it found
   * in a line, and what the software said in full under that. The words
   * are the software's, put in as text: a tool's answer quotes a structure
   * file's names. A conversation kept before the labels were sent is said
   * from this list; a tool not in it by its name. */
  var LOOKED = {
    find_structure: "Looked up a name in the PDB",
    inspect_structure: "Inspected the structure",
    preview_setup: "Previewed what setup builds",
    check_config: "Checked the config",
    check_selection: "Checked a selection",
    read_study: "Read a study's record",
    list_studies: "Listed the studies here",
    compare_studies: "Compared two studies",
    methods_of_study: "Read a study's methods",
    current_view: "Checked what the page shows"
  };

  function lookLabel(l) {
    return l.label || LOOKED[l.tool] || ("Used " + l.tool);
  }

  /* "Looked up "trp-cage" in the PDB, checked the config": the first as
   * it is, the rest after a comma with a small first letter. */
  function lookedSaid(looks) {
    return looks.map(function (l, i) {
      var said = lookLabel(l) + (l.ok ? "" : " (refused)");
      return i ? said.charAt(0).toLowerCase() + said.slice(1) : said;
    }).join(", ");
  }

  function looked(box, looks) {
    if (!looks || !looks.length) return null;
    var fold = document.createElement("details");
    fold.className = "agent-looks";
    var head = document.createElement("summary");
    head.textContent = lookedSaid(looks);
    fold.appendChild(head);
    looks.forEach(function (l) {
      var item = document.createElement("div");
      item.className = "agent-look" + (l.ok ? "" : " is-refused");
      item.setAttribute("data-tool", l.tool || "");
      var name = document.createElement("div");
      name.className = "agent-look-name";
      name.textContent = lookLabel(l);
      item.appendChild(name);
      if (l.found) {
        var found = document.createElement("div");
        found.className = "agent-look-found";
        found.textContent = l.found;
        item.appendChild(found);
      }
      var whole = document.createElement("details");
      var named = document.createElement("summary");
      named.textContent = "What the software said";
      whole.appendChild(named);
      var said = document.createElement("pre");
      said.className = "agent-look-said";
      said.textContent = l.said || "";
      whole.appendChild(said);
      item.appendChild(whole);
      fold.appendChild(item);
    });
    box.appendChild(fold);
    return fold;
  }

  /* The software's refusals of what the Agent wrote, in view, as one line
   * each; a reply the loop could not read as a config at all
   * (`config.file.unparseable`) is a format repair and stays out of sight.
   * Under an accepted config, that it was written again. */
  var FORMAT_REPAIR = "config.file.unparseable";
  /* A conversation kept before the code was kept with each refusal names
   * its format repairs only by their words (agent/propose.py,
   * agent/conversation.py). */
  var FORMAT_REPAIRS_SAID = [
    "The reply was not a YAML mapping.", "The config was not a mapping.",
    "The question was empty.", "Two actions in one reply.", "A scene without an answer.",
    "The reply was empty.", "No reply was made.",
    "The looks for this reply are used up; answer from what the software said.",
    "Name the windows to run again, by number, as `windows`."
  ];
  /* Said with the name the reply used, so matched by how they begin. */
  var FORMAT_REPAIRS_BEGIN = /^There is no (tool called|action) /;

  function isFormatRepair(refusal) {
    var said = String(refusal.message || "");
    return refusal.code ? refusal.code === FORMAT_REPAIR
      : FORMAT_REPAIRS_SAID.indexOf(said) >= 0 || FORMAT_REPAIRS_BEGIN.test(said);
  }

  function refusals(box, attempts, accepted) {
    var shown = 0;
    (attempts || []).forEach(function (a) {
      var refusal = a && a.refusal;
      if (!refusal || isFormatRepair(refusal)) return;
      var line = document.createElement("div");
      line.className = "agent-refused";
      var lead = document.createElement("b");
      lead.textContent = "The software refused it: ";
      line.appendChild(lead);
      line.appendChild(document.createTextNode(String(refusal.message || "")
        + (accepted ? " I wrote it again." : "")));
      box.appendChild(line);
      shown += 1;
    });
    return shown;
  }

  /* A question the Agent asks, with the candidates it named as buttons:
   * "2VB1 hen egg-white, X-ray 0.65 A, 2007" shows its identifier first,
   * and a press answers with it. Typing answers too. A question answered,
   * or followed by anything else, keeps its candidates, not pressable. */
  var PDB_ID = /^([0-9][A-Za-z0-9]{3})\b[\s,:;.-]*(.*)$/;

  function asking(box, question, choices, spent) {
    var said = document.createElement("div");
    said.className = "agent-answer agent-question";
    said.innerHTML = prose(question);
    box.appendChild(said);
    if (!choices || !choices.length) return said;
    var grid = document.createElement("div");
    grid.className = "agent-choices";
    choices.forEach(function (choice) {
      var text = String(choice || "");
      var id = PDB_ID.exec(text);
      var button = document.createElement("button");
      button.type = "button";
      button.className = "agent-choice";
      var name = document.createElement("span");
      name.className = "agent-choice-name";
      name.textContent = id ? id[1] : text;
      button.appendChild(name);
      if (id && id[2]) {
        var facts = document.createElement("span");
        facts.className = "agent-choice-facts";
        facts.textContent = id[2];
        button.appendChild(facts);
      }
      button.disabled = !!spent;
      button.addEventListener("click", function () {
        if (writing) return;
        Array.prototype.forEach.call(grid.querySelectorAll("button"), function (b) {
          b.disabled = true;
        });
        button.classList.add("is-chosen");
        var area = el("agent-request");
        area.value = id ? id[1] : text;
        draft();
      });
      grid.appendChild(button);
    });
    box.appendChild(grid);
    return said;
  }

  /* A question's candidates are spent once anything else is said. */
  function spendChoices() {
    Array.prototype.forEach.call(
      document.querySelectorAll("#agent-thread .agent-choice, #agent-thread .agent-confirm-btn"),
      function (b) { b.disabled = true; });
    /* Nothing waits for the person once they have said something. */
    Array.prototype.forEach.call(
      document.querySelectorAll('#agent-thread .agent-msg-agent[data-state="waiting"]'),
      function (n) { n.setAttribute("data-state", "done"); });
  }

  /* Under a reply: Useful or Wrong, kept with the conversation for the
   * Agent's evaluation (a second press takes it back), and what the reply
   * took, in tokens. */
  function usageSaid(u) {
    if (!u || !(u.input_tokens || u.cache_read_tokens || u.cache_write_tokens)) return "";
    var sent = (u.input_tokens || 0) + (u.cache_read_tokens || 0) + (u.cache_write_tokens || 0);
    var kept = [];
    if (u.cache_read_tokens) kept.push(u.cache_read_tokens.toLocaleString("en-US") + " from the cache");
    if (u.cache_write_tokens) {
      kept.push(u.cache_write_tokens.toLocaleString("en-US") + " written to the cache");
    }
    return sent.toLocaleString("en-US") + " tokens in" + (kept.length ? " (" + kept.join(", ") + ")" : "")
      + " \u00b7 " + (u.output_tokens || 0).toLocaleString("en-US") + " out";
  }

  function meta(r, entry) {
    var body = r.part("body") || r.node;
    var row = document.createElement("div");
    row.className = "agent-meta";
    ["useful", "wrong"].forEach(function (kind) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "agent-feedback";
      b.setAttribute("data-feedback", kind);
      b.textContent = kind === "useful" ? "Useful" : "Wrong";
      b.setAttribute("aria-pressed", entry.feedback === kind ? "true" : "false");
      b.addEventListener("click", function () {
        entry.feedback = entry.feedback === kind ? null : kind;
        Array.prototype.forEach.call(row.querySelectorAll(".agent-feedback"), function (x) {
          x.setAttribute("aria-pressed", x.getAttribute("data-feedback") === entry.feedback
            ? "true" : "false");
        });
        persist();
      });
      row.appendChild(b);
    });
    var used = usageSaid(entry.usage);
    if (used) {
      var said = document.createElement("span");
      said.className = "agent-usage";
      said.textContent = used;
      row.appendChild(said);
    }
    var tools = body.querySelector(".agent-msg-tools");
    body.insertBefore(row, tools);
    return row;
  }

  /* What the Agent is doing, said by its icon beside the reply. */
  var STATES = { working: "Working", waiting: "Waiting for you", done: "Done",
                 stopped: "Could not finish" };

  function whoIs(r, state) {
    if (!r || !r.node) return;
    r.node.setAttribute("data-state", state);
    var who = r.part("who");
    if (who) who.title = STATES[state] || "";
  }

  /* What the Agent read for this reply, kept beside the conversation
   * (`/api/agent/receipt`), read only when opened: each step, the prompt in
   * full, or its instructions, the tools it could use and the messages it
   * had not been given before (a look's result among them). Said as the
   * Agent says it: the AI model is its own, named only in Settings (user,
   * 10-07: "no talk about the AI model as a separate entity"). */
  function receiptPart(body, label, text) {
    var name = document.createElement("div");
    name.className = "agent-look-name";
    name.textContent = label;
    var said = document.createElement("pre");
    said.className = "agent-look-said";
    said.textContent = text;
    body.appendChild(name);
    body.appendChild(said);
  }

  function receiptMessage(body, message) {
    var who = { user: "You", assistant: "I", results: "The software" };
    var label = who[message.role] || message.role;
    if (message.text) receiptPart(body, label, message.text);
    (message.calls || []).forEach(function (call) {
      receiptPart(body, label + " called " + call.name, JSON.stringify(call.arguments, null, 1));
    });
    (message.results || []).forEach(function (result) {
      receiptPart(body, "What " + (result.name || "the call") + " gave back"
                  + (result.is_error ? " (not taken)" : ""), String(result.content || ""));
    });
  }

  function sent(box, brief) {
    if (!brief || !brief.sha256 || !brief.asked) return null;
    var fold = document.createElement("details");
    fold.className = "agent-looks agent-sent";
    var head = document.createElement("summary");
    head.textContent = "What I read for this reply (" + brief.asked
      + (brief.asked === 1 ? " step" : " steps") + ")"
      + (brief.truncated ? ", long parts shortened" : "");
    fold.appendChild(head);
    var body = document.createElement("div");
    fold.appendChild(body);
    fold.addEventListener("toggle", function () {
      if (!fold.open || fold.dataset.read) return;
      fold.dataset.read = "1";
      body.textContent = "Reading\u2026";
      fetch("/api/agent/receipt?sha256=" + encodeURIComponent(brief.sha256),
            { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .catch(function () { return { ok: false, error: "The server did not answer." }; })
        .then(function (said) {
          body.textContent = "";
          if (!said || !said.ok) {
            body.textContent = (said && said.error) || "Not kept.";
            delete fold.dataset.read;
            return;
          }
          var shownSystem = null;
          var shownTools = null;
          (said.receipt.sent || []).forEach(function (entry, i) {
            var asked = document.createElement("div");
            asked.className = "agent-look-name";
            asked.textContent = "Step " + (i + 1);
            body.appendChild(asked);
            if (entry.prompt != null) {
              receiptPart(body, "What I was given", entry.prompt);
              return;
            }
            if (entry.system !== shownSystem) {
              shownSystem = entry.system;
              var system = document.createElement("details");
              var summary = document.createElement("summary");
              summary.textContent = "My instructions (the same each step)";
              system.appendChild(summary);
              var text = document.createElement("pre");
              text.className = "agent-look-said";
              text.textContent = (said.receipt.systems || [])[entry.system] || "";
              system.appendChild(text);
              body.appendChild(system);
            }
            if (entry.tools !== shownTools) {
              shownTools = entry.tools;
              var declared = (said.receipt.toolsets || [])[entry.tools] || [];
              var tools = document.createElement("details");
              var named = document.createElement("summary");
              named.textContent = "Tools I could use: " + declared.map(function (tool) {
                return tool.name;
              }).join(", ");
              tools.appendChild(named);
              var specs = document.createElement("pre");
              specs.className = "agent-look-said";
              specs.textContent = JSON.stringify(declared, null, 1);
              tools.appendChild(specs);
              body.appendChild(tools);
            }
            (entry.messages || []).forEach(function (message) {
              receiptMessage(body, message);
            });
          });
          var digest = document.createElement("div");
          digest.className = "muted small agent-sent-digest";
          digest.textContent = "SHA-256 " + said.receipt.sha256;
          body.appendChild(digest);
        });
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

  /* Ready or not, and nothing else. The AI model is recorded on the study --
   * `agent_model` in the config and the manifest -- which is where a
   * reader needs it. It does not belong in the chrome. */
  function describeEngine(current) {
    /* Inside the settings dialog, where somebody is choosing. It was on
     * the page itself, as "Ready. Drafting with <model>." -- a status line
     * about an engine, on a page about a study. */
    return current ? current.model : "Nothing set yet.";
  }

  var OTHER = "__other__";

  /* The AI models a provider is known to have, plus a way to name one that is
   * not on the list. A closed list would lock out an AI model released next
   * month; free text alone made somebody type a string exactly right with
   * nothing to check it against, and left the previous provider's AI model
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
    /* Said outright rather than as "Other…". Somebody looking for an AI model
     * the list does not have needs to see that typing one is possible;
     * reported as "I still can't choose any model, only prelisted ones"
     * while this option was sitting in the dropdown saying "Other…". */
    other.textContent = "Type an AI model name\u2026";
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
    /* Only Anthropic is told how long to keep its cache. */
    el("agent-cache-field").hidden = id !== "anthropic";
    /* The AI model follows the provider. It used to be filled only when the
     * field was empty, so switching provider left the previous one's AI model
     * in place -- OpenAI selected and claude-sonnet-4-6 still showing. */
    fillModels(spec, chosen || spec.default_model || "");
    el("agent-key-help").textContent =
      (KEY_HELP[id] || "") + " Stored in a file of its own, readable only " +
      "by you, and never written into a config, a manifest or a log.";
    el("agent-url-examples").textContent =
      (spec.examples || []).map(function (e) {
        return e.label + ": " + e.url;
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
    var records = el("agent-start-records");
    if (records) records.hidden = engineChosen || !studyOpen;
    var note = el("agent-start-note");
    var mode = el("agent-mode");
    if (note && mode) {
      note.textContent = "I draft a config and the software validates it. "
        + (MODE_NOTES[mode.value] || "");
    }
  }

  // As the page's other dialogs (preferences.js): Escape and a click
  // outside close it, focus stays in it and goes back to what opened it.
  function openSettings() {
    if (window.FastMDXDialog) window.FastMDXDialog.open("agent-settings");
    else el("agent-settings").hidden = false;
  }
  function closeSettings() {
    if (window.FastMDXDialog) window.FastMDXDialog.close("agent-settings");
    else el("agent-settings").hidden = true;
  }

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
      el("agent-cache").value = data.cache_for || "auto";
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
      api_key: el("agent-key").value,
      cache_for: el("agent-cache").value
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

  /* The conversation, as the Agent sees it: what was said each way. And
   * the last config it wrote, so "make it 5 ns" is a change to it rather
   * than a study from nothing. */
  var history = [];
  var currentConfig = null;
  /* The transcript, as it can be replayed: every entry carries enough to
   * draw it again without asking the AI model. Saved to the workspace after
   * each exchange, restored when the page opens. The thread used to live
   * only in the browser's memory, and a refresh emptied it. */
  var transcript = [];
  /* Which conversation this is and where it lives, from the last save or
   * restore, so a launch can name it exactly when moving it. */
  var convId = null;
  var convStudy = null;
  /* Files attached to the next message: what read_attachment returned. */
  var pendingFiles = [];
  var fromStarter = null;

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

  /* Saved where it lives: a chat of no study stays one while a study is
   * open, and a study's conversation stays its study's. */
  function persist() {
    var body = convId ? { entries: transcript, id: convId, study: convStudy || null }
      : { entries: transcript };
    return post("/api/agent/conversation", body).then(function (d) {
      if (d && d.ok) {
        convId = d.id || convId;
        if (d.study !== undefined) convStudy = d.study;
        window.dispatchEvent(new CustomEvent("fmx:conversation-saved"));
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
    stopPending = null;
    runPending = null;
    fixPending = null;
    keptVersions();
    /* What the next message changes is the newest version left. Read from
     * the history it missed a config written with a note beside it, and the
     * Agent then edited an older version (review, 10-07). */
    currentConfig = versions.length ? versions[versions.length - 1].data.yaml : null;
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
          /* In place, as every AI app a person has used does it: the
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
    tools(part("body") || node, [
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
    placeholder();
    /* The last message into view, whichever ancestor scrolls. Setting
     * scrollTop on the thread assumed the thread was the scroll
     * container, and when it was not, nothing moved. After a frame, so
     * the reply just appended has a height. */
    var thread = el("agent-thread");
    var column = thread.closest(".main, .agent-drawer-body") || document.scrollingElement;
    function toEnd() {
      /* Only while the conversation is the page shown. The later passes
       * outlived a move to another page: a cited figure opened from an
       * answer just restored was scrolled to, and then the Analysis page
       * was taken to its foot, the figure above the view (CI, 10-02). */
      if (thread.offsetParent === null) return;
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
    /* Sent as typed. It used to be joined to a question the Agent had
     * asked, whatever it said ("which lysozyme...?" and "Has it run long
     * enough?" went as one request); the conversation goes with every
     * message, the question in it, so an answer is read as one. */
    var request = typed;

    var files = pendingFiles.slice();
    pendingFiles = [];
    renderChips();
    var record = files.map(function (f) {
      return { name: f.name, path: f.path, size: f.size, sha256: f.sha256, truncated: !!f.truncated };
    });
    spendChoices();
    say(typed, record);
    var historyText = typed + (record.length ? "\n[attached: " + record.map(function (a) { return a.name; }).join(", ") + "]" : "");
    history.push({ role: "user", text: historyText });
    transcript.push(record.length ? { role: "user", text: typed, attachments: record } : { role: "user", text: typed });
    area.value = "";
    autosize(area);
    var r = reply();
    var box = r.part("attempts");
    if (stopPending || runPending || fixPending) whoIs(r, "done");
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
    scrollToEnd();

    var asked = fromStarter && fromStarter.prompt === typed ? fromStarter.key : null;
    fromStarter = null;
    propose({
      request: request,
      records_question: asked,
      agent: el("agent-mode").value,
      history: history.slice(0, -1),
      current_config: currentConfig,
      attachments: files.map(function (f) { return { name: f.name, text: f.text, truncated: !!f.truncated }; }),
      current_view: window.FastMDXMoleculeViewer?.currentViewHints
        ? window.FastMDXMoleculeViewer.currentViewHints() : null
    }, box).then(function (data) {
      box.innerHTML = "";
      looked(box, data.looks);
      refusals(box, data.attempts, !!data.ok);
      sent(box, data.context_receipt);
      var receipt = data.context_receipt && data.context_receipt.kept
        ? data.context_receipt : null;
      if (data.action) {
        /* An instruction, carried out through the same door the button
         * uses. The thread says what was done, so nothing happens
         * silently. */
        history.push({ role: "agent", text: "DO: " + data.action });
        /* A run the person's own message did not plainly ask for is
         * asked about first. The server says which from what they typed;
         * without its word, ask. */
        var confirmRunFirst = data.action === "run" && data.confirm !== false;
        whoIs(r, confirmRunFirst || data.action === "stop" || data.fix ? "waiting" : "done");
        if (data.action === "run the fix" || data.action === "rerun windows" ||
            data.action === "analyze again" || data.action === "write the report again") {
          /* Asked, not done, and not kept as waiting: a reloaded thread
           * asks the Agent again rather than run a fix it no longer shows. */
          transcript.push({ role: "agent", kind: "question",
                            text: data.fix ? fixQuestion(data.fix)
                                           : data.refused || "Nothing here to run." });
        } else if (confirmRunFirst) {
          transcript.push({ role: "agent", kind: "question", confirm: "run",
                            text: runQuestion(), facts: runFacts() });
        } else if (data.action === "stop") {
          /* Asked, not done. A stop is recorded when it is confirmed, so
           * a reloaded thread never says "Did: stop" about a run that was
           * never stopped -- which it did, and the person's "yes" then
           * went to the AI model as a new message. */
          transcript.push({ role: "agent", kind: "question", confirm: "stop",
                            text: stopQuestion(data.where), facts: STOP_FACTS });
        }
        /* A run is kept once it has started (or said why not), by the
         * launch: kept before, a run refused read back as "Did: run". */
        var done = null;
        if (data.action !== "stop" && data.action !== "run" && !data.fix && !data.refused &&
            ["run the fix", "rerun windows", "analyze again", "write the report again"]
              .indexOf(data.action) < 0) {
          done = { role: "agent", kind: "action", action: data.action, where: data.where || "",
                   receipt: receipt };
          transcript.push(done);
        }
        persist();
        act(data.action, data.where || "", box, r, confirmRunFirst, data.fix || null,
            data.refused || "", done);
        scrollToEnd();
        return;
      }
      if (data.answer) {
        /* A question, answered. No config, no actions. */
        whoIs(r, "done");
        var p = document.createElement("div");
        p.className = "agent-answer";
        p.innerHTML = prose(data.answer);
        box.appendChild(p);
        cite(box, data.cites);
        history.push({ role: "agent", text: data.answer });
        var answered = { role: "agent", kind: "answer", text: data.answer,
                         cites: data.cites || [], looks: data.looks || [], receipt: receipt,
                         usage: data.usage || null };
        if (data.scene) answered.scene = data.scene;
        transcript.push(answered);
        sceneCard(box, data.scene, answered);
        meta(r, answered);
        persist();
        area.focus();
        scrollToEnd();
        return;
      }
      if (data.question) {
        whoIs(r, "waiting");
        asking(box, data.asked || data.question, data.choices || []);
        history.push({ role: "agent", text: data.question });
        var asked = { role: "agent", kind: "question", text: data.question,
                      asked: data.asked || null, choices: data.choices || [],
                      looks: data.looks || [], receipt: receipt, usage: data.usage || null };
        transcript.push(asked);
        meta(r, asked);
        persist();
        area.focus();
        scrollToEnd();
        return;
      }
      if (!data.ok) {
        whoIs(r, "stopped");
        note(box, IN_THE_GUI[data.code] || data.error);
        transcript.push({ role: "agent", kind: "error", text: IN_THE_GUI[data.code] || data.error });
        persist();
        if (IN_THE_GUI[data.code]) openSettings();
        scrollToEnd();
        return;
      }
      /* No "Accepted first time": that the software checked it is said by
       * the card's "passes the checks", and a refusal on the way above. */
      whoIs(r, "done");
      saidBeside(box, data.note);
      history.push({ role: "agent", text: (data.note ? data.note + "\n\n" : "")
                     + "Wrote a config:\n" + data.yaml });
      currentConfig = data.yaml;
      var made = wireActions(r, data, box);
      var wrote = { role: "agent", kind: "config", yaml: data.yaml, config: data.config,
                        usage: data.usage || null,
                        note: data.note || null, version: made.number,
                        changes: data.changes || null,
                        plan: data.plan || [], looks: data.looks || [], receipt: receipt,
                        cycles: data.cycles, attempts: (data.attempts || []).map(function (a) {
                          return a.refusal ? { refusal: { message: a.refusal.message,
                                                          code: a.refusal.code || null } } : {};
                        }) };
      transcript.push(wrote);
      meta(r, wrote);
      persist();
      scrollToEnd();
    }).catch(function (error) {
      box.innerHTML = "";
      whoIs(r, "stopped");
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

  /* What the Agent said beside a config it wrote (why it chose as it did),
   * shown as an answer is. */
  function saidBeside(box, text) {
    if (!text) return;
    var p = document.createElement("div");
    p.className = "agent-answer";
    p.innerHTML = prose(text);
    box.appendChild(p);
  }

  /* ---- The reply as it is written ------------------------------------ */

  /* The server sends the reply as the AI model writes it, one event a line
   * (`/api/agent/propose-stream`): a `begin` each time the AI model is asked,
   * its text in pieces, each look as it is taken, and the answer at the
   * end, the same as `/api/agent/propose` gives. The send button stops it
   * while it is written. A browser without streams asks for it whole. */
  var writing = null;

  function writingState(on) {
    setTimeout(placeholder, 0);
    var button = el("agent-propose");
    button.classList.toggle("is-writing", on);
    button.setAttribute("aria-label", on ? "Stop" : "Send");
    button.title = on ? "Stop the reply being written"
      : "Send (Enter). Shift+Enter for a new line.";
    button.textContent = on ? "\u25a0" : "\u2191";
  }

  /* What the Agent is writing, as a person reads it: a look is said as
   * one, the reply's own marker is left off. */
  function writtenSoFar(raw) {
    if (/^\s*USE:/.test(raw)) return "";
    // A scene proposed on the last line is shown as its card once written.
    return raw.replace(/^\s*(SAY|ASK):\s*/, "").replace(/\n\s*SHOW:[^\n]*$/i, "");
  }

  /* What the Agent is doing while it does it, a line a step: each look as
   * it begins ("Looking up "trp-cage" in the PDB...") and, once taken, as
   * done; writing the config. Under them, its words as it writes them. */
  function stepsIn(box) {
    box.innerHTML = "";
    var steps = document.createElement("div");
    steps.className = "agent-steps";
    steps.setAttribute("role", "status");
    var shown = document.createElement("div");
    shown.className = "agent-writing";
    box.appendChild(steps);
    box.appendChild(shown);
    function finished() {
      var now = steps.querySelector(".agent-step.is-now");
      if (now) now.classList.remove("is-now");
      return now;
    }
    return {
      writing: shown,
      begin: function (label) {
        finished();
        var line = document.createElement("div");
        line.className = "agent-step is-now";
        line.textContent = label;
        steps.appendChild(line);
      },
      done: function (label) {
        var now = finished();
        if (now && label) now.textContent = label;
      },
      finished: finished
    };
  }

  function propose(body, box) {
    var steps = stepsIn(box);
    if (!window.ReadableStream || !window.AbortController || !window.TextDecoder) {
      writingState(true);
      return post("/api/agent/propose", body).finally(function () { writingState(false); });
    }
    writing = new AbortController();
    writingState(true);
    var shown = steps.writing;
    var raw = "";
    var answer = null;
    function handle(line) {
      if (!line.trim()) return;
      var event;
      try { event = JSON.parse(line); } catch (e) { return; }
      if (event.type === "begin") {
        raw = "";
        shown.textContent = "";
      } else if (event.type === "text") {
        raw += event.text || "";
        shown.textContent = writtenSoFar(raw);
        scrollToEnd();
      } else if (event.type === "looking") {
        steps.begin(String(event.label || "Looking") + "\u2026");
        scrollToEnd();
      } else if (event.type === "look" && event.look) {
        steps.done(lookLabel(event.look) + (event.look.ok ? "" : " (refused)"));
      } else if (event.type === "step") {
        steps.begin(String(event.label || "") + "\u2026");
        scrollToEnd();
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
  /* Asked before 10-07 as text to answer with yes; a conversation kept
   * then is still read so. */
  var RUN_QUESTION = "Run the study above? Say yes.";
  var STOP_FACTS = "What it has written so far is kept.";

  function newest() { return versions[versions.length - 1] || null; }

  /* Whether a study is running here, by the server's word (`app-state`). */
  var runGoing = false;

  /* A run ended: its version can be run again, and its line no longer
   * offers Stop. */
  function runEnded() {
    versions.forEach(function (v) {
      var button = v.r.part("run");
      if (button && button.textContent === "Running") {
        button.textContent = "Run again";
        button.disabled = false;
      }
    });
    Array.prototype.forEach.call(document.querySelectorAll("#agent-thread .agent-running"),
      function (line) {
        var stop = line.querySelector(".agent-running-stop");
        if (stop) stop.remove();
        var light = line.querySelector(".recent-light");
        if (light) light.setAttribute("data-state", "not started");
        var said = line.querySelector(".agent-running-said");
        if (said) said.textContent = said.textContent.replace(/^Running version/, "Ran version");
      });
  }

  /* A run asked about: which version, and what it is, from its plan. */
  function runQuestion() {
    var v = newest();
    return v ? "Run version " + v.number + " on this machine?" : "Run the study on this machine?";
  }

  function runFacts() {
    var v = newest();
    if (!v) return "";
    var said = [];
    Array.prototype.forEach.call(v.r.part("plan").querySelectorAll("dt"), function (term) {
      var label = term.textContent;
      if (["System", "Conditions", "Production", "Size", "Time here"].indexOf(label) < 0) return;
      var value = term.nextElementSibling;
      var shown = value && value.querySelector(".agent-plan-value");
      said.push(label === "Time here" ? "time here: " + (shown || value).textContent
                                      : (shown || value).textContent);
    });
    return said.join(" \u00b7 ");
  }

  function stopQuestion(where) {
    return "Stop the run" + (where ? " at " + where : "") + "?";
  }

  /* A question that waits for the person, with its two answers as
   * buttons. A press says the answer in the thread, as typing it does
   * (typing "yes" or "no" still works). Spent once anything is said. */
  function confirmCard(box, question, facts, yes, no, spent) {
    var card = document.createElement("div");
    card.className = "agent-confirm";
    var asked = document.createElement("div");
    asked.className = "agent-confirm-q";
    asked.textContent = question;
    card.appendChild(asked);
    if (facts) {
      var said = document.createElement("div");
      said.className = "agent-confirm-facts";
      said.textContent = facts;
      card.appendChild(said);
    }
    var row = document.createElement("div");
    row.className = "agent-confirm-row";
    [[yes, "yes", "primary-btn"], [no, "no", "ctl-btn"]].forEach(function (b) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = b[2] + " agent-confirm-btn";
      button.textContent = b[0];
      button.setAttribute("data-answer", b[1]);
      button.disabled = !!spent;
      button.addEventListener("click", function () {
        if (writing) return;
        button.classList.add("is-chosen");
        el("agent-request").value = b[1];
        draft();
      });
      row.appendChild(button);
    });
    card.appendChild(row);
    box.appendChild(card);
    return card;
  }

  /* The study running, said under the message that started it: which
   * version, since when, a way to watch it and to stop it. Read again from
   * a kept conversation, it says when it started and nothing more. */
  function runLine(box, number, started, live) {
    var line = document.createElement("div");
    line.className = "agent-running";
    /* The study's own light, as Recent shows it. */
    var light = document.createElement("span");
    light.className = "recent-light";
    light.setAttribute("data-state", live ? "running" : "not started");
    light.setAttribute("aria-hidden", "true");
    var said = document.createElement("span");
    said.className = "agent-running-said";
    var when = started ? new Date(started) : null;
    var at = when && !isNaN(when.getTime())
      ? when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    said.textContent = (live ? "Running version " : "Started version ") + (number || "")
      + (at ? (live ? ", started " : " at ") + at : "");
    line.append(light, said);
    var spacer = document.createElement("span");
    spacer.className = "agent-spacer";
    line.appendChild(spacer);
    var watch = document.createElement("button");
    watch.type = "button";
    watch.className = "ctl-btn";
    watch.textContent = "Watch on the Overview";
    watch.addEventListener("click", function () {
      if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
        window.FastMDXDashboard.navigate("overview");
      } else {
        window.location.hash = "#overview";
      }
    });
    line.appendChild(watch);
    if (live) {
      var stop = document.createElement("button");
      stop.type = "button";
      stop.className = "ctl-btn agent-running-stop";
      stop.textContent = "Stop";
      stop.addEventListener("click", function () {
        if (writing) return;
        /* Asked first, as every stop is; nothing else still waits. */
        runPending = null; fixPending = null;
        var asked = "Stop the run";
        spendChoices();
        say(asked, []);
        history.push({ role: "user", text: asked });
        transcript.push({ role: "user", text: asked });
        var r = reply();
        whoIs(r, "waiting");
        transcript.push({ role: "agent", kind: "question", confirm: "stop",
                          text: stopQuestion(""), facts: STOP_FACTS });
        persist();
        act("stop", "", r.part("attempts"), r);
        scrollToEnd();
      });
      line.appendChild(stop);
    }
    box.appendChild(line);
    return line;
  }

  /* Yes, or the verb being confirmed: "stop it" confirms a stop and not
   * a run, and "run it" a run and not a stop. */
  function saidYes(typed, verb) {
    return new RegExp("^\\s*(yes|y|yes please|do it|" + verb + " it|confirm)\\s*\\.?\\s*$", "i")
      .test(typed);
  }

  function act(action, where, box, r, confirmFirst, fix, refused, entry) {
    if (action === "analyze again" || action === "write the report again") {
      /* The study's analyses or report run again in its folder, checked
       * by the server against the study; asked, or said why not. */
      if (!fix) {
        note(box, refused || "This study cannot be run again here.");
        return;
      }
      fixPending = fix;
      fixCard(box, fix);
      return;
    }
    if (action === "rerun windows") {
      /* The windows and values the person named, checked by the server
       * against the study; asked with the price, or said why not. */
      if (!fix) {
        note(box, refused || "Those windows cannot be run again here.");
        return;
      }
      fixPending = fix;
      fixCard(box, fix);
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
      fixCard(box, fix);
      return;
    }
    if (action === "run") {
      if (!lastReply) {
        note(box, "Nothing to run yet. Describe a study first.");
        return;
      }
      var runBtn = lastReply.part("run");
      if (runGoing || runBtn.disabled) {
        /* A study running here, by the server's word (`app-state`), or this
         * one just pressed. The button alone went stale both ways: still
         * "Running" after the run ended, and pressable again after a
         * reload while it ran (review, 10-07). */
        note(box, "A study is already running here.");
        transcript.push({ role: "agent", kind: "answer", text: "A study is already running here." });
        persist();
        return;
      }
      if (confirmFirst) {
        /* The reply came from a model, which reads attached files and can
         * be wrong or be told what to say. Starting work on this machine
         * waits for the person, as a stop does. */
        runPending = true;
        confirmCard(box, runQuestion(), runFacts(), "Run it", "Not now");
        return;
      }
      /* Started here, and said here, under the message that asked: it was
       * said in the config's card, two messages up. */
      newest().launch(box, entry);
      return;
    }
    if (action === "stop") {
      /* Irreversible, so it is confirmed in the thread. The next message
       * that says yes stops it; anything else is taken as no. */
      stopPending = true;
      confirmCard(box, stopQuestion(where), STOP_FACTS, "Stop it", "Keep running");
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

  /* When a run started here ends, what it found, from the study's records
   * (`/api/agent/run-summary`), so it costs no tokens: said once, under the
   * conversation, and kept with it. */
  var SUMMARY_ROWS = [["found", "What it found"], ["long_enough", "Long enough?"],
                      ["strengthen", "To strengthen it"]];

  function summaryCard(box, s) {
    var card = document.createElement("div");
    card.className = "agent-summary";
    var head = document.createElement("div");
    head.className = "agent-summary-head";
    var said = document.createElement("b");
    said.textContent = s.head || "The run ended";
    var from = document.createElement("span");
    from.className = "agent-badge";
    from.textContent = "from the study\u2019s records";
    var spacer = document.createElement("span");
    spacer.className = "agent-spacer";
    head.append(said, spacer, from);
    card.appendChild(head);
    var rows = document.createElement("dl");
    rows.className = "agent-summary-rows";
    SUMMARY_ROWS.forEach(function (row) {
      if (!s[row[0]]) return;
      var term = document.createElement("dt");
      term.textContent = row[1];
      var value = document.createElement("dd");
      value.className = "agent-answer";
      value.innerHTML = prose(s[row[0]]);
      rows.append(term, value);
    });
    card.appendChild(rows);
    var foot = document.createElement("div");
    foot.className = "agent-summary-foot";
    [["Open the Overview", "overview"], ["Open the report", "report"]].forEach(function (b) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "ctl-btn";
      button.textContent = b[0];
      button.addEventListener("click", function () {
        if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
          window.FastMDXDashboard.navigate(b[1]);
        } else {
          window.location.hash = "#" + b[1];
        }
      });
      foot.appendChild(button);
    });
    card.appendChild(foot);
    box.appendChild(card);
    return card;
  }

  /* A run this conversation started whose summary is not said yet. */
  function runAwaitingSummary() {
    for (var i = transcript.length - 1; i >= 0; i--) {
      var e = transcript[i];
      if (e.kind === "summary") return null;
      if (e.kind === "action" && e.action === "run" && e.output) {
        return e.no_summary ? null : e;
      }
    }
    return null;
  }

  var summaryAsked = false;
  /* A run that did not finish; one whose record says nothing is not that. */
  var STOPPED_AS = /^(failed|stopped|interrupted)$/;
  function sayHowTheRunEnded() {
    var run = runAwaitingSummary();
    if (!run || summaryAsked) return;
    summaryAsked = true;
    post("/api/agent/run-summary", { study: run.output }).then(function (s) {
      summaryAsked = false;
      if (s && !s.ok) {
        // No study there any more: not asked again.
        run.no_summary = true;
        persist();
        return;
      }
      if (!s || !s.ended || runAwaitingSummary() !== run) return;
      var r = reply();
      whoIs(r, STOPPED_AS.test(s.status || "") ? "stopped" : "done");
      summaryCard(r.part("attempts"), s);
      var entry = { role: "agent", kind: "summary", study: run.output, head: s.head,
                    status: s.status, found: s.found, long_enough: s.long_enough,
                    strengthen: s.strengthen };
      transcript.push(entry);
      history.push({ role: "agent", text: s.head + ".\n" + [s.found, s.long_enough,
                     s.strengthen].filter(Boolean).join("\n") });
      persist();
      placeholder();
      scrollToEnd();
    }).catch(function () { summaryAsked = false; });
  }

  /* The message box's words follow the situation (user, 10-07: "This
   * default should be adaptive"). */
  var studyState = "none";
  function placeholder() {
    var area = el("agent-request");
    if (!area) return;
    var thread = el("agent-thread");
    var nodes = thread ? thread.querySelectorAll(".agent-msg") : [];
    var last = nodes.length ? nodes[nodes.length - 1] : null;
    var words;
    if (writing) {
      words = "I am working on it; you can write your next message";
    } else if (stopPending || runPending || fixPending) {
      words = "Answer above, or type yes or no";
    } else if (last && last.querySelector(".agent-choice:not(:disabled)")) {
      words = "Pick one above, or type your answer";
    } else if (last && last.querySelector(".agent-question")) {
      words = "Type your answer";
    } else if (last && last.querySelector(".agent-study:not([hidden]):not(.is-replaced)")) {
      words = "Change something, or say run it";
    } else if (studyState === "running") {
      words = "Ask how it is going, or tell me to stop it";
    } else if (studyState === "stopped") {
      words = "Ask why it stopped, or what would fix it";
    } else if (studyState === "finished") {
      words = "Ask about this study, or describe the next one";
    } else {
      words = "Describe a study, or ask about one in this folder";
    }
    area.placeholder = words;
  }

  /* A fix asked about with its price, as the two buttons. */
  function fixCard(box, fix) {
    var what = fix.request ? String(fix.fix || "").replace(/\.$/, "") : "Run " + fix.command;
    confirmCard(box, what + "?", fix.price_said ? "It costs " + fix.price_said + "." : "",
                "Run it", "Not now");
  }

  function fixQuestion(fix) {
    /* Windows named by the person are said as what will run, with the
     * values; a fix from the record as its command. */
    var what = fix.request ? String(fix.fix || "").replace(/\.$/, "") : "Run " + fix.command;
    return what + "?" +
      (fix.price_said ? " It costs " + fix.price_said + "." : "");
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
    fetch(fix.route || "/api/fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fix.request || { index: fix.index })
    }).then(function (res) { return res.json(); }).then(function (d) {
      var started = d && d.ok;
      var said = started ? "Started: " + fix.command + "." : (d && d.error) || "Could not start it.";
      note(box, said, started);
      history.push({ role: "agent", text: said });
      transcript.push(started ? { role: "agent", kind: "action",
                                  action: fix.action || (fix.request ? "rerun windows" : "run the fix"),
                                  where: "" }
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
    history.push({ role: "agent", text: "DO: run" });
    persist();
    act("run", "", box, null, false, null, null, null);
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

  /* Where a value came from (`gui/plan.py` `sourced`, from the config's
   * `decisions`): the person's request, their fastmdx-defaults.yml, the
   * Agent's choice with its reason, or FastMDXplora's default. A value set
   * with no decision recorded says nothing rather than a guess. */
  var SOURCES = {
    asked: ["you asked", "is-asked", "Your message said it."],
    yours: ["your default", "is-yours", "From fastmdx-defaults.yml, your defaults."],
    agent: ["Agent\u2019s choice", "is-agent", "I chose it; why is under it."],
    "default": ["default", "is-default",
                "Not set in the config: the value the run takes when nothing is said."]
  };

  function showPlan(host, plan) {
    if (!host) return;
    host.innerHTML = "";
    var lines = Array.isArray(plan) ? plan : [];
    lines.forEach(function (line) {
      var term = document.createElement("dt");
      term.textContent = line.label;
      var value = document.createElement("dd");
      var said = document.createElement("span");
      said.className = "agent-plan-value";
      said.textContent = line.value;
      value.appendChild(said);
      var source = SOURCES[line.source || (line["default"] ? "default" : "")];
      if (source) {
        var tag = document.createElement("span");
        tag.className = "agent-source " + source[1];
        tag.textContent = source[0];
        tag.title = source[2];
        value.appendChild(document.createTextNode(" "));
        value.appendChild(tag);
      }
      if (line.why) {
        var why = document.createElement("div");
        why.className = "agent-plan-why";
        why.textContent = line.why;
        value.appendChild(why);
      }
      host.appendChild(term);
      host.appendChild(value);
    });
    host.hidden = !lines.length;
  }

  /* What changed from the version before (`gui/plan.py`
   * `changes_between`): "Temperature 310 K -> 330 K", and that the rest
   * is as it was. */
  function showChanges(r, changes, before) {
    var host = r.part("change");
    var count = r.part("count");
    if (!host || !Array.isArray(changes) || !before) return;
    host.innerHTML = "";
    changes.forEach(function (c) {
      var row = document.createElement("div");
      row.className = "agent-change-row";
      row.appendChild(document.createTextNode(c.label + " "));
      var was = document.createElement("span");
      was.className = "was";
      was.textContent = c.before;
      var arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.textContent = "\u2192";
      var now = document.createElement("span");
      now.className = "now";
      now.textContent = c.after;
      row.append(was, arrow, now);
      host.appendChild(row);
    });
    var rest = document.createElement("div");
    rest.className = "agent-change-rest";
    rest.textContent = changes.length ? "Everything else as version " + before + "."
      : "The same study as version " + before + ".";
    host.appendChild(rest);
    host.hidden = false;
    count.textContent = changes.length === 1 ? "1 change" : changes.length + " changes";
    count.hidden = !changes.length;
  }

  /* The versions of the study written in this conversation, oldest first;
   * the newest is the one Run, "run it" and the next edit act on. */
  var versions = [];

  /* An older version folded to its head: no Run, Show to read it, and Use
   * this version to make it the newest again. */
  function fold(v, by) {
    var r = v.r;
    var study = r.part("study");
    study.classList.add("is-replaced");
    study.classList.remove("is-unfolded");
    r.part("checks").hidden = true;
    var replaced = r.part("replaced");
    replaced.textContent = "replaced by version " + by;
    replaced.hidden = false;
    r.part("count").hidden = true;
    r.part("change").hidden = true;
    r.part("whole").hidden = true;
    r.part("actions").hidden = true;
    var unfold = r.part("unfold");
    unfold.hidden = false;
    unfold.textContent = "Show";
    r.part("use").hidden = false;
  }

  /* The newest version as it was written: whole, with its actions. */
  function unfolded(v) {
    var r = v.r;
    var study = r.part("study");
    study.classList.remove("is-replaced", "is-unfolded");
    r.part("checks").hidden = false;
    r.part("replaced").hidden = true;
    r.part("unfold").hidden = true;
    r.part("use").hidden = true;
    r.part("whole").hidden = false;
    r.part("actions").hidden = false;
    if (v.changes) showChanges(r, v.changes, v.number - 1);
  }

  function newVersion(r, data, number) {
    var v = { r: r, data: data, number: number || versions.length + 1,
              changes: data.changes || null };
    var previous = versions[versions.length - 1];
    if (previous) fold(previous, v.number);
    versions.push(v);
    r.part("version").textContent = "Study, version " + v.number;
    r.part("study").hidden = false;
    if (v.changes && previous) showChanges(r, v.changes, previous.number);
    r.part("unfold").onclick = function () {
      var study = r.part("study");
      var opening = !study.classList.contains("is-unfolded");
      study.classList.toggle("is-unfolded", opening);
      r.part("whole").hidden = !opening;
      r.part("unfold").textContent = opening ? "Hide" : "Show";
    };
    r.part("use").onclick = function () { useVersion(v); };
    return v;
  }

  /* An older version made the newest again: written into the conversation
   * as the person's choice and as a new version of the same config, so the
   * next "make it 5 ns" changes it, and Run runs it. Nothing is asked of
   * the Agent's AI model. */
  function useVersion(v) {
    if (writing) return;
    /* A new choice, so nothing still waiting for a yes takes it. */
    stopPending = null; runPending = null; fixPending = null;
    spendChoices();
    var asked = "Use version " + v.number;
    say(asked, []);
    history.push({ role: "user", text: asked });
    transcript.push({ role: "user", text: asked });
    var r = reply();
    whoIs(r, "done");
    var again = { yaml: v.data.yaml, config: v.data.config, plan: v.data.plan,
                  note: "Version " + v.number + ", again.", changes: null };
    saidBeside(r.part("attempts"), again.note);
    history.push({ role: "agent", text: again.note + "\n\nWrote a config:\n" + again.yaml });
    currentConfig = again.yaml;
    var made = wireActions(r, again, r.part("attempts"));
    transcript.push({ role: "agent", kind: "config", yaml: again.yaml, config: again.config,
                      note: again.note, plan: again.plan || [], cycles: 1, attempts: [],
                      version: made.number, again: v.number });
    persist();
    scrollToEnd();
  }

  /* After an edit or a retry cut the thread, the newest version left is the
   * one to run: unfolded, with its actions. */
  function keptVersions() {
    var thread = el("agent-thread");
    versions = versions.filter(function (v) { return thread.contains(v.r.node); });
    var newest = versions[versions.length - 1];
    if (newest) unfolded(newest);
    lastReply = newest ? newest.r : null;
  }

  function wireActions(r, data, box, number) {
    lastReply = r;
    var made = newVersion(r, data, number);
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
    /* The reply's config as the builder reads it, without putting it in
     * the builder: the file, the command, the script and the cost come
     * from the builder's own derivation on this config, and somebody's
     * draft in the builder is left as it is until they open this one. */
    var loadedState = null;
    var loaded = post("/api/load-config", { config: data.config }).then(function (m) {
      if (!m || !m.ok) {
        noteEl.textContent = (m && m.error) || "Could not prepare the config for the builder.";
        return false;
      }
      var run = window.FastMDXRun;
      if (!run || !run.forLoaded) return false;
      loadedState = m.state;
      return true;
    });
    function onItsConfig() {
      return window.FastMDXRun.forLoaded(loadedState, {
        full: function () { return fullBox.checked; },
        say: function (words) { noteEl.textContent = words || ""; }
      });
    }

    /* What the proposal would build and how long it would take here, added
     * to its plan: the cost read before the run, not learned from setup's
     * log. A moment after the reply, because a structure named by its
     * identifier is fetched to be inspected. */
    loaded.then(function (ok) {
      if (!ok) return;
      var wants = (data.config && data.config.include_phase) || null;
      if (wants && wants.indexOf("setup") < 0) return;
      onItsConfig().previewCost().then(function (cost) {
        if (!cost) return;
        addPlanLines(r.part("plan"), [
          { label: "Size", value: cost.size },
          { label: "Time here", value: cost.time },
        ]);
      });
    });

    function viaBuilder(action) {
      return function () {
        loaded.then(function (ok) {
          if (!ok) return;
          onItsConfig()[action]();
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
        onItsConfig().fetchConfig().then(function (built) {
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
      loaded.then(function (ok) {
        if (!ok) return;
        Promise.resolve(window.FastMDXRun.applyLoadedState(loadedState, {
          note: "Opened the Agent's study here. Changes are saved as a new config."
        })).then(function () { window.location.hash = "#run"; });
      });
    };
    /* Started from the card's button, or from a message ("run it", a "yes"):
     * said in `here`, the reply to that message, or the card's own. The
     * conversation records it with the version and when it started. */
    made.launch = function (here, entry) {
      here = here || box;
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
          var at = new Date().toISOString();
          runLine(here, made.number, at, true);
          if (!entry) {
            entry = { role: "agent", kind: "action", action: "run", where: "" };
            transcript.push(entry);
            history.push({ role: "agent", text: "Started version " + made.number + "." });
          }
          entry.version = made.number;
          entry.started = at;
          entry.output = started.output || null;
          persist();
          /* The conversation that launched a study belongs with it. Without
           * this the thread would vanish from view the moment the page
           * switched to the new study's empty list. */
          if (started.output) {
            post("/api/agent/conversation/attach", {
              study: started.output, id: convId, from_study: fromStudy
            }).then(function (m) {
              if (m && m.ok && m.id) { convId = m.id; convStudy = m.study || started.output; }
              if (m && m.moved) note(here, "This conversation now belongs to the new study.", true);
            }).catch(function () {});
          }
        } else {
          runBtn.disabled = false;
          noteEl.textContent = started.error;
          if (here !== box) note(here, started.error || "It did not start.");
          transcript.push({ role: "agent", kind: "error", text: started.error || "It did not start." });
          persist();
          runFix(r.part("fix"), started, data);
        }
      });
    };
    runBtn.onclick = function () {
      /* A press is the person's own word: nothing still waiting for a yes
       * takes the next message. */
      stopPending = null; runPending = null; fixPending = null;
      spendChoices();
      made.launch(box, null);
    };
    /* More: a menu, put away by a choice in it, a click elsewhere or Escape. */
    var more = r.part("more");
    if (more) {
      more.addEventListener("click", function (e) {
        if (e.target.closest(".agent-more-menu button")) more.open = false;
      });
      more.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && more.open) {
          more.open = false;
          more.querySelector("summary").focus();
        }
      });
    }
    return made;
  }

  /* A refused run with what would fix it (remedies.py): the fix, and the
   * command that does it where there is one. Nothing has run, so there is
   * no price. Where the fix is a setting of the
   * study, the Agent is offered it: pressing the button sends the refusal
   * into the thread as the person's next message, and the Agent rewrites
   * the config it wrote. A choice only the person can make, a budget, an
   * install and a machine's state are said and not handed on: the
   * software does not choose them, so neither does the AI model. Built as
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
      stopPending = null; runPending = null; fixPending = null;
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
   * config, so Run on this machine on a restored thread runs what was written. */
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
        whoIs(r, e.kind === "error" ? "stopped" : "done");
        looked(box, e.looks);
        sent(box, e.receipt);
        if (e.kind === "config") {
          refusals(box, e.attempts, true);
          saidBeside(box, e.note);
          history.push({ role: "agent", text: (e.note ? e.note + "\n\n" : "")
                         + "Wrote a config:\n" + e.yaml });
          currentConfig = e.yaml;
          wireActions(r, { yaml: e.yaml, config: e.config, cycles: e.cycles,
                           plan: e.plan || [], changes: e.changes || null }, box, e.version);
          if (e.again == null) meta(r, e);
        } else if (e.kind === "answer") {
          var p = document.createElement("div");
          p.className = "agent-answer";
          p.innerHTML = prose(e.text);
          box.appendChild(p);
          cite(box, e.cites);
          sceneCard(box, e.scene, e);
          if (e.cites !== undefined || e.usage) meta(r, e);
          history.push({ role: "agent", text: e.text });
        } else if (e.kind === "question") {
          // Waiting for you only while it is the last thing said; its
          // candidates pressable only then.
          var last = e === entries[entries.length - 1];
          if (last) whoIs(r, "waiting");
          if (e.asked) { asking(box, e.asked, e.choices || [], !last); meta(r, e); }
          else if (e.confirm === "run") confirmCard(box, e.text, e.facts, "Run it", "Not now", !last);
          else if (e.confirm === "stop") {
            confirmCard(box, e.text, e.facts, "Stop it", "Keep running", !last);
          } else note(box, e.text);
          history.push({ role: "agent", text: e.text });
          // A stop or run confirmation that was the last thing said is still
          // waiting for its yes after a reload.
          stopPending = e.confirm === "stop" || /^Stop the run.*\? Say yes\.$/.test(e.text || "")
            ? true : null;
          runPending = e.confirm === "run" || e.text === RUN_QUESTION ? true : null;
        } else if (e.kind === "summary") {
          whoIs(r, STOPPED_AS.test(e.status || "") ? "stopped" : "done");
          summaryCard(box, e);
          history.push({ role: "agent", text: (e.head || "") + ".\n" + [e.found, e.long_enough,
                         e.strengthen].filter(Boolean).join("\n") });
        } else if (e.kind === "action") {
          if (e.action === "run" && e.started) runLine(box, e.version, e.started, false);
          else note(box, e.action === "stop" ? "Stopped the run." : "Did: " + e.action + ".", true);
          history.push({ role: "agent", text: "DO: " + e.action });
        } else {
          note(box, e.text || "");
        }
        transcript.push(e);
      });
      // Only a stop question that is the final entry keeps its pending
      // state; anything said after it answered or superseded it.
      var last = entries[entries.length - 1];
      if (!(last && last.kind === "question" && (last.confirm === "stop" ||
            /^Stop the run.*\? Say yes\.$/.test(last.text || "")))) {
        stopPending = null;
      }
      if (!(last && last.kind === "question" && (last.confirm === "run" ||
            last.text === RUN_QUESTION))) {
        runPending = null;
      }
      // A run started here that ended while the page was closed.
      sayHowTheRunEnded();
      scrollToEnd();
  }

  document.addEventListener("DOMContentLoaded", function () {
    restore();
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("app-state", function (s) {
        var going = !!(s && (s.process_running || s.status === "running" || s.running_elsewhere));
        if (runGoing && !going) runEnded();
        runGoing = going;
      });
    }
    /* A study's More menu is put away by a click anywhere else. */
    document.addEventListener("click", function (e) {
      Array.prototype.forEach.call(document.querySelectorAll(".agent-more[open]"), function (m) {
        if (!m.contains(e.target)) m.open = false;
      });
    });
    /* Start fresh: the thread on screen is already saved and stays in
     * the list. Nothing is lost, so nothing is confirmed. */
    function resetThread() {
      el("agent-thread").innerHTML = "";
      history = []; transcript = []; currentConfig = null;
      stopPending = null; runPending = null; fixPending = null; lastReply = null;
      versions = [];
      placeholder();
    }
    /* A fresh thread: where the GUI is, or, given `study` (null for a
     * chat of no study), there. */
    function freshConversation(study) {
      var body = study === undefined ? {} : { study: study };
      return post("/api/agent/conversation/new", body).then(function (d) {
        if (d && d.ok) {
          convId = d.id || null;
          convStudy = d.study === undefined ? convStudy : d.study;
        }
        resetThread();
        hideList();
        told();
        return d;
      });
    }
    var fresh = el("agent-new");
    if (fresh) {
      fresh.addEventListener("click", function () {
        freshConversation().then(function () { el("agent-request").focus(); })
          .catch(function () {});
      });
    }

    /* One conversation opened, from the list or the sidebar. Another
     * study's loads that study: the page reloads so every panel reads it,
     * and the conversation is current there on return. */
    function openConversation(id, study, loaded) {
      return post("/api/agent/conversation/open", { id: id, study: study }).then(function (o) {
        if (!o || !o.ok) { window.alert((o && o.error) || "Could not open it."); return o; }
        if (o.loaded_study && !loaded) {
          location.reload();
          return o;
        }
        convId = o.id || null; convStudy = o.study || null;
        resetThread();
        hideList();
        replay(o.entries || []);
        told();
        return o;
      });
    }

    /* The list of conversations: this study's, the chats of no study, and
     * each other study's folded under its ID; found by typing, the one
     * talked in last first. A title opens it; the pencil names it; the bin
     * deletes it, and only it, after asking. */
    var list = el("agent-conv-list");
    function hideList() { if (list) list.hidden = true; }
    var LINE = '<svg class="line-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" '
      + 'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
      + 'aria-hidden="true">';
    var PENCIL = LINE + '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>';
    var BIN = LINE + '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/></svg>';
    var SEARCH = LINE + '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>';

    function node(tag, cls, text) {
      var made = document.createElement(tag);
      if (cls) made.className = cls;
      if (text != null) made.textContent = text;
      return made;
    }

    function ago(when) {
      var t = new Date(when).getTime();
      if (isNaN(t)) return "";
      var minutes = Math.round((Date.now() - t) / 60000);
      if (minutes < 1) return "just now";
      if (minutes < 60) return minutes + " min ago";
      if (minutes < 24 * 60) return Math.round(minutes / 60) + " h ago";
      return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }

    function row(c, g) {
      var here = c.id === convId && (c.study || null) === (convStudy || null);
      var line = node("div", "agent-conv-row" + (here ? " current" : ""));
      line.dataset.id = c.id;
      var title = node("button", "title", c.title);
      title.type = "button";
      title.title = c.entries + (c.entries === 1 ? " message" : " messages")
        + (g.loaded || g.chats ? "" : " · opens this study");
      if (here) title.setAttribute("aria-current", "true");
      title.addEventListener("click", function () {
        openConversation(c.id, g.study, g.loaded || g.chats);
      });
      var meta = node("span", "when", ago(c.updated) || c.started);
      var name = node("button", "line-btn conv-rename");
      name.type = "button";
      name.title = "Name it";
      name.setAttribute("aria-label", "Name “" + c.title + "”");
      name.innerHTML = PENCIL;
      name.addEventListener("click", function () { renaming(line, title, c, g); });
      var del = node("button", "line-btn conv-delete");
      del.type = "button";
      del.title = "Delete it";
      del.setAttribute("aria-label", "Delete “" + c.title + "”");
      del.innerHTML = BIN;
      del.addEventListener("click", function () {
        if (!window.confirm("Delete “" + c.title + "”? This cannot be undone.")) return;
        post("/api/agent/conversation/delete", { id: c.id, study: g.study }).then(function () {
          if (here) { convId = null; resetThread(); }
          told();
          showList();
        });
      });
      line.append(title, meta, name, del);
      return line;
    }

    /* Named in place: Enter keeps it, Escape leaves it as it was; empty,
     * it is its first question again. */
    function renaming(line, title, c, g) {
      var input = node("input", "conv-name");
      input.value = c.named ? c.title : "";
      input.placeholder = c.title;
      input.maxLength = 80;
      input.setAttribute("aria-label", "A name for this conversation");
      title.replaceWith(input);
      input.focus();
      var done = false;
      function finish(keep) {
        if (done) return;
        done = true;
        if (!keep) { showList(); return; }
        post("/api/agent/conversation/rename", { id: c.id, study: g.study, title: input.value })
          .then(function (d) {
            if (!d || !d.ok) window.alert((d && d.error) || "Could not name it.");
            told();
            showList();
          });
      }
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); finish(true); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
      });
      input.addEventListener("blur", function () { finish(true); });
    }

    function showList() {
      return fetch("/api/agent/conversations").then(function (r) { return r.json(); }).then(function (d) {
        var groups = (d && d.groups) || [];
        var wanted = list.querySelector(".conv-search input");
        var typed = wanted ? wanted.value : "";
        list.replaceChildren();
        var search = node("label", "search-field conv-search");
        search.innerHTML = SEARCH.replace('class="line-icon"', 'class="search-icon"');
        var box = node("input");
        box.type = "search";
        box.placeholder = "Find a conversation";
        box.setAttribute("aria-label", "Find a conversation");
        box.value = typed;
        search.appendChild(box);
        list.appendChild(search);
        var body = node("div", "conv-groups");
        list.appendChild(body);

        function fill() {
          var words = box.value.toLowerCase().split(/\s+/).filter(Boolean);
          var hits = function (c, g) {
            var text = (c.title + " " + (g.label || "") + " " + (g.folder || "")).toLowerCase();
            return words.every(function (w) { return text.indexOf(w) >= 0; });
          };
          body.replaceChildren();
          var shown = 0;
          var others = groups.filter(function (g) { return !g.loaded && !g.chats; });
          groups.filter(function (g) { return g.loaded || g.chats; })
            .sort(function (a, b) { return a.loaded === b.loaded ? 0 : a.loaded ? -1 : 1; })
            .forEach(function (g) {
              var rows = g.conversations.filter(function (c) { return hits(c, g); });
              var head = node("div", "agent-conv-group" + (g.loaded ? " loaded" : ""));
              head.textContent = g.chats ? "Chats · no study"
                : "This study · " + g.label;
              if (!g.chats && g.folder && g.folder !== g.label) head.title = g.folder;
              body.appendChild(head);
              if (!rows.length) {
                body.appendChild(node("div", "agent-conv-empty",
                  words.length ? "None by that name." : g.chats ? "No chats yet."
                    : "No conversations about this study yet."));
              }
              rows.forEach(function (c) { body.appendChild(row(c, g)); shown += 1; });
            });
          if (others.length) {
            body.appendChild(node("div", "agent-conv-group", "Other studies"));
            others.forEach(function (g) {
              var rows = g.conversations.filter(function (c) { return hits(c, g); });
              if (!rows.length) return;
              var fold = node("details", "agent-conv-study");
              // Folded, unless what is typed found something in it.
              fold.open = words.length > 0;
              var head = node("summary", "", "");
              head.appendChild(node("span", "conv-study-id", g.label));
              head.appendChild(node("span", "conv-study-folder", g.folder));
              head.appendChild(node("span", "conv-study-count", String(rows.length)));
              fold.appendChild(head);
              rows.forEach(function (c) { fold.appendChild(row(c, g)); shown += 1; });
              body.appendChild(fold);
            });
          }
          return shown;
        }
        fill();
        box.addEventListener("input", fill);
        list.hidden = false;
        if (typed) box.focus();
      }).catch(function () {});
    }
    /* The phone bar's conversations icon: the Agent page, its list open. */
    var phoneConvs = el("phone-convs");
    if (phoneConvs && list) {
      phoneConvs.addEventListener("click", function () {
        if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
          window.FastMDXDashboard.navigate("agent");
        } else {
          window.location.hash = "#agent";
        }
        showList().then(function () {
          var head = list.querySelector("input");
          if (head) head.focus();
        });
      });
    }
    var convs = el("agent-conversations");
    if (convs && list) {
      convs.addEventListener("click", function () {
        if (list.hidden) showList(); else hideList();
      });
      list.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !list.hidden && e.target.className !== "conv-name") {
          hideList();
          convs.focus();
        }
      });
    }

    /* The sidebar's lists are told when a conversation is begun, opened,
     * written, named or deleted (chats.js). */
    function told() {
      window.dispatchEvent(new CustomEvent("fmx:conversations", {
        detail: { id: convId, study: convStudy } }));
    }
    window.addEventListener("fmx:conversation-saved", told);

    /* Another study opened: its own conversation is shown, as the page
     * opened on it would show it. A chat of no study goes on as it is. */
    window.addEventListener("dashboard:run-changed", function (event) {
      var detail = event.detail || {};
      if (detail.first || !convStudy) return;
      if (String(detail.activeRun || "") === String(convStudy)) return;
      resetThread();
      hideList();
      restore();
      told();
    });

    window.FastMDXAgentPanel = {
      open: function (id, study, loaded) { return openConversation(id, study, loaded); },
      fresh: freshConversation,
      list: function () { return showList(); },
      get current() { return { id: convId, study: convStudy }; },
    };

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
        // Not over a reply still being written.
        if (writing) return;
        area.value = b.getAttribute("data-prompt") || b.textContent;
        /* A question about the study open, which its records answer where
         * no AI model is set: asked as it was offered, it says which. */
        fromStarter = b.getAttribute("data-records")
          ? { key: b.getAttribute("data-records"), prompt: area.value } : null;
        autosize(area);
        /* Sent as it is pressed. Written into the box to be changed
         * first, a question asked by a press read as not asked, and the
         * box sat waiting with the question in it. In autonomous mode a
         * study the Agent writes is started without being shown, so there
         * a suggestion waits in the box to be read and sent. */
        if (el("agent-mode").value === "autonomous") {
          area.focus();
          area.setSelectionRange(area.value.length, area.value.length);
        } else {
          draft();
        }
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
      var ended = "";
      var processState = "idle";
      var said = function () {
        studyState = processState === "running" ? "running"
          : /^(failed|stopped|interrupted)$/.test(ended) ? "stopped"
          : studyOpen ? "finished" : "none";
        placeholder();
      };
      window.FastMDXDashboard.on("app-state", function (s) {
        served = !!(s && s.active_run);
        processState = (s && s.status) || "idle";
        decide();
        said();
        /* The run the Agent started has ended: what it found. Asked on any
         * state not running while one waits for it, not only on a change
         * seen from running: a run that ended between two of the page's
         * polls was missed (review, 10-07). The server says not yet while
         * it goes on. */
        if (processState !== "running") sayHowTheRunEnded();
      });
      window.FastMDXDashboard.on("status-updated", function (u) {
        var status = (u && u.status) || {};
        ran.status = !!(status.stage || status.current_step != null);
        ended = String(status.status || "").toLowerCase();
        decide();
        said();
      });
      window.FastMDXDashboard.on("results-updated", function (r) {
        ran.results = !!(r && (r.has_analysis || r.has_report));
        decide();
        said();
      });
    }
    syncStart();
  });

  /* For the settings popup: "Agent settings…" should open this dialog,
   * not merely land on the Agent page. */
  window.FastMDXAgent = { openSettings: openSettings, closeSettings: closeSettings };
})();
