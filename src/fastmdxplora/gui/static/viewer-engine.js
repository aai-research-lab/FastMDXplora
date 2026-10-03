/* FastMDXplora: the molecule viewer's engine, Mol* behind one adapter.
 *
 * The page talks to this and never to Mol* itself, so the page, its tests
 * and a later version of Mol* meet in one place. Mol* runs as a bare
 * canvas (no Mol* panels), from the bundle vendored beside this file
 * (static/molstar/molstar.js, Mol* 5.12.0, MIT).
 *
 * What it adds to Mol*, and why:
 *  - Secondary structure is the study's DSSP (gui/by_residue.py), frame by
 *    frame, as the secondary structure analysis computes it. Mol*'s own
 *    DSSP is computed chain by chain and pairs no strands between chains,
 *    so a fibril, each strand its own chain, would be rendered as coil; and
 *    given the assignment, Mol* need not compute its own for every frame.
 *  - Atoms are named by their index in the topology the server sent, which
 *    is MDTraj's index in the same file: a selection is evaluated by MDTraj
 *    and handed over as indices, because VMD's "resid" (an author number in
 *    Mol*'s VMD syntax) and MDTraj's (an index) select different atoms.
 *  - A study's per-residue result is a colour theme of its own.
 */

(function () {
  "use strict";

  const SS_FLAGS = {H: 0x2 | 0x1000, E: 0x4 | 0x400000, C: 0};
  const RESULT_STOPS = [[44, 123, 182], [247, 247, 247], [215, 25, 28]];
  const NO_VALUE = 0x5c5c66;
  /* An atom picked to measure. */
  const PICKED = [{type: "ball-and-stick", typeParams: {sizeFactor: 0.45},
    color: "uniform", colorParams: {value: 0xe69f00}}];
  /* Terminal caps, as ligand_detection.py names them. */
  const CAPS = ["ACE", "NME", "NHE", "NH2", "FOR", "NMA"];

  /* The page's names for colours, as Mol*'s colour themes. */
  const COLOUR_THEMES = {
    chain: "chain-id",
    spectrum: "sequence-id",
    residue: "residue-name",
    element: "element-symbol",
    secondary_structure: "secondary-structure",
    monochrome: "uniform",
  };

  /* The page's names for representations, as Mol*'s. */
  const REPRESENTATIONS = {
    cartoon: "cartoon",
    backbone: "backbone",
    sticks: "ball-and-stick",
    ballAndStick: "ball-and-stick",
    lines: "line",
    surface: "molecular-surface",
    spacefill: "spacefill",
  };

  function molstar() {
    if (!window.molstar || !window.molstar.lib) throw new Error("Mol* did not load");
    return window.molstar;
  }

  async function create(element, options) {
    const M = molstar();
    const lib = M.lib;
    const opts = options || {};
    const spec = lib.plugin.DefaultPluginSpec();
    // A click is the page's: it names the atom, or picks it to measure.
    // Mol*'s own would also fly the camera to the residue and render what is
    // around it, at every atom picked; its keys would also move the camera
    // and its snapshots where the page's keys step the frames.
    const page = new Set(["ms-plugin.representation-select-loci",
      "ms-plugin.representation-focus-loci", "ms-plugin.camera-focus-loci",
      "ms-plugin.create-structure-focus-representation", "ms-plugin.camera-controls",
      "ms-plugin.snapshot-controls"]);
    spec.behaviors = [...spec.behaviors.filter((behavior) => !page.has(behavior.transformer.id)),
      M.ExtensionMap.mvs];
    // Rendered on any WebGL there is: Mol* refuses one the browser marks as
    // slow (software rendering, a virtual machine, a remote desktop), where
    // a slow picture is better than none.
    spec.config = [...(spec.config || []),
      [lib.plugin.PluginConfig.General.AllowMajorPerformanceCaveat, true]];
    const view = new lib.extensions.plugin.models.PluginViewModel({spec});
    await view.initialized;
    view.mount(element);
    const plugin = view.plugin;
    // Mounting is queued: the canvas exists once it has run.
    if (plugin.canvas3dInitialized) await plugin.canvas3dInitialized;
    for (let tries = 0; !plugin.canvas3d && tries < 200; tries++) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    if (!plugin.canvas3d) throw new Error("Mol* could not create its canvas");
    const engine = new Engine(M, view, plugin);
    // The canvas follows its box: a page laid out after the canvas was
    // made, a panel opened or closed, full screen.
    if (typeof ResizeObserver === "function") {
      let asked = false;
      engine.resizing = new ResizeObserver(() => {
        if (asked) return;
        asked = true;
        requestAnimationFrame(() => {
          asked = false;
          engine.resize();
        });
      });
      engine.resizing.observe(element);
    }
    engine.transparent = !!opts.transparent;
    // The orientation axes in the corner, where a picture is small they
    // only crowd it.
    if (opts.axes === false) {
      plugin.canvas3d.setProps({camera: {helper: {axes: {name: "off", params: {}}}}});
    }
    engine.background = opts.background == null ? 0x050505 : opts.background;
    engine.setBackground(engine.background);
    engine.setQuality(opts.quality && opts.quality !== "auto" ? opts.quality
      : (engine.softwareRendering() ? "low" : "high"));
    engine.installSecondaryStructure();
    return oneAtATime(engine);
  }

  /* What changes the scene, one call at a time, in the order asked: a live
   * frame arriving while the frames load, or a click while the last pick
   * is still being rendered, each replaced the other's half-built scene. The
   * engine's own calls to itself are not queued, so none waits on itself. */
  const QUEUED = new Set(["loadFrames", "loadStructure", "setCoordinates", "showScene",
    "clear", "build", "setScene", "showBox", "setFrame", "setRepresentation", "setColour",
    "setSecondaryStructure", "redraw", "measure", "showPicks", "clearMeasurements",
    "showContacts", "loadEnvironment", "renderEnvironment", "moveEnvironment",
    "removeEnvironment", "picture"]);

  function oneAtATime(engine) {
    let last = Promise.resolve();
    return new Proxy(engine, {
      get(target, name) {
        const value = target[name];
        if (typeof value !== "function") return value;
        if (!QUEUED.has(name)) return value.bind(target);
        return (...args) => {
          const run = last.then(() => value.apply(target, args));
          last = run.catch(() => undefined);
          return run;
        };
      },
    });
  }

  class Engine {
    constructor(M, view, plugin) {
      this.M = M;
      this.lib = M.lib;
      this.view = view;
      this.plugin = plugin;
      this.representation = "cartoon";
      this.colour = "chain";
      this.result = null;
      this.secondary = null;
      this.listeners = {click: [], hover: []};
      const SE = this.lib.structure.StructureElement;
      const emit = (kind) => ({current}) => {
        const loci = current && current.loci;
        const atom = SE.Loci.is(loci) && !SE.Loci.isEmpty(loci)
          ? this.record(SE.Loci.getFirstLocation(loci)) : null;
        this.listeners[kind].forEach((listener) => listener(atom));
      };
      plugin.behaviors.interaction.click.subscribe(emit("click"));
      plugin.behaviors.interaction.hover.subscribe(emit("hover"));
    }

    /* -------------------------------------------------------------- */
    /* What is rendered                                                 */
    /* -------------------------------------------------------------- */

    /** A topology and its frames (`/structure/frames-topology.pdb` and
     * `/structure/frames.dcd`). */
    async loadFrames(topologyUrl, coordinatesUrl) {
      await this.clear();
      const plugin = this.plugin;
      const builders = plugin.builders;
      const topology = await builders.data.download({url: absolute(topologyUrl), isBinary: false});
      const model = await builders.structure.createModel(
        await builders.structure.parseTrajectory(topology, "pdb"));
      const coordinates = await plugin.dataFormats.get("dcd").parse(plugin,
        await builders.data.download({url: absolute(coordinatesUrl), isBinary: true}));
      const trajectory = await plugin.build().toRoot()
        .apply(this.lib.plugin.StateTransforms.Model.TrajectoryFromModelAndCoordinates,
          {modelRef: model.ref, coordinatesRef: coordinates.ref},
          {dependsOn: [model.ref, coordinates.ref]})
        .commit();
      await builders.structure.hierarchy.applyPreset(trajectory, "default",
        {representationPreset: "empty", showUnitcell: false});
      await this.build();
      return {frames: this.frameCount(), atoms: this.atomCount()};
    }

    /** One structure, as PDB text or a URL. With ``coordinates``, the
     * bytes of a one-frame DCD of its atoms, it is rendered at those
     * coordinates, and ``setCoordinates`` moves its atoms after. */
    async loadStructure(source) {
      const view = source.keepCamera ? this.cameraSnapshot() : null;
      await this.clear();
      const builders = this.plugin.builders;
      const data = source.text != null
        ? await builders.data.rawData({data: source.text, label: source.label || "structure"})
        : await builders.data.download({url: absolute(source.url), isBinary: false});
      const trajectory = source.coordinates
        ? await this.atCoordinates(data, source.coordinates)
        : await builders.structure.parseTrajectory(data, source.format || "pdb");
      await builders.structure.hierarchy.applyPreset(trajectory, "default",
        {representationPreset: "empty", showUnitcell: false});
      // The view kept is the person's, where it still looks at this
      // structure: a frame the engine wrote about its own origin, 4 nm from
      // the prepared system, left a kept camera on empty space.
      const keep = view && this.looksAt(view);
      await this.build(!keep);
      if (keep) this.restoreCamera(view);
      return {frames: this.frameCount(), atoms: this.atomCount()};
    }

    /** A topology's atoms at the coordinates of a DCD's bytes: the same
     * trajectory the frames are, its coordinates kept as data that
     * ``setCoordinates`` replaces. */
    async atCoordinates(topology, bytes) {
      const builders = this.plugin.builders;
      const model = await builders.structure.createModel(
        await builders.structure.parseTrajectory(topology, "pdb"));
      const raw = await builders.data.rawData({data: bytes, label: "coordinates"});
      const coordinates = await this.plugin.dataFormats.get("dcd").parse(this.plugin, raw);
      this.coordinatesRef = raw.ref;
      return this.plugin.build().toRoot()
        .apply(this.lib.plugin.StateTransforms.Model.TrajectoryFromModelAndCoordinates,
          {modelRef: model.ref, coordinatesRef: coordinates.ref},
          {dependsOn: [model.ref, coordinates.ref]})
        .commit();
    }

    /** The atoms shown moved to a one-frame DCD's coordinates: every
     * representation, measurement and pick follows them, as they follow a
     * frame played, and nothing is loaded again. False where the structure
     * was not loaded with coordinates or these are not its atoms' (Mol*
     * then has nothing to show, and the caller loads the frame whole). */
    async setCoordinates(bytes) {
      const cells = this.plugin.state.data.cells;
      if (!this.coordinatesRef || !cells.has(this.coordinatesRef)) return false;
      const atoms = this.atomCount();
      try {
        await this.plugin.build().to(this.coordinatesRef)
          .update((old) => ({...old, data: bytes})).commit();
      } catch (error) {
        console.debug("coordinates not applied", error);
        return false;
      }
      if (!this.structure() || this.atomCount() !== atoms) return false;
      await this.followThePocket();
      return true;
    }

    /** Whether a camera's target is within the structure shown. */
    looksAt(snapshot) {
      const structure = this.structure();
      if (!structure || !snapshot || !snapshot.target) return false;
      const sphere = structure.boundary.sphere;
      const [x, y, z] = [0, 1, 2].map((axis) => snapshot.target[axis] - sphere.center[axis]);
      return Math.hypot(x, y, z) <= Math.max(5, sphere.radius);
    }

    /** One structure with shapes beside it, as one MolViewSpec scene, in
     * place of whatever was rendered: the protein as a cartoon coloured by
     * chain, anything else as sticks; ``marks``, atoms rendered as spheres
     * ({chain, resi, atom, colour, size, label}), a label at the middle of
     * the atom's residue; ``tubes``, straight
     * lines between two points in angstroms ({start, end, radius,
     * colour}); and the camera ({target, position, up}). Labels are
     * ``labelSize`` angstroms high, in ``labelColour``. */
    async showScene(scene) {
      const mvs = this.lib.extensions.mvs;
      const builder = mvs.createBuilder();
      const url = URL.createObjectURL(new Blob([scene.text], {type: "text/plain"}));
      try {
        const structure = builder.download({url}).parse({format: scene.format || "pdb"})
          .modelStructure({ref: "system"});
        const polymer = structure.component({selector: "polymer"});
        polymer.representation({type: "cartoon"})
          .color({custom: {molstar_color_theme_name: "chain-id"}});
        // A ribbon needs a few residues to be one: a short peptide's atoms
        // are rendered as well.
        const alphas = (scene.text.match(/^ATOM  .{6} CA /gm) || []).length;
        if (alphas > 0 && alphas < 8) {
          polymer.representation({type: "ball_and_stick"})
            .color({custom: {molstar_color_theme_name: "element-symbol"}});
        }
        structure.component({selector: "ligand"}).representation({type: "ball_and_stick"})
          .color({custom: {molstar_color_theme_name: "element-symbol"}});
        const at = (mark) => ({auth_asym_id: mark.chain, auth_seq_id: Number(mark.resi),
          auth_atom_id: mark.atom || "CA"});
        const shapes = builder.primitives({label_color: scene.labelColour || "#ffffff"});
        (scene.marks || []).forEach((mark) => {
          structure.component({selector: at(mark)})
            .representation({type: "spacefill", size_factor: mark.size || 1})
            .color({color: mark.colour});
          if (mark.label) {
            // At the middle of the residue, off its atom's sphere.
            shapes.label({position: {structure_ref: "system", expressions: [
              {auth_asym_id: mark.chain, auth_seq_id: Number(mark.resi)}]},
            text: mark.label, label_size: scene.labelSize || 3, label_offset: 4});
          }
        });
        (scene.tubes || []).forEach((tube) => shapes.tube({start: tube.start, end: tube.end,
          radius: tube.radius || 0.35, color: tube.colour}));
        if (scene.camera) builder.camera(scene.camera);
        await mvs.loadMVS(this.plugin, builder.getState(), {sanityChecks: false});
      } finally {
        URL.revokeObjectURL(url);
      }
      // A scene sets the canvas as it says; the engine's settings are kept.
      this.setBackground(this.background);
      this.setQuality(this.quality);
      this.mainRef = null;
      return {atoms: this.atomCount()};
    }

    async clear() {
      this.coordinatesRef = null;
      await this.plugin.clear();
    }

    structureCell() {
      const structures = this.plugin.managers.structure.hierarchy.current.structures;
      const main = structures.find((s) => s.cell.transform.ref === this.mainRef);
      if (main) return main.cell;
      const others = structures.filter((s) => s.cell.transform.ref !== this.environmentRef);
      return others.length ? others[others.length - 1].cell : null;
    }

    structure() {
      const cell = this.structureCell();
      return cell && cell.obj ? cell.obj.data : null;
    }

    async build(fit) {
      const cell = this.structureCell();
      if (!cell) return;
      this.mainRef = cell.transform.ref;
      this.environmentRef = null;
      await this.setScene(this.scene || {}, {fit: fit !== false});
    }

    /** Render what the scene asks for, from nothing: the protein as the
     * representation asks, the ligand, its pocket within the cutoff, water,
     * ions, hydrogens, the periodic box, a residue in focus, surfaces. */
    async setScene(scene, options) {
      this.scene = Object.assign({}, scene);
      const cell = this.structureCell();
      if (!cell) return;
      const view = options && options.fit ? null : this.cameraSnapshot();
      await this.removeChildren(cell.transform.ref);
      const s = this.scene;
      const show = Object.assign({protein: true, ligand: true, pocket: true, water: false,
        ions: false, hydrogens: false, box: false}, s.show || {});
      const ligands = (s.ligandNames || []).filter(Boolean);
      const ligandText = ligands.length ? `resn ${ligands.join("+")}` : null;
      // The pocket is worked out here, not by Mol*'s "within", which took
      // in atoms a whole angstrom past the cutoff.
      const usesPocket = ligandText && !s.mini && (s.pocketOnly || s.pocketSurface
        || (show.pocket && !s.isolateLigand));
      const pocket = usesPocket ? this.pocketAtoms(ligands, s.pocketCutoff) : null;
      this.pocketShown = pocket ? pocket.join(",") : null;
      const ignoreHydrogens = !show.hydrogens;
      const monochrome = this.colour === "monochrome";
      // What a component is: a name Mol* knows, a PyMOL selection, or atoms
      // by their indices.
      const add = async (key, what, representations) => {
        if (Array.isArray(what) && !what.length) return null;
        const component = what === "polymer" || what === "water" || what === "ion"
          ? await this.plugin.builders.structure.tryCreateComponentStatic(cell, what)
          : await this.plugin.builders.structure.tryCreateComponent(cell, {
            type: Array.isArray(what) ? {name: "bundle", params: this.bundleOf(what)}
              : {name: "script", params: {language: "pymol", expression: what}},
            nullIfEmpty: true, label: key}, `fastmdx-${key}`);
        if (!component) return null;
        rendered.push(key);
        for (const props of representations) {
          await this.plugin.builders.structure.representation.addRepresentation(component, props);
        }
        return component;
      };
      const proteinReps = () => this.proteinRepresentations(ignoreHydrogens);
      const atomic = (size, colour) => ({type: "ball-and-stick",
        typeParams: {sizeFactor: size, ignoreHydrogens},
        color: colour ? "uniform" : "element-symbol",
        colorParams: colour ? {value: colour} : {carbonColor: {name: "element-symbol", params: {}}}});
      this.components = {};
      // What is rendered, by the engine's names: "polymer", "short", "ligand"...
      const rendered = [];
      if (s.isolateLigand && ligandText) {
        this.components.ligand = await add("ligand", ligandText, [atomic(0.35, monochrome ? 0x63e6ff : 0)]);
      } else if (s.pocketOnly && ligandText) {
        this.components.pocket = await add("pocket", pocket, proteinReps());
        if (show.ligand) {
          this.components.ligand = await add("ligand", ligandText, [atomic(0.35, monochrome ? 0x63e6ff : 0)]);
        }
      } else {
        if (show.protein) {
          // Mol* counts a terminal cap as no part of the chain: it is
          // rendered in one with the protein, so the bond between them is.
          const protein = this.hasCaps() ? `polymer or resn ${CAPS.join("+")}` : "polymer";
          this.components.polymer = await add("polymer", protein, proteinReps());
          if (this.representation === "cartoon" && this.isShortPeptide()) {
            await add("short", protein, [atomic(0.22, 0)]);
          }
        }
        if (show.pocket && pocket) {
          this.components.pocket = await add("pocket", pocket, [atomic(0.16, monochrome ? 0xa78bfa : 0)]);
        }
        if (show.ligand && ligandText) {
          this.components.ligand = await add("ligand", ligandText, [atomic(0.35, monochrome ? 0x63e6ff : 0)]);
        }
        if (show.water && !s.mini) {
          await add("water", "water", [{type: "ball-and-stick",
            typeParams: {sizeFactor: 0.2, ignoreHydrogens}, color: "uniform",
            colorParams: {value: 0x4da3ff}}]);
        }
        if (show.ions && !s.mini) {
          await add("ions", "ion", [{type: "spacefill", typeParams: {sizeFactor: 0.55},
            color: "element-symbol"}]);
        }
      }
      if (s.pocketSurface && pocket) {
        this.components.pocketSurface = await add("pocket-surface", pocket, [{type: "molecular-surface",
          typeParams: {alpha: 0.55}, color: "uniform", colorParams: {value: 0xa78bfa}}]);
      }
      if (Array.isArray(s.focus) && s.focus.length && !s.mini) {
        await add("focus", s.focus, [{type: "ball-and-stick", typeParams: {sizeFactor: 0.22},
          color: "uniform", colorParams: {value: 0xffb86b}}, {type: "label",
          typeParams: {level: "residue", textSize: 1.6, background: true,
            backgroundColor: 0xffb86b, backgroundOpacity: 0.9, backgroundMargin: 0.3,
            // Towards the camera, in front of the ribbon it sits in.
            offsetZ: 6},
          color: "uniform", colorParams: {value: 0x050505}}]);
      }
      if (Array.isArray(s.picks) && s.picks.length && !s.mini) {
        this.components.picks = await add("picks", s.picks, PICKED);
      }
      if (s.labels && ligandText && !s.mini) {
        // The ligand's atoms by name, as the button says.
        await add("labels", ligandText, [{type: "label", typeParams: {level: "element",
          textSize: 0.7, offsetZ: 1.5}, color: "uniform", colorParams: {value: 0xffffff}}]);
      }
      await this.showBox(!!show.box && !s.mini);
      this.rendered = rendered;
      if (view) this.restoreCamera(view);
      else this.fit();
    }

    /** The atoms of the residues of the protein with a heavy atom within
     * ``cutoff`` angstroms of a heavy atom of the ligands, centre to
     * centre, in the frame shown: the pocket, as the contacts analysis
     * finds one. */
    pocketAtoms(ligandNames, cutoff) {
      const reach = Number(cutoff) > 0 ? Number(cutoff) : 5;
      const SP = this.lib.structure.StructureProperties;
      const names = new Set(ligandNames);
      const caps = new Set(CAPS);
      const ligand = [];
      const residues = new Map();
      this.forEachAtom((location) => {
        const name = SP.atom.auth_comp_id(location);
        if (names.has(name)) {
          if (SP.atom.type_symbol(location) !== "H") {
            ligand.push([SP.atom.x(location), SP.atom.y(location), SP.atom.z(location)]);
          }
          return;
        }
        if (SP.entity.type(location) !== "polymer" && !caps.has(name)) return;
        const unit = location.unit;
        const residue = `${unit.id}:${unit.model.atomicHierarchy.residueAtomSegments.index[location.element]}`;
        let atoms = residues.get(residue);
        if (!atoms) residues.set(residue, atoms = {indices: [], heavy: []});
        atoms.indices.push(location.element);
        if (SP.atom.type_symbol(location) !== "H") {
          atoms.heavy.push([SP.atom.x(location), SP.atom.y(location), SP.atom.z(location)]);
        }
      });
      if (!ligand.length) return [];
      // The ligand's atoms in cubes as wide as the cutoff: an atom within it
      // is in its own cube or one beside it.
      const cube = (p) => p.map((value) => Math.floor(value / reach));
      const cubes = new Map();
      ligand.forEach((p) => {
        const key = cube(p).join(",");
        if (!cubes.has(key)) cubes.set(key, []);
        cubes.get(key).push(p);
      });
      const squared = reach * reach;
      const near = (p) => {
        const [i, j, k] = cube(p);
        for (let a = i - 1; a <= i + 1; a++) {
          for (let b = j - 1; b <= j + 1; b++) {
            for (let c = k - 1; c <= k + 1; c++) {
              const found = cubes.get(`${a},${b},${c}`);
              if (found && found.some((q) => (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
                + (p[2] - q[2]) ** 2 <= squared)) return true;
            }
          }
        }
        return false;
      };
      const pocket = [];
      residues.forEach((atoms) => {
        if (atoms.heavy.some(near)) pocket.push(...atoms.indices);
      });
      return pocket.sort((a, b) => a - b);
    }

    /** Atoms by their indices as a selection Mol* keeps across frames. */
    bundleOf(indices) {
      return this.lib.structure.StructureElement.Bundle.fromLoci(this.lociOf(indices));
    }

    /** The pocket of the frame now shown, where it is rendered and has
     * changed: its components are given the new atoms, and nothing else is
     * rendered again. */
    async followThePocket() {
      const s = this.scene || {};
      if (this.pocketShown == null) return;
      const atoms = this.pocketAtoms((s.ligandNames || []).filter(Boolean), s.pocketCutoff);
      const now = atoms.join(",");
      if (now === this.pocketShown) return;
      const parts = [this.components.pocket, this.components.pocketSurface].filter(Boolean);
      if (!atoms.length || parts.length === 0) {
        await this.setScene(s);
        return;
      }
      this.pocketShown = now;
      const bundle = this.bundleOf(atoms);
      const update = this.plugin.build();
      parts.forEach((part) => update.to(part.ref || part.transform.ref).update((old) => ({
        ...old, type: {name: "bundle", params: bundle}})));
      await update.commit();
    }

    /** The protein's representations for the representation chosen. */
    proteinRepresentations(ignoreHydrogens) {
      const colour = this.colourProps();
      const type = REPRESENTATIONS[this.representation] || "cartoon";
      if (this.representation === "surface") {
        return [
          Object.assign({type: "cartoon", typeParams: {alpha: 0.18}}, colour),
          Object.assign({type: "molecular-surface", typeParams: {alpha: 0.78}},
            this.colour === "result" ? colour : {color: "uniform", colorParams: {value: 0xd8d8dd}}),
        ];
      }
      if (type === "ball-and-stick") {
        return [Object.assign({type, typeParams: {sizeFactor: this.representation === "sticks"
          ? 0.18 : 0.3, ignoreHydrogens}}, colour)];
      }
      return [Object.assign({type, typeParams: type === "line" ? {ignoreHydrogens} : {}}, colour)];
    }

    colourProps() {
      if (this.colour === "monochrome") return {color: "uniform", colorParams: {value: 0xffffff}};
      // By element is by element throughout: Mol*'s colours carbon by chain.
      if (this.colour === "element") {
        return {color: "element-symbol",
          colorParams: {carbonColor: {name: "element-symbol", params: {}}}};
      }
      return {color: this.themeName()};
    }

    /** Whether the structure has a terminal cap, counted once for each
     * structure loaded. */
    hasCaps() {
      const structure = this.structure();
      if (!structure) return false;
      if (this.capsOf !== structure) {
        const SP = this.lib.structure.StructureProperties;
        const names = new Set(CAPS);
        let found = false;
        this.forEachAtom((location) => {
          if (!found && names.has(SP.atom.auth_comp_id(location))) found = true;
        });
        this.capsOf = structure;
        this.capsFound = found;
      }
      return this.capsFound;
    }

    isShortPeptide() {
      const structure = this.structure();
      if (!structure) return false;
      let alphas = 0;
      const SP = this.lib.structure.StructureProperties;
      this.forEachAtom((location) => {
        if (SP.atom.auth_atom_id(location) === "CA" && SP.entity.type(location) === "polymer") {
          alphas += 1;
        }
      });
      return alphas > 0 && alphas < 8;
    }

    async removeChildren(ref) {
      const tree = this.plugin.state.data.tree;
      const children = tree.children.get(ref);
      if (!children || !children.size) return;
      const update = this.plugin.build();
      children.forEach((child) => update.delete(child));
      await update.commit();
    }

    /** The periodic box, from the topology's CRYST1, shown or not. */
    async showBox(on) {
      const models = this.plugin.managers.structure.hierarchy.current.models;
      for (const model of models) {
        const unitcell = model.unitcell;
        if (on && !unitcell) {
          await this.plugin.builders.structure.tryCreateUnitcell(model.cell.transform.ref,
            undefined, {isHidden: false});
        } else if (!on && unitcell) {
          await this.plugin.build().delete(unitcell.cell.transform.ref).commit();
        }
      }
    }

    cameraSnapshot() {
      return this.plugin.canvas3d ? this.plugin.canvas3d.camera.getSnapshot() : null;
    }

    restoreCamera(snapshot) {
      if (snapshot && this.plugin.canvas3d) this.plugin.canvas3d.camera.setState(snapshot, 0);
    }

    /** Each atom of the structure shown, as a location. */
    forEachAtom(visit) {
      const structure = this.structure();
      if (!structure) return;
      const location = this.lib.structure.StructureElement.Location.create(structure);
      for (const unit of structure.units) {
        location.unit = unit;
        for (let i = 0; i < unit.elements.length; i++) {
          location.element = unit.elements[i];
          visit(location);
        }
      }
    }

    /** The atoms matching a simple selection: residue names (one or a list),
     * chain, residue number, atom name; every atom where it is empty. */
    find(selection) {
      const wanted = selection || {};
      const names = wanted.resn == null ? null
        : new Set((Array.isArray(wanted.resn) ? wanted.resn : [wanted.resn]).map(String));
      const SP = this.lib.structure.StructureProperties;
      const found = [];
      this.forEachAtom((location) => {
        if (names && !names.has(SP.atom.auth_comp_id(location))) return;
        if (wanted.chain != null && SP.chain.auth_asym_id(location) !== wanted.chain) return;
        if (wanted.resi != null && SP.residue.auth_seq_id(location) !== Number(wanted.resi)) return;
        if (wanted.atom != null && SP.atom.auth_atom_id(location) !== wanted.atom) return;
        if (wanted.elem != null && SP.atom.type_symbol(location) !== wanted.elem) return;
        found.push(this.record(location));
      });
      return found;
    }

    atomCount() {
      const structure = this.structure();
      return structure ? structure.elementCount : 0;
    }

    frameCount() {
      const model = this.trajectoryModelCell();
      if (!model || !model.obj) return 0;
      return this.lib.structure.Model.TrajectoryInfo.get(model.obj.data).size;
    }

    frame() {
      const model = this.trajectoryModelCell();
      if (!model || !model.obj) return 0;
      return this.lib.structure.Model.TrajectoryInfo.get(model.obj.data).index;
    }

    trajectoryModelCell() {
      const models = this.plugin.managers.structure.hierarchy.current.models;
      const info = this.lib.structure.Model.TrajectoryInfo;
      const moving = models.find((model) => model.cell.obj && info.get(model.cell.obj.data).size > 1);
      return (moving || models[0] || {}).cell || null;
    }

    async setFrame(index) {
      const cell = this.trajectoryModelCell();
      if (!cell) return;
      const total = this.frameCount();
      const frame = Math.max(0, Math.min(total - 1, Math.round(index)));
      await this.plugin.build().to(cell.transform.ref)
        .update((old) => ({...old, modelIndex: frame})).commit();
      await this.followThePocket();
    }

    async setRepresentation(name) {
      this.representation = REPRESENTATIONS[name] ? name : "cartoon";
      await this.setScene(this.scene || {});
    }

    /* -------------------------------------------------------------- */
    /* Colours                                                          */
    /* -------------------------------------------------------------- */

    themeName() {
      if (this.colour === "result" && this.result) return this.result.theme;
      return COLOUR_THEMES[this.colour] || "chain-id";
    }

    async setColour(name, property, shared) {
      this.colour = name;
      if (name === "result") this.result = property ? this.registerResult(property, shared) : null;
      await this.setScene(this.scene || {});
    }

    /** A per-residue result (`/api/residue-values`) as a colour theme:
     * blue at the low end of its range, white in the middle, red at the
     * high end, reversed where the result says so; grey without a value. */
    registerResult(property, shared) {
      const lib = this.lib;
      const SP = lib.structure.StructureProperties;
      const SE = lib.structure.StructureElement;
      const Bond = lib.structure.Bond;
      const chained = property.values.some((row) => row[0] != null);
      const values = new Map(property.values.map(
        (row) => [`${chained ? row[0] : ""}|${row[1]}|${row[2] || ""}`, Number(row[3])]));
      const low = Number(property.low);
      const span = Number(property.high) - low;
      const colourOf = (value) => {
        if (value == null || !Number.isFinite(value)) return NO_VALUE;
        let t = span > 0 ? Math.max(0, Math.min(1, (value - low) / span)) : 0;
        if (property.reverse) t = 1 - t;
        const scaled = t * (RESULT_STOPS.length - 1);
        const lower = Math.min(RESULT_STOPS.length - 2, Math.floor(scaled));
        const within = scaled - lower;
        const c = RESULT_STOPS[lower].map((channel, i) =>
          Math.round(channel + within * (RESULT_STOPS[lower + 1][i] - channel)));
        return (c[0] << 16) | (c[1] << 8) | c[2];
      };
      const unnamed = shared instanceof Set ? shared : new Set();
      const valueAt = (location) => {
        const key = `${chained ? String(SP.chain.auth_asym_id(location)).trim() : ""}|`
          + `${SP.residue.auth_seq_id(location)}|${(SP.residue.pdbx_PDB_ins_code(location) || "").trim()}`;
        // Two residues the structure cannot tell apart are given neither's.
        if (unnamed.has(key)) return null;
        if (values.has(key)) return values.get(key);
        return property.absent == null ? null : Number(property.absent);
      };
      const scratch = SE.Location.create();
      const theme = `fastmdx-result-${property.key}`;
      const provider = {
        name: theme,
        label: property.label,
        category: "FastMDXplora",
        factory: (ctx, props) => ({
          factory: provider.factory, granularity: "group", props,
          description: property.about,
          color: (location) => {
            if (SE.Location.is(location)) return colourOf(valueAt(location));
            if (Bond.isLocation(location)) {
              scratch.structure = location.aStructure;
              scratch.unit = location.aUnit;
              scratch.element = location.aUnit.elements[location.aIndex];
              return colourOf(valueAt(scratch));
            }
            return NO_VALUE;
          },
        }),
        getParams: () => ({}),
        defaultValues: {},
        isApplicable: () => true,
      };
      const registry = this.plugin.representation.structure.themes.colorThemeRegistry;
      if (registry.has(provider)) registry.remove(provider);
      registry.add(provider);
      return {theme, colourOf, valueAt, property};
    }

    /* -------------------------------------------------------------- */
    /* Secondary structure                                              */
    /* -------------------------------------------------------------- */

    /** Answer Mol*'s secondary structure from the study's DSSP where it is
     * given, frame by frame, and from Mol* where it is not. */
    installSecondaryStructure() {
      const provider = this.plugin.customStructureProperties
        .get("molstar_computed_secondary_structure");
      if (!provider || provider.fastmdxOriginal) return;
      const original = provider.get.bind(provider);
      const cache = new WeakMap();
      provider.fastmdxOriginal = original;
      provider.get = (structure) => {
        const root = structure.root || structure;
        if (!this.secondary) return original(structure);
        let kept = cache.get(root);
        if (!kept || kept.version !== this.secondary.version) {
          kept = {version: this.secondary.version, box: this.secondaryFor(root)};
          cache.set(root, kept);
        }
        return kept.box || original(structure);
      };
    }

    /** The study's DSSP (`/api/secondary-structure`) for what is rendered:
     * one string of H, E and C for each frame, residues named by chain,
     * number, insertion code, name and occurrence. */
    async setSecondaryStructure(said) {
      this.secondary = said && said.available ? {
        said,
        index: new Map(said.residues.map(
          (row, i) => [`${row[0]}|${row[1]}|${row[2]}|${row[3]}#${row[4]}`, i])),
        version: (this.secondary ? this.secondary.version : 0) + 1,
      } : null;
      // Each frame's structure is answered once for each assignment given.
      await this.redraw();
      return !!this.secondary;
    }

    secondaryFor(structure) {
      const info = this.lib.structure.Model.TrajectoryInfo.get(structure.model);
      const said = this.secondary.said;
      const codes = said.frames[said.frames.length === 1 ? 0 : info.index];
      if (codes == null) return null;
      const index = this.secondary.index;
      const value = new Map();
      const seen = new Map();
      for (const unit of structure.units) {
        if (unit.kind !== 0) continue;
        const h = unit.model.atomicHierarchy;
        const residues = [];
        let last = -1;
        for (let i = 0; i < unit.elements.length; i++) {
          const residue = h.residueAtomSegments.index[unit.elements[i]];
          if (residue !== last) { residues.push(residue); last = residue; }
        }
        const chain = h.chains.auth_asym_id.value(unit.chainIndex[unit.elements[0]]);
        const type = new Uint32Array(residues.length);
        const keys = [];
        const elements = [];
        residues.forEach((residue, i) => {
          const name = h.atoms.auth_comp_id.value(h.residueAtomSegments.offsets[residue]);
          const key = `${String(chain).trim()}|${h.residues.auth_seq_id.value(residue)}|`
            + `${(h.residues.pdbx_PDB_ins_code.value(residue) || "").trim()}|${name}`;
          const n = seen.get(key) || 0;
          seen.set(key, n + 1);
          const position = index.get(`${key}#${n}`);
          const code = position == null ? "C" : codes[position];
          type[i] = SS_FLAGS[code] || 0;
          if (!elements.length || elements[elements.length - 1].flags !== type[i]) {
            elements.push({kind: code === "H" ? "helix" : (code === "E" ? "sheet" : "none"),
              flags: type[i]});
          }
          keys[i] = elements.length - 1;
        });
        const getIndex = (residue) => {
          let lo = 0;
          let hi = residues.length - 1;
          while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (residues[mid] === residue) return mid;
            if (residues[mid] < residue) lo = mid + 1; else hi = mid - 1;
          }
          return -1;
        };
        value.set(unit.invariantId, {type, key: keys, elements, getIndex});
      }
      return {value, version: 1e6 * this.secondary.version + info.index};
    }

    /** What is rendered for each residue of the frame shown: H, E or C, in
     * the order of the structure's atoms. */
    secondaryStructureShown() {
      const structure = this.structure();
      if (!structure) return {};
      const provider = this.plugin.customStructureProperties
        .get("molstar_computed_secondary_structure");
      const value = provider.get(structure).value;
      const out = {};
      if (!value) return out;
      for (const unit of structure.units) {
        const ss = value.get(unit.invariantId);
        if (!ss) continue;
        const h = unit.model.atomicHierarchy;
        let last = -1;
        let codes = "";
        for (let i = 0; i < unit.elements.length; i++) {
          const residue = h.residueAtomSegments.index[unit.elements[i]];
          if (residue === last) continue;
          last = residue;
          const flag = ss.type[ss.getIndex(residue)];
          codes += (flag & 0x2) ? "H" : ((flag & 0x4) ? "E" : "C");
        }
        const chain = h.chains.auth_asym_id.value(unit.chainIndex[unit.elements[0]]);
        out[chain] = (out[chain] || "") + codes;
      }
      return out;
    }

    async redraw() {
      await this.setScene(this.scene || {});
    }

    /* -------------------------------------------------------------- */
    /* Atoms                                                            */
    /* -------------------------------------------------------------- */

    /** An atom as the page reads it, its index the topology's. */
    record(location) {
      const SP = this.lib.structure.StructureProperties;
      return {
        index: location.element,
        chain: SP.chain.auth_asym_id(location),
        resi: SP.residue.auth_seq_id(location),
        icode: (SP.residue.pdbx_PDB_ins_code(location) || "").trim(),
        resn: SP.atom.auth_comp_id(location),
        atom: SP.atom.auth_atom_id(location),
        elem: SP.atom.type_symbol(location),
        x: SP.atom.x(location), y: SP.atom.y(location), z: SP.atom.z(location),
      };
    }

    /** The atoms at these topology indices, in the frame shown. */
    atoms(indices) {
      const structure = this.structure();
      if (!structure || !indices.length) return [];
      const SE = this.lib.structure.StructureElement;
      const loci = SE.Schema.toLoci(structure, {items: {atom_index: indices}});
      const found = [];
      SE.Loci.forEachLocation(loci, (location) => found.push(this.record(location)));
      return found;
    }

    /** The colour the result shown gives the atom at this index. */
    resultColourOf(index) {
      if (!this.result) return null;
      const SE = this.lib.structure.StructureElement;
      const location = SE.Loci.getFirstLocation(this.lociOf([index]));
      return location ? this.result.colourOf(this.result.valueAt(location)) : null;
    }

    lociOf(indices) {
      const structure = this.structure();
      const SE = this.lib.structure.StructureElement;
      return SE.Schema.toLoci(structure, {items: {atom_index: indices}});
    }

    /** The camera on these atoms, with some of what is around them. */
    focus(indices) {
      if (!indices || !indices.length) return;
      const loci = this.lociOf(indices);
      this.plugin.managers.camera.focusLoci(loci, {extraRadius: 6});
    }

    /** The camera on one of the scene's parts: "polymer", "ligand" or
     * "pocket", where it is rendered. */
    focusPart(name) {
      const component = this.components && this.components[name];
      const structure = component && component.cell && component.cell.obj && component.cell.obj.data;
      if (!structure) return false;
      this.plugin.managers.camera.focusLoci(
        this.lib.structure.Structure.toStructureElementLoci(structure));
      return true;
    }

    /** The residues of the protein, in order, as the viewer finds them: a
     * new one wherever chain, number, insertion code or name changes, or an
     * atom's name comes round again (gui/by_residue.py's residue_runs). */
    residues() {
      const SP = this.lib.structure.StructureProperties;
      const found = [];
      let previous = null;
      let names = new Set();
      this.forEachAtom((location) => {
        if (SP.entity.type(location) !== "polymer") return;
        const chain = String(SP.chain.auth_asym_id(location)).trim();
        const resi = SP.residue.auth_seq_id(location);
        const icode = String(SP.residue.pdbx_PDB_ins_code(location) || "").trim();
        const resn = SP.atom.auth_comp_id(location);
        const atom = SP.atom.auth_atom_id(location);
        const key = `${chain}|${resi}|${icode}|${resn}`;
        if (key === previous && !names.has(atom)) {
          names.add(atom);
          return;
        }
        previous = key;
        names = new Set([atom]);
        found.push({chain, resi, icode, resn});
      });
      return found;
    }

    /** The secondary structure rendered for the residue of the atom at this
     * index in the frame shown: "h", "s" or "c". */
    secondaryStructureOf(index) {
      const structure = this.structure();
      if (!structure) return "c";
      const SE = this.lib.structure.StructureElement;
      const location = SE.Loci.getFirstLocation(this.lociOf([index]));
      if (!location) return "c";
      const provider = this.plugin.customStructureProperties
        .get("molstar_computed_secondary_structure");
      const value = provider.get(structure).value;
      const ss = value && value.get(location.unit.invariantId);
      if (!ss) return "c";
      const residue = location.unit.model.atomicHierarchy.residueAtomSegments.index[location.element];
      const flag = ss.type[ss.getIndex(residue)];
      return (flag & 0x2) ? "h" : ((flag & 0x4) ? "s" : "c");
    }

    /** Two atoms' distance, three an angle, four a dihedral, rendered by Mol*,
     * which follows them as the frames change. */
    async measure(indices) {
      await this.clearMeasurements();
      if (!indices || indices.length < 2) return;
      const loci = indices.map((index) => this.lociOf([index]));
      const measurement = this.plugin.managers.structure.measurement;
      // Lines and words a figure can carry: Mol*'s are a hairline and a
      // label a few tenths of an angstrom high.
      const options = {lineParams: {linesColor: 0xe69f00, linesSize: 0.12, dashLength: 0.3},
        labelParams: {textColor: 0xffffff, textSize: 2, borderColor: 0x1f2328,
          borderWidth: 0.25, background: true, backgroundColor: 0x1f2328, backgroundOpacity: 0.75,
          offsetZ: 4}};
      if (loci.length === 2) await measurement.addDistance(loci[0], loci[1], options);
      if (loci.length === 3) await measurement.addAngle(loci[0], loci[1], loci[2], options);
      if (loci.length === 4) await measurement.addDihedral(loci[0], loci[1], loci[2], loci[3], options);
      this.measured = true;
    }

    /** The atoms picked to measure, shown, and nothing else rendered again. */
    async showPicks(indices) {
      const cell = this.structureCell();
      if (!cell) return;
      this.scene = Object.assign({}, this.scene, {picks: indices.slice()});
      const components = this.components || (this.components = {});
      if (components.picks) {
        await this.plugin.build().delete(components.picks.ref).commit();
        components.picks = null;
      }
      if (!indices.length) return;
      const component = await this.plugin.builders.structure.tryCreateComponent(cell, {
        type: {name: "bundle", params: this.bundleOf(indices)}, nullIfEmpty: true,
        label: "picks"}, "fastmdx-picks");
      if (!component) return;
      for (const props of PICKED) {
        await this.plugin.builders.structure.representation.addRepresentation(component, props);
      }
      components.picks = component;
    }

    /** How many distances, angles and dihedrals are shown. */
    measurementCount() {
      const state = this.plugin.managers.structure.measurement.state;
      return (state.distances || []).length + (state.angles || []).length
        + (state.dihedrals || []).length;
    }

    async clearMeasurements() {
      const state = this.plugin.managers.structure.measurement.state;
      const refs = [...(state.distances || []), ...(state.angles || []),
        ...(state.dihedrals || []), ...(state.labels || [])].map((cell) => cell.transform.ref);
      if (!refs.length) return;
      const update = this.plugin.build();
      refs.forEach((ref) => update.delete(ref));
      await update.commit();
      this.measured = false;
    }

    /** Dashed lines from atoms to atoms, each with its distance: the
     * ligand's closest contacts. */
    async showContacts(pairs) {
      await this.clearMeasurements();
      const measurement = this.plugin.managers.structure.measurement;
      for (const [a, b] of pairs) {
        await measurement.addDistance(this.lociOf([a]), this.lociOf([b]),
          {lineParams: {linesColor: 0x63e6ff, linesSize: 0.08, dashLength: 0.25},
            labelParams: {textSize: 0.9, textColor: 0xffffff, background: true,
              backgroundColor: 0x1f2328, backgroundOpacity: 0.75, offsetZ: 2}});
      }
    }

    zoom(factor) {
      const camera = this.plugin.canvas3d && this.plugin.canvas3d.camera;
      if (!camera) return;
      const snapshot = camera.getSnapshot();
      camera.setState({...snapshot, radius: snapshot.radius / factor}, 200);
    }

    spin(on) {
      // A turn every ten seconds, about the screen's vertical.
      this.plugin.canvas3d.setProps({trackball: {animate: on
        ? {name: "spin", params: {speed: 0.1, axis: [0, -1, 0]}} : {name: "off", params: {}}}});
      this.spinning = !!on;
    }

    /* -------------------------------------------------------------- */
    /* The solvent beside the frames                                    */
    /* -------------------------------------------------------------- */

    /** The solvated system, rendered beside the frames for its water, ions and
     * box: the frames are sent without them. Moved by ``translation``, in
     * angstroms, so it sits where the frame does. */
    async loadEnvironment(text, show, translation) {
      await this.removeEnvironment();
      const builders = this.plugin.builders;
      const data = await builders.data.rawData({data: text, label: "environment"});
      const trajectory = await builders.structure.parseTrajectory(data, "pdb");
      const model = await builders.structure.createModel(trajectory);
      const structure = await builders.structure.createStructure(model);
      this.environmentRef = structure.ref;
      this.environmentShow = Object.assign({}, show);
      this.environmentShift = translation || [0, 0, 0];
      await this.renderEnvironment();
    }

    async renderEnvironment() {
      if (!this.environmentRef) return;
      await this.removeChildren(this.environmentRef);
      const show = this.environmentShow || {};
      let target = this.environmentRef;
      const [x, y, z] = this.environmentShift || [0, 0, 0];
      if (x || y || z) {
        const moved = await this.plugin.build().to(this.environmentRef).apply(
          this.lib.plugin.StateTransforms.Model.TransformStructureConformation,
          {transform: {name: "components", params: {translation: [x, y, z], axis: [1, 0, 0],
            angle: 0, rotationCenter: {name: "point", params: {point: [0, 0, 0]}}}}}).commit();
        target = moved.ref;
      }
      const builders = this.plugin.builders.structure;
      if (show.water) {
        const water = await builders.tryCreateComponentStatic(target, "water");
        if (water) {
          await builders.representation.addRepresentation(water, {type: "ball-and-stick",
            typeParams: {sizeFactor: 0.2, ignoreHydrogens: !show.hydrogens},
            color: "uniform", colorParams: {value: 0x4da3ff}});
        }
      }
      if (show.ions) {
        const ions = await builders.tryCreateComponentStatic(target, "ion");
        if (ions) {
          await builders.representation.addRepresentation(ions, {type: "spacefill",
            typeParams: {sizeFactor: 0.7}, color: "element-symbol"});
        }
      }
    }

    async moveEnvironment(translation) {
      this.environmentShift = translation;
      await this.renderEnvironment();
    }

    async removeEnvironment() {
      if (!this.environmentRef) return;
      const tree = this.plugin.state.data.tree;
      let root = this.environmentRef;
      // Up to the data it was parsed from, so nothing of it is left.
      for (let parent = tree.transforms.get(root).parent; parent && parent !== "-=root=-";
        parent = tree.transforms.get(parent).parent) root = parent;
      await this.plugin.build().delete(root).commit();
      this.environmentRef = null;
    }

    /** Atoms of the environment, for the tests and the box. */
    environmentCount(resn) {
      if (!this.environmentRef) return 0;
      const cell = this.plugin.state.data.cells.get(this.environmentRef);
      const structure = cell && cell.obj && cell.obj.data;
      if (!structure) return 0;
      const SP = this.lib.structure.StructureProperties;
      const location = this.lib.structure.StructureElement.Location.create(structure);
      let count = 0;
      for (const unit of structure.units) {
        location.unit = unit;
        for (let i = 0; i < unit.elements.length; i++) {
          location.element = unit.elements[i];
          if (!resn || SP.atom.auth_comp_id(location) === resn) count += 1;
        }
      }
      return count;
    }

    /** A click on the atom at this index, as Mol* sends one. */
    click(index) {
      this.plugin.behaviors.interaction.click.next({current: {loci: this.lociOf([index])},
        buttons: 0, button: 0, modifiers: {}});
    }

    on(kind, listener) {
      if (this.listeners[kind]) this.listeners[kind].push(listener);
    }

    /* -------------------------------------------------------------- */
    /* The canvas                                                       */
    /* -------------------------------------------------------------- */

    /** Whether WebGL is rendered by the processor (SwiftShader, llvmpipe):
     * occlusion, outlines and multi-sampling then cost seconds a frame. */
    softwareRendering() {
      try {
        const gl = this.plugin.canvas3d.webgl.gl;
        const info = gl.getExtension("WEBGL_debug_renderer_info");
        const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
        return /swiftshader|llvmpipe|software|softpipe/i.test(renderer);
      } catch (error) {
        return false;
      }
    }

    setBackground(colour) {
      this.background = colour;
      this.plugin.canvas3d.setProps({transparentBackground: !!this.transparent,
        renderer: {backgroundColor: colour}});
    }

    /** "high": occlusion, outlines and multi-sampling; "low" none of them,
     * for software rendering, where each costs seconds a frame. */
    setQuality(level) {
      this.quality = level;
      const high = level === "high";
      this.plugin.canvas3d.setProps({
        multiSample: {mode: high ? "temporal" : "off"},
        postprocessing: high ? {
          occlusion: {name: "on", params: {samples: 32, multiScale: {name: "off", params: {}},
            radius: 5, bias: 0.8, blurKernelSize: 15, blurDepthBias: 0.5, resolutionScale: 1,
            color: 0x000000, transparentThreshold: 0.4}},
          outline: {name: "on", params: {scale: 1, threshold: 0.33, color: 0x000000,
            includeTransparent: true}},
        } : {occlusion: {name: "off", params: {}}, outline: {name: "off", params: {}}},
      });
    }

    /** The camera on the whole structure, at once. Mol*'s own reset is a
     * request carried out at the next frame it renders, which on a busy
     * machine left the camera where it was for a while after a load. */
    fit() {
      const structure = this.structure();
      const canvas = this.plugin.canvas3d;
      if (!structure || !canvas) {
        this.plugin.managers.camera.reset();
        return;
      }
      const sphere = structure.boundary.sphere;
      canvas.camera.setState(canvas.camera.getFocus(sphere.center, Math.max(sphere.radius, 1) + 2), 0);
      canvas.requestDraw();
    }

    resetView() {
      this.plugin.managers.camera.reset();
    }

    resize() {
      if (this.plugin.canvas3d) this.plugin.canvas3d.handleResize();
    }

    /** The view as a PNG, `width` by `height` pixels. */
    async picture(width, height) {
      const helper = this.plugin.helpers.viewportScreenshot;
      helper.behaviors.values.next({...helper.behaviors.values.value,
        resolution: {name: "custom", params: {width, height}}});
      return helper.getImageDataUri();
    }

    dispose() {
      if (this.resizing) this.resizing.disconnect();
      this.view.unmount();
      this.plugin.dispose();
    }
  }

  function absolute(url) {
    return new URL(url, window.location.href).href;
  }

  window.FastMDXViewerEngine = {create, COLOUR_THEMES, REPRESENTATIONS};
}());
