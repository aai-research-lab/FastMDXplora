/* Stop the run: one control, asked once more before it acts, wherever it
 * is offered.
 *
 * A run could be stopped only from New study (and by asking the Agent), at
 * one press with nothing asked: a run of hours ended by a stray click. It
 * is now offered beside where the run stands in the sidebar, in the
 * Overview's health card, on New study and from Cmd+K, each time asking
 * first and saying what a stop at this stage leaves: a run in production
 * ends at its next frame with a checkpoint there, one before production
 * has none to carry on from. What it answers is said where it was asked,
 * in words: a stop is not reported as a failure. */
(function () {
  "use strict";

  var running = false;
  var wasRunning = false;
  var stage = "";

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* What stopping now leaves, by the stage the run is in. */
  function whatItLeaves() {
    if (stage === "production") {
      return "It ends at its next frame with a checkpoint there, and can be carried on from it.";
    }
    if (stage === "analysis" || stage === "report") {
      return "The simulation is finished and kept; the " + stage + " can be run again.";
    }
    return "It has not reached production, so it leaves no checkpoint: carrying it on runs it again.";
  }

  /* Ask in ``host``, then stop. ``done`` is told the answer's words. */
  function ask(host, done) {
    if (!host) return;
    host.hidden = false;
    host.innerHTML = "";
    host.appendChild(el("span", "fix-said", "Stop the run? " + whatItLeaves()));
    var row = el("span", "stop-run-answers");
    var yes = el("button", "danger-btn stop-run-yes", "Yes, stop it");
    yes.type = "button";
    var no = el("button", "ghost-btn stop-run-no", "Not now");
    no.type = "button";
    no.addEventListener("click", function () {
      host.innerHTML = "";
      host.hidden = true;
      if (done) done(null);
    });
    yes.addEventListener("click", function () {
      yes.disabled = true;
      no.disabled = true;
      host.innerHTML = "";
      host.appendChild(el("span", "fix-said", "Stopping…"));
      stop().then(function (said) {
        host.innerHTML = "";
        host.appendChild(el("span", "fix-said stop-run-answer", said));
        if (done) done(said);
        // What would fix it, once the stop is in the study's records: it
        // came only with a reload.
        if (window.FastMDXFixes) {
          [1500, 5000].forEach(function (wait) {
            setTimeout(function () { window.FastMDXFixes.load(); }, wait);
          });
        }
      });
    });
    row.appendChild(yes);
    row.appendChild(no);
    host.appendChild(row);
    // The answer that does nothing has the keyboard: Enter after Ctrl+K,
    // "stop", Enter stopped the run with no further thought.
    no.focus();
  }

  function stop() {
    return fetch("/api/explore/stop", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
    }).then(function (res) { return res.json(); }).then(function (d) {
      // The server answers `stopped`, never `ok`: the builder read `ok` and
      // said "Could not stop." of every stop that worked.
      if (d && d.stopped) return "Stopped.";
      return (d && (d.detail || d.error)) || "Could not stop it.";
    }).catch(function () { return "Could not reach the server to stop it."; });
  }

  /* The controls this offers, shown only while a run goes here. */
  function show() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-stop-run]"), function (button) {
      button.hidden = !running;
      button.disabled = false;
    });
    var started = running && !wasRunning;
    wasRunning = running;
    Array.prototype.forEach.call(document.querySelectorAll("[data-stop-ask]"), function (host) {
      // A question left open when the run ends goes with it; an earlier
      // stop's answer goes once another run starts ("Stopped." stayed under
      // a live Stop the whole carry-on through).
      if ((!running && host.querySelector(".stop-run-yes"))
          || (started && host.querySelector(".stop-run-answer"))) {
        host.innerHTML = "";
        host.hidden = true;
      }
    });
  }

  function wire() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-stop-run]"), function (button) {
      if (button.dataset.stopWired) return;
      button.dataset.stopWired = "1";
      button.addEventListener("click", function () {
        var host = document.getElementById(button.getAttribute("data-stop-run"));
        button.disabled = true;
        ask(host, function () { button.disabled = false; });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    show();
    var board = window.FastMDXDashboard;
    if (!board || !board.on) return;
    board.on("app-state", function (detail) {
      running = Boolean(detail && detail.process_running);
      show();
    });
    board.on("status-updated", function (detail) {
      var status = (detail && detail.status) || {};
      stage = String(status.stage || "").toLowerCase();
    });
  });

  window.FastMDXStop = {
    ask: ask,
    stop: stop,
    running: function () { return running; }
  };
}());
