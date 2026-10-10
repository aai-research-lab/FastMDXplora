/* The Config Builder.
 *
 * One page for every study, in five steps in the order a study is decided:
 * what it starts from, the system, the protocol, the analysis and report,
 * and a review. Beside them, the study as it stands: what setup will build,
 * what was changed, the config in each of its languages, whether it will
 * run, and the way to run it.
 *
 * Nothing is built from a list written here. The settings, their groups, which
 * are shown first, their labels, units, limits and what each does when left
 * unset come from the schema payload (schema_payload.py), which is read
 * from the software itself, and the config, the command and the script come
 * back from the server, which builds them by the one derivation the command
 * line and the Agent use.
 *
 * A change to a setting changes that setting's field and the summary, and
 * nothing else on the page: the form used to build every control again after
 * each change, so Tab out of a field landed on the page itself.
 */

(function () {
  "use strict";

  const PHASES = [
    { name: "setup", label: "Setup", blurb: "Protonate, solvate, add ions, and assign a force field." },
    { name: "simulation", label: "Simulate", blurb: "Minimise, equilibrate, and run production dynamics." },
    { name: "analysis", label: "Analyze", blurb: "Analyse the trajectory: structure, flexibility, interactions." },
    { name: "report", label: "Report", blurb: "Collect the figures and numbers into a document." },
  ];

  /* What each starting point implies. Somebody with a protein wants the whole
   * thing; somebody with a trajectory has already done the expensive part. */
  const STARTING_POINTS = {
    structure: {
      label: "A structure",
      detail: "A PDB ID or a structure file. Prepared, simulated, analysed and reported.",
      phases: ["setup", "simulation", "analysis", "report"],
      // Sequence-to-structure is not implemented -- the setup phase refuses
      // it -- so it is not offered here.
      offers: ["setup", "simulation", "analysis", "report"],
    },
    trajectory: {
      label: "A trajectory",
      detail: "Frames you already have, from here or elsewhere. Analysed and reported.",
      phases: ["analysis", "report"],
      // Setup and simulation have nothing to act on: there is no supported
      // way to continue a run from a trajectory, and re-preparing the
      // structure would not connect to the frames already recorded.
      offers: ["analysis", "report"],
    },
    config: {
      label: "A config I have",
      detail: "Checked first, then run as it stands or opened here to change.",
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
    open: new Set(),       // groups shown whole, analyses' settings shown
    found: null,
    configVerdict: null,
    loadedFrom: null,
    sweep: [],             // [{ axis, values }] -- values as typed
    refused: null,         // a refusal said on its fields: { targets, why, fix, ... }
    systems: [],           // [{ id, system, own: { phase: {...} }, facts }]
    decisions: {},         // dotted setting -> { why, source, alternatives }
    analysisMode: "all",   // "all": every one that applies; "only": those ticked
    find: "",
    onlyChanged: false,
    built: null,           // the server's last answer for the config
    estimate: null,        // the server's last answer for the system
  };

  const el = (id) => document.getElementById(id);
  const text = (target, value) => { if (target) target.textContent = value; };

  function node(tag, className, content) {
    const made = document.createElement(tag);
    if (className) made.className = className;
    if (content !== undefined && content !== null) made.textContent = String(content);
    return made;
  }

  // ------------------------------------------------------------------ schema

  /* One request for the settings, however many ask before it is answered:
   * a config opened as the page loads and the saved draft wait on the same
   * answer, so neither can land after the other by chance. */
  let schemaAsked = null;
  function loadSchema() {
    if (state.schema) return Promise.resolve(state.schema);
    if (!schemaAsked) {
      schemaAsked = fetch("/api/schema").then((response) => response.json())
        .then((schema) => { state.schema = schema; return schema; })
        .catch((error) => { schemaAsked = null; throw error; });
    }
    return schemaAsked;
  }
  // Set once a config has been opened here: the draft is then older news.
  let openedAConfig = false;

  function fieldsFor(phase) {
    if (phase === RUN_OPTIONS_KEY) return (state.schema && state.schema.run_options) || [];
    if (phase === EXECUTION_KEY) return (state.schema && state.schema.execution_options) || [];
    const block = state.schema && state.schema.phases[phase];
    return block ? block.fields : [];
  }

  function fieldOf(phase, name) {
    return fieldsFor(phase).find((field) => field.name === name) || null;
  }

  /* Settings a run supplies for itself. Asking somebody to type the trajectory
   * path in the settings when they chose it two questions ago is the kind of
   * thing that makes a form feel like paperwork. */
  const SUPPLIED = new Set(["trajectory", "topology"]);

  /* Settings another control on this page already owns. The Analyses picker
   * owns `include` and `exclude`; per-analysis settings sit under each
   * analysis; how a study was written is a record the Review gives, not a
   * choice made in the form (a loaded study's value is carried through). */
  const OWNED_ELSEWHERE = { analysis: new Set(["include", "exclude"]) };
  const OWNED_BY_ROLE = new Set(["provenance", "per-analysis"]);

  function ownedElsewhere(phase, name) {
    if (OWNED_ELSEWHERE[phase] && OWNED_ELSEWHERE[phase].has(name)) return true;
    const field = fieldOf(phase, name);
    return Boolean(field && OWNED_BY_ROLE.has(field.role));
  }

  /* A setting by what it is to the form, from the payload's roles and
   * controls: the page names no setting of its own. */
  function byRole(phase, role) {
    return fieldsFor(phase).find((field) => field.role === role) || null;
  }

  function byControl(phase, control) {
    return fieldsFor(phase).find((field) => field.control === control) || null;
  }

  function nameOf(field) {
    return field ? field.name : null;
  }

  function settingsFor(phase) {
    return fieldsFor(phase).filter(
      (field) => !SUPPLIED.has(field.name) && !ownedElsewhere(phase, field.name)
    );
  }

  /* The same settings, in the groups the schema declares. Empty groups are
   * dropped, so a trajectory does not leave headings with nothing under them. */
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

  /* Settings that belong to the run rather than to a phase, and how the runs
   * are scheduled, under sentinel keys so one control builder makes them. */
  const RUN_OPTIONS_KEY = "__run__";
  const EXECUTION_KEY = "__execution__";
  const SWEEP_KEY = "__sweep__";
  const SEED_AXIS = "simulation.random_seed";
  const MODEL_AXIS = "setup.model";

  /* The dotted name a setting goes by in a config's `decisions`. */
  function dotted(phase, name) {
    if (phase === RUN_OPTIONS_KEY) return name;
    if (phase === EXECUTION_KEY) return "execution." + name;
    return phase + "." + name;
  }

  // ------------------------------------------------------------ what it holds

  function valueOf(phase, name) {
    return (state.values[phase] || {})[name];
  }

  function isEmpty(value) {
    return value === undefined || value === null || value === ""
      || (Array.isArray(value) && value.length === 0)
      || (typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length);
  }

  /* Whether a setting holds something other than what it does when left
   * alone. A value typed as its own default is not a change: the config
   * leaves it out. */
  function isChanged(phase, name) {
    const value = valueOf(phase, name);
    if (isEmpty(value)) return false;
    const field = fieldOf(phase, name);
    if (!field || field.default === null || field.default === undefined) return true;
    return !sameValue(value, field.default);
  }

  function sameValue(a, b) {
    if (typeof b === "number" && a !== "" && !isNaN(Number(a))) return Number(a) === b;
    if (typeof b === "boolean") return a === b || String(a) === String(b);
    if (Array.isArray(b) && Array.isArray(a)) return JSON.stringify(a) === JSON.stringify(b);
    return String(a) === String(b);
  }

  function said(value, unit) {
    if (value === null || value === undefined) return "";
    if (typeof value === "boolean") return value ? "on" : "off";
    if (Array.isArray(value)) return value.join(", ");
    if (typeof value === "object") return Object.keys(value).length + " entries";
    return String(value) + (unit ? " " + unit : "");
  }

  // ------------------------------------------------------- what have you got

  function renderStart() {
    const host = el("run-start");
    if (!host) return;
    if (!host.childElementCount) {
      Object.keys(STARTING_POINTS).forEach((key) => {
        const point = STARTING_POINTS[key];
        const card = node("label", "builder-start-card");
        card.dataset.start = key;
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "run-start";
        radio.value = key;
        radio.className = "builder-start-radio";
        radio.addEventListener("change", () => {
          if (!radio.checked) return;
          chooseStart(key);
        });
        card.append(radio, node("span", "builder-start-title", point.label),
                    node("span", "builder-start-what", point.detail));
        host.appendChild(card);
      });
    }
    host.querySelectorAll("input[name='run-start']").forEach((radio) => {
      radio.checked = radio.value === state.start;
      radio.closest(".builder-start-card").classList.toggle("is-chosen", radio.checked);
    });

    const started = Boolean(state.start);
    const fromConfig = state.start === "config";
    el("run-input-card").hidden = !started;
    el("run-start-more").hidden = !started || fromConfig;
    el("run-phases-card").hidden = !started || fromConfig;
    el("run-structure-field").hidden = state.start !== "structure";
    el("run-trajectory-field").hidden = state.start !== "trajectory";
    el("run-config-field").hidden = !fromConfig;
    el("run-output-field").hidden = fromConfig;
    ["system", "protocol", "analysis", "review"].forEach((step) => {
      const section = el("run-step-" + step);
      if (section) section.hidden = !started || fromConfig;
    });
    document.querySelectorAll("#run-steps .builder-step-link").forEach((link) => {
      link.hidden = link.dataset.step !== "start" && (!started || fromConfig);
    });
  }

  function chooseStart(key) {
    state.start = key || null;
    const choice = STARTING_POINTS[state.start] || { phases: [] };
    state.phases = new Set(choice.phases);
    if (state.start === "structure" && !state.systems.length) {
      state.systems = [{ id: "", system: "", own: {}, facts: null }];
    }
    renderAll();
    saveDraft();
  }

  /* For the tests and anything that drives the form by value: the
   * starting point as a select used to be set. */
  function setStart(key) {
    chooseStart(key);
  }

  // ------------------------------------------------------------- the systems

  function renderSystems() {
    const host = el("run-systems");
    if (!host) return;
    host.replaceChildren();
    if (state.start !== "structure") return;
    if (!state.systems.length) state.systems = [{ id: "", system: "", own: {}, facts: null }];
    const head = node("div", "builder-system-row builder-system-head");
    head.setAttribute("role", "row");
    ["Name", "Structure", "What it holds", ""].forEach((said) => {
      const cell = node("span", null, said);
      cell.setAttribute("role", "columnheader");
      head.appendChild(cell);
    });
    host.appendChild(head);
    state.systems.forEach((row, index) => host.appendChild(systemRow(row, index)));
    if (window.FastMDXPicker && window.FastMDXPicker.attachAll) window.FastMDXPicker.attachAll();
  }

  function systemRow(row, index) {
    const line = node("div", "builder-system-row");
    line.setAttribute("role", "row");
    line.dataset.index = String(index);

    const name = document.createElement("input");
    name.type = "text";
    name.className = "mono builder-system-id";
    name.placeholder = "s" + (index + 1);
    name.value = row.id || "";
    name.setAttribute("aria-label", "Name of system " + (index + 1));
    name.addEventListener("input", () => { row.id = name.value.trim(); afterChange(); });

    const structure = document.createElement("input");
    structure.type = "text";
    structure.className = "mono builder-system-structure";
    structure.autocomplete = "off";
    // The first row is the form's structure field, by the id it has
    // always had: scripts and tests that fill `#run-system` still do.
    structure.id = index === 0 ? "run-system" : "run-system-" + (index + 1);
    structure.dataset.picks = "structure";
    structure.placeholder = "PDB ID, or a path to a structure file";
    structure.value = row.system || "";
    structure.setAttribute("aria-label", "Structure of system " + (index + 1));
    structure.addEventListener("input", () => {
      row.system = structure.value.trim();
      row.facts = null;
      describeOutput();
      afterChange();
      scheduleFacts(row, line);
    });

    const holds = node("span", "builder-system-holds muted small");
    holds.textContent = factsSaid(row);

    const remove = window.FastMDXIcons.button("close", "Remove system " + (index + 1), "builder-icon-btn");
    remove.disabled = state.systems.length < 2;
    remove.addEventListener("click", () => {
      state.systems.splice(index, 1);
      renderSystems();
      afterChange();
    });

    const cells = [name, structure, holds, remove];
    cells.forEach((cell) => line.appendChild(cell));

    // Settings for this system alone, said and removable. Added from a
    // config that set them; their values are written as the study's are.
    const own = row.own || {};
    const owned = [];
    Object.keys(own).forEach((phase) => {
      Object.keys(own[phase] || {}).forEach((key) => owned.push([phase, key, own[phase][key]]));
    });
    if (owned.length) {
      const list = node("div", "builder-system-own");
      list.appendChild(node("span", "muted small", "For this system only:"));
      owned.forEach(([phase, key, value]) => {
        const chip = node("span", "builder-chip");
        chip.appendChild(node("span", "mono", `${phase}.${key}: ${said(value)}`));
        const drop = window.FastMDXIcons.button("close", `Remove ${phase}.${key} for this system`, "builder-icon-btn");
        drop.addEventListener("click", () => {
          delete own[phase][key];
          if (!Object.keys(own[phase]).length) delete own[phase];
          renderSystems();
          afterChange();
        });
        chip.appendChild(drop);
        list.appendChild(chip);
      });
      line.appendChild(list);
    }
    if (!row.facts && row.system) scheduleFacts(row, line);
    return line;
  }

  function factsSaid(row) {
    const facts = row.facts;
    if (!row.system) return "";
    if (!facts) return "Reading it…";
    if (!facts.ok) return facts.reason || "Could not be read.";
    const parts = [`${facts.residues} residues`,
      (facts.chains.length === 1 ? "chain " : "chains ") + facts.chains.join(", ")];
    if (facts.models > 1) parts.push(`${facts.models} models`);
    parts.push(facts.ligands.length ? "ligand " + facts.ligands.join(", ") : "no ligand");
    return parts.join(" · ");
  }

  const factsTimers = new WeakMap();
  function scheduleFacts(row, line) {
    clearTimeout(factsTimers.get(row));
    if (!row.system) return;
    factsTimers.set(row, setTimeout(async () => {
      const asked = row.system;
      let facts;
      try {
        const response = await fetch("/api/structure-facts", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ system: asked }),
        });
        facts = await response.json();
      } catch (error) {
        facts = { ok: false, reason: "Could not be reached." };
      }
      if (row.system !== asked) return;
      row.facts = facts;
      const holds = line.querySelector(".builder-system-holds");
      text(holds, factsSaid(row));
      if (state.systems[0] === row) renderSteps();
    }, 500));
  }

  /* The facts of the first system, which the form's choices are about: its
   * chains, its models, its ligands. From the estimate when there is one,
   * from the row's own reading otherwise. */
  function structureFacts() {
    const fromEstimate = state.estimate && state.estimate.ok && state.estimate.structure;
    if (fromEstimate && fromEstimate.ok) return fromEstimate;
    const row = state.systems[0];
    return row && row.facts && row.facts.ok ? row.facts : null;
  }

  // ------------------------------------------------ what should happen to it

  function renderPhases() {
    const host = el("run-phases");
    if (!host) return;
    host.replaceChildren();
    const offered = STARTING_POINTS[state.start] ? STARTING_POINTS[state.start].offers : [];
    PHASES.forEach((phase) => {
      const row = node("label", "run-phase builder-chip-check");
      row.dataset.phase = phase.name;
      const available = offered.includes(phase.name);
      row.dataset.available = String(available);
      row.dataset.chosen = String(state.phases.has(phase.name));
      row.title = available ? phase.blurb : "Nothing to act on: a trajectory is already the result of this.";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = available && state.phases.has(phase.name);
      box.disabled = !available;
      box.addEventListener("change", () => {
        if (box.checked) state.phases.add(phase.name);
        else state.phases.delete(phase.name);
        renderAll();
        saveDraft();
      });
      row.append(box, node("span", "run-phase-label", phase.label));
      host.appendChild(row);
    });
    text(el("run-phases-said"), state.start === "trajectory"
      ? "A trajectory is analysed as it stands: setup and simulation have nothing to act on."
      : "Leave out a phase to run the others.");
  }

  // ------------------------------------------------------------- the steps

  function renderSteps() {
    if (!state.schema || !state.start || state.start === "config") return;
    const focus = focusedSetting();
    renderSystemStep();
    renderProtocolStep();
    renderAnalysisStep();
    renderReviewStep();
    if (window.FastMDXPicker && window.FastMDXPicker.attachAll) window.FastMDXPicker.attachAll();
    applyFind();
    restoreFocus(focus);
  }
  /* The name the rest of the page used for building the settings again. */
  const renderSettings = renderSteps;

  function stepNote(host, said) {
    host.replaceChildren(node("p", "builder-step-empty muted", said));
  }

  function renderSystemStep() {
    const host = el("run-system-body");
    if (!host) return;
    host.replaceChildren();
    if (!state.phases.has("setup")) {
      stepNote(host, state.start === "trajectory"
        ? "The trajectory's own system is analysed: nothing is prepared."
        : "Setup is not part of this study, so no system is prepared here.");
      return;
    }
    const facts = structureFacts();
    groupsFor("setup").forEach((group) => {
      const fields = group.fields.filter((field) => {
        // A structure of one model has no model to choose.
        if (field.role === "ensemble-member" && facts && facts.models < 2
            && !isChanged("setup", field.name)) return false;
        return true;
      });
      if (fields.length) host.appendChild(settingsGroup("setup", group.title, group.why, fields));
    });
  }

  function renderProtocolStep() {
    const host = el("run-protocol-body");
    if (!host) return;
    host.replaceChildren();
    if (!state.phases.has("simulation")) {
      stepNote(host, "Simulation is not part of this study.");
      host.appendChild(sweepGroup());
      return;
    }
    host.appendChild(lengthGroup());
    groupsFor("simulation").forEach((group) => {
      if (group.title === "Enhanced sampling") {
        host.appendChild(samplingGroup(group));
        return;
      }
      // The length and its equilibration are one question, asked first.
      if (group.fields.some((field) => field.role === "production-length")) return;
      host.appendChild(settingsGroup("simulation", group.title, group.why, group.fields));
    });
  }

  function renderAnalysisStep() {
    const host = el("run-analysis-body");
    if (!host) return;
    host.replaceChildren();
    if (!state.phases.has("analysis") && !state.phases.has("report")) {
      stepNote(host, "Neither analysis nor a report is part of this study.");
      return;
    }
    if (state.phases.has("analysis")) {
      host.appendChild(analysisSection());
      groupsFor("analysis").forEach((group) => {
        host.appendChild(settingsGroup("analysis", group.title, group.why, group.fields));
      });
    }
    if (state.phases.has("report")) {
      groupsFor("report").forEach((group) => {
        host.appendChild(settingsGroup("report", group.title, group.why, group.fields));
      });
    }
  }

  // ------------------------------------------------------- a group of settings

  /* A group shows its essential settings and those changed or refused; the
   * rest wait behind "Show N more". Every field is built, so finding one
   * or changing one never needs the group built again. */
  function settingsGroup(phase, title, why, fields, options = {}) {
    const key = `${phase}:${title || ""}`;
    const box = node("section", "builder-group");
    box.dataset.group = key;
    if (state.open.has(key)) box.classList.add("is-open");
    const head = node("div", "builder-group-head");
    if (title) head.appendChild(node("h3", null, title));
    if (why) head.appendChild(withCode(node("p"), why));
    box.appendChild(head);
    const grid = node("div", "builder-grid");
    let more = 0;
    fields.forEach((field) => {
      const wrap = control(phase, field);
      if (!field.essential && !options.allEssential) {
        wrap.classList.add("is-more");
        more += 1;
      }
      grid.appendChild(wrap);
    });
    box.appendChild(grid);
    if (more) {
      const allMore = more === fields.length;
      const toggle = node("button", "builder-more");
      toggle.type = "button";
      const label = () => box.classList.contains("is-open")
        ? "Show fewer"
        : allMore ? `Show ${more} setting${more === 1 ? "" : "s"}, each at its default unless changed`
          : `Show ${more} more`;
      toggle.textContent = label();
      toggle.setAttribute("aria-expanded", String(box.classList.contains("is-open")));
      toggle.addEventListener("click", () => {
        const open = !box.classList.contains("is-open");
        box.classList.toggle("is-open", open);
        if (open) state.open.add(key); else state.open.delete(key);
        toggle.textContent = label();
        toggle.setAttribute("aria-expanded", String(open));
      });
      box.appendChild(toggle);
      if (allMore) box.classList.add("is-folded");
    }
    return box;
  }

  // ------------------------------------------------------------ one setting

  /* Composite controls build themselves again from their own state after a
   * change; every other control is left as it is. */
  const REDRAWN = new Set(["stopping", "residues"]);

  function control(phase, field) {
    const wide = ["stopping", "residues", "mapping", "script", "multiselect"].includes(field.control);
    const wrap = node("div", "builder-field" + (wide ? " builder-field-wide" : ""));
    // Named, so what is said about a setting elsewhere on the page can
    // point at it.
    wrap.dataset.setting = field.name;
    wrap.dataset.phase = phase;
    wrap.dataset.find = [field.name, field.label || "", field.summary || "", phase].join(" ").toLowerCase();
    const id = `set-${phase.replace(/_/g, "")}-${field.name}`;

    const head = node("div", "builder-head");
    const label = node(wide ? "span" : "label", "builder-label", field.label || field.name.replace(/_/g, " "));
    if (!wide) label.htmlFor = id;
    head.appendChild(label);
    if (field.unit) head.appendChild(node("span", "builder-unit", field.unit));
    head.appendChild(node("span", "builder-dot"));
    const revert = window.FastMDXIcons.button("undo", `${field.label || field.name} back to the default`, "builder-revert");
    revert.addEventListener("click", () => revertSetting(phase, field.name));
    head.appendChild(revert);
    if (field.default !== null && field.default !== undefined && field.control !== "stopping") {
      // Your default (the workspace's fastmdx-defaults.yml) is said as yours.
      head.appendChild(node("span", "builder-default",
        (field.default_from ? "Your default " : "Default ") + said(field.default, field.unit)));
    }
    wrap.appendChild(head);

    const input = inputFor(phase, field, id);
    const current = valueOf(phase, field.name);
    if (current !== undefined) {
      if (input.writeValue) input.writeValue(current);
      else if (input.type === "checkbox") input.checked = Boolean(current);
      else input.value = current;
    }
    input.addEventListener("change", () => {
      const value = input.readValue
        ? input.readValue()
        : input.type === "checkbox" ? input.checked : input.value;
      setValue(phase, field.name, value);
      // A composite control asks to be built again only where what it shows
      // has changed shape: a number typed into it is left as it is, so the
      // control clicked next is still there to take the click.
      if (REDRAWN.has(field.control) && input.redraw !== false) redrawField(phase, field.name);
      else markField(phase, field.name);
      input.redraw = undefined;
      afterChange();
    });
    if (field.control === "number") {
      input.addEventListener("input", () => checkLimits(wrap, field, input.value));
    }
    wrap.appendChild(input);

    const limit = node("span", "builder-limit");
    limit.setAttribute("role", "alert");
    limit.hidden = true;
    wrap.appendChild(limit);

    if (isRefused(phase, field.name)) wrap.appendChild(refusalNote(phase, field));

    const help = helpFor(field);
    if (help) wrap.appendChild(help);
    wrap.appendChild(decisionFor(phase, field));
    markField(phase, field.name, wrap);
    return wrap;
  }

  function inputFor(phase, field, id) {
    let input;
    if (field.control === "multiselect") {
      // Checkboxes rather than a `<select multiple>`: every option visible at
      // once, and no modifier key needed to pick a second one.
      input = node("div", "builder-multiselect");
      (field.choices || []).forEach((choice) => {
        const label = node("label", "chip-toggle");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = choice;
        label.append(box, document.createTextNode(choice));
        input.appendChild(label);
      });
      input.readValue = () =>
        Array.from(input.querySelectorAll("input:checked")).map((box) => box.value);
      input.writeValue = (value) => {
        const chosen = new Set(Array.isArray(value) ? value : []);
        input.querySelectorAll("input").forEach((box) => { box.checked = chosen.has(box.value); });
      };
    } else if (field.choices && field.choices.length) {
      input = document.createElement("select");
      input.id = id;
      // A list of choices with no default leaves the setting alone first:
      // shown as its first choice, a protein in water read as embedded in
      // POPC, and choosing it back wrote `membrane: POPC`.
      if (field.default === null || field.default === undefined) {
        const unset = node("option", null, "Not set: " + (field.unset || "as the run decides"));
        unset.value = "";
        input.appendChild(unset);
      }
      field.choices.forEach((choice) => {
        const option = node("option", null, choice);
        option.value = choice;
        if (choice === field.default) {
          option.selected = true;
          option.textContent = choice + " (default)";
        }
        input.appendChild(option);
      });
    } else if (field.control === "tristate") {
      // Unset, on or off. Unset sends nothing, so the run decides as it
      // would had the setting never been shown.
      input = document.createElement("select");
      input.id = id;
      [["", "Not set: " + (field.unset || "as the force field was developed")],
       ["true", "On"], ["false", "Off"]].forEach(([value, label]) => {
        const option = node("option", null, label);
        option.value = value;
        input.appendChild(option);
      });
      input.readValue = () => (input.value === "" ? "" : input.value === "true");
      input.writeValue = (value) => {
        input.value = value === true ? "true" : value === false ? "false" : "";
      };
    } else if (field.control === "checkbox") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.id = id;
      input.checked = Boolean(field.default);
    } else if (field.control === "script") {
      input = scriptControl(field);
    } else if (field.control === "stopping") {
      input = stoppingControl(field);
    } else if (field.control === "residues") {
      input = residueStatesControl(field);
    } else if (field.control === "mapping") {
      input = mappingControl(field);
    } else {
      input = document.createElement("input");
      input.id = id;
      input.type = field.control === "number" ? "number" : "text";
      if (field.control === "number") {
        input.step = "any";
        if (field.minimum !== null && field.minimum !== undefined) input.min = String(field.minimum);
        if (field.maximum !== null && field.maximum !== undefined) input.max = String(field.maximum);
      }
      // The default is shown rather than filled in, so the config records
      // what was decided; where there is none, what an empty box does, or
      // an example marked as one.
      if (field.default !== null && field.default !== undefined) {
        input.placeholder = String(field.default);
      } else if (field.unset) {
        input.placeholder = "Not set: " + field.unset;
      } else if (field.example !== null && field.example !== undefined) {
        input.placeholder = "e.g. " + (Array.isArray(field.example)
          ? field.example.join(", ") : String(field.example));
      }
    }
    return input;
  }

  /* Its first sentence, and the rest behind More. */
  function helpFor(field) {
    const whole = String(field.help || "").trim();
    if (!whole) return null;
    const short = String(field.summary || whole).trim();
    const help = node("span", "builder-help");
    withCode(help, short);
    const rest = whole.slice(short.length).trim();
    if (rest) {
      const more = node("button", "builder-help-more", "More");
      more.type = "button";
      more.setAttribute("aria-expanded", "false");
      const body = withCode(node("span", "builder-help-rest"), rest);
      body.hidden = true;
      more.addEventListener("click", () => {
        body.hidden = !body.hidden;
        more.textContent = body.hidden ? "More" : "Less";
        more.setAttribute("aria-expanded", String(!body.hidden));
      });
      help.append(" ", more, body);
    }
    return help;
  }

  function checkLimits(wrap, field, raw) {
    const message = wrap.querySelector(".builder-limit");
    if (!message) return true;
    const value = String(raw || "").trim();
    let problem = "";
    if (value !== "") {
      const number = Number(value);
      const low = field.minimum, high = field.maximum;
      const unit = field.unit ? " " + field.unit : "";
      if (isNaN(number)) problem = `${field.label} is a number.`;
      else if (low !== null && low !== undefined && high !== null && high !== undefined
               && (number < low || number > high)) problem = `${field.label} runs from ${low} to ${high}${unit}.`;
      else if (low !== null && low !== undefined && number < low) problem = `${field.label} is at least ${low}${unit}.`;
      else if (high !== null && high !== undefined && number > high) problem = `${field.label} is at most ${high}${unit}.`;
    }
    message.textContent = problem;
    message.hidden = !problem;
    const was = wrap.classList.contains("is-invalid");
    wrap.classList.toggle("is-invalid", Boolean(problem));
    // The Run button and the summary follow at once, not at the next change.
    if (was !== Boolean(problem)) updateSummary();
    return !problem;
  }

  function setValue(phase, name, value) {
    if (isRefused(phase, name)) clearRefusal();
    state.values[phase] = state.values[phase] || {};
    const empty = value === "" || value === null || value === undefined
      || (Array.isArray(value) && value.length === 0);
    if (empty) delete state.values[phase][name];
    else state.values[phase][name] = value;
    // A reason given for a value no longer there explains nothing.
    if (!isChanged(phase, name)) delete state.decisions[dotted(phase, name)];
  }

  function wrapOf(phase, name) {
    return Array.from(document.querySelectorAll(
      `#run-settings .builder-field[data-setting="${CSS.escape(name)}"]`))
      .find((wrap) => wrap.dataset.phase === phase) || null;
  }

  /* A field says whether it is changed, refused, and why, without anything
   * around it being built again. */
  function markField(phase, name, given) {
    const wrap = given || wrapOf(phase, name);
    if (!wrap) return;
    const changed = isChanged(phase, name);
    wrap.classList.toggle("is-changed", changed);
    // Built again only where what it shows has changed: replaced under a
    // pointer on its way to press it, the press was lost.
    const decision = wrap.querySelector(".builder-decision");
    if (decision && decision.dataset.shows !== decisionShows(phase, name)) {
      decision.replaceWith(decisionFor(phase, fieldOf(phase, name) || { name }));
    }
  }

  function redrawField(phase, name) {
    const wrap = wrapOf(phase, name);
    const field = fieldOf(phase, name);
    if (!wrap || !field) return;
    const focus = focusedSetting();
    const fresh = control(phase, field);
    fresh.className = wrap.className;
    fresh.classList.toggle("is-changed", isChanged(phase, name));
    wrap.replaceWith(fresh);
    restoreFocus(focus);
  }

  /* Where the keyboard is, by setting and place within it, so building
   * that cannot be avoided puts it back. */
  function focusedSetting() {
    const active = document.activeElement;
    const wrap = active && active.closest && active.closest("#run-settings .builder-field");
    if (!wrap) return null;
    const focusable = Array.from(wrap.querySelectorAll("input, select, textarea, button"));
    return { name: wrap.dataset.setting, phase: wrap.dataset.phase, index: focusable.indexOf(active) };
  }

  function restoreFocus(where) {
    if (!where) return;
    const wrap = wrapOf(where.phase, where.name);
    if (!wrap) return;
    const focusable = Array.from(wrap.querySelectorAll("input, select, textarea, button"));
    const target = focusable[Math.max(0, Math.min(where.index, focusable.length - 1))];
    if (target) target.focus({ preventScroll: true });
  }

  function revertSetting(phase, name) {
    if (state.values[phase]) delete state.values[phase][name];
    delete state.decisions[dotted(phase, name)];
    const kind = fieldOf(phase, name);
    if (kind && kind.control === "stopping") stoppingDraft = null;
    if (kind && kind.control === "residues") residuesPending = [];
    if (isRefused(phase, name)) clearRefusal();
    const field = fieldOf(phase, name);
    if (field && (field.control === "stopping" || field.role === "biased-sampling"
                  || field.role === "production-length")) {
      renderSteps();
    } else {
      redrawField(phase, name);
    }
    afterChange();
  }

  // ---------------------------------------------- why a setting has its value

  /* A changed setting can carry its reason: `decisions` in the config, in
   * the record and in the report beside the methods. Given here by a
   * person; the Agent writes its own, and they are shown the same way. */
  /* What a field's reason line shows, as a key: nothing, the question, or
   * the reason given. */
  function decisionShows(phase, name) {
    const decision = state.decisions[dotted(phase, name)];
    if (state.editing && state.editing.name === dotted(phase, name)) return "editing";
    if (decision && decision.why) return "why:" + JSON.stringify(decision);
    return isChanged(phase, name) ? "ask" : "none";
  }

  function decisionFor(phase, field) {
    const name = dotted(phase, field.name);
    // A reason being written survives the field being built again.
    if (state.editing && state.editing.name === name) return decisionEditor(phase, field);
    const box = node("div", "builder-decision");
    box.dataset.shows = decisionShows(phase, field.name);
    const decision = state.decisions[name];
    if (!isChanged(phase, field.name) && !decision) {
      box.hidden = true;
      return box;
    }
    if (decision && decision.why && !box.dataset.editing) {
      const said = node("div", "builder-why");
      said.appendChild(node("span", "builder-why-k", "Why"));
      said.appendChild(document.createTextNode(" " + decision.why));
      const from = [];
      if (decision.source) from.push(decision.source === "person" ? "you" : decision.source);
      if (decision.alternatives && decision.alternatives.length) {
        from.push("set aside: " + decision.alternatives.join(", "));
      }
      if (from.length) said.appendChild(node("span", "builder-why-from", from.join(" · ")));
      const edit = window.FastMDXIcons.button("edit", "Edit the reason", "builder-why-edit");
      edit.addEventListener("click", () => box.replaceWith(decisionEditor(phase, field)));
      said.appendChild(edit);
      box.appendChild(said);
      return box;
    }
    const ask = node("button", "builder-linkish builder-why-add", "Why this value?");
    ask.type = "button";
    ask.title = "Record the reason in the config, the study's record and its report";
    ask.addEventListener("click", () => {
      const editor = decisionEditor(phase, field);
      box.replaceWith(editor);
      const area = editor.querySelector("textarea");
      if (area) area.focus();
    });
    box.appendChild(ask);
    return box;
  }

  function decisionEditor(phase, field) {
    const name = dotted(phase, field.name);
    const decision = state.decisions[name] || {};
    const editing = state.editing && state.editing.name === name ? state.editing
      : { name, why: decision.why || "", alternatives: (decision.alternatives || []).join(", ") };
    state.editing = editing;
    const box = node("div", "builder-decision builder-decision-edit");
    box.dataset.shows = "editing";
    const why = document.createElement("textarea");
    why.rows = 2;
    why.placeholder = "Why this value, in a sentence";
    why.setAttribute("aria-label", `Why ${field.label || field.name} has this value`);
    why.value = editing.why;
    why.addEventListener("input", () => { editing.why = why.value; });
    const alternatives = document.createElement("input");
    alternatives.type = "text";
    alternatives.placeholder = "Values set aside, if any, e.g. 7.4";
    alternatives.setAttribute("aria-label", "Values set aside");
    alternatives.value = editing.alternatives;
    alternatives.addEventListener("input", () => { editing.alternatives = alternatives.value; });
    const save = node("button", "ghost-btn", "Save the reason");
    save.type = "button";
    const drop = window.FastMDXIcons.button("bin", "Remove the reason", "builder-why-drop");
    const done = () => {
      state.editing = null;
      box.replaceWith(decisionFor(phase, field));
      afterChange();
    };
    save.addEventListener("click", () => {
      const reason = why.value.trim();
      if (!reason) { delete state.decisions[name]; done(); return; }
      const others = alternatives.value.split(",").map((part) => part.trim()).filter(Boolean);
      // A reason the person writes is theirs, whoever gave the one before.
      const source = (reason === decision.why && decision.source) || "person";
      state.decisions[name] = Object.assign({ why: reason, source },
        others.length ? { alternatives: others } : {});
      done();
    });
    drop.addEventListener("click", () => { delete state.decisions[name]; done(); });
    why.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) save.click();
      if (event.key === "Escape") done();
    });
    const row = node("div", "builder-actions-row");
    row.append(save, drop);
    box.append(why, alternatives, row);
    return box;
  }

  // ------------------------------------------------- a block of several settings

  /* Umbrella, steered and metadynamics are blocks of several settings. Each
   * of the settings its example names is a field of its own; the block is
   * written as YAML, which the server reads as a config file is read, and
   * "Edit as YAML" gives the whole block for anything the fields do not. */
  function mappingControl(field) {
    const box = node("div", "builder-mapping-form");
    const keys = field.example && typeof field.example === "object" ? Object.keys(field.example) : [];
    const rows = node("div", "builder-mapping-rows");
    const inputs = {};
    keys.forEach((key) => {
      const line = node("label", "builder-mapping-row");
      line.appendChild(node("span", "mono", key));
      const input = document.createElement("input");
      input.type = "text";
      input.className = "mono";
      input.placeholder = "e.g. " + String(field.example[key]);
      input.addEventListener("change", (event) => { event.stopPropagation(); commit(); });
      inputs[key] = input;
      line.appendChild(input);
      rows.appendChild(line);
    });
    const area = document.createElement("textarea");
    area.rows = 6;
    area.spellcheck = false;
    area.className = "builder-mapping mono";
    area.placeholder = keys.map((key) => `${key}: ${field.example[key]}`).join("\n")
      || "one setting per line, as in a config file";
    area.hidden = Boolean(keys.length);
    area.addEventListener("change", (event) => { event.stopPropagation(); commit(); });
    const toggle = node("button", "builder-linkish", keys.length ? "Edit as YAML" : "");
    toggle.type = "button";
    toggle.hidden = !keys.length;
    toggle.addEventListener("click", () => {
      const yamlMode = area.hidden;
      if (yamlMode) area.value = composed();
      area.hidden = !yamlMode;
      rows.hidden = yamlMode;
      toggle.textContent = yamlMode ? "Edit as fields" : "Edit as YAML";
    });
    const composed = () => keys.filter((key) => inputs[key].value.trim())
      .map((key) => `${key}: ${inputs[key].value.trim()}`).join("\n");
    const commit = () => box.dispatchEvent(new Event("change", { bubbles: false }));
    box.append(rows, area, toggle);
    box.readValue = () => (area.hidden ? composed() : area.value.trim());
    box.writeValue = (value) => {
      if (value && typeof value === "object") {
        const unknown = Object.keys(value).filter((key) => !(key in inputs));
        if (unknown.length || !keys.length) {
          area.value = Object.keys(value).map((key) => `${key}: ${yamlScalar(value[key])}`).join("\n");
          area.hidden = false;
          rows.hidden = true;
          toggle.textContent = "Edit as fields";
          return;
        }
        Object.keys(value).forEach((key) => { inputs[key].value = yamlScalar(value[key]); });
      } else if (typeof value === "string") {
        area.value = value;
        area.hidden = false;
        rows.hidden = true;
        toggle.textContent = "Edit as fields";
      }
    };
    return box;
  }

  function yamlScalar(value) {
    if (Array.isArray(value) || (value && typeof value === "object")) return JSON.stringify(value);
    return String(value);
  }

  /* One script with an on-switch. The engine reads the config's one
   * `script` string either way -- a single line naming a file that exists is
   * read from disk, anything else is the script itself -- so the page offers
   * both: a path, with the same Browse control the trajectory and structure
   * fields use, or the text written here. */
  function scriptControl(field) {
    const input = node("div", "builder-script");
    const onRow = node("label", "chip-toggle");
    const on = document.createElement("input");
    on.type = "checkbox";
    onRow.append(on, document.createTextNode("Enabled"));
    input.appendChild(onRow);

    const path = document.createElement("input");
    path.type = "text";
    // The picker's kind for a script field is the field's own name -- the
    // KINDS table on the server is keyed to agree.
    path.id = "builder-" + field.name + "-path";
    path.dataset.picks = field.name;
    path.placeholder = "path to a PLUMED .dat file on this machine";
    input.appendChild(path);

    const script = document.createElement("textarea");
    script.rows = 8;
    script.spellcheck = false;
    script.className = "builder-mapping mono";
    script.placeholder = "or the PLUMED input written here,\nexactly as it would appear in the .dat file";
    input.appendChild(script);

    const note = node("span", "builder-help");
    input.appendChild(note);

    const tell = () => {
      // Both slots feed the one `script` key: text written here wins,
      // because somebody who loaded a file and then edited the text meant
      // the edits.
      note.textContent = script.value.trim() && path.value.trim()
        ? "The script written here is what the config carries; clear it to use the file path instead."
        : "";
    };
    let hadContent = false;
    const settle = () => {
      const has = Boolean(script.value.trim() || path.value.trim());
      // The first content to arrive turns the switch on, only on the
      // empty-to-filled step, so a box deliberately unticked stays so.
      if (has && !hadContent && !on.checked) on.checked = true;
      hadContent = has;
      tell();
    };
    path.addEventListener("change", settle);
    script.addEventListener("change", settle);
    input.readValue = () => {
      const text = script.value;
      const file = path.value.trim();
      // A ticked box over two empty slots writes nothing: an on-switch with
      // no script is not a study anybody described.
      if (!text.trim() && !file) return null;
      return { enabled: on.checked, script: text.trim() ? text : file };
    };
    input.writeValue = (value) => {
      if (!value || typeof value !== "object") return;
      on.checked = Boolean(value.enabled);
      const carried = typeof value.script === "string" ? value.script : "";
      // Restored with the reading the engine gives the config: a newline
      // means the script itself, one line means a path.
      if (carried.includes("\n")) { script.value = carried; path.value = ""; }
      else { path.value = carried; script.value = ""; }
      hadContent = Boolean(carried.trim());
      tell();
    };
    return input;
  }

  // ---------------------------------------------------- how long, how many

  let stashedStop = null;

  function lengthGroup() {
    const box = node("section", "builder-group");
    box.dataset.group = "simulation:length";
    const head = node("div", "builder-group-head");
    head.append(node("h3", null, "Length and replicas"),
      node("p", null, "A fixed length, or until each quantity you name is determined to the error you ask."));
    box.appendChild(head);

    const durationField = byRole("simulation", "production-length");
    const stopField = byControl("simulation", "stopping");
    const stopName = nameOf(stopField);
    const until = (stopName && !isEmpty(valueOf("simulation", stopName))) || state.lengthMode === "until";
    const mode = segmented("run-length-mode", [["fixed", "A fixed length"], ["until", "Until it is determined"]],
      until ? "until" : "fixed", "How long it runs", (chosen) => {
        if (chosen === "until") {
          state.lengthMode = "until";
          if (stashedStop && stopName) {
            state.values.simulation = state.values.simulation || {};
            state.values.simulation[stopName] = stashedStop;
            bringDecisionBack(dotted("simulation", stopName));
          }
        } else {
          state.lengthMode = "fixed";
          const current = stopName ? valueOf("simulation", stopName) : null;
          if (!isEmpty(current)) stashedStop = current;
          if (state.values.simulation && stopName) delete state.values.simulation[stopName];
          if (stopName) setDecisionAside(dotted("simulation", stopName));
          stoppingDraft = null;
        }
        renderSteps();
        afterChange();
      });
    const lengthField = node("div", "builder-field builder-field-wide builder-length");
    lengthField.dataset.setting = "length";
    lengthField.dataset.phase = "simulation";
    lengthField.dataset.find = "length how long production duration until determined stop";
    lengthField.append(labelled("How long it runs"), mode);
    const grid = node("div", "builder-grid");
    grid.appendChild(lengthField);
    if (!until && durationField) grid.appendChild(control("simulation", durationField));
    if (until && stopField) grid.appendChild(control("simulation", stopField));
    grid.appendChild(replicasField());
    // The rest of the schema's group with the length in it: equilibration,
    // the ensemble, the step counts, behind "Show more" but the essential.
    const lengths = (state.schema.phases.simulation.groups || [])
      .find((group) => group.fields.some((field) => field.role === "production-length"));
    let more = 0;
    (lengths ? lengths.fields : []).filter((field) =>
      field.role !== "production-length" && field.control !== "stopping"
      && !ownedElsewhere("simulation", field.name))
      .forEach((field) => {
        const wrap = control("simulation", field);
        if (!field.essential) { wrap.classList.add("is-more"); more += 1; }
        grid.appendChild(wrap);
      });
    box.appendChild(grid);
    if (more) {
      const key = "simulation:length";
      if (state.open.has(key)) box.classList.add("is-open");
      const toggle = node("button", "builder-more");
      toggle.type = "button";
      const label = () => (box.classList.contains("is-open") ? "Show fewer"
        : `Show ${more} more: equilibration and step counts`);
      toggle.textContent = label();
      toggle.addEventListener("click", () => {
        const open = !box.classList.contains("is-open");
        box.classList.toggle("is-open", open);
        if (open) state.open.add(key); else state.open.delete(key);
        toggle.textContent = label();
      });
      box.appendChild(toggle);
    }
    box.appendChild(sweepGroup(true));
    return box;
  }

  function labelled(said) {
    const head = node("div", "builder-head");
    head.appendChild(node("span", "builder-label", said));
    return head;
  }

  function segmented(id, options, chosen, label, onChoose) {
    const group = node("div", "builder-seg");
    group.id = id;
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", label);
    options.forEach(([value, said]) => {
      const button = node("button", "builder-seg-btn", said);
      button.type = "button";
      button.dataset.value = value;
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", String(value === chosen));
      button.addEventListener("click", () => {
        if (value === chosen) return;
        onChoose(value);
      });
      group.appendChild(button);
    });
    return group;
  }

  /* Replicas are runs that differ only by their seed, written as a sweep of
   * `simulation.random_seed`; or runs from different models of an NMR
   * ensemble, a sweep of `setup.model`. */
  function replicasField() {
    const wrap = node("div", "builder-field builder-field-wide builder-replicas");
    wrap.dataset.setting = "replicas";
    wrap.dataset.phase = "simulation";
    wrap.dataset.find = "replicas seeds random_seed independent runs models ensemble";
    wrap.appendChild(labelled("Replicas"));
    const seedRow = state.sweep.find((row) => row.axis === SEED_AXIS);
    const modelRow = state.sweep.find((row) => row.axis === MODEL_AXIS);
    const count = seedRow ? sweepRunsOf(seedRow) : 1;
    const plain = (n) => Array.from({ length: n }, (_, i) => String(i + 1)).join(", ");
    let chosen = modelRow ? "models" : !seedRow ? "1"
      : ["3", "5"].includes(String(count)) && seedRow.values.trim() === plain(count) ? String(count) : "other";
    const facts = structureFacts();
    const options = [["1", "1"], ["3", "3"], ["5", "5"], ["other", "Other seeds"]];
    if ((facts && facts.models > 1) || modelRow) options.push(["models", "From the NMR models"]);
    const setRows = (axis, values) => {
      state.sweep = state.sweep.filter((row) => row.axis !== SEED_AXIS && row.axis !== MODEL_AXIS);
      if (axis) state.sweep.push({ axis, values });
    };
    wrap.appendChild(segmented("run-replicas", options, chosen, "Replicas", (value) => {
      if (value === "1") setRows(null);
      else if (value === "3" || value === "5") setRows(SEED_AXIS, plain(Number(value)));
      else if (value === "other") setRows(SEED_AXIS, seedRow ? seedRow.values : "11, 22, 33");
      else if (value === "models") setRows(MODEL_AXIS, plain(Math.min(3, (facts && facts.models) || 3)));
      renderSteps();
      afterChange();
    }));
    const row = seedRow && chosen === "other" ? seedRow : modelRow;
    if (row) {
      const values = document.createElement("input");
      values.type = "text";
      values.className = "mono";
      values.value = row.values;
      values.setAttribute("aria-label", row.axis === MODEL_AXIS ? "Models" : "Seeds");
      values.addEventListener("change", () => { row.values = values.value; afterChange(); });
      wrap.appendChild(values);
    }
    wrap.appendChild(node("span", "builder-help", modelRow
      ? "One run from each model named, by its number in the file: replicas that start from different structures of the ensemble."
      : "Independent runs that differ only by their seed, written as a sweep of `simulation.random_seed`. A stopping rule asks for at least three."));
    withCodeIn(wrap.lastChild);
    return wrap;
  }

  // ---------------------------------------------------------------- sweeps

  function sweepRuns() {
    return state.sweep.reduce((runs, row) => runs * Math.max(1, sweepRunsOf(row)), 1);
  }

  /* Commas outside quotes separate values; one inside a quoted value, in the
   * bracketed form, does not. The server reads the values; this only counts. */
  function sweepRunsOf(row) {
    if (!row.axis || !String(row.values || "").trim()) return 1;
    const typed = String(row.values).trim().replace(/^\[|\]$/g, "");
    let count = 0, quoted = null, seen = "";
    for (const ch of typed) {
      if (quoted) { if (ch === quoted) quoted = null; seen += ch; continue; }
      if (ch === '"' || ch === "'") { quoted = ch; seen += ch; continue; }
      if (ch === ",") { if (seen.trim()) count += 1; seen = ""; continue; }
      seen += ch;
    }
    if (seen.trim()) count += 1;
    return Math.max(1, count);
  }

  function seedSwept() {
    return state.sweep.some((row) => (row.axis === SEED_AXIS || row.axis === MODEL_AXIS)
      && sweepRunsOf(row) >= 2);
  }

  /* Run the study once for every value of a setting, other than the seed
   * and the model, which Replicas owns. */
  function sweepGroup(inside) {
    const section = node("div", "run-section builder-sweep" + (inside ? "" : " builder-group"));
    section.id = "run-sweep";
    const axes = ((state.schema && state.schema.sweep_axes) || [])
      .filter((axis) => axis !== SEED_AXIS && axis !== MODEL_AXIS);
    if (!axes.length) return section;
    const rows = state.sweep.filter((row) => row.axis !== SEED_AXIS && row.axis !== MODEL_AXIS);
    const head = node("button", "run-section-head builder-more");
    head.type = "button";
    const live = rows.filter((row) => row.axis && String(row.values).trim()).length;
    head.textContent = live ? `Varied: ${live} setting${live === 1 ? "" : "s"}, ${sweepRuns()} runs in all`
      : "Vary another setting across runs";
    head.setAttribute("aria-expanded", String(state.open.has(SWEEP_KEY) || rows.length > 0));
    head.addEventListener("click", () => {
      if (state.open.has(SWEEP_KEY)) state.open.delete(SWEEP_KEY);
      else state.open.add(SWEEP_KEY);
      if (!state.open.has(SWEEP_KEY) || rows.length) { renderSteps(); return; }
      state.sweep.push({ axis: "", values: "" });
      renderSteps();
    });
    section.appendChild(head);
    if (!state.open.has(SWEEP_KEY) && !rows.length) return section;

    const body = node("div", "run-section-body run-sweep-body");
    body.appendChild(node("p", "builder-help run-section-note",
      "The study runs once for every combination. Separate values with commas; where a value holds a comma, write the list in brackets."));
    rows.forEach((row) => body.appendChild(sweepRow(row, axes)));
    const add = node("button", "run-sweep-add builder-add", "+ Add a setting");
    add.type = "button";
    add.addEventListener("click", () => {
      state.sweep.push({ axis: "", values: "" });
      renderSteps();
    });
    body.appendChild(add);
    section.appendChild(body);
    return section;
  }

  function sweepRow(row, axes) {
    const line = node("div", "run-sweep-row");
    const select = document.createElement("select");
    select.className = "run-sweep-axis";
    select.setAttribute("aria-label", "Setting to vary");
    const blank = node("option", null, "Choose a setting");
    blank.value = "";
    select.appendChild(blank);
    // Grouped by phase, each by its label, so 110 dotted names read as four
    // short lists.
    const byPhase = {};
    axes.forEach((axis) => {
      const [phase, name] = axis.split(".");
      (byPhase[phase] = byPhase[phase] || []).push([axis, name]);
    });
    Object.keys(byPhase).forEach((phase) => {
      const group = document.createElement("optgroup");
      group.label = (PHASES.find((p) => p.name === phase) || { label: phase }).label;
      byPhase[phase].forEach(([axis, name]) => {
        const field = fieldOf(phase, name);
        const option = node("option", null, field ? `${field.label}${field.unit ? " (" + field.unit + ")" : ""}` : axis);
        option.value = axis;
        group.appendChild(option);
      });
      select.appendChild(group);
    });
    select.value = row.axis || "";
    select.addEventListener("change", () => {
      row.axis = select.value;
      afterChange();
    });
    const values = document.createElement("input");
    values.type = "text";
    values.className = "run-sweep-values mono";
    values.placeholder = "e.g. 300, 310, 320";
    values.setAttribute("aria-label", "Values to run");
    values.value = row.values || "";
    values.addEventListener("input", () => {
      row.values = values.value;
      afterChange();
    });
    const remove = window.FastMDXIcons.button("close", "Remove this setting", "run-sweep-remove builder-icon-btn");
    remove.addEventListener("click", () => {
      state.sweep.splice(state.sweep.indexOf(row), 1);
      renderSteps();
      afterChange();
    });
    line.append(select, values, remove);
    return line;
  }

  // ---------------------------------------------------- enhanced sampling

  /* Enhanced sampling as cards: none, or one of the blocks the schema marks
   * as biased sampling, each said by its own first sentence. */
  const stashedSampling = {};

  function samplingFields() {
    return fieldsFor("simulation").filter((field) => field.role === "biased-sampling");
  }

  function samplingGroup(group) {
    const box = node("section", "builder-group");
    box.dataset.group = "simulation:" + group.title;
    const head = node("div", "builder-group-head");
    head.append(node("h3", null, group.title), withCode(node("p"), group.why || ""));
    box.appendChild(head);
    const kinds = samplingFields().map((field) => field.name);
    const chosen = kinds.filter((name) => !isEmpty(valueOf("simulation", name)));
    const cards = node("div", "builder-cards");
    cards.setAttribute("role", "group");
    cards.setAttribute("aria-label", group.title);
    const card = (key, title, what) => {
      const button = node("button", "builder-card-choice");
      button.type = "button";
      button.dataset.sampling = key;
      const on = key === "none" ? !chosen.length : chosen.includes(key);
      button.setAttribute("aria-pressed", String(on));
      button.append(node("span", "builder-card-title", title), node("span", "builder-card-what", what));
      button.addEventListener("click", () => {
        state.values.simulation = state.values.simulation || {};
        kinds.forEach((name) => {
          const value = state.values.simulation[name];
          if (name !== key && !isEmpty(value)) {
            stashedSampling[name] = value;
            delete state.values.simulation[name];
            setDecisionAside(dotted("simulation", name));
          }
        });
        if (key !== "none") {
          state.open.add("sampling:" + key);
          if (stashedSampling[key] !== undefined && isEmpty(state.values.simulation[key])) {
            state.values.simulation[key] = stashedSampling[key];
            bringDecisionBack(dotted("simulation", key));
          }
        }
        state.samplingChosen = key;
        renderSteps();
        afterChange();
      });
      cards.appendChild(button);
    };
    card("none", "None", "Plain dynamics.");
    group.fields.forEach((field) => {
      if (kinds.includes(field.name)) card(field.name, field.label || field.name, field.summary || "");
    });
    box.appendChild(cards);
    const shown = chosen.length ? chosen
      : (state.samplingChosen && state.samplingChosen !== "none" ? [state.samplingChosen] : []);
    const grid = node("div", "builder-grid");
    group.fields.filter((field) => shown.includes(field.name))
      .forEach((field) => grid.appendChild(control("simulation", field)));
    // Settings of the group that are not one of the four, should there be any.
    group.fields.filter((field) => !kinds.includes(field.name))
      .forEach((field) => grid.appendChild(control("simulation", field)));
    if (grid.childElementCount) box.appendChild(grid);
    return box;
  }

  // -------------------------------------------- running until it is determined

  /* `simulation.stop_when` as a form: the measures, each with the error it
   * must reach, a ceiling, and whether replicas must agree. Rows being
   * written (an analysis chosen, its error not yet) are held here until they
   * are whole, and only whole ones reach the config. */
  let stoppingDraft = null;

  function stoppingControl(field) {
    const measures = field.measures || [];
    const box = node("div", "builder-stopping");

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
    const draft = stoppingDraft || draftFrom(valueOf("simulation", field.name));
    stoppingDraft = draft;
    if (!draft.rows.length) draft.rows.push({ analysis: "", kind: "absolute", amount: "" });

    const complete = (row) => row.analysis && Number(row.amount) > 0;
    const commit = (redraw = true) => {
      stoppingDraft = draft;
      box.redraw = redraw;
      box.dispatchEvent(new Event("change", { bubbles: true }));
    };
    const unitOf = (name) => {
      const found = measures.filter((m) => m.analysis === name)[0];
      return found ? found.unit : "";
    };

    const rows = node("div", "builder-stopping-rows");
    draft.rows.forEach((row, index) => {
      const line = node("div", "builder-stopping-row");
      line.dataset.index = String(index);
      const pick = document.createElement("select");
      pick.className = "builder-stopping-analysis";
      pick.setAttribute("aria-label", "Quantity");
      const blank = node("option", null, "Choose a quantity");
      blank.value = "";
      pick.appendChild(blank);
      measures.forEach((m) => {
        const option = node("option", null, m.label);
        option.value = m.analysis;
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
      [["absolute", "to ± " + (unit || "its own unit")], ["relative", "to ± % of the mean"]]
        .forEach(([value, label]) => {
          const option = node("option", null, label);
          option.value = value;
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
      amount.placeholder = row.kind === "relative" ? "e.g. 5" : "e.g. 0.01";
      amount.value = row.amount;
      amount.addEventListener("change", (event) => {
        event.stopPropagation();
        row.amount = amount.value.trim();
        commit(false);
      });
      const remove = window.FastMDXIcons.button("close", "Remove this quantity", "run-sweep-remove builder-icon-btn");
      remove.addEventListener("click", (event) => {
        event.preventDefault();
        draft.rows.splice(index, 1);
        commit();
      });
      line.append(pick, kind, amount, remove);
      rows.appendChild(line);
    });
    box.appendChild(rows);

    const add = node("button", "run-sweep-add builder-stopping-add builder-add", "+ Add a quantity");
    add.type = "button";
    add.addEventListener("click", (event) => {
      event.preventDefault();
      draft.rows.push({ analysis: "", kind: "absolute", amount: "" });
      commit();
    });
    box.appendChild(add);

    const ceilingRow = node("div", "builder-stopping-line");
    const ceiling = document.createElement("input");
    ceiling.type = "number";
    ceiling.step = "any";
    ceiling.min = "0";
    ceiling.className = "builder-stopping-ceiling";
    ceiling.setAttribute("aria-label", "The most production any run may reach, in ns");
    ceiling.placeholder = "e.g. 50";
    ceiling.value = draft.ceiling;
    ceiling.addEventListener("change", (event) => {
      event.stopPropagation();
      draft.ceiling = ceiling.value.trim();
      commit(false);
    });
    ceilingRow.append(node("span", null, "At most"), ceiling,
      node("span", null, "ns of production per run; it runs in pieces until each quantity is determined."));
    box.appendChild(ceilingRow);

    const replicasRow = node("label", "chip-toggle builder-stopping-replicas");
    const replicas = document.createElement("input");
    replicas.type = "checkbox";
    replicas.checked = draft.replicas;
    replicas.addEventListener("change", (event) => {
      event.stopPropagation();
      draft.replicas = replicas.checked;
      commit();
    });
    replicasRow.append(replicas, document.createTextNode(
      "Replicas must agree (recommended: one run can look equilibrated while trapped in one state)"));
    box.appendChild(replicasRow);

    const note = node("div", "builder-help builder-stopping-note");
    if (draft.replicas && !seedSwept()) {
      note.appendChild(document.createTextNode("Replicas are runs swept over the seed, and this study has none. "));
      const sweep = node("button", "ghost-btn builder-stopping-seeds", "Run three replicas");
      sweep.type = "button";
      sweep.addEventListener("click", (event) => {
        event.preventDefault();
        state.sweep = state.sweep.filter((row) => row.axis !== SEED_AXIS && row.axis !== MODEL_AXIS);
        state.sweep.push({ axis: SEED_AXIS, values: "1, 2, 3" });
        renderSteps();
        afterChange();
      });
      note.appendChild(sweep);
    } else if (!draft.replicas) {
      note.textContent = "One run's own precision is accepted, and the record says it was not checked against independent starts.";
    }
    box.appendChild(note);

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

  /* A refusal carries what would fix it and the setting it is about
   * (fastmdxplora.remedies), so it is said on that setting's field, with
   * the field shown and pointed at. Where the schema holds a spelling near
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

  function clearRefusal() {
    if (!state.refused) return;
    const targets = state.refused.targets;
    state.refused = null;
    targets.forEach((target) => {
      const wrap = wrapOf(target.phase, target.name);
      if (!wrap) return;
      wrap.classList.remove("is-refused");
      const said = wrap.querySelector(".builder-refusal");
      if (said) said.remove();
    });
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
        redrawField(phase, field.name);
        afterChange();
      });
      note.appendChild(use);
    }
    return note;
  }

  /* Text whose `setting` names are set as code, as the refusals write them. */
  function withCode(host, words) {
    String(words || "").split(/(`[^`]+`)/).forEach((part) => {
      if (/^`[^`]+`$/.test(part)) host.appendChild(node("code", "", part.slice(1, -1)));
      else if (part) host.appendChild(document.createTextNode(part));
    });
    return host;
  }

  function withCodeIn(host) {
    if (!host) return host;
    const words = host.textContent;
    host.textContent = "";
    return withCode(host, words);
  }

  /* Said on its fields when the answer names one; cleared by an answer that
   * went through. Returns whether it was said on a field. */
  function sayRefusal(answer) {
    if (!answer || answer.ok) {
      if (state.refused) clearRefusal();
      return false;
    }
    const remedy = answer.remedy || null;
    const targets = remedy ? (remedy.settings || []).map(targetOf).filter(Boolean) : [];
    if (!targets.length) return false;
    state.refused = {
      targets, why: remedy.why || answer.error || "", fix: remedy.fix || "",
      suggestion: remedy.suggestion === undefined ? null : remedy.suggestion,
    };
    renderSteps();
    const first = targets[0];
    const wrap = wrapOf(first.phase, first.name);
    if (wrap) {
      wrap.classList.add("is-refused");
      const group = wrap.closest(".builder-group");
      if (group) group.classList.add("is-open");
      wrap.scrollIntoView({ block: "center", behavior: "smooth" });
      const input = wrap.querySelector("input, select, textarea");
      if (input) input.focus({ preventScroll: true });
    }
    return true;
  }

  // ------------------------------------------ a residue's protonation state

  /* `setup.residue_states` as rows: a residue named by chain and number, and
   * the state it takes in place of the one setup would choose. The residues
   * are offered from the structure, and a histidine clicked in the picture
   * is added here. A row whose state is not chosen yet stays in the form and
   * reaches the config once it has one. */
  let residuesPending = [];
  let titratable = [];

  function residueStatesChosen() {
    const field = byControl("setup", "residues");
    const value = field ? (state.values.setup || {})[field.name] : null;
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function residueStatesControl(field) {
    const table = field.states || {};
    const meaning = field.meaning || {};
    const box = node("div", "builder-residues");
    const chosen = Object.assign({}, residueStatesChosen());
    const known = new Map(titratable.map((residue) => [residue.key, residue]));
    const commit = () => box.dispatchEvent(new Event("change", { bubbles: true }));
    const stateLabel = (name) => (meaning[name] ? `${name}, ${meaning[name]}` : name);

    const keys = Object.keys(chosen).concat(residuesPending.filter((key) => !(key in chosen)));
    const rows = node("div", "builder-residues-rows");
    keys.forEach((key) => {
      const residue = known.get(key);
      const line = node("div", "builder-residue-row");
      line.dataset.residue = key;
      line.appendChild(node("span", "builder-residue-name mono", residue ? `${key} ${residue.resname}` : key));
      const pick = document.createElement("select");
      pick.className = "builder-residue-state";
      pick.setAttribute("aria-label", `State for ${key}`);
      const blank = node("option", null, "Choose a state");
      blank.value = "";
      pick.appendChild(blank);
      const states = residue ? residue.states : [].concat(...Object.values(table));
      states.forEach((name) => {
        const option = node("option", null, stateLabel(name));
        option.value = name;
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
        box.readValueNow = Object.keys(chosen).length ? Object.assign({}, chosen) : null;
        commit();
      });
      line.appendChild(pick);
      const remove = window.FastMDXIcons.button("close", `Leave ${key} to setup`, "run-sweep-remove builder-icon-btn");
      remove.addEventListener("click", (event) => {
        event.preventDefault();
        delete chosen[key];
        residuesPending = residuesPending.filter((k) => k !== key);
        commit();
      });
      line.appendChild(remove);
      if (residue && residue.near) {
        line.appendChild(node("span", "builder-help builder-residue-near", residue.near));
      }
      rows.appendChild(line);
    });
    box.appendChild(rows);

    const add = node("div", "builder-residues-add");
    const free = titratable.filter((residue) => !keys.includes(residue.key));
    if (titratable.length) {
      const pick = document.createElement("select");
      pick.className = "builder-residue-pick";
      pick.setAttribute("aria-label", "A residue to set");
      const blank = node("option", null, free.length ? "Set a residue" : "Every residue is listed");
      blank.value = "";
      pick.appendChild(blank);
      Object.keys(table).forEach((resname) => {
        const these = free.filter((residue) => residue.resname === resname);
        if (!these.length) return;
        const group = document.createElement("optgroup");
        group.label = resname;
        these.forEach((residue) => {
          const option = node("option", null,
            `${residue.key} ${resname}` + (residue.near ? ` (${residue.near})` : ""));
          option.value = residue.key;
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
      typed.className = "builder-residue-typed mono";
      typed.placeholder = "e.g. A:57";
      typed.setAttribute("aria-label", "A residue, as chain and number");
      const go = node("button", "ghost-btn builder-residue-add", "Add");
      go.type = "button";
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
      add.append(typed, go);
    }
    box.appendChild(add);
    if (titratable.some((residue) => residue.resname === "HIS")) {
      box.appendChild(node("span", "builder-help",
        "A histidine in the picture of the system can be clicked to add it here."));
    }
    box.readValue = () => (Object.keys(chosen).length ? Object.assign({}, chosen) : null);
    box.writeValue = () => {};
    return box;
  }

  /* A residue picked in the picture: added to the rows, its group shown,
   * and its state asked for. */
  function pickResidue(key) {
    if (!key) return;
    if (!(key in residueStatesChosen()) && !residuesPending.includes(key)) {
      residuesPending.push(key);
    }
    renderSteps();
    const row = document.querySelector(
      `#run-settings .builder-residue-row[data-residue="${CSS.escape(key)}"]`);
    if (!row) return;
    const group = row.closest(".builder-group");
    if (group) group.classList.add("is-open");
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    const pick = row.querySelector("select");
    if (pick) pick.focus({ preventScroll: true });
    row.classList.add("is-pointed");
    setTimeout(() => row.classList.remove("is-pointed"), 1600);
  }

  // --------------------------------------------------- which analyses

  /* Why an analysis will not run for this study, or "" where it will or
   * where it cannot be told: the server's reading of the config and, once
   * the structure is read, of what it holds (applicable.py). */
  function whyNotApplies(name) {
    const fromStructure = state.estimate && state.estimate.ok && state.estimate.not_applicable;
    const fromConfig = state.built && state.built.ok && state.built.not_applicable;
    const reasons = fromStructure || fromConfig || {};
    const reason = reasons[name] || "";
    // One the study has to name (two groups to analyse) runs where it is
    // chosen, unless it needs something else the study lacks: the form
    // knows that before any answer comes back.
    if (chosenOnly(name, reason) && state.analyses.has(name)) return "";
    return reason;
  }

  /* Whether all that keeps an analysis out is that it has to be chosen. */
  function chosenOnly(name, reason) {
    const options = state.schema && state.schema.analysis_options;
    return onRequest(name) && (!reason || reason === (options && options.when_chosen));
  }

  /* An analysis left out of every default plan and run only where chosen,
   * as its class declares (`requires_naming`). */
  function onRequest(name) {
    const options = state.schema && state.schema.analysis_options;
    const needs = (options && options.needs && options.needs[name]) || [];
    return needs.indexOf("naming") >= 0;
  }

  /* The analyses alone built again, where what applies has changed. */
  function redrawAnalyses() {
    const old = document.querySelector("#run-settings .builder-analyses");
    if (!old || !state.phases.has("analysis")) return;
    const focus = focusedSetting();
    old.replaceWith(analysisSection());
    applyFind();
    restoreFocus(focus);
  }

  function analysisSection() {
    const section = node("section", "builder-group run-section builder-analyses");
    section.dataset.group = "analysis:analyses";
    const head = node("div", "builder-group-head");
    head.append(node("h3", null, "Analyses"),
      node("p", null, "Every analysis that applies to this system runs, unless you choose some."));
    section.appendChild(head);

    const options = state.schema.analysis_options;
    if (!options || !options.available) {
      section.appendChild(node("div", "empty-detail muted", (options && options.reason) || "No analyses available."));
      return section;
    }
    const only = state.analyses.size > 0 || state.analysisMode === "only";
    const bar = node("div", "builder-analyses-bar");
    bar.appendChild(segmented("run-analyses-mode",
      [["all", "Every one that applies"], ["only", "Only these"]], only ? "only" : "all", "Which analyses run",
      (value) => {
        if (value === "only") {
          state.analysisMode = "only";
          Object.keys(options.analyses).forEach((name) => {
            if (!onRequest(name) && !whyNotApplies(name)) state.analyses.add(name);
          });
        } else {
          state.analysisMode = "all";
          state.analyses.clear();
        }
        renderSteps();
        afterChange();
      }));
    const note = node("span", "builder-help");
    note.textContent = only ? "Written to the config as `analysis.include`." : "";
    withCodeIn(note);
    bar.appendChild(note);
    section.appendChild(bar);

    const order = options.category_order || [];
    const categories = options.categories || {};
    const grouped = {};
    Object.keys(options.analyses).forEach((name) => {
      const title = categories[name] || "Other";
      (grouped[title] = grouped[title] || []).push(name);
    });
    const body = node("div", "run-analyses builder-analyses-grid");
    order.forEach((title) => {
      const members = grouped[title];
      if (!members || !members.length) return;
      const theme = node("div", "builder-analyses-theme");
      theme.appendChild(node("div", "run-analysis-group", title));
      members.forEach((name) => theme.appendChild(analysisRow(name, options, only)));
      body.appendChild(theme);
    });
    section.appendChild(body);
    return section;
  }

  function analysisRow(name, options, only) {
    const explanation = (options.explanations || {})[name] || {};
    const settings = (options.analyses[name] || []).filter((o) => !o.shared);
    const label = ((options.labels || {})[name]) || name;
    const why = whyNotApplies(name);

    const row = node("div", "analyse-row builder-field");
    row.dataset.setting = "analysis:" + name;
    row.dataset.phase = "analysis";
    row.dataset.find = [name, label, explanation.title || "", "analysis"].join(" ").toLowerCase();
    row.dataset.chosen = String(state.analyses.has(name));
    if (why) row.classList.add("is-off");
    if (only && state.analyses.has(name)) row.classList.add("is-changed");

    const head = node("div", "analyse-row-head");
    const choice = node("label", "analyse-choice");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = only ? state.analyses.has(name) : !why;
    box.disabled = !only || Boolean(why && !chosenOnly(name, why) && !state.analyses.has(name));
    box.addEventListener("change", () => {
      if (box.checked) state.analyses.add(name);
      else state.analyses.delete(name);
      row.dataset.chosen = String(box.checked);
      row.classList.toggle("is-changed", box.checked);
      if (!state.analyses.size) state.analysisMode = "only";
      if (onRequest(name)) redrawAnalyses();
      afterChange();
    });
    const naming = node("span", "analyse-naming");
    naming.appendChild(node("span", "analyse-name", label));
    naming.appendChild(node("code", "analyse-code", name));
    choice.append(box, naming);
    head.appendChild(choice);
    if (why) head.appendChild(node("span", "analyse-why muted small", why));

    const key = `analysis:${name}`;
    if (settings.length) {
      const toggle = node("button", "analyse-toggle builder-linkish",
        `${settings.length} setting${settings.length === 1 ? "" : "s"}`);
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(state.open.has(key)));
      toggle.addEventListener("click", () => {
        if (state.open.has(key)) state.open.delete(key);
        else state.open.add(key);
        const shown = state.open.has(key);
        toggle.setAttribute("aria-expanded", String(shown));
        const body = row.querySelector(".analyse-settings");
        if (body) body.hidden = !shown;
      });
      head.appendChild(toggle);
    }
    row.appendChild(head);

    if (explanation.detail || explanation.title) {
      const explains = node("p", "analyse-explains builder-help", explanation.detail || explanation.title);
      row.appendChild(explains);
    }
    if (settings.length) {
      const body = node("div", "analyse-settings builder-grid");
      body.hidden = !state.open.has(key);
      settings.forEach((option) => body.appendChild(analysisControl(name, option)));
      row.appendChild(body);
    }
    return row;
  }

  function analysisControl(analysis, option) {
    const wrap = node("label", "builder-field");
    const head = node("div", "builder-head");
    head.appendChild(node("span", "builder-label", option.label || option.name.replace(/_/g, " ")));
    wrap.appendChild(head);

    /* Something the run works out for itself. Shown, because seeing what it
     * will use is worth something, but not editable: typing a ligand name
     * that does not match the one detected would have the analysis find
     * nothing and report the ligand as absent. */
    if (option.supplied_by_the_run) {
      wrap.appendChild(node("div", "run-supplied-value", "detected from the structure when the run starts"));
      if (option.help) wrap.appendChild(node("span", "builder-help", option.help));
      return wrap;
    }

    let input;
    if (option.control === "multiselect" && option.choices) {
      /* Checkboxes, not a multiple select: no modifier key to take a
       * second one, and every option in view. */
      const chosen = new Set(Array.isArray(option.default) ? option.default : []);
      const current = (state.analysisOptions[analysis] || {})[option.name];
      if (Array.isArray(current)) { chosen.clear(); current.forEach((v) => chosen.add(v)); }
      const group = node("div", "run-checkbox-group");
      const boxes = [];
      option.choices.forEach((choice) => {
        const item = node("label", "run-checkbox chip-toggle");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = choice;
        box.checked = chosen.has(choice);
        item.append(box, node("span", null, choice.replace(/_/g, " ")));
        group.appendChild(item);
        boxes.push(box);
      });
      const report = () => {
        const picked = boxes.filter((b) => b.checked).map((b) => b.value);
        state.analysisOptions[analysis] = state.analysisOptions[analysis] || {};
        // All of them is the default, so recording it changes nothing.
        if (!picked.length || picked.length === option.choices.length) {
          delete state.analysisOptions[analysis][option.name];
        } else {
          state.analysisOptions[analysis][option.name] = picked;
        }
        afterChange();
      };
      boxes.forEach((b) => b.addEventListener("change", report));
      wrap.appendChild(group);
      if (option.help) wrap.appendChild(node("span", "builder-help", option.summary || option.help));
      return wrap;
    }
    if (option.choices && option.choices.length) {
      input = document.createElement("select");
      option.choices.forEach((choice) => {
        const item = node("option", null, choice === option.default ? choice + " (default)" : choice);
        item.value = choice;
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
        // The same picker every other path field has.
        input.id = `option-${analysis}-${option.name}`;
        input.setAttribute("data-picks", "structure");
      }
      input.placeholder = option.default === null || option.default === undefined ? "" : String(option.default);
    }
    const current = (state.analysisOptions[analysis] || {})[option.name];
    if (current !== undefined) {
      if (input.type === "checkbox") input.checked = Boolean(current);
      else input.value = current;
    }
    input.addEventListener("change", () => {
      const value = input.type === "checkbox" ? input.checked : input.value;
      state.analysisOptions[analysis] = state.analysisOptions[analysis] || {};
      if (value === "" || value === null) delete state.analysisOptions[analysis][option.name];
      else state.analysisOptions[analysis][option.name] = value;
      afterChange();
    });
    wrap.appendChild(input);
    if (option.help) wrap.appendChild(node("span", "builder-help", option.summary || option.help));
    return wrap;
  }

  // ---------------------------------------------------------------- review

  function renderReviewStep() {
    const host = el("run-review-body");
    if (!host) return;
    host.replaceChildren();

    const planBox = node("section", "builder-group builder-plan");
    planBox.dataset.group = "review:plan";
    planBox.appendChild(groupHead("The plan", "What will be built, run and analysed, defaults marked."));
    const plan = node("dl", "builder-plan-lines");
    plan.id = "run-plan";
    planBox.appendChild(plan);
    const checks = (state.schema && state.schema.checks) || [];
    if (checks.length && state.phases.has("simulation")) {
      const held = node("div", "builder-checked");
      held.appendChild(node("div", "builder-checked-k", "Checked after"));
      const list = node("ul");
      checks.forEach((check) => list.appendChild(node("li", null, check)));
      held.appendChild(list);
      planBox.appendChild(held);
    }
    host.appendChild(planBox);

    const knowing = node("section", "builder-group");
    knowing.id = "run-worth-knowing";
    knowing.dataset.group = "review:knowing";
    knowing.hidden = true;
    host.appendChild(knowing);

    const decided = node("section", "builder-group");
    decided.id = "run-decisions";
    decided.dataset.group = "review:decisions";
    host.appendChild(decided);

    // Where it runs, and what it may spend: the scheduling block and the
    // study's own settings that are choices rather than records.
    const runs = node("section", "builder-group");
    runs.dataset.group = "review:runs";
    runs.appendChild(groupHead("Where it runs", "How the runs are scheduled, what the study may spend, and what it says as it works."));
    const grid = node("div", "builder-grid");
    (state.schema.execution_options || []).forEach((field) => {
      const wrap = control(EXECUTION_KEY, field);
      if (!field.essential) wrap.classList.add("is-more");
      grid.appendChild(wrap);
    });
    (state.schema.run_options || []).filter((field) => field.role !== "provenance").forEach((field) => {
      const wrap = control(RUN_OPTIONS_KEY, field);
      if (!field.essential) wrap.classList.add("is-more");
      grid.appendChild(wrap);
    });
    const written = node("div", "builder-field");
    written.dataset.setting = "written-by";
    written.dataset.phase = RUN_OPTIONS_KEY;
    written.dataset.find = "written by agent ai model provenance";
    written.appendChild(labelled("Written by"));
    written.appendChild(node("div", "builder-readonly", writtenBy()));
    written.appendChild(node("span", "builder-help",
      "Recorded, not chosen. A study the Agent or an AI app drafted carries its AI model's name."));
    grid.appendChild(written);
    const full = node("div", "builder-field");
    full.dataset.setting = "full";
    full.dataset.phase = RUN_OPTIONS_KEY;
    full.dataset.find = "write every setting full config";
    full.appendChild(labelled("Config"));
    const toggle = node("label", "chip-toggle");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = "run-full-config";
    box.checked = Boolean(state.full);
    box.addEventListener("change", () => { state.full = box.checked; afterChange(); });
    toggle.append(box, document.createTextNode("Write every setting"));
    full.append(toggle, node("span", "builder-help",
      "Not only the ones changed, so a default that moves in a later version cannot change what the file meant."));
    grid.appendChild(full);
    runs.appendChild(grid);
    const more = runs.querySelectorAll(".is-more").length;
    if (more) {
      const show = node("button", "builder-more", `Show ${more} more`);
      show.type = "button";
      show.addEventListener("click", () => {
        const open = !runs.classList.contains("is-open");
        runs.classList.toggle("is-open", open);
        show.textContent = open ? "Show fewer" : `Show ${more} more`;
      });
      runs.appendChild(show);
    }
    host.appendChild(runs);
    renderReviewLive();
  }

  function groupHead(title, why) {
    const head = node("div", "builder-group-head");
    head.appendChild(node("h3", null, title));
    if (why) head.appendChild(node("p", null, why));
    return head;
  }

  /* How the study was written, as it records it: the form names none of
   * the recorded values, which the schema holds. */
  function writtenBy() {
    const run = state.values[RUN_OPTIONS_KEY] || {};
    const fields = (state.schema.run_options || []).filter((field) => field.role === "provenance");
    const said = fields.filter((field) => !isEmpty(run[field.name]) && field.control !== "mapping")
      .map((field) => `${field.label}: ${said_(run[field.name])}`);
    return said.length ? said.join(" \u00b7 ") : "You, in this form";
  }

  function said_(value) {
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }

  /* The parts of the review that follow the study as it changes: the plan
   * from the server's last answer, what is worth knowing from the last
   * estimate, and the reasons recorded. */
  function renderReviewLive() {
    const plan = el("run-plan");
    if (plan) {
      plan.replaceChildren();
      const lines = (state.built && state.built.ok && state.built.plan) || [];
      lines.filter((line) => line.label !== "Checked after").forEach((line) => {
        plan.appendChild(node("dt", null, line.label));
        const value = node("dd", line.default ? "is-default" : null, line.value);
        if (line.default) value.appendChild(node("span", "builder-default-mark", "default"));
        plan.appendChild(value);
      });
      if (!lines.length) plan.appendChild(node("dd", "muted", whyNotReady() || "Being worked out…"));
    }

    const knowing = el("run-worth-knowing");
    // The estimate, which carries the advice, can arrive after the checks.
    const status = el("run-status");
    if (status && status.dataset.state === "ok") setStatus("ok", checksPass());
    if (knowing) {
      knowing.replaceChildren();
      const advice = (state.estimate && state.estimate.ok && state.estimate.advisories) || [];
      const time = state.estimate && state.estimate.ok ? state.estimate.time || {} : {};
      knowing.hidden = !advice.length && !(state.estimate && state.estimate.ok && !time.ok);
      knowing.appendChild(groupHead("Worth knowing"));
      const list = node("ul", "system-preview-advice builder-advice");
      advice.forEach((item) => {
        const entry = node("li");
        entry.appendChild(node("strong", null, item.summary));
        entry.appendChild(withCode(node("span"), ` ${item.detail} ${item.remedy}`));
        if (item.setting && phaseOf(item.setting)) {
          const go = node("button", "ghost-btn system-preview-go",
            `Show ${(fieldOf(phaseOf(item.setting), item.setting) || {}).label || item.setting.replace(/_/g, " ")}`);
          go.type = "button";
          go.addEventListener("click", () => showSetting(item.setting));
          entry.appendChild(go);
        }
        list.appendChild(entry);
      });
      if (state.estimate && state.estimate.ok && !time.ok && time.reason) {
        const entry = node("li");
        entry.appendChild(node("strong", null, "Time here not known yet."));
        entry.appendChild(document.createTextNode(" " + time.reason));
        list.appendChild(entry);
      }
      knowing.appendChild(list);
    }

    const decided = el("run-decisions");
    if (decided) {
      decided.replaceChildren();
      const names = Object.keys(state.decisions);
      decided.appendChild(groupHead("Why these settings",
        names.length ? "Recorded in the config and the study's record, and given in the report beside the methods."
          : "A changed setting can carry its reason: press “Why this value?” under it."));
      if (names.length) {
        const list = node("ul", "builder-decisions");
        names.forEach((name) => {
          const decision = state.decisions[name];
          const entry = node("li");
          entry.appendChild(node("code", null, name));
          entry.appendChild(document.createTextNode(" " + (decision.why || "")));
          if (decision.source) entry.appendChild(node("span", "builder-why-from",
            decision.source === "person" ? "you" : decision.source));
          list.appendChild(entry);
        });
        decided.appendChild(list);
      }
    }
  }

  // ------------------------------------------------------------- the config

  /* The form as plain values: what it holds, apart from the page. */
  function snapshotOfForm() {
    const fullBox = el("run-full-config");
    return {
      start: state.start, phases: Array.from(state.phases), values: state.values,
      analyses: Array.from(state.analyses), analysisOptions: state.analysisOptions,
      sweep: state.sweep, decisions: state.decisions,
      systems: state.systems.map((row) => ({ id: row.id, system: row.system, own: row.own || {} })),
      output: (el("run-output") && el("run-output").value.trim()) || "",
      trajectory: (el("run-trajectory") && el("run-trajectory").value.trim()) || "",
      topology: (el("run-topology") && el("run-topology").value.trim()) || "",
      full: fullBox ? fullBox.checked : Boolean(state.full),
    };
  }

  /* A config the server has mapped to form state, as the same plain values,
   * without touching the form: what the Agent panel's actions read, so a
   * reply does not overwrite somebody's draft to price itself. */
  function snapshotOfLoaded(from) {
    const values = JSON.parse(JSON.stringify(from.phases || {}));
    let decisions = {};
    if (from.study && Object.keys(from.study).length) {
      const study = Object.assign({}, from.study);
      if (study.decisions && typeof study.decisions === "object") decisions = Object.assign({}, study.decisions);
      delete study.decisions;
      values[RUN_OPTIONS_KEY] = Object.assign({}, values[RUN_OPTIONS_KEY] || {}, study);
    }
    if (from.execution && Object.keys(from.execution).length) {
      values[EXECUTION_KEY] = Object.assign({}, values[EXECUTION_KEY] || {}, from.execution);
    }
    // Every system, whole, with its name and its own settings.
    const listed = Array.isArray(from.systems) && from.systems.length ? from.systems
      : (from.system ? [{ system: from.system, id: from.system_id || "" }] : []);
    const systems = listed.map((entry) => {
      const own = {};
      PHASES.forEach((phase) => {
        if (entry[phase.name] && typeof entry[phase.name] === "object") own[phase.name] = Object.assign({}, entry[phase.name]);
      });
      return { id: String(entry.id || ""), system: String(entry.system || ""), own };
    });
    return {
      start: from.start,
      // `include_phase` is what the server sends; the old name is still read.
      phases: from.include_phase || from.include || [],
      values, decisions, systems,
      analyses: from.analyses || [], analysisOptions: from.analysis_options || {},
      sweep: (from.sweep || []).map((row) => ({ axis: String(row.axis || ""), values: String(row.values || "") })),
      output: from.output || "", trajectory: from.trajectory || "", topology: from.topology || "",
      full: false,
    };
  }

  /* The request the server builds a config from, for a snapshot: one
   * derivation for the form and for anything that holds a config of its own. */
  function bodyOf(snap) {
    const phases = new Set(snap.phases || []);
    const config = {
      output: snap.output || "",
      include_phase: PHASES.filter((p) => phases.has(p.name)).map((p) => p.name),
      sweep: (snap.sweep || []).filter((row) => row.axis && String(row.values).trim())
        .map((row) => ({ axis: row.axis, values: String(row.values).trim() })),
    };

    if (snap.start === "structure") {
      const rows = (snap.systems || []).filter((row) => row.system);
      config.systems = rows.map((row) => Object.assign(
        { system: row.system }, row.id ? { id: row.id } : {}, cloneOwn(row.own)));
      config.system = rows.length ? rows[0].system : "";
    } else {
      // The structure a trajectory refers to is the system, and the
      // trajectory itself is where the analysis looks.
      config.system = snap.topology || "";
      config.analysis = { trajectory: snap.trajectory || "", topology: snap.topology || "" };
    }

    const values = snap.values || {};
    PHASES.forEach((phase) => {
      if (!phases.has(phase.name)) return;
      const chosen = values[phase.name];
      if (chosen && Object.keys(chosen).length) {
        config[phase.name] = Object.assign(config[phase.name] || {}, chosen);
      }
    });
    // The two blocks that are not phases, under the keys the server reads.
    [RUN_OPTIONS_KEY, EXECUTION_KEY].forEach((key) => {
      const chosen = values[key];
      if (chosen && Object.keys(chosen).length) config[key] = Object.assign({}, chosen);
    });
    // A reason goes with a setting the study has: one left behind by a
    // phase unticked would explain a setting the study does not use.
    const decisions = {};
    Object.keys(snap.decisions || {}).forEach((name) => {
      const decision = snap.decisions[name];
      if (decision && decision.why && runs(config, name)) decisions[name] = decision;
    });
    if (Object.keys(decisions).length) config.study = { decisions };

    if (phases.has("analysis")) {
      const analysis = config.analysis || {};
      const chosen = new Set(snap.analyses || []);
      if (chosen.size) analysis.include = Array.from(chosen).join(", ");
      const nested = {};
      Object.keys(snap.analysisOptions || {}).forEach((name) => {
        const settings = snap.analysisOptions[name];
        const wanted = !chosen.size || chosen.has(name);
        if (wanted && settings && Object.keys(settings).length) nested[name] = settings;
      });
      if (Object.keys(nested).length) analysis.options = nested;
      config.analysis = Object.assign(analysis, config.analysis || {});
    }
    config.full = Boolean(snap.full);
    return config;
  }

  function currentState() {
    return bodyOf(snapshotOfForm());
  }

  /* Whether a reason's setting is part of the study: one of a phase not
   * run explains nothing the study does. A reason for a default is kept. */
  function runs(config, name) {
    const block = name.split(".")[0];
    const phase = PHASES.find((p) => p.name === block);
    return !phase || (config.include_phase || []).indexOf(block) >= 0;
  }

  /* A choice taken back (a sampling method set to None, a fixed length
   * again) takes its reason with it, and a choice made again brings it
   * back, as the value comes back. */
  const stashedDecisions = {};
  /* What a choice taken back set aside belongs to the study it was taken
   * back in: Reset, a config opened or a draft restored forgets it. */
  function forgetWhatWasSetAside() {
    [stashedSampling, stashedDecisions].forEach((kept) => {
      Object.keys(kept).forEach((name) => { delete kept[name]; });
    });
  }
  function setDecisionAside(name) {
    if (!state.decisions[name]) return;
    stashedDecisions[name] = state.decisions[name];
    delete state.decisions[name];
  }
  function bringDecisionBack(name) {
    if (stashedDecisions[name] && !state.decisions[name]) state.decisions[name] = stashedDecisions[name];
    delete stashedDecisions[name];
  }

  function cloneOwn(own) {
    const out = {};
    Object.keys(own || {}).forEach((phase) => { out[phase] = Object.assign({}, own[phase]); });
    return out;
  }

  /* Why the run cannot start, or "" when it can. Said where it can be acted
   * on, under the button: a grey button that says nothing leaves the reader
   * guessing at a rule the software already knows. */
  function whyNotReady() {
    if (!state.start) return "Choose what this study starts from.";
    if (state.start === "config") {
      if (!state.configVerdict) return "No config checked yet.";
      return state.configVerdict.ok ? "" : state.configVerdict.error;
    }
    if (!state.phases.size) return "Choose at least one phase to run.";
    /* Simulate without Setup, starting from a structure, has nothing to
     * simulate: setup is what turns a structure into a system. The rule is
     * the software's own -- the pipeline refuses this -- so the button
     * refuses it first. */
    if (state.start === "structure" && state.phases.has("simulation") && !state.phases.has("setup")) {
      return "Simulate needs Setup first: a structure has to be solvated and parameterised before it can be run. Tick Setup, or start from an existing run's output instead.";
    }
    if (state.start === "structure" && !state.systems.some((row) => row.system)) {
      return "Choose a structure: a PDB file, or an identifier to fetch.";
    }
    if (state.start !== "structure" && !(el("run-trajectory").value.trim() && el("run-topology").value.trim())) {
      return "Choose a trajectory and the structure that matches it.";
    }
    if (state.phases.has("analysis") && state.analysisMode === "only" && !state.analyses.size) {
      return "Choose at least one analysis, or run every one that applies.";
    }
    const invalid = document.querySelector("#run-settings .builder-field.is-invalid");
    if (invalid) {
      const said = invalid.querySelector(".builder-limit");
      return (said && said.textContent) || "A setting is out of its range.";
    }
    return "";
  }

  /* The default name is the server's to make -- one rule, in
   * fastmdxplora/naming.py. For the note under the field it shows the
   * pattern. */
  function defaultOutput() {
    const system = state.start === "structure"
      ? ((state.systems[0] && state.systems[0].system) || "")
      : ((el("run-topology") && el("run-topology").value.trim()) || "");
    const slug = system ? system.split(/[\\/]/).pop().replace(/\.[^.]+$/, "")
      .replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "").slice(0, 40) : "";
    return "fastmdxplora" + (slug ? "_" + slug : "") + "_study_<timestamp>";
  }

  function ready() {
    return !whyNotReady();
  }

  // --------------------------------------------------------- after a change

  function afterChange() {
    updateSummary();
    saveDraft();
  }

  function updateSummary() {
    const chain = el("run-summary");
    if (state.start === "config") {
      const verdict = state.configVerdict;
      text(chain, verdict && verdict.ok ? verdict.phases.join(" → ") : "No config checked yet");
    } else {
      const doing = PHASES.filter((p) => state.phases.has(p.name)).map((p) => p.label);
      const systems = state.start === "structure" ? state.systems.filter((row) => row.system).length : 1;
      const runs = sweepRuns() * Math.max(1, systems);
      const joined = doing.join(" → ");
      text(chain, !doing.length ? "" : runs > 1 ? `${joined}, ${runs} runs` : joined);
    }
    const why = whyNotReady();
    const can = !why;
    ["run-start-button", "run-download", "run-copy-command", "run-download-script",
     "run-elsewhere", "run-phone-start"].forEach((id) => {
      const button = el(id);
      if (button) button.disabled = !can;
    });
    if (!can) setStatus("waiting", why);
    renderChanged();
    scheduleCheck();
    schedulePreview();
  }

  /* "Checks pass", and how many things are worth knowing where there are:
   * a 10 fs timestep read "Checks pass" above the advice that it is too
   * long for its constraints. */
  function checksPass() {
    const advice = (state.estimate && state.estimate.ok && state.estimate.advisories) || [];
    return advice.length
      ? `Checks pass; ${advice.length === 1 ? "one thing" : advice.length + " things"} worth knowing`
      : "Checks pass";
  }

  function setStatus(kind, said) {
    ["run-status", "run-phone-status"].forEach((id) => {
      const target = el(id);
      if (!target) return;
      target.dataset.state = kind;
      target.textContent = said;
      target.title = said;
    });
    const note = el("run-note");
    if (note && kind === "waiting") {
      note.textContent = said;
      note.dataset.ok = "";
    }
  }

  /* What differs from what the run would do anyway, each with its way back. */
  function renderChanged() {
    const list = el("run-changed");
    if (!list) return;
    list.replaceChildren();
    const entries = [];
    const add = (label, value, back) => entries.push({ label, value, back });
    [...PHASES.map((p) => p.name), RUN_OPTIONS_KEY, EXECUTION_KEY].forEach((phase) => {
      if (PHASES.some((p) => p.name === phase) && !state.phases.has(phase)) return;
      Object.keys(state.values[phase] || {}).forEach((name) => {
        if (!isChanged(phase, name)) return;
        const field = fieldOf(phase, name);
        if (!field || field.role === "provenance") return;
        const before = field.default === null || field.default === undefined
          ? (field.unset ? "not set" : "") : said(field.default, field.unit);
        const now = said(valueOf(phase, name), field.control === "number" ? field.unit : "");
        add(field.label || name, before ? `${before} → ${now}` : now, () => revertSetting(phase, name));
      });
    });
    if (state.analyses.size) {
      add("Analyses", `only ${state.analyses.size}`, () => {
        state.analyses.clear();
        state.analysisMode = "all";
        renderSteps();
        afterChange();
      });
    }
    state.sweep.filter((row) => row.axis && String(row.values).trim()).forEach((row) => {
      const label = row.axis === SEED_AXIS ? "Replicas" : row.axis === MODEL_AXIS ? "Replicas from models" : row.axis;
      add(label, `${sweepRunsOf(row)} runs`, () => {
        state.sweep.splice(state.sweep.indexOf(row), 1);
        renderSteps();
        afterChange();
      });
    });
    entries.forEach((entry) => {
      const item = node("li");
      item.appendChild(node("span", "builder-changed-label", entry.label));
      item.appendChild(node("span", "mono builder-changed-value", entry.value));
      const back = window.FastMDXIcons.button("undo", `${entry.label} back to the default`, "builder-revert");
      back.addEventListener("click", entry.back);
      item.appendChild(back);
      list.appendChild(item);
    });
    if (!entries.length) list.appendChild(node("li", "muted", "Nothing changed: every setting at its default."));
    text(el("run-changed-n"), String(entries.length));
    const only = el("run-only-changed");
    if (only) only.hidden = !entries.length && !state.onlyChanged;
  }

  /* The config, the command, the script and the plan, from the server, a
   * moment after the last change; an answer to an older form is dropped.
   * Quiet: a refusal is said in the summary, and on its field only when
   * asked, so a field is not taken from under somebody typing. */
  const CHECK_DELAY_MS = 450;
  let checkTimer = null;
  let checkSeq = 0;

  function scheduleCheck() {
    clearTimeout(checkTimer);
    if (!state.start || state.start === "config" || !ready()) {
      state.built = null;
      showCode(null);
      renderReviewLive();
      return;
    }
    checkTimer = setTimeout(checkNow, CHECK_DELAY_MS);
  }

  async function checkNow() {
    const seq = ++checkSeq;
    let built;
    try {
      const response = await fetch("/api/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentState()),
      });
      built = await response.json();
    } catch (error) {
      built = { ok: false, error: "The server could not be reached." };
    }
    if (seq !== checkSeq) return;
    const before = JSON.stringify(state.built && state.built.not_applicable);
    state.built = built;
    showCode(built);
    renderReviewLive();
    if (built.ok && JSON.stringify(built.not_applicable) !== before
        && !(state.estimate && state.estimate.ok)) redrawAnalyses();
    if (!ready()) return;
    if (built.ok) {
      setStatus("ok", checksPass());
      const note = el("run-note");
      if (note && note.dataset.ok === "") note.textContent = "";
    } else {
      setStatus("refused", "Will not run: " + firstSentence(built.error || ""));
      const note = el("run-note");
      if (note) {
        note.replaceChildren(withCode(node("span"), built.error || ""));
        note.dataset.ok = "false";
        const remedy = built.remedy || null;
        if (remedy && (remedy.settings || []).some((name) => targetOf(name))) {
          const show = node("button", "builder-linkish", "Show the setting");
          show.type = "button";
          show.addEventListener("click", () => sayRefusal(built));
          note.append(" ", show);
        }
      }
    }
  }

  function firstSentence(words) {
    const found = String(words).match(/^.*?[.:](\s|$)/);
    return (found ? found[0] : String(words)).trim();
  }

  function showCode(built) {
    const yaml = el("run-config-preview");
    const command = el("run-command-preview");
    const script = el("run-script-preview");
    if (!built) {
      text(yaml, "");
      text(command, "");
      text(script, "");
      return;
    }
    text(yaml, built.ok ? built.yaml : (built.error || ""));
    text(command, built.ok ? (built.command || "This study cannot be said as one command; use the config file.") : "");
    text(script, built.ok ? (built.script || "") : "");
  }

  // ------------------------------------------------ what setup will build

  /* The box, the particle count and the time were learned from setup's log,
   * minutes into a run and after the settings could be changed. The
   * structure and the settings decide most of it, so it is said beside the
   * form as the settings change: asked for a moment after the last change,
   * and an answer to an older form is dropped. */
  const PREVIEW_DELAY_MS = 600;
  let previewTimer = null;
  let previewKey = "";
  let previewSeq = 0;

  function schedulePreview() {
    const box = el("run-system-preview");
    if (!box) return;
    const wanted = state.start === "structure" && state.phases.has("setup")
      && state.systems.some((row) => row.system);
    if (!wanted) {
      box.hidden = true;
      previewKey = "";
      previewSeq += 1;
      clearTimeout(previewTimer);
      return;
    }
    const body = currentState();
    // What the estimate reads: the structure and setup's settings, and the
    // length and runs for the time. Not the report's title.
    const key = JSON.stringify([body.systems && body.systems[0], body.setup || {},
      body.simulation || {}, body.sweep, body.include_phase]);
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
    text(el("run-system-preview-state"), "Working it out…");
    let answer;
    try {
      const response = await fetch("/api/preview-system", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      answer = await response.json();
    } catch (error) {
      answer = { ok: false, reason: "The preview could not be reached." };
    }
    if (seq !== previewSeq) return;
    box.classList.remove("is-loading");
    const before = JSON.stringify(state.estimate && state.estimate.structure);
    state.estimate = answer || {};
    renderPreview(state.estimate);
    renderReviewLive();
    // The structure decides what some settings offer (its models, its
    // residues): built again when what it holds is first known or changes.
    if (JSON.stringify(state.estimate.structure) !== before) renderSteps();
    else redrawAnalyses();
  }

  const count = (value) => Number(value).toLocaleString("en-US");

  function signed(value) {
    const n = Number(value) || 0;
    return n > 0 ? `+${n}` : n < 0 ? `−${-n}` : "0";
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
      body.appendChild(node("p", "builder-help", answer.reason || "Nothing to say yet."));
      return;
    }
    const e = answer.estimate;
    text(el("run-system-preview-state"), "an estimate");
    const listed = Array.isArray(answer.titratable) ? answer.titratable : [];
    if (JSON.stringify(listed) !== JSON.stringify(titratable)) {
      titratable = listed;
      redrawField("setup", nameOf(byControl("setup", "residues")));
    }

    const headline = node("p", "system-preview-headline");
    headline.appendChild(node("strong", "system-preview-count mono", `about ${count(e.particles)}`));
    headline.appendChild(document.createTextNode(
      ` particles in a ${e.box_shape} ${Number(e.width_nm).toFixed(2)} nm from face to face ` +
      `(${Math.round(e.volume_nm3).toLocaleString("en-US")} nm³)`));
    body.appendChild(headline);

    const rows = node("dl", "system-preview-rows");
    const row = (label, value, tone) => {
      rows.appendChild(node("dt", null, label));
      rows.appendChild(node("dd", tone ? `is-${tone}` : null, value));
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
    row("Ligands", e.ligands.length ? e.ligands.join(", ") : "none");
    row("Water", `${count(e.waters)} ${String(e.water_model).toUpperCase()}`);
    row("Ions", `${count(e.ions_positive)} ${e.positive_ion} and ${count(e.ions_negative)} ${e.negative_ion}`);
    if (e.refuses) {
      row("Padding", `${e.padding_nm} nm leaves the box narrower than twice the cutoff, ` +
          "and setup will refuse it: raise the padding, choose a cube, or lower the cutoff.", "warning");
    } else if (e.grows) {
      row("Padding", `grown from ${e.padding_nm} to ${Number(e.padding_used_nm).toFixed(2)} nm, ` +
          "so the box clears twice the cutoff with room for the barostat to shrink it", "note");
    } else {
      row("Padding", `${e.padding_nm} nm between the solute and its nearest periodic image`);
    }
    if (answer.forcefield && answer.forcefield.name) {
      row("Force field", answer.forcefield.description || answer.forcefield.name);
    }
    const time = answer.time || {};
    row("Time here", time.ok
      ? `about ${duration(time.seconds)}` + (time.runs > 1 ? ` for ${time.runs} runs` : "") +
        ` on ${time.platform || "this machine"}, ${count(time.steps)} steps in all`
      : "not known yet: this machine has not been timed", time.ok ? null : "muted");
    body.appendChild(rows);
    (e.notes || []).forEach((note) => body.appendChild(node("p", "builder-help", note)));
    body.appendChild(node("p", "builder-help",
      "Worked out from the structure and these settings. Setup's own numbers replace it once it has run."));
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
   * periodic images (the Wigner-Seitz cell). Its corners are where three of
   * the planes halfway to a neighbouring image meet, inside all the others;
   * an edge joins two corners on two planes. */
  function periodicCell(vectors) {
    const dot = (p, q) => p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
    const planes = [];
    for (let i = -1; i <= 1; i += 1) {
      for (let j = -1; j <= 1; j += 1) {
        for (let k = -1; k <= 1; k += 1) {
          if (!i && !j && !k) continue;
          const n = [0, 1, 2].map((d) => i * vectors[0][d] + j * vectors[1][d] + k * vectors[2][d]);
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

  let previewEngine = null;
  let previewAsked = 0;

  /* The structure setup keeps, copies included, inside the cell it builds,
   * to scale: how much water there is around the protein is seen, not read. */
  function drawSystem(answer) {
    const host = el("run-system-preview-view");
    if (!host) return;
    const e = answer && answer.ok ? answer.estimate : null;
    if (!e || !answer.drawing || typeof window.FastMDXViewerEngine === "undefined") {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const asked = ++previewAsked;
    void showSystem(host, answer, e, asked).catch((error) => {
      console.debug("system preview not rendered", error);
      if (asked === previewAsked) host.hidden = true;
    });
  }

  async function showSystem(host, answer, e, asked) {
    const tone = (name, fallback) => (getComputedStyle(document.documentElement)
      .getPropertyValue(name) || "").trim() || fallback;
    if (!previewEngine) {
      previewEngine = window.FastMDXViewerEngine.create(host,
        { transparent: true, quality: "auto", axes: false });
      (await previewEngine).on("click", (atom) => {
        const residue = atom && previewListed.get(`${atom.chain}:${atom.resi}`);
        if (residue) pickResidue(residue.key);
      });
    }
    const engine = await previewEngine;
    if (asked !== previewAsked) return;
    const cell = periodicCell(boxVectors(e.box_shape, Number(e.width_nm) * 10));
    const centre = e.centre_angstrom || [0, 0, 0];
    const at = (v) => [v[0] + centre[0], v[1] + centre[1], v[2] + centre[2]];
    const colour = tone("--accent-cyan", "#2698ba");
    const tubes = cell.edges.map(([i, j]) => ({ start: at(cell.vertices[i]),
      end: at(cell.vertices[j]), radius: 0.35, colour }));
    // Turned a little off the box's axes, so the cell reads as a solid, and
    // far enough back that all of it is in view.
    const reach = Math.max(1, ...cell.vertices.map((v) => Math.hypot(v[0], v[1], v[2])));
    const away = [0.42, 0.36, 0.83];
    const distance = 1.15 * reach / Math.sin(Math.PI / 8);
    const camera = { target: centre, up: [0, 1, 0],
      position: centre.map((value, axis) => value + distance * away[axis]) };
    engine.resize();
    const marks = markResidues(answer, tone);
    await engine.showScene({ text: answer.drawing, marks, tubes,
      camera, labelSize: Math.max(3, reach / 4),
      labelColour: tone("--text-primary", "#1f2328") });
    if (asked !== previewAsked) return;
    host.dataset.edges = String(cell.edges.length);
    host.dataset.residuesMarked = String(marks.length);
  }

  /* Each histidine as a small sphere on its alpha carbon, and every residue
   * given a state as a larger one labelled with it; a click on either picks
   * the residue for `setup.residue_states`. */
  let previewListed = new Map();

  function markResidues(answer, tone) {
    const listed = Array.isArray(answer.titratable) ? answer.titratable : [];
    const chosen = residueStatesChosen();
    const histidine = tone("--accent-orange", "#ffb86b");
    const set = tone("--accent-violet", "#a78bfa");
    const marks = [];
    previewListed = new Map(listed.map((residue) => [`${residue.chain}:${residue.number}`, residue]));
    listed.forEach((residue) => {
      const given = chosen[residue.key];
      if (residue.resname !== "HIS" && !given) return;
      marks.push({ chain: residue.chain, resi: residue.number, atom: "CA",
        // Spheres of 1.6 and 1.1 angstroms: Mol* scales a carbon's 1.7.
        size: (given ? 1.6 : 1.1) / 1.7, colour: given ? set : histidine,
        label: given ? `${residue.key} ${given}` : null });
    });
    return marks;
  }

  /* The size and the time of an answer from /api/preview-system, in two
   * sentences: the lines the Agent's plan ends with. */
  function describeCost(answer) {
    if (!answer || !answer.ok) return null;
    const e = answer.estimate;
    let size = `about ${count(e.particles)} particles in a ${e.box_shape} ` +
      `${Number(e.width_nm).toFixed(1)} nm from face to face`;
    if (e.grows) size += `, the padding grown to ${Number(e.padding_used_nm).toFixed(2)} nm for the cutoff`;
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

  async function previewCost(body) {
    try {
      const response = await fetch("/api/preview-system", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || currentState()),
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
    const wrap = wrapOf(phase, setting);
    if (!wrap) return;
    const group = wrap.closest(".builder-group");
    if (group) group.classList.add("is-open");
    wrap.scrollIntoView({ block: "center", behavior: "smooth" });
    const input = wrap.querySelector("input, select, textarea");
    if (input) input.focus({ preventScroll: true });
    wrap.classList.add("is-pointed");
    setTimeout(() => wrap.classList.remove("is-pointed"), 1600);
  }

  // ------------------------------------------------- find, and changed only

  /* Typed words show only the settings and analyses they name, from every
   * step, folded or not; Escape or an empty box shows them all again. */
  function applyFind() {
    const words = state.find.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const root = el("run-settings");
    if (!root) return;
    const finding = words.length > 0;
    root.classList.toggle("is-finding", finding);
    root.classList.toggle("is-only-changed", state.onlyChanged && !finding);
    let found = 0;
    root.querySelectorAll(".builder-field[data-find]").forEach((wrap) => {
      const hit = finding && words.every((word) => wrap.dataset.find.includes(word));
      wrap.classList.toggle("is-found", hit);
      if (hit) found += 1;
    });
    root.querySelectorAll(".builder-group").forEach((group) => {
      const any = finding ? group.querySelector(".is-found")
        : state.onlyChanged ? group.querySelector(".builder-field.is-changed") : true;
      group.classList.toggle("is-empty-here", !any);
    });
    root.querySelectorAll(".builder-step").forEach((step) => {
      const any = finding ? step.querySelector(".is-found")
        : state.onlyChanged ? step.querySelector(".builder-field.is-changed") : true;
      step.classList.toggle("is-empty-here", !any);
    });
    const said = el("run-find-said");
    if (said) {
      said.hidden = !finding && !state.onlyChanged;
      said.textContent = finding
        ? (found ? `${found} setting${found === 1 ? "" : "s"} found. Escape shows every setting.`
          : "No setting by that name.")
        : state.onlyChanged ? "Only the settings changed are shown." : "";
    }
  }

  // --------------------------------------------------------------- the draft

  /* The form as it stands, saved in this browser for this person, so a
   * reload or a visit to another page loses nothing. Nothing leaves the
   * browser; a study run or Reset discards it. */
  const DRAFT_KEY = "fmx.builderDraft";
  let draftTimer = null;
  let draftReady = false;
  let draftEndWired = false;

  function draftKey() {
    const where = (state.schema && state.schema.workspace) || "";
    return DRAFT_KEY + (where ? ":" + where : "");
  }

  function saveDraft() {
    if (!draftReady) return;
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      const draft = {
        start: state.start, phases: Array.from(state.phases), values: state.values,
        analyses: Array.from(state.analyses), analysisOptions: state.analysisOptions,
        sweep: state.sweep, decisions: state.decisions, full: Boolean(state.full),
        analysisMode: state.analysisMode, lengthMode: state.lengthMode || null,
        systems: state.systems.map((row) => ({ id: row.id, system: row.system, own: row.own || {} })),
        output: (el("run-output") && el("run-output").value) || "",
        trajectory: (el("run-trajectory") && el("run-trajectory").value) || "",
        topology: (el("run-topology") && el("run-topology").value) || "",
        configPath: (el("run-config-path") && el("run-config-path").value) || "",
      };
      try {
        const written = JSON.stringify(draft);
        const before = window.localStorage.getItem(draftKey());
        if (!draft.start) window.localStorage.removeItem(draftKey());
        else window.localStorage.setItem(draftKey(), written);
        const said = el("run-draft");
        if (said) {
          said.hidden = !draft.start;
          // Each save that changed the draft is said again: the class is
          // taken off and put back so its animation plays once more, and
          // taken off as it ends, so nothing shown again replays it.
          if (!draftEndWired) {
            draftEndWired = true;
            said.addEventListener("animationend", (event) => {
              if (event.animationName === "builder-saved") said.classList.remove("just-saved");
            });
          }
          said.classList.remove("just-saved");
          if (draft.start && written !== before) {
            void said.offsetWidth;
            said.classList.add("just-saved");
          }
        }
      } catch (error) {
        // No storage here: the form works without it, and says no save.
        const said = el("run-draft");
        if (said) {
          said.hidden = true;
          said.classList.remove("just-saved");
        }
      }
    }, 300);
  }

  function restoreDraft() {
    let draft = null;
    try { draft = JSON.parse(window.localStorage.getItem(draftKey()) || "null"); }
    catch (error) { draft = null; }
    if (!draft || !draft.start || !STARTING_POINTS[draft.start]) return false;
    forgetWhatWasSetAside();
    state.start = draft.start;
    state.phases = new Set(draft.phases || []);
    state.values = draft.values || {};
    state.analyses = new Set(draft.analyses || []);
    state.analysisOptions = draft.analysisOptions || {};
    state.sweep = draft.sweep || [];
    state.decisions = draft.decisions || {};
    state.full = Boolean(draft.full);
    state.analysisMode = draft.analysisMode || "all";
    state.lengthMode = draft.lengthMode || null;
    state.systems = (draft.systems || []).map((row) => ({ id: row.id || "", system: row.system || "", own: row.own || {}, facts: null }));
    if (el("run-output")) el("run-output").value = draft.output || "";
    if (el("run-trajectory")) el("run-trajectory").value = draft.trajectory || "";
    if (el("run-topology")) el("run-topology").value = draft.topology || "";
    if (el("run-config-path")) el("run-config-path").value = draft.configPath || "";
    const said = el("run-draft");
    if (said) said.hidden = false;
    return true;
  }

  function discardDraft() {
    try { window.localStorage.removeItem(draftKey()); } catch (error) { /* nothing to discard */ }
    const said = el("run-draft");
    if (said) said.hidden = true;
  }

  // ---------------------------------------------------------- the actions

  /* The config for the form, its refusal said on its field; or for a
   * body given, said nowhere on the form. */
  async function fetchConfig(body, onTheForm = true) {
    const response = await fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || currentState()),
    });
    const built = await response.json();
    if (onTheForm && !body) sayRefusal(built);
    return built;
  }

  const sayOnTheForm = (words) => text(el("run-note"), words);

  /* Save a file where the person chooses. showSaveFilePicker asks for a
   * location; where the browser lacks it, an anchor with a download
   * attribute saves to the default folder. */
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

  async function download(body, say = sayOnTheForm) {
    const built = await fetchConfig(body);
    if (!built.ok) {
      say(built.error);
      return;
    }
    const saved = await saveAs(built.yaml, "fastmdxplora_config.yml", "text/yaml");
    say(saved ? `${built.settings_changed} setting(s) written.` : "Not saved.");
  }

  /* The same study in its other two languages, derived on the server from
   * the same schema the form was built from. */
  async function copyCommand(body, say = sayOnTheForm, button = null) {
    const built = await fetchConfig(body);
    if (!built.ok) {
      say(built.error);
      return;
    }
    if (!built.command) {
      say("This study cannot be said as one command; use the config file.");
      return;
    }
    // The button ticks and the notice says so; with none, the line does.
    // A note left from an earlier try is not left beside the tick.
    if (button) say("");
    const refused = body ? "Clipboard unavailable here" : "Clipboard unavailable here; the command is shown above.";
    if (await window.FastMDXIcons.copy(button, built.command, "Command copied", refused)) {
      if (!button) say("Command copied.");
      return;
    }
    // A page served over plain HTTP -- an SSH tunnel, commonly -- may have
    // no clipboard. The command is shown instead, still selectable.
    if (!body) {
      showCodeTab("command");
      text(el("run-command-preview"), built.command);
      if (!button) say(refused);
    } else {
      say(refused + ": " + built.command);
    }
  }

  async function downloadScript(body, say = sayOnTheForm) {
    const built = await fetchConfig(body);
    if (!built.ok) {
      say(built.error);
      return;
    }
    const saved = await saveAs(built.script, "fastmdxplora_study.py", "text/x-python");
    if (!saved) say("Not saved.");
  }

  function showCodeTab(which) {
    document.querySelectorAll("#run-summary-panel .builder-tabs [data-code]").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab.dataset.code === which));
    });
    document.querySelectorAll("#run-summary-panel [data-code-pane]").forEach((pane) => {
      pane.hidden = pane.dataset.codePane !== which;
    });
  }

  /* A config that has been elsewhere can be run as it stands, or opened and
   * changed. Opening it never writes to it: anything altered is saved as a
   * new file and the original is left alone. */
  async function loadConfigIntoForm() {
    const path = el("run-config-path").value.trim();
    if (!path) return;
    const response = await fetch("/api/load-config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const loaded = await response.json();
    if (!loaded.ok) {
      text(el("run-config-verdict"), loaded.error);
      return;
    }
    applyLoadedState(loaded.state, {
      from: path,
      note: `Opened ${path}. Changes here are saved as a new file; that one is left as it is.`,
    });
  }

  /* Fill the form in from a config the server has already mapped to form
   * state. The Agent panel uses it too: the mapping from a config to the
   * form is the server's, and a copy here would drift. */
  function applyLoadedState(from, options = {}) {
    if (!from) return undefined;
    if (!state.schema) {
      // A config that arrives before the settings waits for them.
      return loadSchema().then(() => applyLoadedState(from, options));
    }
    const { from: origin, note } = options;
    openedAConfig = true;
    const snap = snapshotOfLoaded(from);
    state.start = snap.start;
    state.phases = new Set(snap.phases);
    state.values = snap.values;
    state.decisions = snap.decisions;
    forgetWhatWasSetAside();
    state.editing = null;
    state.analyses = new Set(snap.analyses);
    state.analysisMode = state.analyses.size ? "only" : "all";
    state.analysisOptions = snap.analysisOptions;
    state.sweep = snap.sweep;
    state.systems = snap.systems.map((row) => Object.assign(row, { facts: null }));
    state.full = false;
    stoppingDraft = null;
    stashedStop = null;
    residuesPending = [];
    state.lengthMode = null;
    state.samplingChosen = null;
    state.refused = null;
    if (origin) state.loadedFrom = origin;
    state.estimate = null;

    if (el("run-trajectory")) el("run-trajectory").value = snap.trajectory;
    if (el("run-topology")) el("run-topology").value = snap.topology;
    if (el("run-output")) el("run-output").value = snap.output;
    renderAll();
    describeOutput();
    if (note) text(el("run-note"), note);
    saveDraft();
    return undefined;
  }

  /* The actions the Agent panel offers on a config it wrote, on that config
   * and not on the form: the file, the command, the script and the cost,
   * by the form's own derivation, with somebody's draft left as it is. */
  function forLoaded(from, options = {}) {
    const body = () => Object.assign(bodyOf(snapshotOfLoaded(from)), { full: Boolean(options.full && options.full()) });
    const say = options.say || (() => {});
    return {
      fetchConfig: () => fetchConfig(body(), false),
      download: () => download(body(), say),
      // The Agent card's button, to tick, when it is given.
      copyCommand: (button) => copyCommand(body(), say, button || null),
      downloadScript: () => downloadScript(body(), say),
      previewCost: () => previewCost(body()),
    };
  }

  /* Complete studies to start from (starters.py): each with what it is for
   * and what it will run. Chosen, it is loaded as any config is, and the
   * note says what to change first. */
  const STARTER_FACTS = ["System", "Production", "Sampling", "Stops when", "Membrane"];

  function drawStarters() {
    const host = el("run-starters");
    const card = el("run-starters-card");
    const starters = (state.schema && state.schema.starters) || [];
    if (!host || !card) return;
    host.replaceChildren();
    card.hidden = !starters.length;
    starters.forEach((starter) => {
      const tile = node("button", "starter builder-chip");
      tile.type = "button";
      tile.setAttribute("role", "listitem");
      tile.dataset.starter = starter.id;
      tile.appendChild(node("span", "starter-title", starter.title));
      const what = node("span", "starter-what", starter.what);
      const facts = node("span", "starter-facts mono", (starter.plan || [])
        .filter((line) => STARTER_FACTS.includes(line.label))
        .map((line) => line.value).join(" · "));
      tile.title = `${starter.what}\n${facts.textContent}`;
      tile.append(what, facts);
      tile.addEventListener("click", () => startFrom(starter, tile));
      host.appendChild(tile);
    });
  }

  async function startFrom(starter, tile) {
    const note = el("run-starters-note");
    let loaded = null;
    try {
      const response = await fetch("/api/load-config", {
        method: "POST", headers: { "Content-Type": "application/json" },
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
    text(note, `Started from "${starter.title}". Change first: ${String(starter.change || "").replace(/`/g, "")}`);
  }

  async function runAsItStands() {
    const path = el("run-config-path").value.trim();
    if (!path) return;
    const button = el("run-as-is");
    button.disabled = true;
    text(el("run-note"), "Starting…");
    const response = await fetch("/api/run-config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, output: el("run-output").value.trim() }),
    });
    const started = await response.json();
    if (!started.ok) {
      text(el("run-note"), started.error || "Could not start.");
      button.disabled = false;
      return;
    }
    text(el("run-note"), `Running ${started.config_path} as it stands.`
      + sharedSaid(started));
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
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const verdict = await response.json();
    const knowing = (verdict.ok && verdict.worth_knowing) || [];
    // "Runs." in green beside a timestep its run fails on said it would
    // run well: with something worth knowing it is said as the form says
    // it, checks passing, in the colour of advice.
    text(note, verdict.ok
      ? `${knowing.length ? "Checks pass" : "Runs"}. ${verdict.phases.join(" → ")}, `
        + `${verdict.systems} system(s), ${verdict.settings_named} setting(s) named.`
        + (knowing.length ? ` Worth knowing: ${knowing.join(" ")}` : "")
      : verdict.error);
    // A setting named in the advice in code, as the form says it.
    if (verdict.ok && knowing.length) withCodeIn(note);
    note.dataset.ok = verdict.ok && knowing.length ? "advice" : String(Boolean(verdict.ok));
    state.configVerdict = verdict;
    // Opening and running are only offered once the file is known to be sound.
    ["run-open-config", "run-as-is", "run-as-is-elsewhere"].forEach((id) => {
      const button = el(id);
      if (button) button.disabled = !verdict.ok;
    });
    updateSummary();
    saveDraft();
    return verdict;
  }

  /* The Stop button appears only while something is running, and the
   * dashboard is what knows: `process_running`, not `active_run`, which
   * stays true after a run fails. */
  function watchForARun() {
    if (!window.FastMDXDashboard || !window.FastMDXDashboard.on) return;
    window.FastMDXDashboard.on("app-state", (detail) => {
      // Stop the run is stop-run.js's, asked once more before it acts.
      if (detail && detail.status === "failed" && detail.error) {
        const note = el("run-note");
        if (note && note.textContent !== detail.error) {
          text(note, detail.error);
          note.dataset.ok = "false";
        }
      }
    });
  }

  /* Where it was started beside other studies on this computer: the GPU it
   * was given and that the computer is shared. */
  function sharedSaid(started) {
    const lines = Array.isArray(started.shared)
      ? started.shared.filter((line) => typeof line === "string") : [];
    return lines.length ? " " + lines.join(" ") : "";
  }

  async function start() {
    const button = el("run-start-button");
    if (!ready()) return;
    button.disabled = true;
    const phone = el("run-phone-start");
    if (phone) phone.disabled = true;
    text(el("run-note"), "Starting…");
    let started;
    try {
      const response = await fetch("/api/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentState()),
      });
      started = await response.json();
    } catch (error) {
      text(el("run-note"), "Could not reach the server.");
      button.disabled = false;
      if (phone) phone.disabled = false;
      return;
    }
    if (!started.ok) {
      text(el("run-note"), started.error || "Could not start.");
      sayRefusal(started);
      button.disabled = false;
      if (phone) phone.disabled = false;
      return;
    }
    sayRefusal(started);
    // The study is now its config file, saved beside its results; the
    // draft has done its work.
    discardDraft();
    text(el("run-note"), `Running. Config saved to ${started.config_path}`
      + sharedSaid(started));
    if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
      window.FastMDXDashboard.navigate("overview");
    }
  }

  /* Hosted behind a service that runs studies on its own compute: the config
   * is saved in the workspace beside the folder it will write, and the
   * service's page opens with both filled in. The person confirms there. */
  function openRunsPage(base, config, output) {
    const query = new URLSearchParams({ config });
    if (output) query.set("output", output);
    window.location.assign(base + "?" + query.toString());
  }

  async function runElsewhere() {
    const button = el("run-elsewhere");
    button.disabled = true;
    text(el("run-note"), "Saving the config…");
    let saved;
    try {
      const response = await fetch("/api/save-config", {
        method: "POST", headers: { "Content-Type": "application/json" },
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
    text(el("run-note"), `Saved ${saved.config}. Opening the page that runs it…`);
    openRunsPage(button.dataset.runsUrl, saved.config, saved.output);
  }

  function runAsItStandsElsewhere() {
    const path = el("run-config-path").value.trim();
    if (!path) return;
    openRunsPage(el("run-as-is-elsewhere").dataset.runsUrl, path, el("run-output").value.trim());
  }

  function resetEverything() {
    state.phases = state.start ? new Set(STARTING_POINTS[state.start].phases) : new Set();
    state.values = {};
    stoppingDraft = null;
    stashedStop = null;
    residuesPending = [];
    state.lengthMode = null;
    state.samplingChosen = null;
    state.analyses.clear();
    state.analysisMode = "all";
    state.analysisOptions = {};
    state.sweep = [];
    state.decisions = {};
    forgetWhatWasSetAside();
    state.editing = null;
    state.full = false;
    state.open.clear();
    state.refused = null;
    state.systems = state.start === "structure"
      ? state.systems.slice(0, 1).map((row) => ({ id: "", system: row.system, own: {}, facts: row.facts }))
      : [];
    const output = el("run-output");
    if (output) output.value = "";
    renderAll();
    text(el("run-note"), "Every setting is back to its default.");
    // The draft written over, not discarded first: the note stays, and
    // plays only where Reset changed what was saved.
    saveDraft();
  }

  // ------------------------------------------------------------------- wire

  function renderAll() {
    renderStart();
    renderSystems();
    renderPhases();
    renderSteps();
    updateSummary();
  }

  /* Where the results will actually be written, for whatever is in the box. */
  function describeOutput() {
    const box = el("run-output");
    const where = (state.schema && state.schema.workspace) || "";
    if (!box) return;
    const typed = box.value.trim();
    const name = typed || defaultOutput();
    // A path is absolute on this machine, not on the one this was written
    // on; the server decides for real, and this only has to agree.
    const absolute = name.startsWith("/") || name.startsWith("~") || /^[A-Za-z]:[\\/]/.test(name);
    const separator = where.includes("\\") ? "\\" : "/";
    const full = absolute ? name : (where ? `${where}${separator}${name}` : name);
    text(el("run-output-note"), `Results will be saved in ${full}`);
  }

  /* A trajectory usually sits beside the structure it moves: found there
   * where it can be, rather than asked for. */
  async function findStructureBeside() {
    const trajectory = el("run-trajectory").value.trim();
    const topology = el("run-topology");
    if (!trajectory || !topology || topology.value.trim()) return;
    const folder = trajectory.replace(/[/\\][^/\\]*$/, "");
    if (!folder) return;
    try {
      const response = await fetch("/api/inspect-directory?path=" + encodeURIComponent(folder));
      const found = await response.json();
      if (found.ok && found.suggestion && found.suggestion.topology) {
        topology.value = found.suggestion.topology;
        afterChange();
      }
    } catch (error) {
      // Not finding one is not a failure: the field is still there to type in.
    }
  }

  /* The step in view, in the bar, as the column scrolls. */
  function followTheSteps() {
    const links = Array.from(document.querySelectorAll("#run-steps .builder-step-link"));
    links.forEach((link) => link.addEventListener("click", () => {
      const step = el("run-step-" + link.dataset.step);
      if (step && !step.hidden) step.scrollIntoView({ block: "start", behavior: "smooth" });
    }));
    if (typeof IntersectionObserver === "undefined") return;
    const seen = new Map();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => seen.set(entry.target.dataset.step, entry.isIntersecting));
      const current = ["start", "system", "protocol", "analysis", "review"].find((step) => seen.get(step));
      if (!current) return;
      links.forEach((link) => link.classList.toggle("is-current", link.dataset.step === current));
    }, { rootMargin: "-30% 0px -60% 0px" });
    document.querySelectorAll("section.builder-step").forEach((step) => observer.observe(step));
  }

  function attach() {
    if (!el("run-start")) return;

    ["run-trajectory", "run-topology", "run-output"].forEach((id) => {
      const input = el(id);
      if (input) input.addEventListener("input", afterChange);
    });
    const output = el("run-output");
    if (output) output.addEventListener("input", describeOutput);
    const trajectory = el("run-trajectory");
    if (trajectory) trajectory.addEventListener("change", findStructureBeside);
    const addSystem = el("run-add-system");
    if (addSystem) addSystem.addEventListener("click", () => {
      state.systems.push({ id: "", system: "", own: {}, facts: null });
      renderSystems();
      const rows = document.querySelectorAll("#run-systems .builder-system-structure");
      if (rows.length) rows[rows.length - 1].focus();
      afterChange();
    });

    const configPath = el("run-config-path");
    if (configPath) configPath.addEventListener("change", checkConfigFile);
    const wire = (id, handler) => { const button = el(id); if (button) button.addEventListener("click", handler); };
    wire("run-check-config", checkConfigFile);
    wire("run-open-config", loadConfigIntoForm);
    wire("run-as-is", runAsItStands);
    wire("run-as-is-elsewhere", runAsItStandsElsewhere);
    wire("run-elsewhere", runElsewhere);
    wire("run-download", () => download());
    wire("run-copy-command", () => copyCommand(undefined, sayOnTheForm, el("run-copy-command")));
    wire("run-download-script", () => downloadScript());
    wire("run-start-button", start);
    wire("run-phone-start", start);
    wire("run-reset", resetEverything);
    wire("run-only-changed", () => {
      state.onlyChanged = !state.onlyChanged;
      const toggle = el("run-only-changed");
      toggle.setAttribute("aria-pressed", String(state.onlyChanged));
      toggle.textContent = state.onlyChanged ? "Show every setting" : "Show only these";
      applyFind();
    });
    document.querySelectorAll("#run-summary-panel .builder-tabs [data-code]").forEach((tab) => {
      tab.addEventListener("click", () => showCodeTab(tab.dataset.code));
    });
    const find = el("run-find");
    if (find) {
      find.addEventListener("input", () => { state.find = find.value; applyFind(); });
      find.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { find.value = ""; state.find = ""; applyFind(); }
      });
    }
    // Run with the keyboard from anywhere on the page.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return;
      if (document.documentElement.getAttribute("data-page") !== "run") return;
      if (event.target && event.target.closest && event.target.closest(".builder-decision-edit")) return;
      const button = el("run-start-button");
      if (button && !button.disabled) { event.preventDefault(); start(); }
    });
    // The Agent beside the builder, not in its place: what it drafts opens
    // here, and the form stays as it was.
    const toTheAgent = document.querySelector("#run-step-start .builder-agent-link");
    if (toTheAgent) {
      toTheAgent.addEventListener("click", (event) => {
        const beside = window.FastMDXAgentBeside;
        if (!beside) return;
        event.preventDefault();
        if (!beside.isOpen()) beside.open();
        const box = el("agent-request");
        if (box) box.focus();
      });
    }
    watchForARun();
    followTheSteps();

    loadSchema().then(() => {
      if (!openedAConfig) restoreDraft();
      draftReady = true;
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
    state, currentState, PHASES, STARTING_POINTS, applyLoadedState, setStart,
    /* The four actions the Agent panel offers on a config it just wrote.
     * They read the builder's state, which the panel loads silently first,
     * so the file, the command and the script are the same ones the builder
     * would produce -- one derivation, two doors. */
    fetchConfig, download, copyCommand, downloadScript,
    renderPreview, describeCost, previewCost, boxVectors, periodicCell, pickResidue,
    sayRefusal, renderSettings, showSetting, forLoaded, bodyOf, snapshotOfLoaded,
  };
})();
