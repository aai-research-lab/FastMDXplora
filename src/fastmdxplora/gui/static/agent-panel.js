/* The Agent panel.
 *
 * The subject of this page is the FastMDXplora Agent. Which AI model
 * it drafts with is an engine setting -- chosen once, then out of the way.
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
      "An API key is needed. Open Settings to add one."
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
    var show = window.FastMDXIcons.button("viewer", "Show it in the Viewer");
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
      /* Saved in the thread it was asked in, whatever is on screen when
       * the server answers (seventeenth review, 10-08). */
      var c = conv, t = transcript;
      write.disabled = true;
      said.textContent = "Writing the scene\u2026";
      var view = {};
      ["frame", "representation", "colour", "superposed"].forEach(function (key) {
        if (scene[key] != null) view[key] = scene[key];
      });
      fetch("/api/scenes", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: named, view: view, highlight: scene.highlight || "",
                               labels: !!scene.labels, ligands: sceneLigands(),
                               page_study: pageStudy() || undefined }),
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
          persistOf(c, t);
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
    current_view: "Checked what the page shows",
    read_docs: "Read the docs"
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
        sendPressed(id ? id[1] : text, function () {
          Array.prototype.forEach.call(grid.querySelectorAll("button"), function (b) {
            b.disabled = true;
          });
          button.classList.add("is-chosen");
        });
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

  /* Under a reply, on one row with Copy: Useful or Wrong, kept with the
   * conversation for the Agent's evaluation (a second press takes it back),
   * and what the reply took, in tokens, shown only once its icon is pressed
   * (user, 10-09: "a button that user needs to click first to reveal the
   * token info ... on the same row with copy"). Each is a line icon named
   * on hover and to a screen reader (user, 10-09: "those buttons should be
   * line icons"), filled while pressed, so which is pressed is not said by
   * its colour alone. */
  var FEEDBACK = { useful: "Useful", wrong: "Wrong" };

  function feedbackShown(b, pressed) {
    b.setAttribute("aria-pressed", pressed ? "true" : "false");
    var drawn = b.querySelector("svg");
    if (drawn) drawn.setAttribute("fill", pressed ? "currentColor" : "none");
  }

  function usageSaid(u) {
    if (!u || !(u.input_tokens || u.cache_read_tokens)) return "";
    var sent = (u.input_tokens || 0) + (u.cache_read_tokens || 0);
    var kept = [];
    if (u.cache_read_tokens) kept.push(u.cache_read_tokens.toLocaleString("en-US") + " from the cache");
    return sent.toLocaleString("en-US") + " tokens in" + (kept.length ? " (" + kept.join(", ") + ")" : "")
      + " \u00b7 " + (u.output_tokens || 0).toLocaleString("en-US") + " out";
  }

  function meta(r, entry) {
    var body = r.part("body") || r.node;
    /* The reply's own row of tools, Copy first; one is made where a reply
     * has none, so the marks are never on a row of their own. */
    var row = body.querySelector(".agent-msg-tools");
    if (!row) {
      row = document.createElement("div");
      row.className = "agent-msg-tools";
      body.appendChild(row);
    }
    row.classList.add("agent-meta");
    ["useful", "wrong"].forEach(function (kind) {
      var b = window.FastMDXIcons.button(kind, FEEDBACK[kind], "agent-feedback");
      b.setAttribute("data-feedback", kind);
      feedbackShown(b, entry.feedback === kind);
      b.addEventListener("click", function () {
        entry.feedback = entry.feedback === kind ? null : kind;
        Array.prototype.forEach.call(row.querySelectorAll(".agent-feedback"), function (x) {
          feedbackShown(x, x.getAttribute("data-feedback") === entry.feedback);
        });
        persist();
      });
      row.appendChild(b);
    });
    var used = usageSaid(entry.usage);
    if (used) {
      var said = document.createElement("span");
      said.className = "agent-usage";
      said.id = "agent-usage-" + (++usageShown);
      said.textContent = used;
      said.hidden = true;
      var show = window.FastMDXIcons.button("usage", "Tokens this reply took",
                                            "agent-usage-toggle");
      show.title = "Show the tokens this reply took";
      show.setAttribute("aria-expanded", "false");
      show.setAttribute("aria-controls", said.id);
      show.addEventListener("click", function () {
        said.hidden = !said.hidden;
        show.setAttribute("aria-expanded", said.hidden ? "false" : "true");
        show.title = (said.hidden ? "Show" : "Hide") + " the tokens this reply took";
      });
      row.appendChild(show);
      row.appendChild(said);
    }
    return row;
  }
  var usageShown = 0;

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

  function providerSpec(id) {
    return providers.filter(function (p) { return p.id === id; })[0] || null;
  }

  function engineLabelParts(choice) {
    var spec = providerSpec(choice.provider);
    var label = spec ? spec.label : choice.provider;
    var location = choice.base_url ? " at " + choice.base_url : "";
    return [label + " · ", choice.model || "no model", location];
  }

  function engineLabel(choice) {
    return engineLabelParts(choice).join("");
  }

  var savedEngineChoice = null;
  var savedKeyIsSet = false;

  function updateKeyHelp(id) {
    var spec = providerSpec(id);
    if (!spec) return;
    var help = KEY_HELP[id] || "";
    if (help) help += " ";
    var baseUrl = spec.needs_url ? (el("agent-base-url").value || "").trim() : "";
    var matches = savedEngineChoice && savedEngineChoice.provider === id &&
      (savedEngineChoice.base_url || "") === baseUrl;
    var status = matches && savedKeyIsSet
      ? " An API key is set for this choice; its value is never shown."
      : "";
    el("agent-key-help").textContent = help +
      "Saved keys stay in your owner-only model.json file. " +
      spec.environment_variable + " takes precedence; the key is never shown again." + status;
  }

  function updateEngineSummary(error) {
    var saved = savedEngineChoice;
    var provider = el("agent-provider").value;
    var spec = providerSpec(provider);
    var selected = { provider: provider,
      model: chosenModel(), base_url: spec && spec.needs_url
        ? el("agent-base-url").value.trim() : "" };
    var parts = saved && engineLabelParts(saved);
    var before = saved ? "Saved for Agent requests: " + parts[0]
      : "No AI model is set.";
    var after = saved ? (saved.model ? "" : "no model") + parts[2] : "";
    var same = saved && saved.provider === selected.provider && saved.model === selected.model &&
      (saved.base_url || "") === selected.base_url;
    if (!same && (selected.model || selected.provider)) {
      after += "\nCurrent selection (unsaved): " + engineLabel(selected);
    }
    if (typeof error === "string" && error) after += "\n" + error;
    var current = el("agent-model-current");
    current.textContent = saved && saved.model || "";
    el("agent-model-summary").replaceChildren(document.createTextNode(before),
      current, document.createTextNode(after));
  }

  function applyModelOptions(options) {
    if (!Array.isArray(options) || !options.length) return;
    var spec = providerSpec(el("agent-provider").value);
    if (!spec) return;
    spec.model_options = options;
    spec.models = options.map(function (item) { return item.id; });
    var selected = chosenModel();
    fillModels(spec, selected);
    updateEngineSummary();
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
    var modelOptions = spec.model_options || (spec.models || []).map(function (name) {
      return {id: name, label: name};
    });
    modelOptions.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label || item.id;
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
    var spec = providerSpec(id);
    if (!spec) return;
    /* A pasted key belongs to the provider that was selected when it was
     * entered. Never carry it across a provider switch. */
    el("agent-key").value = "";
    el("agent-url-field").hidden = !spec.needs_url;
    fillModels(spec, chosen || spec.default_model || "");
    updateKeyHelp(id);
    el("agent-url-examples").textContent =
      (spec.examples || []).map(function (e) {
        return e.label + ": " + e.url;
      }).join("  ·  ");
    updateEngineSummary();
  }

  function engineIsSet(current, keyIsSet) {
    savedEngineChoice = current ? Object.assign({}, current) : null;
    savedKeyIsSet = !!keyIsSet;
    updateKeyHelp(current ? current.provider : el("agent-provider").value);
    updateEngineSummary();
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
    var shownAgain = start.hidden && !thread.childElementCount;
    start.hidden = thread.childElementCount > 0;
    /* Shown again once the thread is emptied (a refused message taken out):
     * from its top, not where the thread was scrolled to (CI-774 review
     * L22 L5: its heading was under the Agent bar). */
    if (shownAgain && thread.offsetParent !== null) {
      var column = thread.closest(".main, .agent-drawer-body") || document.scrollingElement;
      if (column) column.scrollTop = 0;
    }
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
    loadMemory();
  }

  /* What the Agent remembers of the person (agent/memory.py): shown and
   * changed in its Settings, every line with where it came from, and said
   * under a reply when a chat changed it, with Undo. The file is theirs
   * too (agent_memory.md); this reads and changes it through
   * /api/agent/memory, the same as `fastmdx agent memory`. */
  var memoryState = null;

  function memoryBox() {
    var box = el("agent-memory");
    if (box) return box;
    var body = document.querySelector("#agent-settings .agent-dialog-body");
    if (!body) return null;
    box = document.createElement("section");
    box.id = "agent-memory";
    box.className = "agent-memory";
    box.setAttribute("aria-label", "What the Agent remembers of you");
    body.appendChild(box);
    /* Made once, beside the section rather than in it, so a screen reader
     * hears each change to it: a live region drawn anew is not heard. */
    var status = memoryNode("p", "builder-card-note agent-memory-status");
    status.id = "agent-memory-status";
    status.setAttribute("role", "status");
    body.appendChild(status);
    return box;
  }

  function memoryStatus(said) {
    var status = el("agent-memory-status");
    if (!status) return;
    status.textContent = "";
    if (said) setTimeout(function () { status.textContent = said; }, 60);
  }

  /* Asked of the server, then the section drawn again with focus where
   * the person was (`focus`: "add", "switch-use", "switch-from_chats",
   * "line-N", "foot") and, where it was refused, what they typed kept. */
  function memoryAsk(payload, focus, typed, done) {
    return post("/api/agent/memory", payload).then(function (data) {
      if (data && data.lines) memoryState = data;
      // A refusal (a key, a line too long) is said where it was asked.
      var refused = data && data.ok === false;
      // A refusal is said in the status line too, so it is heard.
      renderMemory(refused ? data : null, refused ? typed : "",
                   refused ? (data.error || "") : done);
      memoryFocus(refused && typed ? "add" : focus);
      return data;
    }).catch(function () {
      var unreached = "The memory could not be reached; nothing was changed.";
      renderMemory({ error: unreached }, typed, unreached);
      memoryFocus(focus);
      return null;
    });
  }

  function memoryFocus(where) {
    var box = el("agent-memory");
    if (!box || !where) return;
    var target = null;
    if (where === "add") target = box.querySelector(".agent-memory-add input");
    else if (where.indexOf("switch-") === 0) {
      target = box.querySelector('[data-switch="' + where.slice(7) + '"]');
    } else if (where.indexOf("line-") === 0) {
      var rows = box.querySelectorAll(".agent-memory-lines:not(.agent-memory-held) " +
                                      ".agent-memory-line");
      var row = rows[Math.min(parseInt(where.slice(5), 10), rows.length - 1)];
      target = row ? row.querySelector("button") : null;
    } else if (where === "foot") target = box.querySelector(".agent-memory-foot button");
    else if (where === "clear") {
      target = box.querySelector('.agent-memory-foot button[aria-label="Forget everything"]');
    }
    (target || box.querySelector(".agent-memory-add input")).focus();
  }

  function loadMemory() {
    var box = memoryBox();
    if (!box) return Promise.resolve(null);
    return fetch("/api/agent/memory").then(function (r) { return r.json(); })
      .then(function (data) {
        memoryState = data && data.lines ? data : null;
        renderMemory(memoryState ? null : data);
        return data;
      }).catch(function () {
        memoryState = null;
        renderMemory({ error: "What the Agent remembers could not be read." });
      });
  }

  function memoryNode(tag, cls, text) {
    var made = document.createElement(tag);
    if (cls) made.className = cls;
    if (text !== undefined) made.textContent = text;
    return made;
  }

  function memoryWhen(line) {
    var when = (line.updated || line.added || "").slice(0, 10);
    var from = line.source === "chat" ? "from a chat" : "yours";
    return when ? from + ", " + when : from;
  }

  /* `done`: what the person just did, said in the section's status line
   * for a screen reader as well as on the page. */
  function renderMemory(problem, typed, done) {
    var box = memoryBox();
    if (!box) return;
    box.innerHTML = "";
    box.appendChild(memoryNode("div", "builder-label", "What I remember of you"));
    var data = memoryState;
    if (!data) {
      box.appendChild(memoryNode("p", "builder-card-note",
        (problem && problem.error) || "This GUI keeps no memory of you."));
      return;
    }
    box.appendChild(memoryNode("p", "builder-card-note",
      "Told to me with each message, so my answers suit you; never a setting's " +
      "value (those go in your fastmdx-defaults.yml)."));
    if (data.unreadable) {
      box.appendChild(memoryNode("p", "builder-card-note agent-memory-problem",
        "It could not be read: " + data.unreadable));
    }
    if (problem && problem.error) {
      var said = memoryNode("p", "builder-card-note agent-memory-problem", problem.error);
      said.id = "agent-memory-problem";
      box.appendChild(said);
    }
    var switches = memoryNode("div", "agent-memory-switches");
    [["use", "Use this memory"],
     ["from_chats", "Learn from what you write to me (each message I read for it " +
      "costs one more short call to your provider)"]].forEach(function (pair) {
      var label = memoryNode("label", "agent-memory-switch");
      var tick = document.createElement("input");
      tick.type = "checkbox";
      tick.checked = !!data[pair[0]];
      tick.setAttribute("data-switch", pair[0]);
      // Nothing is learnt for a memory that is not used.
      if (pair[0] === "from_chats" && !data.use) {
        tick.disabled = true;
        label.classList.add("agent-memory-off");
      }
      tick.addEventListener("change", function () {
        var ask = { op: "switches" };
        ask[pair[0]] = tick.checked;
        memoryAsk(ask, "switch-" + pair[0], "",
                  pair[1].replace(/ \(.*$/, "") + (tick.checked ? ": on." : ": off."));
      });
      label.appendChild(tick);
      label.appendChild(memoryNode("span", null, pair[1]));
      switches.appendChild(label);
    });
    box.appendChild(switches);
    box.appendChild(memoryNode("p", "builder-card-note",
      data.use ? "Each change here is kept at once, in " + data.where + "."
               : "Off: nothing here is told to me, and nothing is learnt from what you " +
                 "write. What is here stays, in " + data.where + ", for when you turn it on."));

    var list = memoryNode("ul", "agent-memory-lines");
    if (!data.use) list.classList.add("agent-memory-off");
    if (!data.lines.length) {
      list.appendChild(memoryNode("li", "agent-memory-empty", "Nothing yet."));
    }
    var group = "";
    data.lines.forEach(function (line, index) {
      // The person's own groups in the file (### Systems), as they made them.
      if ((line.heading || "") !== group) {
        group = line.heading || "";
        if (group) {
          list.appendChild(memoryNode("li", "agent-memory-group",
                                      group.replace(/^#+\s*/, "")));
        }
      }
      var row = memoryNode("li", "agent-memory-line");
      var words = memoryNode("span", "agent-memory-text", line.text);
      var meta = memoryNode("span", "agent-memory-meta", memoryWhen(line));
      var edit = window.FastMDXIcons.button("edit", "Change: " + line.text);
      var bin = window.FastMDXIcons.button("bin", "Forget: " + line.text);
      edit.addEventListener("click", function () { memoryEdit(row, line); });
      bin.addEventListener("click", function () {
        memoryAsk({ op: "forget", id: line.id }, "line-" + index, "",
                  "Forgot: " + line.text);
      });
      row.appendChild(words);
      row.appendChild(meta);
      row.appendChild(edit);
      row.appendChild(bin);
      list.appendChild(row);
    });
    box.appendChild(list);

    var add = memoryNode("div", "agent-memory-add");
    var input = document.createElement("input");
    input.type = "text";
    input.maxLength = data.most_line || 300;
    input.placeholder = "Something for me to remember, such as \"You are new to MD.\"";
    input.setAttribute("aria-label", "A line to remember");
    if (typed) input.value = typed;
    if (typed && problem && problem.error) {
      // What was typed is refused: the reason is the box's description.
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", "agent-memory-problem");
    }
    var keep = window.FastMDXIcons.button("new", "Remember this");
    function remembered() {
      var text = input.value.trim();
      if (!text) return;
      memoryAsk({ op: "add", text: text }, "add", text, "Remembered: " + text);
    }
    keep.addEventListener("click", remembered);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); remembered(); }
    });
    add.appendChild(input);
    add.appendChild(keep);
    box.appendChild(add);

    if (data.not_told && data.not_told.length) {
      box.appendChild(memoryNode("div", "builder-label", "In your file, not told to me"));
      box.appendChild(memoryNode("p", "builder-card-note",
        "Lines I found in the file and do not tell myself, with why. To keep one, " +
        "write it again above (a value for every study goes in your " +
        "fastmdx-defaults.yml instead); to take one out, change the file itself, in " +
        data.where + "."));
      var held = memoryNode("ul", "agent-memory-lines agent-memory-held");
      data.not_told.forEach(function (item) {
        var row = memoryNode("li", "agent-memory-line");
        row.appendChild(memoryNode("span", "agent-memory-text", item.text));
        row.appendChild(memoryNode("span", "agent-memory-meta", item.why));
        held.appendChild(row);
      });
      box.appendChild(held);
    }

    var foot = memoryNode("div", "agent-memory-foot");
    var last = (data.changes || []).filter(function (c) { return !c.undone; }).pop();
    if (last) {
      foot.appendChild(memoryNode("span", "agent-memory-meta", "The last change. " +
        memorySaid(last)));
      var back = window.FastMDXIcons.button("undo", "Undo this. " + memorySaid(last));
      back.addEventListener("click", function () {
        memoryAsk({ op: "undo", change: last.id }, "foot", "", memoryUndone(last));
      });
      foot.appendChild(back);
    }
    if (data.lines.length || (data.not_told && data.not_told.length)) {
      var clear = window.FastMDXIcons.button("erase", "Forget everything");
      clear.addEventListener("click", function () { memoryClearAsked(foot); });
      foot.appendChild(clear);
    }
    box.appendChild(foot);
    memoryStatus(done || "");
  }

  /* Forget everything is asked in place, in the foot: a sentence saying
   * what goes and what stays, then the two answers, the safe one first. */
  function memoryClearAsked(foot) {
    foot.innerHTML = "";
    foot.classList.add("agent-memory-asking");
    var ask = memoryNode("span", "agent-memory-meta",
      "Forget every line I remember of you? Your own notes in the file stay, " +
      "and this cannot be undone.");
    ask.id = "agent-memory-clear-ask";
    var keep = memoryNode("button", "ctl-btn", "Keep them");
    var go = memoryNode("button", "ctl-btn agent-memory-clear", "Forget everything");
    keep.type = go.type = "button";
    go.setAttribute("aria-describedby", ask.id);
    function kept() {
      window.removeEventListener("keydown", escaped, true);
      renderMemory(null);
      memoryFocus("clear");
    }
    /* Escape answers the question as Keep them, and leaves the dialog open:
     * caught on the window, before the dialog's own handler. */
    function escaped(e) {
      if (e.key === "Escape" && foot.contains(e.target)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        kept();
      }
    }
    window.addEventListener("keydown", escaped, true);
    keep.addEventListener("click", kept);
    go.addEventListener("click", function () {
      window.removeEventListener("keydown", escaped, true);
      memoryAsk({ op: "clear" }, "add", "", "Forgot everything I remembered of you.");
    });
    foot.appendChild(ask);
    foot.appendChild(keep);
    foot.appendChild(go);
    keep.focus();
  }

  function memorySaid(change) {
    if (change.what === "added") return "Remembered: " + change.text;
    if (change.what === "changed") return "Changed what I remember: " + change.text;
    return "Forgot: " + change.text;
  }

  /* What an undo did, said from the person's side of it. */
  function memoryUndone(change) {
    if (change.what === "added") return "Undone: I no longer remember " + change.text;
    if (change.what === "changed") return "Undone: the line has its earlier words again.";
    return "Undone: I remember again " + change.text;
  }

  function memoryEdit(row, line) {
    row.innerHTML = "";
    var input = document.createElement("input");
    input.type = "text";
    input.value = line.text;
    input.maxLength = (memoryState && memoryState.most_line) || 300;
    input.setAttribute("aria-label", "New words for this line");
    var save = window.FastMDXIcons.button("check", "Keep these words");
    var cancel = window.FastMDXIcons.button("close", "Leave it as it was");
    var at = Array.prototype.indexOf.call(row.parentNode.children, row);
    function left() {
      window.removeEventListener("keydown", escaped, true);
      renderMemory(null);
      memoryFocus("line-" + at);
    }
    function kept() {
      var text = input.value.trim();
      if (!text || text === line.text) { left(); return; }
      window.removeEventListener("keydown", escaped, true);
      memoryAsk({ op: "change", id: line.id, text: text }, "line-" + at, "",
                "Changed what I remember: " + text);
    }
    /* Escape in the edit box leaves the edit, not the dialog: caught on
     * the window, before the dialog's own handler on the document. */
    function escaped(e) {
      if (e.key === "Escape" && e.target === input) {
        e.preventDefault();
        e.stopImmediatePropagation();
        left();
      }
    }
    window.addEventListener("keydown", escaped, true);
    save.addEventListener("click", kept);
    cancel.addEventListener("click", left);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); kept(); }
    });
    row.appendChild(input);
    row.appendChild(save);
    row.appendChild(cancel);
    input.focus();
  }

  /* After a reply the server reads what the person wrote for the memory,
   * in its own time (`memory: "reading"`): asked for a while what that one
   * reply changed, and each change said under the reply, with Undo. */
  function memoryUnder(node, replyKey) {
    /* Asked until the server says the reading is done, however long the
     * AI model takes (a local one may take minutes), and through a failed
     * ask or two; given up after ten minutes. */
    var began = Date.now();
    var wait = 1500;
    function again() {
      if (Date.now() - began > 600000) return;
      setTimeout(look, wait);
      wait = Math.min(wait * 1.5, 15000);
    }
    function look() {
      fetch("/api/agent/memory?reply=" + encodeURIComponent(replyKey))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data && data.reading) { again(); return; }
          var made = (data && data.changes) || [];
          if (made.length) memoryNoted(node, made);
        }).catch(again);
    }
    again();
  }

  function memoryNoted(node, made) {
    var body = node.querySelector('[data-role="body"]') || node;
    var note = memoryNode("div", "agent-memory-note");
    note.setAttribute("role", "status");
    note.tabIndex = -1;
    made.forEach(function (change) {
      var row = memoryNode("div", "agent-memory-note-row");
      var said = memoryNode("span", null, memorySaid(change));
      row.appendChild(said);
      if (!change.undone) {
        var back = window.FastMDXIcons.button("undo", "Undo: " + memorySaid(change));
        back.addEventListener("click", function () {
          post("/api/agent/memory", { op: "undo", change: change.id }).then(function (a) {
            if (a && (a.ok || a.undone_already)) {
              // Undone here, or in Settings or by the command since: said,
              // not an error.
              said.textContent = memoryUndone(change);
            } else {
              // Why not, after what it was about, so the line is still said.
              said.textContent = memorySaid(change) + ". " +
                ((a && a.error) || "It could not be undone.");
            }
            // The button goes; the focus stays in the note, not lost.
            if (document.activeElement === back) note.focus();
            back.remove();
            // Settings, if it is open, shows the memory as it now is; only
            // with the memory sent back, so nothing open there is lost.
            if (a && a.lines) {
              memoryState = a;
              if (el("agent-memory")) renderMemory(null);
            }
          }).catch(function () {
            said.textContent = memorySaid(change) + ". The memory could not be reached, " +
              "so it is not undone yet.";
          });
        });
        row.appendChild(back);
      }
      note.appendChild(row);
    });
    var edit = window.FastMDXIcons.button("gear", "What I remember, in Settings");
    edit.addEventListener("click", openSettings);
    note.lastChild.appendChild(edit);
    body.appendChild(note);
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
      showProvider(select.value, data.current && data.current.model);
      engineIsSet(data.current, data.key_set);
    });
  }

  function saveEngine(keepOpen) {
    var button = el("agent-save-model");
    button.disabled = true;
    var provider = el("agent-provider").value;
    var spec = providerSpec(provider);
    return post("/api/agent/model", {
      provider: provider,
      model: chosenModel(),
      base_url: spec && spec.needs_url ? el("agent-base-url").value.trim() : "",
      api_key: el("agent-key").value
    }).then(function (data) {
      button.disabled = false;
      if (!data.ok) {
        updateEngineSummary(data.error || "The provider settings could not be saved.");
        return false;
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
        if (spec) {
          spec.models = data.models;
          spec.model_options = data.model_options || data.models.map(function (model) {
            return {id: model, label: model};
          });
          fillModels(spec, data.current.model);
        }
      }
      engineIsSet(data.current, data.key_set);
      if (!keepOpen) closeSettings();
      return true;
    }).catch(function () {
      button.disabled = false;
      updateEngineSummary("The provider settings could not be saved.");
      return false;
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
   * restore, so a launch can name it exactly when moving it. One object per
   * thread, replaced (never changed) when another thread is shown: a save
   * or a launch keeps the thread it was made for, and its late answer
   * changes that thread alone (eleventh and twelfth reviews, 10-08: a
   * thread switched while a save waited was written over). */
  var conv = threadOf(null, null, []);

  /* A thread's own record: where it lives, every entry it has held (by
   * `eid`), how many of its saves are waiting, and whether it was deleted
   * elsewhere. */
  function threadOf(id, study, entries) {
    /* `page`: the study the page was for as the thread was put on it, by
     * key. Its saves name it, and the server writes none into another
     * study's conversations. */
    var t = { id: id || null, study: study || null, known: {}, confirmed: {}, waiting: 0,
              gone: false, key: newEid(), entries: null, page: pageStudy() };
    (entries || []).forEach(function (e) {
      if (e && e.eid) { t.known[e.eid] = true; t.confirmed[e.eid] = JSON.stringify(e); }
    });
    return t;
  }

  /* Threads with saves still waiting, so a page closing sends them all. */
  var waitingThreads = [];

  var eids = 0;
  function newEid() {
    eids += 1;
    return "e" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7) + eids;
  }
  /* Files attached to the next message: what read_attachment returned. */
  var pendingFiles = [];
  var attaching = 0;
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
      var rm = window.FastMDXIcons.button("close", "Remove " + f.name, "rm");
      rm.addEventListener("click", function () { pendingFiles.splice(i, 1); renderChips(); });
      chip.appendChild(name); chip.appendChild(size); chip.appendChild(rm);
      host.appendChild(chip);
    });
    keepUnsent();
  }

  /* ---- Words not sent yet, kept per study in this browser window ------ */
  /* What is typed in the box and the files attached to it belong to the
   * study open as they were begun. They are kept in session storage under
   * that study's key (the words, and of each file only its path, name,
   * size and digest: the file is read again when they are put back), so a
   * Back, a page put away by the browser, or any other way out of the study
   * and back again finds them there; sending or clearing them forgets them.
   * Where this browser keeps nothing, they are kept in the page while it is
   * open, a switch of study is held for them (beforeStudySwitch), and the
   * browser asks before the page is left (beforeunload). */
  var UNSENT_WORDS = "fmx.agentUnsent:";
  var unsentKey = "";
  var unsentTitle = "";
  /* The studies runs this page started went to, as their launches
   * answered them (`study_key`), each for a short while after: the GUI
   * goes to a run's folder as it starts, and the page may see that after
   * the answer. Words typed are taken to a run's study only, by the first
   * switch seen after the answer; any other study opened (another window,
   * an AI app) gets none of them (CI-774 reviews K3, K4: for a minute after
   * a launch, they went to whatever study was opened next). */
  var runStudies = [];
  /* A study seen while a launch is still going: whether it is the run's is
   * known only once the launch answers, so it is judged then. */
  var seenWhileLaunching = null;
  var unsentKept = false;
  /* Per study, what this page holds that no storage kept. */
  var heldInPage = {};
  /* Files kept with the words being read again: nothing is written over
   * the kept entry meanwhile. */
  var restoring = 0;
  /* Each study seen, by its key: how its notes name it (its folder). */
  var studyNames = {};
  function studyName(key) {
    return studyNames[key] || runFolders[key] || "";
  }
  var NOT_KEPT = "This browser window could not keep ";
  function validStudyKey(key) { return typeof key === "string" && /^[0-9a-f]{16}$/.test(key); }
  function boxNow() {
    var area = el("agent-request");
    return area ? area.value : "";
  }
  function fileKept(f) {
    return { path: f.path, name: f.name, size: f.size, sha256: f.sha256 || "", truncated: !!f.truncated };
  }
  /* What the note beside the box says: "kept" (could not be kept), "lost"
   * (files not attached again) or "moved" (words kept for another study). */
  var noteKind = "";
  /* The study a "moved" note is about: said no more once it is open. */
  var noteAbout = "";
  function unsentSaid(text, kind, about) {
    noteKind = text ? kind || "" : "";
    noteAbout = text ? about || "" : "";
    var area = el("agent-request");
    var host = el("agent-unsent-note");
    if (!host && area) {
      var composer = area.closest(".agent-composer");
      if (!composer) return;
      host = document.createElement("p");
      host.className = "agent-unsent-note muted small";
      host.id = "agent-unsent-note";
      host.setAttribute("role", "status");
      composer.insertBefore(host, composer.firstChild);
    }
    if (!host) return;
    host.textContent = text || "";
    host.hidden = !text;
  }
  function keepUnsent() {
    var words = boxNow();
    var empty = !words.trim() && !pendingFiles.length;
    if (restoring) return;
    if (empty) {
      if (validStudyKey(unsentKey)) {
        delete heldInPage[unsentKey];
        try { sessionStorage.removeItem(UNSENT_WORDS + unsentKey); } catch (e) { /* none kept */ }
      }
      unsentKept = true;
      if (noteKind === "kept" || noteKind === "lost") unsentSaid("");
      return;
    }
    if (!validStudyKey(unsentKey)) {
      /* Not yet known which study they are for: kept once it is
       * (restoreUnsent), never written over what was kept for it. */
      unsentKept = false;
      return;
    }
    unsentKept = false;
    try {
      sessionStorage.setItem(UNSENT_WORDS + unsentKey,
                             JSON.stringify({ words: words, files: pendingFiles.map(fileKept) }));
      unsentKept = true;
      delete heldInPage[unsentKey];
      if (noteKind === "kept") unsentSaid("");
    } catch (e) {
      heldInPage[unsentKey] = { words: words, files: pendingFiles.slice(), title: unsentTitle };
      unsentSaid(pendingFiles.length
        ? NOT_KEPT + "this message and its attachments: they are lost if the page is left before it is sent."
        : NOT_KEPT + "this message: it is lost if the page is left before it is sent.", "kept");
    }
  }
  function storedUnsent(key) {
    var kept = null;
    try { kept = JSON.parse(sessionStorage.getItem(UNSENT_WORDS + key) || "null"); } catch (e) { kept = null; }
    if (!kept && heldInPage[key]) kept = heldInPage[key];
    if (!kept || typeof kept !== "object") return null;
    var files = Array.isArray(kept.files) ? kept.files.filter(function (f) {
      return f && typeof f === "object" && typeof f.path === "string" && typeof f.name === "string"
        && typeof f.size === "number";
    }).slice(0, 6) : [];
    return { words: typeof kept.words === "string" ? kept.words.slice(0, 100000) : "", files: files };
  }
  /* Unsent work this page holds that nothing outside it keeps: what the
   * browser is asked to leave the page over. */
  function unsentUnkept() {
    var here = boxNow().trim() || pendingFiles.length;
    return Boolean((here && !unsentKept) || Object.keys(heldInPage).length
                   || editsLeft().some(function (e) { return !validStudyKey(e.key); }));
  }
  /* Edits refused and still being edited, each with the study it was
   * written for (CI-774 reviews L17 B, L18 4: the thread replaced, by a
   * study opened elsewhere, a reload or a switch as its files were read
   * again, the edited words were nowhere). */
  var refusedEdits = [];
  function editsLeft() {
    return refusedEdits.filter(function (e) {
      var words = e.words();
      return words && words !== e.before.trim();
    });
  }
  /* The words of an edit that will not be sent from its bubble: saved for
   * the study it was written for, as unsent words are, and said; for the
   * study shown, or with no study, put in the box after what is there. */
  function editKept(edit, words) {
    var host = el("agent-unsent-note");
    var said = noteKind === "moved" && host ? host.textContent : "";
    var about = said ? noteAbout : "";
    if (validStudyKey(edit.key) && edit.key !== unsentKey) {
      var kept = storedUnsent(edit.key) || { words: "", files: [] };
      var all = kept.words.trim() ? kept.words + "\n\n" + words : words;
      var files = withFiles(kept.files, edit.files);
      try {
        sessionStorage.setItem(UNSENT_WORDS + edit.key, JSON.stringify({ words: all, files: files }));
      } catch (e) {
        heldInPage[edit.key] = { words: all, files: files, title: edit.title };
      }
      var title = edit.title || "the study it was written in";
      unsentSaid((said ? said + " " : "") + "The edit not sent is saved for " + title
                 + ", not put here: it is in the box again when " + title + " is open.",
                 "moved", about || edit.key);
      return;
    }
    var area = el("agent-request");
    if (!area) return;
    var now = boxNow();
    area.value = now.trim() ? now + "\n\n" + words : words;
    area.dispatchEvent(new Event("input"));
    keepUnsent();
    unsentSaid((said ? said + " " : "") + "The edit not sent is in the box, to send from there.",
               "moved", about);
    var mine = pendingFiles.map(function (f) { return f.path; });
    var again = edit.files.filter(function (f) { return mine.indexOf(f.path) < 0; });
    savedWithFiles(again);
    readFilesAgain(again, unsentKey);
  }
  /* The words in the box saved with the files about to be read again for
   * them: a switch seen while they are read saves what is saved here
   * (CI-774 reviews L21 M1, L22 M1: saved without them, the files read
   * after the switch were dropped, unsaid). */
  function savedWithFiles(files) {
    if (!files.length || !validStudyKey(unsentKey)) return;
    var entry = { words: boxNow(), files: withFiles(pendingFiles.map(fileKept), files) };
    try {
      sessionStorage.setItem(UNSENT_WORDS + unsentKey, JSON.stringify(entry));
      delete heldInPage[unsentKey];
    } catch (e) {
      heldInPage[unsentKey] = { words: entry.words, files: entry.files, title: unsentTitle };
    }
  }
  /* Files read again after their words went to another study: added to
   * what is saved for the study the words are saved for. */
  function filesSavedFor(key, got) {
    if (!validStudyKey(key) || !got.length) return;
    var kept = storedUnsent(key);
    if (!kept) return;
    var files = withFiles(kept.files, got.map(fileKept));
    var held = heldInPage[key];
    try {
      /* What else is saved with them (`asRunStarted`, `editLeft`) stays. */
      var raw = JSON.parse(sessionStorage.getItem(UNSENT_WORDS + key) || "null");
      if (!raw || typeof raw !== "object") throw new Error("held in this page");
      raw.files = files;
      sessionStorage.setItem(UNSENT_WORDS + key, JSON.stringify(raw));
    } catch (e) {
      if (held) held.files = files;
      else heldInPage[key] = { words: kept.words, files: files, title: studyName(key) };
    }
  }
  /* Files saved with words, and those of an edit or a message put with
   * them: each once, six at most. */
  function withFiles(kept, more) {
    return kept.concat((more || []).filter(function (f) {
      return !kept.some(function (k) { return k.path === f.path; });
    })).slice(0, 6);
  }
  /* The thread on screen is replaced: each edit left is kept so. */
  function keepEditsLeft() {
    var left = editsLeft().map(function (e) { return { edit: e, words: e.words() }; });
    refusedEdits = [];
    return function () {
      left.forEach(function (e) { editKept(e.edit, e.words); });
    };
  }
  /* The reads going on, each with the study its words are for: that
   * study changes as the words go with the box to another (`readsFollow`). */
  var reads = [];
  function readsFollow(from, to) {
    reads.forEach(function (r) { if (r.key === from) r.key = to; });
  }
  /* Kept files read again: those that cannot be, or that changed since,
   * are said by name. */
  function readFilesAgain(files, key) {
    if (!files.length) return;
    var lost = [];
    var left = files.length;
    var read = { key: key, got: [] };
    reads.push(read);
    restoring += 1;
    files.forEach(function (f) {
      attaching += 1;
      post("/api/agent/attachment", { path: f.path }).then(function (d) {
        if (!d || !d.ok) { lost.push(f.name); return; }
        if (f.sha256 && d.sha256 && d.sha256 !== f.sha256) {
          lost.push(f.name + " (changed since)");
          return;
        }
        read.got.push(d);
        if (unsentKey !== read.key) return;
        if (pendingFiles.some(function (p) { return p.path === d.path; }) || pendingFiles.length >= 6) return;
        pendingFiles.push(d);
      }).catch(function () { lost.push(f.name); }).then(function () {
        attaching -= 1;
        left -= 1;
        if (left) return;
        restoring -= 1;
        reads = reads.filter(function (r) { return r !== read; });
        /* Their words saved for another study as they were read: nothing
         * read is dropped. */
        if (unsentKey !== read.key) { filesSavedFor(read.key, read.got); return; }
        renderChips();
        if (lost.length) {
          unsentSaid("Could not attach again: " + lost.join(", ") + ". Attach " +
                     (lost.length === 1 ? "it" : "them") + " again to send " +
                     (lost.length === 1 ? "it." : "them."), "lost");
        }
      });
    });
  }
  /* Forgotten: a study's tab closed, or its folder gone (study-tabs.js). The
   * words on screen stay, with the study they were typed for. */
  function forgetUnsent(key) {
    if (!validStudyKey(key)) return;
    if (key === unsentKey && (boxNow().trim() || pendingFiles.length)) return;
    delete heldInPage[key];
    try { sessionStorage.removeItem(UNSENT_WORDS + key); } catch (e) { /* none kept */ }
  }
  function shownStudy() {
    var dashboard = window.FastMDXDashboard;
    var key = dashboard && dashboard.state ? dashboard.state.studyShown : "";
    /* No study open (a run that failed at once leaves none): the study the
     * page showed last is no longer the page's (CI-774 review L11). */
    if (noStudyOpen()) return "";
    return validStudyKey(key) ? key : "";
  }
  function noStudyOpen() {
    var dashboard = window.FastMDXDashboard;
    var app = dashboard && dashboard.state ? dashboard.state.appState : null;
    return Boolean(app && Object.keys(app).length && !app.active_run);
  }
  /* The study this page is for: the one it shows, or before that is known,
   * the one its address names. */
  function pageStudy() {
    var key = shownStudy();
    if (key || noStudyOpen()) return key;
    try { key = new URL(location.href).searchParams.get("study") || ""; } catch (e) { key = ""; }
    return validStudyKey(key) ? key : "";
  }
  /* The study shown is known: what was left unsent in it is put back.
   * Typed here before it was known (a Back's page, its box filled again by
   * the browser, or words typed before the first answer): put after what
   * was kept, never over it. Typed for another study shown before (the
   * study changed under this window): kept for that one, where it was
   * begun, and taken out of this study's box. */
  function restoreUnsent(study) {
    var key = study && study.key;
    if (!validStudyKey(key)) { leftForNoStudy(); return; }
    var title = (study && study.title) || "";
    /* Named by its folder alone where it has one: a study is named from its
     * manifest until its results are read, so its title as the switch is
     * seen may be its system's, and several studies may be of one system
     * (CI-774 reviews L6, L16: one study was named two ways in its notes). */
    var folder = (study && study.folder) || "";
    if (folder) title = folder;
    if (title) studyNames[key] = title;
    if (key === unsentKey) {
      seenWhileLaunching = null;
      unsentTitle = title || unsentTitle;
      if (!launching && study.past) {
        /* Still on the study the words were typed in, read once every
         * launch answered: they stay with it (CI-774 review L6). */
        wordsFollowRun = "";
        runStudies = [];
      }
      return;
    }
    if (launching) {
      /* The words stay with the study they were begun in until the launch
       * says where the run went. */
      seenWhileLaunching = { key: key, title: title };
      return;
    }
    /* Words typed after Run into the thread the run took with it: the run's
     * study's, whatever other study is seen first (CI-774 reviews K6, L2
     * and L4). */
    var follow = wordsFollowRun;
    wordsFollowRun = "";
    if (follow === "next") follow = key;
    if (follow && key !== follow && validStudyKey(unsentKey) && unsentKey !== follow) {
      delete heldInPage[unsentKey];
      try { sessionStorage.removeItem(UNSENT_WORDS + unsentKey); } catch (e) { /* none kept */ }
      readsFollow(unsentKey, follow);
      unsentKey = follow;
      /* Named by its folder alone, as every note names a study (CI-774
       * reviews L12, L20). */
      unsentTitle = runFolders[follow] || "the run's study";
    }
    var area = el("agent-request");
    var typed = boxNow();
    var begunElsewhere = validStudyKey(unsentKey);
    /* The study a run this page started went to: the thread went there
     * with it, and so do the words being typed into it, whole (CI-774
     * review K2: the part typed before the page saw the switch was kept
     * for the study left). Judged once, by the first switch seen to it.
     * The GUI goes to each run's study before its launch answers: seen,
     * a run's study is past every one before it, and a study that is no
     * run's, read once every launch has answered (`past`), is past them
     * all; one seen while a launch went on, or read as asked before it
     * answered, may have come before the run's (CI-774 reviews K5, L1 and
     * L6: the first judged emptied a later run's, a run's never seen was
     * kept for a study opened elsewhere later, and a read asked before the
     * answer emptied the run's). */
    var now = Date.now();
    var at = lastRun(runStudies, key);
    var runsThere = (at >= 0 && now < runStudies[at].until) || follow === key;
    runStudies = runStudies.slice(at + 1).filter(function (r) { return now < r.until; });
    if (study.past) runStudies = [];
    if (begunElsewhere && runsThere) {
      delete heldInPage[unsentKey];
      try { sessionStorage.removeItem(UNSENT_WORDS + unsentKey); } catch (e) { /* none kept */ }
      readsFollow(unsentKey, key);
      unsentKey = key;
      unsentTitle = title;
      putBack(key, typed);
      return;
    }
    if (begunElsewhere && (typed.trim() || pendingFiles.length || attaching)) {
      var was = unsentKey;
      var forTitle = unsentTitle || "the study before";
      keepUnsent();
      var onlyHere = Boolean(heldInPage[was]);
      unsentKey = key;
      unsentTitle = title;
      /* Emptied as the words go: not taken for the shown study's box
       * emptied, which would forget what is kept for it (CI-774 reviews
       * L5 and L6). */
      restoring += 1;
      if (area) { area.value = ""; area.dispatchEvent(new Event("input")); }
      pendingFiles = [];
      renderChips();
      restoring -= 1;
      var last = onlyHere ? " in this page, which this browser cannot keep it past." : ".";
      unsentSaid("The unsent message typed for " + forTitle + " is saved for it, not put here: "
                 + "it is in the box again when " + forTitle + " is open" + last, "moved", was);
      putBack(key, "");
      /* Words of this study's own put in place of those being typed: an
       * Enter meant for those sends nothing (`draft`). */
      swappedAt = boxNow().trim() ? Date.now() : 0;
      return;
    }
    /* Typed with no study known: the files being read again for them go
     * with them. */
    if (!begunElsewhere) readsFollow(unsentKey, key);
    unsentKey = key;
    unsentTitle = title;
    putBack(key, begunElsewhere ? "" : typed);
    if (!begunElsewhere) {
      saidWhereKeptAsARunStarted(key);
      openedAsSentSaid(title);
    }
  }
  /* The GUI went from a study to no study, or to a folder that is not one:
   * the words typed for the study are saved for it and taken out of the
   * box, and said, as for another study (CI-774 review L18: they stayed in
   * the box and went into the workspace's chats). Not while a run this page
   * started may still take them to its study, nor while a run starts in the
   * folder shown, not yet a study (Send waits, and its study, once one, is
   * judged as any), nor for a run it started that ended at once: they are
   * the person's words about it, there to send. */
  function leftForNoStudy() {
    var dashboard = window.FastMDXDashboard;
    var state = (dashboard && dashboard.state) || {};
    var now = Date.now();
    if (!validStudyKey(unsentKey) || launching || wordsFollowRun
        || runStudies.some(function (r) { return now < r.until; })
        || (runFolders[unsentKey] && now < (launchedUntil[unsentKey] || 0))
        || sendWaits() === "run") return;
    if (!noStudyOpen() && state.studyIsOne !== false) return;
    var was = unsentKey;
    var forTitle = unsentTitle || "the study before";
    var typed = boxNow().trim() || pendingFiles.length || attaching;
    if (typed) keepUnsent();
    var onlyHere = Boolean(heldInPage[was]);
    unsentKey = "";
    unsentTitle = "";
    if (!typed) return;
    var area = el("agent-request");
    restoring += 1;
    if (area) { area.value = ""; area.dispatchEvent(new Event("input")); }
    pendingFiles = [];
    renderChips();
    restoring -= 1;
    var last = onlyHere ? " in this page, which this browser cannot keep it past." : ".";
    unsentSaid("The unsent message typed for " + forTitle + " is saved for it, not put here: "
               + "it is in the box again when " + forTitle + " is open" + last, "moved", was);
  }
  /* Words kept as the page before went while a run started: where they are
   * is said, once. */
  function saidWhereKeptAsARunStarted(shown) {
    try {
      for (var i = 0; i < sessionStorage.length; i += 1) {
        var name = sessionStorage.key(i) || "";
        if (name.indexOf(UNSENT_WORDS) !== 0) continue;
        var kept = JSON.parse(sessionStorage.getItem(name) || "null");
        if (!kept || typeof kept !== "object" || !kept.asRunStarted) continue;
        var key = name.slice(UNSENT_WORDS.length);
        var title = String(kept.asRunStarted);
        delete kept.asRunStarted;
        sessionStorage.setItem(name, JSON.stringify(kept));
        if (key === shown || !validStudyKey(key)) continue;
        unsentSaid("The unsent message typed for " + title + " as a run started is saved for it, "
                   + "not put here: it is in the box again when " + title + " is open.", "moved", key);
      }
    } catch (e) { /* nothing kept */ }
  }
  /* The last of `list` for the study `key`, or -1. */
  function lastRun(list, key) {
    for (var i = list.length - 1; i >= 0; i -= 1) if (list[i].key === key) return i;
    return -1;
  }
  /* A run whose launch took the thread on screen with it, words typed
   * into that thread after Run being in the box: the words are the run's
   * study's, whatever study is seen first (`restoreUnsent`). */
  var wordsFollowRun = "";
  /* Every launch this page made has answered: the run's study, if one
   * started (`key`), is known, and a switch seen meanwhile is judged. */
  /* The folder each run this page started went to, by its study's key. */
  var runFolders = {};
  /* Until when each run this page started may still end at once, leaving
   * no study open, by its study's key: words typed for it then stay in the
   * box (`leftForNoStudy`); each run its own time (CI-774 review L19 L4: one
   * for all, a later launch's kept an earlier run's words in the box). */
  var launchedUntil = {};
  function launchAnswered(key, follows, output) {
    if (validStudyKey(key)) runStudies.push({ key: key, until: Date.now() + 30000 });
    if (validStudyKey(key)) launchedUntil[key] = Date.now() + 30000;
    if (validStudyKey(key) && output) {
      runFolders[key] = String(output).replace(/[\\/]+$/, "").split(/[\\/]/).pop();
    }
    /* An answer not read (`key` null): the run's study is not known, and
     * the first study seen other than the words' own is taken for it. */
    if (follows) wordsFollowRun = validStudyKey(key) ? key : key === null ? "next" : "";
    if (launching) return;
    var seen = seenWhileLaunching;
    seenWhileLaunching = null;
    if (seen) { restoreUnsent(seen); return; }
    /* Already on the run's study (it was the study open, or the page
     * learnt which study it was on only then): nothing to take there. */
    var at = lastRun(runStudies, unsentKey);
    if (at >= 0) runStudies = runStudies.slice(at + 1);
  }
  /* When a study opened elsewhere put its own kept words in the box in
   * place of those being typed (CI-774 review L16: an Enter pressed for the
   * words typed sent the words put back). */
  var swappedAt = 0;
  var SWAPPED = "Not sent: the box now holds the words saved for this study, put back as it was "
    + "opened. Press Enter again to send them.";
  /* An edit saved as the page was left (a reload) is put back with the
   * words: said once, as a switch says it (CI-774 review L20: in the box,
   * with nothing said of why). */
  var EDIT_LEFT = "The edit not sent as the page was left is in the box, to send from there.";
  function editLeftFor(key) {
    try {
      var raw = JSON.parse(sessionStorage.getItem(UNSENT_WORDS + key) || "null");
      if (!raw || typeof raw !== "object" || !raw.editLeft) return false;
      delete raw.editLeft;
      sessionStorage.setItem(UNSENT_WORDS + key, JSON.stringify(raw));
      return true;
    } catch (e) { return false; }
  }
  function putBack(key, typed) {
    var area = el("agent-request");
    var edited = editLeftFor(key);
    var kept = storedUnsent(key);
    var files = kept ? kept.files : [];
    var words = kept ? kept.words : "";
    if (typed.trim() && words.trim() && typed.trim() !== words.trim()) {
      words = words + "\n\n" + typed;
    } else if (typed.trim()) {
      words = typed;
    }
    var mine = pendingFiles.map(function (f) { return f.path; });
    restoring += 1;
    if (area && words !== boxNow()) {
      area.value = words;
      area.dispatchEvent(new Event("input"));
    }
    readFilesAgain(files.filter(function (f) { return mine.indexOf(f.path) < 0; }), key);
    restoring -= 1;
    keepUnsent();
    /* Open now: a note that its words are kept for it says so no more. */
    if (noteKind === "moved" && noteAbout === key) unsentSaid("");
    if (edited) unsentSaid(EDIT_LEFT, "moved", "");
    sendWaitSaid();
  }
  function attachFile(path) {
    if (!path) return;
    attaching += 1;
    post("/api/agent/attachment", { path: path }).then(function (d) {
      if (!d || !d.ok) { window.alert((d && d.error) || "Could not attach that file."); return; }
      if (pendingFiles.some(function (f) { return f.path === d.path; })) return;
      if (pendingFiles.length >= 6) { window.alert("Six files at most on one message."); return; }
      pendingFiles.push(d);
      renderChips();
      el("agent-request").focus();
    }).catch(function () { window.alert("Could not reach the server."); })
      .finally(function () { attaching -= 1; });
  }

  /* ---- What is named with @, the page, and the plan ------------------- */

  /* `@rmsd`, `@study`, `@frame 120`, `@Trp6`, `@file analysis/rg/options.json`:
   * shown as chips while they are typed, and sent as typed. The server finds
   * what each names and tells the Agent where its record is (`agent/context.py`,
   * the same pattern), never its text. */
  var MENTION = /(^|[^\w@.])@(frame\s+\d{1,7}|file\s+[^\s`]+|[A-Za-z0-9][\w.:+\-]{0,79})/g;
  var MOST_MENTIONS = 8;
  function mentionsIn(text) {
    var found = [];
    var m;
    MENTION.lastIndex = 0;
    while ((m = MENTION.exec(String(text || ""))) && found.length < MOST_MENTIONS) {
      var said = m[2].replace(/\s+/g, " ").replace(/[.,;:!?)\]}"']+$/, "");
      if (said && found.indexOf(said) < 0) found.push(said);
    }
    return found;
  }
  function renderMentions() {
    var area = el("agent-request");
    var files = el("agent-attachments");
    if (!area || !files) return;
    var host = el("agent-mentions");
    if (!host) {
      host = document.createElement("div");
      host.id = "agent-mentions";
      host.className = "agent-attachments agent-mentions";
      host.setAttribute("aria-label", "Named with @");
      files.parentNode.insertBefore(host, files);
    }
    var found = mentionsIn(area.value);
    host.innerHTML = "";
    host.hidden = found.length === 0;
    found.forEach(function (said) {
      var chip = document.createElement("span");
      chip.className = "agent-chip";
      chip.title = "Named with @: the software looks for what it names and tells the Agent "
        + "where its record is";
      var name = document.createElement("span");
      name.className = "name";
      name.textContent = "@" + said;
      chip.appendChild(name);
      host.appendChild(chip);
    });
  }

  /* What the page shows, for the Agent: where to look, never what is so
   * (`current_view`, `agent/tools.py`). The Viewer gives its own view; a page
   * that keeps hints (`window.FastMDXPageHints[page]`) gives its; every page
   * its name, the study open and the file open in the reader. Only the page
   * shown: the Viewer's frame went with every message, from every page. */
  function clipped(value) {
    return typeof value === "string" ? value.slice(0, 500) : value;
  }
  function pageHints() {
    var dash = (window.FastMDXDashboard && window.FastMDXDashboard.state) || {};
    var page = dash.activePage || null;
    var viewer = window.FastMDXMoleculeViewer;
    if (page === "viewer" && viewer && viewer.currentViewHints) return viewer.currentViewHints();
    var hints = {};
    if (page) hints.page = page;
    var app = dash.appState || {};
    var study = app.active_run || null;
    if (study) hints.study = clipped(String(study));
    /* No study open, once read so: named as such, and refused by the
     * server where the GUI has one open (CI-774 review L18: asked with a
     * study another window opened, it was filed in the workspace's chats). */
    else if (Object.keys(app).length) hints.study = "";
    var frame = window.FastMDXFrame;
    var file = frame && frame.previewing ? frame.previewing() : null;
    if (file) hints.file = clipped(String(file));
    var own = window.FastMDXPageHints && page ? window.FastMDXPageHints[page] : null;
    if (typeof own === "function") {
      try {
        var more = own() || {};
        Object.keys(more).forEach(function (key) {
          if (more[key] != null && more[key] !== "") hints[key] = clipped(more[key]);
        });
      } catch (e) { /* a page's hints never stop a message */ }
    }
    return Object.keys(hints).length ? hints : null;
  }

  /* The plan the Agent keeps across turns (`keep_plan`): shown as a
   * checklist under the reply that gave it, the newest sent back with each
   * message so the Agent keeps to it and ticks it. It does nothing: each
   * step is still done only when the person says so. */
  var currentSteps = null;
  function stepsOf(value) {
    if (!Array.isArray(value)) return null;
    var steps = value.filter(function (s) { return s && typeof s.step === "string" && s.step; })
      .slice(0, 12).map(function (s) { return { step: s.step.slice(0, 200), done: s.done === true }; });
    return steps.length ? steps : null;
  }
  function stepsCard(box, steps) {
    if (!steps) return;
    var done = steps.filter(function (s) { return s.done; }).length;
    var head = document.createElement("div");
    head.className = "agent-plan-head";
    head.textContent = "Plan: " + done + " of " + steps.length + " done";
    head.title = "The Agent keeps this plan and ticks a step once it is done. It does "
      + "nothing itself: each step is done only when you say so.";
    head.style.margin = "8px 0 2px";
    head.style.fontSize = "var(--text-xs)";
    head.style.color = "var(--text-muted)";
    var list = document.createElement("ol");
    list.className = "agent-plan";
    /* Its look here, until the stylesheet (held for another round) has a
     * rule for it: the ticks are the list's marks. */
    list.style.listStyle = "none";
    list.style.paddingLeft = "0";
    list.style.margin = "0";
    list.setAttribute("aria-label", "The Agent's plan");
    steps.forEach(function (s) {
      var item = document.createElement("li");
      var tick = document.createElement("input");
      tick.type = "checkbox";
      tick.checked = s.done;
      tick.disabled = true;
      tick.setAttribute("aria-label", s.done ? "Done" : "To do");
      var words = document.createElement("span");
      words.textContent = " " + s.step;
      item.appendChild(tick);
      item.appendChild(words);
      list.appendChild(item);
    });
    box.appendChild(head);
    box.appendChild(list);
  }

  /* Saved where it lives: a chat of no study stays one while a study is
   * open, and a study's conversation stays its study's. The server keeps
   * the conversation and merges each save into it: the page sends its
   * entries and every entry it has held, so another tab's entries, and
   * where a run went, are kept, and what the page cut goes (tenth to
   * fourteenth reviews, 10-08: whole lists written over each other lost
   * entries). Saves, and opening or starting a thread, go one at a time:
   * one sent before the first had named the thread made two. */
  var saving = Promise.resolve();
  function inTurn(job) {
    var done = saving.then(job);
    saving = done.catch(function () {});
    return done;
  }

  function persist() { return persistOf(conv, transcript); }

  function saveBody(c, entries) {
    entries.forEach(function (e) {
      if (!e.eid) e.eid = newEid();
      c.known[e.eid] = true;
    });
    /* Current only for the thread on screen: a save of another made it
     * the one a reload opened (sixteenth review, 10-08). */
    var body = { entries: entries, seen: Object.keys(c.known), current: c === conv };
    /* The first save of a study's thread made after the page has gone on
     * to another study: the thread its study shows when opened again (the
     * study had none), not a conversation found only in the list. */
    if (!c.id && !c.placed && c.study && validStudyKey(c.page) && c.page !== pageStudy()) {
      body.current = true;
    }
    if (c.page) body.page_study = c.page;
    if (c.id) {
      body.id = c.id; body.study = c.study || null;
    } else {
      body.key = c.key;
      /* A thread begun in a place of its own (a chat of no study while a
       * study is open) is made there by its first save too (nineteenth
       * review, 10-08: made in the open study's folder). So is a study's
       * thread, in the study it was written in, the GUI's study now or not
       * (CI-774 reviews L13, L14: saved to the study open, it was refused
       * once another window had opened another, and the answer lost). And
       * a chat of no study read for where the GUI was, in the workspace's
       * chats: saved to the study open since, it was refused (CI-774 review
       * L15). */
      if (c.placed || c.standsIn || (c.page && c.study)) body.study = c.study || null;
    }
    return body;
  }

  /* A thread saved: `c` is where it is, `entries` what it says. */
  function persistOf(c, entries) {
    /* Deleted: written again only as a new conversation, once the person
     * has said something since (twentieth review, 10-08: a mark meeting the
     * delete first left every later message unsaved). */
    if (c.gone) return keptAsNew(c, entries) || Promise.resolve(null);
    c.waiting += 1;
    c.entries = entries;
    if (waitingThreads.indexOf(c) < 0) waitingThreads.push(c);
    return inTurn(function () {
      if (c.gone) { keptAsNew(c, entries); return null; }
      var body = saveBody(c, entries);
      /* Each entry as it went: a field set on it while the save was on its
       * way (a scene written, a mark) is not confirmed by this save
       * (nineteenth review, 10-08). */
      var sent = {}, as = {};
      body.entries.forEach(function (e) {
        sent[e.eid] = true;
        as[e.eid] = JSON.parse(JSON.stringify(e));
      });
      return post("/api/agent/conversation", body).then(function (d) {
        if (d && d.ok) {
          c.id = d.id || c.id;
          /* Moved by the server (a run took it with it): where it is now is
           * the server's word. */
          if (d.study !== undefined && (d.study || null) !== (c.study || null)) c.page = "";
          if (d.study !== undefined) c.study = d.study;
          if (c.study) c.standsIn = false;
          takeIn(c, entries, d.entries || [], sent, as, d.cut || []);
          window.dispatchEvent(new CustomEvent("fmx:conversation-saved"));
        } else if (d && d.study_changed) {
          /* Refused as written from a page for another study than the one
           * the GUI has open: said, and kept unsaved here. */
          var dashboard = window.FastMDXDashboard;
          if (dashboard && dashboard.toast) dashboard.toast(d.error || "Not saved.", "warning");
          keepRefusedWords(c, entries);
        } else if (d && d.gone) {
          /* Deleted elsewhere: not written again, unless the person has
           * said something since. What the deleted conversation held went
           * with it, so it is not taken for that (twenty-fourth review,
           * 10-08: a conversation kept anew, then deleted on purpose, came
           * back). */
          (d.held || []).forEach(function (eid) {
            entries.forEach(function (e) {
              if (e.eid === eid) c.confirmed[eid] = JSON.stringify(e);
            });
          });
          c.gone = true;
          keptAsNew(c, entries);
        }
        return d;
      }).catch(function () {});
    }).then(function (d) { settled(c); return d; }, function () { settled(c); });
  }

  /* A save refused as the GUI had opened another study (another window, a
   * run of another tab): the messages the server has not kept are kept as
   * unsent words for the study the thread is for, and said; the thread
   * put up in its place lost them (CI-774 review L12). */
  var keptRefused = {};
  function keepRefusedWords(c, entries) {
    var key = c.page;
    if (!validStudyKey(key)) return;
    var refused = entries.filter(function (e) {
      return e && e.role === "user" && e.eid && e.text && !c.confirmed[e.eid] && !keptRefused[e.eid];
    });
    if (!refused.length) return;
    var words = refused.map(function (e) { keptRefused[e.eid] = true; return e.text; });
    /* With their files (CI-774 review L13: kept without them). */
    var files = [];
    refused.forEach(function (e) {
      (e.attachments || []).forEach(function (a) {
        if (a && a.path && !files.some(function (f) { return f.path === a.path; })) files.push(fileKept(a));
      });
    });
    var text = words.join("\n\n");
    if (key === unsentKey) {
      var area = el("agent-request");
      if (!area) return;
      /* Taken out of the thread on screen, with what answered them: in
       * both, they were sent again and kept twice (CI-774 review L13). */
      if (c === conv && entries === transcript) {
        var first = transcript.filter(function (e) { return e.role === "user"; }).indexOf(refused[0]);
        var node = first < 0 ? null : el("agent-thread").querySelectorAll(".agent-msg-user")[first];
        if (node) cutFrom(node);
      }
      area.value = boxNow().trim() ? boxNow() + "\n\n" + text : text;
      area.dispatchEvent(new Event("input"));
      savedWithFiles(files);
      if (files.length) readFilesAgain(files, key);
      unsentSaid("Not added to the conversation, as the GUI had opened another study: the message is "
                 + "back in the box, its answer not saved.", "moved", "");
      return;
    }
    var kept = storedUnsent(key);
    var all = kept && kept.words.trim() ? kept.words + "\n\n" + text : text;
    var allFiles = (kept ? kept.files : []).concat(files.filter(function (f) {
      return !(kept ? kept.files : []).some(function (k) { return k.path === f.path; });
    })).slice(0, 6);
    try {
      sessionStorage.setItem(UNSENT_WORDS + key, JSON.stringify({ words: all, files: allFiles }));
    } catch (e) {
      heldInPage[key] = { words: all, files: allFiles, title: "" };
    }
    unsentSaid("Not added to the conversation, as the GUI had opened another study: the message is "
               + "saved, unsent, for "
               + "the study it was written in, in the box again when that study is open; its answer "
               + "is not saved.", "moved", key);
  }
  /* A message refused as the study open changed as it was sent (another
   * window or an AI app opened a study, `agent.view.changed`): not answered,
   * so not left in its thread, which went out of view with it (CI-774
   * review L19 M1: the words were gone, unsaid). Its words go back as a
   * refused save's do: in the box for a chat of no study or the study the
   * box is for, else saved for the study it was written in; and said. */
  var NOT_SENT_VIEW = "Not sent, as the study open changed as it was sent: ";
  function viewChangedWords(c, entries, heard, asked, said, bubble, reply) {
    var at = entries.indexOf(said);
    if (at >= 0) entries.splice(at, 1);
    var told = heard.indexOf(asked);
    if (told >= 0) heard.splice(told, 1);
    [bubble, reply].forEach(function (n) { if (n && n.parentNode) n.parentNode.removeChild(n); });
    if (c === conv) placeholder();
    if (c.known[said.eid]) persistOf(c, entries);
    var files = (said.attachments || []).map(fileKept);
    var key = c.study ? c.page || (c === conv ? convFor : "") || "" : "";
    if (!validStudyKey(key) || key === unsentKey) {
      var area = el("agent-request");
      if (!area) return;
      area.value = boxNow().trim() ? boxNow() + "\n\n" + said.text : said.text;
      area.dispatchEvent(new Event("input"));
      savedWithFiles(files);
      if (files.length) readFilesAgain(files, unsentKey);
      /* Of no study: the study opened is named, once the page has seen it
       * (CI-774 review L22 L4). */
      var opened = validStudyKey(key) ? "" : validStudyKey(unsentKey) ? studyName(unsentKey) : "";
      nameOpened = !validStudyKey(key) && !opened;
      unsentSaid(opened ? openedAsSent(opened) : NOT_SENT_NONE, "moved", "");
      return;
    }
    var kept = storedUnsent(key);
    var all = kept && kept.words.trim() ? kept.words + "\n\n" + said.text : said.text;
    var allFiles = withFiles(kept ? kept.files : [], files);
    var name = studyName(key);
    try {
      sessionStorage.setItem(UNSENT_WORDS + key, JSON.stringify({ words: all, files: allFiles }));
    } catch (e) {
      heldInPage[key] = { words: all, files: allFiles, title: name };
    }
    /* Named by its folder, as every note names a study (CI-774 reviews L21
     * L2, L22 L4). */
    unsentSaid(NOT_SENT_VIEW + (name
      ? "the message is saved for " + name + ", where it was written: it is in the box again when "
        + name + " is open."
      : "the message is saved for the study it was written in, in the box again when that study "
        + "is open."), "moved", key);
  }
  var NOT_SENT_NONE = NOT_SENT_VIEW + "the message is back in the box.";
  var nameOpened = false;
  function openedAsSent(title) {
    return "Not sent, as " + title + " was opened as it was sent: the message is back in the box.";
  }
  /* The study a message of no study was refused for is seen: named in the
   * note, if the note still says only that the study open changed. */
  function openedAsSentSaid(title) {
    var waiting = nameOpened;
    nameOpened = false;
    var host = el("agent-unsent-note");
    if (!waiting || !title || !host || noteKind !== "moved" || host.textContent !== NOT_SENT_NONE) return;
    unsentSaid(openedAsSent(title), "moved", "");
  }
  function settled(c) {
    c.waiting -= 1;
    if (!c.waiting) waitingThreads = waitingThreads.filter(function (t) { return t !== c; });
  }

  /* A thread deleted in another window while the person went on in it:
   * what they said since is not dropped. The thread is kept as a new
   * conversation, where it was, and the thread says so (nineteenth review,
   * 10-08: everything typed after the delete was lost without a word). A
   * thread nobody spoke in since (a run's summary, a mark) stays deleted. */
  /* The server's words for the same (`KEPT_AFTER_DELETE`): true whether
   * the thread is kept whole, or, from a page closing first, only what was
   * said after (twenty-second review, 10-08). */
  var DELETED_ELSEWHERE = "This conversation was deleted in another window. " +
    "What was said after is kept here, as a new conversation.";
  /* Conversations this page deleted: nothing of theirs is kept as new
   * (twentieth review, 10-08: one deleted here while its answer was
   * written came back, said to be deleted "in another window"). */
  var deletedHere = {};
  /* Those whose delete was answered, and the keys of threads deleted
   * before their first save named them. */
  var deletedDone = {};
  var deletedKeys = {};
  var deletesWaiting = {};
  /* The person's own word this page holds and no save has confirmed: a
   * message, or a run they started. Another tab's entries are confirmed
   * as they are taken in, so they are not this person's word here. */
  function spokeSince(c, entries) {
    return entries.some(function (e) {
      return !(e.eid && e.eid in c.confirmed) &&
        (e.role === "user" || (e.kind === "action" && e.action === "run"));
    });
  }
  function keptAsNew(c, entries) {
    if (deletedHere[c.id] || deletedKeys[c.key] || !spokeSince(c, entries)) return null;
    /* What was confirmed stays so: a page closing before the new one is
     * saved sends only what is new, not the whole thread (twenty-first
     * review, 10-08). */
    c.id = null;
    /* The key its closing copy asked to keep it by, if a page restored
     * from the cache had one: a later load may have used that copy, and
     * the thread is then kept once, there (twenty-third review, 10-08). */
    c.key = c.keepKey || newEid();
    c.keepKey = null;
    c.placed = true;
    c.known = {};
    c.gone = false;
    /* The note the server writes when it keeps a closing page's words,
     * with the same eid, so one thread holds one. */
    entries.push({ eid: "kept-" + c.key, role: "agent", kind: "note", text: DELETED_ELSEWHERE });
    if (c === conv && entries === transcript) {
      var r = reply();
      whoIs(r, "done");
      note(r.part("attempts"), DELETED_ELSEWHERE);
      scrollToEnd();
    }
    return persistOf(c, entries);
  }

  /* What the server kept, taken in: a field it wrote into an entry (where
   * a run went), and the entries this page never held (another tab's),
   * drawn when the thread is the one on screen. */
  function takeIn(c, entries, merged, sent, as, cutThere) {
    var mine = {};
    entries.forEach(function (e) { if (e.eid) mine[e.eid] = e; });
    var kept = {};
    var others = false;
    merged.forEach(function (e) {
      if (!e || !e.eid) return;
      kept[e.eid] = true;
      var held = mine[e.eid];
      if (held) {
        var went = (as && as[e.eid]) || {};
        Object.keys(e).forEach(function (k) {
          if (!(k in held)) held[k] = e[k];
          if (!(k in went)) went[k] = e[k];
        });
        c.confirmed[e.eid] = JSON.stringify(went);
        c.known[e.eid] = true;
      } else {
        /* Another tab's, as the server keeps it. */
        c.confirmed[e.eid] = JSON.stringify(e);
        if (!c.known[e.eid]) others = true;
      }
    });
    /* Cut by another tab: sent, and cut there. Sent and not kept is not
     * enough: past the length kept, the oldest go too, and every save
     * then drew the thread again (nineteenth review, 10-08). */
    var cutAt = {};
    (cutThere || []).forEach(function (eid) { cutAt[eid] = true; });
    var cut = entries.some(function (e) { return sent[e.eid] && !kept[e.eid] && cutAt[e.eid]; });
    if (!others && !cut) return;
    var onScreen = c === conv && entries === transcript;
    if (onScreen && (writing || launching)) {
      /* A reply or a run being started would be drawn into a thread
       * redrawn under it: the redraw waits for it (seventeenth and
       * eighteenth reviews, 10-08), and comes with the next save. */
      c.redrawWanted = true;
      return;
    }
    /* Another tab changed it: the thread as the server keeps it, in its
     * order, then what was said here since this save went (fifteenth and
     * sixteenth reviews, 10-08: drawn at the bottom, a cut here took the
     * other tab's entries with it). What this page cut after the save
     * went is not brought back (eighteenth review, 10-08). */
    var since = entries.filter(function (e) { return !sent[e.eid] && !kept[e.eid]; });
    var whole = merged.filter(function (e) {
      return e && e.eid && !(sent[e.eid] && !mine[e.eid]);
    }).map(function (e) { return mine[e.eid] || e; }).concat(since);
    whole.forEach(function (e) { c.known[e.eid] = true; });
    c.redrawWanted = false;
    if (onScreen) {
      redraw(whole);
    } else {
      entries.length = 0;
      whole.forEach(function (e) { entries.push(e); });
    }
  }

  /* A redraw put off for a reply or a launch: asked for again by a save
   * once both have settled. */
  function redrawIfWanted() {
    if (conv.redrawWanted && !writing && !launching) {
      conv.redrawWanted = false;
      persist();
    }
  }

  /* A page closed or reloaded with saves still waiting: the thread on
   * screen sent as it is, so what was said is kept (thirteenth and
   * fourteenth reviews, 10-08). Merged as any save is, so a save that
   * also lands does no harm. */
  /* The page is going: what comes back late (an answer, a launch's word)
   * is not said, nor saved, by it (nineteenth review, 10-08: a reload
   * during a launch saved "I could not read the software's answer"). */
  var leaving = false;
  /* Asked to go (a reload, a link followed, the tab closed): a request the
   * going cuts off fails as the page is put away, in the same turn as
   * pagehide and in Chromium sometimes just before it (CI on cd2818a9: a
   * reload as a run started saved "I could not read the software's
   * answer"). */
  var askedToGo = false;
  window.addEventListener("beforeunload", function (event) {
    /* Unsent words this browser could not keep: it asks before the page
     * is left (a Back across studies, a reload, an address typed). */
    if (unsentUnkept()) {
      event.preventDefault();
      event.returnValue = "";
      return;
    }
    askedToGo = true;
  });
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    leaving = false;
    askedToGo = false;
    try { sessionStorage.removeItem(UNSENT); } catch (e) { /* none */ }
  });
  /* A launch still unanswered as the page went, taken as the going begins:
   * its request, cut by the going, may fail between this page's own pagehide
   * listeners, and its failure then counts the launch as answered before
   * the words kept are marked as kept as a run started (CI-774 reviews L17,
   * L18: the page that came back said nothing of where they were). A
   * Config Builder's launch too, counted by the dashboard (review L19 L2). */
  var leftLaunching = false;
  window.addEventListener("pagehide", function (event) {
    if (!event.persisted) {
      leaving = true;
      var dashboard = window.FastMDXDashboard;
      leftLaunching = launching > 0
        || Boolean(dashboard && dashboard.state && dashboard.state.launchesAsked > 0);
      /* A question whose answer is still being written: kept, with the
       * answer said as stopped, as pressing Stop says it (nineteenth
       * review, 10-08: the question was saved only with its answer, and a
       * reload lost it). */
      /* Or whose request failed before the page heard it was going (CI
       * run #704 on 10-08: the reload cut the reply off first, and the
       * question was kept with nothing after it), while nothing has been
       * said after it. */
      if (answering && transcript.indexOf(answering) >= 0 &&
          (writing || transcript[transcript.length - 1] === answering)) {
        history.push({ role: "agent", text: "(stopped before answering)" });
        transcript.push({ role: "agent", kind: "answer", text: "Stopped before it finished." });
        answering = null;
      }
    }
    /* Only what the server has not confirmed, as additions that cut
     * nothing: small enough to go as the page closes (a whole thread over
     * 64 KB did not), and harmless if it lands late (fifteenth and
     * sixteenth reviews, 10-08). Every thread with saves waiting, and the
     * one on screen, which may hold what no save has carried yet. */
    conv.entries = transcript;
    var threads = waitingThreads.slice();
    if (threads.indexOf(conv) < 0) threads.push(conv);
    var unsent = [];
    threads.forEach(function (c) {
      if (!c.entries || deletedHere[c.id] || deletedKeys[c.key]) return;
      var spoke = spokeSince(c, c.entries);
      /* Deleted elsewhere, and nothing said since: stays deleted. Said
       * since: sent as below, with its old id and a key to keep it by;
       * the whole thread would be too large to go as the page closes
       * (twenty-first review, 10-08). */
      if (c.gone && !spoke) return;
      var body = saveBody(c, c.entries);
      body.entries = body.entries.filter(function (e) {
        return c.confirmed[e.eid] !== JSON.stringify(e);
      });
      /* Which conversation a reload opens is left to the tabs still
       * open; a thread with no id yet is the one this tab was in
       * (twentieth review, 10-08: a tab closing took the current one from
       * another). */
      body.current = !c.id;
      /* Deleted elsewhere before this was heard: what the person said
       * here is kept as a new conversation all the same. A page kept to
       * come back to does not ask it as it goes (it keeps its thread, and
       * says it again as it goes on; twenty-first review, 10-08), but its
       * copy for the next load does: dropped from the cache, the page
       * comes back as a fresh load that sends it, and a page restored
       * forgets the copy (twenty-second review, 10-08). */
      /* One key for the thread until it is kept by it: a copy sent by an
       * earlier load made its conversation by that key, and a page restored
       * and closed again, or cached again, keeps its words there, not twice
       * (twenty-fourth review, 10-08). */
      /* A thread named only by its key is kept the same way (twenty-fourth
       * review: one whose /new was answered gone closed with nothing to
       * keep it by). */
      var keep = (c.id || c.gone) && spoke ?
        { keep: c.keepKey || newEid(), keep_current: c === conv } : null;
      if (keep) c.keepKey = keep.keep;
      if (keep && !event.persisted) Object.assign(body, keep);
      /* What this page cut and the server may not know yet (a retry with
       * its save still waiting; eighteenth review, 10-08). */
      var held = {};
      c.entries.forEach(function (e) { held[e.eid] = true; });
      body.cut = Object.keys(c.known).filter(function (eid) { return !held[eid]; });
      if (!body.entries.length && !body.cut.length) return;
      body.append = true;
      unsent.push(keep ? Object.assign({}, body, keep) : body);
      try {
        fetch("/api/agent/conversation", {
          method: "POST", keepalive: true,
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body)
        }).catch(function () {});
      } catch (e) { /* kept below for the page that comes next */ }
    });
    /* Kept for the page that comes next in this tab too: a request sent
     * as a page goes can be held back by the browser. Sent again on load,
     * before the thread is read; adding twice adds once. */
    if (unsent.length) {
      try { sessionStorage.setItem(UNSENT, JSON.stringify(unsent)); } catch (e) { /* none */ }
    }
  });

  var UNSENT = "fmx-agent-unsent";
  function sendUnsent() {
    var raw = null;
    try {
      raw = sessionStorage.getItem(UNSENT);
      sessionStorage.removeItem(UNSENT);
    } catch (e) { return; }
    var bodies = [];
    try { bodies = JSON.parse(raw || "[]"); } catch (e) { return; }
    bodies.forEach(function (body) {
      inTurn(function () { return post("/api/agent/conversation", body).catch(function () {}); });
    });
  }

  function tools(msg, items) {
    var bar = document.createElement("div");
    bar.className = "agent-msg-tools";
    items.forEach(function (it) {
      var b = window.FastMDXIcons.button(it.icon, it.label);
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
    /* A reply still being written is to a question cut here. */
    stopWriting();
    /* A run being started from what is cut started all the same: its
     * entry stays, after what is left, so where it went is written into
     * it (nineteenth review, 10-08: cut while it started, and an answer
     * the page could not read, and the thread forgot the run). */
    var starting = startingRuns.filter(function (e) { return transcript.indexOf(e) >= 0; });
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
    starting.forEach(function (e) { if (transcript.indexOf(e) < 0) transcript.push(e); });
    persist();
    stopPending = null;
    runPending = null;
    fixPending = null;
    keptVersions();
    /* What the next message changes is the newest version left. Read from
     * the history it missed a config written with a note beside it, and the
     * Agent then edited an older version (review, 10-07). */
    currentConfig = versions.length ? versions[versions.length - 1].data.yaml : null;
    /* And the plan is the newest one left: a plan from a reply cut here
     * went out with the next message. */
    currentSteps = null;
    transcript.forEach(function (e) {
      var kept = stepsOf(e.steps);
      if (kept) currentSteps = kept;
    });
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
      { label: "Copy", icon: "copy", run: function () { copyText(text); } },
      { label: "Edit", icon: "edit", title: "Edit this message in place and send it again",
        run: function () {
          /* In place, as every AI app a person has used does it: the
           * bubble becomes editable, Enter sends, Escape puts it back.
           * Copying the text down into the composer was a detour. */
          if (body.isContentEditable) return;
          /* The words alone are edited: the line naming its files is put
           * beside the bubble meanwhile, and the files go with the edit
           * (CI-774 review L16: the line was edited and sent as words, the
           * files dropped). */
          var before = text;
          var clip = body.querySelector(".attached");
          /* The study of the thread it is in ("" for a chat of no study). */
          var forKey = conv.study ? conv.page || convFor || "" : "";
          var edit = { before: before, key: forKey, files: (attached || []).map(fileKept),
                       title: forKey && forKey === unsentKey ? unsentTitle : "",
                       words: function () { return body.isContentEditable ? body.textContent.trim() : ""; } };
          function begin(words) {
            if (clip && clip.parentNode === body) msg.insertBefore(clip, body.nextSibling);
            body.textContent = typeof words === "string" ? words : body.textContent;
            body.contentEditable = "true";
            body.classList.add("editing");
            body.focus();
            var range = document.createRange();
            range.selectNodeContents(body);
            range.collapse(false);
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            body.addEventListener("keydown", onKey);
            body.addEventListener("blur", onBlur);
          }
          function done(send) {
            /* Its listeners off and its words read first: the bubble made
             * not editable loses the focus, and its blur put the old words
             * back before they were read, so the old words were sent. */
            body.removeEventListener("keydown", onKey);
            body.removeEventListener("blur", onBlur);
            refusedEdits = refusedEdits.filter(function (e) { return e !== edit; });
            var edited = body.textContent.trim();
            body.contentEditable = "false";
            body.classList.remove("editing");
            body.textContent = send && edited ? edited : before;
            if (clip) body.appendChild(clip);
            if (!send || !edited) return;
            /* Refused (Send waits, a review holds it): nothing is cut, and
             * the edited words stay in the bubble, still being edited, beside
             * the note that says why (CI-774 review L13: they were undone).
             * A click elsewhere then leaves them so, as the click the note
             * asks for (a choice in the review) or one to wait elsewhere;
             * Escape alone puts the old words back (CI-774 reviews L15,
             * L16: the click threw the edit away). */
            function again() {
              refused = true;
              /* Its thread replaced meanwhile (its files read again): kept
               * as an edit left is. */
              if (!msg.isConnected) { if (edited !== before.trim()) editKept(edit, edited); return; }
              begin(edited);
              refusedEdits.push(edit);
            }
            if (!sendPressed(edited, function () { cutFrom(msg); }, true, attached, again)) again();
          }
          var refused = false;
          function onKey(e) {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); done(true); }
            if (e.key === "Escape") { e.preventDefault(); done(false); }
          }
          function onBlur() { if (!refused) done(false); }
          begin();
        } },
      { label: "Retry", icon: "refresh", title: "Send this again from here",
        run: function () {
          sendPressed(text, function () { cutFrom(msg); }, true, attached);
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
      { label: "Copy", icon: "copy", title: "Copy this reply",
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
      /* Emptied since (a refused message taken out): what can be asked is
       * shown from its top (`syncStart`). */
      if (!thread.childElementCount) return;
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

  /* `text`: the words of a button that sends (`sendPressed`), sent as they
   * are; the box and its files are the person's, and are left as they are. */
  var viewSending = false;
  function draft(text, attached) {
    var area = el("agent-request");
    var own = typeof text === "string";
    var typed = (own ? text : area.value).trim();
    if (!typed || writing) return;
    if (heldByReview("sending another message")) return;
    /* Pressed within moments of the box's words being changed by a study
     * opened elsewhere, nothing typed since: not the words it was pressed
     * for. Said, and the next Enter sends them. */
    if (!own && swappedAt && Date.now() - swappedAt < 3000) {
      swappedAt = 0;
      var host = el("agent-unsent-note");
      var before = noteKind === "moved" && host && host.textContent !== SWAPPED ? host.textContent : "";
      unsentSaid((before ? before + " " : "") + SWAPPED, "moved", noteAbout);
      return;
    }
    swappedAt = 0;
    /* Not while it cannot be known where it would be answered, nor before
     * the thread has been read (seventeenth review, 10-08; CI-774 review
     * L12: queued until it was, it went by itself): said, and the words
     * stay in the box (`sendWaits`). Where only the page's view is still
     * to be read after the status of the study shown (a study changed
     * under the page a moment ago), it is read now, as the note says, and
     * this Enter sends once it is, only if nothing changed meanwhile: the
     * same words, the same thread, the same study, nothing else to wait
     * for, and within two seconds; else it says it was not sent. */
    if (sendWaits()) {
      var dashboard = window.FastMDXDashboard;
      if (!own && viewOnlyWaits() && dashboard.readView) {
        /* One at a time: a second Enter meanwhile keeps the note and does
         * no more (CI-774 review L13: the first sent, the second then said
         * "Not sent"). */
        unsentSaid(VIEW_READS, "waits");
        if (viewSending) return;
        var words = area.value, thread = conv, study = shownStudy();
        var late = new Promise(function (done) { window.setTimeout(function () { done(false); }, 2000); });
        viewSending = true;
        Promise.race([dashboard.readView(), late]).then(function (read) {
          viewSending = false;
          if (read && area.value === words && conv === thread && shownStudy() === study
              && !sendWaits()) {
            if (noteKind === "waits") unsentSaid("");
            draft();
          } else {
            unsentSaid("Not sent: the study, its conversation or the words changed, or the "
                       + "study's view took too long to read. Send it again.", "moved", "");
            sendReady();
          }
        });
        return;
      }
      sendWaitSaid(true);
      return;
    }
    /* Sent as typed. It used to be joined to a question the Agent had
     * asked, whatever it said ("which lysozyme...?" and "Has it run long
     * enough?" went as one request); the conversation goes with every
     * message, the question in it, so an answer is read as one. */
    var request = typed;

    var files = own ? (attached || []).slice() : pendingFiles.slice();
    if (!own) {
      pendingFiles = [];
      renderChips();
    }
    var record = files.map(function (f) {
      return { name: f.name, path: f.path, size: f.size, sha256: f.sha256, truncated: !!f.truncated };
    });
    spendChoices();
    var bubble = say(typed, record);
    var historyText = typed + (record.length ? "\n[attached: " + record.map(function (a) { return a.name; }).join(", ") + "]" : "");
    var heard = { role: "user", text: historyText };
    history.push(heard);
    var said = record.length ? { role: "user", text: typed, attachments: record }
      : { role: "user", text: typed };
    /* Its own id from the start: a second save of it is merged on the
     * server, never kept twice. */
    said.eid = newEid();
    transcript.push(said);
    if (!own) {
      area.value = "";
      keepUnsent();
      autosize(area);
      renderMentions();
      /* A note about the words in the box is done with once they are sent
       * (CI-774 review L16: a starter's note stayed beside an empty box). */
      if (noteKind === "moved" && !noteAbout) unsentSaid("");
      /* Its sentence that they were not sent, joined to a note about
       * another study's words, goes too (CI-774 reviews L17 A, L18). */
      var shown = el("agent-unsent-note");
      var told = noteKind === "moved" && shown ? shown.textContent : "";
      if (told.slice(-SWAPPED.length) === SWAPPED) {
        var rest = told.slice(0, -SWAPPED.length).trim();
        unsentSaid(rest, "moved", noteAbout);
      }
    }
    /* The thread this was said in. Another shown before the answer came
     * stops the answer, which is said in this one, not the one on screen
     * (sixteenth review, 10-08: it was written into the other thread). */
    var mine = conv, saidHere = transcript, heardHere = history;
    function elsewhere() {
      if (conv === mine) return false;
      heardHere.push({ role: "agent", text: "(stopped before answering)" });
      saidHere.push({ role: "agent", kind: "answer", text: "Stopped before it finished." });
      persistOf(mine, saidHere);
      return true;
    }
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
    /* An answer is said only while its question stands: a retry or an
     * edit above cut it, and the answer, coming after, was saved with no
     * question (nineteenth review, 10-08). Nor once the page is going. */
    function stands() {
      if (answering === said) answering = null;
      return !leaving && saidHere.indexOf(said) >= 0;
    }
    answering = said;
    /* This reply's key: what the memory learns from the message is asked
     * for by it, and said under this reply. */
    var replyKey = "r-" + Date.now().toString(36) + "-" +
      Math.random().toString(36).slice(2, 8);
    propose({
      request: request,
      reply_key: replyKey,
      conversation: mine && mine.id ? String(mine.id) : null,
      records_question: asked,
      agent: el("agent-mode").value,
      history: history.slice(0, -1),
      current_config: currentConfig,
      attachments: files.map(function (f) { return { name: f.name, text: f.text, truncated: !!f.truncated }; }),
      current_view: pageHints(),
      steps: currentSteps
    }, box).then(function (data) {
      if (!stands()) return;
      if (data && !data.ok && data.code === "agent.view.changed") {
        viewChangedWords(mine, saidHere, heardHere, heard, said, bubble, r.node);
        return;
      }
      if (elsewhere()) return;
      box.innerHTML = "";
      if (data.memory === "reading") memoryUnder(r.node, replyKey);
      looked(box, data.looks);
      refusals(box, data.attempts, !!data.ok);
      sent(box, data.context_receipt);
      var receipt = data.context_receipt && data.context_receipt.kept
        ? data.context_receipt : null;
      /* The plan, where this reply kept one: shown under it, kept with it,
       * and the one sent with the next message. */
      var steps = stepsOf(data.steps);
      if (steps) currentSteps = steps;
      function withSteps(entry) { if (steps) entry.steps = steps; return entry; }
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
          transcript.push(withSteps({ role: "agent", kind: "question",
                            text: data.fix ? fixQuestion(data.fix)
                                           : data.refused || "Nothing here to run." }));
        } else if (confirmRunFirst) {
          transcript.push(withSteps({ role: "agent", kind: "question", confirm: "run",
                            text: runQuestion(), facts: runFacts() }));
        } else if (data.action === "stop") {
          /* Asked, not done. A stop is recorded when it is confirmed, so
           * a reloaded thread never says "Did: stop" about a run that was
           * never stopped -- which it did, and the person's "yes" then
           * went to the AI model as a new message. */
          transcript.push(withSteps({ role: "agent", kind: "question", confirm: "stop",
                            text: stopQuestion(data.where), facts: STOP_FACTS }));
        }
        /* A run is kept once it has started (or said why not), by the
         * launch: kept before, a run refused read back as "Did: run". */
        var done = null;
        if (data.action !== "stop" && data.action !== "run" && !data.fix && !data.refused &&
            ["run the fix", "rerun windows", "analyze again", "write the report again"]
              .indexOf(data.action) < 0) {
          done = withSteps({ role: "agent", kind: "action", action: data.action,
                             where: data.where || "", receipt: receipt });
          transcript.push(done);
        }
        persist();
        act(data.action, data.where || "", box, r, confirmRunFirst, data.fix || null,
            data.refused || "", done);
        stepsCard(box, steps);
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
        var answered = withSteps({ role: "agent", kind: "answer", text: data.answer,
                                   cites: data.cites || [], looks: data.looks || [],
                                   receipt: receipt, usage: data.usage || null });
        if (data.scene) answered.scene = data.scene;
        transcript.push(answered);
        sceneCard(box, data.scene, answered);
        stepsCard(box, steps);
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
        var asked = withSteps({ role: "agent", kind: "question", text: data.question,
                                asked: data.asked || null, choices: data.choices || [],
                                looks: data.looks || [], receipt: receipt,
                                usage: data.usage || null });
        stepsCard(box, steps);
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
      stepsCard(box, steps);
      var wrote = { role: "agent", kind: "config", yaml: data.yaml, config: data.config,
                        usage: data.usage || null,
                        note: data.note || null, version: made.number,
                        changes: data.changes || null,
                        plan: data.plan || [], looks: data.looks || [], receipt: receipt,
                        cycles: data.cycles, attempts: (data.attempts || []).map(function (a) {
                          return a.refusal ? { refusal: { message: a.refusal.message,
                                                          code: a.refusal.code || null } } : {};
                        }) };
      transcript.push(withSteps(wrote));
      meta(r, wrote);
      persist();
      scrollToEnd();
    }).catch(function (error) {
      if (!stands() || elsewhere()) return;
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
      /* Still waiting for its answer: if this failed because the page is
       * going, the page says so as it goes (above). */
      answering = said;
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
  var pendingReplies = 0;
  /* The question whose answer is being written. */
  var answering = null;

  /* The reply being written stopped, and the box free at once: a retry or
   * an edit sends its own straight after (nineteenth review, 10-08: it
   * waited in the box). */
  function stopWriting() {
    if (!writing) return;
    var was = writing;
    writing = null;
    writingState(false);
    was.abort();
  }

  function writingState(on) {
    setTimeout(placeholder, 0);
    var button = el("agent-propose");
    button.classList.toggle("is-writing", on);
    button.textContent = on ? "\u25a0" : "\u2191";
    sendReady();
  }

  /* Send stays quiet until the composer contains a nonblank message. */
  function sendReady() {
    var button = el("agent-propose");
    var area = el("agent-request");
    if (!button || !area) return;
    var on = button.classList.contains("is-writing");
    var blank = !on && !area.value.trim();
    /* Offered again once the page knows where it would be answered. */
    var waits = Boolean(!on && !blank && sendWaits() && !viewOnlyWaits());
    button.classList.toggle("is-blank", blank || waits);
    button.setAttribute("aria-disabled", String(blank || waits));
    // Named Send either way; aria-disabled and the title say why it waits.
    button.setAttribute("aria-label", on ? "Stop" : "Send");
    button.title = on ? "Stop the reply being written"
      : blank ? "Write a message to send it"
      : waits ? SEND_WAITS
      : "Send (Enter). Shift+Enter for a new line.";
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
    pendingReplies += 1;
    if (!window.ReadableStream || !window.AbortController || !window.TextDecoder) {
      writingState(true);
      return post("/api/agent/propose", body).finally(function () {
        pendingReplies -= 1;
        writingState(false);
      });
    }
    var mine = writing = new AbortController();
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
      pendingReplies -= 1;
      /* Not another reply's: one sent after this was stopped is writing. */
      if (writing === mine) {
        writing = null;
        writingState(false);
      }
      setTimeout(redrawIfWanted, 0);
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
  /* Runs being started from this page, until each launch has answered. */
  var launching = 0;
  /* The run entries of launches not yet answered. */
  var startingRuns = [];

  /* The newest run of a version, from the transcript. */
  function latestRunOf(number) {
    for (var i = transcript.length - 1; i >= 0; i--) {
      var e = transcript[i];
      if (e.kind === "action" && e.action === "run" && e.version === number) return e;
    }
    return null;
  }

  /* A run ended: its version can be run again, and its line no longer
   * offers Stop. With `only` (a run's transcript entry), that run's line
   * alone, and its version's button unless the version was run again
   * since (fifth and sixth reviews, 10-07). */
  function runEnded(only) {
    versions.forEach(function (v) {
      if (only && (v.number !== only.version || latestRunOf(v.number) !== only)) return;
      var button = v.r.part("run");
      if (button && button.textContent === "Running") {
        button.textContent = "Run again";
        button.disabled = false;
      }
    });
    Array.prototype.forEach.call(document.querySelectorAll("#agent-thread .agent-running"),
      function (line) {
        if (only && line.getAttribute("data-started") !== String(only.started || "")) return;
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
        sendPressed(b[1], function () { button.classList.add("is-chosen"); });
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
    /* Whose line it is: a run's summary ends its own line alone. */
    line.setAttribute("data-started", String(started || ""));
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
    /* The thread the run is in: another may be shown before the answer
     * (seventeenth and eighteenth reviews, 10-08). */
    var c = conv, t = transcript, h = history;
    post("/api/agent/run-summary", { study: run.output }).then(function (s) {
      summaryAsked = false;
      if (s && !s.ok) {
        // No study there any more: not asked again.
        run.no_summary = true;
        persistOf(c, t);
        return;
      }
      if (!s || !s.ended) return;
      /* One summary of a run, whichever tab asked: two tabs' copies are
       * one entry (eighteenth review, 10-08: both kept their own). */
      var eid = "sum-" + (run.eid || run.started || run.output);
      /* The server's word for that folder: this run has ended, whatever
       * else runs. A run that ended before a poll saw it going stayed
       * "Running", with Stop, and the next run was refused (second
       * review, 10-07); its own line alone, as a later run may be going
       * (third to sixth reviews, 10-07). */
      var entry = { eid: eid, role: "agent", kind: "summary", study: run.output, head: s.head,
                    status: s.status, found: s.found, long_enough: s.long_enough,
                    strengthen: s.strengthen };
      if (conv !== c) {
        /* Not on screen: kept with its thread, not drawn. */
        if (!t.some(function (e) { return e.eid === eid; })) t.push(entry);
        persistOf(c, t);
        return;
      }
      runEnded(run);
      if (runAwaitingSummary() !== run) return;
      var r = reply();
      whoIs(r, STOPPED_AS.test(s.status || "") ? "stopped" : "done");
      summaryCard(r.part("attempts"), s);
      t.push(entry);
      h.push({ role: "agent", text: s.head + ".\n" + [s.found, s.long_enough,
               s.strengthen].filter(Boolean).join("\n") });
      persistOf(c, t);
      placeholder();
      scrollToEnd();
    }).catch(function () { summaryAsked = false; });
  }

  /* The message box's words follow the situation (user, 10-07: "This
   * default should be adaptive"). */
  var studyState = "none";
  /* On the Agent page the full prompt fits; beside a page it is shortened
   * for the narrower drawer. Keep each pair in sync with the two layouts. */
  var PROMPTS = {
    writing: ["I am working on it; you can write your next message", "Write your next message"],
    answer: ["Answer above, or type yes or no", "Answer above: yes or no"],
    pick: ["Pick one above, or type your answer", "Pick one above, or type it"],
    question: ["Type your answer", "Type your answer"],
    study: ["Change something, or say run it", "Change it, or say run it"],
    running: ["Ask how it is going, or tell me to stop it", "Ask how it is going"],
    stopped: ["Ask why it stopped, or what would fix it", "Ask why it stopped"],
    finished: ["Ask about this study, or describe the next one", "Ask, or describe a study"],
    none: ["Describe a study, or ask about one in this folder", "Ask, or describe a study"],
  };

  function placeholder() {
    var area = el("agent-request");
    if (!area) return;
    var thread = el("agent-thread");
    var nodes = thread ? thread.querySelectorAll(".agent-msg") : [];
    var last = nodes.length ? nodes[nodes.length - 1] : null;
    var said;
    if (writing) {
      said = "writing";
    } else if (stopPending || runPending || fixPending) {
      said = "answer";
    } else if (last && last.querySelector(".agent-choice:not(:disabled)")) {
      said = "pick";
    } else if (last && last.querySelector(".agent-question")) {
      said = "question";
    } else if (last && last.querySelector(".agent-study:not([hidden]):not(.is-replaced)")) {
      said = "study";
    } else if (studyState === "running") {
      said = "running";
    } else if (studyState === "stopped") {
      said = "stopped";
    } else if (studyState === "finished") {
      said = "finished";
    } else {
      said = "none";
    }
    var beside = document.body.classList.contains("agent-beside");
    area.placeholder = PROMPTS[said][beside ? 1 : 0];
    if (!area.value && area.offsetParent !== null) autosize(area);
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
    var c = conv, t = transcript, h = history;
    fetch(fix.route || "/api/fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fix.request || { index: fix.index })
    }).then(function (res) { return res.json(); }).then(function (d) {
      var started = d && d.ok;
      var said = started ? "Started: " + fix.command + "." : (d && d.error) || "Could not start it.";
      note(box, said, started);
      h.push({ role: "agent", text: said });
      t.push(started ? { role: "agent", kind: "action",
                         action: fix.action || (fix.request ? "rerun windows" : "run the fix"),
                         where: "" }
                     : { role: "agent", kind: "error", text: said });
      persistOf(c, t);
      if (started && window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
        window.FastMDXDashboard.navigate("overview");
      }
    }).catch(function () { note(box, "Could not reach the server to run it."); });
  }

  function confirmRun(typed, box) {
    runPending = null;
    if (!saidYes(typed, "run")) {
      note(box, "Not run.");
      /* Said to the Agent as a reloaded thread says it: the run it named
       * was declined (`agent/context.py`). */
      history.push({ role: "agent", text: "Not run." });
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
    /* Said in the thread it was asked in, whatever is on screen when the
     * stop answers (eighteenth review, 10-08). */
    var c = conv, t = transcript, h = history;
    fetch("/api/explore/stop", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var stopped = d && d.ok !== false;
        note(box, stopped ? "Stopped the run." : (d && d.error) || "Could not stop it.", true);
        h.push({ role: "agent", text: stopped ? "Stopped the run." : "Could not stop the run." });
        t.push(stopped ? { role: "agent", kind: "action", action: "stop", where: "" }
                       : { role: "agent", kind: "error", text: "Could not stop the run." });
        persistOf(c, t);
        if (stopped && lastReply && conv === c) {
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
    r.part("review").hidden = true;
    v.review = null;
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
    if (v.review && v.review.rows) drawReview(v, v.review);
    else offerReview(v);
  }

  function newVersion(r, data, number) {
    var v = { r: r, data: data, number: number || versions.length + 1,
              changes: data.changes || null };
    var previous = versions[versions.length - 1];
    v.previous = previous || null;
    /* Proposed by the Agent: not a version the person made from choices
     * or from an earlier version. */
    v.proposed = !!previous && !data.review && data.again == null;
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
    /* After the thread in hand is drawn: on a restore only the newest is
     * read. */
    Promise.resolve().then(function () { offerReview(v); });
    return v;
  }

  /* Each change of a version the Agent proposed, old value to new, with
   * Accept and Reject beside Use this version. Use this version takes the
   * proposal as it is; once a change is rejected, Use reviewed version
   * makes a new version from the choices, and only through the validator.
   * The two Configs compared are never edited in place. Choices not yet
   * applied are kept in this browser by conversation, version and the
   * changes themselves, so a reload keeps them and another proposal given
   * the same version number never takes them; a draft is never written as
   * a Config. */
  var REVIEW_KEY = "fmx.reviewChoices.";

  /* A short digest of a text (53-bit, cyrb53), to name a draft by what it
   * is about. */
  function digest(text) {
    var h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (var i = 0; i < text.length; i++) {
      var c = text.charCodeAt(i);
      h1 = Math.imul(h1 ^ c, 2654435761);
      h2 = Math.imul(h2 ^ c, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
  }

  /* Each change shown, old and new values and reasons, is part of the key:
   * a version number used again in the same conversation is another
   * proposal (round 2 review). */
  function reviewKey(review, v) {
    var c = review.conv;
    if (!c.id || !review.rows) return null;
    return REVIEW_KEY + JSON.stringify([c.study || null, c.id, v.number,
                                        digest(JSON.stringify(review.rows))]);
  }

  function keptChoices(review, v) {
    var key = reviewKey(review, v);
    if (!key) return null;
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; }
  }

  function keepChoices(review, v, kept) {
    var key = reviewKey(review, v);
    if (!key) return;
    try {
      if (kept) localStorage.setItem(key, JSON.stringify(kept));
      else localStorage.removeItem(key);
    } catch (e) { /* not kept: the review still works */ }
  }

  function rejectedIn(review) {
    return (review.rows || []).filter(function (c) { return review.choices[c.setting] === "reject"; });
  }

  /* A rejected change not yet applied holds Run and the next message: either
   * would act on the proposal, not on what was chosen. */
  function reviewPending() {
    var v = newest();
    return !!(v && v.review && v.review.rows && rejectedIn(v.review).length);
  }

  function heldByReview(doing) {
    if (!reviewPending()) return false;
    var v = newest();
    v.review.note.textContent = "Use reviewed version, or Use this version to keep version "
      + v.number + " as proposed, before " + doing + ".";
    v.review.note.scrollIntoView({ block: "nearest" });
    return true;
  }

  /* The note that a button's message waits for the review, said no more
   * once nothing is held by it (CI-774 review L15: it stayed after the
   * choice was made). */
  function reviewNoteDone() {
    var host = el("agent-unsent-note");
    if (noteKind === "moved" && host && host.textContent === REVIEW_NOT_SENT && !reviewPending()) {
      unsentSaid("");
    }
  }

  function counted(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  function eitherOf(names) {
    return names.length < 2 ? names.join("")
      : names.slice(0, -1).join(", ") + " or " + names[names.length - 1];
  }

  /* Read the changes one by one from the server, for the newest proposal. */
  function offerReview(v) {
    if (!v.proposed || v.review || v !== newest()) return;
    var mine = conv;
    var review = v.review = { conv: mine, rows: null, choices: Object.create(null),
                              refusal: null, busy: false };
    review.stands = function () {
      return !leaving && conv === mine && v === newest() && v.review === review
        && v.r.node.isConnected;
    };
    var compared = review.compared = { before: v.previous.data.config, after: v.data.config };
    function unavailable(why) {
      if (!review.stands()) return;
      var said = document.createElement("div");
      said.className = "agent-change-rest";
      said.textContent = "Accept and Reject are not available here: "
        + (why || "the changes could not be read one by one.");
      v.r.part("change").appendChild(said);
    }
    post("/api/agent/review-config", compared).then(function (answer) {
      if (!review.stands()) return;
      if (!answer || !answer.ok || !Array.isArray(answer.changes)) {
        unavailable(answer && answer.error);
        return;
      }
      /* Nothing to choose: the version's summary says so on its own. */
      if (!answer.changes.length) return;
      review.rows = answer.changes;
      drawReview(v, review);
    }).catch(function () { unavailable(""); });
  }

  /* The version's change list, each row with Accept and Reject, in place of
   * its summary; below it Accept all, Reject all, Use this version and Use
   * reviewed version. */
  function drawReview(v, review) {
    var r = v.r;
    var compared = review.compared;
    var host = r.part("review");
    var kept = keptChoices(review, v);
    if (kept && kept.choices) {
      var all = true;
      Object.keys(kept.choices).forEach(function (setting) {
        var k = kept.choices[setting];
        var row = review.rows.filter(function (c) { return c.setting === setting; })[0];
        /* Only a choice about the very change shown, should two drafts
         * ever share a key. */
        if (row && k && k.before === row.before && k.after === row.after
            && (k.choice === "accept" || k.choice === "reject")) {
          review.choices[setting] = k.choice;
        } else {
          all = false;
        }
      });
      if (all && kept.refusal) review.refusal = kept.refusal;
    }
    var controls = [];
    var list = document.createElement("div");
    list.className = "agent-review-list";
    var rowsBy = Object.create(null);
    function line(into, label, before, after) {
      var part = document.createElement("div");
      part.className = "agent-change-row";
      part.appendChild(document.createTextNode(label + " "));
      var was = document.createElement("span");
      was.className = "was";
      was.textContent = before;
      var arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.textContent = "→";
      var now = document.createElement("span");
      now.className = "now";
      now.textContent = after;
      part.append(was, arrow, now);
      into.appendChild(part);
    }
    review.rows.forEach(function (change) {
      var row = document.createElement("div");
      row.className = "agent-review-row";
      row.setAttribute("role", "group");
      row.setAttribute("aria-label", change.label);
      row.dataset.reviewSetting = change.setting;
      var what = document.createElement("div");
      what.className = "agent-review-what";
      if (change.same_value) {
        var same = document.createElement("div");
        same.className = "agent-change-row";
        same.textContent = change.label + " " + change.after + ", the same; its reason changed.";
        what.appendChild(same);
      } else if (Array.isArray(change.parts)) {
        var whole = document.createElement("div");
        whole.className = "agent-review-label";
        whole.textContent = change.label;
        what.appendChild(whole);
        change.parts.forEach(function (p) { line(what, p.label, p.before, p.after); });
      } else {
        line(what, change.label, change.before, change.after);
      }
      if (Array.isArray(change.alias_labels)) {
        var aliases = document.createElement("div");
        aliases.className = "agent-review-reasons";
        aliases.textContent = change.alias_labels.join(" and ")
          + " are two names for one setting, reviewed together.";
        what.appendChild(aliases);
      }
      if (Object.prototype.hasOwnProperty.call(change, "after_reason")) {
        var reasons = document.createElement("div");
        reasons.className = "agent-review-reasons";
        reasons.textContent = "Reason: " + change.before_reason + " → " + change.after_reason;
        what.appendChild(reasons);
      }
      var choose = document.createElement("div");
      choose.className = "agent-review-choice";
      ["accept", "reject"].forEach(function (choice) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "ctl-btn";
        button.textContent = choice === "accept" ? "Accept" : "Reject";
        button.setAttribute("aria-label", button.textContent + " " + change.label);
        button.dataset.changeId = change.id;
        button.dataset.choice = choice;
        button.onclick = function () { chose(function () { review.choices[change.setting] = choice; }); };
        controls.push(button);
        choose.appendChild(button);
      });
      row.append(what, choose);
      rowsBy[change.setting] = row;
      list.appendChild(row);
    });
    var rest = document.createElement("div");
    rest.className = "agent-change-rest";
    rest.textContent = "Everything else as version " + v.previous.number + ".";
    var refusal = document.createElement("div");
    refusal.className = "agent-review-refusal";
    refusal.setAttribute("role", "alert");
    var note = review.note = document.createElement("p");
    note.className = "agent-review-note";
    note.setAttribute("role", "status");
    var bar = document.createElement("div");
    bar.className = "agent-review-actions";
    ["accept", "reject"].forEach(function (choice) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "ctl-btn";
      button.textContent = choice === "accept" ? "Accept all" : "Reject all";
      button.onclick = function () {
        chose(function () { review.rows.forEach(function (c) { review.choices[c.setting] = choice; }); });
      };
      controls.push(button);
      bar.appendChild(button);
    });
    var keep = document.createElement("button");
    keep.type = "button";
    keep.className = "ctl-btn";
    keep.textContent = "Use this version";
    keep.title = "Use version " + v.number + " as proposed, every change accepted";
    var apply = document.createElement("button");
    apply.type = "button";
    apply.className = "primary-btn";
    apply.textContent = "Use reviewed version";
    apply.title = "Make a new version from your choices, checked first";
    var uses = document.createElement("div");
    uses.className = "agent-review-uses";
    uses.append(keep, apply);
    bar.appendChild(uses);
    var change = r.part("change");
    change.replaceChildren(list, rest);
    change.hidden = false;
    host.replaceChildren(refusal, bar, note);
    host.hidden = false;

    function save() {
      var settings = Object.keys(review.choices);
      if (!settings.length) { keepChoices(review, v, null); return; }
      var choices = {};
      settings.forEach(function (setting) {
        var row = review.rows.filter(function (c) { return c.setting === setting; })[0];
        choices[setting] = { choice: review.choices[setting], before: row.before, after: row.after };
      });
      keepChoices(review, v, { choices: choices, refusal: review.refusal });
    }
    function update() {
      var rejected = rejectedIn(review).length;
      apply.disabled = review.busy || !rejected;
      keep.disabled = review.busy;
      controls.forEach(function (b) {
        b.disabled = review.busy;
        if (b.dataset.choice) {
          var setting = review.rows[Number(b.dataset.changeId)].setting;
          b.setAttribute("aria-pressed", String(review.choices[setting] === b.dataset.choice));
        }
      });
      var involved = (review.refusal && review.refusal.involved) || [];
      Object.keys(rowsBy).forEach(function (setting) {
        rowsBy[setting].classList.toggle("is-refused", involved.indexOf(setting) >= 0);
      });
      refusal.hidden = !review.refusal;
      if (review.refusal) {
        var labels = involved.map(function (s) {
          return review.rows.filter(function (c) { return c.setting === s; })[0].label;
        });
        refusal.textContent = "These choices cannot be used together. " + review.refusal.error
          + (labels.length ? " Change your choice for " + eitherOf(labels) + "." : "");
      }
      reviewNoteDone();
      if (review.busy) return;
      note.textContent = rejected
        ? counted(rejected, "change") + " rejected. Use reviewed version makes a new version without "
          + (rejected === 1 ? "it" : "them") + "; the other changes stay as proposed."
        : "Version " + v.number + " as proposed is the one in use. Reject a change to make a version without it.";
    }
    function chose(how) {
      if (!review.stands() || review.busy) return;
      how();
      review.refusal = null;
      save();
      update();
    }
    /* As Use this version on an older version: said in the conversation
     * and made the newest version, here with every change accepted. */
    keep.onclick = function () {
      if (!review.stands() || review.busy || writing) return;
      keepChoices(review, v, null);
      useVersion(v, review);
      reviewNoteDone();
    };
    apply.onclick = function () {
      if (!review.stands() || writing || apply.disabled) return;
      review.busy = true;
      update();
      note.textContent = "Checking the selected Config...";
      var accepted = review.rows.filter(function (c) { return review.choices[c.setting] !== "reject"; })
        .map(function (c) { return c.id; });
      function free(words) {
        review.busy = false;
        update();
        if (words) note.textContent = words;
      }
      post("/api/agent/review-config", Object.assign({}, compared, { accepted: accepted })).then(function (checked) {
        if (!review.stands()) return;
        if (!checked.ok) {
          var ids = checked.involved || [];
          review.refusal = { error: checked.error || "These choices cannot be used together.",
                             involved: review.rows.filter(function (c) { return ids.indexOf(c.id) >= 0; })
                               .map(function (c) { return c.setting; }) };
          save();
          free("No new version was made. Your choices are kept.");
          refusal.scrollIntoView({ block: "nearest" });
          return;
        }
        review.choices = Object.create(null);
        review.refusal = null;
        keepChoices(review, v, null);
        stopPending = null; runPending = null; fixPending = null;
        spendChoices();
        var receipt = checked.review;
        var asked = "Reviewed version " + v.number + ". " + receipt.said;
        say(asked, []);
        history.push({ role: "user", text: asked });
        transcript.push({ role: "user", text: asked });
        checked.note = "Your choices from version " + v.number + ", checked as a new Config.";
        var r2 = reply();
        whoIs(r2, "done");
        saidBeside(r2.part("attempts"), checked.note);
        currentConfig = checked.yaml;
        history.push({ role: "agent", text: checked.note + "\n\nWrote a config:\n" + checked.yaml });
        var made = wireActions(r2, checked, r2.part("attempts"));
        transcript.push({ role: "agent", kind: "config", yaml: checked.yaml, config: checked.config,
          plan: checked.plan, changes: checked.changes, note: checked.note, version: made.number,
          review: { base: v.previous.number, proposal: v.number, accepted: receipt.accepted,
                    rejected: receipt.rejected }, cycles: 1, attempts: [] });
        persist();
        reviewNoteDone();
        scrollToEnd();
      }).catch(function () {
        if (review.stands()) free("Could not check the choices. Try again.");
      });
    };
    update();
    if (review.refusal) note.textContent = "No new version was made. Your choices are kept.";
  }

  /* An older version made the newest again: written into the conversation
   * as the person's choice and as a new version of the same config, so the
   * next "make it 5 ns" changes it, and Run runs it. Nothing is asked of
   * the Agent's AI model. From a review, the proposal as it is: its note
   * and record say every change was accepted. */
  function acceptedAll(rows) {
    return "Accepted: " + rows.map(function (c) {
      return c.same_value ? c.label + " (its reason)" : c.label + " " + c.before + " \u2192 " + c.after;
    }).join("; ") + ".";
  }

  function useVersion(v, review) {
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
    var asProposed = review && review.rows
      ? { base: v.previous.number, proposal: v.number,
          accepted: review.rows.map(function (c) { return c.setting; }), rejected: [] }
      : null;
    var again = { yaml: v.data.yaml, config: v.data.config, plan: v.data.plan,
                  note: asProposed ? "Version " + v.number + " as proposed, again. " + acceptedAll(review.rows)
                                   : "Version " + v.number + ", again.",
                  changes: null, again: v.number, review: asProposed };
    saidBeside(r.part("attempts"), again.note);
    history.push({ role: "agent", text: again.note + "\n\nWrote a config:\n" + again.yaml });
    currentConfig = again.yaml;
    var made = wireActions(r, again, r.part("attempts"));
    var entry = { role: "agent", kind: "config", yaml: again.yaml, config: again.config,
                  note: again.note, plan: again.plan || [], cycles: 1, attempts: [],
                  version: made.number, again: v.number };
    if (asProposed) entry.review = asProposed;
    transcript.push(entry);
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
        config: data.config,
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
      if (made !== newest() || heldByReview("running it")) return;
      here = here || box;
      runBtn.disabled = true;
      /* The thread is saved before the launch, and the launch is told
       * which conversation to move and where it is now: the server moves
       * it as the run starts, and says where it went (tenth review, 10-08:
       * moved by the page afterwards, it raced the page's own saves and
       * its switch to the new study, and a slow disk lost the thread).
       * The thread that started it is the one it is said in: the person
       * may open another, or a new one, before the launch answers. */
      var c = conv, sent = transcript, said = history;
      /* The run's entry goes in first, saved before the run is asked for;
       * the server writes where the run went into it as it moves the
       * thread, so a reload while the run starts keeps the run. Other
       * saves do not wait for the launch (twelfth to fourteenth reviews,
       * 10-08). */
      if (!entry) {
        entry = { role: "agent", kind: "action", action: "run", where: "" };
        sent.push(entry);
      }
      entry.version = made.number;
      entry.eid = entry.eid || newEid();
      launching += 1;
      var dashboard = window.FastMDXDashboard;
      if (dashboard && dashboard.studyChangeAsked) dashboard.studyChangeAsked();
      var boxAtRun = boxNow();
      startingRuns.push(entry);
      function answered(started) {
        launching -= 1;
        var runKey = started && started.ok && validStudyKey(started.study_key) ? started.study_key : "";
        /* The study the run went to is this window's own change; no other
         * is. An answer not read says nothing of where it went. */
        if (dashboard && dashboard.studyChangeAnswered) {
          dashboard.studyChangeAnswered(started === undefined ? null : runKey);
        }
        var took = started && started.conversation;
        launchAnswered(started === undefined ? null : runKey,
                       Boolean(c === conv && boxNow().trim() && boxNow() !== boxAtRun
                               && (started === undefined || (took && took.ok && took.moved))),
                       started && started.output);
        sendWaitLater();
        startingRuns = startingRuns.filter(function (e) { return e !== entry; });
        /* Said where the person can see it: the reply it was asked in may
         * have been cut by a retry while it started. */
        if (!here.isConnected && c === conv && sent === transcript) {
          var again = reply();
          whoIs(again, "done");
          here = again.part("attempts");
        }
      }
      persistOf(c, sent).then(function () {
        /* After the saves queued behind it: a thread deleted elsewhere is
         * kept anew by one, and the run takes that one with it
         * (twenty-first review, 10-08). One of them may open another
         * study: its reload waits for the run (`reloadOnceLaunched`). */
        return saving;
      }).then(function () {
        return post("/api/agent/run", {
          config: data.config,
          budget_hours: el("agent-budget").value,
          conversation: c.id ? { id: c.id, study: c.study || null, run: entry.eid } : null
        });
      }).then(function (started) {
        answered(started);
        var moved = started && started.conversation;
        if (moved && moved.ok && moved.id) {
          c.id = moved.id;
          /* Moved by the run into its folder: where it is now is the
           * server's word, as for a save's answer (persistOf). Named for
           * the study left, the next save was refused, and the read of the
           * run's study put the thread back without what was said as the
           * run started. */
          if ((moved.study || started.output || null) !== (c.study || null)) {
            c.page = "";
            /* The thread is the run's study's now, and its saves are named
             * so, as a page reading it there would name them. A page that
             * goes on showing another study (opened again elsewhere as the
             * run started) reads that study's own thread. */
            c.page = validStudyKey(started.study_key) ? started.study_key : "";
            c.ranTo = c.page;
            if (c === conv) {
              convFor = c.page;
              var from = shownStudy();
              launchedAway = convFor && from !== convFor ? { key: convFor, from: from } : null;
            }
          }
          c.study = moved.study || started.output;
          if (validStudyKey(moved.study_key)) c.page = moved.study_key;
          if (c === conv) {
            // The launch already moved this conversation. Its old page's
            // key must not schedule a replacement read over newer messages.
            convFor = validStudyKey(moved.study_key) ? moved.study_key : "";
            readAgain = false;
          }
          if (c.study) c.standsIn = false;
        }
        if (started.ok) {
          /* Started once. A second press started it again into the same
           * folder and was refused for the folder being occupied. The
           * button says what happened and stays put. */
          runBtn.textContent = "Running";
          var at = new Date().toISOString();
          runLine(here, made.number, at, true);
          said.push({ role: "agent", text: "Started version " + made.number + "." });
          entry.started = at;
          entry.output = started.output || null;
          if (sent.indexOf(entry) < 0) {
            /* An edit or a retry above it cut the run's entry while the
             * run started: the run started all the same, so it is said
             * again, under a new eid (the cut one stays cut; eighteenth
             * review, 10-08). */
            entry.eid = newEid();
            sent.push(entry);
          }
          persistOf(c, sent);
          if (moved && moved.moved) {
            note(here, "This conversation now belongs to the new study.", true);
          }
        } else {
          /* Not started: no run in the thread, and why. */
          var placed = sent.indexOf(entry);
          if (placed >= 0) sent.splice(placed, 1);
          runBtn.disabled = false;
          noteEl.textContent = started.error;
          if (here !== box) note(here, started.error || "It did not start.");
          sent.push({ role: "agent", kind: "error", text: started.error || "It did not start." });
          persistOf(c, sent);
          runFix(r.part("fix"), started, data);
        }
        redrawIfWanted();
      }, function () {
        /* No answer the page could read (an error page, a dropped
         * connection): the run may have started, so it is not said that
         * it did not; where it went is filled in by the server if it did
         * (thirteenth and fourteenth reviews, 10-08). A page going as it
         * started (a reload) has nothing to say: the next one reads where
         * the run went (nineteenth review, 10-08). */
        function cannotRead() {
          if (leaving) { launching -= 1; return; }
          answered();
          runBtn.disabled = false;
          var why = "I could not read the software's answer to starting the run. " +
                    "If it started, the Overview shows it running.";
          note(here, why);
          sent.push({ role: "agent", kind: "answer", text: why });
          persistOf(c, sent);
          redrawIfWanted();
        }
        /* Failed as the page was asked to go: said only if the page is
         * still here a moment later (a leave cancelled, a download), never
         * once it has gone. */
        if (askedToGo && !leaving) {
          setTimeout(function () { askedToGo = false; cannotRead(); }, 3000);
          return;
        }
        cannotRead();
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
      var asked = "Running it was refused: " + (started.error || remedy.why || "") +
        "\nWhat would fix it: " + (remedy.fix || "") + "\nChange the config so it runs.";
      sendPressed(asked, function () {
        ask.disabled = true;
        /* A new instruction, so nothing still waiting for a yes takes it. */
        stopPending = null; runPending = null; fixPending = null;
        currentConfig = data.yaml;
      });
    });
    host.appendChild(ask);
  }

  /* Another thread on screen: a reply still being written for this one is
   * stopped (and said in this one), and saves still waiting keep the
   * thread they were for. */
  function clearThread() {
    stopWriting();
    var edits = keepEditsLeft();
    el("agent-thread").innerHTML = "";
    conv = threadOf(null, conv.study, []);
    history = []; transcript = []; currentConfig = null; currentSteps = null;
    stopPending = null; runPending = null; fixPending = null; lastReply = null;
    versions = [];
    placeholder();
    /* No review on screen holds the next message now. */
    reviewNoteDone();
    edits();
  }

  /* The same thread drawn again, as the server keeps it: the arrays stay
   * the ones the thread's saves and launches hold. */
  function redraw(entries) {
    var keep = entries.slice();
    var edits = keepEditsLeft();
    el("agent-thread").innerHTML = "";
    transcript.length = 0;
    history.length = 0;
    currentConfig = null;
    currentSteps = null;
    stopPending = null; runPending = null; fixPending = null; lastReply = null;
    versions = [];
    replay(keep);
    placeholder();
    reviewNoteDone();
    edits();
  }

  /* Draw a saved thread again. Each entry renders the way it rendered
   * the first time; a config gets its actions back, wired to the stored
   * config, so Run on this machine on a restored thread runs what was written. */
  var threadRead = false;
  /* The study whose conversation was read, by key; and whether a read was
   * refused as for another study than the GUI has open. */
  var convFor = "";
  var readAgain = false;
  /* The thread on screen as a run took it to its study (`key`), with the
   * study the page shows instead (`from`): kept until the page shows the
   * run's study. Once `from`'s own thread is read in place of the run's,
   * the page says where the run's thread is (CI-774 reviews K6 and L3). */
  var launchedAway = null;
  /* The page's own study's thread read in place of one a run took to a
   * study this page never showed: no word typed here goes there, nor is
   * its opening this window's own, and where the run's thread is is said
   * beside what is already said there. */
  function readAway() {
    var away = launchedAway;
    if (!away || away.from === away.key || convFor !== away.from) return;
    launchedAway = null;
    runStudies = runStudies.filter(function (r) { return r.key !== away.key; });
    var dashboard = window.FastMDXDashboard;
    if (dashboard && dashboard.studyChangeNotComing) dashboard.studyChangeNotComing(away.key);
    var host = el("agent-unsent-note");
    var before = noteKind === "moved" && host ? host.textContent : "";
    unsentSaid((before ? before + " " : "") + "The run's conversation is in the run's study, "
               + "not this one: open the run's study to go on with it.", "moved", away.key);
  }
  /* Send waits while it cannot be known where a message would be answered
   * and kept: while a launch of this page is unanswered, before the page
   * has read which study is open (at first, and once every launch
   * answered), while a run this page started is not yet a study, while
   * the thread of the study shown is being read, while the thread on
   * screen is another study's (one a run took away, or the one before a
   * study changed under the page) or a chat of no study read before a
   * study was opened, and before the page's view of the study shown is
   * read after the status that showed it. No study open is an answer too:
   * the page then reads the workspace's chats. Send and Enter then
   * send nothing: they say so beside the box, the words stay there (kept
   * per study as any unsent words), and once the page knows, Send is
   * offered again for the person to press. Nothing is ever sent later by
   * itself (CI-774 reviews L1 to L12: a message held to be sent later was
   * lost, sent twice, sent into another thread or study, or held every
   * switch of the window). `sendWaits` says why: "run", "read", "view"
   * (only the view is left to read), or "" when a message can go. */
  var SEND_WAITS = "Send waits until the run has started and this page shows which study is open.";
  var SEND_READS = "Send waits until this page has read the study open and its conversation.";
  var VIEW_READS = "Sent once this page has read the study's view, if nothing changes meanwhile.";
  function sendWaits() {
    var dashboard = window.FastMDXDashboard;
    var dash = (dashboard && dashboard.state) || {};
    if (launching > 0 || dash.launchesAsked > 0) return "run";
    if (dashboard && dashboard.studyKnown && !dashboard.studyKnown()) {
      return dash.answeredAt ? "run" : "read";
    }
    /* A read refused as the GUI had moved on: the study open and its
     * thread are read again first (CI-774 review L13: Enter went with the
     * view of the study left). */
    if (!threadRead || reading > 0 || readAgain) return "read";
    /* No study open (a run that failed at once leaves none): the study
     * the page last showed is no thread's or view's to wait for (CI-774
     * review L11: Send never sent again). */
    var shown = shownStudy();
    if (conv.ranTo && shown && conv.ranTo !== shown) return "run";
    /* A run going in the folder shown that is not yet a study (a run of
     * this page), or for which a chat of no study stood in for its thread,
     * in any window: the run has not started (CI-774 reviews L10 to L12).
     * Only while it goes: one that ended first leaves a folder that is no
     * study, and Send goes as for any (reviews L13, L14: it waited for good,
     * saying a run was starting). */
    var going = Boolean(dash.appState && dash.appState.process_running);
    var ours = Boolean(shown && dashboard.launchedHere && dashboard.launchedHere(shown));
    if (going && shown && ((ours && dash.studyIsOne === false) || conv.unread === shown)) return "run";
    /* The thread on screen is the study's shown: a study's other than it
     * (read before the study changed), or a chat of no study the page put
     * up for a folder that was not yet a study, waits for its own. */
    if (conv.study && conv.page && shown && conv.page !== shown) return "read";
    /* So does a chat of no study read for where the GUI was, once a study
     * is shown there (CI-774 review L15). */
    if (conv.standsIn && !conv.study && shown && dash.studyIsOne !== false) return "read";
    if (shown && dashboard.viewKnown && !dashboard.viewKnown()) return "view";
    return "";
  }
  /* All the page waits for is its view read after the study's status. */
  function viewOnlyWaits() { return sendWaits() === "view"; }
  /* What Send says and offers now; `asked`: Send or Enter was pressed. */
  function sendWaitSaid(asked) {
    var why = sendWaits();
    var text = why === "run" ? SEND_WAITS : why === "read" ? SEND_READS : "";
    if (text && (asked || noteKind === "waits")) unsentSaid(text, "waits");
    else if (!why && noteKind === "waits") unsentSaid("");
    var dashboard = window.FastMDXDashboard;
    if (asked && dashboard && dashboard.readStatusNow
        && ((why === "run" && dashboard.studyKnown && !dashboard.studyKnown())
            || (why === "read" && (readAgain || conv.standsIn)))) {
      dashboard.readStatusNow();
    }
    /* The view is read meanwhile, whatever else is still waited for. */
    if ((why === "view" || why === "read") && dashboard && dashboard.readView && dashboard.viewKnown
        && !dashboard.viewKnown() && dashboard.studyKnown && dashboard.studyKnown()) {
      dashboard.readView();
    }
    sendReady();
  }
  /* Whatever may end the wait: a status read (once every listener has
   * heard it), the view read, a conversation read, a launch's answer. */
  function sendWaitLater() { window.setTimeout(function () { sendWaitSaid(false); }, 0); }
  window.addEventListener("dashboard:status-updated", sendWaitLater);
  window.addEventListener("dashboard:view-read", sendWaitLater);
  /* A button that sends words of its own (a choice, a confirm answer,
   * Retry, an Edit, a fix asked for, a starter): while Send waits it says
   * so and changes nothing (no thread cut, no choice spent); else `before`
   * is done and its words are sent as they are, the box and what the
   * person typed in it left as they are (CI-774 reviews L10, L11, L12: it
   * wrote over the words kept in the box). */
  /* `cuts`: `before` cuts the thread from the message pressed, which
   * stops an answer being written to it. Every refusal `draft` makes is
   * asked first, and said: refused after `before`, the thread was cut or a
   * choice spent and nothing sent (CI-774 reviews L13, L14: under a
   * review's hold). */
  var WRITING_NOT_SENT = "Not sent: an answer is still being written. Press it again once it is done, "
    + "or stop it first.";
  var REVIEW_NOT_SENT = "Not sent: choose in the review above first, as it says.";
  /* Whether a button's message is refused now, and why, said. */
  function refusedNow(cuts) {
    if (writing && !cuts) {
      unsentSaid(WRITING_NOT_SENT, "moved", "");
      return true;
    }
    if (heldByReview("sending another message")) {
      unsentSaid(REVIEW_NOT_SENT, "moved", "");
      return true;
    }
    if (sendWaits()) {
      unsentSaid(sendWaits() === "run" ? SEND_WAITS : SEND_READS, "waits");
      sendReady();
      return true;
    }
    return false;
  }
  /* `files`: the files the message was sent with (Retry, Edit), read
   * again and sent with it, as they were (CI-774 review L16: they were
   * dropped); one gone or changed since is said, and nothing sent. Read
   * again, the press is asked again, and `refused` is told when it is
   * refused then. */
  function sendPressed(text, before, cuts, files, refused) {
    if (!String(text || "").trim()) return false;
    if (refusedNow(cuts)) return false;
    function go(attached) {
      if (before) before();
      draft(text, attached);
      if (boxNow().trim()) {
        unsentSaid("The words in the box are kept, not sent with it: send them with Enter.", "moved", "");
      }
    }
    if (!files || !files.length) {
      go([]);
      return true;
    }
    var thread = conv;
    var read = [], lost = [];
    Promise.all(files.map(function (f) {
      return post("/api/agent/attachment", { path: f.path }).then(function (d) {
        if (!d || !d.ok || (f.sha256 && d.sha256 && d.sha256 !== f.sha256)) lost.push(f.name);
        else read.push(d);
      }, function () { lost.push(f.name); });
    })).then(function () {
      var no = function (why) {
        if (why) unsentSaid(why, "moved", "");
        if (refused) refused();
      };
      if (lost.length) {
        no("Not sent: " + lost.join(", ") + (lost.length === 1 ? " is" : " are")
           + " gone or changed since, and could not be sent with it again. Attach "
           + (lost.length === 1 ? "it" : "them") + " to a message in the box to send "
           + (lost.length === 1 ? "it." : "them."));
        return;
      }
      if (conv !== thread) {
        no("Not sent: another conversation is shown now.");
        return;
      }
      if (refusedNow(cuts)) { no(""); return; }
      /* In the order they were sent with it. */
      go(files.map(function (f) {
        return read.filter(function (d) { return d.path === f.path; })[0];
      }).filter(Boolean));
    });
    return true;
  }
  var reading = 0;
  var readFirst;
  var firstRead = new Promise(function (done) { readFirst = done; });
  function restore(swap) {
    /* After the saves still waiting: read before them, the thread on
     * screen missed what they said (fourteenth review, 10-08). With
     * `swap`, the thread on screen is replaced only when the other has
     * come, so what is typed meanwhile is said in the one it was typed in
     * (fifteenth review, 10-08). */
    var was = conv;
    reading += 1;
    var dashboard = window.FastMDXDashboard;
    var settled = dashboard && dashboard.studySettled ? dashboard.studySettled : Promise.resolve();
    return settled.then(function () {
      return inTurn(function () {
        var key = pageStudy();
        return fetch("/api/agent/conversation" + (key ? "?study=" + key : ""))
          .then(function (r) { return r.json(); });
      });
    }).then(function (d) {
      /* The GUI has another study open than this page is for: nothing of
       * another study's is drawn here; read again once the page knows its
       * study. */
      if (d && d.study_changed) { readAgain = true; refusedFor = d.study_key || ""; return; }
      readAgain = false;
      var entries = (d && d.entries) || [];
      /* Another thread put on screen while this was read (New pressed
       * as the page opened) stays (nineteenth review, 10-08: the old one
       * was drawn over it). */
      if (!swap && conv !== was) return;
      if (swap) clearThread();
      if (d && d.ok) {
        conv = threadOf(d.id, d.study, entries);
        convFor = d.study_key || "";
        if (!conv.page) conv.page = convFor;
        /* A folder open that is not yet a study (a run just started): the
         * chat of no study given in its place is not one the person chose;
         * for a run this page started, its own thread is read once it is
         * one, and Send waits until then (CI-774 reviews L10, L11, L12: a
         * message went into that chat). */
        conv.unread = !d.study && validStudyKey(d.study_key) ? d.study_key : "";
        /* Read for where the GUI is with no study there (none open, or a
         * folder that is not one): a study opened next has its own thread
         * read in its place (`standsIn`). */
        conv.standsIn = !d.study;
      }
      replay(entries);
    }).catch(function () {}).then(function () {
      reading -= 1;
      threadRead = true;
      readFirst();
      readAgainNow();
      sendWaitLater();
    });
  }
  /* The study the server said the GUI has open as it refused a read ("" for
   * none). Where the page already shows it (the read named the study its
   * address names, before the page read the GUI's), the GUI's place is read
   * again now, and with it this study's thread (`study-addressed`): no
   * change of study is coming to ask it (CI-774 review L15: Send waited for
   * good after a reload, a folder that is no study open). */
  var refusedFor = null;
  function readAgainNow() {
    var open = refusedFor;
    refusedFor = null;
    var dashboard = window.FastMDXDashboard;
    if (open === null || !readAgain || reading || !dashboard || !dashboard.readStatusNow) return;
    var shown = noStudyOpen() ? "" : (dashboard.state || {}).studyShown || "";
    if (open === shown) dashboard.readStatusNow();
  }

  function replay(entries) {
      if (!entries.length) return;
      entries.forEach(function (e) {
        /* A run asked for with no word yet that it started (the page went
         * as it was asked for): kept, and not said, nor told to the Agent,
         * until the server writes where it went (sixteenth review, 10-08:
         * one never started read as asked, and as done to the Agent). */
        if (e.kind === "action" && e.action === "run" && !e.started && !e.output) {
          transcript.push(e);
          return;
        }
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
                           plan: e.plan || [], changes: e.changes || null,
                           review: e.review || null, again: e.again }, box, e.version);
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
        var kept = stepsOf(e.steps);
        if (kept) { stepsCard(box, kept); currentSteps = kept; }
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
    sendUnsent();
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
    function resetThread() { clearThread(); }
    /* A fresh thread: where the GUI is, or, given `study` (null for a
     * chat of no study), there. */
    function freshConversation(study) {
      var body = study === undefined ? {} : { study: study };
      /* The new thread is on screen at once, so what is typed next is
       * said in it; it takes its id when the server has made it, after
       * the saves still waiting (fourteenth review, 10-08). */
      resetThread();
      var c = conv;
      if (study !== undefined) { c.study = study; c.placed = true; }
      /* The thread's key: a page closing before /new is sent saves into
       * the same conversation /new makes (eighteenth review, 10-08). */
      body.key = c.key;
      hideList();
      sendWaitLater();
      return inTurn(function () {
        return post("/api/agent/conversation/new", body);
      }).then(function (d) {
        if (d && d.ok && !c.id) {
          c.id = d.id || null;
          if (d.study !== undefined) c.study = d.study;
          /* Made as a chat of no study for the folder shown, not yet a
           * study (a run starting): Send waits as for the one read, and
           * the folder's own thread is read once it is one (CI-774 review
           * L13: New filed every message in a chat of no study). */
          if (study === undefined && !d.study && validStudyKey(c.page) && c.page === shownStudy()) {
            c.unread = c.page;
          }
          /* Made where the GUI is, with no study there: as one read there. */
          if (study === undefined && !d.study) c.standsIn = true;
          sendWaitLater();
        } else if (d && d.gone && !c.id) {
          /* Its key's conversation was deleted: so is this thread, kept
           * anew once the person says something in it; what the deleted
           * one held went with it. */
          var held = c === conv ? transcript : (c.entries || []);
          (d.held || []).forEach(function (eid) {
            held.forEach(function (e) {
              if (e.eid === eid) c.confirmed[eid] = JSON.stringify(e);
            });
          });
          c.gone = true;
        }
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
    /* A reload waits for a run being started: it would stop the page
     * before the run was asked for, or before its answer was heard
     * (twenty-second review, 10-08). */
    function reloadOnceLaunched(waited) {
      waited = waited || 0;
      /* A run with no answer in a minute does not hold the page. */
      if (launching && waited < 60000) {
        setTimeout(function () { reloadOnceLaunched(waited + 100); }, 100);
        return;
      }
      location.reload();
    }

    /* Another study's conversation opened: that study opened as a step of
     * its own, on the Agent page, so Back returns to the page before. The
     * study opened is the conversation's, as the open's answer names it,
     * never whichever study the GUI has open by then (a run, another
     * window). */
    function openStudyOfConversation(o, waited) {
      waited = waited || 0;
      if (launching && waited < 60000) {
        setTimeout(function () { openStudyOfConversation(o, waited + 100); }, 100);
        return;
      }
      var dashboard = window.FastMDXDashboard;
      if (!dashboard || !dashboard.studyOpened || !o || !o.study_key) { reloadOnceLaunched(); return; }
      dashboard.studyOpened({ study_key: o.study_key }, "agent");
    }

    /* Said beside the list, where it is open: why an open is held, or
     * that it waits. */
    function saidBesideList(text) {
      if (!list || list.hidden) return null;
      var said = list.querySelector(".agent-conv-held") || node("p", "agent-conv-held muted small");
      said.setAttribute("role", "status");
      said.textContent = text;
      list.insertBefore(said, list.firstChild);
      return said;
    }

    /* Said where the person is: beside the list where it is open, else in
     * the page's notice (CI-774 review K3: with the list closed, an open
     * that waited said nothing). */
    function saidWhereSeen(text) {
      var said = saidBesideList(text);
      if (said) return said;
      var dashboard = window.FastMDXDashboard;
      if (dashboard && dashboard.toast) dashboard.toast(text, "ok");
      return null;
    }

    /* Asked while a run this page started is being launched: the whole
     * open waits for the launch's answer, its hold judged and the server
     * asked only then. The launch switches the GUI to the run's folder as
     * it starts, so a study opened before its answer was left again at
     * once (CI-774 review K1). The list said for the GUI before the run
     * may name a study no longer open: the conversation's own study is
     * opened. The latest asked is the one opened, and it is said that it
     * replaced the one asked before. */
    var openWaiting = null;
    var WAITS = "Opens once the run has started.";
    var WAITS_UNSEEN = "The conversation opens once the run has started.";
    var REPLACED = "The conversation asked for last opens once the run has started, not the one before.";
    function openOnceLaunched(id, study) {
      var replaced = Boolean(openWaiting);
      if (openWaiting) openWaiting.resolve({ ok: false, error: "Another conversation was asked for." });
      var said = saidWhereSeen(replaced ? REPLACED : (list && !list.hidden ? WAITS : WAITS_UNSEEN));
      return new Promise(function (resolve) {
        var mine = openWaiting = { resolve: resolve };
        var waited = 0;
        (function look() {
          if (openWaiting !== mine) return;
          /* A run with no answer in a minute: the open is judged now, and
           * held by the launch as any switch is. */
          if (launching && waited < 60000) {
            waited += 100;
            setTimeout(look, 100);
            return;
          }
          openWaiting = null;
          if (said && (said.textContent === WAITS || said.textContent === REPLACED)) said.remove();
          resolve(openConversation(id, study, false, true));
        })();
      });
    }

    function openConversation(id, study, loaded, launchWaited) {
      if (launching && !launchWaited) return openOnceLaunched(id, study);
      /* Another study's opens that study: held as every other way to
       * another study is, with the reason said beside the list too. */
      var dashboard = window.FastMDXDashboard;
      var another = Boolean(study) && !loaded && dashboard && dashboard.beginStudySwitch;
      if (another) {
        var held = dashboard.beginStudySwitch();
        if (held) {
          saidBesideList(held);
          return Promise.resolve({ ok: false, error: held });
        }
      }
      function ended() { if (another) dashboard.endStudySwitch(); }
      /* After the saves still waiting, so a reload keeps them and the
       * thread opened says all they said (thirteenth and fourteenth
       * reviews, 10-08). */
      return inTurn(function () {
        return post("/api/agent/conversation/open", { id: id, study: study });
      }).then(function (o) {
        if (!o || !o.ok) { ended(); window.alert((o && o.error) || "Could not open it."); return o; }
        /* Loaded elsewhere than the list said (a run took it with it):
         * the page reads that study too, unless it is the one shown. */
        if (o.loaded_study && (!loaded || (o.study || null) !== (study || null)) &&
            !(o.study_key && o.study_key === shownStudy())) {
          openStudyOfConversation(o);
          return o;
        }
        ended();
        resetThread();
        conv = threadOf(o.id, o.study, o.entries || []);
        hideList();
        replay(o.entries || []);
        told();
        return o;
      }, function (error) { ended(); throw error; });
    }

    /* The list of conversations: this study's, the chats of no study, and
     * each other study's folded under its ID; found by typing, the one
     * talked in last first. A title opens it; the pencil names it; the bin
     * deletes it, and only it, after asking. */
    var list = el("agent-conv-list");
    function hideList() { if (list) list.hidden = true; }
    var PENCIL = window.FastMDXIcons.svg("edit");
    var BIN = window.FastMDXIcons.svg("bin");
    var SEARCH = window.FastMDXIcons.svg("search");

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
      var here = c.id === conv.id && (c.study || null) === (conv.study || null);
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
        /* Deleted by the person here: a save of it still on its way is
         * not taken for words said after a delete elsewhere. */
        deletedHere[c.id] = true;
        deletesWaiting[c.id] = (deletesWaiting[c.id] || 0) + 1;
        var shown = conv;
        post("/api/agent/conversation/delete",
             { id: c.id, study: g.study, key: shown.key }).then(function (d) {
          deletesWaiting[c.id] -= 1;
          if (!d || !d.ok) {
            /* Not deleted, unless this page deleted it already, or may yet:
             * a second press on a row not yet drawn again, whose answer can
             * come first (twenty-third and twenty-fourth reviews). */
            if (deletedDone[c.id] || deletesWaiting[c.id]) return;
            delete deletedHere[c.id];
            window.alert((d && d.error) || "Could not delete it.");
            return;
          }
          deletedDone[c.id] = true;
          /* The thread on screen, by its id, or, before its first save
           * has named it, by its key, which the server knows: decided at
           * once (twenty-first to twenty-third reviews, 10-08: left on
           * screen, or reset late, what was typed next was dropped). */
          if (d.on_screen) deletedKeys[shown.key] = true;
          /* Or opened again while the delete was on its way (twenty-fourth
           * review, 10-08: left on screen, what was typed next was lost). */
          if (c.id === conv.id || (conv === shown && d.on_screen)) resetThread();
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
        detail: { id: conv.id, study: conv.study } }));
    }
    window.addEventListener("fmx:conversation-saved", told);

    /* The study shown is known: a conversation read for another study than
     * the page's (or refused as one) is read again, for the page's. A chat
     * of no study goes on as it is. */
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("study-addressed", function (study) {
        var key = study && study.key;
        if (!validStudyKey(key)) {
          /* No study open, or a folder open that is not a study: a read
           * refused for the study left is read again, for where the GUI is
           * (the workspace's chats), so Send waits no longer (CI-774 review
           * L15: with a folder open, or after a reload, it waited for good). */
          if (readAgain && !launching && !reading) {
            readAgain = false;
            hideList();
            restore(true).then(function () { told(); });
          }
          return;
        }
        /* On the run's study, its thread is the page's. On any other, that
         * study's own thread is read in its place, and where the run's is
         * said (CI-774 review L3). */
        if (launchedAway) {
          if (key === launchedAway.key) launchedAway = null;
          else launchedAway.from = key;
        }
        sendWaitLater();
        if (launching || reading) return;
        /* A read refused as the GUI had gone on (a run's study, its
         * thread on screen): the page is on that study now. */
        if (readAgain && convFor === key && conv.study) { readAgain = false; return; }
        var dash = window.FastMDXDashboard;
        /* A chat of no study given for a folder that is a study now (a
         * run's, whichever window started it): its own thread is read
         * (CI-774 review L14: another window filed every message in that
         * chat). */
        var nowOne = conv.unread === key && dash.state && dash.state.studyIsOne !== false;
        /* A chat of no study read or made for where the GUI was (no study,
         * or a folder that is not one): a study opened there since, from
         * another window or an AI app, has its own thread read in its place,
         * so a message written on it goes into its thread (CI-774 review L15:
         * every message was asked in that chat, refused on save and kept
         * for a folder the GUI does not open). */
        var standingIn = Boolean(conv.standsIn && !conv.study && conv.unread !== key);
        if (readAgain || nowOne || standingIn || (conv.study && convFor && convFor !== key)) {
          readAgain = false;
          hideList();
          restore(true).then(function () { readAway(); told(); });
        }
      });
    }

    /* Another study opened: its own conversation is shown, as the page
     * opened on it would show it. A chat of no study goes on as it is. */
    window.addEventListener("dashboard:run-changed", function (event) {
      var detail = event.detail || {};
      if (detail.first || !conv.study) return;
      /* A run starting: the GUI has switched to its folder, and the
       * conversation is on its way there (tenth review, 10-08). */
      if (launching) return;
      if (String(detail.activeRun || "") === String(conv.study)) return;
      hideList();
      restore(true).then(function () { readAway(); told(); });
    });

    window.FastMDXAgentPanel = {
      open: function (id, study, loaded) { return openConversation(id, study, loaded); },
      fresh: freshConversation,
      list: function () { return showList(); },
      get current() { return { id: conv.id, study: conv.study }; },
      /* Why Send waits now, or "" (`sendWaits`). */
      get waits() { return sendWaits(); },
    };

    if (!el("agent-provider")) return;
    loadEngine();

    el("agent-provider").addEventListener("change", function () {
      showProvider(this.value);
    });
    el("agent-model").addEventListener("change", function () {
      modelChanged();
      updateEngineSummary();
    });
    el("agent-model-other").addEventListener("input", updateEngineSummary);
    el("agent-base-url").addEventListener("input", function () {
      updateKeyHelp(el("agent-provider").value);
      updateEngineSummary();
    });
    el("agent-save-model").addEventListener("click", function () { saveEngine(false); });
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
         * a thread about a study, the workspace for a general one. The
         * thread is the page's study's (read once that study is open), so
         * this is the folder of the study the page is for. */
        window.FastMDXPicker.open({ into: "agent-attach-path", mode: "file",
                                    start: conv.study || workspaceRoot || "" });
      });
      attachPath.addEventListener("change", function () {
        var p = attachPath.value.trim();
        attachPath.value = "";
        attachFile(p);
      });
    }
    var area = el("agent-request");
    area.addEventListener("input", function () { autosize(area); renderMentions(); });
    area.addEventListener("input", sendReady);
    area.addEventListener("input", keepUnsent);
    /* Said that words went with their study: said until the person types. */
    area.addEventListener("input", function (event) {
      if (event.isTrusted && noteKind === "moved") unsentSaid("");
      if (event.isTrusted) swappedAt = 0;
    });
    window.addEventListener("pagehide", keepUnsent);
    /* Left with an edit refused (a reload): its words saved with the words
     * of the study it was written for, in its box when the page is back. */
    window.addEventListener("pagehide", function (event) {
      if (event.persisted) return;
      editsLeft().forEach(function (e) {
        if (!validStudyKey(e.key)) return;
        var kept = storedUnsent(e.key) || { words: "", files: [] };
        var words = e.words();
        try {
          sessionStorage.setItem(UNSENT_WORDS + e.key, JSON.stringify({
            words: kept.words.trim() ? kept.words + "\n\n" + words : words,
            files: withFiles(kept.files, e.files), editLeft: true }));
        } catch (err) { /* this browser keeps nothing */ }
      });
    });
    /* Left as a run started (a reload, a Back): the words kept are marked
     * so, with their study's name, and the page that comes back says
     * where they are (CI-774 review L12). */
    window.addEventListener("pagehide", function () {
      if (!(launching || leftLaunching) || !validStudyKey(unsentKey) || !boxNow().trim()) return;
      try {
        var kept = JSON.parse(sessionStorage.getItem(UNSENT_WORDS + unsentKey) || "null");
        if (!kept || typeof kept !== "object") return;
        kept.asRunStarted = unsentTitle || "the study it was typed in";
        sessionStorage.setItem(UNSENT_WORDS + unsentKey, JSON.stringify(kept));
      } catch (e) { /* kept as it is */ }
    });
    if (window.FastMDXDashboard) {
      window.FastMDXDashboard.on("study-addressed", restoreUnsent);
    }
    // Beside a page or on the Agent page, the box asks in that column's words.
    // Moved beside a page or back to the Agent page, the box is sized for
    // the prompt it asks, or for what was written in it, in its new column.
    function sizedWhereItIs() {
      placeholder();
      if (area.value && area.offsetParent !== null) autosize(area);
    }
    document.addEventListener("fmx:agent-beside", sizedWhereItIs);
    window.addEventListener("dashboard:navigate", function (event) {
      if (!event.detail || event.detail.page !== "agent") return;
      requestAnimationFrame(sizedWhereItIs);
    });
    // Values supplied by page controls also update Send's ready state.
    var nativeValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
    if (nativeValue && nativeValue.get && nativeValue.set) {
      Object.defineProperty(area, "value", {
        configurable: true,
        get: function () { return nativeValue.get.call(this); },
        set: function (value) { nativeValue.set.call(this, value); sendReady(); },
      });
    }
    sendReady();
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
      /* The mode by its name and its two words ("Seeker: built-in,
       * assisted"), as the choice says it, wherever it is shown: under the
       * composer, in the settings menu and in the status bar (statusbar.js
       * reads the same choice). */
      var select = el("agent-mode");
      var said = select.selectedIndex >= 0 ? select.options[select.selectedIndex].text : mode;
      ["agent-footer-mode", "settings-mode"].forEach(function (id) {
        var node = el(id);
        if (node) node.textContent = said;
      });
      el("agent-budget-note").textContent = mode === "autonomous"
        ? "Required in this mode. Checked after setup, where the cost is "
          + "first known."
        : "Optional. Stops a study that would cost more than you meant.";
    }
    /* The mode and the GPU-hour ceiling saved in this browser as they are
     * chosen (`fmx.agentChoices`), and chosen again as the page opens: a
     * reload left the Agent in Seeker with no ceiling, whatever was set. */
    var CHOICES_KEY = "fmx.agentChoices";
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(CHOICES_KEY) || "null"); } catch (e) { saved = null; }
    var modeField = el("agent-mode");
    var budgetField = el("agent-budget");
    var restored = false;
    if (saved && typeof saved === "object") {
      if (typeof saved.mode === "string" && Array.prototype.some.call(modeField.options, function (o) {
        return o.value === saved.mode;
      })) {
        restored = modeField.value !== saved.mode;
        modeField.value = saved.mode;
      }
      if (typeof saved.budget === "string" && /^\d*\.?\d*$/.test(saved.budget)) budgetField.value = saved.budget;
    }
    function saveChoices() {
      try {
        localStorage.setItem(CHOICES_KEY, JSON.stringify({ mode: modeField.value, budget: budgetField.value }));
      } catch (e) { /* a private window saves nothing */ }
    }
    modeField.addEventListener("change", saveChoices);
    budgetField.addEventListener("change", saveChoices);
    el("agent-mode").addEventListener("change", modeChanged);
    el("agent-mode").addEventListener("change", syncStart);
    modeChanged();
    // Said wherever the mode is shown (the status bar reads the choice).
    if (restored) modeField.dispatchEvent(new Event("change", { bubbles: true }));

    Array.prototype.forEach.call(document.querySelectorAll(".agent-starter"), function (b) {
      b.addEventListener("click", function () {
        var prompt = b.getAttribute("data-prompt") || b.textContent;
        /* Sent as it is pressed. Written into the box to be changed
         * first, a question asked by a press read as not asked, and the
         * box sat waiting with the question in it. In autonomous mode a
         * study the Agent writes is started without being shown, so there
         * a suggestion waits in the box to be read and sent, never over
         * words already in it. */
        if (el("agent-mode").value === "autonomous") {
          /* Said, not done in silence (CI-774 reviews L13, L14). */
          if (area.value.trim()) {
            unsentSaid("The box holds words of yours: send or clear them first, then press the "
                       + "suggestion again.", "moved", "");
            return;
          }
          fromStarter = b.getAttribute("data-records")
            ? { key: b.getAttribute("data-records"), prompt: prompt } : null;
          area.value = prompt;
          autosize(area);
          keepUnsent();
          area.focus();
          area.setSelectionRange(area.value.length, area.value.length);
          return;
        }
        sendPressed(prompt, function () {
          /* A question about the study open, which its records answer
           * where no AI model is set: asked as it was offered, it says
           * which. */
          fromStarter = b.getAttribute("data-records")
            ? { key: b.getAttribute("data-records"), prompt: prompt } : null;
        });
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
  window.FastMDXAgent = { openSettings: openSettings, closeSettings: closeSettings,
    /* A study's kept unsent words forgotten: its tab closed, its folder gone. */
    forgetUnsent: forgetUnsent,
    beforeStudySwitch: function (how) {
      if (launching) return "Wait for the study launch to finish before switching studies.";
      if (attaching) return "Wait for the Agent attachment to finish loading before switching studies.";
      if (writing || pendingReplies) return "Finish or stop the Agent reply before switching studies.";
      /* Said with the review's own buttons, beside the review too. */
      if (heldByReview("switching studies")) {
        return "Use reviewed version, or Use this version to keep version " + newest().number
          + " as proposed, before switching studies.";
      }
      if (pendingFiles.length || (el("agent-request")?.value || "").trim()) {
        /* Back is not held for words kept for this study: they are there
         * again when it is. */
        keepUnsent();
        if (how && how.back && unsentKept) return "";
        return "Send or clear the unsent Agent message and attachments before switching studies.";
      }
      return "";
    }
  };
})();
