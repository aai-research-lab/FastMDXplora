/* Stop the run: one control, asked once more before it acts, wherever it
 * is offered.
 *
 * A run could be stopped only from New study (and by asking the Agent), at
 * one press with nothing asked: a run of hours ended by a stray click. It
 * is now offered beside the run in the status bar, in the Overview's
 * health card and from Cmd+K, each time asking
 * first and saying what a stop at this stage leaves: a run in production
 * ends at its next frame with a checkpoint there, one before production
 * has none to carry on from. What it answers is said where it was asked,
 * in words: a stop is not reported as a failure. */
(function () {
  "use strict";

  var running = false;
  var statusReady = false;
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
      return "The simulation is finished and saved; the " + stage + " can be run again.";
    }
    return "It has not reached production, so it leaves no checkpoint: carrying it on runs it again.";
  }

  /* Bound beyond this computer, where a stop's POST is refused: not offered. */
  function beyondLoopback() { return document.body.dataset.beyondLoopback === "true"; }

  /* Ask in ``host`` (confirm.js), then stop. ``done`` is told the answer's
   * words, or null for Not now. */
  function ask(host, done) {
    if (!host || !running || !statusReady || !window.FastMDXConfirm || beyondLoopback()) return;
    window.FastMDXConfirm.ask(host, {
      title: "Stop the run?", detail: whatItLeaves(), yes: "Yes, stop it",
      yesClass: "stop-run-yes", noClass: "stop-run-no",
    }).then(function (yes) {
      if (!yes) {
        window.FastMDXConfirm.done(host);
        if (done) done(null);
        return;
      }
      host.replaceChildren(el("span", "fix-said", "Stopping…"));
      stop().then(function (said) {
        host.replaceChildren(el("span", "fix-said stop-run-answer", said));
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
    var available = running && statusReady;
    Array.prototype.forEach.call(document.querySelectorAll("[data-stop-run]"), function (button) {
      button.hidden = !available || beyondLoopback();
      button.disabled = false;
    });
    var started = available && !wasRunning;
    wasRunning = available;
    Array.prototype.forEach.call(document.querySelectorAll("[data-stop-ask]"), function (host) {
      // A question left open when the run ends goes with it; an earlier
      // stop's answer goes once another run starts ("Stopped." stayed under
      // a live Stop the whole carry-on through).
      if ((!available && host.querySelector(".stop-run-yes"))
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
        ask(host, function (said) {
          button.disabled = false;
          // A card over the page (the status bar's) says the answer for a
          // moment and goes; one in its page's flow keeps it.
          if (said && host && host.hasAttribute("data-stop-fades")) {
            setTimeout(function () {
              if (host.querySelector(".stop-run-answer")) {
                host.innerHTML = "";
                host.hidden = true;
              }
            }, 4000);
          }
          if (!said) button.focus();
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    show();
    var board = window.FastMDXDashboard;
    if (!board || !board.on) return;
    board.on("run-changed", function () {
      running = false;
      statusReady = false;
      stage = "";
      show();
    });
    board.on("app-state", function (detail) {
      running = Boolean(detail && detail.process_running);
      show();
    });
    board.on("status-updated", function (detail) {
      var status = (detail && detail.status) || {};
      stage = String(status.stage || "").toLowerCase();
      // App state can arrive before the record and its Overview panels.
      // Ask only after they are drawn, so the safe answer can take focus
      // and the question says what stopping this stage actually leaves.
      // An empty record still answers: runs without telemetry can stop too.
      statusReady = true;
      show();
    });
  });

  window.FastMDXStop = {
    ask: ask,
    stop: stop,
    running: function () { return running && statusReady && !beyondLoopback(); }
  };
}());
