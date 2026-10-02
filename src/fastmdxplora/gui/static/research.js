/* Structured dashboard context and persistent study-local research bookmarks. */
(function () {
  "use strict";
  const el = (id) => document.getElementById(id);
  let analysis = null, field = null, study = null, rows = [], editing = null;
  let docked = false, origin = null, loadedStudy = null;
  let refreshGeneration = 0;
  let warning = null, auditEvent = null;
  let enabled = localStorage.getItem("fastmdx-agent-sidebar-enabled") !== "false";
  const board = () => window.FastMDXDashboard;
  const molecule = () => window.FastMDXMoleculeViewer;
  function capture() {
    const state = board()?.state || {};
    const page = state.activePage === "agent" ? origin || "overview" : state.activePage;
    const view = {page: page || "overview"};
    if (state.appState?.active_run) view.study = state.appState.active_run;
    if (warning) view.warning = warning;
    if (auditEvent) view.audit_event = auditEvent;
    if (page === "analysis" && analysis) {
      view.analysis = analysis;
      const range = window.FastMDXSeries?.getRange(analysis);
      if (range) view.range = range;
    }
    if (page === "run" && field) view.field = field;
    if (page === "viewer") {
      const viewer = molecule()?.STATE;
      if (viewer?.researchSelection) view.selection = viewer.researchSelection;
      if (viewer?.viewer?.getView) view.camera = viewer.viewer.getView();
      if (viewer?.playbackLoaded) {
        view.frame = Number(el("traj-slider")?.value || 0);
        view.playback_signature = viewer.playbackSignature;
      }
    }
    return JSON.parse(JSON.stringify(view));
  }
  function describe(view) {
    return [view.page, view.analysis, view.field,
      view.selection ? [view.selection.resname, view.selection.chain,
        view.selection.resseq].join(" ") : null,
      view.frame != null ? "frame " + view.frame : null,
      view.range ? "range " + view.range.join("–") : null].filter(Boolean).join(" · ");
  }
  function updateContext() {
    el("research-context").textContent = "Context: " + describe(capture());
  }
  function setDock(open) {
    const page = document.querySelector('[data-page="agent"]');
    const dock = el("research-agent-dock");
    if (!page || !dock) return;
    open = open && enabled;
    docked = open;
    document.body.classList.toggle("research-agent-open", open);
    dock.hidden = !open;
    if (open) {
      dock.appendChild(page);
      page.hidden = false;
      el("agent-request").focus();
    } else {
      el("research-agent-anchor").after(page);
      page.hidden = board()?.state.activePage !== "agent";
    }
    el("research-agent-toggle").setAttribute("aria-expanded", String(open));
    molecule()?.resize();
  }
  async function refresh() {
    const generation = ++refreshGeneration;
    const response = await fetch("/api/research/bookmarks");
    const data = await response.json();
    if (generation !== refreshGeneration) return;
    study = data.study || null;
    rows = data.bookmarks || [];
    render();
    if (!data.ok) el("research-status").textContent = data.error;
  }
  function status(text) { el("research-status").textContent = text; }
  async function write(body) {
    try {
      const response = await fetch("/api/research/bookmarks", {
        method: "POST", headers: {"content-type": "application/json"},
        body: JSON.stringify(Object.assign({study: study}, body))
      });
      const data = await response.json();
      if (!data.ok) { status(data.error); return false; }
      rows = data.bookmarks; render(); return true;
    } catch (_) { status("Could not reach the server. Your note is still in the form."); return false; }
  }
  function button(label, action, parent) {
    const node = document.createElement("button");
    node.type = "button"; node.className = "ghost-btn"; node.textContent = label;
    node.addEventListener("click", action); parent.appendChild(node);
  }
  function render() {
    const host = el("research-list"); host.replaceChildren();
    el("research-save").disabled = !study;
    el("research-export").disabled = !rows.length;
    if (!rows.length) status(study ? "No bookmarks yet. Save a view and a note." : "Load a study to save bookmarks.");
    rows.slice().reverse().forEach((row) => {
      const card = document.createElement("article"); card.className = "research-bookmark";
      const title = document.createElement("strong"); title.textContent = row.title;
      const meta = document.createElement("div"); meta.className = "muted small";
      meta.textContent = describe(row.view || {});
      const note = document.createElement("p"); note.textContent = row.note;
      card.append(title, meta, note);
      button("Restore", () => restore(row.view), card);
      button("Edit note", () => {
        editing = row; el("research-title").value = row.title;
        el("research-note").value = row.note; el("research-save").textContent = "Update note";
      }, card);
      button("Delete", async () => {
        if (!window.confirm('Delete bookmark "' + row.title + '"?')) return;
        if (await write({action: "delete", id: row.id})) status("Bookmark deleted.");
      }, card);
      host.appendChild(card);
    });
  }
  async function restore(view) {
    if (study && study !== board()?.state.appState.active_run) {
      status("The study changed. Reload bookmarks before restoring."); return;
    }
    if (view.analysis) {
      analysis = view.analysis;
      window.FastMDXSeries?.setRange(view.analysis, view.range || null);
      board()?.showAnalysis(view.analysis);
    } else board()?.navigate(view.page || "overview");
    if (view.page === "viewer") {
      status("Restoring viewer…");
      const result = await molecule()?.restoreResearchView(view);
      status(result || "The viewer is unavailable.");
    } else status("Bookmark restored.");
    updateContext();
  }
  document.addEventListener("DOMContentLoaded", () => {
    const page = document.querySelector('[data-page="agent"]');
    const anchor = document.createElement("span"); anchor.id = "research-agent-anchor";
    page.before(anchor);
    board()?.on("navigate", (event) => {
      if (event.page === "agent") { if (docked) setDock(false); }
      else {
        origin = event.page;
        if (docked) page.hidden = false;
      }
      updateContext();
    });
    board()?.on("app-state", (state) => {
      const next = state.active_run || null;
      if (next !== loadedStudy) {
        loadedStudy = next; analysis = null; field = null; warning = null; auditEvent = null;
        if (molecule()) molecule().STATE.researchSelection = null;
        editing = null; el("research-title").value = ""; el("research-note").value = "";
        el("research-save").textContent = "Save bookmark";
        refresh().catch(() => status("Could not load bookmarks."));
      }
    });
    document.addEventListener("click", (event) => {
      const card = event.target.closest(".analysis-card[data-analysis]");
      if (card) { analysis = card.dataset.analysis; warning = null; auditEvent = null; updateContext(); }
      const issue = event.target.closest("[data-research-warning]");
      if (issue) { warning = issue.dataset.researchWarning; updateContext(); }
      const change = event.target.closest("[data-research-audit]");
      if (change) { auditEvent = change.dataset.researchAudit; updateContext(); }
    });
    document.addEventListener("focusin", (event) => {
      const input = event.target.closest("[data-research-field]");
      if (input) { field = input.dataset.researchField; updateContext(); }
    });
    el("research-agent-toggle").addEventListener("click", () => {
      origin = board()?.state.activePage === "agent" ? origin : board()?.state.activePage;
      setDock(!docked); updateContext();
    });
    el("research-agent-close").addEventListener("click", () => setDock(false));
    const preference = el("research-agent-enabled");
    preference.checked = enabled;
    el("research-agent-toggle").hidden = !enabled;
    preference.addEventListener("change", () => {
      enabled = preference.checked;
      localStorage.setItem("fastmdx-agent-sidebar-enabled", String(enabled));
      el("research-agent-toggle").hidden = !enabled;
      if (!enabled) { setDock(false); window.FastMDXAgent?.cancelReply(); }
    });
    el("research-explain-residue").addEventListener("click", () => {
      ask("Explain the selected residue using its recorded analysis and preparation evidence. What is known, and what cannot be established from this frame?");
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        el("research-bookmarks").hidden = true;
        if (docked) setDock(false);
      }
    });
    el("research-bookmarks-toggle").addEventListener("click", () => {
      el("research-bookmarks").hidden = !el("research-bookmarks").hidden;
      if (!el("research-bookmarks").hidden) refresh().catch(() => status("Could not load bookmarks."));
    });
    el("research-bookmarks-close").addEventListener("click", () => { el("research-bookmarks").hidden = true; });
    el("research-save").addEventListener("click", async () => {
      if (await write({action: "save", id: editing?.id, title: el("research-title").value,
        note: el("research-note").value, view: editing ? editing.view : capture()})) {
        editing = null; el("research-save").textContent = "Save bookmark";
        el("research-title").value = ""; el("research-note").value = ""; status("Bookmark saved.");
      }
    });
    el("research-cancel-edit").addEventListener("click", () => {
      editing = null; el("research-save").textContent = "Save bookmark";
      el("research-title").value = ""; el("research-note").value = "";
    });
    el("research-export").addEventListener("click", () => {
      const blob = new Blob([JSON.stringify({version: 1, study: study, bookmarks: rows}, null, 2)],
        {type: "application/json"});
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = "research-bookmarks.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    board()?.on("research-selection", () => {
      el("research-explain-residue").disabled = !molecule()?.STATE.researchSelection;
      warning = null; auditEvent = null; updateContext();
    });
    board()?.on("research-frame", updateContext);
    board()?.on("research-analysis", (event) => { analysis = event.analysis; updateContext(); });
    updateContext();
  });
  function ask(text) {
    if (!enabled) { el("research-status").textContent = "Enable the Agent sidebar in Settings to ask about this selection."; return; }
    origin = board()?.state.activePage === "agent" ? origin : board()?.state.activePage;
    setDock(true); updateContext();
    el("agent-request").value = text;
    el("agent-request").focus();
  }
  window.FastMDXResearch = {capture: capture, restore: restore, describe: describe, ask: ask, enabled: () => enabled,
    selectAudit: (id) => { auditEvent = id; warning = null; updateContext(); }};
})();
