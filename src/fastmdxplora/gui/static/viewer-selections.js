/* Selections of the Viewer, as PyMOL keeps them, saved with the study.
 *
 * Atoms are selected by typing a selection in MDTraj's language (read by
 * the server in the very structure the Viewer renders, so the atoms are the
 * ones shown), by the sequence, or by clicking in the structure. What is
 * selected can be named: a named selection is listed with its colour,
 * whether it is shown, a representation of its own (sticks, spheres, lines,
 * a surface, a cartoon) and labels for its residues, each changed in its
 * row, and kept with the study (/api/viewer-selections). A selection made
 * of residues is found again by their chains, numbers and names in whatever
 * the Viewer renders; a typed one is read again in it. Hiding one makes its
 * atoms transparent in every representation; its colour is painted over
 * every representation of its atoms.
 */
(function () {
  "use strict";

  var PALETTE = ["#e69f00", "#56b4e9", "#009e73", "#f0e442", "#0072b2", "#d55e00", "#cc79a7"];
  var SHAPES = [["none", "As shown"], ["sticks", "Sticks"], ["spheres", "Spheres"],
    ["lines", "Lines"], ["surface", "Surface"], ["cartoon", "Cartoon"]];

  var state = {
    named: [],
    atoms: new Map(),     // name -> atoms in what is rendered now
    notes: new Map(),     // name -> why it found no atoms there
    signature: "",
    asked: 0,
    typed: new Map(),     // signature + expression -> the server's answer
  };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE ? v.STATE.engine : null;
  }

  function said(text) {
    var line = byId("sel-said");
    if (line) line.textContent = text || "";
  }

  function announce(text) {
    var live = byId("sr-live");
    if (live) live.textContent = text;
  }

  function plural(n, one) { return n + " " + one + (n === 1 ? "" : "s"); }

  /* ---------------------------------------------------------------- */
  /* What is rendered                                                  */
  /* ---------------------------------------------------------------- */

  /** The named selections as the engine renders them, for what is rendered
   * now; none until their atoms are known in it. */
  function forScene() {
    var v = viewer();
    if (!v || !state.named.length || state.signature !== v.modelSignature()) return null;
    return state.named.map(function (one) {
      return {
        atoms: state.atoms.get(one.name) || [],
        colour: one.colour ? parseInt(one.colour.slice(1), 16) : null,
        shown: one.shown,
        representation: one.representation,
        labelled: one.labelled,
      };
    });
  }

  function shows(one) {
    return (state.atoms.get(one.name) || []).length
      && (!one.shown || one.colour || one.representation !== "none" || one.labelled);
  }

  /** Where the server reads a typed selection: what the Viewer renders. */
  function where() {
    var v = viewer();
    var model = v && v.STATE ? v.STATE.model : null;
    var of = model ? model.of : "structure";
    return "of=" + encodeURIComponent(of) + "&solvent="
      + (of === "structure" && v.needsFullTopology() ? "1" : "0");
  }

  function typedAtoms(expression, signature) {
    var key = signature + "\u0000" + expression;
    if (state.typed.has(key)) return Promise.resolve(state.typed.get(key));
    return fetch("/api/viewer-atoms?" + where() + "&expression=" + encodeURIComponent(expression),
      { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (answer) {
        var e = engine();
        if (answer && answer.ok && e && answer.n_atoms !== e.atomCount()) {
          answer = { ok: false, reason: "MDTraj read " + answer.n_atoms + " atoms where the Viewer"
            + " renders " + e.atomCount() + ", so their places cannot be matched." };
        }
        if (answer && answer.ok) {
          state.typed.set(key, answer);
          while (state.typed.size > 40) state.typed.delete(state.typed.keys().next().value);
        }
        return answer;
      });
  }

  /** Each named selection's atoms found again in what is rendered now, and
   * the Viewer rendered again where any of them shows. */
  function resolve() {
    var v = viewer();
    var e = engine();
    if (!v || !e || !v.STATE.model) return Promise.resolve();
    var signature = v.modelSignature();
    var asked = ++state.asked;
    var found = new Map();
    var notes = new Map();
    var waits = state.named.map(function (one) {
      if (one.kind === "residues") {
        found.set(one.name, e.atomsOfResidues(one.residues));
        return Promise.resolve();
      }
      return typedAtoms(one.expression, signature).then(function (answer) {
        found.set(one.name, answer && answer.ok ? answer.atoms : []);
        if (!answer || !answer.ok) notes.set(one.name, (answer && answer.reason) || "");
      });
    });
    return Promise.all(waits).then(function () {
      if (asked !== state.asked || signature !== v.modelSignature()) return;
      var before = forScene();
      state.atoms = found;
      state.notes = notes;
      state.signature = signature;
      list();
      if (state.named.some(shows) || (before && before.some(function (one) {
        return one.atoms.length;
      }))) {
        return v.restyle();
      }
    });
  }

  /* ---------------------------------------------------------------- */
  /* Typing a selection                                                */
  /* ---------------------------------------------------------------- */

  function onTyped(event) {
    event.preventDefault();
    var input = byId("sel-expression");
    var expression = input ? input.value.trim() : "";
    var v = viewer();
    if (!v || !v.STATE.model || !engine()) { said("The Viewer is not rendering a structure."); return; }
    if (!expression) { said("Type a selection, such as: resSeq 10 to 25 and backbone."); return; }
    said("Reading the selection…");
    typedAtoms(expression, v.modelSignature()).then(function (answer) {
      if (!answer || !answer.ok) {
        said((answer && answer.reason) || "MDTraj could not read that selection.");
        return;
      }
      if (!answer.atoms.length) {
        said("No atom of the structure shown matches " + expression + ".");
        return;
      }
      var text = "Selected by " + expression + ": " + plural(answer.atoms.length, "atom")
        + " in " + plural(answer.residues, "residue") + ".";
      v.selectAtoms(answer.atoms, { expression: expression });
      if (window.FastMDXSequence) window.FastMDXSequence.markAtoms(answer.atoms, text);
      said(text);
      announce(text);
      offerName();
    });
  }

  /* ---------------------------------------------------------------- */
  /* Naming one                                                        */
  /* ---------------------------------------------------------------- */

  function freeName() {
    for (var n = 1; ; n += 1) {
      var name = "sele" + n;
      if (!state.named.some(function (one) { return one.name === name; })) return name;
    }
  }

  function offerName() {
    var v = viewer();
    var current = v && v.STATE ? v.STATE.selection : null;
    var keep = byId("sel-keep");
    var name = byId("sel-name");
    if (keep) keep.disabled = !current;
    if (name && !name.value) name.placeholder = freeName();
  }

  function post(body) {
    return fetch("/api/viewer-selections", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, reason: "HTTP " + r.status }; });
    }).catch(function () { return { ok: false, reason: "The server did not answer." }; });
  }

  function keep() {
    var v = viewer();
    var current = v && v.STATE ? v.STATE.selection : null;
    if (!current) { said("Select something first."); return; }
    var input = byId("sel-name");
    var name = (input && input.value.trim()) || freeName();
    var selection = current.expression
      ? { kind: "expression", expression: current.expression }
      : { kind: "residues", residues: current.residues.map(function (r) {
        return [r.chain || "", r.resi, r.icode || "", r.resn];
      }) };
    if (selection.kind === "residues" && !selection.residues.length) {
      said("Only residues or a typed selection can be named.");
      return;
    }
    var existing = state.named.find(function (one) { return one.name === name; });
    selection.colour = existing ? existing.colour : PALETTE[state.named.length % PALETTE.length];
    selection.shown = true;
    selection.representation = existing ? existing.representation : "sticks";
    selection.labelled = existing ? existing.labelled : false;
    post({ name: name, selection: selection }).then(function (answer) {
      if (!answer || !answer.ok) { said((answer && answer.reason) || "Not saved."); return; }
      state.named = answer.selections;
      if (input) input.value = "";
      said("Named " + name + ".");
      announce("Named the selection " + name + ".");
      offerName();
      resolve();
    });
  }

  /** One named selection changed and saved, and rendered again. */
  function change(name, changes) {
    var one = state.named.find(function (s) { return s.name === name; });
    if (!one) return;
    Object.assign(one, changes);
    list();
    var v = viewer();
    if (v) v.restyle();
    var body = Object.assign({}, one);
    delete body.name;
    post({ name: name, selection: body }).then(function (answer) {
      if (!answer || !answer.ok) said((answer && answer.reason) || "Not saved.");
    });
  }

  function forget(name) {
    post({ action: "delete", name: name }).then(function (answer) {
      if (!answer || !answer.ok) { said((answer && answer.reason) || "Not forgotten."); return; }
      var shown = state.named.some(function (one) { return one.name === name && shows(one); });
      state.named = answer.selections;
      state.atoms.delete(name);
      list();
      said("Forgot " + name + ".");
      var v = viewer();
      if (shown && v) v.restyle();
    });
  }

  function pick(name) {
    var v = viewer();
    var atoms = state.atoms.get(name) || [];
    var one = state.named.find(function (s) { return s.name === name; });
    if (!v || !one || !atoms.length) { said("No atom of " + name + " is in the structure shown."); return; }
    var residues = one.kind === "residues" ? one.residues.map(function (r) {
      return { chain: r[0], resi: r[1], icode: r[2], resn: r[3] };
    }) : [];
    v.selectAtoms(atoms, { residues: residues, expression: one.kind === "expression"
      ? one.expression : null });
    var text = "Selected " + name + ": " + plural(atoms.length, "atom") + ".";
    if (window.FastMDXSequence) window.FastMDXSequence.markAtoms(atoms, text);
    said(text);
    offerName();
  }

  /* ---------------------------------------------------------------- */
  /* The list                                                           */
  /* ---------------------------------------------------------------- */

  function button(className, label, title, svgPath) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = className;
    b.setAttribute("aria-label", label);
    b.title = title;
    if (svgPath) {
      b.innerHTML = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="'
        + svgPath + '"/></svg>';
    }
    return b;
  }

  function list() {
    var host = byId("sel-list");
    if (!host) return;
    host.replaceChildren();
    state.named.forEach(function (one) {
      var atoms = state.atoms.get(one.name);
      var item = document.createElement("li");
      item.className = "sel-item";
      item.dataset.name = one.name;
      var top = document.createElement("div");
      top.className = "sel-line";
      var eye = button("icon-btn sel-eye", "Shown: " + one.name, "Show or hide these atoms",
        one.shown ? "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6"
          : "M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3 3.6M6.6 6.6C3.8 8.4 2 12 2 12s4 7 10 7a9.6 9.6 0 0 0 4.2-1");
      eye.setAttribute("aria-pressed", one.shown ? "true" : "false");
      eye.addEventListener("click", function () { change(one.name, { shown: !one.shown }); });
      var colour = document.createElement("input");
      colour.type = "color";
      colour.className = "sel-colour";
      colour.value = one.colour || "#9a9aa2";
      colour.setAttribute("aria-label", "Colour of " + one.name);
      colour.title = "The colour of these atoms in every representation";
      colour.addEventListener("change", function () { change(one.name, { colour: colour.value }); });
      var name = document.createElement("button");
      name.type = "button";
      name.className = "sel-pick";
      name.textContent = one.name;
      name.title = one.kind === "expression" ? "Select " + one.expression
        : "Select these " + plural(one.residues.length, "residue");
      name.addEventListener("click", function () { pick(one.name); });
      var count = document.createElement("span");
      count.className = "sel-count muted small mono";
      count.textContent = atoms == null ? "" : plural(atoms.length, "atom");
      if (state.notes.get(one.name)) count.title = state.notes.get(one.name);
      var centre = button("icon-btn sel-centre", "Centre on " + one.name, "Centre the camera on these atoms",
        "M12 2v4M12 18v4M2 12h4M18 12h4M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8");
      centre.disabled = !(atoms && atoms.length);
      centre.addEventListener("click", function () {
        var e = engine();
        if (e) e.focus(state.atoms.get(one.name) || []);
      });
      var drop = button("icon-btn sel-forget", "Forget " + one.name, "Forget this selection",
        "M6 6l12 12M18 6L6 18");
      drop.addEventListener("click", function () { forget(one.name); });
      top.append(eye, colour, name, count, centre, drop);
      var bottom = document.createElement("div");
      bottom.className = "sel-line";
      var shape = document.createElement("select");
      shape.className = "sel-shape";
      shape.setAttribute("aria-label", "Representation of " + one.name);
      SHAPES.forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        shape.appendChild(option);
      });
      shape.value = one.representation;
      shape.addEventListener("change", function () { change(one.name, { representation: shape.value }); });
      var tinted = document.createElement("label");
      tinted.className = "chip-toggle micro";
      var tint = document.createElement("input");
      tint.type = "checkbox";
      tint.className = "sel-tinted";
      tint.checked = !!one.colour;
      tint.addEventListener("change", function () {
        change(one.name, { colour: tint.checked ? colour.value : null });
      });
      tinted.append(tint, document.createTextNode("Coloured"));
      var labelled = document.createElement("label");
      labelled.className = "chip-toggle micro";
      var labels = document.createElement("input");
      labels.type = "checkbox";
      labels.className = "sel-labels";
      labels.checked = one.labelled;
      labels.addEventListener("change", function () { change(one.name, { labelled: labels.checked }); });
      labelled.append(labels, document.createTextNode("Labels"));
      bottom.append(shape, tinted, labelled);
      item.append(top, bottom);
      host.appendChild(item);
    });
  }

  function load() {
    return fetch("/api/viewer-selections", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (answer) {
        state.named = answer && answer.ok && Array.isArray(answer.selections) ? answer.selections : [];
      })
      .catch(function () { state.named = []; })
      .then(function () {
        state.signature = "";
        list();
        offerName();
        return resolve();
      });
  }

  function wire() {
    var form = byId("sel-form");
    if (!form) return;
    form.addEventListener("submit", onTyped);
    var keeper = byId("sel-keep");
    if (keeper) keeper.addEventListener("click", keep);
    var name = byId("sel-name");
    if (name) {
      name.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { event.preventDefault(); keep(); }
      });
    }
    window.addEventListener("dashboard:viewer-rendered", function () {
      var v = viewer();
      offerName();
      if (v && v.modelSignature() !== state.signature) resolve();
    });
    window.addEventListener("dashboard:selection-changed", offerName);
    window.addEventListener("dashboard:viewer-page-opened", function () { load(); });
    window.addEventListener("dashboard:run-changed", function () {
      state.named = [];
      state.atoms = new Map();
      state.typed = new Map();
      state.signature = "";
      list();
      load();
    });
    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXSelections = {
    state: state, forScene: forScene, resolve: resolve, load: load, pick: pick,
    keep: keep, change: change, forget: forget,
  };
}());
