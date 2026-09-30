# Membrane proteins

A membrane protein simulated in water is not the protein: the hydrophobic belt
that sits in the bilayer is exposed to solvent and the helices splay. So it is
built as a membrane system, in a lipid bilayer OpenMM packs around it, with no
external packing tool.

```yaml
systems:
  - system: 1AFO
setup:
  membrane: POPC
  membrane_orient: true
simulation:
  temperature_K: 310
```

or on the command line:

```bash
fastmdx explore --system 1AFO \
  --setup-membrane POPC --setup-membrane-orient \
  --simulate-temperature-K 310 \
  --output runs/glycophorin
```

That is a membrane protein from a PDB identifier to a finished report. What
happens in between, and what to check afterwards, is below.

---

## Choosing the lipid, and the temperature

OpenMM builds a bilayer of one of seven lipids.

| Lipid | Chains | Main transition | Use |
|---|---|---|---|
| `POPC` | 16:0 / 18:1 | 271 K (−2 °C) | The default for most work; fluid at any usual temperature |
| `DOPC` | 18:1 / 18:1 | about 256 K (−17 °C) | Fluid, slightly thicker than POPC |
| `DLPC` | 12:0 / 12:0 | 271 K (−2 °C) | A thin bilayer |
| `DMPC` | 14:0 / 14:0 | 297 K (24 °C) | Thin; fluid only above about 303 K |
| `POPE` | 16:0 / 18:1 | 298 K (25 °C) | Bacterial inner membranes; fluid above about 304 K |
| `DLPE` | 12:0 / 12:0 | 303 K (30 °C) | Thin, ethanolamine head group |
| `DPPC` | 16:0 / 16:0 | 314 K (41 °C) | The classic model bilayer; fluid only above about 320 K |

Transition temperatures are those of Koynova and Caffrey (1998). **Below its
main transition a bilayer's equilibrium is the gel phase**, which a simulation
reaches, if at all, over far longer than a run, and which force fields
reproduce less well than the fluid. The area per lipid and thickness then
describe neither phase. DPPC at the default 300 K is the case. FastMDXplora
says so before the run starts, and says so again within 6 K above the
transition, where the bilayer is fluid but its area changes steeply with
temperature.

OpenMM's DPPC patch is packed at 0.50 nm² per lipid and 4.3 nm thick,
closer to the gel than to the fluid at 50 °C (0.631 nm²; Kučerka, Nieh and
Katsaras 2011). Above 41 °C it expands during equilibration, and the
`area_per_lipid` series shows it doing so.

---

## Placing the protein

OpenMM builds the bilayer in the xy plane and takes the protein's frame as it
is. A structure straight from the PDB is in no such frame: crystallographic
axes have no relation to a membrane normal, and 1AFO's NMR frame has its
helices lying in the plane the membrane is about to occupy. Embedding it
anyway packs lipids around a protein lying flat in them, the run completes,
and every number describes a structure nobody would recognise.

So the protein is placed first, and the bilayer is built at z = 0 around it.
Which frame is used follows the settings:

| The study gives | The protein is placed |
|---|---|
| An **OPM file** | In OPM's frame, as published. Recognised by its membrane markers, which are removed before the build; nothing to set |
| `membrane_orient: true` | Rotated onto the fitted membrane normal and centred on the fitted bilayer |
| `membrane_orientation_checked: true` | In its own orientation, with the centre fitted along z |
| `membrane_orientation_checked: true` and `membrane_center_z_nm` | In its own orientation, with the centre where you say |
| None of these | In its own orientation if that is already within 20° of the fitted normal; otherwise refused, with the tilt stated |

How it was placed is recorded in the setup manifest under `bilayer`
(`placed_by`, the fitted hydrophobic thickness, the tilt of the input, the
rotation and the shift), and the methods paragraph states it.

### The fit

The membrane normal, centre and hydrophobic thickness are fitted from where
the protein's **lipid-facing** surface is apolar, in the manner of OPM's PPM
method: the slab that buries the most apolar surface, less the polar and
charged surface it would bury too, is where the bilayer's core goes. Only the
surface a lipid can reach counts, so the charged lining of a porin's pore
does not pull the slab away from its outer belt.

It was checked against OPM's orientations for 65 membrane proteins, from
three and two random starting frames each (170 fits): helical bundles, GPCRs
with a fusion partner or a G protein, whose longest axis is not the normal,
β-barrels, and trimeric porins. The fitted normal came within 21° of OPM's
every time, within 15° for all but two fits, and within 3 to 4° at the median.
The centre was within 0.1 nm of OPM's at the median, and the thickness 0.2 nm
thicker. Twenty-five of the proteins were held out of every choice made in
building the fit.

The fit cannot tell which way **up** a protein sits: a bilayer of one lipid is
the same on both sides, so it does not matter to the build. It matters for an
asymmetric membrane, which OpenMM does not build.

### What is refused

- **A structure that is not a membrane protein** (`setup.membrane.no_belt`).
  Every one of the 65 membrane proteins buries at least 18.5 nm² of net apolar
  surface in its best slab, and no soluble protein reached more than 5.4.
  Below 10 the structure is refused. A membrane protein whose transmembrane
  part is missing from the model reads this way too, so check `chains`.
- **A structure on its side** (`setup.membrane.orientation_unchecked`), when
  neither setting says what to do with it. The message gives the tilt and the
  three ways on.
- **Copies of one chain that would not have the same side up.** In one
  membrane the symmetry relating two copies turns the normal onto itself, as
  a porin trimer's three-fold does. Two copies from a crystal's asymmetric
  unit, packed head to tail, are related by a two-fold across it instead, and
  one of them would be embedded upside down. The message names the chains
  and the angle; simulate one copy with `chains`.

---

## The build

`addMembrane` tiles a pre-equilibrated patch of about 6 nm, so the box is a
whole number of patches across and rectangular (`box_shape` does not apply).
Lipids overlapping the protein are removed equally from both leaflets, water
and ions are added above and below, and a short relaxation lets the lipids
close around the protein. The setup manifest records how many lipids were
built, how many in each leaflet, the box, and its area per lipid before the
protein's share is taken out.

### The barostat

An ordinary barostat scales x, y and z together, which squeezes a bilayer
that should be free to change thickness independently of its area. The run
completes and is wrong. FastMDXplora uses `MonteCarloMembraneBarostat` with x
and y coupled, z free, and no surface tension, chosen from the topology rather
than from a setting, and recorded in the simulation manifest. A system is a
bilayer when it holds at least 20 lipids: a residue name alone is not enough,
because `POP` is also pyrophosphate in the PDB, and an enzyme with one bound
is not a membrane.

### Equilibration

A bilayer's area per lipid relaxes over nanoseconds at constant pressure,
where water's density takes picoseconds, and the lipids have just been packed
around a protein. Give NPT a few nanoseconds (`npt_duration_ns`), with the
protein restrained (`restrain: "protein and not element H"`), and read the
`area_per_lipid` series before trusting anything after it. FastMDXplora warns
when NPT is shorter than 1 ns.

---

## Checking the bilayer

Three analyses characterise the bilayer itself, and run automatically wherever
there is one. Each has an experimental value and each moves when a force
field, a temperature or a barostat is wrong, so they are what a membrane run
is checked against before anything about the protein in it is believed.

| Analysis | What it computes |
|---|---|
| `area_per_lipid` | The box's area in xy, less the protein's cross section in the hydrophobic core, per lipid of one leaflet. nm², per frame |
| `bilayer_thickness` | The distance between the two leaflets' phosphate planes, D_PP. nm, per frame |
| `lipid_order` | The deuterium order parameter S_CD of every acyl-chain carbon, by chain. −S_CD is plotted |

Both per-frame quantities carry the mean after equilibration, its error, and the
number of independent samples behind it, as every time series does.

**The bilayer centre is found across the periodic boundary**, as the middle of
the lipid slab, and each head is assigned to a leaflet by which side of it it
is on, every frame. Lipids whose heads do not form two layers normal to z are
refused rather than analysed along the wrong axis.

**The protein's cross section** is the area inside the outline a methylene
group traces round the protein's van der Waals discs, in five planes across
the hydrophobic core, averaged. Every protein correction to an area per lipid
is a convention, because lipids next to a protein do not pack as those in bulk
do; the findings give the protein's share of the box, so it is clear how much
the value depends on it.

**The chains are found from the bonds**, not from atom names, so any force
field's naming works: a chain starts at a carbonyl bonded to an ester oxygen,
is sn-2 when the glycerol carbon it is attached to carries one hydrogen and
sn-1 when it carries two, and runs to its end. S_CD is averaged over each
lipid's hydrogens and frames, then over lipids, with the standard error taken
across lipids. It needs the hydrogens.

### What to compare with

Compare at the temperature of the measurement; area and thickness both change
by several percent over ten degrees. Kučerka, Nieh and Katsaras (2011) give
areas and thicknesses for the phosphatidylcholines as a function of
temperature from joint X-ray and neutron scattering: 0.643 nm² for POPC at
30 °C, and 0.631 nm² for DPPC at 50 °C, for example. D_PP from a simulation
is close to the head-to-head thickness D_HH that X-ray scattering reports,
and not the same quantity.

---

## What is not built

- **Mixtures, cholesterol and asymmetric bilayers.** OpenMM builds one lipid.
  A mixed or asymmetric membrane built elsewhere (CHARMM-GUI, for instance)
  can still be analysed: the three bilayer analyses read CHARMM36's lipid
  names and AMBER Lipid21's, which makes each chain a residue of its own, and
  count a sterol as a lipid.
- **Four-site water.** OpenMM's patches carry three-site water, and AMBER
  Lipid17 and CHARMM36 lipids were developed with TIP3P; a force field given
  with TIP4P-Ew or OPC is refused with a membrane rather than failing inside
  the packing.
- **A peripheral protein resting on a bilayer.** The fit looks for a
  transmembrane belt, and a protein with none is refused rather than pushed
  into the membrane.

---

## References

- Lomize, Pogozheva, Joo, Mosberg and Lomize. OPM database and PPM web server.
  *Nucleic Acids Res.* 40, D370 (2012).
- Wolf, Hoefling, Aponte-Santamaría, Grubmüller and Groenhof. g_membed.
  *J. Comput. Chem.* 31, 2169 (2010). The method `addMembrane` follows.
- Koynova and Caffrey. Phases and phase transitions of the
  phosphatidylcholines. *Biochim. Biophys. Acta* 1376, 91 (1998).
- Kučerka, Nieh and Katsaras. Fluid phase lipid areas and bilayer thicknesses
  of commonly used phosphatidylcholines as a function of temperature.
  *Biochim. Biophys. Acta* 1808, 2761 (2011).
