/* Structured dashboard context and persistent study-local research bookmarks. */
(function () {
  "use strict";
  const el = (id) => document.getElementById(id);
  let analysis = null, field = null, figure = null, study = null, rows = [], editing = null;
  let docked = false, origin = null, loadedStudy = null;
  let refreshGeneration = 0;
  let warning = null, auditEvent = null, auditSource = null, auditSelection = null;
  let importPreview = null, shownCount = 20;
  let comparisonSelection = null;
  let contextGeneration = 0;
  let tags = ["Simulation settings", "Graph", "Figure", "Trajectory frame", "Structure", "Preparation", "Observation"];
  let enabled = localStorage.getItem("fastmdx-agent-sidebar-enabled") !== "false";
  const board = () => window.FastMDXDashboard;
  const molecule = () => window.FastMDXMoleculeViewer;
  function capture() {
    const state = board()?.state || {};
    const page = state.activePage === "agent" ? origin || "overview" : state.activePage;
    const view = {page: page || "overview"};
    if (state.appState?.active_run) view.study = state.appState.active_run;
    if (warning) view.warning = warning;
    if (page === "overview" && auditEvent) {
      view.audit_event = auditEvent;
      if (auditSource) view.audit_source = auditSource;
      if (auditSelection) view.audit_selection = auditSelection;
      const display = window.FastMDXPreparationAudit?.capture();
      if (display) view.audit_display = display;
    }
    if (["analysis", "report"].includes(page) && analysis) {
      view.analysis = analysis;
      const range = window.FastMDXSeries?.getRange(analysis);
      if (range) view.range = range;
    }
    if (["analysis", "report"].includes(page) && figure) view.figure = figure;
    if (page === "run" && field) {
      view.field = field;
      const control = document.querySelector('[data-research-field="' + CSS.escape(field) + '"]');
      if (control && control.type !== "password") view.field_value = control.type === "checkbox" ? control.checked : control.value.slice(0, 2000);
    }
    if (page === "viewer") {
      const viewer = molecule()?.STATE;
      if (viewer) view.mode = viewer.mode;
      if (viewer?.researchSelection) view.selection = viewer.researchSelection;
      if (comparisonSelection) view.comparison_selection = comparisonSelection;
      if (viewer) view.display = {representation: viewer.representation,
        colorMode: viewer.colorMode, visibility: viewer.visibility,
        pocketSurface: viewer.pocketSurface, pocketOnly: viewer.pocketOnly,
        isolateLigand: viewer.isolateLigand, pocketCutoff: viewer.pocketCutoff,
        ligandResname: viewer.ligandResname};
      if (viewer?.viewer?.getView) view.camera = viewer.viewer.getView();
      if (viewer?.playbackLoaded && viewer.mode === "playback") {
        view.frame = Number(el("traj-slider")?.value || 0);
        view.playback_signature = viewer.playbackSignature;
      }
      if (viewer?.mode === "live" && viewer.liveFrameIndex != null) view.live_step = viewer.liveFrameIndex;
    }
    return JSON.parse(JSON.stringify(view));
  }
  function describe(view) {
    return [view.page, view.analysis, view.field,
      view.selection ? [view.selection.resname, view.selection.chain,
        view.selection.resseq].join(" ") : null,
      view.frame != null ? "frame " + view.frame : view.live_step != null ? "live step " + view.live_step : null,
      view.range ? "range " + view.range.join("–") : null].filter(Boolean).join(" · ");
  }
  function updateContext() {
    const view = capture();
    el("research-context").textContent = "Context: " + describe(view);
    const summary = el("agent-context-summary");
    if (summary) summary.textContent = el("agent-use-context").checked ? describe(view) : "Current view excluded";
  }
  function messageContext() {
    ++contextGeneration;
    const request = {view_context: capture(), include_view_context: el("agent-use-context").checked};
    el("agent-context-evidence").textContent = "Resolving the context for this message…";
    return request;
  }
  function showContext(receipt, title = "Evidence used by the last message") {
    ++contextGeneration;
    el("agent-context-evidence").textContent = receipt.ok ?
      title + "\n\n" + receipt.study_evidence + "\n\n" + receipt.view_evidence +
      "\n\nKnowledge contract: " + receipt.knowledge_version + "\nContext fingerprint: " + receipt.fingerprint :
      receipt.error || "Context is unavailable.";
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
      el("research-bookmarks").hidden = true;
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
    tags = data.tags || tags;
    renderTags();
    render();
    if (!data.ok) el("research-status").textContent = data.error;
  }
  function status(text) { el("research-status").textContent = text; }
  async function write(body) {
    const expectedStudy = study, generation = refreshGeneration;
    try {
      const response = await fetch("/api/research/bookmarks", {
        method: "POST", headers: {"content-type": "application/json"},
        body: JSON.stringify(Object.assign({study: study}, body))
      });
      const data = await response.json();
      if (expectedStudy !== study || generation !== refreshGeneration) { status("The study changed. Reload bookmarks to see the saved state."); return false; }
      if (!data.ok) { status(data.error); return false; }
      rows = data.bookmarks; render(); return true;
    } catch (_) { status("Could not reach the server. Your note is still in the form."); return false; }
  }
  function button(label, action, parent) {
    const node = document.createElement("button");
    node.type = "button"; node.className = "ghost-btn"; node.textContent = label;
    node.addEventListener("click", action); parent.appendChild(node);
  }
  function selectedTags() {
    return Array.from(el("research-tags").querySelectorAll("input:checked")).map((input) => input.value);
  }
  function renderTags(selected = selectedTags()) {
    const host = el("research-tags");
    host.querySelectorAll("label").forEach((node) => node.remove());
    Array.from(new Set(tags.concat(selected))).forEach((tag) => {
      const label = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox"; input.value = tag; input.checked = selected.includes(tag);
      label.append(input, document.createTextNode(" " + tag)); host.append(label);
    });
    const filter = el("research-tag-filter"), previous = filter.value;
    filter.replaceChildren(new Option("All tags", ""));
    Array.from(new Set(tags.concat(rows.flatMap((row) => row.tags || [])))).forEach((tag) => filter.add(new Option(tag, tag)));
    filter.value = previous;
  }
  function suggestTags() {
    if (editing || selectedTags().length) return;
    const view = capture();
    const suggested = view.audit_event ? "Preparation" : view.page === "analysis" ? "Graph"
      : view.page === "viewer" ? (view.frame != null ? "Trajectory frame" : "Structure")
      : view.page === "run" ? "Simulation settings" : "Observation";
    renderTags([suggested]);
  }
  async function screenshot(view) {
    let source;
    if (view.page === "viewer" && molecule()?.STATE.viewer?.pngURI) {
      const image = new Image(); image.src = molecule().STATE.viewer.pngURI();
      await image.decode(); source = image;
    } else {
      let target;
      if (view.figure) target = document.querySelector('.analysis-card[data-research-figure="' + CSS.escape(view.figure) + '"]');
      if (view.audit_event) target = el("preparation-audit-panel");
      if (!target && view.analysis) target = document.querySelector('.page[data-page="' + view.page + '"] .analysis-card[data-analysis="' + CSS.escape(view.analysis) + '"]');
      else if (view.page === "run" && view.field) target = document.querySelector('[data-research-field="' + CSS.escape(view.field) + '"]')?.closest(".builder-field");
      else if (!target && ["overview", "report"].includes(view.page)) target = document.querySelector('.page[data-page="' + view.page + '"] .card');
      if (!target || !window.html2canvas) throw new Error("No screenshot is available for this view. The bookmark can still be saved.");
      source = await window.html2canvas(target, {logging: false, allowTaint: false,
        useCORS: false, scale: Math.min(1, 1280 / Math.max(target.offsetWidth, target.offsetHeight)),
        onclone: (document) => {
          // html2canvas 1.4 does not parse modern CSS color() values. Convert
          // computed colors in its private clone through the browser's canvas
          // color parser; the live dashboard and source figure stay unchanged.
          const swatch = document.createElement("canvas"); swatch.width = swatch.height = 1;
          const context = swatch.getContext("2d", {willReadFrequently: true});
          document.querySelectorAll("*").forEach((node) => {
            const style = document.defaultView.getComputedStyle(node);
            for (const key of ["color", "backgroundColor", "borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor", "outlineColor", "textDecorationColor"]) {
              const value = style[key];
              if (!value || !/color\(|lab\(|lch\(/.test(value)) continue;
              context.clearRect(0, 0, 1, 1); context.fillStyle = value; context.fillRect(0, 0, 1, 1);
              const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
              node.style[key] = `rgba(${r},${g},${b},${a / 255})`;
            }
            if (/color\(|lab\(|lch\(/.test(style.boxShadow)) node.style.boxShadow = "none";
            if (/color\(|lab\(|lch\(/.test(style.backgroundImage)) node.style.backgroundImage = "none";
          });
        },
        ignoreElements: (node) => node.matches?.('input[type="password"], [data-html2canvas-ignore], #research-agent-dock, #research-bookmarks')});
    }
    const canvas = document.createElement("canvas"), scale = Math.min(1, 1280 / Math.max(source.width, source.height));
    canvas.width = Math.max(1, Math.round(source.width * scale));
    canvas.height = Math.max(1, Math.round(source.height * scale));
    const context = canvas.getContext("2d"); context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height); context.drawImage(source, 0, 0, canvas.width, canvas.height);
    let value = canvas.toDataURL("image/png");
    // Bound uploads without changing the molecular or graph source.
    while (value.length > 790000 && canvas.width > 160 && canvas.height > 160) {
      const smaller = document.createElement("canvas"); smaller.width = Math.round(canvas.width * 0.75); smaller.height = Math.round(canvas.height * 0.75);
      smaller.getContext("2d").drawImage(canvas, 0, 0, smaller.width, smaller.height);
      canvas.width = smaller.width; canvas.height = smaller.height; context.drawImage(smaller, 0, 0);
      value = canvas.toDataURL("image/png");
    }
    if (value.length > 790000) throw new Error("The screenshot is too large. The bookmark can still be saved.");
    return value;
  }
  function render() {
    const host = el("research-list"); host.replaceChildren();
    el("research-save").disabled = !study;
    el("research-export").disabled = !rows.length;
    el("research-export-bundle").disabled = !rows.length;
    if (!rows.length) status(study ? "No bookmarks yet. Save a view and a note." : "Load a study to save bookmarks.");
    const search = el("research-search").value.trim().toLocaleLowerCase();
    const tag = el("research-tag-filter").value;
    const filtered = rows.filter((row) => (!tag || (row.tags || []).includes(tag)) &&
      (!search || [row.title, row.note, ...(row.tags || [])].join(" ").toLocaleLowerCase().includes(search)));
    if (rows.length && !filtered.length) {
      const empty = document.createElement("p"); empty.textContent = "No bookmarks match this search."; host.append(empty);
    }
    el("research-more").hidden = filtered.length <= shownCount;
    filtered.slice().reverse().slice(0, shownCount).forEach((row) => {
      const card = document.createElement("article"); card.className = "research-bookmark";
      const title = document.createElement("strong"); title.textContent = row.title;
      const meta = document.createElement("div"); meta.className = "muted small";
      meta.textContent = [describe(row.view || {}), row.view?.figure?.split("/").pop(),
        row.view?.field_value != null ? "Saved draft value: " + String(row.view.field_value) : null,
        (row.tags || []).join(" · ")].filter(Boolean).join(" — ");
      const note = document.createElement("p"); note.textContent = row.note;
      card.append(title, meta, note);
      if (row.screenshot) {
        const image = document.createElement("img"); image.alt = "Saved research view: " + row.title;
        image.className = "research-screenshot"; card.append(image);
        fetch("/api/research/bookmarks/image?id=" + encodeURIComponent(row.id)).then((response) => response.json()).then((data) => {
          if (!image.isConnected) return;
          if (data.ok) image.src = data.image;
          else { image.remove(); const missing = document.createElement("p"); missing.textContent = data.error; card.append(missing); }
        }).catch(() => { if (image.isConnected) image.remove(); });
      }
      button("Restore", async () => {
        status("Checking bookmarked sources…");
        try {
          const response = await fetch("/api/research/bookmarks/restore?id=" + encodeURIComponent(row.id));
          const data = await response.json();
          if (!data.ok) { status(data.error); return; }
          await restore(data.view);
        } catch (_) { status("Could not verify the saved source. Your note and screenshot remain available."); }
      }, card);
      button("Edit note", () => {
        editing = row; el("research-title").value = row.title;
        el("research-screenshot").checked = false; el("research-remove-screenshot").checked = false;
        renderTags(row.tags || []);
        el("research-note").value = row.note; el("research-save").textContent = "Update note";
      }, card);
      button("Delete", async () => {
        if (!window.confirm('Delete bookmark "' + row.title + '"?')) return;
        if (await write({action: "delete", id: row.id, expected_updated_at: row.updated_at})) status("Bookmark deleted.");
      }, card);
      host.appendChild(card);
    });
  }
  async function restore(view) {
    if (study && study !== board()?.state.appState.active_run || view.study && view.study !== board()?.state.appState.active_run) {
      status("The study changed. Reload bookmarks before restoring."); return;
    }
    if (view.analysis) {
      analysis = view.analysis;
      window.FastMDXSeries?.setRange(view.analysis, view.range || null);
      if (view.page === "analysis") board()?.showAnalysis(view.analysis);
      else board()?.navigate(view.page || "report");
    } else board()?.navigate(view.page || "overview");
    if (view.figure) {
      figure = view.figure;
      document.querySelector('.analysis-card[data-research-figure="' + CSS.escape(view.figure) + '"]')?.scrollIntoView({block: "center"});
    }
    if (view.audit_event) {
      await window.FastMDXPreparationAudit?.restore(view.audit_event, view.audit_source, view.audit_selection, view.audit_display);
      status("Preparation bookmark restored.");
    } else if (view.page === "viewer") {
      comparisonSelection = view.comparison_selection || null;
      updateComparison();
      status("Restoring viewer…");
      const result = await molecule()?.restoreResearchView(view);
      updateComparison();
      status(result || "The viewer is unavailable.");
    } else {
      if (view.field) document.querySelector('[data-research-field="' + CSS.escape(view.field) + '"]')?.focus();
      status("Bookmark restored.");
    }
    updateContext();
  }
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelector(".page-shell").prepend(document.querySelector(".research-tools"));
    el("agent-use-context").addEventListener("change", () => {
      ++contextGeneration;
      updateContext();
      el("agent-context-evidence").textContent = "Context preference changed. Inspect again to see the evidence for the next message.";
    });
    el("agent-inspect-context").addEventListener("click", async () => {
      const request = messageContext(), generation = contextGeneration;
      try {
        const response = await fetch("/api/agent/context", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(request)});
        const receipt = await response.json();
        if (generation !== contextGeneration || JSON.stringify(request.view_context) !== JSON.stringify(capture())) return;
        showContext(receipt, "Current evidence preview — reverified at Send");
      } catch (_) {
        if (generation === contextGeneration) el("agent-context-evidence").textContent = "Could not inspect context. Check the dashboard connection.";
      }
    });
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
        window.FastMDXAgent?.cancelReply();
        ++contextGeneration;
        el("agent-context-evidence").textContent = "The study changed. Inspect its context before sending.";
        loadedStudy = next; analysis = null; field = null; figure = null; warning = null; auditEvent = null;
        comparisonSelection = null;
        importPreview = null; el("research-import-preview").hidden = true;
        el("research-import-file").value = ""; shownCount = 20;
        if (molecule()) molecule().STATE.researchSelection = null;
        updateComparison();
        editing = null; el("research-title").value = ""; el("research-note").value = "";
        renderTags([]);
        el("research-save").textContent = "Save bookmark";
        refresh().catch(() => status("Could not load bookmarks."));
      }
    });
    document.addEventListener("click", (event) => {
      const card = event.target.closest(".analysis-card[data-analysis]");
      if (card) { analysis = card.dataset.analysis; warning = null; auditEvent = null; updateContext(); }
      const figureCard = event.target.closest(".analysis-card[data-research-figure]");
      if (figureCard) figure = figureCard.dataset.researchFigure;
      const issue = event.target.closest("[data-research-warning]");
      if (issue) { warning = issue.dataset.researchWarning; updateContext(); }
      const change = event.target.closest("[data-research-audit]");
      if (change) { auditEvent = change.dataset.researchAudit; updateContext(); }
      const bookmark = event.target.closest("[data-research-bookmark]");
      if (bookmark) {
        if (bookmark.dataset.researchBookmark) analysis = bookmark.dataset.researchBookmark;
        el("research-bookmarks").hidden = false;
        refresh().then(suggestTags).catch(() => status("Could not load bookmarks."));
        if (editing) status("Finish editing or press Clear before saving a new view.");
        el("research-title").focus();
      }
    });
    document.addEventListener("focusin", (event) => {
      const input = event.target.closest("[data-research-field]");
      if (input) {
        field = input.dataset.researchField; updateContext();
        const holder = input.closest(".builder-field");
        if (holder && !holder.querySelector("[data-research-bookmark]")) {
          const bookmark = document.createElement("button"); bookmark.type = "button";
          bookmark.className = "ghost-btn"; bookmark.dataset.researchBookmark = "";
          bookmark.textContent = "Bookmark setting"; holder.append(bookmark);
        }
      }
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
    el("research-pin-residue").addEventListener("click", () => {
      const selected = molecule()?.STATE.researchSelection;
      comparisonSelection = selected ? JSON.parse(JSON.stringify(selected)) : null;
      updateComparison(); updateContext();
    });
    el("research-clear-comparison").addEventListener("click", () => {
      comparisonSelection = null; updateComparison(); updateContext();
    });
    el("research-compare-residues").addEventListener("click", () => {
      if (!comparisonSelection || !molecule()?.STATE.researchSelection) return;
      ask("Compare the selected residue with the pinned comparison residue using recorded analysis and preparation evidence for both. Which measured differences are supported, and which possible chemical explanations remain unproven?");
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        el("research-bookmarks").hidden = true;
        if (docked) setDock(false);
      }
    });
    el("research-bookmarks-toggle").addEventListener("click", () => {
      el("research-bookmarks").hidden = !el("research-bookmarks").hidden;
      if (!el("research-bookmarks").hidden) {
        if (docked) setDock(false);
        refresh().then(suggestTags).catch(() => status("Could not load bookmarks."));
        el("research-title").focus();
      }
    });
    el("research-bookmarks-close").addEventListener("click", () => { el("research-bookmarks").hidden = true; });
    el("research-save").addEventListener("click", async () => {
      const captureImage = el("research-screenshot").checked;
      const view = editing && !captureImage ? editing.view : capture();
      const expectedStudy = study, generation = refreshGeneration;
      const request = {action: "save", id: editing?.id, title: el("research-title").value,
        expected_updated_at: editing?.updated_at,
        note: el("research-note").value, tags: selectedTags(), view: view,
        remove_screenshot: el("research-remove-screenshot").checked};
      let image = null, screenshotError = null;
      el("research-save").disabled = true;
      if (captureImage) {
        status("Capturing research view…");
        try { image = await screenshot(view); } catch (error) { screenshotError = error.message; }
      }
      if (study !== expectedStudy || generation !== refreshGeneration) {
        status("The study changed during capture. Save the current view again.");
        el("research-save").disabled = !study; return;
      }
      if (await write({...request, screenshot: image})) {
        editing = null; el("research-save").textContent = "Save bookmark";
        el("research-title").value = ""; el("research-note").value = ""; status(screenshotError ? "Bookmark saved without screenshot. " + screenshotError : "Bookmark saved.");
        el("research-screenshot").checked = false; el("research-remove-screenshot").checked = false;
        renderTags([]);
      }
      el("research-save").disabled = !study;
    });
    el("research-cancel-edit").addEventListener("click", () => {
      editing = null; el("research-save").textContent = "Save bookmark";
      el("research-title").value = ""; el("research-note").value = "";
      renderTags([]);
      el("research-screenshot").checked = false; el("research-remove-screenshot").checked = false;
    });
    async function exportFile(images) {
      status("Preparing bookmark export…");
      try {
        let query = "images=" + (images ? "1" : "0");
        if (el("research-export-filtered").checked) {
          const search = el("research-search").value.trim().toLocaleLowerCase(), tag = el("research-tag-filter").value;
          const matching = rows.filter((row) => (!tag || (row.tags || []).includes(tag)) &&
            (!search || [row.title, row.note, ...(row.tags || [])].join(" ").toLocaleLowerCase().includes(search)));
          if (!matching.length) { status("No bookmarks match this export filter."); return; }
          query += "&ids=" + encodeURIComponent(matching.map((row) => row.id).join(","));
        }
        const response = await fetch("/api/research/bookmarks/export?" + query);
        if (!response.ok) { const data = await response.json(); status(data.error); return; }
        const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement("a");
        link.href = url; link.download = "research-bookmarks." + (images ? "zip" : "json"); link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000); status("Bookmark export ready.");
      } catch (_) { status("Could not export bookmarks. Saved bookmarks remain unchanged."); }
    }
    el("research-export").addEventListener("click", () => exportFile(false));
    el("research-export-bundle").addEventListener("click", () => exportFile(true));
    el("research-import-file").addEventListener("change", async (event) => {
      const file = event.target.files?.[0]; importPreview = null;
      el("research-import-preview").hidden = true;
      if (!file) return;
      if (file.size > 32000000) { status("Import a file up to 32 MB."); return; }
      const expectedStudy = study, generation = refreshGeneration;
      status("Checking bookmark import…");
      try {
        const response = await fetch("/api/research/bookmarks/import-preview", {method: "POST",
          headers: {"content-type": "application/octet-stream"}, body: await file.arrayBuffer()});
        const data = await response.json();
        if (expectedStudy !== study || generation !== refreshGeneration || data.study && data.study !== study) { status("The study changed. Choose the file again."); return; }
        if (!data.ok) { status(data.error); return; }
        importPreview = data;
        el("research-import-summary").textContent = `${data.count} bookmarks · ${data.duplicates} matching IDs · ${data.compatible} compatible views. Other notes and screenshots can be imported without restoring their views.`;
        const list = el("research-import-list"); list.replaceChildren();
        data.bookmarks.forEach((row) => {
          const item = document.createElement("p");
          item.textContent = `${row.title}${row.duplicate ? " (matching ID)" : ""}${row.screenshot ? " · screenshot" : ""}: ${row.reason}`;
          list.append(item);
        });
        el("research-import-duplicates").value = "copy";
        el("research-import-preview").hidden = false; status("Review the import before adding bookmarks.");
      } catch (_) { status("Could not preview the import. No bookmarks were changed."); }
    });
    el("research-import-apply").addEventListener("click", async () => {
      if (!importPreview) return;
      const expectedStudy = study, generation = refreshGeneration;
      el("research-import-apply").disabled = true;
      try {
        const response = await fetch("/api/research/bookmarks/import", {method: "POST", headers: {"content-type": "application/json"},
          body: JSON.stringify({study: importPreview.study, token: importPreview.token, duplicates: el("research-import-duplicates").value})});
        const data = await response.json();
        if (expectedStudy !== study || generation !== refreshGeneration) { status("The study changed. Reload bookmarks to check the import."); return; }
        if (!data.ok) { status(data.error); return; }
        importPreview = null; el("research-import-preview").hidden = true;
        el("research-import-file").value = ""; rows = data.bookmarks; renderTags(); render();
        status(`${data.imported} bookmarks imported.`);
      } catch (_) { status("Could not import bookmarks. Reload the list to check its current state."); }
      finally { el("research-import-apply").disabled = false; }
    });
    el("research-import-cancel").addEventListener("click", () => {
      if (importPreview) fetch("/api/research/bookmarks/import", {method: "POST", headers: {"content-type": "application/json"},
        body: JSON.stringify({study: importPreview.study, token: importPreview.token, cancel: true})}).catch(() => {});
      importPreview = null; el("research-import-preview").hidden = true; el("research-import-file").value = "";
      status("Import cancelled. No bookmarks were changed.");
    });
    el("research-search").addEventListener("input", () => { shownCount = 20; render(); });
    el("research-tag-filter").addEventListener("change", () => { shownCount = 20; render(); });
    el("research-more").addEventListener("click", () => { shownCount += 20; render(); });
    renderTags();
    board()?.on("research-selection", () => {
      el("research-explain-residue").disabled = !molecule()?.STATE.researchSelection;
      updateComparison();
      warning = null; auditEvent = null; updateContext();
    });
    board()?.on("research-frame", updateContext);
    board()?.on("research-analysis", (event) => { analysis = event.analysis; figure = null; updateContext(); });
    updateContext();
  });
  function updateComparison() {
    const selected = molecule()?.STATE.researchSelection;
    el("research-pin-residue").disabled = !selected;
    el("research-compare-residues").disabled = !selected || !comparisonSelection ||
      ["chain", "resseq", "resname", "icode"].every(key => (selected[key] || "") === (comparisonSelection[key] || ""));
    el("research-clear-comparison").hidden = !comparisonSelection;
    el("research-comparison-status").textContent = comparisonSelection ?
      "Pinned: " + [comparisonSelection.resname, comparisonSelection.chain, comparisonSelection.resseq].join(" ") + ". Select another residue to compare." : "";
  }
  function ask(text) {
    if (!enabled) { el("research-status").textContent = "Enable the Agent sidebar in Settings to ask about this selection."; return; }
    origin = board()?.state.activePage === "agent" ? origin : board()?.state.activePage;
    setDock(true); updateContext();
    el("agent-request").value = text;
    el("agent-request").focus();
  }
  window.FastMDXResearch = {capture: capture, restore: restore, describe: describe, ask: ask, enabled: () => enabled,
    messageContext: messageContext, showContext: showContext,
    selectAudit: (id, source, selection) => { auditEvent = id; auditSource = source || null; auditSelection = selection || null; warning = null; updateContext(); }};
})();
