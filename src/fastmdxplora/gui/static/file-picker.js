/* Choosing a file or a folder, from any field that needs one.
 *
 * A browser will not offer a dialog for a path the server has to open: the one
 * it has uploads files to a page, which is a different thing. So the walking
 * is done by the server and drawn here, and every field that wants a path gets
 * the same control rather than one page having a picker and the rest asking
 * people to type.
 *
 * The text box stays. Typing a path is faster when you know it, and pasting
 * one from a terminal is how most people will arrive.
 */

(function () {
  "use strict";

  const state = { target: null, kind: null, mode: "folder", at: null };

  function studyLabel(entry) {
    const short = (axis) => String(axis).split(".").pop();
    if (entry.runs) {
      const swept = (entry.swept || []).map(short);
      return swept.length
        ? `sweep over ${swept.join(", ")}, ${entry.runs} runs`
        : `study of ${entry.runs} runs`;
    }
    if (entry.run_of) {
      const values = Object.entries(entry.values || {})
        .map(([axis, value]) => `${short(axis)} = ${value}`).join(", ");
      return values ? `run of ${entry.run_of}, ${values}` : `run of ${entry.run_of}`;
    }
    return entry.continues ? "continues " + entry.continues : "study";
  }

  function el(id) { return document.getElementById(id); }
  function text(node, value) { if (node) node.textContent = value; }

  /* Built once, on demand, rather than written into every page that wants
   * one -- the markup was duplicated the moment a second page needed it. */
  function panel() {
    let host = el("fastmdx-picker");
    if (host) return host;

    host = document.createElement("div");
    host.id = "fastmdx-picker";
    host.className = "analyse-picker";
    host.hidden = true;
    host.innerHTML = `
      <div class="analyse-picker-panel" role="dialog" aria-modal="true"
           aria-label="Choose a file or folder">
        <div class="analyse-picker-head">
          <div>
            <div class="builder-label" id="fastmdx-picker-what">Folder</div>
            <div class="mono small" id="fastmdx-picker-path"></div>
            <div class="builder-card-note" id="fastmdx-picker-holds"></div>
          </div>
          <button type="button" class="ghost-btn" id="fastmdx-picker-cancel">Cancel</button>
        </div>
        <div class="analyse-picker-list" id="fastmdx-picker-list"></div>
        <div class="analyse-picker-foot">
          <button type="button" class="primary-btn" id="fastmdx-picker-choose">
            Use this folder
          </button>
        </div>
      </div>`;
    document.body.appendChild(host);

    el("fastmdx-picker-cancel").addEventListener("click", close);
    el("fastmdx-picker-choose").addEventListener("click", () => {
      if (state.at) select(state.at);
    });
    host.addEventListener("click", (event) => {
      // Clicking the darkened surround is the other way people close these.
      if (event.target === host) close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !host.hidden) close();
    });
    return host;
  }

  function close() {
    const host = el("fastmdx-picker");
    if (host) host.hidden = true;
    state.target = null;
  }

  function select(path) {
    const input = el(state.target);
    if (input) {
      input.value = path;
      // Pages watch their inputs to decide what to enable, and a value set
      // from code fires neither of these on its own.
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    close();
  }

  async function show(path) {
    const query = new URLSearchParams();
    if (path) query.set("path", path);
    if (state.kind) query.set("kind", state.kind);

    let listing;
    try {
      const response = await fetch("/api/browse?" + query.toString());
      listing = await response.json();
    } catch (error) {
      text(el("fastmdx-picker-list"), "Could not read that folder.");
      return;
    }

    const list = el("fastmdx-picker-list");
    if (!listing.ok) {
      list.innerHTML = `<div class="empty-detail muted">${listing.error}</div>`;
      return;
    }

    state.at = listing.path;
    text(el("fastmdx-picker-path"), listing.path);
    text(
      el("fastmdx-picker-holds"),
      listing.trajectories || listing.structures
        ? `${listing.trajectories} trajectory, ${listing.structures} structure file(s) here`
        : "nothing to analyse directly in this folder"
    );

    list.innerHTML = "";
    if (listing.parent) list.appendChild(row("\u2191 up one level", listing.parent, null, true));
    (listing.entries || []).forEach((entry) => {
      /* A study first: the folder a run wrote is what a person opening
       * the picker from the Agent is looking for most of the time, and
       * it is told apart from a folder of structures or trajectories by
       * colour as well as by word. */
      /* A study of several runs, and a run inside one, are studies too,
       * qualified: "sweep over temperature_K, 4 runs", "run of my_sweep,
       * temperature_K = 310". One kind of folder wherever a study can go. */
      const kind = entry.study ? "study"
        : entry.trajectories ? "trajectory"
        : entry.structures ? "structure" : null;
      const holds = entry.study ? studyLabel(entry)
        : entry.trajectories ? `${entry.trajectories} trajectory`
        : entry.structures ? `${entry.structures} structure` : null;
      list.appendChild(row(entry.name, entry.path, holds, true, kind));
    });
    (listing.files || []).forEach((file) => {
      const ext = (file.name.split(".").pop() || "").toLowerCase();
      const kind = ["dcd", "xtc", "trr", "nc", "netcdf", "h5", "hdf5"].includes(ext) ? "trajectory"
        : ["pdb", "cif", "gro", "psf", "prmtop", "top"].includes(ext) ? "structure" : null;
      list.appendChild(row(file.name, file.path, `${file.size_mb} MB`, false, kind));
    });

    if (!list.children.length) {
      list.innerHTML = '<div class="empty-detail muted">Nothing here.</div>';
    }
  }

  function row(label, path, badge, isFolder, kind) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "analyse-folder-row fastmdx-picker-row" + (kind === "study" ? " is-study" : "");
    button.dataset.folder = String(Boolean(isFolder));
    if (kind) button.dataset.kind = kind;

    const name = document.createElement("span");
    name.className = "analyse-folder-name name";
    name.textContent = label;
    button.appendChild(name);

    if (badge) {
      const mark = document.createElement("span");
      mark.className = "analyse-folder-holds" + (kind ? " badge-" + kind : "");
      mark.textContent = badge;
      button.appendChild(mark);
    }
    button.addEventListener("click", () => {
      if (isFolder) show(path);
      else select(path);
    });
    return button;
  }

  /* into: the id of the input to fill.
   * kind: trajectory | structure | config, or nothing for a folder.
   * mode: "file" or "folder". */
  function open(options) {
    const host = panel();
    state.target = options.into;
    state.kind = options.kind || null;
    state.mode = options.mode || (options.kind ? "file" : "folder");

    text(el("fastmdx-picker-what"),
         state.mode === "file" ? `Choose a ${options.kind || "file"}` : "Choose a folder");
    // Choosing the folder you are in only makes sense when a folder is what
    // was asked for; picking a file is done by clicking it.
    el("fastmdx-picker-choose").hidden = state.mode !== "folder";
    host.hidden = false;

    const current = el(options.into);
    // Where to start: what the field already holds, else the caller's
    // suggestion -- the Agent opens a study's own folder for a thread
    // about that study -- else the workspace, which the picker learns
    // from the app state, so Load available study and every builder
    // field start among the studies rather than at home.
    show((current && current.value.trim()) || options.start || state.workspace || "");
  }

  /* Any input marked with data-picks gets a button, so a new field needs no
   * wiring: `data-picks="trajectory"` is the whole of it. */
  function attachAll() {
    document.querySelectorAll("input[data-picks]").forEach((input) => {
      if (input.dataset.picksWired === "true") return;
      input.dataset.picksWired = "true";

      const kind = input.getAttribute("data-picks");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost-btn picker-button";
      button.textContent = "Browse\u2026";
      button.addEventListener("click", () =>
        open({ into: input.id, kind: kind === "folder" ? null : kind })
      );

      // Some fields already sit in a row with a button of their own; the
      // picker joins that row rather than wrapping it in a second one.
      const existing = input.parentNode;
      if (existing && existing.classList.contains("analyse-path-row")) {
        existing.appendChild(button);
      } else {
        const row = document.createElement("div");
        row.className = "analyse-path-row";
        existing.insertBefore(row, input);
        row.appendChild(input);
        row.appendChild(button);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attachAll);
  } else {
    attachAll();
  }

  if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
    window.FastMDXDashboard.on("app-state", (s) => {
      if (s && s.exploration_root) state.workspace = s.exploration_root;
    });
  }

  window.FastMDXPicker = { open, close, attachAll };
})();
