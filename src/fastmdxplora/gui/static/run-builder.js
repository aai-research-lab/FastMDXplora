/* Building a run.
 *
 * There was a page for starting from a protein and a page for starting from a
 * trajectory, and they were the same thing: both write a config and run it.
 * They differed only in which phases the config named -- and having been built
 * separately, one of them offered eleven of the eighty-three settings that
 * exist while the other offered all of them.
 *
 * So there is one page, and it asks three questions in the order somebody
 * actually answers them: what have you got, what should happen to it, and is
 * there anything you want to change. The first answer sets the second, the
 * second decides which settings are worth showing, and nothing is drawn from a
 * list kept here -- it all comes from the schema, which is read from the
 * software itself.
 */

(function () {
  "use strict";

  const PHASES = [
    {
      name: "setup",
      label: "Setup",
      blurb: "Protonate, solvate, add ions, and assign a force field.",
    },
    {
      name: "simulation",
      label: "Simulate",
      blurb: "Minimise, equilibrate, and run production dynamics.",
    },
    {
      name: "analysis",
      label: "Analyze",
      blurb: "Analyse the trajectory: structure, flexibility, interactions.",
    },
    {
      name: "report",
      label: "Report",
      blurb: "Collect the figures and numbers into a document.",
    },
  ];

  /* What each starting point implies. Somebody with a protein wants the whole
   * thing; somebody with a trajectory has already done the expensive part. */
  const STARTING_POINTS = {
    structure: {
      label: "A structure",
      detail:
        "A four-character PDB identifier, or a path to a PDB or CIF file. " +
        "Everything is built from it.",
      phases: ["setup", "simulation", "analysis", "report"],
      // Sequence-to-structure is not implemented -- the setup phase refuses
      // it -- so it is not offered here.
      offers: ["setup", "simulation", "analysis", "report"],
    },
    trajectory: {
      label: "A trajectory",
      detail:
        "A simulation you already have, from here or anywhere else. It is " +
        "analysed as it stands.",
      phases: ["analysis", "report"],
      // Setup and simulation have nothing to act on: there is no supported
      // way to continue a run from a trajectory, and re-preparing the
      // structure would not connect to the frames already recorded.
      offers: ["analysis", "report"],
    },
    config: {
      label: "A config I already have",
      detail:
        "One written earlier, edited by hand, or brought back from a " +
        "cluster. It is checked before anything runs.",
      phases: [],
      offers: [],
    },
  };

  const state = {
    schema: null,
    start: null,
    phases: new Set(),
    values: {},            // phase -> { field: value }
    analyses: new Set(),
    analysisOptions: {},   // analysis -> { option: value }
    open: new Set(),
    found: null,
    configVerdict: null,
    loadedFrom: null,
    sweep: [],             // [{ axis, values }] -- values as typed
    refused: null,         // a refusal said on its fields: { targets, why, fix, ... }
  };

  const el = (id) => document.getElementById(id);
  const text = (node, value) => { if (node) node.textContent = value; };

  // ------------------------------------------------------------------ schema

  async function loadSchema() {
    if (state.schema) return state.schema;
    const response = await fetch("/api/schema");
    state.schema = await response.json();
    return state.schema;
  }

  function fieldsFor(phase) {
    const block = state.schema.phases[phase];
    return block ? block.fields : [];
  }

  /* Settings a run supplies for itself. Asking somebody to type the trajectory
   * path in the settings when they chose it two questions ago is the kind of
   * thing that makes a form feel like paperwork. */
  const SUPPLIED = new Set(["trajectory", "topology"]);

  /* Settings another control on this page already owns. The Analyses
   * panel is the analysis picker: sixteen analyses grouped by what they
   * answer, each with what it computes and why. Offering `include` and
   * `exclude` again in the options grid put two controls on one key -- and
   * the grid won, because the config it builds is merged over the panel's.
   * Somebody could choose four analyses, then set `include` to something
   * else without either control saying the other existed. */
  const OWNED_ELSEWHERE = {analysis: new Set(["include", "exclude"])};

  function ownedElsewhere(phase, name) {
    return Boolean(OWNED_ELSEWHERE[phase] && OWNED_ELSEWHERE[phase].has(name));
  }

  function settingsFor(phase) {
    return fieldsFor(phase).filter(
      (field) => !SUPPLIED.has(field.name) && !ownedElsewhere(phase, field.name)
    );
  }

  /* The same settings, in the groups the schema declares. Thirty-seven
   * controls in one grid is a list to read rather than a form to fill in:
   * a pH sat beside a dispersion correction, and finding the one you wanted
   * meant going through all of them. Empty groups are dropped, so choosing a
   * trajectory does not leave headings with nothing under them. */
  function groupsFor(phase) {
    const block = state.schema.phases[phase];
    if (!block || !block.groups) {
      return [{ title: null, why: null, fields: settingsFor(phase) }];
    }
    return block.groups
      .map((group) => ({
        title: group.title,
        why: group.why,
        fields: group.fields.filter(
          (field) => !SUPPLIED.has(field.name) && !ownedElsewhere(phase, field.name)
        ),
      }))
      .filter((group) => group.fields.length);
  }

  // ------------------------------------------------------- what have you got

  function renderStart() {
    const select = el("run-start");
    if (!select) return;
    if (!select.options.length) {
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Choose…";
      select.appendChild(blank);
      Object.keys(STARTING_POINTS).forEach((key) => {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = STARTING_POINTS[key].label;
        select.appendChild(option);
      });
      select.addEventListener("change", () => {
        state.start = select.value || null;
        const choice = STARTING_POINTS[state.start] || { phases: [] };
        state.phases = new Set(choice.phases);
        renderAll();
      });
    }
    select.value = state.start || "";

    // The description belongs with the control, not on a card of its own:
    // it is what the option means, and it changes as the option does.
    text(
      el("run-start-detail"),
      state.start ? STARTING_POINTS[state.start].detail : ""
    );

    const started = Boolean(state.start);
    // Once a config has been opened into the form, the form is what is being
    // edited -- the starting point is whatever the config described.
    const fromConfig = state.start === "config";
    el("run-input-card").hidden = !started;
    el("run-phases-card").hidden = !started || fromConfig;
    el("run-settings-card").hidden = !started || fromConfig;
    el("run-actions-card").hidden = !started;

    el("run-structure-field").hidden = state.start !== "structure";
    el("run-trajectory-field").hidden = state.start !== "trajectory";
    el("run-config-field").hidden = !fromConfig;
    el("run-output-field").hidden = fromConfig;
  }

  // ------------------------------------------------ what should happen to it

  function renderPhases() {
    const host = el("run-phases");
    if (!host) return;
    host.innerHTML = "";
    PHASES.forEach((phase) => {
      const row = document.createElement("label");
      row.className = "run-phase";
      row.dataset.chosen = String(state.phases.has(phase.name));

      const offered = STARTING_POINTS[state.start]
        ? STARTING_POINTS[state.start].offers
        : [];
      const available = offered.includes(phase.name);
      row.dataset.available = String(available);

      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = available && state.phases.has(phase.name);
      box.disabled = !available;
      box.addEventListener("change", () => {
        if (box.checked) state.phases.add(phase.name);
        else state.phases.delete(phase.name);
        renderAll();
      });

      const naming = document.createElement("span");
      naming.className = "run-phase-naming";
      const label = document.createElement("span");
      label.className = "run-phase-label";
      label.textContent = phase.label;
      const blurb = document.createElement("span");
      blurb.className = "run-phase-blurb";
      blurb.textContent = available
        ? phase.blurb
        : "Nothing to act on: a trajectory is already the result of this.";
      naming.appendChild(label);
      naming.appendChild(blurb);

      row.appendChild(box);
      row.appendChild(naming);
      host.appendChild(row);
    });
  }

  // ------------------------------------------- anything you want to change

  function renderSettings() {
    const host = el("run-settings");
    if (!host) return;
    host.innerHTML = "";

    const chosen = PHASES.filter((phase) => state.phases.has(phase.name));
    if (!chosen.length) {
      host.innerHTML =
        '<div class="empty-detail muted">Choose at least one thing to do.</div>';
      return;
    }

    host.appendChild(runOptionsSection());
    host.appendChild(sweepSection());
    host.appendChild(executionSection());

    chosen.forEach((phase) => {
      host.appendChild(settingsSection(phase));
      if (phase.name === "analysis") host.appendChild(analysisSection());
    });

    // Settings are drawn after the page loads, so the picker has to be told
    // there are new path fields to attach to.
    if (window.FastMDXPicker && window.FastMDXPicker.attachAll) {
      window.FastMDXPicker.attachAll();
    }
  }

  /* Settings that belong to the run rather than to a phase. They reached the
     command line and the config file and not this form, so the browser was
     the one interface that could not turn them on or off. Stored under a
     sentinel key so the same control builder draws them. */
  const RUN_OPTIONS_KEY = "__run__";

  function runOptionsSection() {
    const section = document.createElement("div");
    section.className = "run-section";

    const options = (state.schema && state.schema.run_options) || [];
    if (!options.length) return section;

    // The same markup a phase section uses, because the stylesheet lays out
    // a head and a body grid and nothing else. A bare heading with controls
    // appended ran the labels and their help text together.
    const changed = Object.keys(state.values[RUN_OPTIONS_KEY] || {}).length;

    const head = document.createElement("button");
    head.type = "button";
    head.className = "run-section-head";
    head.setAttribute("aria-expanded", String(state.open.has(RUN_OPTIONS_KEY)));
    head.innerHTML =
      '<span class="run-section-name">This run</span>' +
      `<span class="run-section-count">${
        changed ? `${changed} changed` : `${options.length} settings`
      }</span>`;
    head.addEventListener("click", () => {
      if (state.open.has(RUN_OPTIONS_KEY)) state.open.delete(RUN_OPTIONS_KEY);
      else state.open.add(RUN_OPTIONS_KEY);
      renderSettings();
    });
    section.appendChild(head);

    if (state.open.has(RUN_OPTIONS_KEY)) {
      const grid = document.createElement("div");
      grid.className = "run-section-body";
      options.forEach((field) => grid.appendChild(control(RUN_OPTIONS_KEY, field)));
      section.appendChild(grid);
    }
    return section;
  }

  /* How the runs are scheduled. Under its own sentinel key for the same
     reason "This run" has one: the control builder takes a phase name, and
     `execution` is a top-level block rather than a phase -- it is not in the
     plan and cannot be included or excluded. It reached the config file and
     neither the flags nor the form, so a study wanting two GPUs had to be
     written by hand. */
  const EXECUTION_KEY = "__execution__";

  function executionSection() {
    const section = document.createElement("div");
    section.className = "run-section";

    const options = (state.schema && state.schema.execution_options) || [];
    if (!options.length) return section;

    const changed = Object.keys(state.values[EXECUTION_KEY] || {}).length;

    const head = document.createElement("button");
    head.type = "button";
    head.className = "run-section-head";
    head.setAttribute("aria-expanded", String(state.open.has(EXECUTION_KEY)));
    head.innerHTML =
      '<span class="run-section-name">How the runs are scheduled</span>' +
      `<span class="run-section-count">${
        changed ? `${changed} changed` : `${options.length} settings`
      }</span>`;
    head.addEventListener("click", () => {
      if (state.open.has(EXECUTION_KEY)) state.open.delete(EXECUTION_KEY);
      else state.open.add(EXECUTION_KEY);
      renderSettings();
    });
    section.appendChild(head);

    if (state.open.has(EXECUTION_KEY)) {
      const grid = document.createElement("div");
      grid.className = "run-section-body";
      options.forEach((field) => grid.appendChild(control(EXECUTION_KEY, field)));
      section.appendChild(grid);
    }
    return section;
  }

  /* Run the study once for every value of a setting. A sweep reached the
     config file and the command line and not this form, so the browser was
     the one interface that could not say it. Each row is a setting and the
     values typed for it, read on the server by the same rule --sweep uses. */
  const SWEEP_KEY = "__sweep__";

  function sweepRuns() {
    return state.sweep.reduce((runs, row) => {
      if (!row.axis || !row.values.trim()) return runs;
      // Commas outside quotes separate values; one inside a quoted value,
      // in the bracketed form, does not. The server reads the values; this
      // only counts them.
      const text = row.values.trim().replace(/^\[|\]$/g, "");
      let count = 0, quoted = null, seen = "";
      for (const ch of text) {
        if (quoted) { if (ch === quoted) quoted = null; seen += ch; continue; }
        if (ch === '"' || ch === "'") { quoted = ch; seen += ch; continue; }
        if (ch === ",") { if (seen.trim()) count += 1; seen = ""; continue; }
        seen += ch;
      }
      if (seen.trim()) count += 1;
      return runs * Math.max(1, count);
    }, 1);
  }

  function sweepSection() {
    const section = document.createElement("div");
    section.className = "run-section";
    section.id = "run-sweep";
    const axes = (state.schema && state.schema.sweep_axes) || [];
    if (!axes.length) return section;

    const live = state.sweep.filter((row) => row.axis && row.values.trim()).length;
    const runs = sweepRuns();
    const head = document.createElement("button");
    head.type = "button";
    head.className = "run-section-head";
    head.setAttribute("aria-expanded", String(state.open.has(SWEEP_KEY)));
    head.innerHTML =
      '<span class="run-section-name">Sweep a setting</span>' +
      `<span class="run-section-count">${
        live ? `${live} setting${live === 1 ? "" : "s"}, ${runs} runs` : "one run"
      }</span>`;
    head.addEventListener("click", () => {
      if (state.open.has(SWEEP_KEY)) state.open.delete(SWEEP_KEY);
      else state.open.add(SWEEP_KEY);
      renderSettings();
    });
    section.appendChild(head);
    if (!state.open.has(SWEEP_KEY)) return section;

    const body = document.createElement("div");
    body.className = "run-section-body run-sweep-body";
    const note = document.createElement("p");
    note.className = "builder-card-note run-section-note";
    note.textContent =
      "The study runs once for every combination. Separate values with " +
      "commas; where a value holds a comma, write the list in brackets.";
    body.appendChild(note);
    state.sweep.forEach((row, index) => body.appendChild(sweepRow(row, index, axes)));
    const add = document.createElement("button");
    add.type = "button";
    add.className = "run-sweep-add";
    add.textContent = "Add a setting";
    add.addEventListener("click", () => {
      state.sweep.push({ axis: "", values: "" });
      renderSettings();
    });
    body.appendChild(add);
    section.appendChild(body);
    return section;
  }

  function sweepRow(row, index, axes) {
    const line = document.createElement("div");
    line.className = "run-sweep-row";
    const select = document.createElement("select");
    select.className = "run-sweep-axis";
    select.setAttribute("aria-label", "Setting to sweep");
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Choose a setting";
    select.appendChild(blank);
    axes.forEach((axis) => {
      const option = document.createElement("option");
      option.value = axis;
      option.textContent = axis;
      select.appendChild(option);
    });
    select.value = row.axis || "";
    select.addEventListener("change", () => {
      row.axis = select.value;
      updateSummary();
      renderSettings();
    });
    const values = document.createElement("input");
    values.type = "text";
    values.className = "run-sweep-values";
    values.placeholder = "300, 310, 320";
    values.setAttribute("aria-label", "Values to sweep");
    values.value = row.values || "";
    values.addEventListener("input", () => {
      row.values = values.value;
      updateSummary();
    });
    values.addEventListener("change", renderSettings);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "run-sweep-remove";
    remove.setAttribute("aria-label", "Remove this setting");
    remove.textContent = "\u00d7";
    remove.addEventListener("click", () => {
      state.sweep.splice(index, 1);
      renderSettings();
    });
    line.appendChild(select);
    line.appendChild(values);
    line.appendChild(remove);
    return line;
  }

  function settingsSection(phase) {
    const section = document.createElement("div");
    section.className = "run-section";

    const settings = settingsFor(phase.name);
    const changed = Object.keys(state.values[phase.name] || {}).length;

    const head = document.createElement("button");
    head.type = "button";
    head.className = "run-section-head";
    head.setAttribute("aria-expanded", String(state.open.has(phase.name)));
    head.innerHTML =
      `<span class="run-section-name">${phase.label}</span>` +
      `<span class="run-section-count">${
        changed ? `${changed} changed` : `${settings.length} settings`
      }</span>`;
    head.addEventListener("click", () => {
      if (state.open.has(phase.name)) state.open.delete(phase.name);
      else state.open.add(phase.name);
      renderSettings();
    });
    section.appendChild(head);

    if (state.open.has(phase.name)) {
      groupsFor(phase.name).forEach((group) => {
        if (group.title) {
          const head = document.createElement("div");
          head.className = "run-group-head";
          head.innerHTML =
            `<span class="run-group-name">${group.title}</span>` +
            (group.why ? `<span class="run-group-why">${group.why}</span>` : "");
          section.appendChild(head);
        }
        const grid = document.createElement("div");
        grid.className = "run-section-body";
        group.fields.forEach((field) =>
          grid.appendChild(control(phase.name, field))
        );
        section.appendChild(grid);
      });
    }
    return section;
  }

  function control(phase, field) {
    // A form of several controls is not one labelled control: inside a
    // <label>, a click on its name pressed the first button in it.
    const wide = field.control === "stopping" || field.control === "residues";
    const wrap = document.createElement(wide ? "div" : "label");
    wrap.className = "builder-field" + (wide ? " builder-field-wide" : "");
    // Named, so what is said about a setting elsewhere on the page can
    // open the section it is in and point at it.
    wrap.dataset.setting = field.name;
    const refusedHere = isRefused(phase, field.name);
    if (refusedHere) wrap.classList.add("is-refused");

    const label = document.createElement("span");
    label.className = "builder-label";
    label.textContent = field.name.replace(/_/g, " ");
    wrap.appendChild(label);

    let input;
    if (field.choices && field.choices.length) {
      input = document.createElement("select");
      field.choices.forEach((choice) => {
        const option = document.createElement("option");
        option.value = choice;
        option.textContent = choice;
        if (choice === field.default) option.selected = true;
        input.appendChild(option);
      });
    } else if (field.control === "multiselect") {
      // Checkboxes rather than a `<select multiple>`: every option visible at
      // once, and no modifier key needed to pick a second one. The value is a
      // list, which is what the config file wants.
      input = document.createElement("div");
      input.className = "builder-multiselect";
      (field.choices || []).forEach((choice) => {
        const label = document.createElement("label");
        label.className = "chip-toggle";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = choice;
        label.appendChild(box);
        label.appendChild(document.createTextNode(choice));
        input.appendChild(label);
      });
      input.readValue = () =>
        Array.from(input.querySelectorAll("input:checked")).map((box) => box.value);
      input.writeValue = (value) => {
        const chosen = new Set(Array.isArray(value) ? value : []);
        input.querySelectorAll("input").forEach((box) => {
          box.checked = chosen.has(box.value);
        });
      };
    } else if (field.control === "tristate") {
      // Unset, on or off. Unset sends nothing, so the run decides as it
      // would had the setting never been shown.
      input = document.createElement("select");
      [["", "Default (see note)"], ["true", "On"], ["false", "Off"]].forEach(
        ([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          input.appendChild(option);
        });
      input.readValue = () =>
        input.value === "" ? "" : input.value === "true";
      input.writeValue = (value) => {
        input.value = value === true ? "true" : value === false ? "false" : "";
      };
    } else if (field.control === "checkbox") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(field.default);
    } else if (field.control === "script") {
      // One script with an on-switch. The engine reads the config's one
      // `script` string either way -- a single line naming a file that
      // exists is read from disk, anything else is the script itself -- so
      // the page offers both: a path, found with the same Browse control
      // the trajectory and structure fields use, or the text written here.
      input = document.createElement("div");
      input.className = "builder-script";

      const onRow = document.createElement("label");
      onRow.className = "chip-toggle";
      const on = document.createElement("input");
      on.type = "checkbox";
      onRow.appendChild(on);
      onRow.appendChild(document.createTextNode("Enabled"));
      input.appendChild(onRow);

      // The picker attaches itself to anything marked data-picks once
      // renderSettings() calls attachAll(), so the Browse button needs no
      // wiring here -- only a name for the picker to fill.
      const path = document.createElement("input");
      path.type = "text";
      // Derived, not written: a setting named in this file is a list to
      // keep in step by hand, and the picker's kind for a script field is
      // the field's own name -- the KINDS table on the server is keyed to
      // agree.
      path.id = "builder-" + field.name + "-path";
      path.dataset.picks = field.name;
      path.placeholder = "path to a PLUMED .dat file on this machine";
      input.appendChild(path);

      const script = document.createElement("textarea");
      script.rows = 8;
      script.spellcheck = false;
      script.className = "builder-mapping";
      script.placeholder =
        "or the PLUMED input written here,\n" +
        "exactly as it would appear in the .dat file";
      input.appendChild(script);

      const note = document.createElement("span");
      note.className = "builder-card-note";
      input.appendChild(note);

      const tell = () => {
        // Both slots feed the one `script` key, so the rule is stated
        // where it applies rather than discovered from the config: text
        // written here wins, because somebody who loaded a file and then
        // edited the text meant the edits.
        note.textContent =
          script.value.trim() && path.value.trim()
            ? "The script written here is what the config carries; " +
              "clear it to use the file path instead."
            : "";
      };

      let hadContent = false;
      const settle = () => {
        const has = Boolean(script.value.trim() || path.value.trim());
        // The first content to arrive turns the switch on -- a script
        // somebody just chose was chosen to run -- but only on the
        // empty-to-filled step, so a box deliberately unticked over a
        // staged script stays unticked through further edits.
        if (has && !hadContent && !on.checked) on.checked = true;
        hadContent = has;
        tell();
      };
      path.addEventListener("change", settle);
      script.addEventListener("change", settle);

      input.readValue = () => {
        const text = script.value;
        const file = path.value.trim();
        // Content is the decision; the switch only modifies it. A ticked
        // box over two empty slots writes nothing, because an on-switch
        // with no script attached is not a study anybody described.
        if (!text.trim() && !file) return null;
        return { enabled: on.checked, script: text.trim() ? text : file };
      };
      input.writeValue = (value) => {
        if (!value || typeof value !== "object") return;
        on.checked = Boolean(value.enabled);
        const carried = typeof value.script === "string" ? value.script : "";
        // Restored with the same reading the engine gives the config: a
        // newline means the script itself, one line means a path. A
        // one-line inline script lands in the path box, where it still
        // round-trips into the same `script` string.
        if (carried.includes("\n")) {
          script.value = carried;
          path.value = "";
        } else {
          path.value = carried;
          script.value = "";
        }
        hadContent = Boolean(carried.trim());
        tell();
      };
    } else if (field.control === "stopping") {
      input = stoppingControl(field);
    } else if (field.control === "residues") {
      input = residueStatesControl(field);
    } else if (field.control === "mapping") {
      // Umbrella, steered and metadynamics are blocks of several settings,
      // not one value. A single-line box could not hold one, and what was
      // typed into it arrived as a string that no phase could read -- so the
      // browser was the one interface where enhanced sampling could not be
      // set up at all. Written here the way it is written in a config file.
      input = document.createElement("textarea");
      input.rows = 6;
      input.spellcheck = false;
      input.className = "builder-mapping";
      input.placeholder = field.example
        ? Object.keys(field.example)
            .map((key) => key + ": " + field.example[key])
            .join("\n")
        : "one setting per line, as in a config file";
    } else {
      input = document.createElement("input");
      input.type = field.control === "number" ? "number" : "text";
      if (field.control === "number") input.step = "any";
      // Shown rather than filled in, so the config records what was decided.
      input.placeholder =
        field.default === null || field.default === undefined
          ? (field.example === null ? "" : String(field.example ?? ""))
          : String(field.default);
    }
    const current = (state.values[phase] || {})[field.name];
    if (current !== undefined) {
      if (input.writeValue) input.writeValue(current);
      else if (input.type === "checkbox") input.checked = Boolean(current);
      else input.value = current;
    }

    input.addEventListener("change", () => {
      const value = input.readValue
        ? input.readValue()
        : input.type === "checkbox" ? input.checked : input.value;
      // Changed, so what was refused is no longer what is there.
      if (isRefused(phase, field.name)) state.refused = null;
      state.values[phase] = state.values[phase] || {};
      const empty = value === "" || value === null
        || (Array.isArray(value) && value.length === 0);
      if (empty) delete state.values[phase][field.name];
      else state.values[phase][field.name] = value;
      renderSettings();
      updateSummary();
    });
    input.dataset.researchField = phase + "." + field.name;
    wrap.appendChild(input);
    if (refusedHere) wrap.appendChild(refusalNote(phase, field));

    if (field.help) {
      const help = document.createElement("span");
      help.className = "builder-card-note";
      help.textContent = field.help;
      wrap.appendChild(help);
    }
    return wrap;
  }

  // -------------------------------------------- running until it is determined

  /* `simulation.stop_when` as a form: the measures, each with the error it
   * must reach, a ceiling, and whether replicas must agree. Drawn again
   * after every change, as every control is, so rows being written (an
   * analysis chosen, its error not yet) are kept here until they are whole
   * and only whole ones reach the config. */
  const SEED_AXIS = "simulation.random_seed";
  let stoppingDraft = null;

  function seedSwept() {
    return state.sweep.some((row) => row.axis === SEED_AXIS && sweepRunsOf(row) >= 2);
  }

  function sweepRunsOf(row) {
    const kept = state.sweep;
    state.sweep = [row];
    const runs = sweepRuns();
    state.sweep = kept;
    return runs;
  }

  function stoppingControl(field) {
    const measures = field.measures || [];
    const box = document.createElement("div");
    box.className = "builder-stopping";

    const draftFrom = (value) => {
      const rule = value && typeof value === "object" ? value : {};
      return {
        rows: (Array.isArray(rule.measures) ? rule.measures : []).map((m) => ({
          analysis: m.analysis || "",
          kind: m.relative_error !== undefined && m.relative_error !== null ? "relative" : "absolute",
          amount: m.relative_error !== undefined && m.relative_error !== null
            ? String(+(m.relative_error * 100).toPrecision(6))
            : (m.standard_error === undefined || m.standard_error === null ? "" : String(m.standard_error)),
        })),
        ceiling: rule.max_duration_ns === undefined || rule.max_duration_ns === null
          ? "" : String(rule.max_duration_ns),
        replicas: rule.independent_starts !== "not_required",
      };
    };
    const current = (state.values.simulation || {}).stop_when;
    const draft = stoppingDraft || draftFrom(current);
    stoppingDraft = draft;

    const complete = (row) => row.analysis && Number(row.amount) > 0;
    const commit = () => {
      stoppingDraft = draft;
      box.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const unitOf = (name) => {
      const found = measures.filter((m) => m.analysis === name)[0];
      return found ? found.unit : "";
    };

    const rows = document.createElement("div");
    rows.className = "builder-stopping-rows";
    draft.rows.forEach((row, index) => {
      const line = document.createElement("div");
      line.className = "builder-stopping-row";
      line.dataset.index = String(index);
      const pick = document.createElement("select");
      pick.className = "builder-stopping-analysis";
      pick.setAttribute("aria-label", "Quantity");
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Choose a quantity";
      pick.appendChild(blank);
      measures.forEach((m) => {
        const option = document.createElement("option");
        option.value = m.analysis;
        option.textContent = m.label;
        pick.appendChild(option);
      });
      pick.value = row.analysis;
      pick.addEventListener("change", (event) => {
        event.stopPropagation();
        row.analysis = pick.value;
        commit();
      });
      const kind = document.createElement("select");
      kind.className = "builder-stopping-kind";
      kind.setAttribute("aria-label", "How the error is given");
      const unit = unitOf(row.analysis);
      [["absolute", "to \u00b1 " + (unit || "its own unit")], ["relative", "to \u00b1 % of the mean"]]
        .forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          kind.appendChild(option);
        });
      kind.value = row.kind;
      kind.addEventListener("change", (event) => {
        event.stopPropagation();
        row.kind = kind.value;
        commit();
      });
      const amount = document.createElement("input");
      amount.type = "number";
      amount.step = "any";
      amount.min = "0";
      amount.className = "builder-stopping-amount";
      amount.setAttribute("aria-label", row.kind === "relative" ? "Percent of the mean" : "Standard error");
      amount.placeholder = row.kind === "relative" ? "5" : "0.01";
      amount.value = row.amount;
      amount.addEventListener("change", (event) => {
        event.stopPropagation();
        row.amount = amount.value.trim();
        commit();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "run-sweep-remove";
      remove.setAttribute("aria-label", "Remove this quantity");
      remove.textContent = "\u00d7";
      remove.addEventListener("click", (event) => {
        event.preventDefault();
        draft.rows.splice(index, 1);
        commit();
      });
      line.appendChild(pick);
      line.appendChild(kind);
      line.appendChild(amount);
      line.appendChild(remove);
      rows.appendChild(line);
    });
    box.appendChild(rows);

    const add = document.createElement("button");
    add.type = "button";
    add.className = "run-sweep-add builder-stopping-add";
    add.textContent = draft.rows.length ? "Add a quantity" : "Run until a quantity is determined";
    add.addEventListener("click", (event) => {
      event.preventDefault();
      draft.rows.push({ analysis: "", kind: "absolute", amount: "" });
      commit();
    });
    box.appendChild(add);

    if (draft.rows.length) {
      const ceilingRow = document.createElement("div");
      ceilingRow.className = "builder-stopping-line";
      const ceilingLabel = document.createElement("span");
      ceilingLabel.textContent = "At most";
      const ceiling = document.createElement("input");
      ceiling.type = "number";
      ceiling.step = "any";
      ceiling.min = "0";
      ceiling.className = "builder-stopping-ceiling";
      ceiling.setAttribute("aria-label", "The most production any run may reach, in ns");
      ceiling.placeholder = "50";
      ceiling.value = draft.ceiling;
      ceiling.addEventListener("change", (event) => {
        event.stopPropagation();
        draft.ceiling = ceiling.value.trim();
        commit();
      });
      const after = document.createElement("span");
      after.textContent = "ns of production per run; duration_ns is then the first piece.";
      ceilingRow.appendChild(ceilingLabel);
      ceilingRow.appendChild(ceiling);
      ceilingRow.appendChild(after);
      box.appendChild(ceilingRow);

      const replicasRow = document.createElement("label");
      replicasRow.className = "chip-toggle builder-stopping-replicas";
      const replicas = document.createElement("input");
      replicas.type = "checkbox";
      replicas.checked = draft.replicas;
      replicas.addEventListener("change", (event) => {
        event.stopPropagation();
        draft.replicas = replicas.checked;
        commit();
      });
      replicasRow.appendChild(replicas);
      replicasRow.appendChild(document.createTextNode(
        "Replicas must agree (recommended: one run can look equilibrated while trapped in one state)"));
      box.appendChild(replicasRow);

      const note = document.createElement("div");
      note.className = "builder-card-note builder-stopping-note";
      if (draft.replicas && !seedSwept()) {
        note.appendChild(document.createTextNode(
          "Replicas are runs swept over the seed, and this study has none. "));
        const sweep = document.createElement("button");
        sweep.type = "button";
        sweep.className = "ghost-btn builder-stopping-seeds";
        sweep.textContent = "Sweep the seed over three values";
        sweep.addEventListener("click", (event) => {
          event.preventDefault();
          state.sweep = state.sweep.filter((row) => row.axis !== SEED_AXIS);
          state.sweep.push({ axis: SEED_AXIS, values: "1, 2, 3" });
          state.open.add(SWEEP_KEY);
          updateSummary();
          renderSettings();
        });
        note.appendChild(sweep);
      } else if (!draft.replicas) {
        note.textContent = "One run's own precision is accepted, and the record says it was " +
          "not checked against independent starts.";
      }
      box.appendChild(note);
    }

    box.readValue = () => {
      const whole = draft.rows.filter(complete);
      if (!whole.length) return null;
      const rule = {
        measures: whole.map((row) => row.kind === "relative"
          ? { analysis: row.analysis, relative_error: Number(row.amount) / 100 }
          : { analysis: row.analysis, standard_error: Number(row.amount) }),
      };
      if (Number(draft.ceiling) > 0) rule.max_duration_ns = Number(draft.ceiling);
      if (!draft.replicas) rule.independent_starts = "not_required";
      return rule;
    };
    // Drawn from the draft, which a loaded config or a reset clears; the
    // value in the form is only the draft's whole rows, so writing it back
    // would drop a row being written.
    box.writeValue = () => {};
    return box;
  }

  // ------------------------------------------ a refusal, on its own field

  /* A config the validator refused was said in one line under the buttons,
   * however far down the form the setting it was about sat. The refusal now
   * carries what would fix it and the setting it is about (fastmdxplora.
   * remedies), so it is said on that setting's field, with the section
   * opened and the field pointed at. Where the schema holds a spelling near
   * what was given, it is offered as a button; nothing is changed until it
   * is pressed. */
  function targetOf(setting) {
    const parts = String(setting || "").split(".");
    if (!parts[0]) return null;
    if (parts.length > 1 && PHASES.some((phase) => phase.name === parts[0])) {
      return { phase: parts[0], name: parts[1] };
    }
    if (parts[0] === "execution" && parts.length > 1) {
      return { phase: EXECUTION_KEY, name: parts[1] };
    }
    const runs = (state.schema && state.schema.run_options) || [];
    if (runs.some((field) => field.name === parts[0])) {
      return { phase: RUN_OPTIONS_KEY, name: parts[0] };
    }
    return null;
  }

  function isRefused(phase, name) {
    return Boolean(state.refused && state.refused.targets.some(
      (target) => target.phase === phase && target.name === name));
  }

  function refusalNote(phase, field) {
    const said = state.refused;
    const note = node("div", "builder-refusal");
    note.setAttribute("role", "alert");
    note.appendChild(withCode(node("span", "builder-refusal-why"), said.why));
    note.appendChild(withCode(node("span", "builder-refusal-fix"), said.fix));
    if (said.suggestion !== null && said.suggestion !== undefined) {
      const use = node("button", "ghost-btn builder-refusal-use", `Use ${said.suggestion}`);
      use.type = "button";
      use.addEventListener("click", (event) => {
        event.preventDefault();
        state.values[phase] = state.values[phase] || {};
        state.values[phase][field.name] = said.suggestion;
        state.refused = null;
        renderSettings();
        updateSummary();
      });
      note.appendChild(use);
    }
    return note;
  }

  /* Text whose `setting` names are set as code, as the refusals write them. */
  function withCode(host, said) {
    String(said || "").split(/(`[^`]+`)/).forEach((part) => {
      if (/^`[^`]+`$/.test(part)) host.appendChild(node("code", "", part.slice(1, -1)));
      else if (part) host.appendChild(document.createTextNode(part));
    });
    return host;
  }

  /* Said on its fields when the answer names one; cleared by an answer that
   * went through. Returns whether it was said on a field. */
  function sayRefusal(answer) {
    if (!answer || answer.ok) {
      if (state.refused) {
        state.refused = null;
        renderSettings();
      }
      return false;
    }
    const remedy = answer.remedy || null;
    const targets = remedy ? (remedy.settings || []).map(targetOf).filter(Boolean) : [];
    if (!targets.length) return false;
    state.refused = {
      targets, why: remedy.why || answer.error || "", fix: remedy.fix || "",
      suggestion: remedy.suggestion === undefined ? null : remedy.suggestion,
    };
    targets.forEach((target) => state.open.add(target.phase));
    renderSettings();
    const first = targets[0];
    const wrap = Array.from(document.querySelectorAll("#run-settings .builder-field.is-refused"))
      .find((found) => found.dataset.setting === first.name);
    if (wrap) {
      wrap.scrollIntoView({ block: "center", behavior: "smooth" });
      const input = wrap.querySelector("input, select, textarea");
      if (input) input.focus({ preventScroll: true });
    }
    return true;
  }

  // ------------------------------------------ a residue's protonation state

  /* `setup.residue_states` as rows: a residue named by chain and number, and
   * the state it takes in place of the one setup would choose. The residues
   * are offered from the structure (the preview lists each one that can take
   * another state), and a histidine clicked in the picture is added here. A
   * row whose state is not chosen yet stays in the form and reaches the
   * config once it has one. */
  let residuesPending = [];
  let titratable = [];

  function residueStatesChosen() {
    const value = (state.values.setup || {}).residue_states;
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function residueStatesControl(field) {
    const table = field.states || {};
    const meaning = field.meaning || {};
    const box = document.createElement("div");
    box.className = "builder-residues";
    const chosen = Object.assign({}, residueStatesChosen());
    const known = new Map(titratable.map((residue) => [residue.key, residue]));
    const commit = () => box.dispatchEvent(new Event("change", { bubbles: true }));
    const stateLabel = (name) => (meaning[name] ? `${name}, ${meaning[name]}` : name);

    const keys = Object.keys(chosen).concat(residuesPending.filter((key) => !(key in chosen)));
    const rows = document.createElement("div");
    rows.className = "builder-residues-rows";
    keys.forEach((key) => {
      const residue = known.get(key);
      const line = document.createElement("div");
      line.className = "builder-residue-row";
      line.dataset.residue = key;
      line.appendChild(node("span", "builder-residue-name",
        residue ? `${key} ${residue.resname}` : key));
      const pick = document.createElement("select");
      pick.className = "builder-residue-state";
      pick.setAttribute("aria-label", `State for ${key}`);
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Choose a state";
      pick.appendChild(blank);
      const states = residue ? residue.states
        : [].concat(...Object.values(table));
      states.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = stateLabel(name);
        pick.appendChild(option);
      });
      pick.value = chosen[key] || "";
      pick.addEventListener("change", (event) => {
        event.stopPropagation();
        if (pick.value) {
          chosen[key] = pick.value;
          residuesPending = residuesPending.filter((k) => k !== key);
        } else {
          delete chosen[key];
          if (!residuesPending.includes(key)) residuesPending.push(key);
        }
        commit();
      });
      line.appendChild(pick);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "run-sweep-remove";
      remove.setAttribute("aria-label", `Leave ${key} to setup`);
      remove.textContent = "\u00d7";
      remove.addEventListener("click", (event) => {
        event.preventDefault();
        delete chosen[key];
        residuesPending = residuesPending.filter((k) => k !== key);
        commit();
      });
      line.appendChild(remove);
      if (residue && residue.near) {
        line.appendChild(node("span", "builder-card-note builder-residue-near", residue.near));
      }
      rows.appendChild(line);
    });
    box.appendChild(rows);

    const add = document.createElement("div");
    add.className = "builder-residues-add";
    const free = titratable.filter((residue) => !keys.includes(residue.key));
    if (titratable.length) {
      const pick = document.createElement("select");
      pick.className = "builder-residue-pick";
      pick.setAttribute("aria-label", "A residue to set");
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = free.length ? "Add a residue" : "Every residue is listed";
      pick.appendChild(blank);
      Object.keys(table).forEach((resname) => {
        const these = free.filter((residue) => residue.resname === resname);
        if (!these.length) return;
        const group = document.createElement("optgroup");
        group.label = resname;
        these.forEach((residue) => {
          const option = document.createElement("option");
          option.value = residue.key;
          option.textContent = `${residue.key} ${resname}` + (residue.near ? ` (${residue.near})` : "");
          group.appendChild(option);
        });
        pick.appendChild(group);
      });
      pick.disabled = !free.length;
      pick.addEventListener("change", (event) => {
        event.stopPropagation();
        if (!pick.value) return;
        residuesPending.push(pick.value);
        commit();
      });
      add.appendChild(pick);
    } else {
      // No structure read yet: a residue named as the structure numbers it.
      const typed = document.createElement("input");
      typed.type = "text";
      typed.className = "builder-residue-typed";
      typed.placeholder = "A:57";
      typed.setAttribute("aria-label", "A residue, as chain and number");
      const go = document.createElement("button");
      go.type = "button";
      go.className = "ghost-btn builder-residue-add";
      go.textContent = "Add";
      const take = (event) => {
        event.preventDefault();
        event.stopPropagation();
        const key = typed.value.trim().toUpperCase().replace(/\s+/g, "");
        if (!/^[A-Z0-9]{1,4}:-?\d+[A-Z]?$/.test(key) || keys.includes(key)) return;
        residuesPending.push(key);
        commit();
      };
      go.addEventListener("click", take);
      typed.addEventListener("change", (event) => event.stopPropagation());
      typed.addEventListener("keydown", (event) => { if (event.key === "Enter") take(event); });
      add.appendChild(typed);
      add.appendChild(go);
    }
    box.appendChild(add);
    if (titratable.some((residue) => residue.resname === "HIS")) {
      box.appendChild(node("span", "builder-card-note",
        "A histidine in the picture of the system can be clicked to add it here."));
    }

    box.readValue = () => (Object.keys(chosen).length ? Object.assign({}, chosen) : null);
    box.writeValue = () => {};
    return box;
  }

  /* A residue picked in the picture: added to the rows, the setup settings
   * opened on it, and its state asked for. */
  function pickResidue(key) {
    if (!key) return;
    if (!(key in residueStatesChosen()) && !residuesPending.includes(key)) {
      residuesPending.push(key);
    }
    state.open.add("setup");
    renderSettings();
    const row = document.querySelector(
      `#run-settings .builder-residue-row[data-residue="${CSS.escape(key)}"]`);
    if (!row) return;
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    const pick = row.querySelector("select");
    if (pick) pick.focus({ preventScroll: true });
    row.classList.add("is-pointed");
    setTimeout(() => row.classList.remove("is-pointed"), 1600);
  }

  // --------------------------------------------------- which measurements

  function analysisSection() {
    const section = document.createElement("div");
    section.className = "run-section run-section-open";

    const head = document.createElement("div");
    head.className = "run-section-head run-section-head-static";
    head.innerHTML =
      '<span class="run-section-name">Analyses</span>' +
      `<span class="run-section-count">${
        state.analyses.size ? `${state.analyses.size} chosen` : "all of them"
      }</span>`;
    section.appendChild(head);

    const note = document.createElement("p");
    note.className = "builder-card-note run-section-note";
    note.textContent =
      state.analyses.size
        ? "Written to the config as `analysis.include`."
        : "Choose nothing and every analysis that applies is run.";
    section.appendChild(note);

    const options = state.schema.analysis_options;
    const body = document.createElement("div");
    body.className = "run-analyses";
    if (!options || !options.available) {
      const said = document.createElement("div");
      said.className = "empty-detail muted";
      said.textContent = (options && options.reason) || "No analyses available.";
      body.appendChild(said);
      section.appendChild(body);
      return section;
    }

    /* Fifteen analyses read as a list; grouped, they read as a few kinds of
     * question. The order comes from the payload so the grouping lives with
     * the analyses rather than here. */
    const order = options.category_order || [];
    const categories = options.categories || {};
    const grouped = {};
    Object.keys(options.analyses).sort().forEach((name) => {
      const title = categories[name] || "Other";
      (grouped[title] = grouped[title] || []).push(name);
    });
    order.forEach((title) => {
      const members = grouped[title];
      if (!members || !members.length) return;
      const heading = document.createElement("div");
      heading.className = "run-analysis-group";
      heading.textContent = title;
      body.appendChild(heading);
      members.forEach((name) => body.appendChild(analysisRow(name, options)));
    });
    section.appendChild(body);
    return section;
  }

  function analysisRow(name, options) {
    const explanation = (options.explanations || {})[name] || {};
    const settings = (options.analyses[name] || []).filter((o) => !o.shared);

    const row = document.createElement("div");
    row.className = "analyse-row";
    row.dataset.chosen = String(state.analyses.has(name));

    const head = document.createElement("div");
    head.className = "analyse-row-head";

    const label = document.createElement("label");
    label.className = "analyse-choice";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.analyses.has(name);
    box.addEventListener("change", () => {
      if (box.checked) state.analyses.add(name);
      else state.analyses.delete(name);
      renderSettings();
      updateSummary();
    });
    const naming = document.createElement("span");
    naming.className = "analyse-naming";
    const title = document.createElement("span");
    title.className = "analyse-name";
    title.textContent = name;
    naming.appendChild(title);
    if (explanation.title) {
      const what = document.createElement("span");
      what.className = "analyse-what";
      what.textContent = explanation.title;
      naming.appendChild(what);
    }
    label.appendChild(box);
    label.appendChild(naming);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "analyse-toggle";
    toggle.textContent = settings.length
      ? `${settings.length} setting${settings.length === 1 ? "" : "s"}`
      : "no settings";
    toggle.disabled = !settings.length;
    const key = `analysis:${name}`;
    toggle.setAttribute("aria-expanded", String(state.open.has(key)));
    toggle.addEventListener("click", () => {
      if (state.open.has(key)) state.open.delete(key);
      else state.open.add(key);
      renderSettings();
    });

    head.appendChild(label);
    head.appendChild(toggle);
    row.appendChild(head);

    if (explanation.detail) {
      const explains = document.createElement("p");
      explains.className = "analyse-explains";
      explains.textContent = explanation.detail;
      row.appendChild(explains);
    }

    if (state.open.has(key)) {
      const body = document.createElement("div");
      body.className = "analyse-settings";
      settings.forEach((option) => body.appendChild(analysisControl(name, option)));
      row.appendChild(body);
    }
    return row;
  }

  function analysisControl(analysis, option) {
    const wrap = document.createElement("label");
    wrap.className = "builder-field";
    const label = document.createElement("span");
    label.className = "builder-label";
    label.textContent = option.name.replace(/_/g, " ");
    wrap.appendChild(label);

    /* Something the run works out for itself. Shown, because seeing what it
     * will use is worth something, but not editable: typing a ligand name
     * that does not match the one detected would have the analysis find
     * nothing and report the ligand as absent. */
    if (option.supplied_by_the_run) {
      const shown = document.createElement("div");
      shown.className = "run-supplied-value";
      shown.textContent = "detected from the structure when the run starts";
      wrap.appendChild(shown);
      if (option.help) {
        const help = document.createElement("span");
        help.className = "builder-card-note";
        help.textContent = option.help;
        wrap.appendChild(help);
      }
      return wrap;
    }

    let input;
    if (option.control === "multiselect" && option.choices) {
      /* Checkboxes, not a multiple select. A native multiple select needs
       * ctrl-click to take more than one, which nobody guesses and which
       * makes the control look broken to anyone who tries it once. With eight
       * interaction types the boxes also show at a glance what is on. */
      const chosen = new Set(
        Array.isArray(option.default) ? option.default : []
      );
      const group = document.createElement("div");
      group.className = "run-checkbox-group";
      const boxes = [];
      option.choices.forEach((choice) => {
        const item = document.createElement("label");
        item.className = "run-checkbox";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = choice;
        box.checked = chosen.has(choice);
        const text = document.createElement("span");
        text.textContent = choice.replace(/_/g, " ");
        item.appendChild(box);
        item.appendChild(text);
        group.appendChild(item);
        boxes.push(box);
      });
      const report = () => {
        const picked = boxes.filter((b) => b.checked).map((b) => b.value);
        state.analysisOptions[analysis] = state.analysisOptions[analysis] || {};
        // All of them is the default, so recording it changes nothing and
        // only clutters the config.
        const isDefault = picked.length === option.choices.length;
        if (!picked.length || isDefault) {
          delete state.analysisOptions[analysis][option.name];
        } else {
          state.analysisOptions[analysis][option.name] = picked;
        }
        updateSummary();
      };
      boxes.forEach((b) => b.addEventListener("change", report));
      wrap.appendChild(group);
      if (option.help) {
        const help = document.createElement("span");
        help.className = "builder-card-note";
        help.textContent = option.help;
        wrap.appendChild(help);
      }
      return wrap;
    }
    if (option.choices && option.choices.length) {
      input = document.createElement("select");
      option.choices.forEach((choice) => {
        const item = document.createElement("option");
        item.value = choice;
        item.textContent = choice;
        if (choice === option.default) item.selected = true;
        input.appendChild(item);
      });
    } else if (typeof option.default === "boolean") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = option.default;
    } else {
      input = document.createElement("input");
      input.type = typeof option.default === "number" ? "number" : "text";
      if (input.type === "number") input.step = "any";
      if (option.is_path) {
        // The same picker every other path field has: a browser will not
        // offer a dialog for a path the server has to open.
        input.id = `option-${analysis}-${option.name}`;
        input.setAttribute("data-picks", "structure");
      }
      input.placeholder =
        option.default === null || option.default === undefined
          ? ""
          : String(option.default);
    }
    const current = (state.analysisOptions[analysis] || {})[option.name];
    if (current !== undefined) {
      if (input.type === "checkbox") input.checked = Boolean(current);
      else input.value = current;
    }
    input.addEventListener("change", () => {
      let value;
      if (input.multiple) {
        value = Array.from(input.selectedOptions).map((o) => o.value);
        // Choosing none means "leave it alone", not "run nothing".
        if (!value.length) value = "";
      } else {
        value = input.type === "checkbox" ? input.checked : input.value;
      }
      state.analysisOptions[analysis] = state.analysisOptions[analysis] || {};
      if (value === "" || value === null) {
        delete state.analysisOptions[analysis][option.name];
      } else {
        state.analysisOptions[analysis][option.name] = value;
      }
      updateSummary();
    });
    wrap.appendChild(input);

    if (option.help) {
      const help = document.createElement("span");
      help.className = "builder-card-note";
      help.textContent = option.help;
      wrap.appendChild(help);
    }
    return wrap;
  }

  // ------------------------------------------------------------- the config

  function currentState() {
    const config = {
      output: el("run-output").value.trim(),
      include_phase: PHASES.filter((p) => state.phases.has(p.name)).map((p) => p.name),
      sweep: state.sweep.filter((row) => row.axis && row.values.trim())
        .map((row) => ({ axis: row.axis, values: row.values.trim() })),
    };

    if (state.start === "structure") {
      config.system = el("run-system").value.trim();
    } else {
      // The structure a trajectory refers to is the system, and the
      // trajectory itself is where the analysis looks.
      config.system = el("run-topology").value.trim();
      config.analysis = {
        trajectory: el("run-trajectory").value.trim(),
        topology: el("run-topology").value.trim(),
      };
    }

    PHASES.forEach((phase) => {
      if (!state.phases.has(phase.name)) return;
      const chosen = state.values[phase.name];
      if (chosen && Object.keys(chosen).length) {
        config[phase.name] = Object.assign(config[phase.name] || {}, chosen);
      }
    });

    // The two sections that are not phases. They are drawn from the schema
    // and stored under sentinel keys like everything else, and this loop
    // walks PHASES -- so every setting in "This run" and "How the runs are
    // scheduled" was collected by the form and then left in the browser.
    // The server has read both keys all along; nothing was sending them.
    // The execution section's own note says it exists because a study
    // wanting two GPUs had to be written by hand, and it still did.
    [RUN_OPTIONS_KEY, EXECUTION_KEY].forEach((key) => {
      const chosen = state.values[key];
      if (chosen && Object.keys(chosen).length) config[key] = chosen;
    });

    if (state.phases.has("analysis")) {
      const analysis = config.analysis || {};
      if (state.analyses.size) {
        analysis.include = Array.from(state.analyses).join(", ");
      }
      const nested = {};
      Object.keys(state.analysisOptions).forEach((name) => {
        const values = state.analysisOptions[name];
        const wanted = !state.analyses.size || state.analyses.has(name);
        if (wanted && values && Object.keys(values).length) nested[name] = values;
      });
      if (Object.keys(nested).length) analysis.options = nested;
      config.analysis = Object.assign(analysis, config.analysis || {});
    }

    const everything = el("run-full-config");
    config.full = Boolean(everything && everything.checked);
    if (state.study) config.study = JSON.parse(JSON.stringify(state.study));
    return config;
  }

  /* Why the run cannot start, or "" when it can.
   *
   * This used to be a boolean, so the button went grey and nothing said
   * which condition had failed. A config arriving from the Agent with no
   * `systems` landed in a form with no structure, a disabled button, and
   * no way to find out why -- the reader is left guessing at a rule the
   * software already knows. */
  function whyNotReady() {
    if (!state.start) return "Choose what this run starts from.";
    if (state.start === "config") {
      if (!state.configVerdict) return "No config checked yet.";
      return state.configVerdict.ok ? "" : state.configVerdict.error;
    }
    if (!state.phases.size) return "Choose at least one phase to run.";
    /* Simulate without Setup, starting from a structure, has nothing to
     * simulate: setup is what turns a structure into a system. Reported
     * from the browser as a run that started, failed a minute later with
     * "setup outputs are missing", and then had a paragraph about
     * timesteps attached to it. The rule is the software's own -- the
     * pipeline refuses this -- so the button should refuse it first. */
    if (state.start === "structure" &&
        state.phases.has("simulation") && !state.phases.has("setup")) {
      return "Simulate needs Setup first: a structure has to be solvated " +
             "and parameterised before it can be run. Tick Setup, or " +
             "start from an existing run's output instead.";
    }
    if (state.start === "structure" && !el("run-system").value.trim()) {
      return "Choose a structure: a PDB file, or an identifier to fetch.";
    }
    if (state.start !== "structure" &&
        !(el("run-trajectory").value.trim() &&
          el("run-topology").value.trim())) {
      return "Choose a trajectory and the topology that matches it.";
    }
    return "";
  }

  /* The default name is the server's to make -- one rule, in
   * fastmdxplora/naming.py, that knows the system:
   * fastmdxplora_<system>_study_<UTC timestamp>. The browser sends the
   * field as typed, empty included, and never names a folder itself; a
   * copy of the rule here would be the seventh, and drift. For the note
   * under the field it shows the pattern. */
  function defaultOutput() {
    const field = state.start === "structure" ? el("run-system") : el("run-topology");
    const system = (field && field.value.trim()) || "";
    const slug = system ? system.split(/[\\/]/).pop().replace(/\.[^.]+$/, "")
      .replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "").slice(0, 40) : "";
    return "fastmdxplora" + (slug ? "_" + slug : "") + "_study_<timestamp>";
  }

  function ready() {
    return !whyNotReady();
  }

  function updateSummary() {
    if (state.start === "config") {
      const verdict = state.configVerdict;
      text(
        el("run-summary"),
        verdict && verdict.ok ? verdict.phases.join(" → ") : "No config checked yet"
      );
    } else {
      const doing = PHASES.filter((p) => state.phases.has(p.name))
        .map((p) => p.label);
      // The chain of phases, or nothing. The reason a run is not ready
      // was shown here too, and "Choose what this run starts from" in the
      // page header read as a heading rather than a note; it is said at
      // the Run button, where it can be acted on.
      const runs = sweepRuns();
      const chain = doing.join(" → ");
      text(el("run-summary"),
           !doing.length ? "" : runs > 1 ? `${chain}, ${runs} runs` : chain);
    }
    const can = ready();
    ["run-start-button", "run-download", "run-copy-command",
     "run-download-script", "run-elsewhere"].forEach((id) => {
      const button = el(id);
      if (button) button.disabled = !can;
    });
    schedulePreview();
  }

  // ------------------------------------------------ what setup will build

  /* The box, the particle count and the time were learned from setup's log,
   * minutes into a run and after the settings could be changed. The
   * structure and the settings decide most of it, so it is said here, under
   * the structure, as the settings change: asked for half a second after
   * the last change, and an answer to an older form is dropped. */
  const PREVIEW_DELAY_MS = 600;
  let previewTimer = null;
  let previewKey = "";
  let previewSeq = 0;

  function schedulePreview() {
    const box = el("run-system-preview");
    const system = el("run-system");
    if (!box || !system) return;
    const wanted = state.start === "structure" && state.phases.has("setup")
      && system.value.trim();
    if (!wanted) {
      box.hidden = true;
      previewKey = "";
      previewSeq += 1;
      clearTimeout(previewTimer);
      return;
    }
    const body = currentState();
    const key = JSON.stringify(body);
    if (key === previewKey) return;
    previewKey = key;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => loadPreview(body), PREVIEW_DELAY_MS);
  }

  async function loadPreview(body) {
    const seq = ++previewSeq;
    const box = el("run-system-preview");
    box.hidden = false;
    box.classList.add("is-loading");
    text(el("run-system-preview-state"), "Working it out\u2026");
    let answer;
    try {
      const response = await fetch("/api/preview-system", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      answer = await response.json();
    } catch (error) {
      answer = { ok: false, reason: "The preview could not be reached." };
    }
    if (seq !== previewSeq) return;
    box.classList.remove("is-loading");
    renderPreview(answer || {});
  }

  function node(tag, className, content) {
    const made = document.createElement(tag);
    if (className) made.className = className;
    if (content !== undefined && content !== null) made.textContent = String(content);
    return made;
  }

  const count = (value) => Number(value).toLocaleString("en-US");

  function signed(value) {
    const n = Number(value) || 0;
    return n > 0 ? `+${n}` : n < 0 ? `\u2212${-n}` : "0";
  }

  function duration(seconds) {
    const s = Number(seconds) || 0;
    if (s < 90) return `${Math.round(s)} seconds`;
    if (s < 5400) return `${Math.round(s / 60)} minutes`;
    if (s < 129600) return `${(s / 3600).toFixed(1)} hours`;
    return `${(s / 86400).toFixed(1)} days`;
  }

  function renderPreview(answer) {
    const body = el("run-system-preview-body");
    if (!body) return;
    body.textContent = "";
    try {
      drawSystem(answer);
    } catch (error) {
      // The numbers stand without the picture.
      const view = el("run-system-preview-view");
      if (view) view.hidden = true;
    }
    if (!answer.ok) {
      text(el("run-system-preview-state"), "");
      body.appendChild(node("p", "builder-card-note", answer.reason || "Nothing to say yet."));
      return;
    }
    const e = answer.estimate;
    text(el("run-system-preview-state"), "an estimate");
    const listed = Array.isArray(answer.titratable) ? answer.titratable : [];
    if (JSON.stringify(listed) !== JSON.stringify(titratable)) {
      titratable = listed;
      if (state.open.has("setup")) renderSettings();
    }

    const headline = node("p", "system-preview-headline");
    headline.appendChild(node("strong", "system-preview-count", `about ${count(e.particles)}`));
    headline.appendChild(document.createTextNode(
      ` particles in a ${e.box_shape} ${Number(e.width_nm).toFixed(2)} nm from face to face ` +
      `(${Math.round(e.volume_nm3).toLocaleString("en-US")} nm\u00b3)`));
    body.appendChild(headline);

    const rows = node("dl", "system-preview-rows");
    const row = (label, value, tone) => {
      const term = node("dt", null, label);
      const said = node("dd", tone ? `is-${tone}` : null, value);
      rows.appendChild(term);
      rows.appendChild(said);
    };
    const named = e.chains.length === 1 ? `chain ${e.chains[0]}` : `chains ${e.chains.join(", ")}`;
    const copies = Number(e.copies) || 1;
    const chains = copies > 1
      ? `${e.chains.length * copies} chains (${named} and ` +
        `${copies - 1 === 1 ? "a copy" : `${copies - 1} copies`} of ` +
        `${e.chains.length === 1 ? "it" : "each"} by symmetry)`
      : named;
    row("Solute",
        `${count(e.residues)} residues in ${chains}` +
        (e.gaps_built ? ` (${count(e.gaps_built)} missing ones built)` : "") +
        `, ${count(e.solute_atoms)} atoms with hydrogens, net charge ${signed(e.net_charge)}`);
    row("Ligands", e.ligands.length ? e.ligands.join(", ") : "none kept");
    row("Water", `${count(e.waters)} ${String(e.water_model).toUpperCase()}`);
    row("Ions", `${count(e.ions_positive)} ${e.positive_ion} and ` +
                `${count(e.ions_negative)} ${e.negative_ion}`);
    if (e.refuses) {
      row("Padding", `${e.padding_nm} nm leaves the box narrower than twice the cutoff, ` +
          "and setup will refuse it: raise the padding, choose a cube, or lower the cutoff.",
          "warning");
    } else if (e.grows) {
      row("Padding", `grown from ${e.padding_nm} to ${Number(e.padding_used_nm).toFixed(2)} nm, ` +
          "so the box clears twice the cutoff with room for the barostat to shrink it",
          "note");
    } else {
      row("Padding", `${e.padding_nm} nm between the solute and its nearest periodic image`);
    }
    const time = answer.time || {};
    row("Time here", time.ok
      ? `about ${duration(time.seconds)}` + (time.runs > 1 ? ` for ${time.runs} runs` : "") +
        ` on ${time.platform || "this machine"}, ${count(time.steps)} steps in all`
      : (time.reason || "not known"), time.ok ? null : "muted");
    body.appendChild(rows);

    (e.notes || []).forEach((said) => body.appendChild(node("p", "builder-card-note", said)));

    const advice = answer.advisories || [];
    if (advice.length) {
      const list = node("ul", "system-preview-advice");
      advice.forEach((item) => {
        const entry = node("li");
        entry.appendChild(node("strong", null, item.summary));
        entry.appendChild(document.createTextNode(` ${item.detail} ${item.remedy}`));
        if (item.setting && phaseOf(item.setting)) {
          const go = node("button", "ghost-btn system-preview-go", `Show ${item.setting.replace(/_/g, " ")}`);
          go.type = "button";
          go.addEventListener("click", () => showSetting(item.setting));
          entry.appendChild(go);
        }
        list.appendChild(entry);
      });
      body.appendChild(list);
    }
    body.appendChild(node("p", "builder-card-note",
      "Worked out from the structure and these settings. Setup's own numbers " +
      "replace it once it has run."));
  }

  /* The periodic cell setup builds, as OpenMM lays out its box vectors for a
   * box of width w (nm or Angstrom, as given). */
  function boxVectors(shape, w) {
    const r2 = Math.SQRT2;
    if (shape === "dodecahedron") return [[w, 0, 0], [0, w, 0], [w / 2, w / 2, w * r2 / 2]];
    if (shape === "octahedron") {
      return [[w, 0, 0], [w / 3, 2 * r2 * w / 3, 0], [-w / 3, r2 * w / 3, Math.sqrt(6) * w / 3]];
    }
    return [[w, 0, 0], [0, w, 0], [0, 0, w]];
  }

  /* The cell as its shape: the points nearer the origin than any of its
   * periodic images (the Wigner-Seitz cell), which is the cube, the rhombic
   * dodecahedron or the truncated octahedron the names mean. Its corners
   * are where three of the planes halfway to a neighbouring image meet,
   * inside all the others; an edge joins two corners on two planes. */
  function periodicCell(vectors) {
    const dot = (p, q) => p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
    const planes = [];
    for (let i = -1; i <= 1; i += 1) {
      for (let j = -1; j <= 1; j += 1) {
        for (let k = -1; k <= 1; k += 1) {
          if (!i && !j && !k) continue;
          const n = [0, 1, 2].map(
            (d) => i * vectors[0][d] + j * vectors[1][d] + k * vectors[2][d]);
          planes.push({ n, d: dot(n, n) / 2 });
        }
      }
    }
    const tol = 1e-6 * Math.max(...planes.map((p) => p.d));
    const det = (m) => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
      - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
      + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    const meet = (a, b, c) => {
      const m = [a.n, b.n, c.n];
      const whole = det(m);
      if (Math.abs(whole) < 1e-12) return null;
      const rhs = [a.d, b.d, c.d];
      return [0, 1, 2].map((col) =>
        det(m.map((row, r) => row.map((v, cc) => (cc === col ? rhs[r] : v)))) / whole);
    };
    const vertices = [];
    for (let a = 0; a < planes.length; a += 1) {
      for (let b = a + 1; b < planes.length; b += 1) {
        for (let c = b + 1; c < planes.length; c += 1) {
          const x = meet(planes[a], planes[b], planes[c]);
          if (!x || !planes.every((p) => dot(p.n, x) <= p.d + tol)) continue;
          if (vertices.some((v) => Math.hypot(v[0] - x[0], v[1] - x[1], v[2] - x[2]) < 1e-6)) continue;
          vertices.push(x);
        }
      }
    }
    const on = vertices.map((v) => planes
      .map((p, index) => (Math.abs(dot(p.n, v) - p.d) <= tol ? index : -1))
      .filter((index) => index >= 0));
    const edges = [];
    for (let i = 0; i < vertices.length; i += 1) {
      for (let j = i + 1; j < vertices.length; j += 1) {
        if (on[i].filter((p) => on[j].includes(p)).length >= 2) edges.push([i, j]);
      }
    }
    return { vertices, edges };
  }

  let previewViewer = null;

  /* The structure setup keeps, copies included, inside the cell it builds,
   * to scale: how much water there is around the protein is seen, not read. */
  function drawSystem(answer) {
    const host = el("run-system-preview-view");
    if (!host) return;
    const e = answer && answer.ok ? answer.estimate : null;
    if (!e || !answer.drawing || typeof window.$3Dmol === "undefined") {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    if (!previewViewer) {
      previewViewer = window.$3Dmol.createViewer(host, { backgroundAlpha: 0 });
    }
    const viewer = previewViewer;
    const tone = (name, fallback) => (getComputedStyle(document.documentElement)
      .getPropertyValue(name) || "").trim() || fallback;
    viewer.clear();
    viewer.addModel(answer.drawing, "pdb");
    viewer.setStyle({ hetflag: false }, { cartoon: { colorscheme: "chain", thickness: 0.6 } });
    viewer.setStyle({ hetflag: true }, { stick: { radius: 0.25 } });
    markResidues(viewer, answer, tone);
    const cell = periodicCell(boxVectors(e.box_shape, Number(e.width_nm) * 10));
    const centre = e.centre_angstrom || [0, 0, 0];
    const at = (v) => ({ x: v[0] + centre[0], y: v[1] + centre[1], z: v[2] + centre[2] });
    const colour = tone("--accent-cyan", "#63e6ff");
    cell.edges.forEach(([i, j]) => {
      viewer.addCylinder({ start: at(cell.vertices[i]), end: at(cell.vertices[j]),
        radius: 0.35, color: colour, fromCap: 1, toCap: 1 });
    });
    host.dataset.edges = String(cell.edges.length);
    // Sized to the panel as it is now: it was hidden, or the window changed.
    viewer.resize();
    viewer.zoomTo();
    // Turned a little off the box's axes, so the cell reads as a solid.
    viewer.rotate(-25, "x");
    viewer.rotate(30, "y");
    viewer.zoom(0.85);
    viewer.render();
  }

  /* Each histidine as a small sphere on its alpha carbon, and every residue
   * given a state as a larger one labelled with it; a click on either picks
   * the residue for `setup.residue_states`. Histidines, because theirs is the
   * choice setup makes least reliably (a tautomer from hydrogen bonds). */
  function markResidues(viewer, answer, tone) {
    const listed = Array.isArray(answer.titratable) ? answer.titratable : [];
    const chosen = residueStatesChosen();
    const selectionOf = (residue) => ({
      chain: residue.chain, resi: residue.number, atom: "CA" });
    const histidine = tone("--accent-orange", "#ffb86b");
    const set = tone("--accent-violet", "#a78bfa");
    viewer.removeAllLabels();
    let marked = 0;
    listed.forEach((residue) => {
      const given = chosen[residue.key];
      if (residue.resname !== "HIS" && !given) return;
      viewer.addStyle(selectionOf(residue),
        { sphere: { radius: given ? 1.6 : 1.1, color: given ? set : histidine } });
      if (given) {
        viewer.addLabel(`${residue.key} ${given}`, {
          fontSize: 11, showBackground: true, backgroundOpacity: 0.7,
          inFront: true }, selectionOf(residue));
      }
      marked += 1;
    });
    const byAtom = new Map(listed.map((residue) => [`${residue.chain}:${residue.number}`, residue]));
    viewer.setClickable({ atom: "CA" }, true, (atom) => {
      const residue = byAtom.get(`${atom.chain}:${atom.resi}`);
      if (residue) pickResidue(residue.key);
    });
    const host = el("run-system-preview-view");
    if (host) host.dataset.residuesMarked = String(marked);
  }

  /* The size and the time of an answer from /api/preview-system, in two
   * sentences: the lines the Agent's plan ends with. */
  function describeCost(answer) {
    if (!answer || !answer.ok) return null;
    const e = answer.estimate;
    let size = `about ${count(e.particles)} particles in a ${e.box_shape} ` +
      `${Number(e.width_nm).toFixed(1)} nm from face to face`;
    if (e.grows) {
      size += `, the padding grown to ${Number(e.padding_used_nm).toFixed(2)} nm for the cutoff`;
    }
    if (e.refuses) size += "; setup will refuse this padding for the cutoff";
    const time = answer.time || {};
    const taken = time.ok
      ? `about ${duration(time.seconds)}` + (time.runs > 1 ? ` for ${time.runs} runs` : "") +
        ` on ${time.platform || "this machine"}`
      : time.code === "environment.calibration.absent"
        ? "not known: this machine has not been timed"
        : "not known: this machine was timed under other settings";
    return { size, time: taken };
  }

  async function previewCost() {
    try {
      const response = await fetch("/api/preview-system", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentState()),
      });
      return describeCost(await response.json());
    } catch (error) {
      return null;
    }
  }

  function phaseOf(setting) {
    if (!state.schema) return null;
    const found = PHASES.find((phase) => state.phases.has(phase.name)
      && settingsFor(phase.name).some((field) => field.name === setting));
    return found ? found.name : null;
  }

  function showSetting(setting) {
    const phase = phaseOf(setting);
    if (!phase) return;
    state.open.add(phase);
    renderSettings();
    const wrap = document.querySelector(`#run-settings [data-setting="${setting}"]`);
    if (!wrap) return;
    wrap.scrollIntoView({ block: "center", behavior: "smooth" });
    const input = wrap.querySelector("input, select, textarea");
    if (input) input.focus({ preventScroll: true });
    wrap.classList.add("is-pointed");
    setTimeout(() => wrap.classList.remove("is-pointed"), 1600);
  }

  async function fetchConfig() {
    const response = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentState()),
    });
    const built = await response.json();
    sayRefusal(built);
    return built;
  }

  async function showConfig() {
    const box = el("run-config-preview");
    const button = el("run-preview");
    if (!box) return;
    if (!box.hidden) {
      box.hidden = true;
      text(button, "Show the config");
      button.setAttribute("aria-expanded", "false");
      return;
    }
    const built = await fetchConfig();
    box.textContent = built.ok ? built.yaml : built.error;
    box.hidden = false;
    text(button, "Hide the config");
    button.setAttribute("aria-expanded", "true");
  }

  /* Save a file where the person chooses. showSaveFilePicker asks for a
   * location; where the browser lacks it, an anchor with a download
   * attribute saves to the default folder, which is what every download
   * did before and what a person reported as "does not let me choose". */
  async function saveAs(text, name, type) {
    const blob = new Blob([text], { type });
    if (typeof window.showSaveFilePicker === "function") {
      try {
        const handle = await window.showSaveFilePicker({ suggestedName: name });
        const stream = await handle.createWritable();
        await stream.write(blob);
        await stream.close();
        return true;
      } catch (error) {
        if (error && error.name === "AbortError") return false;
        // Fall through: a picker that fails for any other reason should
        // not cost the person the file.
      }
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
    return true;
  }

  async function download() {
    const built = await fetchConfig();
    if (!built.ok) {
      text(el("run-note"), built.error);
      return;
    }
    const saved = await saveAs(built.yaml, "fastmdxplora_config.yml", "text/yaml");
    text(el("run-note"), saved ? `${built.settings_changed} setting(s) written.` : "Not saved.");
  }

  /* The same study, in its other two languages. A form that can only hand
   * back a file leaves the command and the script to be written by hand,
   * which is where a flag gets the wrong prefix and the run that follows is
   * not the study the form described. Both come from the server, derived
   * from the same schema the form was drawn from. */
  async function copyCommand() {
    const built = await fetchConfig();
    if (!built.ok) {
      text(el("run-note"), built.error);
      return;
    }
    if (!built.command) {
      text(el("run-note"),
           "This study cannot be said as one command; use the config file.");
      return;
    }
    try {
      await navigator.clipboard.writeText(built.command);
      text(el("run-note"), "Command copied.");
    } catch (refused) {
      // A page served over plain HTTP -- an SSH tunnel, commonly -- has no
      // clipboard access. The command is shown instead, still selectable.
      const box = el("run-config-preview");
      if (box) {
        box.textContent = built.command;
        box.hidden = false;
      }
      text(el("run-note"), "Clipboard unavailable here; command shown below.");
    }
  }

  async function downloadScript() {
    const built = await fetchConfig();
    if (!built.ok) {
      text(el("run-note"), built.error);
      return;
    }
    const saved = await saveAs(built.script, "fastmdxplora_study.py", "text/x-python");
    if (!saved) text(el("run-note"), "Not saved.");
  }

  /* A config that has been elsewhere can be run as it stands, or opened and
   * changed. Opening it never writes to it: the file may be committed beside
   * a paper or be the record of a run that already happened, so anything
   * altered is saved as a new file and the original is left alone. */
  async function loadConfigIntoForm() {
    const path = el("run-config-path").value.trim();
    if (!path) return;
    const response = await fetch("/api/load-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const loaded = await response.json();
    if (!loaded.ok) {
      text(el("run-config-verdict"), loaded.error);
      return;
    }

    applyLoadedState(loaded.state, {
      from: path,
      note:
        `Opened ${path}. Changes here are saved as a new file; that one is ` +
        "left as it is.",
    });
  }

  /* Fill the form in from a config the server has already mapped to form
     state. Factored out of loadConfigIntoForm so the agent panel can use
     it: the agent holds a config as an object and never as a path, and
     the mapping from one to the other is server-side and not trivial.
     Duplicating it in JavaScript would be a second place for it to drift. */
  function applyLoadedState(from, options = {}) {
    if (!from) return undefined;
    if (!state.schema) {
      // The form is drawn from the schema. A config that arrives first, an
      // Agent's reply restored as the page opens on a slow machine, threw
      // drawing it, and the reply's actions and its plan's cost did nothing.
      return loadSchema().then(() => applyLoadedState(from, options));
    }
    const { from: origin, note } = options;
    state.study = JSON.parse(JSON.stringify(from.study || {}));
    state.start = from.start;
    // `include_phase` is what the server sends since the phase lists took one
    // name; reading `include` alone left every phase unticked on load, and the
    // Agent's configs arrive through here too. The old name is still read.
    state.phases = new Set(from.include_phase || from.include || []);
    state.values = from.phases || {};
    stoppingDraft = null;
    residuesPending = [];
    state.analyses = new Set(from.analyses || []);
    state.analysisOptions = from.analysis_options || {};
    state.sweep = (from.sweep || []).map((row) => ({
      axis: String(row.axis || ""), values: String(row.values || ""),
    }));
    if (state.sweep.length) state.open.add(SWEEP_KEY);
    // How the study was written belongs to the run rather than a phase, so
    // it goes where the "This run" controls read from. Dropped before, so
    // an agent-written config opened here came back claiming a person
    // wrote it.
    if (from.study && Object.keys(from.study).length) {
      state.values[RUN_OPTIONS_KEY] = Object.assign(
        {}, state.values[RUN_OPTIONS_KEY] || {}, from.study
      );
    }
    // And how the runs are scheduled, into the execution controls.
    if (from.execution && Object.keys(from.execution).length) {
      state.values[EXECUTION_KEY] = Object.assign(
        {}, state.values[EXECUTION_KEY] || {}, from.execution
      );
    }
    // Only set where there is a file behind it. A config that was never on
    // disk has nothing to be "left as it is".
    if (origin) state.loadedFrom = origin;

    renderAll();
    // The fields the form owns rather than the settings tables.
    if (el("run-system")) el("run-system").value = from.system || "";
    if (el("run-trajectory")) el("run-trajectory").value = from.trajectory || "";
    if (el("run-topology")) el("run-topology").value = from.topology || "";
    if (el("run-output")) el("run-output").value = from.output || "";
    updateSummary();
    if (note) text(el("run-note"), note);
  }

  /* Complete studies to start from (starters.py): a tile each, with what it
   * is for and what it will run. Chosen, it is loaded as any Config is, and
   * the note says what to change first. Built as elements. */
  const STARTER_FACTS = ["System", "Production", "Sampling", "Stops when", "Membrane"];
  const STARTERS_FOLDED = "fastmdx-starters-folded";

  function drawStarters() {
    const host = el("run-starters");
    const card = el("run-starters-card");
    const starters = (state.schema && state.schema.starters) || [];
    if (!host || !card) return;
    host.replaceChildren();
    card.hidden = !starters.length;
    // Folded, it stays folded for this person: someone who writes their
    // own studies need not scroll past it each time.
    try {
      if (window.localStorage.getItem(STARTERS_FOLDED) === "1") card.open = false;
    } catch (error) { /* storage unavailable: shown open */ }
    if (!card.dataset.remembers) {
      card.dataset.remembers = "1";
      card.addEventListener("toggle", () => {
        try {
          window.localStorage.setItem(STARTERS_FOLDED, card.open ? "0" : "1");
        } catch (error) { /* nothing to remember it in */ }
      });
    }
    starters.forEach((starter) => {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "starter";
      tile.setAttribute("role", "listitem");
      tile.dataset.starter = starter.id;
      const title = document.createElement("span");
      title.className = "starter-title";
      title.textContent = starter.title;
      const what = document.createElement("span");
      what.className = "starter-what";
      what.textContent = starter.what;
      const facts = document.createElement("span");
      facts.className = "starter-facts mono";
      facts.textContent = (starter.plan || [])
        .filter((line) => STARTER_FACTS.includes(line.label))
        .map((line) => line.value).join(" \u00b7 ");
      tile.append(title, what, facts);
      tile.addEventListener("click", () => startFrom(starter, tile));
      host.appendChild(tile);
    });
  }

  async function startFrom(starter, tile) {
    const note = el("run-starters-note");
    let loaded = null;
    try {
      const response = await fetch("/api/load-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: starter.config }),
      });
      loaded = await response.json();
    } catch (error) {
      loaded = { ok: false, error: "The server did not answer." };
    }
    if (!loaded || !loaded.ok) {
      text(note, (loaded && loaded.error) || "Could not load it.");
      return;
    }
    await applyLoadedState(loaded.state, {});
    el("run-starters")?.querySelectorAll(".starter").forEach(
      (other) => other.classList.toggle("is-chosen", other === tile));
    text(note, `Started from "${starter.title}". Change first: ${String(starter.change || "")
      .replace(/`/g, "")}`);
  }

  async function runAsItStands() {
    const path = el("run-config-path").value.trim();
    if (!path) return;
    const button = el("run-as-is");
    button.disabled = true;
    text(el("run-note"), "Starting\u2026");
    const response = await fetch("/api/run-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, output: el("run-output").value.trim() }),
    });
    const started = await response.json();
    if (!started.ok) {
      text(el("run-note"), started.error || "Could not start.");
      button.disabled = false;
      return;
    }
    text(el("run-note"), `Running ${started.config_path} as it stands.`);
    if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
      window.FastMDXDashboard.navigate("overview");
    }
  }

  async function checkConfigFile() {
    const path = el("run-config-path").value.trim();
    const note = el("run-config-verdict");
    if (!path) {
      text(note, "");
      return null;
    }
    const response = await fetch("/api/check-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const verdict = await response.json();
    text(
      note,
      verdict.ok
        ? `Runs. ${verdict.phases.join(" → ")}, ` +
          `${verdict.systems} system(s), ${verdict.settings_named} setting(s) named.`
        : verdict.error
    );
    note.dataset.ok = String(Boolean(verdict.ok));
    state.configVerdict = verdict;
    // Opening and running are only offered once the file is known to be sound.
    ["run-open-config", "run-as-is", "run-as-is-elsewhere"].forEach((id) => {
      const button = el(id);
      if (button) button.disabled = !verdict.ok;
    });
    updateSummary();
    return verdict;
  }

  /* Stopping a run. This lived only on the page being retired, so deleting
   * that page without bringing it across would have left the browser able to
   * start something it could not stop. */
  async function stopRunning() {
    const button = el("run-stop");
    button.disabled = true;
    text(el("run-note"), "Stopping\u2026");
    try {
      const response = await fetch("/api/explore/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const stopped = await response.json();
      text(el("run-note"), stopped.ok ? "Stopped." : (stopped.error || "Could not stop."));
    } catch (error) {
      text(el("run-note"), "Could not reach the server.");
    }
    button.disabled = false;
  }

  /* The button appears only while something is running, and the dashboard is
   * what knows. */
  function watchForARun() {
    if (!window.FastMDXDashboard || !window.FastMDXDashboard.on) return;
    // The handler is given the detail itself, not the event carrying it.
    window.FastMDXDashboard.on("app-state", (detail) => {
      /* `process_running`, not `active_run`. The latter means "there is a
       * run to look at", which stays true after one fails -- the output
       * directory is still there and still being watched -- so Stop the
       * run was still offered for a run that had already stopped.
       * Reported from the browser after a setup failure. */
      const running = Boolean(detail && detail.process_running);
      const stop = el("run-stop");
      if (stop) stop.hidden = !running;

      /* And say why it stopped, where somebody is looking at the button
       * that just disappeared. */
      if (detail && detail.status === "failed" && detail.error) {
        const note = el("run-note");
        if (note && note.textContent !== detail.error) {
          text(note, detail.error);
          note.dataset.ok = "false";
        }
      }
    });
  }

  async function start() {
    const button = el("run-start-button");
    button.disabled = true;
    text(el("run-note"), "Starting\u2026");
    let started;
    try {
      let payload = currentState();
      if (payload.study?.agent) {
        const reviewed = await window.FastMDXReview.review(null, "run");
        if (!reviewed) { button.disabled = false; text(el("run-note"), "Run cancelled; the draft remains available."); return; }
        if (JSON.stringify(reviewed.builder_state) !== JSON.stringify(currentState())) throw new Error("The draft changed. Review it again before running.");
        payload = {...reviewed.builder_state, review_token: reviewed.review_token, review_confirmed: true};
      }
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      started = await response.json();
    } catch (error) {
      text(el("run-note"), error.message || "Could not reach the server.");
      button.disabled = false;
      return;
    }
    if (!started.ok) {
      text(el("run-note"), started.error || "Could not start.");
      sayRefusal(started);
      button.disabled = false;
      return;
    }
    sayRefusal(started);
    text(el("run-note"), `Running. Config written to ${started.config_path}`);
    if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
      window.FastMDXDashboard.navigate("overview");
    }
  }

  /* Hosted behind a service that runs studies on its own compute (a GPU it
   * rents): the config is saved in the workspace beside the folder it will
   * write, and the service's page opens with both filled in. The person
   * confirms there -- the compute is the service's to give, so it asks. */
  function openRunsPage(base, config, output) {
    const query = new URLSearchParams({ config });
    if (output) query.set("output", output);
    window.location.assign(base + "?" + query.toString());
  }

  async function runElsewhere() {
    const button = el("run-elsewhere");
    button.disabled = true;
    text(el("run-note"), "Saving the config\u2026");
    let saved;
    try {
      const response = await fetch("/api/save-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentState()),
      });
      saved = await response.json();
    } catch (error) {
      text(el("run-note"), "Could not reach the server.");
      button.disabled = false;
      return;
    }
    if (!saved.ok) {
      text(el("run-note"), saved.error || "Could not save the config.");
      button.disabled = false;
      return;
    }
    text(el("run-note"), `Saved ${saved.config}. Opening the page that runs it\u2026`);
    openRunsPage(button.dataset.runsUrl, saved.config, saved.output);
  }

  function runAsItStandsElsewhere() {
    const path = el("run-config-path").value.trim();
    if (!path) return;
    openRunsPage(el("run-as-is-elsewhere").dataset.runsUrl, path,
                 el("run-output").value.trim());
  }

  function resetEverything() {
    state.study = {};
    state.phases = state.start
      ? new Set(STARTING_POINTS[state.start].phases)
      : new Set();
    state.values = {};
    stoppingDraft = null;
    residuesPending = [];
    state.analyses.clear();
    state.analysisOptions = {};
    state.sweep = [];
    state.open.clear();
    const output = el("run-output");
    if (output) output.value = "";
    const everything = el("run-full-config");
    if (everything) everything.checked = false;
    const box = el("run-config-preview");
    if (box) box.hidden = true;
    text(el("run-preview"), "Show the config");
    text(el("run-note"), "Everything is back to its default.");
    renderAll();
  }

  // ------------------------------------------------------------------- wire

  function renderAll() {
    renderStart();
    renderPhases();
    renderSettings();
    updateSummary();
  }


  /* Where the results will actually be written, for whatever is in the box.
   * Saying "a name lands in /somewhere" left the reader to do the joining;
   * this says the path. */
  function describeOutput() {
    const box = el("run-output");
    const where = (state.schema && state.schema.workspace) || "";
    if (!box) return;
    const typed = box.value.trim();
    const name = typed || defaultOutput();
    // A path is absolute on this machine, not on the one this was written on:
    // C:\Users\... starts with neither a slash nor a tilde, and calling it
    // relative would have the note claim the results land somewhere they
    // will not. The server decides for real; this only has to agree.
    const absolute =
      name.startsWith("/") || name.startsWith("~") || /^[A-Za-z]:[\\/]/.test(name);
    // And joined with whatever separator this machine uses.
    const separator = where.includes("\\") ? "\\" : "/";
    const full = absolute ? name : (where ? `${where}${separator}${name}` : name);
    text(el("run-output-note"), `Results will be saved in ${full}`);
  }

  /* A trajectory usually sits beside the structure it moves. Choosing one and
   * then being asked to find the other in the same folder is a step the page
   * can take itself, and the button that used to do it said "Find beside it",
   * which explained nothing. */
  async function findStructureBeside() {
    const trajectory = el("run-trajectory").value.trim();
    const topology = el("run-topology");
    if (!trajectory || !topology || topology.value.trim()) return;

    const folder = trajectory.replace(/[/\\][^/\\]*$/, "");
    if (!folder) return;
    try {
      const response = await fetch(
        "/api/inspect-directory?path=" + encodeURIComponent(folder)
      );
      const found = await response.json();
      if (found.ok && found.suggestion && found.suggestion.topology) {
        topology.value = found.suggestion.topology;
        updateSummary();
      }
    } catch (error) {
      // Not finding one is not a failure: the field is still there to type in.
    }
  }

  function attach() {
    if (!el("run-start")) return;

    ["run-system", "run-trajectory", "run-topology", "run-output"].forEach((id) => {
      const input = el(id);
      if (input) input.addEventListener("input", updateSummary);
    });
    const output = el("run-output");
    if (output) output.addEventListener("input", describeOutput);
    const trajectory = el("run-trajectory");
    if (trajectory) trajectory.addEventListener("change", findStructureBeside);

    const configPath = el("run-config-path");
    if (configPath) configPath.addEventListener("change", checkConfigFile);
    const checkButton = el("run-check-config");
    if (checkButton) checkButton.addEventListener("click", checkConfigFile);
    const openButton = el("run-open-config");
    if (openButton) openButton.addEventListener("click", loadConfigIntoForm);
    const asIsButton = el("run-as-is");
    if (asIsButton) asIsButton.addEventListener("click", runAsItStands);
    const asIsElsewhere = el("run-as-is-elsewhere");
    if (asIsElsewhere) asIsElsewhere.addEventListener("click", runAsItStandsElsewhere);
    const elsewhere = el("run-elsewhere");
    if (elsewhere) elsewhere.addEventListener("click", runElsewhere);

    const preview = el("run-preview");
    if (preview) preview.addEventListener("click", showConfig);
    const downloadButton = el("run-download");
    if (downloadButton) downloadButton.addEventListener("click", download);
    const commandButton = el("run-copy-command");
    if (commandButton) commandButton.addEventListener("click", copyCommand);
    const scriptButton = el("run-download-script");
    if (scriptButton) scriptButton.addEventListener("click", downloadScript);
    const startButton = el("run-start-button");
    if (startButton) startButton.addEventListener("click", start);
    const reset = el("run-reset");
    if (reset) reset.addEventListener("click", resetEverything);
    const stop = el("run-stop");
    if (stop) stop.addEventListener("click", stopRunning);
    watchForARun();

    loadSchema().then(() => {
      describeOutput();
      renderAll();
      drawStarters();
    }).catch(() => {
      text(el("run-summary"), "Could not read the settings.");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }

  window.FastMDXRun = {
    state, currentState, PHASES, STARTING_POINTS, applyLoadedState,
    /* The four actions the Agent panel offers on a config it just wrote.
     * They read the builder's state, which the panel loads silently
     * first, so the file, the command and the script are the same ones
     * the builder would produce -- one derivation, two doors. */
    fetchConfig, download, copyCommand, downloadScript,
    renderPreview, describeCost, previewCost, boxVectors, periodicCell, pickResidue,
    sayRefusal,
  };
})();
