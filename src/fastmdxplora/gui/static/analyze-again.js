/* Analyze again: the study's analyses run again in its folder, from its
 * records and the settings it recorded, simulating nothing
 * (`fastmdx analyze --output <study> --rerun`, fastmdxplora/again.py).
 *
 * The Analysis page offers it while a study with frames is open and
 * nothing runs. Pressing it opens the analyses to run: those the study ran
 * last, ticked, or, with no record, those that apply to its trajectory; the
 * others this release has remain available to add. The panel
 * says what is written again and what is kept aside, and only its own
 * button starts the run. The analyses appear on the page as they are
 * written, as they do while any run goes. */
(function () {
  "use strict";

  var offered = null;
  var running = false;
  /* When the run started here began, as the server recorded it; null
   * while none is waited for. A state sent before it began is not its end. */
  var started = null;

  function el(id) { return document.getElementById(id); }

  function node(tag, cls, text) {
    var made = document.createElement(tag);
    if (cls) made.className = cls;
    if (text) made.textContent = text;
    return made;
  }

  function panel() { return el("analysis-again-ask"); }

  function close() {
    var box = panel();
    if (box) { box.hidden = true; box.innerHTML = ""; }
    var button = el("analysis-again");
    if (button) button.setAttribute("aria-expanded", "false");
  }

  /* A command to run at the terminal, set as code that breaks only between
   * its words: never between "--" and the word it marks. */
  function commandNode(command) {
    var code = document.createElement("code");
    code.className = "again-terminal";
    String(command).split(" ").forEach(function (word, at) {
      if (at) code.appendChild(document.createTextNode(" "));
      var piece = document.createElement("span");
      piece.style.whiteSpace = "nowrap";
      piece.textContent = word;
      code.appendChild(piece);
    });
    return code;
  }

  /* A sentence, its command (``terminal``, its last words) set as code. */
  function withCommand(said, text, terminal) {
    text = String(text || "");
    if (terminal && text.slice(-terminal.length) === terminal) {
      said.textContent = text.slice(0, text.length - terminal.length);
      said.appendChild(commandNode(terminal));
    } else {
      said.textContent = text;
    }
    return said;
  }

  function say(text, terminal) {
    var box = panel();
    if (!box) return;
    box.innerHTML = "";
    // Closed from its top right corner, as a dialog is.
    var shut = window.FastMDXIcons.button("close", "Close", "panel-x");
    shut.addEventListener("click", close);
    box.appendChild(shut);
    box.appendChild(withCommand(node("p", "again-said", ""), text, terminal));
    box.hidden = false;
  }

  function load() {
    return fetch("/api/again", { cache: "no-store" })
      .then(function (res) { return res.json(); })
      .then(function (data) { offered = data; return data; })
      .catch(function () { offered = null; return null; });
  }

  /* What the run will do, as the person reads it before agreeing. */
  function told(analysis, chosen) {
    var several = offered.several && analysis.runs > 1;
    var where = several ? "each of this study's " + analysis.runs + " runs" : "this study";
    var lines = ["Analyze " + where + " again from its records, with the " +
                 (chosen === 1 ? "analysis" : chosen + " analyses") + " ticked. " +
                 "Nothing is simulated."];
    var kept = "analysis/";
    if (analysis.report_too) {
      lines.push("The report is written again too: one from the analyses before " +
                 "would contradict them.");
      kept = "analysis/ and report/";
    }
    lines.push("The " + kept + " there now " + (analysis.report_too ? "are" : "is") +
               " kept in " + (offered.previous || "previous") + "/, in place of what " +
               "was kept there before.");
    if (offered.several) lines.push("The comparison of the runs is built again after.");
    (analysis.left_out || []).forEach(function (left) {
      lines.push(left.run + " is left out: " + left.why);
    });
    return lines.join(" ");
  }

  function choice(item, ticked) {
    var label = node("label", "again-choice");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.value = item.name;
    box.checked = ticked;
    label.appendChild(box);
    label.appendChild(node("span", "again-name mono", item.name));
    if (item.title && item.title !== item.name) {
      label.appendChild(node("span", "again-title", item.title));
    }
    return label;
  }

  /* `only`: the analyses to tick, all the others left unticked (an
   * analysis's cell asks for itself alone); every one it ran last where
   * none are named. */
  /* Where the question was asked from (the page's button or a cell's),
   * for the keyboard to go back to when Escape puts it away. */
  var askedFrom = null;

  function ask(only) {
    var box = panel();
    var analysis = offered && offered.analysis;
    if (!box || !analysis) return;
    askedFrom = document.activeElement;
    if (!analysis.can) {
      say(analysis.why || "This study cannot be analyzed again here.", analysis.terminal);
      return;
    }
    box.innerHTML = "";
    var said = node("p", "again-said", "");
    box.appendChild(said);
    // A record of the study not read (its analysis manifest) is said, and
    // nothing is ticked in its place: the person chooses.
    var unread = offered.recorded_said || offered.not_read || "";
    if (unread) box.appendChild(node("p", "again-unread", unread));

    var recorded = offered.recorded || [];
    var automatic = Array.isArray(analysis.defaults) ? analysis.defaults : null;
    var catalogue = offered.catalogue || [];
    var known = {};
    catalogue.forEach(function (item) { known[item.name] = item; });
    var fields = node("fieldset", "again-choices");
    fields.appendChild(node("legend", "", recorded.length ? "Analyses it ran last" : "Analyses"));
    var first = node("div", "again-list");
    recorded.forEach(function (name) {
      first.appendChild(choice(known[name] || { name: name }, !only || only.indexOf(name) >= 0));
    });
    fields.appendChild(first);
    if (!unread && !recorded.length && automatic === null) {
      fields.appendChild(node("p", "again-said", analysis.defaults_why ||
        "Automatic choices could not be determined for this study. Choose analyses below."));
    } else if (!unread && !recorded.length && automatic.length === 0) {
      fields.appendChild(node("p", "again-said",
        "No automatic analyses apply to this trajectory. Choose analyses below."));
    }
    var others = catalogue.filter(function (item) { return recorded.indexOf(item.name) < 0; });
    if (others.length) {
      var more = node("details", "again-more");
      more.appendChild(node("summary", "", "More analyses (" + others.length + ")"));
      var rest = node("div", "again-list");
      others.forEach(function (item) {
        var ticked = only ? only.indexOf(item.name) >= 0 :
          !recorded.length && !unread && automatic !== null && automatic.indexOf(item.name) >= 0;
        rest.appendChild(choice(item, ticked));
      });
      more.appendChild(rest);
      if (!recorded.length) more.open = true;
      fields.appendChild(more);
    }
    box.appendChild(fields);

    var row = node("div", "fix-actions");
    var yes = node("button", "primary-btn fix-confirm", "Analyze again");
    yes.type = "button";
    var no = node("button", "ghost-btn fix-cancel", "Not now");
    no.type = "button";
    no.addEventListener("click", close);
    row.appendChild(yes);
    row.appendChild(no);
    box.appendChild(row);

    function ticked() {
      return Array.prototype.map.call(
        box.querySelectorAll(".again-choices input:checked"), function (input) { return input.value; });
    }
    function update() {
      var chosen = ticked().length;
      yes.disabled = !chosen;
      said.textContent = chosen ? told(analysis, chosen) : "Tick at least one analysis to run.";
    }
    fields.addEventListener("change", update);
    yes.addEventListener("click", function () { start(ticked()); });
    update();
    box.hidden = false;
    var button = el("analysis-again");
    if (button) button.setAttribute("aria-expanded", "true");
    if (only) box.scrollIntoView({ block: "nearest" });
    yes.focus({ preventScroll: !!only });
  }

  function start(analyses) {
    say("Starting…");
    fetch("/api/again", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phases: ["analysis"], analyses: analyses })
    }).then(function (res) { return res.json(); }).then(function (d) {
      if (d && d.ok) {
        started = (d.state && d.state.started_at) || "";
        say("Analyzing again. Its log is in the side panel, and the analyses appear " +
            "here as they are written.");
      } else {
        say((d && d.error) || "Could not start it.");
      }
    }).catch(function () { say("Could not reach the server to start it."); });
  }

  function open(only) {
    var box = panel();
    var named = Array.isArray(only) && only.length ? only : null;
    if (!named && box && !box.hidden && box.querySelector(".again-choices")) { close(); return; }
    load().then(function (data) {
      if (!data || !data.available) { say((data && data.why) || "Nothing is open to analyze."); return; }
      ask(named);
    });
  }

  /* Offered while a study is open and nothing runs; said as done once a
   * run started here has ended. */
  function offer(app) {
    var button = el("analysis-again");
    if (!button) return;
    app = app || {};
    running = !!app.process_running;
    // Not offered where it would be refused: a GUI served beyond this
    // computer answers no POST that starts a run.
    button.hidden = !app.active_run || document.body.dataset.beyondLoopback === "true";
    button.disabled = running;
    button.title = running
      ? "A run is going. The study can be analyzed again once it ends."
      : "Analyze this study's frames again, with the analyses you choose, simulating nothing";
    // Each analysis's own (analysis-cells.js), on the same state.
    Array.prototype.forEach.call(document.querySelectorAll(".cell-again"), function (cell) {
      cell.hidden = button.hidden;
      cell.disabled = button.disabled;
      cell.title = running ? button.title : cell.dataset.title || cell.title;
    });
    if (started === null || running || app.started_at !== started) return;
    if (app.returncode === null || app.returncode === undefined) return;
    started = null;
    if (app.returncode !== 0 || app.error) {
      say(app.error || "It stopped before the analyses were written again; its log is in the side panel.");
      return;
    }
    say("Analyzed again. What it replaced is in " + ((offered && offered.previous) || "previous") + "/.");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var box = panel();
    if (box) {
      box.addEventListener("keydown", function (event) {
        if (event.key !== "Escape" || box.hidden) return;
        event.preventDefault();
        close();
        if (askedFrom && askedFrom.isConnected) askedFrom.focus();
      });
    }
    var button = el("analysis-again");
    if (!button) return;
    button.addEventListener("click", function () { open(); });
    var board = window.FastMDXDashboard;
    if (board) offer((board.state || {}).appState);
    if (board && board.on) board.on("app-state", offer);
  });

  window.FastMDXAgain = { open: open };
}());
