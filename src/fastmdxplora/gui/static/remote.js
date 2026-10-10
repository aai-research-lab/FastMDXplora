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

  function machinesKnown() {
    return ask("GET", "/api/remote/machines").then(function (said) {
      machines = said.ok ? (said.machines || []) : [];
      jobs = said.ok ? (said.jobs || []) : [];
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

  function renderRunOn() {
    var host = byId("run-remote");
    if (!host) return;
    host.innerHTML = "";
    var usable = machines.filter(function (m) { return m && m.name; });
    host.hidden = usable.length === 0;
    if (!usable.length) return;
    var row = el("div", "remote-run-row");
    var label = el("label", "small muted", "Or on one of your machines");
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
    function sync() {
      go.disabled = !builderReady();
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

  function watchReady(sync) {
    if (readyWatched) readyWatched.disconnect();
    readyWatched = new MutationObserver(sync);
    ["run-start-button", "run-as-is"].forEach(function (id) {
      var node = byId(id);
      if (node) readyWatched.observe(node, { attributes: true, attributeFilter: ["disabled"] });
    });
    var path = byId("run-config-path");
    if (path) path.addEventListener("input", sync);
  }

  function planSend(machine, host, note, go) {
    go.disabled = true;
    host.hidden = true;
    host.innerHTML = "";
    note.dataset.ok = "";
    note.textContent = "Asking " + machine + " what a send would do…";
    var body = whatIsPlanned();
    body.machine = machine;
    ask("POST", "/api/remote/plan", body).then(function (said) {
      go.disabled = !builderReady();
      if (!said.ok) {
        note.textContent = said.error || "It could not be planned.";
        note.dataset.ok = "false";
        return;
      }
      note.textContent = "";
      showPlan(said, host, note, go);
    });
  }

  function line(list, text) {
    list.appendChild(el("li", null, text));
  }

  function showPlan(plan, host, note, go) {
    host.innerHTML = "";
    host.hidden = false;
    host.appendChild(el("div", "remote-plan-title", "Send to " + plan.machine + "?"));
    var facts = el("dl", "remote-plan-facts");
    [["Runs in", plan.runs_in + " on " + plan.machine + " (" + plan.scheduler + ")"],
     ["Its folder there", plan.folder],
     ["Config", plan.config],
     ["Results come back to", plan.results + " when fetched"]].forEach(function (pair) {
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
    var expiry = setTimeout(function () {
      send.disabled = true;
      kept.textContent = "This plan is no longer kept. Plan the send again.";
    }, (plan.kept_s || 600) * 1000);
    not.addEventListener("click", function () {
      clearTimeout(expiry);
      host.innerHTML = "";
      host.hidden = true;
    });
    send.addEventListener("click", function () {
      clearTimeout(expiry);
      send.disabled = true;
      not.disabled = true;
      note.dataset.ok = "";
      note.textContent = "Sending to " + plan.machine + "…";
      ask("POST", "/api/remote/send", { plan: plan.plan }).then(function (sent) {
        host.innerHTML = "";
        host.hidden = true;
        if (!sent.ok) {
          note.textContent = sent.error || "It was not sent.";
          note.dataset.ok = "false";
          return;
        }
        note.dataset.ok = "true";
        note.textContent = "Sent to " + plan.machine + " as job " + sent.job.name
          + ". It runs there on its own, whether or not this page stays open. ";
        var open = button("builder-linkish", "Follow it under Remote jobs");
        open.addEventListener("click", function () {
          if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
            window.FastMDXDashboard.navigate("studies");
          }
        });
        note.appendChild(open);
        machinesKnown().then(renderJobs);
      });
    });
    go.focus();
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
    host.hidden = jobs.length === 0;
    if (!jobs.length) return;
    var head = el("div", "remote-jobs-head");
    var title = el("h2", "card-title", "Remote jobs");
    title.id = "remote-jobs-title";
    head.appendChild(title);
    head.appendChild(el("span", "muted small",
      "Studies sent from this workspace to your machines."));
    host.appendChild(head);
    var list = el("ul", "remote-jobs-list");
    jobs.slice().sort(function (a, b) {
      return String(b.submitted_at || "").localeCompare(String(a.submitted_at || ""));
    }).forEach(function (job) { list.appendChild(jobRow(job)); });
    host.appendChild(list);
  }

  function jobRow(job) {
    var row = el("li", "remote-job");
    row.dataset.job = job.name;
    var top = el("div", "remote-job-top");
    top.appendChild(el("span", "remote-job-name mono", job.name));
    var state = el("span", "remote-job-state", STATES[job.state] || job.state);
    state.dataset.state = job.state;
    top.appendChild(state);
    top.appendChild(el("span", "muted small", "on " + job.machine
      + (job.submitted_at ? ", sent " + String(job.submitted_at).replace("T", " ").slice(0, 16)
        : "")));
    row.appendChild(top);
    var where = el("div", "muted small mono", "Results: " + job.results
      + (job.fetched_at ? " (fetched)" : ""));
    row.appendChild(where);
    if (job.detail) row.appendChild(el("div", "small", job.detail));
    var actions = el("div", "remote-job-actions");
    var check = button("ghost-btn", "Ask how it is doing");
    actions.appendChild(check);
    var fetchIt = null;
    var cancel = null;
    if (!GOING[job.state]) {
      fetchIt = button("ghost-btn", job.fetched_at ? "Fetch again" : "Fetch the results");
      actions.appendChild(fetchIt);
    } else {
      cancel = button("ghost-btn", "Stop it");
      actions.appendChild(cancel);
    }
    row.appendChild(actions);
    var said = el("div", "remote-job-said small");
    said.setAttribute("role", "status");
    row.appendChild(said);
    check.addEventListener("click", function () {
      check.disabled = true;
      said.textContent = "Asking " + job.machine + "…";
      ask("GET", "/api/remote/job?job=" + encodeURIComponent(job.name)).then(function (got) {
        check.disabled = false;
        if (!got.ok) { said.textContent = got.error || "It could not be asked."; return; }
        replace(row, got.job);
      });
    });
    if (fetchIt) fetchIt.addEventListener("click", function () { askToFetch(job, said, fetchIt); });
    if (cancel) cancel.addEventListener("click", function () { askToStop(job, said, cancel); });
    return row;
  }

  function replace(row, job) {
    jobs = jobs.map(function (j) { return j.name === job.name ? job : j; });
    var fresh = jobRow(job);
    row.parentNode.replaceChild(fresh, row);
    return fresh;
  }

  function askToFetch(job, said, start) {
    start.disabled = true;
    said.innerHTML = "";
    said.textContent = "Asking " + job.machine + " what a fetch would bring…";
    ask("GET", "/api/remote/fetch-sizes?job=" + encodeURIComponent(job.name)).then(function (sizes) {
      start.disabled = false;
      said.innerHTML = "";
      if (!sizes.ok) { said.textContent = sizes.error || "It could not be asked."; return; }
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
      no.addEventListener("click", function () { said.innerHTML = ""; });
      yes.addEventListener("click", function () {
        yes.disabled = true;
        no.disabled = true;
        var n = sayBringing();
        var body = { job: job.name, with_trajectory: withTrajectory.checked, bringing: n };
        said.innerHTML = "";
        said.textContent = "Fetching " + size(n) + "…";
        ask("POST", "/api/remote/fetch", body).then(function (got) {
          if (!got.ok) {
            said.textContent = (got.error || "It was not fetched.")
              + (got.bringing !== undefined ? " It would bring " + size(got.bringing) + " now." : "");
            return;
          }
          var row = said.closest(".remote-job");
          var fresh = replace(row, got.job);
          var done = fresh.querySelector(".remote-job-said");
          done.textContent = "Fetched into " + got.job.results + "."
            + ((got.warnings || []).length ? " " + got.warnings.join(" ") : "");
          // The study it brought is one of the workspace's now.
          var where = byId("studies-path");
          if (window.FastMDXStudies && window.FastMDXStudies.load) {
            window.FastMDXStudies.load(where && where.value ? where.value : undefined);
          }
        });
      });
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
    no.addEventListener("click", function () { said.innerHTML = ""; start.disabled = false; });
    yes.addEventListener("click", function () {
      yes.disabled = true;
      no.disabled = true;
      said.innerHTML = "";
      said.textContent = "Asking " + job.machine + " to stop it…";
      ask("POST", "/api/remote/cancel", { job: job.name }).then(function (got) {
        start.disabled = false;
        if (!got.ok) { said.textContent = got.error || "It could not be stopped."; return; }
        var row = said.closest(".remote-job");
        var fresh = replace(row, got.job);
        fresh.querySelector(".remote-job-said").textContent = got.stopped
          ? "Asked to stop: it is stopping." : "Asked to stop; it may take a moment.";
      });
    });
  }

  // ------------------------------------------------------------ wiring

  function refresh(page) {
    if (page !== "run" && page !== "studies") return;
    machinesKnown().then(function () {
      if (page === "run") renderRunOn();
      if (page === "studies") renderJobs();
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
