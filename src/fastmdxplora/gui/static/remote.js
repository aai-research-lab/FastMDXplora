/* Your machines, on the page: **Run on** a machine in the Config Builder,
 * and **Remote jobs** on All studies.
 *
 * What `fastmdx remote` does at a terminal, through the routes in
 * `gui/remote_routes.py`. Nothing goes to a machine until the plan the
 * page shows (what travels and each size, where it runs, the GPUs' room,
 * the job script) is agreed to here, and the send is of that plan only:
 * kept ten minutes, sent once, and refused where what would travel has
 * changed since. A fetch says each size first and brings only that; a
 * cancel asks first. Machines are the ones recorded at a terminal
 * (`fastmdx remote --machine NAME`); none recorded, nothing is shown. */
(function () {
  "use strict";

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function button(cls, label) {
    var b = el("button", cls, label);
    b.type = "button";
    return b;
  }

  function byId(id) { return document.getElementById(id); }

  function size(bytes) {
    var n = Number(bytes) || 0;
    var units = ["bytes", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i += 1; }
    return i === 0 ? n + " bytes" : (n < 10 ? n.toFixed(1) : Math.round(n)) + " " + units[i];
  }

  function ask(method, path, body) {
    var options = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (said) {
        if (!response.ok && said.ok === undefined) {
          said = { ok: false, error: "The server answered " + response.status + "." };
        }
        return said;
      });
    }, function () {
      return { ok: false, error: "Could not reach the server." };
    });
  }

  var machines = [];
  var jobs = [];
  // Why the machines could not be read, where they could not (a hosted
  // GUI, which reaches none, says nothing).
  var unread = "";
  // Requests still out, by section: a section is not drawn again under
  // them, so what they answer is said where it was asked.
  var busy = { run: 0, studies: 0 };

  function machinesKnown() {
    return ask("GET", "/api/remote/machines").then(function (said) {
      machines = said.ok ? (said.machines || []) : [];
      jobs = said.ok ? (said.jobs || []) : [];
      unread = !said.ok && !said.hosted ? (said.error || "They could not be read.") : "";
      return said;
    });
  }

  function whileAsking(section, promise) {
    busy[section] += 1;
    return promise.then(function (said) {
      busy[section] -= 1;
      return said;
    });
  }

  // ------------------------------------------------------------ Run on

  /* Whether the builder could start what it holds here: Run on a machine
   * is offered on the same terms. */
  function builderReady() {
    var run = window.FastMDXRun;
    if (run && run.state && run.state.start === "config") {
      var asIs = byId("run-as-is");
      return Boolean(asIs && !asIs.disabled && byId("run-config-path").value.trim());
    }
    var start = byId("run-start-button");
    return Boolean(start && !start.disabled);
  }

  function whatIsPlanned() {
    var run = window.FastMDXRun;
    if (run && run.state && run.state.start === "config") {
      return { config: byId("run-config-path").value.trim() };
    }
    return { state: run && run.currentState ? run.currentState() : {} };
  }

  function fromAFile() {
    var run = window.FastMDXRun;
    return Boolean(run && run.state && run.state.start === "config");
  }

  /* Beside what it sends: under the config file's own Run in "A config I
   * have", else under Run on this machine. */
  function place() {
    var host = byId("run-remote");
    var field = byId("run-config-field");
    var card = byId("run-actions-card");
    if (!host || !field || !card) return;
    var wanted = fromAFile() ? field : card;
    var after = fromAFile() ? field.querySelector(".builder-actions-row")
      : byId("run-stop-ask");
    if (host.parentNode !== wanted && after) {
      after.parentNode.insertBefore(host, after.nextSibling);
    }
    var label = byId("run-remote-label");
    if (label) {
      label.textContent = fromAFile() ? "Or send this config file to one of your machines"
        : "Or on one of your machines";
    }
  }

  var sayKnownNow = null;

  function renderRunOn() {
    var host = byId("run-remote");
    if (!host) return;
    host.innerHTML = "";
    clearPlan();
    var usable = machines.filter(function (m) { return m && m.name; });
    host.hidden = usable.length === 0 && !unread;
    if (!usable.length) {
      if (unread) host.appendChild(el("div", "muted small", "Your machines could not be read: "
        + unread));
      return;
    }
    var row = el("div", "remote-run-row");
    var label = el("label", "small muted", "Or on one of your machines");
    label.id = "run-remote-label";
    var pick = el("select", "remote-run-machine");
    pick.id = "run-remote-machine";
    label.htmlFor = pick.id;
    usable.forEach(function (m) {
      var option = el("option", null, m.name);
      option.value = m.name;
      pick.appendChild(option);
    });
    var first = usable.filter(function (m) { return m.ready; })[0] || usable[0];
    pick.value = first.name;
    // What its record says of it, as inspected at a terminal; the plan asks
    // the machine itself and says what stands in the way, if anything.
    var known = el("div", "muted small remote-run-known");
    function sayKnown() {
      var m = usable.filter(function (each) { return each.name === pick.value; })[0] || {};
      known.textContent = m.summary ? "As last inspected: " + m.summary : "";
    }
    sayKnown();
    sayKnownNow = function () {
      usable = machines.filter(function (m) { return m && m.name; });
      sayKnown();
    };
    pick.addEventListener("change", sayKnown);
    var go = button("ghost-btn remote-run-plan", "Plan the send");
    go.id = "run-remote-plan";
    var note = el("div", "builder-card-note remote-run-note");
    note.id = "run-remote-note";
    note.setAttribute("role", "status");
    var plan = el("div", "remote-plan");
    plan.id = "run-remote-planned";
    plan.hidden = true;
    row.appendChild(label);
    row.appendChild(pick);
    row.appendChild(go);
    host.appendChild(row);
    host.appendChild(known);
    host.appendChild(plan);
    host.appendChild(note);
    drawSent(note);
    function sync() {
      place();
      // Not while a plan or a send is out: one at a time, so a second plan
      // of a study being sent is never made.
      go.disabled = !builderReady() || busy.run > 0;
      go.title = builderReady() ? ""
        : "Make the study ready to run first, as for Run on this machine.";
    }
    sync();
    watchReady(sync);
    go.addEventListener("click", function () {
      planSend(pick.value, plan, note, go);
    });
  }

  var readyWatched = null;
  var syncNow = null;
  var builderWatched = false;
  var quiet = false;
  var expiry = null;

  function watchReady(sync) {
    if (readyWatched) readyWatched.disconnect();
    readyWatched = new MutationObserver(sync);
    ["run-start-button", "run-as-is"].forEach(function (id) {
      var node = byId(id);
      if (node) readyWatched.observe(node, { attributes: true, attributeFilter: ["disabled"] });
    });
    syncNow = sync;
    if (builderWatched) return;
    builderWatched = true;
    var builder = document.querySelector('section.page[data-page="run"]');
    if (!builder) return;
    // A plan is of the study as it was planned: changed since, it is taken
    // away, so Send never sends what the form no longer says.
    // A start chosen (a tile pressed) moves the panel beside what it sends.
    // So does a plan of a study a button changed (a system removed, Reset):
    // it is taken away once the form has taken the change.
    builder.addEventListener("click", function (event) {
      var host = byId("run-remote");
      setTimeout(function () {
        place();
        if (!quiet && host && !host.contains(event.target)) staleTakenAway();
      }, 0);
    }, true);
    ["input", "change"].forEach(function (kind) {
      builder.addEventListener(kind, function (event) {
        if (syncNow) syncNow();
        var host = byId("run-remote");
        if (quiet || !host || host.contains(event.target)) return;
        if (clearPlan()) sayChanged();
      }, true);
    });
  }

  /* Take a plan off the page; whether there was one. */
  function clearPlan() {
    if (expiry) { clearTimeout(expiry); expiry = null; }
    plannedAs = null;
    // Let go here, it is let go there: its config, written for it, is
    // taken back.
    if (planToken) {
      ask("POST", "/api/remote/forget", { plan: planToken });
      planToken = null;
    }
    var card = byId("run-remote-planned");
    if (!card || card.hidden) return false;
    card.innerHTML = "";
    card.hidden = true;
    return true;
  }

  /* The results folder a plan named, written in the form where it was left
   * empty, so the study planned again is the same file, not another. */
  function keepResultsName(results) {
    var run = window.FastMDXRun;
    var box = byId("run-output");
    if (!box || box.value.trim() || !results || (run && run.state
        && run.state.start === "config")) return;
    quiet = true;
    try {
      box.value = results;
      box.dispatchEvent(new Event("input", { bubbles: true }));
      box.dispatchEvent(new Event("change", { bubbles: true }));
    } finally {
      quiet = false;
    }
  }

  /* The study as the form holds it now, to tell a plan of it from one of
   * the study as it was: a button that removes a system or a draft loaded
   * changes the form without any event to hear. */
  var plannedAs = null;

  function asItIsNow() {
    return JSON.stringify(whatIsPlanned());
  }

  /* What the last send answered, said again whenever the panel is drawn. */
  var lastSent = null;
  // The plan on the page, as the server keeps it.
  var planToken = null;

  function drawSent(note) {
    if (!note || !lastSent) return;
    note.dataset.ok = lastSent.ok === null ? "" : (lastSent.ok ? "true" : "false");
    note.textContent = lastSent.text;
    if (lastSent.ok !== true) return;
    var open = button("builder-linkish", "Follow it under Remote jobs");
    open.addEventListener("click", function () {
      if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
        window.FastMDXDashboard.navigate("studies");
      }
    });
    note.appendChild(open);
  }

  var CHANGED = "The study changed after the plan was made. Plan the send again to see "
    + "what would go.";

  /* Said, and kept to be said again if the panel is drawn anew. */
  function sayChanged() {
    lastSent = { ok: null, text: CHANGED };
    drawSent(byId("run-remote-note"));
  }

  function planSend(machine, host, note, go) {
    lastSent = null;
    go.disabled = true;
    clearPlan();
    note.dataset.ok = "";
    note.textContent = "Asking " + machine + " what a send would do…";
    var body = whatIsPlanned();
    var asked = JSON.stringify(body);
    body.machine = machine;
    whileAsking("run", ask("POST", "/api/remote/plan", body)).then(function (said) {
      if (syncNow) syncNow();
      if (!said.ok) {
        note.textContent = forThePage(said.error || "It could not be planned.");
        note.dataset.ok = "false";
        return;
      }
      if (asItIsNow() !== asked) {
        ask("POST", "/api/remote/forget", { plan: said.plan });
        sayChanged();
        return;
      }
      if (body.state) keepResultsName(said.results);
      plannedAs = asItIsNow();
      planToken = said.plan;
      note.textContent = "Planned: what a send to " + said.machine
        + " would do is below. Nothing has been sent.";
      showPlan(said, host, note, go);
    });
  }

  /* A plan shown of a study the form no longer holds is taken away, and
   * said; whether it was. */
  function staleTakenAway() {
    if (plannedAs === null || asItIsNow() === plannedAs) return false;
    if (!clearPlan()) return false;
    sayChanged();
    return true;
  }

  function line(list, text) {
    list.appendChild(el("li", null, text));
  }

  function showPlan(plan, host, note, go) {
    host.innerHTML = "";
    host.hidden = false;
    var title = el("div", "remote-plan-title", "Send to " + plan.machine + "?");
    title.tabIndex = -1;
    host.appendChild(title);
    var facts = el("dl", "remote-plan-facts");
    [["Runs in", plan.runs_in + " on " + plan.machine + " (" + plan.scheduler + ")"],
     ["Its folder there", plan.folder],
     ["Config", plan.config],
     ["Results come back to", (plan.results_path || plan.results) + " when fetched"]]
      .forEach(function (pair) {
      facts.appendChild(el("dt", null, pair[0]));
      facts.appendChild(el("dd", "mono", pair[1]));
    });
    host.appendChild(facts);
    var travels = plan.travels || [];
    if (travels.length) {
      host.appendChild(el("div", "remote-plan-k", "Sent with it"));
      var list = el("ul", "remote-plan-list");
      travels.forEach(function (t) { line(list, t.from + " (" + size(t.bytes) + ")"); });
      host.appendChild(list);
    }
    if ((plan.fetched_there || []).length) {
      host.appendChild(el("div", "remote-plan-k",
        "Fetched there from RCSB: " + plan.fetched_there.join(", ")));
    }
    var room = (plan.room || []).concat(plan.notes || []);
    if (room.length) {
      host.appendChild(el("div", "remote-plan-k", "On " + plan.machine));
      var said = el("ul", "remote-plan-list remote-plan-room");
      room.forEach(function (text) { line(said, text); });
      host.appendChild(said);
    }
    var script = el("details", "remote-plan-script");
    script.appendChild(el("summary", "small", "The job script"));
    script.appendChild(el("pre", "mono small", plan.script || ""));
    host.appendChild(script);
    var answers = el("div", "remote-plan-answers");
    var send = button("primary-btn", "Send to " + plan.machine);
    var not = button("ghost-btn", "Not now");
    answers.appendChild(send);
    answers.appendChild(not);
    host.appendChild(answers);
    var kept = el("div", "muted small", "This plan is kept for "
      + Math.round((plan.kept_s || 600) / 60) + " minutes and sends once.");
    host.appendChild(kept);
    expiry = setTimeout(function () {
      var focused = document.activeElement === send;
      send.disabled = true;
      kept.textContent = "This plan is no longer kept. Plan the send again.";
      say(kept.textContent);
      if (focused) go.focus();
    }, (plan.kept_s || 600) * 1000);
    not.addEventListener("click", function () {
      clearPlan();
      note.textContent = "Not sent.";
      go.focus();
    });
    send.addEventListener("click", function () {
      // What is sent is what was shown: a form changed since, by any door,
      // sends nothing.
      if (staleTakenAway()) {
        go.focus();
        return;
      }
      if (expiry) { clearTimeout(expiry); expiry = null; }
      planToken = null;  // sent, not let go
      send.disabled = true;
      not.disabled = true;
      note.dataset.ok = "";
      note.textContent = "Sending to " + plan.machine + "…";
      var out = whileAsking("run", ask("POST", "/api/remote/send", { plan: plan.plan }));
      if (syncNow) syncNow();
      out.then(function (sent) {
        host.innerHTML = "";
        host.hidden = true;
        // Kept, so the page left and come back to says it still.
        lastSent = sent.ok
          ? { ok: true, text: "Sent to " + plan.machine + " as job " + sent.job.name
              + ". It runs there on its own, whether or not this page stays open. " }
          : { ok: false, text: forThePage(sent.error || "It was not sent.") };
        var now = byId("run-remote-note") || note;
        drawSent(now);
        now.tabIndex = -1;
        if (now.offsetParent) now.focus();
        say(lastSent.text);
        if (syncNow) syncNow();
        machinesKnown().then(function () {
          if (sayKnownNow) sayKnownNow();
          var studies = document.querySelector('section.page[data-page="studies"]');
          if (studies && !studies.hidden && !busy.studies && !askedIn(".remote-job-said button")) {
            renderJobs();
          }
        });
      });
    });
    title.focus();
  }

  // ------------------------------------------------------------ Remote jobs

  var STATES = {
    ready: "Waiting to start", running: "Running", done: "Done",
    failed: "Failed", abandoned: "Stopped",
  };
  var GOING = { ready: true, running: true };

  function renderJobs() {
    var host = byId("remote-jobs");
    if (!host) return;
    host.innerHTML = "";
    host.hidden = jobs.length === 0 && !unread;
    if (!jobs.length && !unread) return;
    var head = el("div", "remote-jobs-head");
    var title = el("h2", "card-title", "Remote jobs");
    title.id = "remote-jobs-title";
    head.appendChild(title);
    head.appendChild(el("span", "muted small",
      "Studies sent from this workspace to your machines."));
    host.appendChild(head);
    if (!jobs.length) {
      host.appendChild(el("div", "small", "They could not be read: " + unread));
      return;
    }
    var list = el("ul", "remote-jobs-list");
    jobs.slice().sort(function (a, b) {
      return String(b.submitted_at || "").localeCompare(String(a.submitted_at || ""));
    }).forEach(function (job) { list.appendChild(jobRow(job)); });
    host.appendChild(list);
  }

  /* What each job's row is doing, kept apart from the row itself, which
   * is drawn again whenever its job is asked about: a question out (with
   * what the row says meanwhile), or what the last one answered. */
  var doing = {};

  function rowOf(name) {
    var rows = document.querySelectorAll("#remote-jobs .remote-job");
    for (var i = 0; i < rows.length; i += 1) {
      if (rows[i].dataset.job === name) return rows[i];
    }
    return null;
  }

  /* Said aloud, once, wherever the row stands by then. */
  var heard = null;
  function say(text) {
    if (!heard) {
      heard = el("span", "sr-only");
      heard.setAttribute("role", "status");
      heard.setAttribute("aria-live", "polite");
      document.body.appendChild(heard);
    }
    heard.textContent = "";
    setTimeout(function () { heard.textContent = text; }, 50);
  }

  /* The job's row drawn again from what is known of it, the button named
   * ``focus`` given focus. */
  function redraw(job, focus) {
    if (job) jobs = jobs.map(function (j) { return j.name === job.name ? job : j; });
    var known = jobs.filter(function (j) { return j.name === (job && job.name); })[0];
    var row = known && rowOf(known.name);
    if (!row) return null;
    var fresh = jobRow(known);
    row.parentNode.replaceChild(fresh, row);
    if (focus) {
      var target = fresh.querySelector('[data-act="' + focus + '"]');
      if (target && !target.disabled) target.focus();
    }
    return fresh;
  }

  /* A question to the machine about ``job``, its row saying ``meanwhile``
   * and every action held until the answer, which ``then`` makes words of
   * (and may give the job as it is now). */
  function asking(job, meanwhile, request, then, focus) {
    doing[job.name] = { busy: true, said: meanwhile };
    redraw(job);
    whileAsking("studies", request).then(function (got) {
      var answer = then(got) || {};
      doing[job.name] = { busy: false, said: answer.said || "" };
      redraw(answer.job || job, focus);
      if (answer.said) say(answer.said);
    });
  }

  function jobRow(job) {
    var row = el("li", "remote-job");
    row.dataset.job = job.name;
    var now = doing[job.name] || {};
    var top = el("div", "remote-job-top");
    top.appendChild(el("span", "remote-job-name mono", job.name));
    var state = el("span", "remote-job-state", STATES[job.state] || job.state);
    state.dataset.state = job.state;
    top.appendChild(state);
    top.appendChild(el("span", "muted small", "on " + job.machine
      + (job.submitted_at ? ", sent " + localTime(job.submitted_at) : "")));
    row.appendChild(top);
    row.appendChild(el("div", "muted small mono", "Results: " + job.results
      + (job.fetched_at ? " (fetched)" : "")));
    if (job.detail) row.appendChild(el("div", "small", job.detail));
    var actions = el("div", "remote-job-actions");
    var check = button("ghost-btn", "Ask how it is doing");
    check.dataset.act = "ask";
    actions.appendChild(check);
    var fetchIt = null;
    var cancel = null;
    if (!GOING[job.state]) {
      fetchIt = button("ghost-btn", job.fetched_at ? "Fetch again" : "Fetch the results");
      fetchIt.dataset.act = "fetch";
      actions.appendChild(fetchIt);
    } else {
      cancel = button("ghost-btn", "Stop it");
      cancel.dataset.act = "stop";
      actions.appendChild(cancel);
    }
    row.appendChild(actions);
    var said = el("div", "remote-job-said small");
    if (now.said) said.textContent = now.said;
    row.appendChild(said);
    [check, fetchIt, cancel].forEach(function (b) { if (b) b.disabled = Boolean(now.busy); });
    check.addEventListener("click", function () {
      asking(job, "Asking " + job.machine + "…",
        ask("GET", "/api/remote/job?job=" + encodeURIComponent(job.name)), function (got) {
          if (!got.ok) return { said: forThePage(got.error || "It could not be asked.") };
          var at = new Date().toLocaleTimeString();
          return { job: got.job, said: "Asked at " + at + ". A machine is asked at most "
            + "every 30 s, so this answer may be up to 30 s old." };
        }, "ask");
    });
    if (fetchIt) fetchIt.addEventListener("click", function () { askToFetch(job, said); });
    if (cancel) cancel.addEventListener("click", function () { askToStop(job, said, cancel); });
    return row;
  }

  /* A moment recorded in UTC, as this computer's clock says it. */
  function localTime(iso) {
    var when = new Date(String(iso));
    if (isNaN(when.getTime())) return String(iso);
    return when.toLocaleString([], { year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit" });
  }

  /* A message written for the command line, said for this page. */
  function forThePage(text) {
    return String(text)
      .replace("fetch again with --with-trajectory to bring them",
        "fetch again with the trajectory ticked to bring them")
      .replace("works from this terminal", "works from a terminal on this computer");
  }

  function askToFetch(job, said) {
    asking(job, "Asking " + job.machine + " what a fetch would bring…",
      ask("GET", "/api/remote/fetch-sizes?job=" + encodeURIComponent(job.name)),
      function (sizes) {
        if (!sizes.ok) return { said: forThePage(sizes.error || "It could not be asked.") };
        // Drawn once the row is drawn again, below.
        setTimeout(function () { offerFetch(job, sizes); }, 0);
        return { said: "" };
      });
  }

  function offerFetch(job, sizes) {
    var row = rowOf(job.name);
    if (!row) return;
    var said = row.querySelector(".remote-job-said");
    said.innerHTML = "";
    if (!sizes.run_written) {
      said.appendChild(el("div", null, "The job ended before its run wrote anything; "
        + "its folder holds only the job's log, which says why."));
    }
    var withTrajectory = el("input");
    withTrajectory.type = "checkbox";
    var choice = el("label", "small");
    choice.appendChild(withTrajectory);
    choice.appendChild(document.createTextNode(" With the trajectory ("
      + size(sizes.trajectory_bytes) + " in " + sizes.trajectory_files + " file"
      + (sizes.trajectory_files === 1 ? "" : "s") + ")"));
    var bringing = el("div", null);
    function sayBringing() {
      var n = withTrajectory.checked ? sizes.bringing["with"] : sizes.bringing.without;
      bringing.textContent = "Bring " + size(n) + " into " + job.results + "?";
      return n;
    }
    sayBringing();
    withTrajectory.addEventListener("change", sayBringing);
    said.appendChild(bringing);
    if (sizes.trajectory_files) said.appendChild(choice);
    var answers = el("div", "remote-plan-answers");
    var yes = button("primary-btn", "Fetch");
    var no = button("ghost-btn", "Not now");
    answers.appendChild(yes);
    answers.appendChild(no);
    said.appendChild(answers);
    yes.focus();
    no.addEventListener("click", function () {
      doing[job.name] = {};
      redraw(job, "fetch");
    });
    yes.addEventListener("click", function () {
      var n = sayBringing();
      var body = { job: job.name, with_trajectory: withTrajectory.checked, bringing: n };
      asking(job, "Fetching " + size(n) + "…", ask("POST", "/api/remote/fetch", body),
        function (got) {
          if (!got.ok) {
            return { said: forThePage(got.error || "It was not fetched.")
              + (got.bringing !== undefined ? " It would bring " + size(got.bringing)
                + " now." : "") };
          }
          // The study it brought is one of the workspace's now.
          var where = byId("studies-path");
          if (window.FastMDXStudies && window.FastMDXStudies.load) {
            window.FastMDXStudies.load(where && where.value ? where.value : undefined);
          }
          return { job: got.job, said: "Fetched into " + got.job.results + "."
            + ((got.warnings || []).length ? " " + got.warnings.map(forThePage).join(" ")
              : "") };
        }, "fetch");
    });
  }

  function askToStop(job, said, start) {
    said.innerHTML = "";
    said.appendChild(el("span", null, "Stop " + job.name + " on " + job.machine
      + "? What it wrote so far stays there and can be fetched."));
    var answers = el("div", "remote-plan-answers");
    var yes = button("danger-btn", "Yes, stop it");
    var no = button("ghost-btn", "Not now");
    answers.appendChild(yes);
    answers.appendChild(no);
    said.appendChild(answers);
    start.disabled = true;
    no.focus();
    no.addEventListener("click", function () {
      doing[job.name] = {};
      redraw(job, "stop");
    });
    yes.addEventListener("click", function () {
      asking(job, "Asking " + job.machine + " to stop it…",
        ask("POST", "/api/remote/cancel", { job: job.name }), function (got) {
          if (!got.ok) return { said: forThePage(got.error || "It could not be stopped.") };
          return { job: got.job, said: got.stopped ? "Asked to stop: it has stopped."
            : "Asked to stop; it may take a moment. Ask how it is doing to see." };
        }, "ask");
    });
  }

  // ------------------------------------------------------------ wiring

  /* Whether a question is open in a section (a plan shown, a fetch or a
   * stop asked): it is not drawn again from under the person. */
  function askedIn(selector) {
    return Boolean(document.querySelector(selector));
  }

  function refresh(page) {
    if (page !== "run" && page !== "studies") return;
    machinesKnown().then(function () {
      if (page === "run") staleTakenAway();
      if (page === "run" && !busy.run && !askedIn("#run-remote-planned:not([hidden])")) {
        renderRunOn();
      }
      if (page === "studies" && !busy.studies && !askedIn(".remote-job-said button")) {
        renderJobs();
      }
    });
  }

  function attach() {
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("navigate", function (detail) {
        refresh(detail && detail.page);
      });
    }
    var shown = document.querySelector("section.page:not([hidden])");
    if (shown) refresh(shown.getAttribute("data-page"));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }

  window.FastMDXRemote = { refresh: refresh };
})();
