/* FastMDXplora Live Dashboard — 3D molecular viewer.
 *
 * Uses the locally bundled 3Dmol.js asset.  The module deliberately keeps
 * structure loading, live-frame replacement, styling, and trajectory playback
 * separate so a failed optional feature never leaves the canvas permanently
 * blank.
 */

(function () {
  "use strict";

  const AMINO_ACIDS = [
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "HID", "HIE", "HIP", "ILE", "LEU", "LYS", "MET", "PHE", "PRO",
    "SER", "THR", "TRP", "TYR", "VAL", "MSE", "SEC", "PYL",
  ];
  const WATERS = ["HOH", "WAT", "TIP", "TIP3", "TIP3P", "SOL", "H2O"];
  const IONS = [
    "NA", "K", "CL", "BR", "I", "F", "MG", "CA", "ZN", "MN", "FE",
    "CU", "NI", "CO", "CD", "HG", "PB", "CS", "RB", "LI", "BA", "SR",
  ];
  const COLORS = {
    cyan: "#63e6ff",
    silver: "#d8d8dd",
    white: "#ffffff",
    violet: "#a78bfa",
    black: "#050505",
    green: "#67e8a3",
  };

  const STATE = {
    viewer: null,
    miniViewer: null,
    viewerUnavailable: false,
    miniViewerUnavailable: false,
    model: null,
    miniModel: null,
    miniPlaybackModel: null,
    structureInfo: null,
    structureUrl: null,
    structurePdb: null,
    currentPdb: null,
    liveFrameIndex: null,
    liveUpdates: true,
    mode: "structure",
    representation: "cartoon",
    colorMode: "spectrum",
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
    pocketSurface: false,
    pocketOnly: false,
    isolateLigand: false,
    preservingCamera: true,
    spinning: false,
    playbackPayload: null,
    playbackPdb: null,
    // Incremented on a dashboard run transition so a late response from the
    // previous run cannot repopulate the reset viewer.
    viewerGeneration: 0,
    // Full solvated topology used as a static overlay while the solute-only
    // trajectory frames remain animated.
    environmentPdb: null,
    environmentUrl: null,
    environmentModel: null,
    environmentBaseCoordinates: null,
    environmentTranslation: null,
    playbackSignature: null,
    playbackLoadPromise: null,
    playbackLoaded: false,
    playbackFrames: 0,
    playbackFrameTimes: [],
    playbackPlaying: false,
    playbackReverse: false,
    playbackLoop: false,
    playbackSpeed: 1,
    playbackTimer: null,
  };

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    wireControls();
    wireTrajectoryControls();
    window.FastMDXDashboard?.on("structure-updated", onStructureUpdated);
    window.FastMDXDashboard?.on("status-updated", ({status}) => onStatusUpdated(status));
    window.FastMDXDashboard?.on("playback-ready", onPlaybackReady);
    window.FastMDXDashboard?.on("run-changed", onRunChanged);
    window.FastMDXDashboard?.on("viewer-page-opened", onViewerPageOpened);
    window.FastMDXDashboard?.on("live-page-opened", onLivePageOpened);
    window.FastMDXDashboard?.on("settings-updated", onSettingsUpdated);
    window.addEventListener("resize", resizeViewers);
    document.addEventListener("fullscreenchange", () => requestAnimationFrame(resizeViewers));
    const refreshSeconds = Number(document.body?.dataset.refreshSeconds || 3);
    window.setInterval(
      pollLiveFrame,
      Math.max(1000, Math.min(60000, refreshSeconds * 1000))
    );
  }

  function onRunChanged() {
    // Requests started for the previous run are not safe to apply after this
    // reset; each async path captures and checks this generation.
    STATE.viewerGeneration += 1;
    STATE.playbackLoadPromise = null;
    pausePlayback();
    STATE.liveUpdates = true;
    STATE.liveFrameIndex = null;
    STATE.mode = "structure";
    STATE.structureInfo = null;
    STATE.structureUrl = null;
    STATE.structurePdb = null;
    STATE.currentPdb = null;
    STATE.playbackPayload = null;
    STATE.playbackPdb = null;
    STATE.environmentPdb = null;
    STATE.environmentUrl = null;
    STATE.environmentModel = null;
    STATE.environmentBaseCoordinates = null;
    STATE.environmentTranslation = null;
    STATE.playbackSignature = null;
    STATE.playbackLoaded = false;
    STATE.playbackFrames = 0;
    STATE.playbackFrameTimes = [];
    STATE.playbackTimer = null;
    STATE.model = null;
    STATE.miniModel = null;
    STATE.miniPlaybackModel = null;
    STATE.spinning = false;
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
    [STATE.viewer, STATE.miniViewer].forEach((viewer) => {
      if (!viewer) return;
      safeCall(viewer, "removeAllModels");
      safeCall(viewer, "removeAllSurfaces");
      safeCall(viewer, "removeAllShapes");
      safeCall(viewer, "removeAllLabels");
      safeCall(viewer, "render");
    });
    document.getElementById("viewer-canvas-frame")?.removeAttribute("data-ready");
    document.getElementById("mini-preview-frame")?.removeAttribute("data-ready");
  }

  /* ------------------------------------------------------------------ */
  /* Mounting and structure loading                                      */
  /* ------------------------------------------------------------------ */
  function has3Dmol() {
    return !!(window.$3Dmol && typeof window.$3Dmol.createViewer === "function");
  }

  function ensureMainViewer() {
    if (STATE.viewer) {
      resizeViewer(STATE.viewer);
      return STATE.viewer;
    }
    const target = document.getElementById("viewer-canvas");
    if (!target || !isVisible(target)) return null;
    if (!has3Dmol()) {
      showViewerMessage("3Dmol.js did not load.", "Confirm /static/3Dmol-min.js is being served, then hard-refresh the page.");
      return null;
    }
    if (STATE.viewerUnavailable) return null;
    try {
      STATE.viewer = window.$3Dmol.createViewer(target, {
        backgroundColor: COLORS.black,
        antialias: true,
        disableFog: false,
      });
    } catch (error) {
      STATE.viewerUnavailable = true;
      STATE.viewer = null;
      console.warn("3Dmol viewer initialization failed", error);
      showViewerMessage(
        "Interactive molecular viewer unavailable",
        "WebGL could not be initialized in this browser. Try enabling hardware acceleration or use the static preview."
      );
      return null;
    }
    if (STATE.currentPdb) installPdb(STATE.viewer, STATE.currentPdb, {main: true, center: true});
    return STATE.viewer;
  }

  function ensureMiniViewer() {
    if (STATE.miniViewer) {
      resizeViewer(STATE.miniViewer);
      return STATE.miniViewer;
    }
    const target = document.getElementById("mini-preview-canvas");
    if (!target || !isVisible(target) || !has3Dmol()) return null;
    if (STATE.miniViewerUnavailable) return null;
    try {
      STATE.miniViewer = window.$3Dmol.createViewer(target, {
        backgroundColor: COLORS.black,
        antialias: true,
        disableFog: true,
        nomouse: false,
      });
    } catch (error) {
      STATE.miniViewerUnavailable = true;
      STATE.miniViewer = null;
      console.warn("3Dmol mini viewer initialization failed", error);
      const empty = document.getElementById("mini-preview-empty");
      if (empty) empty.textContent = "Interactive preview unavailable (WebGL).";
      return null;
    }
    if (STATE.currentPdb) installPdb(STATE.miniViewer, STATE.currentPdb, {mini: true, center: true});
    return STATE.miniViewer;
  }

  function onViewerPageOpened() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const viewer = ensureMainViewer();
      if (viewer && STATE.currentPdb && STATE.mode !== "playback") {
        installPdb(viewer, STATE.currentPdb, {main: true, center: !STATE.model});
      } else if (viewer && STATE.mode === "playback" && STATE.playbackPdb && !STATE.playbackLoaded) {
        installPlaybackPdb(viewer, STATE.playbackPdb, {main: true, center: false});
      }
      resizeViewers();
    }));
  }

  function onLivePageOpened() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const viewer = ensureMiniViewer();
      if (viewer && STATE.mode === "playback" && STATE.playbackPdb) {
        installPlaybackPdb(viewer, STATE.playbackPdb, {mini: true, center: !STATE.miniPlaybackModel});
        setPlaybackFrame(Number(document.getElementById("traj-slider")?.value || 0));
      } else if (viewer && STATE.currentPdb) {
        installPdb(viewer, STATE.currentPdb, {mini: true, center: !STATE.miniModel});
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

    const available = !!(info?.structure_available || info?.valid);
    if (!available) {
      showViewerMessage(
        "Waiting for a molecular structure",
        "The viewer will initialize when setup/prepared.pdb or a simulation topology becomes available."
      );
      return;
    }

    // Playback owns the animated model. Replacing it with a static topology
    // on a visibility or dashboard update stops the trajectory. A separate
    // static environment model provides solvent, ions, and the unit cell.
    if (STATE.mode === "playback") {
      if (needsFullTopology()) await ensurePlaybackEnvironment();
      if (!isViewerGenerationCurrent(generation)) return;
      return;
    }

    // Water and ions are stripped from the copy the browser gets, so the
    // toggles for them had nothing to act on. Ask for the solvated system
    // only when one of them is on: the box is most of the atoms, and a reader
    // who never opens that view should not pay to download it.
    const url = withSolvent(info.structure_url || "/structure/topology.pdb");
    if (url === STATE.structureUrl && STATE.structurePdb) {
      const main = ensureMainViewer();
      if (main && !STATE.model) {
        installPdb(main, STATE.currentPdb || STATE.structurePdb, {main: true, center: true});
      }
      const mini = ensureMiniViewer();
      if (mini && !STATE.miniModel) {
        installPdb(mini, STATE.currentPdb || STATE.structurePdb, {mini: true, center: true});
      }
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
      // Solvent wins over the live frame. Frames are written to disk with
      // water and ions already stripped, so live mode cannot show them at
      // all -- asking for water while a frame is mounted otherwise fetched
      // the solvated system, stored it, and went on drawing the frame.
      if (STATE.mode !== "live" || !STATE.currentPdb || needsFullTopology()) {
        STATE.currentPdb = pdb;
      }
      STATE.mode = STATE.liveFrameIndex != null ? "live" : "structure";
      mountStoredStructureWhereVisible(true, {forceFit: true});
      hideViewerMessage();
    } catch (error) {
      console.warn("molecular structure load failed", error);
      showViewerMessage("Structure could not be loaded.", String(error));
    }
  }

  function mountStoredStructureWhereVisible(center, options) {
    const opts = options || {};
    const main = ensureMainViewer();
    if (main && STATE.currentPdb) installPdb(main, STATE.currentPdb, {main: true, center: !!center, ...opts});
    const mini = ensureMiniViewer();
    if (mini && STATE.currentPdb) installPdb(mini, STATE.currentPdb, {mini: true, center: !!center, ...opts});
  }

  function needsFullTopology() {
    // The saved/live trajectory frames contain solute coordinates only. Water,
    // ions, and CRYST1/unit-cell metadata all require the full topology.
    return Boolean(STATE.visibility?.water || STATE.visibility?.ions || STATE.visibility?.box);
  }

  function withSolvent(url) {
    if (!needsFullTopology()) return url;
    return url + (url.includes("?") ? "&" : "?") + "solvent=1";
  }

  async function ensurePlaybackEnvironment() {
    const generation = STATE.viewerGeneration;
    if (STATE.mode !== "playback") return false;
    if (!needsFullTopology()) {
      restyleViewers();
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
        STATE.environmentModel = null;
        STATE.environmentBaseCoordinates = null;
        STATE.environmentTranslation = null;
      }
      const viewer = ensureMainViewer();
      if (viewer && STATE.environmentPdb && !STATE.environmentModel) {
        STATE.environmentModel = viewer.addModel(STATE.environmentPdb, "pdb", {keepH: true});
        alignPlaybackEnvironment();
      }
      restyleViewers();
      return true;
    } catch (error) {
      console.warn("playback environment load failed", error);
      announce("Water, ions, or the periodic box could not be loaded for trajectory playback.");
      return false;
    }
  }

  function alignPlaybackEnvironment() {
    const playbackModel = STATE.model;
    const environmentModel = STATE.environmentModel;
    if (!playbackModel || !environmentModel || typeof playbackModel.selectedAtoms !== "function"
      || typeof environmentModel.selectedAtoms !== "function") return false;
    try {
      const playbackAnchor = playbackModel.selectedAtoms({resn: AMINO_ACIDS})[0];
      const environmentAtoms = environmentModel.selectedAtoms({});
      const environmentAnchor = environmentModel.selectedAtoms({resn: AMINO_ACIDS})[0];
      if (!playbackAnchor || !environmentAnchor || !environmentAtoms.length) return false;
      if (!STATE.environmentBaseCoordinates) {
        STATE.environmentBaseCoordinates = new Map(environmentAtoms.map((atom) => [
          atom.index, {x: atom.x, y: atom.y, z: atom.z},
        ]));
      }
      const anchorBase = STATE.environmentBaseCoordinates.get(environmentAnchor.index);
      if (!anchorBase) return false;
      const translation = {
        x: playbackAnchor.x - anchorBase.x,
        y: playbackAnchor.y - anchorBase.y,
        z: playbackAnchor.z - anchorBase.z,
      };
      environmentAtoms.forEach((atom) => {
        const base = STATE.environmentBaseCoordinates.get(atom.index);
        if (!base) return;
        atom.x = base.x + translation.x;
        atom.y = base.y + translation.y;
        atom.z = base.z + translation.z;
      });
      STATE.environmentTranslation = translation;
      return true;
    } catch (error) {
      console.debug("playback environment alignment skipped", error);
      return false;
    }
  }

  function installPdb(viewer, pdbText, options) {
    if (!viewer || !pdbText) return;
    const opts = options || {};
    const hadModel = opts.main ? !!STATE.model : !!STATE.miniModel;
    const previousView = hadModel && STATE.preservingCamera && !opts.forceFit
      ? captureView(viewer) : null;
    stopViewerMotion(viewer);
    safeCall(viewer, "removeAllModels");
    safeCall(viewer, "removeAllSurfaces");
    safeCall(viewer, "removeAllShapes");
    safeCall(viewer, "removeAllLabels");
    if (opts.main) {
      STATE.environmentModel = null;
      STATE.environmentBaseCoordinates = null;
      STATE.environmentTranslation = null;
    }

    let model;
    try {
      model = viewer.addModel(pdbText, "pdb", {keepH: true});
    } catch (error) {
      console.warn("3Dmol addModel failed", error);
      showViewerMessage("3Dmol could not parse this structure.", String(error));
      return;
    }
    if (opts.main) {
      STATE.model = model;
      // Made clickable here, not once at viewer creation. 3Dmol sets the
      // property on the atoms currently selected, and at creation there are
      // none -- the model is added afterwards, and every atom in it arrived
      // unclickable. Clicking the structure did nothing, and the selection
      // panel waited for an event that could not be raised.
      try {
        viewer.setHoverable({}, true, onHoverAtom, clearHoverAtom);
        viewer.setClickable({}, true, onClickAtom);
      } catch (error) {
        console.debug("3Dmol interaction callbacks unavailable", error);
      }
      styleViewer(viewer, model, false);
      document.getElementById("viewer-canvas-frame")?.setAttribute("data-ready", "true");
    } else {
      STATE.miniModel = model;
      styleViewer(viewer, model, true);
      document.getElementById("mini-preview-frame")?.setAttribute("data-ready", "true");
    }

    if (previousView) {
      restoreView(viewer, previousView);
    } else if (opts.center !== false || !hadModel) {
      // A viewer created before the first structure has the default camera,
      // which points at empty space.  Always center the first real model even
      // when a live-frame refresh requested camera preservation.
      safeCall(viewer, "zoomTo");
    }
    resizeViewer(viewer);
    safeCall(viewer, "render");
    // 3Dmol can calculate its canvas size one animation frame after a hidden
    // page becomes visible.  Re-center/render once more for the first model so
    // the mini viewer never remains black during a running simulation.
    if (!hadModel) {
      window.requestAnimationFrame(() => {
        resizeViewer(viewer);
        safeCall(viewer, "zoomTo");
        safeCall(viewer, "render");
      });
    }
  }

  function installPlaybackPdb(viewer, pdbText, options) {
    if (!viewer || !pdbText) return null;
    const opts = options || {};
    const previousView = opts.main && STATE.preservingCamera ? captureView(viewer) : null;
    stopViewerMotion(viewer);
    safeCall(viewer, "removeAllModels");
    safeCall(viewer, "removeAllSurfaces");
    safeCall(viewer, "removeAllShapes");
    safeCall(viewer, "removeAllLabels");
    if (opts.main) {
      STATE.environmentModel = null;
      STATE.environmentBaseCoordinates = null;
      STATE.environmentTranslation = null;
    }
    try {
      const added = viewer.addModelsAsFrames(pdbText, "pdb");
      const model = added && typeof added === "object" && !Array.isArray(added)
        ? added : safeCall(viewer, "getModel", 0);
      if (opts.main) {
        STATE.model = model;
        STATE.playbackLoaded = true;
        document.getElementById("viewer-canvas-frame")?.setAttribute("data-ready", "true");
        styleViewer(viewer, model, false);
      } else {
        STATE.miniPlaybackModel = model;
        document.getElementById("mini-preview-frame")?.setAttribute("data-ready", "true");
        styleViewer(viewer, model, true);
      }
      if (previousView) restoreView(viewer, previousView);
      else if (opts.center !== false) safeCall(viewer, "zoomTo");
      resizeViewer(viewer);
      return model;
    } catch (error) {
      console.warn("3Dmol playback parsing failed", error);
      announce("Trajectory playback could not be parsed by 3Dmol.");
      return null;
    }
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
        setOverlay(false, {stage: index.simulation_stage || "waiting", age: "—"});
        return;
      }
      if (String(STATE.liveFrameIndex) === String(index.live_frame_index)) {
        setOverlay(true, {
          stage: index.simulation_stage || STATE.mode,
          age: liveFrameAge(index),
          frame: index.live_frame_index,
          simtime: index.simulation_time_ns,
        });
        return;
      }
      const frameResponse = await fetch(
        `/structure/live-frame.pdb?v=${encodeURIComponent(index.live_frame_mtime || Date.now())}`,
        {cache: "no-store"}
      );
      if (!isViewerGenerationCurrent(generation)) return;
      if (!frameResponse.ok) return;
      const pdb = await frameResponse.text();
      if (!isViewerGenerationCurrent(generation)) return;
      if (!pdb.includes("ATOM") && !pdb.includes("HETATM")) return;
      STATE.liveFrameIndex = index.live_frame_index;
      if (needsFullTopology()) {
        // The frame has no solvent in it; replacing the solvated system with
        // it would empty the view the reader just asked for.
        return;
      }
      STATE.currentPdb = pdb;
      STATE.mode = "live";
      STATE.playbackLoaded = false;
      updateViewerCoordinates(pdb);
      setOverlay(true, {
        stage: index.simulation_stage || "live",
        age: liveFrameAge(index),
        frame: index.live_frame_index,
        simtime: index.simulation_time_ns,
      });
    } catch (error) {
      console.debug("live molecular frame unavailable", error);
    }
  }

  function updateViewerCoordinates(pdbText) {
    const mainTarget = document.getElementById("viewer-canvas");
    const miniTarget = document.getElementById("mini-preview-canvas");
    if (
      STATE.viewer
      && STATE.mode !== "playback"
      && mainTarget
      && isVisible(mainTarget)
    ) {
      installPdb(STATE.viewer, pdbText, {main: true, center: false});
    }
    if (miniTarget && isVisible(miniTarget)) {
      const miniViewer = ensureMiniViewer();
      if (miniViewer) {
        installPdb(miniViewer, pdbText, {mini: true, center: !STATE.miniModel});
      }
    }
  }

  function liveFrameAge(index) {
    if (!index?.live_frame_updated_at) return "—";
    const time = new Date(index.live_frame_updated_at).getTime();
    if (!Number.isFinite(time)) return "—";
    const seconds = Math.max(0, Math.round((Date.now() - time) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.round(seconds / 60)}m`;
  }

  /* ------------------------------------------------------------------ */
  /* Styling                                                             */
  /* ------------------------------------------------------------------ */
  function styleViewer(viewer, model, mini) {
    if (!viewer || !model) return;
    safeCall(viewer, "removeAllSurfaces");
    // Unit-cell lines are viewer shapes, not styles. Without clearing them a
    // visibility/color change stacks duplicate boxes on the canvas.
    safeCall(viewer, "removeAllShapes");
    try { viewer.setStyle({}, {}); } catch (error) { console.debug(error); }

    const ligandNames = ligandResnames();
    const proteinSelection = resolveProteinSelection(model);
    const ligandSelection = ligandNames.length ? {resn: ligandNames} : {resn: "__NO_LIGAND__"};
    const waterSelection = {resn: WATERS};
    const ionSelection = {resn: IONS};
    const pocketSelection = {
      byres: true,
      within: {distance: STATE.pocketCutoff, sel: ligandSelection},
    };

    if (STATE.isolateLigand && ligandNames.length) {
      addStyle(viewer, ligandSelection, ligandStyle());
    } else if (STATE.pocketOnly && ligandNames.length) {
      addStyle(viewer, pocketSelection, proteinStyle());
      if (STATE.visibility.ligand) addStyle(viewer, ligandSelection, ligandStyle());
    } else {
      if (STATE.visibility.protein) {
        if (mini) {
          // The line overlay guarantees a visible silhouette even when a PDB
          // frame lacks HELIX/SHEET records and the cartoon representation is
          // still being inferred by 3Dmol.
          addStyle(viewer, proteinSelection, {
            cartoon: {color: "spectrum", thickness: 0.5, opacity: 1.0},
            line: {color: COLORS.silver, linewidth: 1.0, opacity: 0.55},
          });
        } else {
          addStyle(viewer, proteinSelection, proteinStyle());
        }
      }
      if (!mini && STATE.visibility.pocket && ligandNames.length) {
        addStyle(viewer, pocketSelection, {
          stick: {radius: 0.10, colorscheme: STATE.colorMode === "monochrome" ? undefined : "Jmol", color: STATE.colorMode === "monochrome" ? COLORS.violet : undefined},
        });
      }
      if (STATE.visibility.ligand && ligandNames.length) {
        addStyle(viewer, ligandSelection, ligandStyle());
      }
      if (!mini && STATE.visibility.water) {
        // Explicit cyan-blue gives the solvent enough contrast on the dark
        // canvas; Jmol oxygen/hydrogen colors rendered nearly black at this
        // density and made the checked control appear broken.
        addStyle(viewer, waterSelection, {
          sphere: {scale: 0.28, color: "#4da3ff", opacity: 0.88},
          stick: {radius: 0.075, color: "#4da3ff", opacity: 0.82},
          line: {color: "#4da3ff", linewidth: 1.1, opacity: 0.72},
        });
      }
      if (!mini && STATE.visibility.ions) {
        addStyle(viewer, ionSelection, {sphere: {scale: 0.55, colorscheme: "Jmol"}});
      }
    }

    if (STATE.visibility.hydrogens) {
      addStyle(viewer, {elem: "H"}, {
        sphere: {scale: 0.18, colorscheme: "Jmol"},
        stick: {radius: 0.08, colorscheme: "Jmol"},
      });
    } else {
      try { viewer.setStyle({elem: "H"}, {}); } catch (error) { console.debug(error); }
    }
    if (!mini && STATE.representation === "surface" && STATE.visibility.protein) {
      addSurface(viewer, proteinSelection, {opacity: 0.78, color: "#d8d8dd"});
    }
    if (!mini && STATE.pocketSurface && ligandNames.length) {
      addSurface(viewer, pocketSelection, {opacity: 0.55, color: COLORS.violet});
    }
    if (!mini && STATE.mode === "playback" && STATE.environmentModel) {
      stylePlaybackEnvironment(viewer, STATE.environmentModel);
    }
    const boxModel = STATE.mode === "playback" ? STATE.environmentModel : model;
    if (!mini && STATE.visibility.box && boxModel) drawPeriodicBox(viewer, boxModel);
    safeCall(viewer, "render");
  }

  function drawPeriodicBox(viewer, model) {
    if (!viewer || !model || typeof viewer.addLine !== "function") return;
    try {
      const cryst = typeof model.getCrystData === "function" ? model.getCrystData() : null;
      const elements = cryst?.matrix?.elements;
      const atoms = typeof model.selectedAtoms === "function" ? model.selectedAtoms({}) : [];
      if (!elements || elements.length < 9 || !atoms.length) return;
      const vectors = [
        {x: elements[0], y: elements[1], z: elements[2]},
        {x: elements[3], y: elements[4], z: elements[5]},
        {x: elements[6], y: elements[7], z: elements[8]},
      ];
      const centroid = atoms.reduce((sum, atom) => ({
        x: sum.x + atom.x, y: sum.y + atom.y, z: sum.z + atom.z,
      }), {x: 0, y: 0, z: 0});
      centroid.x /= atoms.length; centroid.y /= atoms.length; centroid.z /= atoms.length;
      const origin = {
        x: centroid.x - 0.5 * (vectors[0].x + vectors[1].x + vectors[2].x),
        y: centroid.y - 0.5 * (vectors[0].y + vectors[1].y + vectors[2].y),
        z: centroid.z - 0.5 * (vectors[0].z + vectors[1].z + vectors[2].z),
      };
      const corner = (a, b, c) => ({
        x: origin.x + a * vectors[0].x + b * vectors[1].x + c * vectors[2].x,
        y: origin.y + a * vectors[0].y + b * vectors[1].y + c * vectors[2].y,
        z: origin.z + a * vectors[0].z + b * vectors[1].z + c * vectors[2].z,
      });
      const corners = [corner(0,0,0), corner(1,0,0), corner(0,1,0), corner(1,1,0),
        corner(0,0,1), corner(1,0,1), corner(0,1,1), corner(1,1,1)];
      // A true periodic cell is physically larger than the protein. Keep the
      // guide visible without allowing it to overpower a protein-only view.
      const boxOpacity = STATE.visibility.water ? 0.55 : 0.38;
      for (const [start, end] of [[0,1],[0,2],[0,4],[1,3],[1,5],[2,3],[2,6],[3,7],[4,5],[4,6],[5,7],[6,7]]) {
        viewer.addLine({start: corners[start], end: corners[end], color: COLORS.cyan, linewidth: 1.2, opacity: boxOpacity});
      }
    } catch (error) { console.debug("periodic box rendering skipped", error); }
  }

  function stylePlaybackEnvironment(viewer, environmentModel) {
    if (!viewer || !environmentModel || typeof environmentModel.getID !== "function") return;
    const scope = {model: environmentModel.getID()};
    // The static full topology is an overlay. Clear its duplicate protein
    // first, then apply only controls that require the full system.
    try { viewer.setStyle(scope, {}); } catch (error) { console.debug(error); }
    if (STATE.visibility.water) {
      // A solvated system contains thousands of water hydrogens. Rendering
      // every bond here hides the solute; oxygen markers preserve the solvent
      // envelope while the Hydrogens control still exposes atom-level detail.
      addStyle(viewer, {...scope, resn: WATERS, elem: "O"}, {
        sphere: {scale: 0.22, color: "#4da3ff", opacity: 0.42},
      });
    }
    if (STATE.visibility.ions) {
      addStyle(viewer, {...scope, resn: IONS}, {
        sphere: {scale: 0.70, colorscheme: "Jmol", opacity: 1.0},
      });
    }
    if (STATE.visibility.hydrogens) {
      addStyle(viewer, {...scope, elem: "H"}, {
        sphere: {scale: 0.12, colorscheme: "Jmol", opacity: 0.45},
      });
    }
  }

  function resolveProteinSelection(model) {
    const aminoSelection = {resn: AMINO_ACIDS};
    try {
      if (model && typeof model.selectedAtoms === "function") {
        if (model.selectedAtoms(aminoSelection).length) return aminoSelection;
        const atomRecords = {hetflag: false};
        if (model.selectedAtoms(atomRecords).length) return atomRecords;
      }
    } catch (error) {
      console.debug("protein selection fallback", error);
    }
    return aminoSelection;
  }

  function proteinStyle() {
    const color = proteinColor();
    switch (STATE.representation) {
      case "backbone": return {cartoon: Object.assign({style: "trace", thickness: 0.3}, color)};
      case "sticks": return {stick: Object.assign({radius: 0.13}, color)};
      case "ballAndStick": return {
        stick: Object.assign({radius: 0.12}, color),
        sphere: Object.assign({scale: 0.25}, color),
      };
      case "lines": return {line: Object.assign({linewidth: 1.2}, color)};
      case "surface": return {cartoon: Object.assign({opacity: 0.18}, color)};
      case "cartoon":
      default: return {cartoon: Object.assign({thickness: 0.35}, color)};
    }
  }

  function proteinColor() {
    if (STATE.colorMode === "monochrome") return {color: COLORS.white};
    if (STATE.colorMode === "spectrum") return {color: "spectrum"};
    const schemes = {
      chain: "chain",
      residue: "amino",
      element: "Jmol",
      secondary_structure: "ssPyMol",
    };
    return {colorscheme: schemes[STATE.colorMode] || "chain"};
  }

  function ligandStyle() {
    if (STATE.colorMode === "monochrome") {
      return {
        stick: {color: COLORS.cyan, radius: 0.20},
        sphere: {color: COLORS.cyan, scale: 0.26},
      };
    }
    return {
      stick: {colorscheme: "Jmol", radius: 0.20},
      sphere: {colorscheme: "Jmol", scale: 0.26},
    };
  }

  function addStyle(viewer, selection, style) {
    try {
      if (typeof viewer.addStyle === "function") viewer.addStyle(selection, style);
      else viewer.setStyle(selection, style);
    } catch (error) {
      console.debug("3Dmol style skipped", error);
    }
  }

  function addSurface(viewer, selection, style) {
    if (typeof viewer.addSurface !== "function" || !window.$3Dmol?.SurfaceType) return;
    try {
      viewer.addSurface(window.$3Dmol.SurfaceType.VDW, style, selection);
    } catch (error) {
      console.debug("3Dmol surface skipped", error);
    }
  }

  function restyleViewers() {
    const mainTarget = document.getElementById("viewer-canvas");
    const miniTarget = document.getElementById("mini-preview-canvas");
    if (STATE.viewer && STATE.model && mainTarget && isVisible(mainTarget)) {
      styleViewer(STATE.viewer, STATE.model, false);
    }
    const miniModel = STATE.mode === "playback"
      ? STATE.miniPlaybackModel
      : STATE.miniModel;
    if (STATE.miniViewer && miniModel && miniTarget && isVisible(miniTarget)) {
      styleViewer(STATE.miniViewer, miniModel, true);
    }
  }

  function ligandResnames() {
    const names = Array.isArray(STATE.structureInfo?.ligand_resnames)
      ? STATE.structureInfo.ligand_resnames.filter(Boolean)
      : [];
    if (STATE.ligandResname && !names.includes(STATE.ligandResname)) names.unshift(STATE.ligandResname);
    return names;
  }

  /* ------------------------------------------------------------------ */
  /* Controls                                                            */
  /* ------------------------------------------------------------------ */
  function wireControls() {
    document.querySelectorAll(".chip-btn[data-rep]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".chip-btn[data-rep]").forEach((item) => item.classList.toggle("active", item === button));
        STATE.representation = button.getAttribute("data-rep") || "cartoon";
        STATE.isolateLigand = false;
        STATE.pocketOnly = false;
        restyleViewers();
      });
    });
    document.querySelectorAll(".chip-btn[data-color]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".chip-btn[data-color]").forEach((item) => item.classList.toggle("active", item === button));
        STATE.colorMode = button.getAttribute("data-color") || "spectrum";
        restyleViewers();
      });
    });
    document.querySelectorAll(".chip-toggle input[data-vis]").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const which = checkbox.getAttribute("data-vis");
        STATE.visibility[which] = checkbox.checked;
        if (which === "water" || which === "ions" || which === "box") {
          if (STATE.mode === "playback") {
            // Keep the animated solute frames mounted and add/update a static
            // full-system overlay for water, ions, and the periodic cell.
            void ensurePlaybackEnvironment();
            return;
          }
          // Outside playback the atoms themselves have to be fetched or
          // dropped, not merely restyled.
          STATE.structureUrl = null;
          STATE.currentPdb = null;
          onStructureUpdated(STATE.structureInfo || {});
          return;
        }
        restyleViewers();
      });
    });
    document.querySelectorAll(".chip-btn[data-cam]").forEach((button) => {
      button.addEventListener("click", () => handleCameraAction(button.getAttribute("data-cam")));
    });
    document.querySelectorAll(".chip-btn[data-ligand]").forEach((button) => {
      button.addEventListener("click", () => handleLigandAction(button.getAttribute("data-ligand")));
    });
    document.querySelectorAll(".ctl-btn[data-action]").forEach((button) => {
      button.addEventListener("click", () => handleToolbarAction(button.getAttribute("data-action"), button));
    });
    document.getElementById("pocket-cutoff")?.addEventListener("change", (event) => {
      STATE.pocketCutoff = clamp(Number(event.target.value), 3, 15, 5);
      restyleViewers();
    });
  }

  async function handleToolbarAction(action, button) {
    if (action === "live-toggle") {
      pausePlayback();
      STATE.liveUpdates = true;
      STATE.mode = STATE.liveFrameIndex != null ? "live" : "structure";
      const viewer = ensureMainViewer();
      if (STATE.currentPdb && viewer) installPdb(viewer, STATE.currentPdb, {main: true, center: false});
      const mini = ensureMiniViewer();
      if (STATE.currentPdb && mini) installPdb(mini, STATE.currentPdb, {mini: true, center: false});
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
    const viewer = ensureMainViewer();
    if (!viewer) return;
    if (action === "play-trajectory") {
      if (STATE.playbackPlaying) pausePlayback();
      else await startPlayback();
      return;
    }
    if (action === "prev-frame") { await seekRelative(-1); return; }
    if (action === "next-frame") { await seekRelative(1); return; }
    if (action === "reset-view") {
      safeCall(viewer, "spin", false);
      safeCall(viewer, "zoomTo");
      safeCall(viewer, "render");
      return;
    }
    if (action === "fullscreen") {
      document.getElementById("viewer-canvas-frame")?.requestFullscreen?.();
      return;
    }
    if (action === "screenshot") takeScreenshot();
  }

  function handleCameraAction(action) {
    const viewer = ensureMainViewer();
    if (!viewer) return;
    if (action === "spin") {
      safeCall(viewer, "spin", true);
      STATE.spinning = true;
      return;
    }
    if (action === "stop") {
      safeCall(viewer, "spin", false);
      STATE.spinning = false;
      return;
    }
    if (action === "center-protein") safeCall(viewer, "zoomTo", {resn: AMINO_ACIDS});
    if (action === "center-ligand" && ligandResnames().length) safeCall(viewer, "zoomTo", {resn: ligandResnames()});
    if (action === "center-pocket" && ligandResnames().length) {
      safeCall(viewer, "zoomTo", {byres: true, within: {distance: STATE.pocketCutoff, sel: {resn: ligandResnames()}}});
    }
    if (action === "zoom-in") safeCall(viewer, "zoom", 1.2);
    if (action === "zoom-out") safeCall(viewer, "zoom", 0.8);
    safeCall(viewer, "render");
  }

  function handleLigandAction(action) {
    const viewer = ensureMainViewer();
    const ligands = ligandResnames();
    if (!viewer || !ligands.length) {
      announce("No ligand is available for this control.");
      return;
    }
    if (action === "center") safeCall(viewer, "zoomTo", {resn: ligands});
    if (action === "isolate") {
      STATE.isolateLigand = !STATE.isolateLigand;
      STATE.pocketOnly = false;
      restyleViewers();
      safeCall(viewer, "zoomTo", {resn: ligands});
    }
    if (action === "show-pocket") {
      STATE.visibility.pocket = true;
      STATE.isolateLigand = false;
      restyleViewers();
      safeCall(viewer, "zoomTo", {byres: true, within: {distance: STATE.pocketCutoff, sel: {resn: ligands}}});
    }
    if (action === "show-pocket-surface") {
      STATE.pocketSurface = !STATE.pocketSurface;
      restyleViewers();
    }
    if (action === "hide-distant") {
      STATE.pocketOnly = !STATE.pocketOnly;
      STATE.isolateLigand = false;
      restyleViewers();
      safeCall(viewer, "zoomTo");
    }
    if (action === "show-labels") {
      safeCall(viewer, "removeAllLabels");
      try { viewer.addResLabels({resn: ligands}, {fontColor: COLORS.white, backgroundColor: "#101012"}); } catch (error) { console.debug(error); }
    }
    if (action === "show-contacts") drawGeometricContacts();
    if (action === "show-hbonds") announce("Hydrogen-bond overlays appear only when a dedicated interaction analysis provides them.");
    safeCall(viewer, "render");
  }

  function drawGeometricContacts() {
    const viewer = STATE.viewer;
    const model = STATE.model;
    const ligands = ligandResnames();
    if (!viewer || !model || !ligands.length || typeof model.selectedAtoms !== "function") return;
    safeCall(viewer, "removeAllShapes");
    try {
      const ligandAtoms = model.selectedAtoms({resn: ligands});
      const pocketAtoms = model.selectedAtoms({
        byres: true,
        within: {distance: STATE.pocketCutoff, sel: {resn: ligands}},
        invert: true,
      });
      const contacts = [];
      ligandAtoms.forEach((ligandAtom) => {
        let closest = null;
        let closestDistance = Infinity;
        pocketAtoms.forEach((atom) => {
          if (ligands.includes(atom.resn)) return;
          const dx = ligandAtom.x - atom.x;
          const dy = ligandAtom.y - atom.y;
          const dz = ligandAtom.z - atom.z;
          const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
          if (distance <= STATE.pocketCutoff && distance < closestDistance) {
            closest = atom;
            closestDistance = distance;
          }
        });
        if (closest) contacts.push({ligandAtom, atom: closest, distance: closestDistance});
      });
      contacts.sort((a, b) => a.distance - b.distance).slice(0, 30).forEach((contact) => {
        viewer.addLine({
          start: {x: contact.ligandAtom.x, y: contact.ligandAtom.y, z: contact.ligandAtom.z},
          end: {x: contact.atom.x, y: contact.atom.y, z: contact.atom.z},
          color: COLORS.cyan,
          dashed: true,
          linewidth: 1,
          opacity: 0.75,
        });
      });
      announce(`Displayed ${Math.min(30, contacts.length)} geometric contacts within ${STATE.pocketCutoff} Å.`);
    } catch (error) {
      console.warn("geometric contact rendering failed", error);
      announce("Geometric contacts could not be calculated for this structure.");
    }
  }

  function takeScreenshot() {
    if (!STATE.viewer || typeof STATE.viewer.pngURI !== "function") return;
    safeCall(STATE.viewer, "render");
    const anchor = document.createElement("a");
    anchor.href = STATE.viewer.pngURI();
    anchor.download = "fastmdxplora-molecular-viewer.png";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }

  /* ------------------------------------------------------------------ */
  /* Playback                                                            */
  /* ------------------------------------------------------------------ */
  function onPlaybackReady(payload) {
    const signature = payload?.source_signature || payload?.compiled_at || null;
    const changed = !!(STATE.playbackSignature && signature && STATE.playbackSignature !== signature);
    const previousPayload = STATE.playbackPayload;
    const sameLiveHistorySource = changed
      && STATE.playbackLoaded
      && previousPayload?.source_kind === "live-history"
      && payload?.source_kind === "live-history";
    STATE.playbackPayload = payload || null;
    // A running job appends to the bounded live-history file, so its source
    // signature changes on every polling cycle. That is not a replacement
    // trajectory: keep the snapshot currently playing and retain the new
    // payload for the next explicit reload.
    if (sameLiveHistorySource) {
      updatePlaybackButtons();
      return;
    }
    STATE.playbackSignature = signature;
    STATE.playbackFrameTimes = Array.isArray(payload?.frame_times_ns) ? payload.frame_times_ns : [];
    STATE.playbackFrames = Number(payload?.n_frames_browser || 0);
    if (changed && !sameLiveHistorySource) {
      STATE.playbackLoaded = false;
      STATE.playbackPdb = null;
      STATE.miniPlaybackModel = null;
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
      const response = await fetch(`/api/playback-info${force ? "?force=1" : ""}`, {cache: "no-store"});
      if (!isViewerGenerationCurrent(generation)) return null;
      if (!response.ok) throw new Error(`playback info HTTP ${response.status}`);
      const payload = await response.json();
      if (!isViewerGenerationCurrent(generation)) return null;
      onPlaybackReady(payload);
      return payload;
    } catch (error) {
      console.warn("playback information request failed", error);
      return null;
    }
  }

  async function ensurePlaybackPayload() {
    if (STATE.playbackPayload?.playback_available) return STATE.playbackPayload;
    // Not forced. Force means "the user asked for this to be rebuilt"; using
    // it for "the browser has not got it yet" bypassed the disk cache every
    // time the viewer mounted or the page reloaded, re-streaming the whole
    // trajectory to produce the file that was already sitting beside it.
    const payload = await requestPlaybackPayload(false);
    if (!payload?.playback_available) {
      const reason = String(payload?.reason || "not enough frames yet").replaceAll("-", " ");
      announce(`Trajectory playback is not ready: ${reason}. Live molecular updates remain active.`);
      return null;
    }
    return payload;
  }

  async function loadPlayback(payload) {
    const generation = STATE.viewerGeneration;
    const available = payload?.playback_available ? payload : await ensurePlaybackPayload();
    if (!isViewerGenerationCurrent(generation) || !available) return false;
    const signature = available.source_signature || available.compiled_at || null;
    if (STATE.playbackLoaded && STATE.playbackPdb && STATE.playbackSignature === signature) return true;
    if (STATE.playbackLoadPromise) return STATE.playbackLoadPromise;

    const viewer = ensureMainViewer();
    if (!viewer) return false;
    let loadPromise;
    loadPromise = (async () => {
      try {
        const response = await fetch(`/structure/playback.pdb?v=${encodeURIComponent(available.compiled_at || Date.now())}`, {cache: "no-store"});
        if (!isViewerGenerationCurrent(generation)) return false;
        if (!response.ok) throw new Error(`playback HTTP ${response.status}`);
        const pdb = await response.text();
        if (!isViewerGenerationCurrent(generation)) return false;
        if (!pdb.includes("MODEL") || (!pdb.includes("ATOM") && !pdb.includes("HETATM"))) {
          throw new Error("playback PDB contains no model frames");
        }
        STATE.playbackPdb = pdb;
        STATE.playbackSignature = signature;
        STATE.playbackFrames = Number(available.n_frames_browser || 0);
        STATE.playbackFrameTimes = Array.isArray(available.frame_times_ns) ? available.frame_times_ns : [];
        STATE.mode = "playback";
        const model = installPlaybackPdb(viewer, pdb, {main: true, center: false});
        if (!model) throw new Error("3Dmol did not create a playback model");

        const mini = ensureMiniViewer();
        if (mini) installPlaybackPdb(mini, pdb, {mini: true, center: false});
        STATE.playbackLoaded = true;
        if (needsFullTopology()) await ensurePlaybackEnvironment();
        if (!isViewerGenerationCurrent(generation)) return false;
        document.getElementById("trajectory-row")?.removeAttribute("hidden");
        /* Follow the run: when frames arrive while it is ticked, show the
         * newest rather than returning to wherever the slider was. Scrubbing
         * back unticks it, so a person looking at frame 40 is not dragged
         * to frame 200 by the next poll. */
        const follow = document.getElementById("traj-follow")?.checked;
        const current = Number(document.getElementById("traj-slider")?.value || 0);
        const target = follow && STATE.playbackFrames > 0 ? STATE.playbackFrames - 1 : current;
        await setPlaybackFrame(Math.max(0, target));
        if (!isViewerGenerationCurrent(generation)) return false;
        updatePlaybackButtons();
        return true;
      } catch (error) {
        console.warn("trajectory playback load failed", error);
        announce("Trajectory playback could not be loaded. Live molecular updates still work.");
        STATE.playbackLoaded = false;
        return false;
      } finally {
        if (STATE.playbackLoadPromise === loadPromise) STATE.playbackLoadPromise = null;
      }
    })();
    STATE.playbackLoadPromise = loadPromise;
    return loadPromise;
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
  }

  async function startPlayback() {
    const payload = await ensurePlaybackPayload();
    if (!payload || !(await loadPlayback(payload))) return;
    STATE.playbackPlaying = true;
    STATE.liveUpdates = false;
    clearPlaybackTimer();
    updatePlaybackButtons();
    const interval = Math.max(50, Math.round(700 / Math.max(0.25, STATE.playbackSpeed)));
    STATE.playbackTimer = window.setInterval(() => {
      if (!STATE.playbackPlaying) return clearPlaybackTimer();
      seekRelative(STATE.playbackReverse ? -1 : 1, true);
    }, interval);
  }

  function pausePlayback() {
    STATE.playbackPlaying = false;
    clearPlaybackTimer();
    updatePlaybackButtons();
  }

  function clearPlaybackTimer() {
    if (STATE.playbackTimer) window.clearInterval(STATE.playbackTimer);
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

  function stopFollowing() {
    const follow = document.getElementById("traj-follow");
    if (follow && follow.checked) follow.checked = false;
  }

  async function setPlaybackFrame(frame) {
    const generation = STATE.viewerGeneration;
    if (!STATE.viewer || !STATE.playbackLoaded) return;
    const index = clamp(Math.round(Number(frame)), 0, Math.max(0, STATE.playbackFrames - 1), 0);
    await setViewerFrame(STATE.viewer, index);
    if (!isViewerGenerationCurrent(generation) || !STATE.playbackLoaded) return;
    if (STATE.environmentModel) {
      alignPlaybackEnvironment();
      if (STATE.visibility.box) {
        safeCall(STATE.viewer, "removeAllShapes");
        drawPeriodicBox(STATE.viewer, STATE.environmentModel);
      }
      safeCall(STATE.viewer, "render");
    }
    if (STATE.miniViewer && STATE.miniPlaybackModel) await setViewerFrame(STATE.miniViewer, index);
    const slider = document.getElementById("traj-slider");
    if (slider) slider.value = String(index);
    setText("traj-current", String(index));
    setText("traj-total", String(STATE.playbackFrames));
    const time = STATE.playbackFrameTimes[index];
    setText("traj-simtime", time != null ? Number(time).toFixed(3) : "—");
    setOverlay(false, {stage: "playback", frame: index, simtime: time});
  }

  async function setViewerFrame(viewer, index) {
    if (!viewer || typeof viewer.setFrame !== "function") return;
    try {
      await Promise.resolve(viewer.setFrame(index));
      viewer.render();
    } catch (error) {
      console.debug("3Dmol setFrame failed", error);
    }
  }

  function updatePlaybackButtons() {
    const toolbar = document.querySelector('[data-action="play-trajectory"]');
    if (toolbar) {
      toolbar.textContent = STATE.playbackPlaying ? "Pause Trajectory" : "Play Trajectory";
      toolbar.classList.toggle("active", STATE.playbackPlaying);
      toolbar.title = STATE.playbackPayload?.playback_available
        ? `${STATE.playbackFrames} browser playback frames available`
        : "Playback becomes available after at least two live coordinate snapshots";
    }
    document.querySelectorAll('[data-action="prev-frame"], [data-action="next-frame"]').forEach((button) => {
      button.setAttribute("aria-disabled", String(!STATE.playbackPayload?.playback_available));
    });
  }

  /* ------------------------------------------------------------------ */
  /* Settings, status, selections                                        */
  /* ------------------------------------------------------------------ */
  function onSettingsUpdated(settings) {
    if (settings.ligand) STATE.ligandResname = String(settings.ligand).toUpperCase();
    if (Number.isFinite(settings.pocketCutoff)) STATE.pocketCutoff = settings.pocketCutoff;
    if (settings.proteinRepresentation) STATE.representation = settings.proteinRepresentation;
    STATE.visibility.water = !!settings.showWater;
    STATE.visibility.ions = !!settings.showIons;
    STATE.preservingCamera = settings.preserveCamera !== false;
    if (STATE.viewer && typeof STATE.viewer.setBackgroundColor === "function") {
      const background = settings.background === "charcoal" ? "#101012" : COLORS.black;
      STATE.viewer.setBackgroundColor(background);
    }
    if (STATE.miniViewer && typeof STATE.miniViewer.setBackgroundColor === "function") {
      STATE.miniViewer.setBackgroundColor(COLORS.black);
    }
    if (settings.spin && STATE.viewer) safeCall(STATE.viewer, "spin", true);
    if (STATE.mode === "playback" && needsFullTopology()) void ensurePlaybackEnvironment();
    const cutoff = document.getElementById("pocket-cutoff");
    if (cutoff) cutoff.value = String(STATE.pocketCutoff);
    restyleViewers();
  }

  function onStatusUpdated(status) {
    const running = String(status?.status || "").toLowerCase() === "running";
    setOverlay(running, {
      stage: status?.stage || "—",
      simtime: status?.simulation_time_completed_ns,
      frame: status?.current_frame_count,
    });
  }

  function onHoverAtom(atom) {
    if (!atom) return;
    updateSelectionPanel(atom);
  }

  function clearHoverAtom() {
    // Keep the last selection visible; hover-out should not erase useful data.
  }

  function onClickAtom(atom) {
    if (!atom) return;
    updateSelectionPanel(atom);
  }

  function updateSelectionPanel(atom) {
    const body = document.getElementById("selection-tab-tbody");
    if (!body) return;
    body.innerHTML = `
      <tr><th>Residue</th><td>${escapeHTML(atom.resn || "—")} ${escapeHTML(atom.resi ?? "")}</td></tr>
      <tr><th>Chain</th><td>${escapeHTML(atom.chain || "—")}</td></tr>
      <tr><th>Atom</th><td>${escapeHTML(atom.atom || atom.name || "—")}</td></tr>
      <tr><th>Element</th><td>${escapeHTML(atom.elem || atom.element || "—")}</td></tr>
      <tr><th>Coordinates</th><td>${coordinate(atom.x)}, ${coordinate(atom.y)}, ${coordinate(atom.z)}</td></tr>`;
  }

  function setOverlay(live, info) {
    const overlay = document.getElementById("viewer-overlay");
    if (!overlay) return;
    overlay.setAttribute("data-live", live ? "true" : "false");
    setText("overlay-tag", live ? "LIVE" : (STATE.mode === "playback" ? "PLAYBACK" : "STATIC"));
    if (info?.stage != null) setText("overlay-stage", info.stage);
    if (info?.frame != null) setText("overlay-frame", `frame ${info.frame}`);
    if (info?.age != null) setText("overlay-age", `age ${info.age}`);
    if (info?.simtime != null) setText("overlay-simtime", `${Number(info.simtime).toFixed(3)} ns`);
  }

  /* ------------------------------------------------------------------ */
  /* Generic helpers                                                     */
  /* ------------------------------------------------------------------ */
  function captureView(viewer) {
    try {
      const view = viewer.getView();
      if (Array.isArray(view)) return view.slice();
      return view ? JSON.parse(JSON.stringify(view)) : null;
    } catch (error) {
      return null;
    }
  }

  function restoreView(viewer, view) {
    try { viewer.setView(view); } catch (error) { console.debug(error); }
  }

  function resizeViewers() {
    const mainTarget = document.getElementById("viewer-canvas");
    const miniTarget = document.getElementById("mini-preview-canvas");
    if (mainTarget && isVisible(mainTarget)) resizeViewer(STATE.viewer);
    if (miniTarget && isVisible(miniTarget)) resizeViewer(STATE.miniViewer);
  }

  function resizeViewer(viewer) {
    if (!viewer) return;
    try {
      if (typeof viewer.resize === "function") viewer.resize();
      viewer.render();
    } catch (error) {
      console.debug("viewer resize skipped", error);
    }
  }

  function stopViewerMotion(viewer) {
    try { viewer.spin(false); } catch (error) { console.debug(error); }
  }

  function safeCall(object, method, ...args) {
    try {
      if (object && typeof object[method] === "function") return object[method](...args);
    } catch (error) {
      console.debug(`3Dmol ${method} skipped`, error);
    }
    return undefined;
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
    return Number.isFinite(Number(value)) ? Number(value).toFixed(3) : "—";
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

  window.FastMDXMoleculeViewer = {
    STATE,
    onStructureUpdated,
    pollLiveFrame,
    loadPlayback,
    resize: resizeViewers,
  };
}());
