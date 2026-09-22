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
      blurb: "Measure the trajectory: structure, flexibility, interactions.",
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
        "measured as it stands.",
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

  /* Settings another control on this page already owns. The Measurements
   * panel is the analysis picker: sixteen analyses grouped by what they
   * answer, each with what it measures and why. Offering `include` and
   * `exclude` again in the options grid put two controls on one key -- and
   * the grid won, because the config it builds is merged over the panel's.
   * Somebody could choose four measurements, then set `include` to something
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
    const wrap = document.createElement("label");
    wrap.className = "builder-field";

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
      state.values[phase] = state.values[phase] || {};
      const empty = value === "" || value === null
        || (Array.isArray(value) && value.length === 0);
      if (empty) delete state.values[phase][field.name];
      else state.values[phase][field.name] = value;
      renderSettings();
      updateSummary();
    });
    wrap.appendChild(input);

    if (field.help) {
      const help = document.createElement("span");
      help.className = "builder-card-note";
      help.textContent = field.help;
      wrap.appendChild(help);
    }
    return wrap;
  }

  // --------------------------------------------------- which measurements

  function analysisSection() {
    const section = document.createElement("div");
    section.className = "run-section run-section-open";

    const head = document.createElement("div");
    head.className = "run-section-head run-section-head-static";
    head.innerHTML =
      '<span class="run-section-name">Measurements</span>' +
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
      body.innerHTML = `<div class="empty-detail muted">${
        (options && options.reason) || "No analyses available."
      }</div>`;
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
      text(el("run-summary"), doing.length ? doing.join(" → ") : "");
    }
    const can = ready();
    ["run-start-button", "run-download", "run-copy-command",
     "run-download-script"].forEach((id) => {
      const button = el(id);
      if (button) button.disabled = !can;
    });
  }

  async function fetchConfig() {
    const response = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentState()),
    });
    return response.json();
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
  function applyLoadedState(from, { from: origin, note } = {}) {
    if (!from) return;
    state.start = from.start;
    // `include_phase` is what the server sends since the phase lists took one
    // name; reading `include` alone left every phase unticked on load, and the
    // Agent's configs arrive through here too. The old name is still read.
    state.phases = new Set(from.include_phase || from.include || []);
    state.values = from.phases || {};
    state.analyses = new Set(from.analyses || []);
    state.analysisOptions = from.analysis_options || {};
    // How the study was written belongs to the run rather than a phase, so
    // it goes where the "This run" controls read from. Dropped before, so
    // an agent-written config opened here came back claiming a person
    // wrote it.
    if (from.study && Object.keys(from.study).length) {
      state.values[RUN_OPTIONS_KEY] = Object.assign(
        {}, state.values[RUN_OPTIONS_KEY] || {}, from.study
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
    ["run-open-config", "run-as-is"].forEach((id) => {
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
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentState()),
      });
      started = await response.json();
    } catch (error) {
      text(el("run-note"), "Could not reach the server.");
      button.disabled = false;
      return;
    }
    if (!started.ok) {
      text(el("run-note"), started.error || "Could not start.");
      button.disabled = false;
      return;
    }
    text(el("run-note"), `Running. Config written to ${started.config_path}`);
    if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
      window.FastMDXDashboard.navigate("overview");
    }
  }

  function resetEverything() {
    state.phases = state.start
      ? new Set(STARTING_POINTS[state.start].phases)
      : new Set();
    state.values = {};
    state.analyses.clear();
    state.analysisOptions = {};
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
  };
})();
