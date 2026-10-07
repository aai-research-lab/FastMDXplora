/* What the Files page does to the study, each after asking: a data
 * deposit of it, written into its deposit/ folder (deposit.py), and the
 * scratch (the Viewer's, and the live view's snapshots) cleared, on the
 * person's own computer. Each only where the server offers it
 * (files_page.py).
 */
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }
  function files() { return window.FastMDXFiles; }

  /* ---- Clearing the scratch ---------------------------------------- */
  function said(text) {
    var line = byId("files-clear-said");
    if (line) line.textContent = text;
  }

  function askToClear(opener) {
    var dialog = byId("files-clear-dialog");
    if (!dialog || !window.FastMDXDialog) return;
    var go = byId("files-clear-go");
    var kept = byId("files-clear-kept");
    go.disabled = true;
    kept.hidden = true;
    kept.innerHTML = "";
    said("Looking at what can go…");
    window.FastMDXDialog.open("files-clear-dialog", opener);
    files().post("/api/files/clear-scratch", { dry: true }).then(function (plan) {
      if (!plan || !plan.ok) {
        said((plan && plan.error) || "The scratch cannot be cleared now.");
        return null;
      }
      if (!plan.files) {
        said("Nothing can be cleared now.");
      } else {
        var text = plan.files + " file" + (plan.files === 1 ? "" : "s") + ", " + files().human(plan.bytes) +
             ": the frames sent to the Viewer and what it computed from them, which it writes " +
             "again from the trajectory the next time it needs them";
        if (plan.snapshots) {
          text += ", and " + plan.snapshots + " of the live view's snapshots of a finished run, " +
                  "which nothing writes again: they go for good";
        }
        said(text + ". The trajectory, the analyses and the report are not touched.");
        go.disabled = false;
      }
      (plan.kept || []).forEach(function (item) {
        var li = document.createElement("li");
        li.textContent = (item.run ? item.run + "/" : "The study") + "'s snapshots are kept: " + item.why + ".";
        kept.appendChild(li);
        kept.hidden = false;
      });
      return plan;
    });
  }

  function clearNow() {
    var go = byId("files-clear-go");
    go.disabled = true;
    said("Clearing…");
    files().post("/api/files/clear-scratch", {}).then(function (done) {
      if (!done || !done.ok) {
        said((done && done.error) || "The scratch could not be cleared.");
        return;
      }
      if (window.FastMDXDialog) window.FastMDXDialog.close("files-clear-dialog");
      files().toast("Cleared " + done.files + " file" + (done.files === 1 ? "" : "s") + ", " +
                    files().human(done.bytes) + ".", "ok");
      files().refresh();
    });
  }

  /* ---- A data deposit ---------------------------------------------- */
  var plan = null;
  var asked = 0;

  function escapeHTML(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function ticked() {
    return Array.prototype.slice.call(document.querySelectorAll("#files-deposit-sets input[data-set]:checked"))
      .map(function (box) { return box.getAttribute("data-set"); });
  }

  function figures() {
    var pick = byId("files-deposit-figures");
    return pick ? pick.value : "svg";
  }

  /* What the sets ticked come to: summed at once, then as the server
   * counts it, a picture the report shows and a figure counted once. */
  function totals(exact) {
    if (!plan) return;
    var keys = ticked();
    var count = 0, bytes = 0;
    plan.sets.forEach(function (set) {
      if (keys.indexOf(set.key) !== -1) { count += set.files; bytes += set.bytes; }
    });
    if (exact) { count = exact.files; bytes = exact.bytes; }
    var said = byId("files-deposit-said");
    said.innerHTML = escapeHTML(files().human(bytes) + " in " + count + " file" + (count === 1 ? "" : "s")) +
      ' · written to <span class="files-mono">' + escapeHTML(plan.where) + "</span>";
    byId("files-deposit-go").disabled = !count;
  }

  function drawSets() {
    var host = byId("files-deposit-sets");
    var rows = plan.sets.map(function (set) {
      var none = !set.files;
      var pick = set.key === "figures"
        ? ' · <select id="files-deposit-figures" aria-label="Which figures">' +
          '<option value="svg"' + (plan.figures === "svg" ? " selected" : "") + ">SVG, to scale</option>" +
          '<option value="png"' + (plan.figures === "png" ? " selected" : "") + ">PNG, to open anywhere</option></select>"
        : "";
      return '<label class="files-dep-row' + (none ? " files-off" : "") + '">' +
        '<input type="checkbox" data-set="' + escapeHTML(set.key) + '"' + (set.on ? " checked" : "") +
        (none ? " disabled" : "") + ">" +
        "<span><b>" + escapeHTML(set.title) + "</b> · " + escapeHTML(set.what) + pick + "</span>" +
        '<span class="files-dep-size">' + (none ? "none" : escapeHTML(files().human(set.bytes))) + "</span></label>";
    });
    var left = plan.left_out || {};
    rows.push('<div class="files-dep-row files-off"><input type="checkbox" disabled aria-label="Never deposited">' +
      "<span><b>Scratch, what --rerun set aside, the bundle</b> · never deposited</span>" +
      '<span class="files-dep-size">' + escapeHTML(files().human(left.bytes || 0)) + "</span></div>");
    host.innerHTML = '<legend class="files-sr">What goes in the deposit</legend>' + rows.join("");
  }

  function askPlan(keepTicks) {
    var keys = keepTicks ? ticked() : null;
    var url = "/api/files/deposit?figures=" + encodeURIComponent(figures());
    if (keys) url += "&sets=" + encodeURIComponent(keys.join(","));
    var mine = ++asked;
    return fetch(url, { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (said) {
      if (mine !== asked) return;
      if (!said || !said.ok) {
        byId("files-deposit-readme").textContent = (said && (said.error || said.reason)) ||
          "The deposit could not be planned.";
        return;
      }
      plan = said;
      if (!keepTicks) drawSets();
      else {
        // The sizes of the figures follow the kind chosen.
        plan.sets.forEach(function (set) {
          var box = document.querySelector('#files-deposit-sets input[data-set="' + set.key + '"]');
          var size = box && box.closest(".files-dep-row").querySelector(".files-dep-size");
          if (size) size.textContent = set.files ? files().human(set.bytes) : "none";
        });
      }
      byId("files-deposit-readme").textContent = said.readme;
      totals(said.chosen);
    }).catch(function () {
      byId("files-deposit-readme").textContent = "The server did not answer.";
    });
  }

  function askToDeposit(opener) {
    var dialog = byId("files-deposit-dialog");
    if (!dialog || !window.FastMDXDialog) return;
    plan = null;
    byId("files-deposit-done").hidden = true;
    byId("files-deposit-go").disabled = true;
    byId("files-deposit-go").textContent = "Write the deposit";
    byId("files-deposit-readme").textContent = "Reading the study…";
    byId("files-deposit-said").textContent = "";
    window.FastMDXDialog.open("files-deposit-dialog", opener);
    askPlan(false);
  }

  var later = null;
  document.addEventListener("change", function (event) {
    if (!event.target.closest || !event.target.closest("#files-deposit-sets")) return;
    totals();
    clearTimeout(later);
    later = setTimeout(function () { askPlan(true); }, 250);
  });

  function depositNow() {
    var go = byId("files-deposit-go");
    var done = byId("files-deposit-done");
    go.disabled = true;
    go.textContent = "Writing…";
    done.hidden = false;
    done.textContent = "Writing the deposit and the SHA-256 of every file. A long trajectory takes a while.";
    files().post("/api/files/deposit", { sets: ticked(), figures: figures() }).then(function (said) {
      go.textContent = "Write the deposit";
      if (!said || !said.ok) {
        done.textContent = (said && said.error) || "The deposit could not be written.";
        go.disabled = false;
        return;
      }
      done.innerHTML = "Written: " + '<a href="' + escapeHTML(said.href) + '" download class="files-mono">' +
        escapeHTML(said.path) + "</a>, " + escapeHTML(files().human(said.bytes)) + ", " + said.files +
        " files. Its SHA-256: " + '<span class="files-mono files-sha">' + escapeHTML(said.sha256) + "</span>";
      files().toast("The deposit is written.", "ok");
      files().refresh();
    });
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    var deposit = target.closest && target.closest("#files-page [data-deposit]");
    if (deposit) {
      event.preventDefault();
      askToDeposit(deposit);
      return;
    }
    if (target.id === "files-deposit-go") { depositNow(); return; }
    var clear = target.closest && target.closest("#files-page [data-clear]");
    if (clear) {
      event.preventDefault();
      askToClear(clear);
      return;
    }
    if (target.id === "files-clear-go") clearNow();
  });
}());
