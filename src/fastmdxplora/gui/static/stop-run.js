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
  var wasRunning = false;
  var ownStage = "";
  var refused = false;
  var refusedNow = null;
  var stoppingNow = false;
  /* The number of the state read Stop's answer carried: a read numbered
   * before it was asked before the stop, and says nothing of it. */
  var stopRead = null;

  /* A refused stop, said until the run can be stopped here or ends, or it
   * is closed. */
  function refusal(said) {
    var answer = el("span", "fix-said stop-run-answer stop-run-refused", said);
    var close = el("button", "ghost-btn stop-run-close", "Close");
    close.type = "button";
    close.setAttribute("aria-label", "Close the note");
    close.addEventListener("click", function () {
      var host = answer.parentNode;
      answer.remove();
      if (host) {
        clearTimeout(host.stopFade);
        if (!host.children.length) host.hidden = true;
        // The keyboard back on the Stop that asked, or the page's heading.
        var asker = host.id && document.querySelector('[data-stop-run="' + host.id + '"]');
        var back = asker && !asker.hidden ? asker : visibleHeading();
        if (back) {
          if (!back.hasAttribute("tabindex") && back.tagName === "H1") back.setAttribute("tabindex", "-1");
          back.focus();
        }
      }
    });
    answer.appendChild(document.createTextNode(" "));
    answer.appendChild(close);
    return answer;
  }

  /* The heading of the page shown: a selector list gave the first heading
   * of the document, a hidden one. */
  function visibleHeading() {
    var headings = document.querySelectorAll(".page:not([hidden]) h1, h1");
    for (var i = 0; i < headings.length; i += 1) {
      if (headings[i].offsetParent !== null) return headings[i];
    }
    return null;
  }

  /* Asked to stop, and not yet stopped: said, in place of a second Stop. */
  function stopping() {
    return el("span", "fix-said stop-run-answer stop-run-stopping", "Stopping…");
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* The stage furthest on among a study of runs' runs going: such a study
   * has no stage of its own, and its question said it had not reached
   * production while its runs were in it. */
  var ORDER = ["setup", "minimization", "minimisation", "nvt", "npt", "production",
               "analysis", "report"];
  function furthest(runs) {
    var best = "";
    (runs || []).forEach(function (run) {
      var at = String((run && run.state === "running" && run.stage) || "").toLowerCase();
      if (ORDER.indexOf(at) > ORDER.indexOf(best)) best = at;
    });
    return best;
  }
  var runsStage = "";

  /* What stopping now leaves, by the stage the run is in. */
  function whatItLeaves() {
    var stage = ORDER.indexOf(ownStage) >= 0 ? ownStage : runsStage || ownStage;
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
    if (!host || !window.FastMDXConfirm || beyondLoopback()) return;
    // Refused now (a run in this GUI's own process, where nothing listens
    // for a stop): said at once, not after asking whether to stop.
    if (refusedNow) {
      host.replaceChildren(refusal(refusedNow));
      host.hidden = false;
      if (done) done(refusedNow);
      return;
    }
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
        host.replaceChildren(refused ? refusal(said)
          : said === "Stopping…" ? stopping() : el("span", "fix-said stop-run-answer", said));
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
      refused = Boolean(d && d.refused);
      // Told by the answer itself, not by the next state read, which in a
      // quiet phase came half a minute later, Stop offered all the while.
      var state = (d && d.state) || {};
      stoppingNow = Boolean((state.process_running && state.stopping)
        || (d && d.stopped && /^Stopping/.test(d.detail || "")));
      if (stoppingNow && typeof state.read === "number") stopRead = state.read;
      show();
      // The server answers `stopped`, never `ok`: the builder read `ok` and
      // said "Could not stop." of every stop that worked.
      // Asked of a run of this GUI's own process: it is stopping, not stopped.
      if (d && d.stopped) return /^Stopping/.test(d.detail || "") ? "Stopping…" : "Stopped.";
      return (d && (d.detail || d.error)) || "Could not stop it.";
    }).catch(function () { return "Could not reach the server to stop it."; });
  }

  /* The controls this offers, shown only while a run goes here. */
  function show() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-stop-run]"), function (button) {
      button.hidden = !running || beyondLoopback() || stoppingNow;
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
      // Stopping: said, and no Stop offered, until the run has stopped.
      if (stoppingNow && !host.querySelector(".stop-run-stopping")) {
        host.replaceChildren(stopping());
        host.hidden = false;
      } else if (!stoppingNow && host.querySelector(".stop-run-stopping")) {
        host.querySelector(".stop-run-stopping").remove();
        if (!host.children.length) host.hidden = true;
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
          // A refusal there stays longer, and can be closed; the Overview's
          // keeps it while it holds. Stopping stays until the run stops.
          if (said && host && host.hasAttribute("data-stop-fades")
              && !host.querySelector(".stop-run-stopping")) {
            var refusedHere = Boolean(host.querySelector(".stop-run-refused"));
            // One timer at a time: an earlier one went off over a later answer.
            clearTimeout(host.stopFade);
            host.stopFade = setTimeout(function () {
              if (host.querySelector(".stop-run-answer:not(.stop-run-stopping)")) {
                host.innerHTML = "";
                host.hidden = true;
              }
            }, refusedHere ? 12000 : 4000);
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
    board.on("app-state", function (detail) {
      running = Boolean(detail && detail.process_running);
      refusedNow = (running && detail && detail.stop_refused) || null;
      // A read asked before Stop's answer (answered late) says nothing of
      // the stop: it put Stop back while the run was stopping.
      var stale = stopRead !== null && detail && typeof detail.read === "number"
        && detail.read < stopRead;
      if (!stale) {
        stoppingNow = Boolean(running && detail && detail.stopping);
        if (!stoppingNow) stopRead = null;
      }
      runsStage = furthest(detail && detail.runs);
      // A refusal goes once the run can be stopped here (production) or ends.
      if (!refusedNow) {
        Array.prototype.forEach.call(document.querySelectorAll(".stop-run-refused"), function (said) {
          var host = said.parentNode;
          said.remove();
          if (host && !host.children.length) host.hidden = true;
        });
      }
      show();
    });
    board.on("status-updated", function (detail) {
      var status = (detail && detail.status) || {};
      ownStage = String(status.stage || "").toLowerCase();
    });
  });

  window.FastMDXStop = {
    ask: ask,
    stop: stop,
    running: function () { return running && !stoppingNow && !beyondLoopback(); }
  };
}());
