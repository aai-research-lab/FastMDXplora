/* Human review of the exact configuration, builder baseline and study. */
(function () {
  "use strict";
  const el = id => document.getElementById(id);
  let pending = null;
  function state() { return JSON.parse(JSON.stringify(window.FastMDXRun.currentState())); }
  function study() { return window.FastMDXDashboard?.state.appState?.active_run || null; }
  async function post(payload) {
    const response = await fetch("/api/agent/review-draft", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    return response.json();
  }
  function unchanged(request) {
    return JSON.stringify(request.builder_state) === JSON.stringify(state()) && request.study === study();
  }
  async function review(config, purpose = "draft") {
    if (pending) return null;
    const request = {config: config ? JSON.parse(JSON.stringify(config)) : null, purpose: purpose,
      builder_state: state(), study: study()};
    const receipt = await post(request);
    if (!receipt.ok) throw new Error(receipt.error || "Could not validate the draft.");
    if (!unchanged(request)) throw new Error("The draft or study changed. Open review again.");
    el("draft-review-heading").textContent = purpose === "run" ? "Review the final Agent draft" : "Review the suggested draft";
    el("draft-review-notice").textContent = receipt.notice;
    el("draft-review-study").textContent = "Study: " + (receipt.study || "New study") + ". Review expires after 10 minutes.";
    el("draft-review-yaml").textContent = receipt.yaml;
    const rows = el("draft-review-rows"); rows.replaceChildren();
    for (const change of receipt.changes) {
      const row = document.createElement("tr"), field = document.createElement("th"); field.scope = "row";
      const name = document.createElement("strong"), help = document.createElement("p");
      name.textContent = change.field; help.textContent = change.help;
      field.append(name, help); row.append(field);
      for (const key of ["before", "after"]) {
        const cell = document.createElement("td");
        cell.textContent = change[key + "_present"] ? JSON.stringify(change[key]) : "Not specified";
        if (!change[key + "_present"] && change.has_schema) cell.append(document.createTextNode("; schema default: " + JSON.stringify(change.default)));
        row.append(cell);
      }
      rows.append(row);
    }
    el("draft-review-confirmed").checked = false;
    el("draft-review-accept").disabled = true;
    el("draft-review-accept").textContent = purpose === "run" ? "Confirm and run this draft" : "Add reviewed draft";
    el("draft-review-status").textContent = purpose === "run" ? "This confirmation starts the reviewed draft using the existing Run here control." : "Adding changes only the builder draft. Nothing is run or saved.";
    return new Promise(resolve => {
      pending = {request, receipt, resolve};
      el("draft-review-dialog").showModal();
      el("draft-review-cancel").focus();
    });
  }
  document.addEventListener("DOMContentLoaded", () => {
    const dialog = el("draft-review-dialog");
    dialog.addEventListener("close", () => { if (pending) { const done = pending; pending = null; done.resolve(null); } });
    el("draft-review-cancel").addEventListener("click", () => dialog.close());
    el("draft-review-confirmed").addEventListener("change", () => { el("draft-review-accept").disabled = !el("draft-review-confirmed").checked; });
    el("draft-review-accept").addEventListener("click", async () => {
      if (!pending || !el("draft-review-confirmed").checked) return;
      el("draft-review-accept").disabled = true;
      const current = pending;
      try {
        if (!unchanged(current.request)) throw new Error("The draft or study changed. Cancel and review again.");
        const result = await post({...current.request, action:"accept", confirmed:true, review_token:current.receipt.review_token});
        if (!result.ok) throw new Error(result.error || "Review could not be accepted.");
        if (!pending || pending !== current || !unchanged(current.request)) throw new Error("The draft or study changed. Review again.");
        pending = null;
        dialog.close();
        current.resolve({...result, review_token:current.receipt.review_token, builder_state:current.request.builder_state});
      } catch (error) { el("draft-review-status").textContent = error.message; }
    });
  });
  window.FastMDXReview = {review};
})();
