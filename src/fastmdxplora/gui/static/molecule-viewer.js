/* FastMDXplora Live Dashboard: the 3D molecular viewer.
 *
 * Rendered by Mol* through the viewer's own engine (viewer-engine.js); this
 * module is the page: the structure, the live frame and the trajectory it
 * is given, the controls under the canvas, the information beside it, and
 * the Overview's preview. Structure loading, live frames, styling and
 * playback are kept apart, so a failed optional feature never leaves the
 * canvas blank.
 */

(function () {
  "use strict";

  const AMINO_ACIDS = [
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "HID", "HIE", "HIP", "ILE", "LEU", "LYS", "MET", "PHE", "PRO",
    "SER", "THR", "TRP", "TYR", "VAL", "MSE", "SEC", "PYL",
    "CYX", "ASH", "GLH", "LYN", "HSD", "HSE", "HSP",
  ];
  const COLORS = {
    cyan: "#2698ba",
    silver: "#d8d8dd",
    white: "#ffffff",
    violet: "#a78bfa",
    black: "#050505",
    green: "#67e8a3",
    orange: "#ffb86b",
  };

  const STATE = {
    // The engines: the Viewer page's and the Overview's preview's.
    engine: null,
    miniEngine: null,
    engineCreating: null,
    miniCreating: null,
    viewerUnavailable: false,
    miniViewerUnavailable: false,
    // What is rendered in each, when something is: its atoms and frames.
    model: null,
    miniModel: null,
    structureInfo: null,
    structureUrl: null,
    structurePdb: null,
    currentPdb: null,
    // A live frame: the PDB its atoms are named by, and the newest
    // coordinates as a one-frame DCD ({bytes, atoms, fingerprint}).
    liveTopology: null,
    liveTopologyAtoms: 0,
    liveCoordinates: null,
    liveFrameIndex: null,
    liveUpdates: true,
    mode: "structure",
    representation: "cartoon",
    colorMode: "spectrum",
    // A colour the person chose (the list, a saved view, a scene), which
    // the colour by run offered later does not take back.
    colourChosen: false,
    // A study of several runs: the runs played together, the colour of the
    // run played, and the runs the person hid (runs_together.py).
    runsTogether: null,
    runColour: null,
    runsHidden: new Set(),
    background: COLORS.black,
    visibility: {
      protein: true,
      ligand: true,
      pocket: true,
      water: false,
      ions: false,
      hydrogens: false,
      box: false,
    },
    ligandResname: null,
    pocketCutoff: 5,
    // The frames as played: "none", or superposed on "backbone" or
    // "pocket", and the address of the frames so.
    superposed: "none",
    // What the frames are fitted to: "first", "start" or "deposited".
    superposedTo: "first",
    // The frames' positions averaged over this many, once fitted.
    smoothedOver: 1,
    superposedUrl: null,
    // A white ground and the highest quality, for a figure.
    publication: false,
    // "dark" (black) or "white".
    // The page's scheme's (--viewer-ground), unless white or dark is chosen.
    ground: "scheme",
    // What Preferences last said of the ground, spin and the ligand.
    groundChosen: "",
    spinChosen: false,
    ligandChosen: false,
    framesCoordinatesUrl: null,
    pocketSurface: false,
    pocketOnly: false,
    isolateLigand: false,
    labels: false,
    preservingCamera: true,
    spinning: false,
    playbackPayload: null,
    // The residues selected, in the sequence or the structure, and their atoms.
    selection: null,
    // Incremented on a dashboard run transition so a late response from the
    // previous run cannot repopulate the reset viewer.
    viewerGeneration: 0,
    // The solvated system beside the frames, for water and ions.
    environment: false,
    environmentPdb: null,
    environmentUrl: null,
    playbackSignature: null,
    playbackLoadPromise: null,
    playbackLoaded: false,
    // The frames are in the engine (playbackLoaded: and shown, ready).
    framesRendered: false,
    // Each press of play, so a later one or a pause outranks an earlier.
    playbackAsked: 0,
    playbackFrames: 0,
    playbackFrameTimes: [],
    playbackPlaying: false,
    playbackReverse: false,
    playbackLoop: false,
    playbackSpeed: 1,
    playbackTimer: null,
    // Measuring: whether clicks pick atoms, and the atoms picked.
    measuring: false,
    picks: [],
    focusResidue: null,
    focusIndices: null,
    // The study's per-residue results the protein can be coloured by, and
    // DSSP for the structures shown (gui/by_residue.py).
    residueValues: null,
    residueValuesAsked: 0,
    residueLookups: null,
    secondaryStructure: null,
    secondaryStructures: null,
  };

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    // Sized before the engine is made, so its canvas is made at its size.
    fitTheLayout();
    wireControls();
    wireTrajectoryControls();
    wireResidueFocus();
    wireKeys();
    tidyOverlay();
    window.FastMDXDashboard?.on("structure-updated", onStructureUpdated);
    window.FastMDXDashboard?.on("status-updated", ({status, times}) => onStatusUpdated(status, times));
    window.FastMDXDashboard?.on("playback-ready", onPlaybackReady);
    window.FastMDXDashboard?.on("run-changed", onRunChanged);
    window.FastMDXDashboard?.on("viewer-page-opened", onViewerPageOpened);
    window.FastMDXDashboard?.on("live-page-opened", onLivePageOpened);
    window.FastMDXDashboard?.on("settings-updated", onSettingsUpdated);
    // The settings kept in this browser, before anything is rendered.
    const settings = window.FastMDXDashboard?.settings?.();
    if (settings) onSettingsUpdated(settings);
    window.addEventListener("resize", resizeViewers);
    document.addEventListener("fullscreenchange", () => requestAnimationFrame(resizeViewers));
    wireFolds();
    const refreshSeconds = Number(document.body?.dataset.refreshSeconds || 3);
    window.setInterval(
      pollLiveFrame,
      Math.max(1000, Math.min(60000, refreshSeconds * 1000))
    );
  }

  function onRunChanged(change) {
    // The first app state names the study the server was already serving:
    // what the Viewer loaded and what the person chose before it came are
    // of that study, and are kept.
    if (change && change.first) return;
    // Requests started for the previous run are not safe to apply after this
    // reset; each async path captures and checks this generation.
    STATE.viewerGeneration += 1;
    STATE.playbackLoadPromise = null;
    pausePlayback();
    STATE.liveUpdates = true;
    STATE.liveFrameIndex = null;
    STATE.liveTopology = null;
    STATE.liveTopologyAtoms = 0;
    STATE.liveCoordinates = null;
    STATE.mode = "structure";
    STATE.structureInfo = null;
    STATE.structureUrl = null;
    STATE.structurePdb = null;
    STATE.currentPdb = null;
    STATE.playbackPayload = null;
    STATE.environment = false;
    STATE.environmentPdb = null;
    STATE.environmentUrl = null;
    STATE.playbackSignature = null;
    STATE.superposedUrl = null;
    STATE.framesCoordinatesUrl = null;
    STATE.playbackLoaded = false;
    STATE.framesRendered = false;
    STATE.playbackFrames = 0;
    STATE.playbackFrameTimes = [];
    STATE.playbackTimer = null;
    STATE.model = null;
    STATE.miniModel = null;
    STATE.spinning = false;
    STATE.picks = [];
    STATE.selection = null;
    STATE.focusResidue = null;
    STATE.focusIndices = null;
    STATE.residueValues = null;
    STATE.residueValuesAsked = 0;
    STATE.residueLookups = null;
    STATE.secondaryStructure = null;
    STATE.secondaryStructures = null;
    STATE.runsTogether = null;
    STATE.runsFittedOnce = false;
    STATE.runsHidden = new Set();
    STATE.extraResults = {};
    STATE.runsShownSignature = null;
    sayTheRuns(null);
    offerResultColours();
    sayTheSecondaryStructure();
    STATE.visibility = {
      protein: true,
      ligand: true,
      pocket: true,
      water: false,
      ions: false,
      hydrogens: false,
      box: false,
    };
    document.querySelectorAll(".chip-toggle input[data-vis]").forEach((checkbox) => {
      checkbox.checked = !!STATE.visibility[checkbox.getAttribute("data-vis")];
    });
    STATE.contactsShown = false;
    [STATE.engine, STATE.miniEngine].forEach((engine) => {
      if (engine) void engine.clear().catch((error) => console.debug(error));
    });
    document.getElementById("viewer-canvas-frame")?.removeAttribute("data-ready");
    document.getElementById("mini-preview-frame")?.removeAttribute("data-ready");
  }

  /* ------------------------------------------------------------------ */
  /* The engines                                                         */
  /* ------------------------------------------------------------------ */
  function hasEngine() {
    return !!(window.FastMDXViewerEngine && window.molstar && window.molstar.lib);
  }

  /** The Viewer page's engine, made the first time its canvas is shown. */
  async function mainEngine() {
    if (STATE.engine) return STATE.engine;
    if (STATE.engineCreating) return STATE.engineCreating;
    const target = document.getElementById("viewer-canvas");
    if (!target || !isVisible(target) || STATE.viewerUnavailable) return null;
    if (!hasEngine()) {
      showViewerMessage("The molecular viewer did not load.",
        "Confirm /static/molstar/molstar.js is being served, then refresh the page.");
      return null;
    }
    STATE.engineCreating = (async () => {
      try {
        const engine = await window.FastMDXViewerEngine.create(target,
          {background: STATE.publication ? 0xffffff : groundColour(), quality: "auto"});
        engine.on("hover", onHoverAtom);
        engine.on("click", onClickAtom);
        STATE.engine = engine;
        if (STATE.spinChosen) setSpinning(engine, true);
        return engine;
      } catch (error) {
        STATE.viewerUnavailable = true;
        console.warn("molecular viewer initialization failed", error);
        showViewerMessage("Interactive molecular viewer unavailable",
          "WebGL could not be initialized in this browser. Try enabling hardware acceleration or use the static preview.");
        return null;
      } finally {
        STATE.engineCreating = null;
      }
    })();
    return STATE.engineCreating;
  }

  /** The Overview's preview, made the first time it is shown. */
  async function miniEngine() {
    if (STATE.miniEngine) return STATE.miniEngine;
    if (STATE.miniCreating) return STATE.miniCreating;
    const target = document.getElementById("mini-preview-canvas");
    if (!target || !isVisible(target) || !hasEngine() || STATE.miniViewerUnavailable) return null;
    STATE.miniCreating = (async () => {
      try {
        const engine = await window.FastMDXViewerEngine.create(target,
          {background: colourNumber(COLORS.black), quality: "auto", axes: false});
        STATE.miniEngine = engine;
        return engine;
      } catch (error) {
        STATE.miniViewerUnavailable = true;
        console.warn("molecular preview initialization failed", error);
        const empty = document.getElementById("mini-preview-empty");
        if (empty) empty.textContent = "Interactive preview unavailable (WebGL).";
        return null;
      } finally {
        STATE.miniCreating = null;
      }
    })();
    return STATE.miniCreating;
  }

  function onViewerPageOpened() {
    fitTheLayout();
    void askForResidueValues();
    requestAnimationFrame(() => requestAnimationFrame(async () => {
      const engine = await mainEngine();
      if (engine && STATE.mode === "playback" && STATE.playbackPayload && !STATE.playbackLoaded) {
        await loadPlayback(STATE.playbackPayload);
      } else if (engine && STATE.currentPdb && !STATE.model) {
        await mountStructure(STATE.currentPdb, {main: true, fit: true});
      }
      resizeViewers();
    }));
  }

  function onLivePageOpened() {
    requestAnimationFrame(() => requestAnimationFrame(async () => {
      const engine = await miniEngine();
      if (engine && !STATE.miniModel) {
        if (STATE.mode === "playback" && STATE.playbackLoaded) await loadMiniFrames();
        else if (STATE.currentPdb) await mountStructure(STATE.currentPdb, {mini: true, fit: true});
      }
      resizeViewers();
    }));
  }

  function isViewerGenerationCurrent(generation) {
    return generation === STATE.viewerGeneration;
  }

  async function onStructureUpdated(info) {
    const generation = STATE.viewerGeneration;
    STATE.structureInfo = info || {};
    const ligandNames = Array.isArray(info?.ligand_resnames) ? info.ligand_resnames : [];
    if (!STATE.ligandResname && ligandNames.length) STATE.ligandResname = ligandNames[0];
    offerTheLigandsControls();
    void askForResidueValues();

    const available = !!(info?.structure_available || info?.valid);
    if (!available) {
      showViewerMessage(
        "Waiting for a molecular structure",
        "The viewer will initialize when setup/prepared.pdb or a simulation topology becomes available."
      );
      return;
    }

    // Playback owns the main canvas. Water, ions and the box beside it come
    // from the solvated system, rendered beside the frames.
    if (STATE.mode === "playback") {
      if (needsFullTopology()) await ensurePlaybackEnvironment();
      return;
    }

    // Water and ions are stripped from the copy the browser gets; the
    // solvated system is asked for only when one of them is shown.
    const url = withSolvent(info.structure_url || "/structure/topology.pdb");
    if (url === STATE.structureUrl && STATE.structurePdb) {
      if (!STATE.model) await mountStructure(STATE.currentPdb || STATE.structurePdb, {main: true, fit: true});
      if (!STATE.miniModel) await mountStructure(STATE.currentPdb || STATE.structurePdb, {mini: true, fit: true});
      return;
    }
    try {
      const response = await fetch(url, {cache: "no-store"});
      if (!isViewerGenerationCurrent(generation)) return;
      if (!response.ok) throw new Error(`structure HTTP ${response.status}`);
      const pdb = await response.text();
      if (!isViewerGenerationCurrent(generation)) return;
      if (!pdb.includes("ATOM") && !pdb.includes("HETATM")) {
        throw new Error("structure response contains no atoms");
      }
      STATE.structureUrl = url;
      STATE.structurePdb = pdb;
      // The frames were asked for while the structure was on its way: they
      // own the canvas, and the structure is kept for when they no longer do.
      if (STATE.mode === "playback") return;
      // Solvent wins over the live frame: frames are written with water and
      // ions already stripped, so live mode cannot show them at all.
      if (STATE.liveFrameIndex != null && STATE.liveTopology && !needsFullTopology()) {
        STATE.currentPdb = STATE.liveTopology;
      } else if (STATE.mode !== "live" || !STATE.currentPdb || needsFullTopology()) {
        STATE.currentPdb = pdb;
      }
      STATE.mode = STATE.liveFrameIndex != null ? "live" : "structure";
      await mountStructure(STATE.currentPdb, {main: true, fit: true});
      await mountStructure(STATE.currentPdb, {mini: true, fit: true});
      hideViewerMessage();
    } catch (error) {
      console.warn("molecular structure load failed", error);
      showViewerMessage("Structure could not be loaded.", String(error));
    }
  }

  /** A PDB's box, [a, b, c, alpha, beta, gamma], from its CRYST1. */
  function cellOf(text) {
    const line = /^CRYST1(.{9})(.{9})(.{9})(.{7})(.{7})(.{7})/m.exec(text || "");
    if (!line) return null;
    const cell = line.slice(1).map(Number);
    // A CRYST1 of ones is a structure with no box (written so by tools).
    if (cell.some((v) => !Number.isFinite(v)) || cell.slice(0, 3).every((v) => v <= 1)) return null;
    return [cell];
  }

  function needsFullTopology() {
    // The frames hold solute coordinates only. Water and ions require the
    // full topology.
    return Boolean(STATE.visibility?.water || STATE.visibility?.ions);
  }

  function withSolvent(url) {
    if (!needsFullTopology()) return url;
    return url + (url.includes("?") ? "&" : "?") + "solvent=1";
  }

  /** One structure into the Viewer's engine or the preview's, as PDB text:
   * rendered as the controls say, its cartoon given DSSP. The same text
   * asked for again while it is being loaded is that load: the structure's
   * first state and the page's opening both asked for it before the first
   * had set the model, and the second emptied the engine for a moment and
   * rendered it all again. */
  function mountStructure(pdbText, options) {
    const key = options && options.mini ? "mini" : "main";
    const going = STATE.mountsGoing && STATE.mountsGoing[key];
    if (going && going.text === pdbText && going.generation === STATE.viewerGeneration) {
      return going.promise;
    }
    const mount = {text: pdbText, generation: STATE.viewerGeneration};
    // Shared until the structure is in the engine, not while its cartoon is
    // given DSSP: asked for again after that, it is loaded again.
    mount.loaded = () => {
      if (STATE.mountsGoing && STATE.mountsGoing[key] === mount) delete STATE.mountsGoing[key];
    };
    STATE.mountsGoing = Object.assign(STATE.mountsGoing || {}, {[key]: mount});
    mount.promise = mountStructureNow(pdbText, options, mount.loaded).finally(mount.loaded);
    return mount.promise;
  }

  async function mountStructureNow(pdbText, options, loadedNow) {
    const opts = options || {};
    const mini = !!opts.mini;
    const generation = STATE.viewerGeneration;
    const engine = mini ? await miniEngine() : await mainEngine();
    if (!engine || !pdbText || !isViewerGenerationCurrent(generation)) return;
    const of = pdbText === STATE.structurePdb ? "structure" : "live";
    const version = of === "structure" ? STATE.structureUrl : STATE.liveFrameIndex;
    const hadModel = mini ? !!STATE.miniModel : !!STATE.model;
    // A live frame is its topology at the newest coordinates, so the next
    // frame moves these atoms rather than loading them again.
    const coordinates = pdbText === STATE.liveTopology ? STATE.liveCoordinates : null;
    applyLook(engine, mini);
    // The structure's own box, from its CRYST1, about its atoms.
    engine.setCells(cellOf(pdbText), "atoms");
    try {
      const loaded = await engine.loadStructure({text: pdbText,
        coordinates: coordinates ? coordinates.bytes : null,
        keepCamera: hadModel && !opts.fit && STATE.preservingCamera});
      if (loadedNow) loadedNow();
      if (!isViewerGenerationCurrent(generation)) return;
      const rendered = {atoms: loaded.atoms, frames: loaded.frames, of};
      if (mini) {
        STATE.miniModel = rendered;
        document.getElementById("mini-preview-frame")?.setAttribute("data-ready", "true");
      } else {
        STATE.model = rendered;
        document.getElementById("viewer-canvas-frame")?.setAttribute("data-ready", "true");
        // The sequence at once, before the cartoon's DSSP is read.
        window.dispatchEvent(new CustomEvent("dashboard:viewer-rendered", {detail: {of}}));
        // A result's colours are for the residues now rendered: two that this
        // structure cannot tell apart are given neither's value.
        if (activeResult()) applyLook(engine, false);
        if (STATE.picks.length) await renderMeasurement();
      }
      await giveTheSecondaryStructure(engine, of, version, mini,
        coordinates ? coordinates.fingerprint : fingerprintOf(pdbText));
      if (!mini) sayTheColours();
    } catch (error) {
      console.warn("the viewer could not render this structure", error);
      if (!mini) showViewerMessage("The viewer could not read this structure.", String(error));
    }
  }

  /** Whether the frames played have moved from where the first frame was
   * written: fitted to a structure other than the first frame. */
  function environmentMoved() {
    return STATE.superposed !== "none"
      && ((STATE.appliedTo || "first") !== "first" || (STATE.appliedSmooth || 1) > 1);
  }

  async function ensurePlaybackEnvironment() {
    const generation = STATE.viewerGeneration;
    if (STATE.mode !== "playback") return false;
    const engine = await mainEngine();
    if (!engine) return false;
    if (!needsFullTopology() || environmentMoved()) {
      if (STATE.environment) {
        await engine.removeEnvironment();
        STATE.environment = false;
      }
      return true;
    }
    const url = withSolvent(STATE.structureInfo?.structure_url || "/structure/topology.pdb");
    try {
      if (STATE.environmentUrl !== url || !STATE.environmentPdb) {
        const response = await fetch(url, {cache: "no-store"});
        if (!isViewerGenerationCurrent(generation)) return false;
        if (!response.ok) throw new Error(`environment HTTP ${response.status}`);
        const pdb = await response.text();
        if (!isViewerGenerationCurrent(generation)) return false;
        if (!pdb.includes("ATOM") && !pdb.includes("HETATM")) {
          throw new Error("environment response contains no atoms");
        }
        STATE.environmentUrl = url;
        STATE.environmentPdb = pdb;
      }
      await engine.loadEnvironment(STATE.environmentPdb, {water: STATE.visibility.water,
        ions: STATE.visibility.ions, hydrogens: STATE.visibility.hydrogens},
        environmentShift(engine));
      STATE.environment = true;
      return true;
    } catch (error) {
      console.warn("playback environment load failed", error);
      announce("Water or ions could not be loaded for trajectory playback.");
      return false;
    }
  }

  /** How far the solvated system is moved to sit where the frame does: by
   * the first atom of the protein in each. The frames are centred on the
   * protein, as the analyses read them, so once is enough. */
  function environmentShift(engine) {
    const anchor = engine.find({resn: AMINO_ACIDS})[0];
    if (!anchor || !STATE.environmentPdb) return [0, 0, 0];
    const line = STATE.environmentPdb.split("\n").find((text) => /^(ATOM  |HETATM)/.test(text)
      && AMINO_ACIDS.includes(text.slice(17, 20).trim()));
    if (!line) return [0, 0, 0];
    const base = [30, 38, 46].map((at) => parseFloat(line.slice(at, at + 8)));
    if (base.some((value) => !Number.isFinite(value))) return [0, 0, 0];
    return [anchor.x - base[0], anchor.y - base[1], anchor.z - base[2]];
  }

  function showViewerMessage(title, detail) {
    const empty = document.getElementById("viewer-empty");
    if (empty) {
      empty.hidden = false;
      empty.querySelector(".empty-title") && (empty.querySelector(".empty-title").textContent = title);
      empty.querySelector(".empty-detail") && (empty.querySelector(".empty-detail").textContent = detail || "");
    }
    const mini = document.getElementById("mini-preview-empty");
    if (mini) mini.textContent = title;
  }

  function hideViewerMessage() {
    const empty = document.getElementById("viewer-empty");
    if (empty) empty.hidden = true;
  }

  /* ------------------------------------------------------------------ */
  /* Live coordinates                                                    */
  /* ------------------------------------------------------------------ */
  /* The live frame's line on the overlay, unless the canvas plays the
   * trajectory: then the overlay is the played frame's (`playbackOverlay`),
   * and the live poll, which goes on for the preview, says nothing there.
   * It said "production step 55,000" beside Playback's frame 39. */
  function sayLive(on, overlay) {
    if (STATE.mode === "playback") return;
    setOverlay(on, overlay);
  }

  async function pollLiveFrame() {
    const generation = STATE.viewerGeneration;
    if (!STATE.liveUpdates || document.body.classList.contains("state-loading")) return;
    try {
      const response = await fetch("/api/live-frame-index", {cache: "no-store"});
      if (!isViewerGenerationCurrent(generation)) return;
      if (!response.ok) return;
      const index = await response.json();
      if (!isViewerGenerationCurrent(generation)) return;
      if (!index.live_frame_available) {
        sayLive(false, {stage: index.simulation_stage || "waiting", age: "\u2014"});
        return;
      }
      if (String(STATE.liveFrameIndex) === String(index.live_frame_index)) {
        sayLive(true, {
          stage: index.simulation_stage || STATE.mode,
          age: liveFrameAge(index),
          step: index.live_frame_index,
          simtime: index.simulation_time_ns,
        });
        return;
      }
      const version = encodeURIComponent(index.live_frame_mtime || Date.now());
      const coordinates = await liveCoordinates(version);
      if (!isViewerGenerationCurrent(generation)) return;
      if (needsFullTopology()) {
        // The frame has no solvent in it; replacing the solvated system with
        // it would empty the view the reader just asked for.
        STATE.liveFrameIndex = index.live_frame_index;
        return;
      }
      const overlay = {
        stage: index.simulation_stage || "live",
        age: liveFrameAge(index),
        step: index.live_frame_index,
        simtime: index.simulation_time_ns,
      };
      // The atoms already shown moved to the new coordinates, where they are
      // the frame's atoms: nothing loaded again, the measurements and picks
      // kept.
      if (coordinates && STATE.liveTopology && STATE.currentPdb === STATE.liveTopology
          && coordinates.atoms === STATE.liveTopologyAtoms) {
        STATE.liveCoordinates = coordinates;
        STATE.liveFrameIndex = index.live_frame_index;
        if (await moveTheLiveAtoms(coordinates)) {
          sayLive(true, overlay);
          return;
        }
      }
      const frameResponse = await fetch(`/structure/live-frame.pdb?v=${version}`,
        {cache: "no-store"});
      if (!isViewerGenerationCurrent(generation)) return;
      if (!frameResponse.ok) return;
      const pdb = await frameResponse.text();
      if (!isViewerGenerationCurrent(generation)) return;
      if (!pdb.includes("ATOM") && !pdb.includes("HETATM")) return;
      STATE.liveFrameIndex = index.live_frame_index;
      const atoms = (pdb.match(/^(?:ATOM  |HETATM)/gm) || []).length;
      STATE.liveTopology = pdb;
      STATE.liveTopologyAtoms = atoms;
      // Coordinates of other atoms than the topology's (the file rewritten
      // between the two requests) are left out: the PDB's own are shown.
      STATE.liveCoordinates = coordinates && coordinates.atoms === atoms ? coordinates : null;
      STATE.currentPdb = pdb;
      if (STATE.mode !== "playback") {
        STATE.mode = "live";
        if (isVisible(document.getElementById("viewer-canvas") || document.body) && STATE.engine) {
          await mountStructure(pdb, {main: true, fit: !STATE.model});
        }
      }
      const preview = document.getElementById("mini-preview-canvas");
      if (preview && isVisible(preview)) await mountStructure(pdb, {mini: true, fit: !STATE.miniModel});
      sayLive(true, overlay);
    } catch (error) {
      console.debug("live molecular frame unavailable", error);
    }
  }

  /** The live frame's coordinates alone (`/structure/live-frame.dcd`): its
   * bytes, how many atoms, and the fingerprint its DSSP is matched by. */
  async function liveCoordinates(version) {
    try {
      const response = await fetch(`/structure/live-frame.dcd?v=${version}`, {cache: "no-store"});
      if (!response.ok) return null;
      const atoms = Number(response.headers.get("X-FastMDX-Atoms"));
      const fingerprint = decodeURIComponent(response.headers.get("X-FastMDX-Fingerprint") || "");
      const bytes = new Uint8Array(await response.arrayBuffer());
      return Number.isFinite(atoms) && atoms > 0 && bytes.length ? {bytes, atoms, fingerprint} : null;
    } catch (error) {
      console.debug("live coordinates unavailable", error);
      return null;
    }
  }

  /** The live frame shown in the Viewer and the preview, each moved to the
   * new coordinates where it is showing the live frame. False where one
   * could not be moved, so the frame is loaded whole. */
  async function moveTheLiveAtoms(coordinates) {
    const generation = STATE.viewerGeneration;
    if (STATE.mode === "playback") return true;
    STATE.mode = "live";
    const targets = [
      {mini: false, model: STATE.model, engine: STATE.engine},
      {mini: true, model: STATE.miniModel, engine: STATE.miniEngine},
    ];
    for (const target of targets) {
      // Moved whether or not its page is open, which costs a few
      // milliseconds, so it is current, camera and all, when it is opened.
      if (!target.engine || !target.model || target.model.of !== "live") continue;
      if (!await target.engine.setCoordinates(coordinates.bytes)) return false;
      if (!isViewerGenerationCurrent(generation)) return true;
      await giveTheSecondaryStructure(target.engine, "live", STATE.liveFrameIndex, target.mini,
        coordinates.fingerprint);
    }
    // The atoms measured have moved, and so have their numbers.
    if (STATE.picks.length) sayMeasurement();
    return true;
  }

  function liveFrameAge(index) {
    if (!index?.live_frame_updated_at) return "\u2014";
    const time = new Date(index.live_frame_updated_at).getTime();
    if (!Number.isFinite(time)) return "\u2014";
    return howLongAgo(Math.max(0, Math.round((Date.now() - time) / 1000)));
  }

  /* An age as a person reads one. Only minutes were counted, so a frame
   * three days old read "age 5311m". */
  function howLongAgo(seconds) {
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${minutes % 60}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  }

  /* ------------------------------------------------------------------ */
  /* Styling                                                             */
  /* ------------------------------------------------------------------ */
  /** What the engine is to render, from the controls: the preview shows the
   * protein as a spectrum cartoon with its ligand, and nothing else. */
  function applyLook(engine, mini) {
    engine.representation = mini ? "cartoon" : STATE.representation;
    engine.runColour = STATE.runColour;
    const property = !mini ? activeResult() : null;
    if (property) {
      const lookup = STATE.model ? lookupFor(property) : null;
      engine.colour = "result";
      engine.result = engine.registerResult(property, lookup ? lookup.shared : null);
    } else {
      engine.colour = mini || STATE.colorMode.startsWith(RESULT_PREFIX) ? "spectrum" : STATE.colorMode;
    }
    engine.scene = sceneFor(mini);
  }

  function sceneFor(mini) {
    return {
      mini,
      show: Object.assign({}, STATE.visibility, mini ? {water: false, ions: false, pocket: false,
        box: false, hydrogens: false} : {},
      // Frames turned to fit the first are each in a box turned with them:
      // no one box is theirs.
      STATE.superposed !== "none" ? {box: false} : {}),
      ligandNames: ligandResnames(),
      pocketCutoff: STATE.pocketCutoff,
      pocketSurface: !mini && STATE.pocketSurface,
      isolateLigand: !mini && STATE.isolateLigand,
      pocketOnly: !mini && STATE.pocketOnly,
      labels: !mini && STATE.labels,
      focus: mini ? null : STATE.focusIndices,
      // The atoms picked to measure, rendered so the person sees what they picked.
      picks: mini ? null : STATE.picks.map((pick) => pick.index),
      selected: mini || !STATE.selection ? null : STATE.selection.atoms,
      selections: mini ? null : (window.FastMDXSelections?.forScene() || null),
    };
  }

  /** Rendered again as the controls now say, in both engines. */
  async function restyleViewers() {
    sayTheColours();
    const engines = [[STATE.engine, false, STATE.model], [STATE.miniEngine, true, STATE.miniModel]];
    for (const [engine, mini, rendered] of engines) {
      if (!engine || !rendered) continue;
      applyLook(engine, mini);
      try {
        await engine.setScene(engine.scene);
      } catch (error) {
        console.debug("restyle skipped", error);
      }
    }
    if (STATE.engine && STATE.mode === "playback" && STATE.environment) {
      STATE.engine.environmentShow = {water: STATE.visibility.water, ions: STATE.visibility.ions,
        hydrogens: STATE.visibility.hydrogens};
      await STATE.engine.renderEnvironment();
    }
    if (STATE.picks.length) await renderMeasurement();
    sayTheColours();
  }

  /* Centring on the ligand or its pocket, and showing either, did nothing
   * on a structure with no ligand, and said nothing either. They are offered
   * where there is a ligand, and say why not where there is none. */
  function offerTheLigandsControls() {
    const none = !ligandResnames().length;
    const why = "This structure has no ligand";
    const pocket = document.querySelector('#traj-superpose option[value="pocket"]');
    if (pocket) pocket.disabled = none;
    document.querySelectorAll('[data-cam="center-ligand"], [data-cam="center-pocket"]')
      .forEach((button) => {
        button.disabled = none;
        if (none) button.title = why; else button.removeAttribute("title");
      });
    document.querySelectorAll('[data-vis="ligand"], [data-vis="pocket"]').forEach((box) => {
      box.disabled = none;
      // Not ticked where there is nothing to show: ticked and greyed, they
      // read as shown.
      box.checked = !none && !!STATE.visibility[box.getAttribute("data-vis")];
      const label = box.closest("label");
      if (!label) return;
      label.classList.toggle("is-unavailable", none);
      if (none) label.title = why; else label.removeAttribute("title");
    });
    sayLigandShown();
  }

  function ligandResnames() {
    const names = Array.isArray(STATE.structureInfo?.ligand_resnames)
      ? STATE.structureInfo.ligand_resnames.filter(Boolean)
      : [];
    if (STATE.ligandResname && !names.includes(STATE.ligandResname)) names.unshift(STATE.ligandResname);
    return names;
  }

  /* ------------------------------------------------------------------ */
  /* The study's results by residue, and its secondary structure          */
  /* ------------------------------------------------------------------ */
  /* Each per-residue result the analyses wrote (gui/by_residue.py) is a
   * colouring of the protein: blue at the low end of its range, white in
   * the middle, red at the high end, as B-factors are coloured, with the
   * values on a bar over the canvas and each residue's value where the
   * pointer is. A residue with no value is grey. */
  const RESULT_PREFIX = "result:";
  const RESULT_STOPS = [[44, 123, 182], [247, 247, 247], [215, 25, 28]];
  const NO_VALUE = "#5c5c66";

  async function askForResidueValues() {
    const generation = STATE.viewerGeneration;
    if (Date.now() - (STATE.residueValuesAsked || 0) < 5000) return;
    STATE.residueValuesAsked = Date.now();
    try {
      const response = await fetch("/api/residue-values", {cache: "no-store"});
      if (!response.ok || !isViewerGenerationCurrent(generation)) return;
      const said = await response.json();
      if (!isViewerGenerationCurrent(generation)) return;
      // With what the page has added (two states compared, viewer-states.js).
      STATE.residueValues = (Array.isArray(said?.properties) ? said.properties : [])
        .concat(Object.values(STATE.extraResults || {}));
    } catch (error) {
      console.debug("per-residue results unavailable", error);
      return;
    }
    STATE.residueLookups = new Map();
    offerResultColours();
    if (STATE.colorMode.startsWith(RESULT_PREFIX)) await restyleViewers();
  }

  /* The results are offered in the "Coloured by" list where there are any,
   * under a heading of their own; a result that is gone falls back to the
   * spectrum. */
  function offerResultColours() {
    const select = document.getElementById("viewer-color");
    if (!select) return;
    let group = document.getElementById("viewer-color-results");
    const properties = STATE.residueValues || [];
    if (!properties.length) {
      group?.remove();
    } else {
      if (!group) {
        group = document.createElement("optgroup");
        group.id = "viewer-color-results";
        group.label = "From this study's analyses";
        select.appendChild(group);
      }
      const counts = {};
      properties.forEach((property) => { counts[property.label] = (counts[property.label] || 0) + 1; });
      group.replaceChildren(...properties.map((property) => {
        const option = document.createElement("option");
        option.value = RESULT_PREFIX + property.key;
        option.textContent = counts[property.label] > 1
          ? `${property.label} (${property.source})` : property.label;
        option.title = property.about || "";
        return option;
      }));
    }
    if (STATE.colorMode.startsWith(RESULT_PREFIX) && !activeResult()) {
      STATE.colorMode = "spectrum";
    }
    select.value = STATE.colorMode;
    sayTheColours();
  }

  function activeResult() {
    if (!STATE.colorMode.startsWith(RESULT_PREFIX)) return null;
    const key = STATE.colorMode.slice(RESULT_PREFIX.length);
    return (STATE.residueValues || []).find((property) => property.key === key) || null;
  }

  function residueKey(chain, resi, icode) {
    return `${chain == null ? "" : String(chain).trim()}|${resi}|${String(icode || "").trim()}`;
  }

  /* A property's values by residue, and the residues of the structure it
   * cannot name one to one: a number that two residues of the protein
   * share (two chains where the study named none, or insertion codes lost
   * on the way) is grey rather than given to both. */
  function lookupFor(property) {
    if (!STATE.residueLookups) STATE.residueLookups = new Map();
    const rendered = STATE.model;
    const cached = STATE.residueLookups.get(property.key);
    if (cached && cached.rendered === rendered) return cached;
    const chained = property.values.some((row) => row[0] != null);
    const values = new Map(property.values.map(
      (row) => [residueKey(chained ? row[0] : "", row[1], row[2]), Number(row[3])]));
    // A mean over replicas: each residue's standard error across them, and
    // each run's own value (runs_together.py, by_residue.py).
    const spread = new Map(property.values.filter((row) => row.length > 4).map(
      (row) => [residueKey(chained ? row[0] : "", row[1], row[2]), {error: row[4], each: row[5]}]));
    const residues = new Map();
    const runs = STATE.engine && rendered ? STATE.engine.residues() : [];
    runs.forEach((run, position) => {
      const key = residueKey(chained ? run.chain : "", run.resi, run.icode);
      const seen = residues.get(key) || new Set();
      seen.add(position);
      residues.set(key, seen);
    });
    const shared = new Set([...residues].filter(([, seen]) => seen.size > 1).map(([key]) => key));
    let named = 0;
    let unnamed = 0;
    residues.forEach((seen, key) => {
      if (shared.has(key) || (!values.has(key) && property.absent == null)) unnamed += seen.size;
      else named += seen.size;
    });
    const lookup = {rendered, chained, values, spread, shared, named, unnamed};
    STATE.residueLookups.set(property.key, lookup);
    return lookup;
  }

  function valueOfResidue(property, lookup, atom) {
    const key = residueKey(lookup.chained ? atom.chain : "", atom.resi, atom.icode);
    if (lookup.shared.has(key)) return null;
    if (lookup.values.has(key)) return lookup.values.get(key);
    return property.absent == null ? null : Number(property.absent);
  }

  function formatValue(value) {
    if (!Number.isFinite(value)) return "\u2014";
    const magnitude = Math.abs(value);
    if (magnitude !== 0 && (magnitude < 0.01 || magnitude >= 10000)) return value.toExponential(2);
    return Number(value.toPrecision(3)).toString();
  }

  function legendTitle(property) {
    return property.unit ? `${property.label} (${property.unit})` : property.label;
  }

  /* The bar over the canvas and the sentence under the controls: what the
   * colours are, their range, and how many residues have none. */
  function sayTheColours() {
    const legend = document.getElementById("viewer-legend");
    const said = document.getElementById("viewer-colour-said");
    const property = activeResult();
    if (!property || !STATE.model) {
      if (legend) legend.hidden = true;
      if (said) { said.hidden = true; said.textContent = ""; }
      return;
    }
    const lookup = lookupFor(property);
    const low = formatValue(Number(property.low));
    const high = formatValue(Number(property.high));
    const stops = property.reverse ? RESULT_STOPS.slice().reverse() : RESULT_STOPS;
    if (legend) {
      legend.hidden = false;
      legend.title = property.about || "";
      legend.querySelector(".legend-title").textContent = legendTitle(property);
      legend.querySelector(".legend-bar").style.background =
        `linear-gradient(to right, ${stops.map((stop) => `rgb(${stop.join(",")})`).join(", ")})`;
      legend.querySelector(".legend-low").textContent = low;
      legend.querySelector(".legend-high").textContent = high;
      const none = legend.querySelector(".legend-none");
      none.hidden = !lookup.unnamed;
      none.textContent = lookup.named ? `No value: ${lookup.unnamed}` : "No residue shown has a value";
    }
    if (said) {
      said.hidden = false;
      const of = lookup.named + lookup.unnamed;
      said.textContent = `${property.about} From ${property.source}; `
        + `${lookup.named} of the ${of} residues shown have a value.`;
    }
  }

  /* Secondary structure: DSSP for the structure, the live frame or each of
   * the frames, from what the viewer was sent, as the secondary structure
   * analysis computes it. Mol*'s own stands wherever it cannot be had, and
   * the Structure tab says which the cartoon is. */
  async function giveTheSecondaryStructure(engine, of, version, mini, fingerprint) {
    if (!engine) return;
    const generation = STATE.viewerGeneration;
    const tag = `${of}|${version == null ? "" : version}`;
    if (!STATE.secondaryStructures) STATE.secondaryStructures = new Map();
    let said = STATE.secondaryStructures.get(tag);
    if (!said) {
      try {
        const response = await fetch(
          `/api/secondary-structure?of=${encodeURIComponent(of)}&v=${encodeURIComponent(version == null ? "" : version)}`,
          {cache: "no-store"});
        said = response.ok ? await response.json() : {available: false, reason: `HTTP ${response.status}`};
      } catch (error) {
        said = {available: false, reason: String(error)};
      }
      if (!isViewerGenerationCurrent(generation)) return;
      if (said.available) {
        STATE.secondaryStructures.set(tag, said);
        while (STATE.secondaryStructures.size > 4) {
          STATE.secondaryStructures.delete(STATE.secondaryStructures.keys().next().value);
        }
      }
    }
    // A live frame and the frames are rewritten as the run goes on: DSSP of
    // a newer file than the one rendered is not DSSP of what is rendered.
    const same = of === "structure"
      || (of === "frames" ? said.signature === STATE.playbackSignature
        : (!fingerprint || said.fingerprint === fingerprint));
    const frames = engine.frameCount() || 1;
    const fits = said.available && same && (said.frames || []).length === frames;
    let applied = false;
    try {
      applied = await engine.setSecondaryStructure(fits ? said : null) && fits;
    } catch (error) {
      console.debug("secondary structure not given", error);
    }
    if (!mini) {
      STATE.secondaryStructure = {of, applied, frames: said.n_frames || 0,
        reason: applied ? null : (said.reason || "the frames it was computed for are not the ones shown")};
      sayTheSecondaryStructure();
      // What is rendered and its cartoon are known: the sequence follows.
      window.dispatchEvent(new CustomEvent("dashboard:viewer-rendered", {detail: {of}}));
    }
  }

  /* The coordinates of each model's first atom, as gui/by_residue.py
   * takes them: enough to tell two versions of a rewritten file apart. */
  function fingerprintOf(text) {
    const parts = [];
    const atom = /^(?:ATOM  |HETATM).{24}(.{24})/gm;
    const model = /^MODEL/gm;
    const firstAtomFrom = (index) => {
      atom.lastIndex = index;
      const found = atom.exec(text);
      if (found) parts.push(found[1]);
      return found;
    };
    let found = model.exec(text);
    if (!found) {
      firstAtomFrom(0);
      return parts.join("");
    }
    while (found) {
      if (!firstAtomFrom(found.index)) break;
      model.lastIndex = found.index + 5;
      found = model.exec(text);
    }
    return parts.join("");
  }

  function sayTheSecondaryStructure() {
    const line = document.getElementById("viewer-ss-said");
    if (!line) return;
    const said = STATE.secondaryStructure;
    if (!said) { line.textContent = ""; return; }
    if (said.applied) {
      const where = said.of === "frames"
        ? `each of the ${said.frames} frames played`
        : (said.of === "live" ? "the frame shown" : "the structure shown");
      line.textContent = `Secondary structure: DSSP, computed for ${where}, as the study's `
        + "secondary structure analysis computes it.";
    } else {
      line.textContent = "Secondary structure: Mol*'s own DSSP, chain by chain, not the "
        + `study's (${said.reason}).`;
    }
  }

  function secondaryStructureOf(atom) {
    const code = STATE.engine ? STATE.engine.secondaryStructureOf(atom.index) : "c";
    const name = {h: "Helix", s: "Strand"}[code] || "Coil";
    return STATE.secondaryStructure?.applied ? `${name} (DSSP)` : `${name} (Mol*'s DSSP)`;
  }

  /* ------------------------------------------------------------------ */
  /* Controls                                                            */
  /* ------------------------------------------------------------------ */
  function wireControls() {
    document.getElementById("viewer-rep")?.addEventListener("change", (event) => {
      STATE.representation = event.target.value || "cartoon";
      STATE.isolateLigand = false;
      STATE.pocketOnly = false;
      void restyleViewers();
    });
    document.getElementById("viewer-color")?.addEventListener("change", (event) => {
      STATE.colorMode = event.target.value || "spectrum";
      STATE.colourChosen = true;
      void restyleViewers();
    });
    document.querySelectorAll(".chip-toggle input[data-vis]").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const which = checkbox.getAttribute("data-vis");
        STATE.visibility[which] = checkbox.checked;
        if (which === "pocket") sayLigandShown();
        if (which === "water" || which === "ions") {
          if (STATE.mode === "playback") {
            // The frames stay; the solvated system is rendered beside them.
            void ensurePlaybackEnvironment();
            return;
          }
          // Outside playback the atoms themselves have to be fetched or
          // dropped, not merely restyled.
          STATE.structureUrl = null;
          STATE.currentPdb = null;
          STATE.model = null;
          STATE.miniModel = null;
          void onStructureUpdated(STATE.structureInfo || {});
          return;
        }
        void restyleViewers();
      });
    });
    document.querySelectorAll(".chip-btn[data-cam]").forEach((button) => {
      button.addEventListener("click", () => handleCameraAction(button.getAttribute("data-cam")));
    });
    document.querySelectorAll("[data-ligand]").forEach((button) => {
      button.addEventListener("click", () => handleLigandAction(button.getAttribute("data-ligand")));
    });
    document.querySelectorAll(".ctl-btn[data-action]").forEach((button) => {
      button.addEventListener("click", () => handleToolbarAction(button.getAttribute("data-action"), button));
    });
    document.getElementById("pocket-cutoff")?.addEventListener("change", (event) => {
      STATE.pocketCutoff = clamp(Number(event.target.value), 3, 15, 5);
      sayTheCutoffInNanometres();
      void restyleViewers();
      // A pocket superposed on is the pocket at this cutoff.
      if (STATE.superposed === "pocket") void superpose("pocket");
    });
  }

  async function handleToolbarAction(action, button) {
    if (action === "live-toggle") {
      pausePlayback();
      STATE.liveUpdates = true;
      STATE.mode = STATE.liveFrameIndex != null ? "live" : "structure";
      STATE.playbackLoaded = false;
      STATE.framesRendered = false;
      if (STATE.currentPdb) {
        await mountStructure(STATE.currentPdb, {main: true});
        await mountStructure(STATE.currentPdb, {mini: true});
      }
      button?.classList.add("active");
      updatePlaybackButtons();
      announce("Live molecular updates enabled.");
      await pollLiveFrame();
      return;
    }
    if (action === "pause-toggle") {
      STATE.liveUpdates = !STATE.liveUpdates;
      button?.classList.toggle("active", !STATE.liveUpdates);
      if (button) button.textContent = STATE.liveUpdates ? "Pause Updates" : "Resume Updates";
      announce(STATE.liveUpdates ? "Live molecular updates resumed." : "Live molecular updates paused.");
      return;
    }
    if (action === "fullscreen") {
      document.getElementById("viewer-canvas-frame")?.requestFullscreen?.();
      return;
    }
    const engine = await mainEngine();
    if (!engine) return;
    if (action === "play-trajectory") {
      if (STATE.playbackPlaying) pausePlayback();
      else await startPlayback();
      return;
    }
    // Stepping a frame is choosing one, as the transport's own buttons are,
    // so the run stops choosing for the person. Left following, the first
    // press loaded playback at the newest frame and "next" went nowhere.
    if (action === "prev-frame") { stopFollowing(); await seekRelative(-1); return; }
    if (action === "next-frame") { stopFollowing(); await seekRelative(1); return; }
    if (action === "reset-view") {
      setSpinning(engine, false);
      engine.resetView();
      return;
    }
    if (action === "screenshot") await takeScreenshot();
    if (action === "measure") toggleMeasuring(button);
    if (action === "publication") setPublication(!STATE.publication);
  }

  /** The look a figure is made in: a white ground, and the outlines and
   * shading of the highest quality, wherever the page is rendered; off,
   * the ground and quality as set. */
  /** The ground the molecule is shown on: "white", "dark" (black), or
   * "scheme", the page's own (dark on Dark, white on Light). */
  function setGround(ground) {
    STATE.ground = ground === "white" || ground === "scheme" ? ground : "dark";
    if (STATE.engine && !STATE.publication) STATE.engine.setBackground(groundColour());
    const chosen = document.getElementById("viewer-ground");
    if (chosen && chosen.value !== STATE.ground) chosen.value = STATE.ground;
    sayTheGround();
  }

  /* The overlays over the molecule are set for the ground it is on: the
   * one chosen, or white in the publication look. */
  function sayTheGround() {
    const frame = document.getElementById("viewer-canvas-frame");
    if (frame) frame.dataset.ground = STATE.publication || groundIsWhite() ? "light" : "dark";
  }

  function groundColour() {
    if (STATE.ground === "white") return 0xffffff;
    if (STATE.ground === "scheme") {
      let token = "";
      try {
        token = getComputedStyle(document.documentElement).getPropertyValue("--viewer-ground").trim();
      } catch (error) { /* no styles yet */ }
      if (/^#[0-9a-f]{6}$/i.test(token)) return colourNumber(token);
    }
    return colourNumber(STATE.background);
  }

  /* Whether the molecule is on a light ground: what the overlays over it,
   * and the ground button, are set for. */
  function groundIsWhite() {
    const ground = groundColour();
    const r = (ground >> 16) & 255, g = (ground >> 8) & 255, b = ground & 255;
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 140;
  }

  // The scheme's ground follows the scheme as it changes.
  document.addEventListener("fmx:theme", () => {
    if (STATE.ground === "scheme") setGround("scheme");
  });

  function setPublication(on) {
    STATE.publication = !!on;
    const engine = STATE.engine;
    if (engine) {
      engine.setBackground(on ? 0xffffff : groundColour());
      engine.setQuality(on || !engine.softwareRendering() ? "high" : "low");
    }
    document.querySelectorAll('[data-action="publication"]').forEach((button) => {
      button.setAttribute("aria-pressed", String(STATE.publication));
      button.classList.toggle("active", STATE.publication);
    });
    sayTheGround();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const chosen = document.getElementById("viewer-ground");
    if (chosen) chosen.addEventListener("change", () => setGround(chosen.value));
  });

  /* A view: the camera, the frame shown and how the molecule is shown, to
   * be saved with the study (viewer-views.js) and shown again. */
  function viewNow() {
    const engine = STATE.engine;
    const camera = engine ? engine.cameraSnapshot() : null;
    if (!camera) return null;
    const view = {
      camera: {position: Array.from(camera.position), target: Array.from(camera.target),
        up: Array.from(camera.up), radius: camera.radius, fov: camera.fov, mode: camera.mode},
      representation: STATE.representation, colour: STATE.colorMode,
      shown: Object.assign({}, STATE.visibility), superposed: STATE.superposed,
      superposed_to: STATE.superposedTo, smoothed_over: STATE.smoothedOver,
      pocket_cutoff: STATE.pocketCutoff, publication: !!STATE.publication,
      // The ground in effect: a view or scene shown elsewhere has no
      // scheme of this page's to follow.
      ground: groundIsWhite() ? "white" : "dark",
    };
    if (STATE.mode === "playback" && STATE.framesRendered) view.frame = engine.frame();
    return view;
  }

  /* What is on screen, for the Agent: where to look, never what is so. The
   * server's `current_view` tool reads the facts from the study's records
   * (`agent/tools.py`). */
  function currentViewHints() {
    const view = viewNow() || {};
    const dashboard = window.FastMDXDashboard?.state || {};
    const appState = dashboard.appState || {};
    const hints = {
      page: dashboard.activePage || null,
      study: appState.active_run || null,
      frame: Number.isInteger(view.frame) ? view.frame : null,
      representation: view.representation || null,
      colour: view.colour || null,
      superposed: view.superposed || null,
    };
    if (STATE.selection?.expression) hints.expression = STATE.selection.expression;
    else if (Array.isArray(STATE.selection?.residues) && STATE.selection.residues.length) {
      hints.selection = STATE.selection.residues.slice(0, 20).map((residue) => ({
        chain: String(residue.chain || "").slice(0, 32),
        resi: Number.isInteger(residue.resi) ? residue.resi : null,
        icode: String(residue.icode || "").slice(0, 1),
        resn: String(residue.resn || "").slice(0, 8),
      }));
    }
    return Object.fromEntries(Object.entries(hints).filter(([, value]) => value != null));
  }

  async function showView(view) {
    if (!view || !view.camera) return false;
    const generation = STATE.viewerGeneration;
    const rep = document.getElementById("viewer-rep");
    if (view.representation && rep && [...rep.options].some((o) => o.value === view.representation)) {
      STATE.representation = view.representation;
      rep.value = view.representation;
    }
    const colour = document.getElementById("viewer-color");
    if (view.colour && colour && [...colour.options].some((o) => o.value === view.colour)) {
      STATE.colorMode = view.colour;
      STATE.colourChosen = true;
      colour.value = view.colour;
    }
    if (Number.isFinite(view.pocket_cutoff)) {
      STATE.pocketCutoff = clamp(Number(view.pocket_cutoff), 3, 15, 5);
      const cutoff = document.getElementById("pocket-cutoff");
      if (cutoff) cutoff.value = String(STATE.pocketCutoff);
      sayTheCutoffInNanometres();
    }
    // The parts shown, through their own boxes: water and ions are fetched
    // or rendered beside the frames as a click on them would.
    Object.entries(view.shown || {}).forEach(([which, on]) => {
      const box = document.querySelector(`.chip-toggle input[data-vis="${which}"]`);
      if (!box || box.disabled || box.checked === !!on) return;
      box.checked = !!on;
      box.dispatchEvent(new Event("change"));
    });
    if (view.ground) setGround(view.ground);
    setPublication(!!view.publication);
    if (Number.isInteger(view.frame) && (await loadPlayback(STATE.playbackPayload))) {
      stopFollowing();
      await setPlaybackFrame(view.frame);
    }
    if (!isViewerGenerationCurrent(generation)) return false;
    const to = view.superposed_to || "first";
    const over = view.superposed && view.superposed !== "none" ? (view.smoothed_over || 1) : 1;
    if (view.superposed && (view.superposed !== STATE.superposed || to !== STATE.superposedTo
        || over !== STATE.smoothedOver)) {
      const select = document.getElementById("traj-superpose");
      if (select) select.value = view.superposed;
      STATE.superposedTo = to;
      const fitted = document.getElementById("traj-superpose-to");
      if (fitted) fitted.value = to;
      STATE.smoothedOver = over;
      const smooth = document.getElementById("traj-smooth");
      if (smooth) smooth.value = String(over);
      await superpose(view.superposed);
    }
    await restyleViewers();
    if (!isViewerGenerationCurrent(generation) || !STATE.engine) return false;
    STATE.engine.restoreCamera(view.camera);
    return true;
  }

  /* The keys a player has: Space plays and pauses, the arrows step a frame
   * (Shift for ten), Home and End go to the ends, R centres the structure,
   * F fills the screen. Only on the viewer's page, and never while typing. */
  const KEYS = {
    " ": () => handleToolbarAction("play-trajectory"),
    ArrowLeft: (event) => stepBy(event.shiftKey ? -10 : -1),
    ArrowRight: (event) => stepBy(event.shiftKey ? 10 : 1),
    Home: () => goToEnd(false),
    End: () => goToEnd(true),
    r: () => handleToolbarAction("reset-view"),
    f: () => handleToolbarAction("fullscreen"),
    m: () => toggleMeasuring(),
    Escape: () => { if (STATE.picks.length) clearPicks(); },
  };

  async function goToEnd(last) {
    stopFollowing();
    if (await loadPlayback(STATE.playbackPayload)) {
      await setPlaybackFrame(last ? Math.max(0, STATE.playbackFrames - 1) : 0);
    }
  }

  async function stepBy(delta) {
    stopFollowing();
    await seekRelative(delta);
  }

  function wireKeys() {
    document.addEventListener("keydown", (event) => {
      if (document.documentElement.dataset.page !== "viewer") return;
      // A movie being made shows its own frames (viewer-movie.js).
      if (STATE.makingMovie) return;
      if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      const target = event.target;
      // Escape is the one key a button does nothing with.
      if (target && event.key !== "Escape" && (target.isContentEditable
          || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName))) return;
      const handler = KEYS[event.key.length === 1 ? event.key.toLowerCase() : event.key];
      if (!handler) return;
      event.preventDefault();
      handler(event);
    });
  }

  /* The viewer is in angstroms and every analysis in nanometres; where the
   * two meet the cutoff is said in both. */
  function sayTheCutoffInNanometres() {
    const said = document.getElementById("pocket-cutoff-nm");
    if (said) said.textContent = `(${(STATE.pocketCutoff / 10).toFixed(2)} nm)`;
  }

  function setSpinning(engine, on) {
    if (engine) engine.spin(on);
    STATE.spinning = on;
    document.querySelectorAll('[data-cam="spin"]').forEach((button) => {
      button.setAttribute("aria-pressed", String(on));
      button.classList.toggle("active", on);
    });
  }

  async function handleCameraAction(action) {
    const engine = await mainEngine();
    if (!engine) return;
    // One button: it spins, and stops what it started.
    if (action === "spin") {
      setSpinning(engine, !STATE.spinning);
      return;
    }
    if (action === "stop") {
      setSpinning(engine, false);
      return;
    }
    if (action === "center-protein") engine.focusPart("polymer") || engine.resetView();
    if (action === "center-ligand" && ligandResnames().length) engine.focusPart("ligand");
    if (action === "center-pocket" && ligandResnames().length) {
      engine.focusPart("pocket") || engine.focusPart("ligand");
    }
    if (action === "zoom-in") engine.zoom(1.2);
    if (action === "zoom-out") engine.zoom(0.8);
  }

  async function handleLigandAction(action) {
    const engine = await mainEngine();
    const ligands = ligandResnames();
    if (!engine || !ligands.length) {
      announce("No ligand is available for this control.");
      return;
    }
    if (action === "center") engine.focusPart("ligand");
    if (action === "isolate") {
      STATE.isolateLigand = !STATE.isolateLigand;
      STATE.pocketOnly = false;
      sayLigandShown();
      await restyleViewers();
      engine.focusPart("ligand");
    }
    if (action === "show-pocket") {
      // A switch, as Display's Binding pocket is, and kept with it.
      STATE.visibility.pocket = !STATE.visibility.pocket;
      const box = document.querySelector('.chip-toggle input[data-vis="pocket"]');
      if (box) box.checked = STATE.visibility.pocket;
      STATE.isolateLigand = false;
      sayLigandShown();
      await restyleViewers();
      if (STATE.visibility.pocket) engine.focusPart("pocket");
    }
    if (action === "show-pocket-surface") {
      STATE.pocketSurface = !STATE.pocketSurface;
      sayLigandShown();
      await restyleViewers();
    }
    if (action === "hide-distant") {
      STATE.pocketOnly = !STATE.pocketOnly;
      STATE.isolateLigand = false;
      sayLigandShown();
      await restyleViewers();
      engine.focusPart(STATE.pocketOnly ? "pocket" : "polymer");
    }
    if (action === "show-labels") {
      STATE.labels = !STATE.labels;
      sayLigandShown();
      await restyleViewers();
    }
    if (action === "show-contacts") {
      if (STATE.contactsShown) {
        await engine.showContacts([]);
        STATE.contactsShown = false;
        announce("The geometric contacts are hidden.");
      } else {
        STATE.contactsShown = await showGeometricContacts();
      }
    }
    sayLigandShown();
  }

  /* Each of the ligand's switches says whether it is on, as a pressed
   * button does: they changed the molecule and said nothing of themselves,
   * so whether a click had turned one on or off was a guess. */
  function sayLigandShown() {
    const on = {
      "isolate": STATE.isolateLigand, "show-pocket": STATE.visibility.pocket,
      "show-pocket-surface": STATE.pocketSurface, "hide-distant": STATE.pocketOnly,
      "show-labels": STATE.labels, "show-contacts": STATE.contactsShown,
    };
    document.querySelectorAll("[data-ligand]").forEach((button) => {
      const action = button.getAttribute("data-ligand");
      if (action in on) button.setAttribute("aria-pressed", on[action] ? "true" : "false");
    });
  }

  /* The ligand's closest contacts: for each ligand atom, the nearest atom
   * of the protein within the cutoff, the thirty shortest of them rendered
   * dashed with their distances. In the frame shown, as rendered. */
  async function showGeometricContacts() {
    const engine = STATE.engine;
    const ligands = ligandResnames();
    if (!engine || !STATE.model || !ligands.length) return;
    try {
      // Heavy atoms, as the contacts analysis and the pocket count them.
      const ligandAtoms = engine.find({resn: ligands}).filter((atom) => atom.elem !== "H");
      const cutoff = STATE.pocketCutoff;
      const near = (a, b) => Math.abs(a.x - b.x) <= cutoff && Math.abs(a.y - b.y) <= cutoff
        && Math.abs(a.z - b.z) <= cutoff;
      const others = engine.find({resn: AMINO_ACIDS}).filter((atom) => atom.elem !== "H");
      const contacts = [];
      ligandAtoms.forEach((ligandAtom) => {
        let closest = null;
        let closestDistance = Infinity;
        others.forEach((atom) => {
          if (!near(ligandAtom, atom)) return;
          const distance = Math.hypot(ligandAtom.x - atom.x, ligandAtom.y - atom.y, ligandAtom.z - atom.z);
          if (distance <= cutoff && distance < closestDistance) {
            closest = atom;
            closestDistance = distance;
          }
        });
        if (closest) contacts.push({a: ligandAtom.index, b: closest.index, distance: closestDistance});
      });
      const shown = contacts.sort((x, y) => x.distance - y.distance).slice(0, 30);
      await engine.showContacts(shown.map((contact) => [contact.a, contact.b]));
      STATE.picks = [];
      sayMeasurement();
      announce(`Displayed ${shown.length} geometric contacts within ${STATE.pocketCutoff} Å.`);
      return shown.length > 0;
    } catch (error) {
      console.warn("geometric contact rendering failed", error);
      announce("Geometric contacts could not be calculated for this structure.");
      return false;
    }
  }

  /* A picture for a page, not for the screen: as many pixels across as
   * chosen, 2,400 unless another width is, which is a double-column figure
   * (183 mm) at 300 dpi; 1,200 is a single column (89 mm). The view's own
   * shape, whatever the width. */
  const PICTURE_WIDTH_PX = 2400;

  function pictureWidth() {
    const chosen = Number(document.getElementById("picture-width")?.value);
    return Number.isFinite(chosen) && chosen >= 300 && chosen <= 8000 ? Math.round(chosen) : PICTURE_WIDTH_PX;
  }

  async function takeScreenshot() {
    const engine = STATE.engine;
    const canvas = document.querySelector("#viewer-canvas canvas");
    if (!engine || !canvas) return;
    const width = pictureWidth();
    const height = Math.max(1, Math.round(width * canvas.clientHeight / Math.max(1, canvas.clientWidth)));
    const transparent = !!document.getElementById("picture-transparent")?.checked;
    let uri = "";
    try {
      uri = await engine.picture(width, height, transparent);
    } catch (error) {
      console.warn("the picture could not be made", error);
      return;
    }
    if (!uri) return;
    // Coloured by a result, the picture carries its colour bar: without it
    // the colours say nothing on a slide or in a figure.
    const property = activeResult();
    if (property) {
      try {
        uri = await withTheLegend(uri, property);
      } catch (error) {
        console.debug("the colour bar was left off the picture", error);
      }
    }
    saveThePicture(uri);
  }

  function saveThePicture(uri) {
    const anchor = document.createElement("a");
    anchor.href = uri;
    anchor.download = "fastmdxplora-molecular-viewer.png";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }

  /* The picture with the colour bar in its lower left corner, sized
   * to the picture as the bar on screen is sized to the canvas. */
  function withTheLegend(uri, property) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onerror = reject;
      image.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = image.naturalWidth;
          canvas.height = image.naturalHeight;
          const context = canvas.getContext("2d");
          context.drawImage(image, 0, 0);
          drawTheLegend(context, property);
          resolve(canvas.toDataURL("image/png"));
        } catch (error) {
          reject(error);
        }
      };
      image.src = uri;
    });
  }

  /** The colour bar of a result, in the lower left corner of what
   * ``context`` draws on: a picture, or a frame of a movie. */
  function drawTheLegend(context, property) {
    const canvas = context.canvas;
    const scale = Math.max(1, canvas.width / 900);
    const pad = 10 * scale;
    const width = 200 * scale;
    const lookup = STATE.model ? lookupFor(property) : null;
    const none = lookup && lookup.unnamed
      ? (lookup.named ? `No value: ${lookup.unnamed}` : "No residue shown has a value") : "";
    const height = (none ? 74 : 56) * scale;
    const left = 14 * scale;
    const top = canvas.height - 14 * scale - height;
    context.save();
    context.fillStyle = "rgba(5, 5, 5, 0.82)";
    context.fillRect(left, top, width, height);
    context.fillStyle = "#f5f5f7";
    context.textBaseline = "top";
    context.font = `600 ${12 * scale}px sans-serif`;
    context.fillText(legendTitle(property), left + pad, top + pad, width - 2 * pad);
    const bar = context.createLinearGradient(left + pad, 0, left + width - pad, 0);
    const stops = property.reverse ? RESULT_STOPS.slice().reverse() : RESULT_STOPS;
    stops.forEach((stop, index) => bar.addColorStop(index / (stops.length - 1), `rgb(${stop.join(",")})`));
    context.fillStyle = bar;
    context.fillRect(left + pad, top + pad + 18 * scale, width - 2 * pad, 10 * scale);
    context.fillStyle = "#c4c4ca";
    context.font = `${11 * scale}px monospace`;
    const ends = top + pad + 31 * scale;
    context.textAlign = "left";
    context.fillText(formatValue(Number(property.low)), left + pad, ends);
    context.textAlign = "right";
    context.fillText(formatValue(Number(property.high)), left + width - pad, ends);
    if (none) {
      context.textAlign = "left";
      context.fillStyle = NO_VALUE;
      context.fillRect(left + pad, ends + 17 * scale, 10 * scale, 10 * scale);
      context.fillStyle = "#c4c4ca";
      context.font = `${11 * scale}px sans-serif`;
      context.fillText(none, left + pad + 16 * scale, ends + 16 * scale);
    }
    context.restore();
  }

  /* ------------------------------------------------------------------ */
  /* Playback                                                            */
  /* ------------------------------------------------------------------ */
  /* The trajectory is the binary frames the server writes
   * (gui/trajectory_frames.py, `/api/frames-info`): a topology once and
   * the frames as a DCD, made whole and centred on the protein. */
  function onPlaybackReady(payload) {
    const signature = payload?.source_signature || payload?.compiled_at || null;
    const changed = !!(STATE.playbackSignature && signature && STATE.playbackSignature !== signature);
    const previousPayload = STATE.playbackPayload;
    const sameLiveHistorySource = changed
      && STATE.playbackLoaded
      && previousPayload?.source_kind === "live-history"
      && payload?.source_kind === "live-history";
    STATE.playbackPayload = payload || null;
    sayTheRuns(payload);
    // The other runs changed (one wrote more snapshots, or finished): they
    // are rendered again, and the frames played are left as they are.
    const runsNow = runsTogether(payload);
    if (runsNow && STATE.playbackLoaded && STATE.engine && STATE.runsShownSignature
        && runsNow.signature && runsNow.signature !== STATE.runsShownSignature) {
      void showTheRuns(STATE.engine, payload).then((shown) => {
        if (shown) return STATE.engine.setRunsAside(!runsFitAsPlayed());
        return null;
      });
    }
    // A running job appends to its history, so the signature changes on
    // every poll. That is not a new trajectory: keep the frames playing and
    // the new payload for the next explicit reload.
    if (sameLiveHistorySource) {
      updatePlaybackButtons();
      return;
    }
    STATE.playbackSignature = signature;
    STATE.playbackFrameTimes = Array.isArray(payload?.frame_times_ns) ? payload.frame_times_ns : [];
    STATE.playbackFrames = Number(payload?.n_frames_browser || 0);
    if (changed && !sameLiveHistorySource) {
      STATE.playbackLoaded = false;
      STATE.framesRendered = false;
      if (STATE.mode === "playback") {
        pausePlayback();
        announce("New trajectory frames are available. Press Play Trajectory to reload them.");
      }
    }
    updatePlaybackButtons();
  }

  async function requestPlaybackPayload(force) {
    const generation = STATE.viewerGeneration;
    try {
      const response = await fetch(`/api/frames-info${force ? "?force=1" : ""}`, {cache: "no-store"});
      if (!isViewerGenerationCurrent(generation)) return null;
      if (!response.ok) throw new Error(`frames info HTTP ${response.status}`);
      const payload = await response.json();
      if (!isViewerGenerationCurrent(generation)) return null;
      onPlaybackReady(payload);
      return payload;
    } catch (error) {
      console.warn("frames information request failed", error);
      return null;
    }
  }

  async function ensurePlaybackPayload() {
    if (STATE.playbackPayload?.playback_available) return STATE.playbackPayload;
    const payload = await requestPlaybackPayload(false);
    if (!payload?.playback_available) {
      const reason = String(payload?.reason || "not enough frames yet");
      announce(`Trajectory playback is not ready: ${reason} Live molecular updates remain active.`);
      return null;
    }
    return payload;
  }

  function framesUrls(payload) {
    const version = encodeURIComponent(payload?.compiled_at || Date.now());
    return [`/structure/frames-topology.pdb?v=${version}`, `/structure/frames.dcd?v=${version}`];
  }

  async function loadPlayback(payload) {
    const generation = STATE.viewerGeneration;
    const available = payload?.playback_available ? payload : await ensurePlaybackPayload();
    if (!isViewerGenerationCurrent(generation) || !available) return false;
    const signature = available.source_signature || available.compiled_at || null;
    if (STATE.playbackLoaded && STATE.playbackSignature === signature) return true;
    if (STATE.playbackLoadPromise) return STATE.playbackLoadPromise;

    let loadPromise;
    loadPromise = (async () => {
      try {
        const engine = await mainEngine();
        if (!engine) return false;
        STATE.playbackSignature = signature;
        STATE.playbackFrames = Number(available.n_frames_browser || 0);
        STATE.playbackFrameTimes = Array.isArray(available.frame_times_ns) ? available.frame_times_ns : [];
        STATE.mode = "playback";
        STATE.environment = false;
        applyLook(engine, false);
        const [topology, coordinates] = framesUrls(available);
        // Each frame's box, centred as the frames were made whole.
        engine.setCells(available.cells, "diagonal");
        const loaded = await loadTheFrames(engine, topology, coordinates, generation);
        if (!isViewerGenerationCurrent(generation)) return false;
        if (!loaded.frames) throw new Error("the viewer made no frames of the trajectory");
        // Frames written again are superposed again, as they were asked.
        STATE.superposedUrl = null;
        STATE.framesCoordinatesUrl = coordinates;
        STATE.playbackFrames = loaded.frames;
        STATE.model = {atoms: loaded.atoms, frames: loaded.frames, of: "frames"};
        window.dispatchEvent(new CustomEvent("dashboard:viewer-rendered", {detail: {of: "frames"}}));
        STATE.residueLookups = new Map();
        if (activeResult()) applyLook(engine, false);
        document.getElementById("viewer-canvas-frame")?.setAttribute("data-ready", "true");
        STATE.framesRendered = true;
        await giveTheSecondaryStructure(engine, "frames", signature, false, null);
        await loadMiniFrames();
        if (needsFullTopology()) await ensurePlaybackEnvironment();
        if (!isViewerGenerationCurrent(generation)) return false;
        document.getElementById("trajectory-row")?.removeAttribute("hidden");
        /* Follow the run: when frames arrive while it is ticked, show the
         * newest rather than returning to wherever the slider was. Scrubbing
         * back unticks it, so a person looking at frame 40 is not dragged
         * to frame 200 by the next poll. Only while the run writes frames:
         * the box is hidden, and still ticked, for a finished study, whose
         * first Play jumped to the last frame and stopped there. */
        const follow = document.getElementById("traj-follow")?.checked && runIsWriting();
        const current = Number(document.getElementById("traj-slider")?.value || 0);
        const target = follow && STATE.playbackFrames > 0 ? STATE.playbackFrames - 1 : current;
        await setPlaybackFrame(Math.max(0, target));
        if (!isViewerGenerationCurrent(generation)) return false;
        // Loaded once a frame is shown, with its cartoon and its preview.
        STATE.playbackLoaded = true;
        const together = await showTheRuns(engine, available);
        if (!isViewerGenerationCurrent(generation)) return false;
        if (STATE.superposed !== "none") await superpose(STATE.superposed);
        if (together) await engine.setRunsAside(!runsFitAsPlayed());
        sayTheColours();
        updatePlaybackButtons();
        // What is placed on the first frame (viewer-occupancy.js) is placed
        // again on the frames now loaded.
        window.dispatchEvent(new CustomEvent("dashboard:frames-ready"));
        return true;
      } catch (error) {
        console.warn("trajectory playback load failed", error);
        announce("Trajectory playback could not be loaded. Live molecular updates still work.");
        STATE.playbackLoaded = false;
        STATE.framesRendered = false;
        return false;
      } finally {
        if (STATE.playbackLoadPromise === loadPromise) STATE.playbackLoadPromise = null;
      }
    })();
    STATE.playbackLoadPromise = loadPromise;
    return loadPromise;
  }

  /** The frames fetched in pieces (`/api/frames-pieces`, XTC: about a
   * third of a DCD) and played from the first while the rest arrive, the
   * frames loaded so far said; as one DCD where there are no pieces. */
  async function loadTheFrames(engine, topology, coordinates, generation) {
    let pieces = null;
    try {
      pieces = await (await fetch("/api/frames-pieces", {cache: "no-store"})).json();
    } catch (error) {
      pieces = null;
    }
    STATE.framesPieces = null;
    if (!pieces || !pieces.ok || !Array.isArray(pieces.pieces) || !pieces.pieces.length) {
      return engine.loadFrames(topology, coordinates);
    }
    const version = Date.now();
    const fetched = async (piece) => new Uint8Array(await (await fetch(
      `${piece.url}&v=${version}`, {cache: "no-store"})).arrayBuffer());
    const parts = [await fetched(pieces.pieces[0])];
    const loaded = await engine.loadFramesFromBytes(topology, joinedBytes(parts), "xtc");
    STATE.framesPieces = {total: pieces.total, loaded: loaded.frames, pieces: pieces.pieces.length};
    sayFramesLoaded();
    if (pieces.pieces.length > 1) {
      announce(`Playing the first ${loaded.frames} of ${pieces.total} frames while the rest arrive.`);
      void (async () => {
        for (const piece of pieces.pieces.slice(1)) {
          const bytes = await fetched(piece);
          if (!isViewerGenerationCurrent(generation) || STATE.engine !== engine) return;
          parts.push(bytes);
          const count = await engine.appendFrames(joinedBytes(parts));
          // Given whole meanwhile (the frames superposed), or loaded again.
          if (!count || !STATE.framesPieces) return;
          STATE.framesPieces.loaded = count;
          STATE.playbackFrames = count;
          if (STATE.model && STATE.model.of === "frames") STATE.model.frames = count;
          sayFramesLoaded();
        }
        announce(`All ${pieces.total} frames have arrived.`);
      })().catch((error) => console.debug("the rest of the frames did not arrive", error));
    }
    return loaded;
  }

  function joinedBytes(parts) {
    const whole = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
    let at = 0;
    parts.forEach((part) => { whole.set(part, at); at += part.length; });
    return whole;
  }

  /** How many of the frames have arrived, beside the transport. */
  function sayFramesLoaded() {
    const row = document.getElementById("trajectory-row");
    const pieces = STATE.framesPieces;
    if (!row || !pieces) return;
    row.setAttribute("data-frames-loaded", String(pieces.loaded));
    const said = document.getElementById("traj-loaded");
    if (said) {
      said.hidden = pieces.loaded >= pieces.total;
      said.textContent = `${pieces.loaded} of ${pieces.total} frames here`;
    }
  }

  /** The frames played superposed on the first, on the protein's backbone
   * or on the backbone of the ligand's pocket, or as written ("none"). The
   * server fits them (`/api/frames-superposed`); the Viewer and the preview
   * then read them in place of the frames shown, at the same frame. */
  async function superpose(on) {
    const generation = STATE.viewerGeneration;
    const select = document.getElementById("traj-superpose");
    const label = document.getElementById("traj-superpose-label");
    const previous = STATE.superposed;
    const previousTo = STATE.appliedTo || "first";
    const previousOver = STATE.appliedSmooth || 1;
    STATE.superposed = on;
    const to = STATE.superposedTo;
    const over = on === "none" ? 1 : STATE.smoothedOver;
    if (!STATE.framesCoordinatesUrl || !STATE.framesRendered || !STATE.engine) return;
    let url = STATE.framesCoordinatesUrl;
    let said = "as written";
    if (on !== "none") {
      const query = new URLSearchParams({on});
      if (STATE.superposedTo !== "first") query.set("to", STATE.superposedTo);
      if (over > 1) query.set("smooth", String(over));
      if (on === "pocket") {
        query.set("ligand", STATE.ligandResname || ligandResnames()[0] || "");
        query.set("cutoff", String(STATE.pocketCutoff));
      }
      let answer = null;
      try {
        answer = await (await fetch(`/api/frames-superposed?${query}`, {cache: "no-store"})).json();
      } catch (error) {
        answer = {ok: false, reason: "The server did not answer."};
      }
      if (!isViewerGenerationCurrent(generation) || STATE.superposed !== on
          || STATE.superposedTo !== to || STATE.smoothedOver !== over) return;
      if (!answer || !answer.ok) {
        STATE.superposed = previous === on && previousTo === to && previousOver === over
          ? "none" : previous;
        STATE.superposedTo = previousTo;
        STATE.smoothedOver = STATE.superposed === "none" ? 1 : previousOver;
        if (select) select.value = STATE.superposed;
        const fitted = document.getElementById("traj-superpose-to");
        if (fitted) fitted.value = previousTo;
        const smooth = document.getElementById("traj-smooth");
        if (smooth) smooth.value = String(STATE.smoothedOver);
        announce(`The frames could not be superposed: ${(answer && answer.reason) || "no reason given"}`);
        return;
      }
      url = answer.url;
      said = `superposed on ${answer.said}`;
    }
    const moved = await STATE.engine.setFramesCoordinates(url);
    if (!isViewerGenerationCurrent(generation) || STATE.superposed !== on) return;
    if (!moved) {
      announce("The frames could not be read again; they are shown as they were.");
      return;
    }
    // Frames still arriving in pieces are now here whole.
    STATE.playbackFrames = STATE.engine.frameCount();
    if (STATE.framesPieces) {
      STATE.framesPieces.loaded = STATE.playbackFrames;
      sayFramesLoaded();
    }
    STATE.superposedUrl = on === "none" ? null : url;
    // Fitted to the deposited structure, the molecule is where that
    // structure's coordinates put it, not in the box the camera was on: it
    // left the view, and the camera aimed at nothing read its matrices as
    // NaN. The camera follows the molecule there and back.
    const placeMoved = (on === "none" ? "first" : to) !== (previous === "none" ? "first" : previousTo)
      && [to, previousTo].includes("deposited");
    STATE.appliedTo = to;
    STATE.appliedSmooth = over;
    if (placeMoved) STATE.engine.fit();
    const runs = STATE.engine.runsShown().length > 0
      || STATE.engine.runsShown("beside").length > 0;
    if (runs) await STATE.engine.setRunsAside(!runsFitAsPlayed());
    const placed = STATE.engine.volumesShown().length > 0 || !!STATE.engine.sitesRef
      || !!STATE.engine.motionShown();
    if (placed) await STATE.engine.setPlacedAside(!placedFitAsPlayed());
    if (STATE.visibility.box) await STATE.engine.showBox(on === "none");
    // Water and ions are the first frame's: they sit where it does, and a
    // first frame fitted to another structure has moved from them.
    if (STATE.environment ? environmentMoved() : (!environmentMoved() && needsFullTopology())) {
      await ensurePlaybackEnvironment();
    }
    if (STATE.miniEngine && STATE.miniModel?.of === "frames") {
      await STATE.miniEngine.setFramesCoordinates(url);
    }
    if (select) select.value = on;
    if (label) label.setAttribute("data-said", said);
    announce((on === "none" ? "The frames are shown as they were written."
      : `Each frame is ${said}.`
        + (STATE.visibility.box ? " The periodic box is not shown in frames turned to fit." : "")
        + (environmentMoved() && (STATE.visibility.water || STATE.visibility.ions)
          ? " Water and ions are not shown: they are the first frame's, and it has moved." : "")
        + (over > 1 ? " An average shortens bonds a little: measure on frames as written." : ""))
      + (runs && !runsFitAsPlayed() ? " The other runs are hidden: they are fitted on the backbone"
        + " to the first frame, and are shown again when these frames are." : "")
      + (placed && !placedFitAsPlayed() ? " What is placed on the first frame is hidden: it is placed"
        + " on the first frame, and are shown again when the frames are fitted to it." : ""));
  }

  /** Whether the frames played are fitted as the maps and water sites are
   * placed: on the pocket or the backbone, to the first frame, unsmoothed. */
  function placedFitAsPlayed() {
    return (STATE.superposed === "pocket" || STATE.superposed === "backbone")
      && (STATE.appliedTo || "first") === "first" && (STATE.appliedSmooth || 1) === 1;
  }

  /* ------------------------------------------------------------------ */
  /* The runs of a study, played together                                */
  /* ------------------------------------------------------------------ */
  /* A study of several runs plays its first run with a trajectory as any
   * study's frames are played, and renders the other runs of the same
   * atoms beside it, each in its colour, fitted on the backbone to the
   * first frame of the run played (gui/runs_together.py). */
  function runsTogether(payload) {
    const together = payload && payload.runs_together;
    return together && Array.isArray(together.runs) ? together : null;
  }

  /** Whether the run played is fitted as the others are: on the backbone
   * to its first frame, and not smoothed. */
  function runsFitAsPlayed() {
    return STATE.superposed === "backbone" && (STATE.appliedTo || "first") === "first"
      && (STATE.appliedSmooth || 1) === 1;
  }

  /** The other runs rendered beside the frames just loaded; the frames
   * superposed on the backbone the first time, so all the runs are on one
   * shared structure. Null where the study is of one run. */
  async function showTheRuns(engine, payload) {
    const together = runsTogether(payload);
    const others = together ? together.runs.filter((run) => !run.main) : [];
    if (!others.length) {
      if (engine.runsShown().length) await engine.removeRuns();
      return null;
    }
    if (!STATE.runsFittedOnce) {
      STATE.runsFittedOnce = true;
      if (STATE.superposed === "none") {
        STATE.superposed = "backbone";
        const select = document.getElementById("traj-superpose");
        if (select) select.value = "backbone";
      }
    }
    // Asked for afresh: a run finishing writes its frames again. A run still
    // running has atoms of its own (its snapshots').
    const version = Date.now();
    STATE.runsShownSignature = together.signature || null;
    await engine.showRuns(others.map((run) => ({label: run.label, colour: run.colour,
      shown: !STATE.runsHidden.has(run.run_id),
      topology: `${run.topology || "/structure/frames-topology.pdb?"}`
        + `${run.topology ? "&" : ""}v=${version}`,
      coordinates: `/structure/frames.dcd?run=${run.index}&v=${version}`})), STATE.runColour);
    sayTheRuns(payload);
    return together;
  }

  /** The Runs section: each run in its colour, the others each shown or
   * hidden, and how they were fitted; the colour by run offered. */
  function sayTheRuns(payload) {
    const together = runsTogether(payload);
    const section = document.getElementById("side-runs");
    const list = document.getElementById("viewer-runs-list");
    const note = document.getElementById("viewer-runs-note");
    const several = !!(together && together.runs.length + together.excluded.length > 1);
    STATE.runsTogether = several ? together : null;
    const played = several ? together.runs.find((run) => run.main) : null;
    STATE.runColour = played ? Number.parseInt(played.colour.slice(1), 16) : null;
    offerTheRunColour(several && together.runs.length > 1);
    if (!section || !list) return;
    section.hidden = !several;
    const engineRuns = STATE.engine ? STATE.engine.runsShown() : [];
    // Each poll brings the same runs: the list is made again only when
    // they change, so a box is not replaced as it is clicked.
    const key = JSON.stringify([together, engineRuns.length]);
    if (key === STATE.runsSaid) return;
    STATE.runsSaid = key;
    list.replaceChildren();
    if (!several) {
      if (note) note.textContent = "";
      return;
    }
    together.runs.forEach((run) => {
      const item = document.createElement("li");
      item.className = "viewer-run";
      item.setAttribute("data-run", run.run_id);
      const swatch = document.createElement("span");
      swatch.className = "viewer-run-swatch";
      swatch.style.background = run.colour;
      const name = document.createElement("span");
      name.textContent = run.label;
      const said = document.createElement("span");
      said.className = "viewer-run-said";
      if (run.main) {
        said.textContent = run.running ? `played, running, ${run.frames} frames so far`
          : `played, ${run.frames} frames`;
        said.title = "What is clicked, the ruler, the pocket and colours by a result are this run's";
        item.append(swatch, name, said);
      } else {
        const label = document.createElement("label");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = !STATE.runsHidden.has(run.run_id);
        box.setAttribute("aria-label", `Show ${run.label}`);
        box.addEventListener("change", async () => {
          if (box.checked) STATE.runsHidden.delete(run.run_id);
          else STATE.runsHidden.add(run.run_id);
          const at = together.runs.filter((one) => !one.main).indexOf(run);
          if (STATE.engine) await STATE.engine.setRunShown(at, box.checked);
        });
        label.append(box, swatch, name);
        said.textContent = run.running ? `running, ${run.frames} frames so far`
          : `${run.frames} frames`;
        item.append(label, said);
      }
      list.append(item);
    });
    together.excluded.forEach((run) => {
      const item = document.createElement("li");
      item.className = "viewer-run is-excluded";
      item.setAttribute("data-run", run.run_id);
      const said = document.createElement("span");
      said.className = "viewer-run-said";
      said.textContent = `${run.label}: not shown. ${run.reason}`;
      item.append(said);
      list.append(item);
    });
    if (note) {
      const loaded = engineRuns.length > 0 || together.runs.length < 2;
      note.textContent = (together.fitted ? `Each run is fitted ${together.fitted}, and shown at `
        + `the frames of ${together.first}'s simulation; a run with fewer frames is not shown `
        + "past its last." : "")
        + (loaded ? "" : " The other runs are shown with the frames: press Play.");
    }
  }

  /** Coloured by run, offered while runs are played together and chosen
   * the first time they are, unless the person chose a colour before they
   * came; taken away, with the colour it stood in for kept, when they are
   * not. */
  function offerTheRunColour(offered) {
    const select = document.getElementById("viewer-color");
    if (!select) return;
    let option = select.querySelector('option[value="run"]');
    if (offered && !option) {
      option = document.createElement("option");
      option.value = "run";
      option.textContent = "Run";
      select.insertBefore(option, select.firstChild);
      if (!STATE.runColourChosen && !STATE.colourChosen) {
        STATE.runColourChosen = true;
        STATE.colorMode = "run";
        select.value = "run";
        void restyleViewers();
      }
    } else if (!offered && option) {
      option.remove();
      if (STATE.colorMode === "run") {
        STATE.colorMode = "spectrum";
        select.value = "spectrum";
        void restyleViewers();
      }
    }
  }

  /** The preview plays the same frames, where it is shown. */
  async function loadMiniFrames() {
    const preview = document.getElementById("mini-preview-canvas");
    if (!preview || !isVisible(preview) || !STATE.playbackPayload) return;
    const engine = await miniEngine();
    if (!engine) return;
    applyLook(engine, true);
    const [topology, coordinates] = framesUrls(STATE.playbackPayload);
    const loaded = await engine.loadFrames(topology, STATE.superposedUrl || coordinates);
    STATE.miniModel = {atoms: loaded.atoms, frames: loaded.frames, of: "frames"};
    document.getElementById("mini-preview-frame")?.setAttribute("data-ready", "true");
    await giveTheSecondaryStructure(engine, "frames", STATE.playbackSignature, true, null);
  }

  /* A residue chosen elsewhere (a point of the RMSF on the Analysis page),
   * rendered in full over whatever else is shown, and labelled. */
  async function focusTheResidue() {
    const residue = STATE.focusResidue;
    const engine = STATE.engine;
    if (!residue || !engine) {
      STATE.focusIndices = null;
      return [];
    }
    const wanted = {resi: residue.resi};
    if (residue.chain) wanted.chain = residue.chain;
    const atoms = engine.find(wanted);
    STATE.focusIndices = atoms.map((atom) => atom.index);
    await restyleViewers();
    if (atoms.length) engine.focus(STATE.focusIndices);
    return atoms;
  }

  /* Once the viewer has a structure: a residue asked for as the page opens
   * is shown when there is something to show it in. */
  function whenRendered(then, tries) {
    const left = tries == null ? 60 : tries;
    if (STATE.engine && (STATE.model || STATE.playbackLoaded)) {
      then();
    } else if (left > 0) {
      window.setTimeout(() => whenRendered(then, left - 1), 150);
    }
  }

  function wireResidueFocus() {
    window.addEventListener("dashboard:residue-focus", (event) => {
      const detail = event.detail || {};
      const resi = Number(detail.resi);
      STATE.focusResidue = Number.isFinite(resi)
        ? {resi, chain: detail.chain || null} : null;
      whenRendered(async () => {
        const atoms = await focusTheResidue();
        if (!STATE.focusResidue) return;
        const name = `${atoms[0]?.resn || "residue"} ${detail.chain ? detail.chain + ":" : ""}${resi}`;
        announce(atoms.length ? `${name} shown` : `Residue ${resi} is not in the structure shown`);
      });
    });
  }

  function wireTrajectoryControls() {
    window.addEventListener("dashboard:trajectory-action", async (event) => {
      /* Any transport action except "last" is a person choosing a frame;
       * the run should stop choosing for them. "last" is what follow does,
       * so it leaves the toggle alone. */
      if (event.detail?.action && event.detail.action !== "last") stopFollowing();
      const action = event.detail?.action;
      if (action === "play") await startPlayback();
      if (action === "pause") pausePlayback();
      if (action === "reverse") {
        STATE.playbackReverse = !STATE.playbackReverse;
        await startPlayback();
      }
      if (action === "prev") await seekRelative(-1);
      if (action === "next") await seekRelative(1);
      if (action === "first") {
        if (await loadPlayback(STATE.playbackPayload)) await setPlaybackFrame(0);
      }
      if (action === "last") {
        if (await loadPlayback(STATE.playbackPayload)) await setPlaybackFrame(Math.max(0, STATE.playbackFrames - 1));
      }
    });
    window.addEventListener("dashboard:trajectory-seek", async (event) => {
      stopFollowing();
      if (await loadPlayback(STATE.playbackPayload)) await setPlaybackFrame(event.detail?.frame || 0);
    });
    document.getElementById("traj-speed")?.addEventListener("change", async (event) => {
      STATE.playbackSpeed = Number(event.target.value) || 1;
      if (STATE.playbackPlaying) await startPlayback();
    });
    document.getElementById("traj-loop")?.addEventListener("change", (event) => {
      STATE.playbackLoop = !!event.target.checked;
    });
    document.getElementById("traj-superpose")?.addEventListener("change", (event) => {
      // Smoothing averages fitted frames: frames as written are not smoothed.
      if (event.target.value === "none" && STATE.smoothedOver > 1) {
        STATE.smoothedOver = 1;
        const smooth = document.getElementById("traj-smooth");
        if (smooth) smooth.value = "1";
      }
      void superpose(event.target.value);
    });
    document.getElementById("traj-smooth")?.addEventListener("change", (event) => {
      STATE.smoothedOver = Number(event.target.value) || 1;
      // An average of a molecule turning is a molecule shrunk: the frames are
      // fitted on the backbone first where they are not fitted already.
      let on = STATE.superposed;
      if (on === "none" && STATE.smoothedOver > 1) {
        on = "backbone";
        const select = document.getElementById("traj-superpose");
        if (select) select.value = on;
      }
      if (on !== "none") void superpose(on);
    });
    document.getElementById("traj-superpose-to")?.addEventListener("change", (event) => {
      STATE.superposedTo = event.target.value;
      if (STATE.superposed !== "none") void superpose(STATE.superposed);
    });
  }

  async function startPlayback() {
    // Playing from the moment it is asked: the button and the keys say so
    // while the frames load, and a pause pressed before they have is kept.
    const asked = ++STATE.playbackAsked;
    STATE.playbackPlaying = true;
    STATE.liveUpdates = false;
    clearPlaybackTimer();
    updatePlaybackButtons();
    const payload = await ensurePlaybackPayload();
    if (!payload || !(await loadPlayback(payload))) {
      if (asked === STATE.playbackAsked) pausePlayback();
      return;
    }
    if (!STATE.playbackPlaying || asked !== STATE.playbackAsked) return;
    // Played from the end it stands at, as a player does: from the first
    // frame where the last is shown (the last, played backwards).
    const shown = Number(document.getElementById("traj-slider")?.value || 0);
    const end = STATE.playbackReverse ? 0 : STATE.playbackFrames - 1;
    if (STATE.playbackFrames > 1 && shown === end && !STATE.playbackLoop) {
      stopFollowing();
      await setPlaybackFrame(STATE.playbackReverse ? STATE.playbackFrames - 1 : 0);
      if (!STATE.playbackPlaying || asked !== STATE.playbackAsked) return;
    }
    const interval = Math.max(50, Math.round(700 / Math.max(0.25, STATE.playbackSpeed)));
    // One frame at a time: the next is asked for once the last is rendered.
    const step = async () => {
      if (!STATE.playbackPlaying) return;
      const started = performance.now();
      await seekRelative(STATE.playbackReverse ? -1 : 1, true);
      if (!STATE.playbackPlaying) return;
      STATE.playbackTimer = window.setTimeout(step,
        Math.max(0, interval - (performance.now() - started)));
    };
    STATE.playbackTimer = window.setTimeout(step, interval);
  }

  function pausePlayback() {
    STATE.playbackPlaying = false;
    clearPlaybackTimer();
    updatePlaybackButtons();
  }

  function clearPlaybackTimer() {
    if (STATE.playbackTimer) window.clearTimeout(STATE.playbackTimer);
    STATE.playbackTimer = null;
  }

  async function seekRelative(delta, fromTimer) {
    if (!STATE.playbackLoaded && !(await loadPlayback(STATE.playbackPayload))) return;
    const slider = document.getElementById("traj-slider");
    const current = Number(slider?.value || 0);
    let next = current + delta;
    if (next < 0 || next >= STATE.playbackFrames) {
      if (STATE.playbackLoop) next = next < 0 ? STATE.playbackFrames - 1 : 0;
      else {
        if (fromTimer) pausePlayback();
        next = clamp(next, 0, Math.max(0, STATE.playbackFrames - 1), 0);
      }
    }
    await setPlaybackFrame(next);
  }

  /** Whether the run is writing frames now, so the newest can be followed. */
  function runIsWriting() {
    return STATE.runStatus === "running" || STATE.runStatus === "starting";
  }

  function stopFollowing() {
    const follow = document.getElementById("traj-follow");
    if (follow && follow.checked) follow.checked = false;
  }

  async function setPlaybackFrame(frame) {
    const generation = STATE.viewerGeneration;
    if (!STATE.engine || !STATE.framesRendered) return;
    const index = clamp(Math.round(Number(frame)), 0, Math.max(0, STATE.playbackFrames - 1), 0);
    const slider = document.getElementById("traj-slider");
    if (slider) slider.value = String(index);
    setText("traj-current", String(index));
    setText("traj-total", String(STATE.playbackFrames));
    const time = STATE.playbackFrameTimes[index];
    setText("traj-simtime", time != null ? Number(time).toFixed(3) : "\u2014");
    setOverlay(false, {stage: "playback", frame: index, simtime: time});
    try {
      await STATE.engine.setFrame(index);
      if (STATE.miniEngine && STATE.miniModel?.of === "frames") await STATE.miniEngine.setFrame(index);
    } catch (error) {
      console.debug("frame change skipped", error);
    }
    if (!isViewerGenerationCurrent(generation) || !STATE.framesRendered) return;
    // The atoms measured have moved with the frame, and so have their numbers.
    if (STATE.picks.length) sayMeasurement();
    // And the series under the transport follows it (frame-series.js).
    window.dispatchEvent(new CustomEvent("dashboard:frame-shown", {detail: {index}}));
  }

  function updatePlaybackButtons() {
    const toolbar = document.querySelector('[data-action="play-trajectory"]');
    if (toolbar) {
      // An icon that shows what pressing it does, named the same way.
      toolbar.toggleAttribute("data-playing", !!STATE.playbackPlaying);
      toolbar.setAttribute("aria-label", STATE.playbackPlaying ? "Pause" : "Play");
      toolbar.classList.toggle("active", STATE.playbackPlaying);
      toolbar.title = STATE.playbackPayload?.playback_available
        ? `${STATE.playbackPlaying ? "Pause" : "Play"} (Space): ${STATE.playbackFrames} frames`
        : "Playback becomes available after at least two live coordinate snapshots";
    }
    document.querySelectorAll('[data-traj="reverse"]').forEach((button) => {
      button.setAttribute("aria-pressed", String(!!STATE.playbackReverse));
      button.classList.toggle("active", !!STATE.playbackReverse);
    });
    document.querySelectorAll('[data-action="prev-frame"], [data-action="next-frame"]').forEach((button) => {
      button.setAttribute("aria-disabled", String(!STATE.playbackPayload?.playback_available));
    });
  }

  /* ------------------------------------------------------------------ */
  /* Settings, status, selections                                        */
  /* ------------------------------------------------------------------ */
  /* The Preferences dialog's settings, as the Viewer starts and as they
   * change. The ground and spin follow a preference only when it changes,
   * so the ground button and the spin button keep what they were last
   * set to otherwise. */
  function onSettingsUpdated(settings) {
    if (settings.ligand) {
      STATE.ligandResname = String(settings.ligand).toUpperCase();
      STATE.ligandChosen = true;
    } else if (STATE.ligandChosen) {
      // Back to the first ligand the structure has, as at the start.
      STATE.ligandChosen = false;
      STATE.ligandResname = (STATE.structureInfo?.ligand_resnames || []).filter(Boolean)[0] || null;
    }
    if (Number.isFinite(settings.pocketCutoff)) STATE.pocketCutoff = settings.pocketCutoff;
    if (settings.proteinRepresentation) {
      STATE.representation = settings.proteinRepresentation;
      // The list says what is rendered, whichever way it was chosen.
      const listed = document.getElementById("viewer-rep");
      if (listed && [...listed.options].some((o) => o.value === STATE.representation)) {
        listed.value = STATE.representation;
      }
    }
    STATE.visibility.water = !!settings.showWater;
    STATE.visibility.ions = !!settings.showIons;
    STATE.preservingCamera = settings.preserveCamera !== false;
    if (settings.ground && settings.ground !== STATE.groundChosen) {
      STATE.groundChosen = settings.ground;
      setGround(settings.ground);
    }
    if (!!settings.spin !== STATE.spinChosen) {
      STATE.spinChosen = !!settings.spin;
      if (STATE.engine) setSpinning(STATE.engine, STATE.spinChosen);
    }
    if (STATE.mode === "playback" && needsFullTopology()) void ensurePlaybackEnvironment();
    const cutoff = document.getElementById("pocket-cutoff");
    if (cutoff) cutoff.value = String(STATE.pocketCutoff);
    void restyleViewers();
  }

  function onStatusUpdated(status, times) {
    STATE.runStatus = String(status?.status || "").toLowerCase();
    const running = STATE.runStatus === "running";
    // Following the run and taking its newest structure mean something only
    // while it writes them: a finished study offered "Now" and "Pause
    // Updates" beside its last frame. Kept while paused, so there is a way
    // to resume.
    const writing = running || STATE.runStatus === "starting";
    document.querySelectorAll("[data-while-running]").forEach((control) => {
      control.hidden = !writing && STATE.liveUpdates !== false;
    });
    // The preview's caption says which frame it is: the newest while the
    // run writes them, and the last once it has stopped.
    const note = document.getElementById("mini-preview-note");
    if (note && STATE.runStatus) {
      note.textContent = running || STATE.runStatus === "starting"
        ? "The newest frame, as it is written."
        : "The last frame the run wrote.";
    }
    // The frame's time in production, as the analyses' axes give it; the
    // live record's own time has the equilibration in it, so the last frame
    // of 100 ps after 10 of equilibration read 0.110 ns. Said with what it
    // is the time of, since it starts again from zero with production.
    const inProduction = times && times.production_ns != null && !times.equilibrating;
    const equilibrating = Boolean(times && times.equilibrating && times.equilibration_ns != null);
    if (STATE.mode === "playback" && STATE.framesRendered) {
      // The frame played is the overlay's to say (setPlaybackFrame): each
      // status update wrote the run's stage and its whole production over
      // it, so the overlay said the run's end beside Playback's frame.
      setOverlay(running, {});
      return;
    }
    setOverlay(running, {
      stage: status?.stage || "\u2014",
      simtime: inProduction ? times.production_ns
        : (equilibrating ? times.equilibration_ns : status?.simulation_time_completed_ns),
      simtimeOf: inProduction ? "production" : (equilibrating ? "equilibration" : ""),
    });
  }

  function onHoverAtom(atom) {
    window.FastMDXSequence?.atomHovered(atom);
    if (!atom) return;
    updateSelectionPanel(atom);
  }

  function onClickAtom(atom, modifiers) {
    if (!atom) return;
    updateSelectionPanel(atom);
    if (STATE.measuring) {
      void showSelectionFor(atom);
      void addPick(atom);
      return;
    }
    // Its residue selected, as in the sequence, and the atom clicked named.
    if (!window.FastMDXSequence?.atomClicked(atom, modifiers) && STATE.engine) {
      // A residue the sequence does not list (a ligand, a water, an ion).
      const residue = {chain: atom.chain || "", resi: atom.resi, icode: atom.icode || "",
                       resn: atom.resn || ""};
      const own = STATE.engine.atomsOfResidues([[residue.chain, residue.resi, residue.icode,
                                                 residue.resn]]);
      const adding = modifiers && (modifiers.shift || modifiers.control || modifiers.meta);
      const atoms = adding && STATE.selection
        ? [...new Set([...STATE.selection.atoms, ...own])].sort((a, b) => a - b) : own;
      selectAtoms(atoms, {residues: adding && STATE.selection
        ? [...STATE.selection.residues, residue] : [residue]});
      window.FastMDXSequence?.markAtoms(atoms);
    }
    updateSelectionPanel(atom);
    void showSelectionFor(atom);
  }

  /* ------------------------------------------------------------------ */
  /* The selection                                                       */
  /* ------------------------------------------------------------------ */
  /* Residues selected in the sequence or clicked in the structure: marked
   * in the structure (Mol*'s selection), and one residue named as a click
   * names it, with its selection as the analyses read it. */
  /** Atoms selected otherwise: by a selection typed or named, or a residue
   * the sequence does not list. */
  function selectAtoms(atoms, options) {
    STATE.selection = atoms.length ? {residues: options?.residues || [], atoms,
      expression: options?.expression || null} : null;
    STATE.engine?.showSelected(atoms).catch((error) => console.debug("selection not marked", error));
    window.dispatchEvent(new CustomEvent("dashboard:selection-changed"));
  }

  /** What the Viewer renders, named so that atoms found in it are known to
   * be its own: the model, its atoms, and the file they came from. */
  function modelSignature() {
    const model = STATE.model;
    if (!model) return "";
    const source = model.of === "frames" ? STATE.playbackSignature
      : (model.of === "structure" ? `${STATE.structureUrl}|${needsFullTopology()}`
        : STATE.liveFrameIndex);
    return `${model.of}|${model.atoms}|${source}`;
  }

  function selectResidues(residues, atoms, options) {
    STATE.selection = residues.length ? {residues, atoms} : null;
    window.dispatchEvent(new CustomEvent("dashboard:selection-changed"));
    const engine = STATE.engine;
    if (engine) {
      engine.showSelected(atoms).catch((error) => console.debug("selection not marked", error));
    }
    if (residues.length === 1 && engine) {
      const residue = residues[0];
      const atomsOfIt = engine.atoms(atoms);
      const named = atomsOfIt.find((atom) => atom.atom === "CA") || atomsOfIt[0];
      if (named) {
        updateSelectionPanel(named);
        void showSelectionFor(named);
      }
      if (options?.announce) announce(`Selected ${residue.resn} ${residue.resi}${residue.icode || ""}`
        + (residue.chain ? `, chain ${residue.chain}.` : "."));
    } else if (options?.announce) {
      announce(residues.length ? `Selected ${residues.length} residues, ${atoms.length} atoms.`
        : "Nothing selected.");
    }
  }

  /* ------------------------------------------------------------------ */
  /* Measuring                                                           */
  /* ------------------------------------------------------------------ */
  /* Two atoms clicked give their distance, three the angle at the middle
   * one, four the dihedral about the middle bond: in the frame on screen,
   * as rendered, in angstroms and degrees. A fifth click starts again. Mol*
   * renders them and follows them as the trajectory plays; the numbers here
   * are read from the frame shown. Two atoms can be measured over every
   * frame too, by the pair_distance analysis (gui/measure.py). */
  function toggleMeasuring(button) {
    STATE.measuring = !STATE.measuring;
    const pressed = button || document.querySelector('[data-action="measure"]');
    pressed?.setAttribute("aria-pressed", String(STATE.measuring));
    pressed?.classList.toggle("active", STATE.measuring);
    if (!STATE.measuring) STATE.picks = [];
    else document.querySelector('.info-tab[data-tab="selection"]')?.click();
    void renderMeasurement();
    sayMeasurement();
    announce(STATE.measuring
      ? "Measuring. Click two atoms for a distance, three for an angle, four for a dihedral."
      : "Measuring off.");
  }

  function clearPicks() {
    STATE.picks = [];
    void renderMeasurement();
    sayMeasurement();
  }

  async function addPick(atom) {
    const pick = {index: atom.index, chain: atom.chain || "", resi: atom.resi,
                  resn: atom.resn || "", atom: atom.atom || atom.name || "",
                  selection: null, asked: false};
    const last = STATE.picks[STATE.picks.length - 1];
    if (last && ["chain", "resi", "resn", "atom"].every((key) => last[key] === pick[key])) return;
    if (STATE.picks.length >= 4) STATE.picks = [];
    STATE.picks.push(pick);
    // Said at once; rendered once Mol* has rendered what was asked before it.
    sayMeasurement();
    void selectionOfPick(pick);
    await renderMeasurement();
  }

  /* The atom a pick names, where it is in the frame on screen: by its
   * index, or by chain, residue and name where the structure shown has
   * been replaced by one numbered otherwise. */
  function currentAtom(pick) {
    const engine = STATE.engine;
    if (!engine || !STATE.model) return null;
    const byIndex = pick.index != null ? engine.atoms([pick.index])[0] : null;
    if (byIndex && byIndex.atom === pick.atom && byIndex.resi === pick.resi) return byIndex;
    const wanted = {resi: pick.resi, atom: pick.atom};
    if (pick.chain) wanted.chain = pick.chain;
    if (pick.resn) wanted.resn = pick.resn;
    const found = engine.find(wanted)[0] || null;
    if (found) pick.index = found.index;
    return found;
  }

  const minus = (a, b) => [a.x - b.x, a.y - b.y, a.z - b.z];
  const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  const cross = (u, v) => [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                           u[0] * v[1] - u[1] * v[0]];
  const length = (u) => Math.sqrt(dot(u, u));

  /* Distances between consecutive atoms, the angle at each inner atom, and
   * the dihedral where there are four (IUPAC's sign). */
  function measurements(atoms) {
    const said = [];
    for (let i = 1; i < atoms.length; i += 1) {
      said.push({kind: "distance", between: [i, i + 1],
                 value: length(minus(atoms[i], atoms[i - 1]))});
    }
    for (let i = 1; i + 1 < atoms.length; i += 1) {
      const u = minus(atoms[i - 1], atoms[i]);
      const v = minus(atoms[i + 1], atoms[i]);
      const cosine = dot(u, v) / (length(u) * length(v));
      said.push({kind: "angle", at: i + 1,
                 value: Math.acos(Math.max(-1, Math.min(1, cosine))) * 180 / Math.PI});
    }
    if (atoms.length === 4) {
      const b1 = minus(atoms[1], atoms[0]);
      const b2 = minus(atoms[2], atoms[1]);
      const b3 = minus(atoms[3], atoms[2]);
      const n1 = cross(b1, b2);
      const n2 = cross(b2, b3);
      // The sign MDTraj gives, IUPAC's: checked against compute_dihedrals.
      const m1 = cross(b2.map((c) => c / length(b2)), n1);
      said.push({kind: "dihedral",
                 value: Math.atan2(dot(m1, n2), dot(n1, n2)) * 180 / Math.PI});
    }
    return said;
  }

  function measureText(m) {
    if (m.kind === "distance") return `${m.value.toFixed(2)} Å`;
    return `${m.value.toFixed(1)}°`;
  }

  /* The picked atoms measured by Mol*: the distance, angle or dihedral the
   * last of them completes, rendered in the structure. */
  async function renderMeasurement() {
    const engine = STATE.engine;
    if (!engine) return;
    const atoms = STATE.picks.map(currentAtom);
    try {
      await engine.showPicks(STATE.picks.map((pick) => pick.index));
      if (atoms.some((atom) => !atom) || atoms.length < 2) await engine.clearMeasurements();
      else await engine.measure(atoms.map((atom) => atom.index));
    } catch (error) {
      console.debug("measurement not rendered", error);
    }
  }

  function pickSaid(pick, number) {
    return `${number}. ${pick.resn} ${pick.resi} ${pick.atom}` + (pick.chain ? `, chain ${pick.chain}` : "");
  }

  function sayMeasurement() {
    const host = document.getElementById("measure-said");
    if (!host) return;
    host.replaceChildren();
    const wasHidden = host.hidden;
    host.hidden = !STATE.measuring && !STATE.picks.length;
    if (host.hidden) return;
    // A measurement is read in Information: shown as measuring begins.
    const rail = window.FastMDXViewerRail;
    if (wasHidden && rail && rail.chosen !== "side-info") rail.choose("side-info");
    const add = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      host.appendChild(node);
      return node;
    };
    // Its name, and beside it the icon that clears the atoms picked.
    const head = add("div", "measure-head");
    head.appendChild(Object.assign(document.createElement("div"),
      {className: "measure-title", textContent: "Measuring"}));
    const clear = window.FastMDXIcons.button("erase", "Clear the atoms picked", "measure-clear");
    clear.disabled = !STATE.picks.length;
    clear.addEventListener("click", clearPicks);
    head.appendChild(clear);
    if (!STATE.picks.length) {
      add("p", "muted small", "Click an atom in the structure. Two give a distance, three an "
        + "angle, four a dihedral; a fifth starts again.");
      return;
    }
    const list = add("ol", "measure-atoms");
    STATE.picks.forEach((pick, i) => {
      const item = document.createElement("li");
      item.textContent = pickSaid(pick, i + 1).replace(/^\d+\. /, "");
      list.appendChild(item);
    });
    const atoms = STATE.picks.map(currentAtom);
    if (!atoms.some((atom) => !atom)) {
      const rows = add("dl", "measure-values");
      measurements(atoms).forEach((m) => {
        const term = document.createElement("dt");
        const value = document.createElement("dd");
        if (m.kind === "distance") {
          term.textContent = `Distance, ${m.between[0]} to ${m.between[1]}`;
          value.textContent = `${m.value.toFixed(2)} \u00c5 (${(m.value / 10).toFixed(3)} nm)`;
        } else if (m.kind === "angle") {
          term.textContent = `Angle at ${m.at}`;
          value.textContent = measureText(m);
        } else {
          term.textContent = "Dihedral, 1-2-3-4";
          value.textContent = measureText(m);
        }
        value.className = "mono";
        rows.append(term, value);
      });
      add("p", "muted small", "As shown in this frame. The analyses compute distances across the "
        + "periodic box the short way round.");
    }
    if (STATE.picks.length === 2) {
      const over = add("div", "measure-over");
      const ready = STATE.picks.every((pick) => pick.selection);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "file-action measure-over-frames";
      button.textContent = "Over every frame";
      button.disabled = !ready;
      button.title = ready ? "The command that computes this distance at every frame"
        : "Waiting for the atoms' selections";
      button.addEventListener("click", () => overEveryFrame(over));
      over.appendChild(button);
    }
  }

  /* An atom of the frames played is an atom of the topology they were
   * written from, by its index there: the server then knows it exactly,
   * rather than by its chain's letter and its residue's number. */
  function framesAtom(atom) {
    return STATE.model?.of === "frames" && Number.isInteger(atom?.index)
      ? {frames_atom: String(atom.index)} : {};
  }

  async function selectionOfPick(pick) {
    if (pick.asked) return;
    pick.asked = true;
    const query = new URLSearchParams({chain: pick.chain, resseq: String(pick.resi ?? ""),
                                       resname: pick.resn, atom: pick.atom,
                                       ...framesAtom(pick)});
    try {
      const answer = await (await fetch(`/api/selection?${query}`)).json();
      pick.selection = answer && answer.ok && answer.atom ? answer.atom.selection : null;
    } catch (error) {
      pick.selection = null;
    }
    if (STATE.picks.includes(pick)) sayMeasurement();
  }

  async function overEveryFrame(host) {
    const [a, b] = STATE.picks.map((pick) => pick.selection);
    host.querySelectorAll(".measure-command, .measure-reason, .measure-copy").forEach(
      (node) => node.remove());
    let answer = null;
    try {
      answer = await (await fetch(`/api/measure-over-frames?${new URLSearchParams({a, b})}`)).json();
    } catch (error) {
      answer = {ok: false, reason: "The server did not answer."};
    }
    if (!answer || !answer.ok) {
      const said = document.createElement("p");
      said.className = "muted small measure-reason";
      said.textContent = (answer && answer.reason) || "No command for these atoms.";
      host.appendChild(said);
      return;
    }
    const said = document.createElement("pre");
    said.className = "measure-command";
    said.textContent = answer.command || answer.config || "";
    const copy = window.FastMDXIcons.button("copy", "Copy the command", "measure-copy");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(said.textContent);
        window.FastMDXIcons.flash(copy, true, "Copied");
      } catch (error) {
        window.FastMDXIcons.flash(copy, false, "Select the text to copy it.");
      }
    });
    host.append(said, copy);
  }

  /* The selection a clicked atom is, as a Config writes it, checked by the
   * server against the topology the analyses read: resSeq, not resid, and
   * the chain by MDTraj's index. Only on a click; a hover asks nothing. */
  let selectionAsked = 0;
  async function showSelectionFor(atom) {
    const host = document.getElementById("selection-strings");
    if (!host) return;
    const asked = ++selectionAsked;
    const query = new URLSearchParams({
      chain: atom.chain || "", resseq: String(atom.resi ?? ""),
      resname: atom.resn || "", atom: atom.atom || atom.name || "",
      ...framesAtom(atom),
    });
    let answer = null;
    try {
      answer = await (await fetch(`/api/selection?${query}`)).json();
    } catch (error) {
      answer = {ok: false, reason: "The server did not answer."};
    }
    if (asked !== selectionAsked) return;
    host.replaceChildren();
    host.hidden = false;
    if (!answer || !answer.ok) {
      const said = document.createElement("p");
      said.className = "muted small";
      said.textContent = (answer && answer.reason) || "No selection for this atom.";
      host.appendChild(said);
      return;
    }
    [["Residue", answer.residue], ["Atom", answer.atom]].forEach(([label, found]) => {
      if (!found) return;
      const row = document.createElement("div");
      row.className = "selection-string";
      row.setAttribute("data-of", label.toLowerCase());
      const name = document.createElement("span");
      name.className = "selection-string-label";
      name.textContent = label;
      const code = document.createElement("code");
      code.textContent = found.selection;
      const copy = window.FastMDXIcons.button("copy", `Copy the ${label.toLowerCase()} selection`);
      copy.title = `Copy the selection: ${found.atoms} atom${found.atoms === 1 ? "" : "s"} in ${answer.against}`;
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(found.selection);
          window.FastMDXIcons.flash(copy, true, "Copied");
        } catch (error) {
          window.FastMDXIcons.flash(copy, false, "Select the text to copy it.");
        }
      });
      row.append(name, code, copy);
      host.appendChild(row);
    });
    const note = document.createElement("p");
    note.className = "muted small";
    note.textContent = `Checked against ${answer.against}, the topology the analyses read.`;
    host.appendChild(note);
    if (TITRATABLE.has(String(atom.resn || "").toUpperCase())) void offerStates(atom, host, asked);
  }

  /* A residue whose protonation state a study can set, offered for a new
   * study of the same structure: its states, what each is, and a button
   * each that opens the Config page with this study's Config and that
   * state set (gui/selection.states_for). Nothing is run. */
  const TITRATABLE = new Set(["HIS", "HID", "HIE", "HIP", "HSD", "HSE", "HSP", "ASP", "ASH",
                              "GLU", "GLH", "LYS", "LYN"]);

  async function offerStates(atom, host, asked) {
    const query = new URLSearchParams({chain: atom.chain || "", resseq: String(atom.resi ?? ""),
                                       resname: atom.resn || ""});
    let answer = null;
    try {
      answer = await (await fetch(`/api/residue-states?${query}`)).json();
    } catch (error) {
      answer = null;
    }
    if (asked !== selectionAsked || !answer) return;
    const block = document.createElement("div");
    block.className = "residue-states";
    const said = document.createElement("p");
    said.className = "muted small";
    if (!answer.ok) {
      said.textContent = answer.reason || "";
      block.appendChild(said);
      host.appendChild(block);
      return;
    }
    said.textContent = `A new study of ${answer.system} with ${answer.resname} ${answer.key} as:`
      + (answer.current ? ` (this one set it ${answer.current})` : " (this one left it to setup)");
    block.appendChild(said);
    const row = document.createElement("div");
    row.className = "residue-state-choices";
    answer.states.forEach(({state, meaning}) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "file-action";
      button.dataset.state = state;
      button.textContent = state;
      button.title = meaning;
      button.addEventListener("click", () => startWithState(answer, state, button));
      row.appendChild(button);
    });
    block.appendChild(row);
    host.appendChild(block);
  }

  async function startWithState(answer, state, button) {
    const config = JSON.parse(JSON.stringify(answer.config));
    const setup = config.setup || (config.setup = {});
    setup.residue_states = Object.assign({}, setup.residue_states || {}, {[answer.key]: state});
    let loaded = null;
    try {
      loaded = await (await fetch("/api/load-config", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({config}),
      })).json();
    } catch (error) {
      loaded = {ok: false, error: "The server did not answer."};
    }
    const run = window.FastMDXRun;
    if (!loaded || !loaded.ok || !run || !run.applyLoadedState) {
      button.textContent = (loaded && loaded.error) || "Could not open the builder.";
      return;
    }
    await run.applyLoadedState(loaded.state, {
      note: `A new study of ${answer.system}, as this one, with ${answer.resname} ${answer.key} `
        + `as ${state}. Nothing has run.`,
    });
    window.FastMDXDashboard?.navigate?.("run");
  }

  function updateSelectionPanel(atom) {
    const body = document.getElementById("selection-tab-tbody");
    if (!body) return;
    body.innerHTML = `
      <tr><th>Residue</th><td>${escapeHTML(atom.resn || "\u2014")} ${escapeHTML(atom.resi ?? "")}</td></tr>
      <tr><th>Chain</th><td>${escapeHTML(atom.chain || "\u2014")}</td></tr>
      <tr><th>Atom</th><td>${escapeHTML(atom.atom || atom.name || "\u2014")}</td></tr>
      <tr><th>Element</th><td>${escapeHTML(atom.elem || atom.element || "\u2014")}</td></tr>
      <tr><th>Coordinates</th><td>${coordinate(atom.x)}, ${coordinate(atom.y)}, ${coordinate(atom.z)}</td></tr>`
      + residueRows(atom);
  }

  /* For an atom of the protein: its residue's secondary structure in this
   * frame, and its value in each of the study's per-residue results. */
  function residueRows(atom) {
    if (!STATE.model || !AMINO_ACIDS.includes(String(atom.resn || "").toUpperCase())) return "";
    let rows = `<tr><th>Secondary structure</th><td>${escapeHTML(secondaryStructureOf(atom))}</td></tr>`;
    (STATE.residueValues || []).forEach((property) => {
      const lookup = lookupFor(property);
      const value = valueOfResidue(property, lookup, atom);
      const unit = property.unit ? ` ${property.unit}` : "";
      const spread = value == null ? null
        : lookup.spread.get(residueKey(lookup.chained ? atom.chain : "", atom.resi, atom.icode));
      let said = value == null ? "\u2014" : `${formatValue(value)}${unit}`;
      if (spread && Number.isFinite(spread.error)) {
        said = `${formatValue(value)} \u00b1 ${formatValue(spread.error)}${unit}`;
      }
      rows += `<tr><th>${escapeHTML(property.label)}</th><td>${escapeHTML(said)}</td></tr>`;
      if (spread && Array.isArray(spread.each) && Array.isArray(property.runs)) {
        const each = property.runs.map((run, i) => `${run}: ${spread.each[i] == null ? "\u2014"
          : formatValue(Number(spread.each[i]))}`).join("; ");
        rows += `<tr><th>Each run</th><td>${escapeHTML(each)}</td></tr>`;
      }
    });
    return rows;
  }

  /* A field of the overlay with nothing to say is not shown: a structure
   * with no run behind it showed a frame, an age and a simulated time, each
   * with a dash for its value. */
  function tidyOverlay() {
    document.querySelectorAll("#viewer-overlay .overlay-detail").forEach((field) => {
      const text = (field.textContent || "").trim();
      field.hidden = !text || text === "\u2014" || /\s\u2014$/.test(text);
    });
  }

  function setOverlay(live, info) {
    const overlay = document.getElementById("viewer-overlay");
    if (!overlay) return;
    // A frame the engine wrote is live only while the engine is writing
    // them: a finished study's last frame read "LIVE, age 56m".
    const running = STATE.runStatus === "running" || STATE.runStatus === "starting";
    const shown = live && (!STATE.runStatus || running);
    overlay.setAttribute("data-live", shown ? "true" : "false");
    // The last frame of a run that has ended is that, not the latest of
    // one still writing; and how long ago it was written is the run's age,
    // which says nothing about the frame once nothing more will come.
    const ended = live && !shown;
    setText("overlay-tag", shown ? "LIVE"
      : (STATE.mode === "playback" ? "PLAYBACK" : (ended ? "LAST FRAME" : "STATIC")));
    if (ended) setText("overlay-age", "");
    if (info?.stage != null) setText("overlay-stage", info.stage);
    // A live frame is named by the step it was written at, which is what
    // the engine records; a frame of the trajectory by its place in it. Both
    // read "frame" once, and the label flipped between the frames written
    // and the step number as the two updates arrived.
    if (info?.step != null) setText("overlay-frame", `step ${Number(info.step).toLocaleString()}`);
    if (info?.frame != null) setText("overlay-frame", `frame ${info.frame}`);
    if (info?.age != null && !ended) setText("overlay-age", `age ${info.age}`);
    if (info?.simtime != null) {
      setText("overlay-simtime",
        `${info.simtimeOf ? info.simtimeOf + " " : ""}${Number(info.simtime).toFixed(3)} ns`);
    }
    tidyOverlay();
  }

  /* ------------------------------------------------------------------ */
  /* Generic helpers                                                     */
  /* ------------------------------------------------------------------ */
  /* The molecule's frame keeps one shape, 4 wide to 3 high: the shape a
   * figure and a slide take, and a globular protein turned any way fits it
   * with little to spare. It is as large as the window allows under the
   * page's header with the sequence's and the playback's heads above and
   * below it, and no wider: the page's spare width is left at its sides.
   * Opening either changes nothing of the molecule: what it shows pushes
   * the rest down, and the column scrolls. */
  const CANVAS_SHAPE = 4 / 3;
  const SMALLEST_CANVAS = 280;

  function fitTheLayout() {
    const layout = document.querySelector(".viewer-layout");
    if (!layout || !isVisible(layout)) return;
    const page = layout.closest(".page");
    const header = page ? page.querySelector(".page-header") : null;
    const top = header ? header.getBoundingClientRect().bottom : layout.getBoundingClientRect().top;
    const room = Math.max(520, Math.floor(window.innerHeight - Math.max(0, top) - 20));
    layout.style.setProperty("--viewer-height", `${room}px`);
    // Twice: the playback's rows wrap by the width the first pass gives.
    for (let pass = 0; pass < 2; pass += 1) {
      const fitted = canvasWidth(layout, room);
      if (fitted == null) {
        layout.style.removeProperty("--viewer-canvas-width");
        if (page) page.style.removeProperty("--viewer-content-width");
        return;
      }
      layout.style.setProperty("--viewer-canvas-width", `${fitted.canvas}px`);
      // The page's header as wide as what is under it, so the two line up.
      if (page) page.style.setProperty("--viewer-content-width", `${fitted.whole}px`);
    }
  }

  /* The canvas's width beside the settings, and theirs together; or null
   * where the two stack (the canvas then takes the page's width, in the
   * same shape). */
  function canvasWidth(layout, room) {
    const columns = getComputedStyle(layout).gridTemplateColumns.split(" ").filter(Boolean);
    const wrap = layout.querySelector(".viewer-canvas-wrap");
    const side = layout.querySelector(".viewer-side");
    const frame = document.getElementById("viewer-canvas-frame");
    if (columns.length < 2 || !wrap || !side || !frame) return null;
    const gap = parseFloat(getComputedStyle(layout).columnGap) || 0;
    const sideWidth = side.getBoundingClientRect().width;
    // From the page's width: the layout itself is held to what it shows.
    const page = layout.closest(".page") || layout;
    // The column's scroll bar has its place kept beside the molecule, so
    // the molecule is as wide as fitted whether the bar shows or not.
    const gutter = Math.max(0, wrap.offsetWidth - wrap.clientWidth);
    const across = page.clientWidth - gap - sideWidth - gutter;
    const between = parseFloat(getComputedStyle(wrap).rowGap) || 0;
    let others = 0;
    Array.from(wrap.children).forEach((child) => {
      if (child === frame || !isVisible(child)) return;
      others += closedHeight(child) + between;
    });
    const high = Math.max(SMALLEST_CANVAS, Math.min(room - others, across / CANVAS_SHAPE));
    const canvas = Math.floor(Math.min(across, high * CANVAS_SHAPE)) + gutter;
    return {canvas, whole: Math.ceil(canvas + gap + sideWidth)};
  }

  /* The height a fold takes closed, its head and its own edges, whether it
   * is open or not: opening the sequence took its lines from the molecule,
   * which shrank as it opened. Anything else, as it stands. */
  function closedHeight(node) {
    const head = node.tagName === "DETAILS" ? node.querySelector(":scope > summary") : null;
    if (!head) return node.getBoundingClientRect().height;
    const style = getComputedStyle(node);
    const edges = ["borderTopWidth", "borderBottomWidth", "paddingTop", "paddingBottom"]
      .reduce((sum, key) => sum + (parseFloat(style[key]) || 0), 0);
    // A head is padded alike above and below while closed; an open
    // section's has less below (its body follows), which counted would
    // move the molecule by those pixels as it opened.
    const own = getComputedStyle(head);
    const below = parseFloat(own.paddingBottom) || 0;
    const above = parseFloat(own.paddingTop) || 0;
    return head.getBoundingClientRect().height - below + above + edges;
  }

  /* The sequence and the playback fold as the settings' sections do, and
   * stay as they were left; the molecule is sized again when either
   * appears or its head wraps, and keeps its size as either opens. */
  function wireFolds() {
    [["sequence-strip", "viewerSequenceOpen"], ["viewer-under-fold", "viewerPlaybackOpen"]]
      .forEach(([id, key]) => {
        const fold = document.getElementById(id);
        if (!fold) return;
        try {
          const kept = localStorage.getItem(`fmx.${key}`);
          if (kept !== null) fold.open = kept === "1";
        } catch (error) { /* private mode */ }
        fold.addEventListener("toggle", () => {
          try { localStorage.setItem(`fmx.${key}`, fold.open ? "1" : "0"); }
          catch (error) { /* private mode */ }
        });
      });
    if (typeof ResizeObserver !== "function") return;
    let asked = false;
    const again = () => {
      if (asked) return;
      asked = true;
      requestAnimationFrame(() => { asked = false; resizeViewers(); });
    };
    const watched = new ResizeObserver(again);
    ["sequence-strip", "viewer-under-fold"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) watched.observe(node);
    });
    // The page's width changes as the columns beside it open and close.
    const shell = document.querySelector(".page-shell");
    if (shell) watched.observe(shell);
  }

  function resizeViewers() {
    fitTheLayout();
    const mainTarget = document.getElementById("viewer-canvas");
    const miniTarget = document.getElementById("mini-preview-canvas");
    [[mainTarget, STATE.engine], [miniTarget, STATE.miniEngine]].forEach(([target, engine]) => {
      if (!target || !engine || !isVisible(target)) return;
      try {
        engine.resize();
      } catch (error) {
        console.debug("viewer resize skipped", error);
      }
    });
  }

  function isVisible(element) {
    return !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
  }

  function announce(message) {
    const live = document.getElementById("sr-live");
    if (live) live.textContent = message;
    console.info(message);
  }

  function setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value == null ? "" : String(value);
  }

  function coordinate(value) {
    return Number.isFinite(Number(value)) ? Number(value).toFixed(3) : "\u2014";
  }

  function clamp(value, low, high, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(low, Math.min(high, number));
  }

  function escapeHTML(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  /* "#rrggbb" as the number Mol* takes for a colour. */
  function colourNumber(hex) {
    const match = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
    return match ? parseInt(match[1], 16) : 0x050505;
  }

  function hexOf(number) {
    return number == null ? null : `#${Number(number).toString(16).padStart(6, "0")}`;
  }

  window.FastMDXMoleculeViewer = {
    STATE,
    onStructureUpdated,
    pollLiveFrame,
    loadPlayback,
    // The frames superposed as asked, and whether what is placed on the
    // first frame is fitted as they are (viewer-occupancy.js).
    superpose,
    placedFitAsPlayed,
    ligandResnames,
    // How the frames are shown, in the words the server is asked in.
    superposition: () => ({on: STATE.superposed, to: STATE.appliedTo || "first",
      smooth: String(STATE.appliedSmooth || 1), ligand: STATE.ligandResname
        || ligandResnames()[0] || "", cutoff: String(STATE.pocketCutoff || 5)}),
    // A result the page made (two states compared), coloured by as the
    // study's own are, and taken away again.
    addResult: (property) => {
      STATE.extraResults = Object.assign({}, STATE.extraResults, {[property.key]: property});
      STATE.residueValues = (STATE.residueValues || []).filter((p) => p.key !== property.key)
        .concat([property]);
      STATE.residueLookups = new Map();
      offerResultColours();
    },
    removeResult: (key) => {
      if (STATE.extraResults) delete STATE.extraResults[key];
      STATE.residueValues = (STATE.residueValues || []).filter((p) => p.key !== key);
      STATE.residueLookups = new Map();
      offerResultColours();
      return restyleViewers();
    },
    colourBy: (mode) => {
      STATE.colorMode = mode;
      STATE.colourChosen = true;
      const select = document.getElementById("viewer-color");
      if (select) select.value = mode;
      return restyleViewers();
    },
    viewNow,
    currentViewHints,
    showView,
    setPublication,
    resize: resizeViewers,
    measurements,
    pick: addPick,
    selectResidues,
    selectAtoms,
    modelSignature,
    needsFullTopology,
    restyle: restyleViewers,
    // What a movie is made of (viewer-movie.js): the frames loaded, one
    // shown, the playback and the following stopped, and the colour bar.
    movie: {
      frames: async () => {
        const payload = await ensurePlaybackPayload();
        if (!payload || !payload.playback_available || !(await loadPlayback(payload))) return null;
        return {count: STATE.playbackFrames, times: STATE.playbackFrameTimes.slice()};
      },
      shownFrame: () => Number(document.getElementById("traj-slider")?.value || 0),
      showFrame: setPlaybackFrame,
      hold: () => {
        pausePlayback();
        stopFollowing();
        STATE.liveUpdates = false;
        const spinning = !!STATE.spinning;
        if (spinning) setSpinning(STATE.engine, false);
        return {spinning};
      },
      release: (held) => {
        if (held && held.spinning) setSpinning(STATE.engine, true);
      },
      legend: (context) => {
        const property = activeResult();
        if (property) drawTheLegend(context, property);
      },
    },
    // The atoms a selection names, as the engine reads them, for the tests.
    atoms: (selection) => (STATE.engine ? STATE.engine.find(selection || {}) : []),
    // What the colours and the cartoon are made from, for the tests.
    byResidue: {
      colourOf: (atom) => (STATE.engine ? hexOf(STATE.engine.resultColourOf(atom.index)) : null),
      describe: updateSelectionPanel,
      giveTheSecondaryStructure,
      fingerprintOf,
    },
  };
}());
