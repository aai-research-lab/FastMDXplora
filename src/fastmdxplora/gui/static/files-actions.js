/* What the Files page does to the study, each after asking: clearing the
 * scratch the Viewer and the live view write again when needed. Only on
 * the person's own computer, where the server offers it (files_page.py).
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

  document.addEventListener("click", function (event) {
    var target = event.target;
    var clear = target.closest && target.closest("#files-page [data-clear]");
    if (clear) {
      event.preventDefault();
      askToClear(clear);
      return;
    }
    if (target.id === "files-clear-go") clearNow();
  });
}());
