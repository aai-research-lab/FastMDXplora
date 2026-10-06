/* The sequence above the molecule: one line a chain, numbered.
 *
 * Each chain of the polymer rendered is a line of one-letter codes, the
 * residue numbers written over every fifth residue (5, 10, 15 ...) by the
 * structure's own numbering, a gap where numbers are missing, and a bar
 * under each residue in a helix or a strand in the frame shown (the
 * cartoon's DSSP). A click selects a residue, a drag a run of them,
 * Shift extends and Ctrl or Cmd adds or removes one; the selection is
 * marked in the structure, and an atom clicked in the structure marks its
 * residue here. A double click centres the camera on what is selected.
 * From the keyboard a line is a list: the arrows move along it (with Shift,
 * extending the selection), Up and Down change chain, Enter selects and
 * Escape clears.
 */
(function () {
  "use strict";

  var MOST_RESIDUES = 40000;

  var ONE_LETTER = {
    ALA: "A", ARG: "R", ASN: "N", ASP: "D", ASH: "D", CYS: "C", CYX: "C", CYM: "C",
    GLN: "Q", GLU: "E", GLH: "E", GLY: "G", HIS: "H", HID: "H", HIE: "H", HIP: "H",
    HSD: "H", HSE: "H", HSP: "H", ILE: "I", LEU: "L", LYS: "K", LYN: "K", MET: "M",
    MSE: "M", PHE: "F", PRO: "P", SER: "S", THR: "T", TRP: "W", TYR: "Y", VAL: "V",
    SEC: "U", PYL: "O", DA: "A", DC: "C", DG: "G", DT: "T", DU: "U", A: "A", C: "C",
    G: "G", U: "U", RA: "A", RC: "C", RG: "G", RU: "U",
  };
  var SS_SAID = { h: "helix", s: "strand", c: "coil" };

  var state = {
    residues: [],      // every residue, in order: {chain, row, resi, icode, resn, first, last, ss, node}
    rows: [],          // per chain: {chain, node, from, to}
    signature: "",
    selected: new Set(),
    anchor: null,
    dragging: false,
    dragged: false,
    active: null,
    hovered: null,
  };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE ? v.STATE.engine : null;
  }

  function host() { return byId("sequence-strip"); }

  function scroller() { return byId("seq-scroll"); }

  function codeOf(resn) {
    return ONE_LETTER[String(resn || "").toUpperCase()] || "X";
  }

  function nameOf(r) {
    return r.resn + " " + r.resi + (r.icode || "") + (r.chain ? ", chain " + r.chain : "");
  }

  /* ---------------------------------------------------------------- */
  /* Building the lines                                                */
  /* ---------------------------------------------------------------- */

  function signatureOf(chains) {
    var count = 0;
    var ends = [];
    chains.forEach(function (c) {
      count += c.residues.length;
      var first = c.residues[0];
      var last = c.residues[c.residues.length - 1];
      ends.push(c.chain + first.resi + first.resn + first.first + "-" + last.resi + last.last);
    });
    return count + ":" + ends.join("|");
  }

  function build(chains) {
    var box = scroller();
    if (!box) return;
    box.replaceChildren();
    state.residues = [];
    state.rows = [];
    var shown = 0;
    var cut = false;
    chains.forEach(function (c) {
      if (cut) return;
      var row = document.createElement("div");
      row.className = "seq-row";
      row.setAttribute("role", "listbox");
      row.setAttribute("aria-multiselectable", "true");
      row.setAttribute("aria-label", "Chain " + (c.chain || "without a name") + ", "
        + c.residues.length + " residue" + (c.residues.length === 1 ? "" : "s"));
      row.tabIndex = state.rows.length ? -1 : 0;
      var label = document.createElement("span");
      label.className = "seq-chain";
      label.setAttribute("aria-hidden", "true");
      label.textContent = c.chain || "·";
      row.appendChild(label);
      var info = { chain: c.chain, node: row, from: state.residues.length, to: 0 };
      var previous = null;
      var fragment = document.createDocumentFragment();
      c.residues.forEach(function (r) {
        if (cut) return;
        if (shown >= MOST_RESIDUES) { cut = true; return; }
        if (previous && !r.icode && !previous.icode && r.resi - previous.resi > 1) {
          var gap = document.createElement("span");
          gap.className = "seq-gap";
          gap.setAttribute("aria-hidden", "true");
          gap.title = "Residues " + (previous.resi + 1) + " to " + (r.resi - 1)
            + " are not in the structure";
          fragment.appendChild(gap);
        }
        var k = state.residues.length;
        var node = document.createElement("span");
        node.className = "seq-res ss-" + r.ss;
        node.id = "seq-" + k;
        node.setAttribute("role", "option");
        node.setAttribute("aria-selected", "false");
        node.setAttribute("aria-label", r.resn + " " + r.resi + (r.icode || ""));
        node.dataset.k = String(k);
        if (!r.icode && r.resi % 5 === 0) node.dataset.n = String(r.resi);
        node.textContent = codeOf(r.resn);
        fragment.appendChild(node);
        state.residues.push({ chain: c.chain, row: state.rows.length, resi: r.resi,
          icode: r.icode, resn: r.resn, first: r.first, last: r.last, ss: r.ss, node: node });
        previous = r;
        shown += 1;
      });
      row.appendChild(fragment);
      info.to = state.residues.length - 1;
      if (info.to >= info.from) {
        state.rows.push(info);
        box.appendChild(row);
      }
    });
    var note = byId("seq-note");
    if (note) {
      note.hidden = !cut;
      note.textContent = cut ? "The first " + MOST_RESIDUES.toLocaleString()
        + " residues are listed." : "";
    }
  }

  /** The lines for what the Viewer renders now, built again only where the
   * residues have changed; the secondary structure always read again. */
  function refresh() {
    var e = engine();
    var strip = host();
    if (!strip) return;
    var chains = [];
    try {
      chains = e && viewer().STATE.model ? e.sequence() : [];
    } catch (error) {
      chains = [];
    }
    chains = chains.filter(function (c) { return c.residues.length; });
    strip.hidden = !chains.length;
    var had = state.selected.size > 0;
    if (!chains.length) {
      state.signature = "";
      state.residues = [];
      state.rows = [];
      state.selected.clear();
      if (had) tell(false); else say();
      return;
    }
    var signature = signatureOf(chains);
    if (signature !== state.signature) {
      // Other residues: what was selected is not among them.
      state.signature = signature;
      state.selected.clear();
      state.anchor = null;
      state.active = null;
      build(chains);
      if (had) tell(false); else say();
      return;
    }
    // The same residues: the selection stands (the scene keeps it), and the
    // secondary structure is read again.
    shade(chains);
  }

  /** Each residue's bar from the frame shown. */
  function shade(chains) {
    var k = 0;
    chains.forEach(function (c) {
      c.residues.forEach(function (r) {
        var kept = state.residues[k];
        k += 1;
        if (!kept || kept.ss === r.ss) return;
        kept.node.classList.remove("ss-" + kept.ss);
        kept.ss = r.ss;
        kept.node.classList.add("ss-" + r.ss);
      });
    });
  }

  var shading = false;
  function shadeSoon() {
    if (shading) return;
    shading = true;
    window.requestAnimationFrame(function () {
      shading = false;
      var e = engine();
      if (!e || !state.residues.length) return;
      try {
        shade(e.sequence());
      } catch (error) { /* the frame changed again */ }
    });
  }

  /* ---------------------------------------------------------------- */
  /* The selection                                                     */
  /* ---------------------------------------------------------------- */

  function atomsOf(ks) {
    var atoms = [];
    ks.forEach(function (k) {
      var r = state.residues[k];
      for (var i = r.first; i <= r.last; i += 1) atoms.push(i);
    });
    return atoms;
  }

  function sorted() {
    return Array.from(state.selected).sort(function (a, b) { return a - b; });
  }

  /** Runs of the selection, as "A 10 to 25, A 30". */
  function runsSaid(ks) {
    var runs = [];
    var start = null;
    var end = null;
    function close() {
      if (start == null) return;
      var a = state.residues[start];
      var b = state.residues[end];
      runs.push((a.chain ? a.chain + " " : "") + a.resi + (a.icode || "")
        + (start === end ? "" : " to " + b.resi + (b.icode || "")));
    }
    ks.forEach(function (k) {
      if (start != null && k === end + 1 && state.residues[k].row === state.residues[end].row) {
        end = k;
        return;
      }
      close();
      start = k;
      end = k;
    });
    close();
    return runs;
  }

  function say(text) {
    var said = byId("seq-said");
    if (!said) return;
    if (text != null) { said.textContent = text; return; }
    var ks = sorted();
    var clearer = byId("seq-clear");
    if (clearer) clearer.hidden = !ks.length;
    if (!ks.length) {
      said.textContent = state.residues.length
        ? "Click or drag to select; double click to centre." : "";
      return;
    }
    if (ks.length === 1) {
      var r = state.residues[ks[0]];
      said.textContent = "Selected " + nameOf(r) + ", " + SS_SAID[r.ss];
      return;
    }
    var runs = runsSaid(ks);
    var atoms = atomsOf(ks).length;
    said.textContent = "Selected " + (runs.length > 3 ? runs.slice(0, 3).join(", ") + " and "
      + (runs.length - 3) + " more" : runs.join(", ")) + ": " + ks.length + " residues, "
      + atoms + " atoms";
  }

  function mark() {
    state.residues.forEach(function (r, k) {
      var on = state.selected.has(k);
      if (r.node.classList.contains("selected") !== on) {
        r.node.classList.toggle("selected", on);
        r.node.setAttribute("aria-selected", on ? "true" : "false");
      }
    });
  }

  /** The selection marked here and in the structure, and the page told. */
  function tell(announce) {
    mark();
    say();
    var ks = sorted();
    var residues = ks.map(function (k) {
      var r = state.residues[k];
      return { chain: r.chain, resi: r.resi, icode: r.icode, resn: r.resn,
        first: r.first, last: r.last };
    });
    var v = viewer();
    if (v && v.selectResidues) v.selectResidues(residues, atomsOf(ks), { announce: announce });
  }

  function setRange(from, to, adding) {
    if (!adding) state.selected.clear();
    var a = Math.min(from, to);
    var b = Math.max(from, to);
    for (var k = a; k <= b; k += 1) state.selected.add(k);
  }

  function clear() {
    var v = viewer();
    if (!state.selected.size && !(v && v.STATE && v.STATE.selection)) return;
    state.selected.clear();
    state.anchor = null;
    tell(true);
  }

  /** The residue of an atom of the structure, or null. */
  function residueOfAtom(index) {
    var list = state.residues;
    var lo = 0;
    var hi = list.length - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (index < list[mid].first) hi = mid - 1;
      else if (index > list[mid].last) lo = mid + 1;
      else return mid;
    }
    // Residues out of the atoms' order: looked for one by one.
    for (var k = 0; k < list.length; k += 1) {
      if (index >= list[k].first && index <= list[k].last) return k;
    }
    return null;
  }

  /* ---------------------------------------------------------------- */
  /* The mouse                                                          */
  /* ---------------------------------------------------------------- */

  function residueAt(target) {
    var node = target && target.closest ? target.closest(".seq-res") : null;
    return node ? Number(node.dataset.k) : null;
  }

  function hover(k) {
    if (state.hovered === k) return;
    if (state.hovered != null && state.residues[state.hovered]) {
      state.residues[state.hovered].node.classList.remove("hovered");
    }
    state.hovered = k;
    var e = engine();
    if (k == null) {
      if (e) e.highlight([]);
      say();
      return;
    }
    var r = state.residues[k];
    r.node.classList.add("hovered");
    if (e) e.highlight(atomsOf([k]));
    if (!state.dragging) say(nameOf(r) + ", " + SS_SAID[r.ss]);
  }

  function onDown(event) {
    if (event.button !== 0) return;
    var k = residueAt(event.target);
    if (k == null) return;
    event.preventDefault();
    var row = state.rows[state.residues[k].row];
    if (row) focusRow(row.node, k);
    if (event.ctrlKey || event.metaKey) {
      if (state.selected.has(k)) state.selected.delete(k); else state.selected.add(k);
      state.anchor = k;
      tell(true);
      return;
    }
    if (event.shiftKey && state.anchor != null
        && state.residues[state.anchor].row === state.residues[k].row) {
      setRange(state.anchor, k, false);
      tell(true);
      return;
    }
    state.anchor = k;
    state.dragging = true;
    state.dragged = false;
    setRange(k, k, false);
    mark();
  }

  function onOver(event) {
    var k = residueAt(event.target);
    hover(k);
    if (!state.dragging || k == null) return;
    if (state.residues[k].row !== state.residues[state.anchor].row) return;
    state.dragged = true;
    setRange(state.anchor, k, false);
    mark();
    say();
  }

  function onUp() {
    if (!state.dragging) return;
    state.dragging = false;
    tell(true);
  }

  function onDouble(event) {
    var k = residueAt(event.target);
    if (k == null) return;
    var e = engine();
    var ks = state.selected.has(k) ? sorted() : [k];
    if (e) e.focus(atomsOf(ks));
  }

  /* ---------------------------------------------------------------- */
  /* The keyboard                                                       */
  /* ---------------------------------------------------------------- */

  function focusRow(node, k) {
    state.rows.forEach(function (row) { row.node.tabIndex = row.node === node ? 0 : -1; });
    if (k != null) activate(k);
    if (document.activeElement !== node) node.focus({ preventScroll: true });
  }

  function activate(k) {
    if (state.active != null && state.residues[state.active]) {
      state.residues[state.active].node.classList.remove("active");
    }
    state.active = k;
    var r = state.residues[k];
    r.node.classList.add("active");
    state.rows[r.row].node.setAttribute("aria-activedescendant", r.node.id);
    r.node.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function onKey(event) {
    var row = event.target.closest ? event.target.closest(".seq-row") : null;
    if (!row) return;
    var index = state.rows.findIndex(function (r) { return r.node === row; });
    if (index < 0) return;
    var info = state.rows[index];
    var k = state.active != null && state.residues[state.active]
      && state.residues[state.active].row === index ? state.active : info.from;
    var next = null;
    switch (event.key) {
      case "ArrowLeft": next = Math.max(info.from, k - 1); break;
      case "ArrowRight": next = Math.min(info.to, k + 1); break;
      case "Home": next = info.from; break;
      case "End": next = info.to; break;
      case "ArrowUp":
      case "ArrowDown": {
        var to = state.rows[index + (event.key === "ArrowUp" ? -1 : 1)];
        if (!to) return;
        event.preventDefault();
        var offset = k - info.from;
        focusRow(to.node, Math.min(to.to, to.from + offset));
        return;
      }
      case "Enter":
      case " ":
        // Not the Viewer's play and pause.
        event.preventDefault();
        event.stopPropagation();
        state.anchor = k;
        setRange(k, k, false);
        tell(true);
        return;
      case "Escape":
        if (state.selected.size) { event.preventDefault(); event.stopPropagation(); clear(); }
        return;
      default:
        return;
    }
    event.preventDefault();
    // The Viewer's own keys (a frame with the arrows) are not these.
    event.stopPropagation();
    activate(next);
    if (event.shiftKey) {
      if (state.anchor == null || state.residues[state.anchor].row !== index) state.anchor = k;
      setRange(state.anchor, next, false);
      tell(false);
    } else {
      say(nameOf(state.residues[next]) + ", " + SS_SAID[state.residues[next].ss]);
    }
  }

  /* ---------------------------------------------------------------- */
  /* From the structure                                                 */
  /* ---------------------------------------------------------------- */

  /** An atom clicked in the structure: its residue selected, or with Shift
   * or Ctrl added to the selection. */
  function atomClicked(atom, modifiers) {
    if (!atom || !state.residues.length) return false;
    var k = residueOfAtom(atom.index);
    if (k == null) return false;
    var adding = modifiers && (modifiers.shift || modifiers.control || modifiers.meta);
    if (adding && state.selected.has(k)) state.selected.delete(k);
    else {
      if (!adding) state.selected.clear();
      state.selected.add(k);
    }
    state.anchor = k;
    tell(false);
    state.residues[k].node.scrollIntoView({ block: "nearest", inline: "center" });
    return true;
  }

  /** The residues holding any of these atoms marked as selected, the page
   * having selected the atoms itself. */
  function markAtoms(atoms, said) {
    state.selected.clear();
    (atoms || []).forEach(function (index) {
      var k = residueOfAtom(index);
      if (k != null) state.selected.add(k);
    });
    state.anchor = null;
    mark();
    say(said);
  }

  function atomHovered(atom) {
    var k = atom ? residueOfAtom(atom.index) : null;
    if (state.hovered === k) return;
    if (state.hovered != null && state.residues[state.hovered]) {
      state.residues[state.hovered].node.classList.remove("hovered");
    }
    state.hovered = k;
    if (k != null) state.residues[k].node.classList.add("hovered");
  }

  function wire() {
    var box = scroller();
    if (!box) return;
    box.addEventListener("mousedown", onDown);
    box.addEventListener("mouseover", onOver);
    box.addEventListener("mouseleave", function () { hover(null); });
    box.addEventListener("dblclick", onDouble);
    box.addEventListener("keydown", onKey);
    window.addEventListener("mouseup", onUp);
    var clearer = byId("seq-clear");
    // In the strip's head: a click clears and does not fold the strip.
    if (clearer) clearer.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      clear();
    });
    window.addEventListener("dashboard:viewer-rendered", refresh);
    window.addEventListener("dashboard:frame-shown", shadeSoon);
    window.addEventListener("dashboard:run-changed", function () {
      state.signature = "";
      state.selected.clear();
      refresh();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXSequence = {
    state: state, refresh: refresh, clear: clear, atomClicked: atomClicked, markAtoms: markAtoms,
    atomHovered: atomHovered, codeOf: codeOf,
    /** Select residues by their positions in the lines, for the tests. */
    select: function (ks) {
      state.selected = new Set(ks);
      state.anchor = ks.length ? ks[0] : null;
      tell(true);
    },
  };
}());
