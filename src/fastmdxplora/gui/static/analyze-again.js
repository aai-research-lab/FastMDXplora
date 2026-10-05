/* Analyze again: the study's analyses run again in its folder, from its
 * records and the settings it recorded, simulating nothing
 * (`fastmdx analyze --output <study> --rerun`, fastmdxplora/again.py).
 *
 * The Analysis page offers it while a study with frames is open and
 * nothing runs. Pressing it opens the analyses to run: those the study ran
 * last, ticked, and the others this release has, to add one. The panel
 * says what is written again and what is kept aside, and only its own
 * button starts the run. The analyses appear on the page as they are
 * written, as they do while any run goes. */
(function () {
  "use strict";

  var offered = null;
  var running = false;
  var started = false;

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

  function say(text) {
    var box = panel();
    if (!box) return;
    box.innerHTML = "";
    box.appendChild(node("p", "again-said", text));
    var shut = node("button", "ghost-btn fix-cancel", "Close");
    shut.type = "button";
    shut.addEventListener("click", close);
    var row = node("div", "fix-actions");
    row.appendChild(shut);
    box.appendChild(row);
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

  function ask() {
    var box = panel();
    var analysis = offered && offered.analysis;
    if (!box || !analysis) return;
    if (!analysis.can) { say(analysis.why || "This study cannot be analyzed again here."); return; }
    box.innerHTML = "";
    var said = node("p", "again-said", "");
    box.appendChild(said);

    var recorded = offered.recorded || [];
    var catalogue = offered.catalogue || [];
    var known = {};
    catalogue.forEach(function (item) { known[item.name] = item; });
    var fields = node("fieldset", "again-choices");
    fields.appendChild(node("legend", "", recorded.length ? "Analyses it ran last" : "Analyses"));
    var first = node("div", "again-list");
    recorded.forEach(function (name) { first.appendChild(choice(known[name] || { name: name }, true)); });
    fields.appendChild(first);
    var others = catalogue.filter(function (item) { return recorded.indexOf(item.name) < 0; });
    if (others.length) {
      var more = node("details", "again-more");
      more.appendChild(node("summary", "", "More analyses (" + others.length + ")"));
      var rest = node("div", "again-list");
      others.forEach(function (item) { rest.appendChild(choice(item, !recorded.length)); });
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
    yes.focus();
  }

  function start(analyses) {
    say("Starting…");
    fetch("/api/again", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phases: ["analysis"], analyses: analyses })
    }).then(function (res) { return res.json(); }).then(function (d) {
      if (d && d.ok) {
        started = true;
        say("Analyzing again. Its log is in the side panel, and the analyses appear " +
            "here as they are written.");
      } else {
        say((d && d.error) || "Could not start it.");
      }
    }).catch(function () { say("Could not reach the server to start it."); });
  }

  function open() {
    var box = panel();
    if (box && !box.hidden && box.querySelector(".again-choices")) { close(); return; }
    load().then(function (data) {
      if (!data || !data.available) { say((data && data.why) || "Nothing is open to analyze."); return; }
      ask();
    });
  }

  /* Offered while a study is open and nothing runs; said as done once a
   * run started here has ended. */
  function offer(app) {
    var button = el("analysis-again");
    if (!button) return;
    app = app || {};
    running = !!app.process_running;
    button.hidden = !app.active_run;
    button.disabled = running;
    button.title = running
      ? "A run is going. The study can be analyzed again once it ends."
      : "Analyze this study's frames again, with the analyses you choose, simulating nothing";
    if (started && !running) {
      started = false;
      say("Analyzed again. What it replaced is in " + ((offered && offered.previous) || "previous") + "/.");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = el("analysis-again");
    if (!button) return;
    button.addEventListener("click", open);
    var board = window.FastMDXDashboard;
    if (board) offer((board.state || {}).appState);
    if (board && board.on) board.on("app-state", offer);
  });
}());
