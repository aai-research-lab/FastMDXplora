/* What holds the chains together, in the Viewer's panel and in the structure.
 *
 * The hydrogen bonds and salt bridges between the protein's chains in the
 * frames played (/api/chain-contacts, gui/chain_contacts.py), by the criteria
 * the interactions analysis applies to a ligand: listed the most often
 * present first, each with the share of frames it was present in, those of
 * the frame shown marked; and shown in the structure as dashed lines between
 * their atoms, coloured by kind, as the frames play.
 */
(function () {
  "use strict";

  var MOST_ROWS = 12;
  // The ligand panel's colours for the same kinds (frame-interactions.js).
  var KIND_COLOURS = { hydrogen_bond: "#56B4E9", salt_bridge: "#D55E00" };

  var state = { data: null, asked: false, index: null, shownKey: "" };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE && v.STATE.model && v.STATE.model.of === "frames" ? v.STATE.engine : null;
  }

  function present(contact, index) {
    return index != null && contact.episodes.some(function (run) {
      return run[0] <= index && index <= run[1];
    });
  }

  function colourNumber(hex) { return parseInt(hex.slice(1), 16); }

  /* The contacts of the frame shown, in the structure, unless asked not to. */
  function showInStructure() {
    var shown = engine();
    if (!shown || !shown.showInteractions) return;
    var wanted = byId("chain-contacts-shown");
    var pairs = [];
    if (state.data && (!wanted || wanted.checked)) {
      state.data.contacts.forEach(function (contact) {
        if (present(contact, state.index)) {
          pairs.push({ a: contact.atoms[0], b: contact.atoms[1],
            colour: colourNumber(KIND_COLOURS[contact.kind] || "#999999") });
        }
      });
    }
    var key = pairs.map(function (p) { return p.a + ":" + p.b + ":" + p.colour; }).join(",");
    // The engine forgets them when it loads the frames again.
    if (key === state.shownKey && shown.interactionsHeld("chains") === pairs.length) return;
    state.shownKey = key;
    shown.showInteractions(pairs, "chains").catch(function () { state.shownKey = ""; });
  }

  function mark() {
    var d = state.data;
    if (!d) return;
    var now = d.contacts.filter(function (c) { return present(c, state.index); }).length;
    var said = byId("chain-contacts-now");
    if (said) {
      said.textContent = state.index == null ? ""
        : now + " of " + d.total + " present in frame " + state.index;
    }
    document.querySelectorAll("#chain-contacts-list .chain-contact").forEach(function (row) {
      var contact = d.contacts[Number(row.getAttribute("data-index"))];
      row.classList.toggle("is-present", !!contact && present(contact, state.index));
    });
    showInStructure();
  }

  function list() {
    var d = state.data;
    var host = byId("chain-contacts-list");
    if (!host || !d) return;
    host.replaceChildren();
    d.contacts.slice(0, MOST_ROWS).forEach(function (contact, index) {
      var row = document.createElement("li");
      row.className = "chain-contact";
      row.setAttribute("data-index", String(index));
      row.title = contact.said + " (" + contact.atoms_said + "): " + d.criteria[contact.kind]
        + "; present in " + Math.round(contact.occupancy * 100) + "% of the frames played";
      var swatch = document.createElement("span");
      swatch.className = "chain-contact-kind";
      swatch.style.background = KIND_COLOURS[contact.kind] || "#999999";
      swatch.setAttribute("aria-label", contact.said);
      var residues = document.createElement("span");
      residues.className = "chain-contact-residues mono";
      residues.textContent = contact.residues[0] + " ↔ " + contact.residues[1];
      var share = document.createElement("span");
      share.className = "chain-contact-share mono";
      share.textContent = Math.round(contact.occupancy * 100) + "%";
      row.append(swatch, residues, share);
      host.appendChild(row);
    });
    var notes = (d.notes || []).slice();
    if (d.total > MOST_ROWS) {
      notes.push("The " + MOST_ROWS + " most often present of " + d.total + " are listed; "
        + "every one present in the frame shown is in the structure.");
    }
    if (!d.total) notes.push("No hydrogen bond or salt bridge joins the chains in the frames played.");
    var note = byId("chain-contacts-note");
    if (note) note.textContent = notes.join(" ");
  }

  function load() {
    if (state.asked) return;
    state.asked = true;
    fetch("/api/chain-contacts", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false }; })
      .then(function (said) {
        var section = byId("side-chains");
        if (!said || !said.ok) {
          state.data = null;
          if (section) section.hidden = true;
          return;
        }
        state.data = said;
        if (section) section.hidden = false;
        list();
        mark();
      });
  }

  function forget() {
    state.data = null;
    state.asked = false;
    state.index = null;
    state.shownKey = "";
    var section = byId("side-chains");
    if (section) section.hidden = true;
    var shown = engine();
    if (shown && shown.showInteractions) shown.showInteractions([], "chains").catch(function () {});
  }

  function wire() {
    window.addEventListener("dashboard:playback-ready", function () {
      // The frames may have been written again, and the contacts with them.
      state.shownKey = "";
      state.asked = false;
      load();
    });
    window.addEventListener("dashboard:frame-shown", function (event) {
      state.index = event.detail ? event.detail.index : null;
      load();
      mark();
    });
    window.addEventListener("dashboard:run-changed", forget);
    var wanted = byId("chain-contacts-shown");
    if (wanted) wanted.addEventListener("change", showInStructure);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXChainContacts = { state: state, load: load };
}());
