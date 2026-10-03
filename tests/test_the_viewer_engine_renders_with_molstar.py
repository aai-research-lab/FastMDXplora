"""The viewer's engine: Mol* behind one adapter (static/viewer-engine.js).

The page is to talk to the engine and never to Mol* itself. These are the
engine's promises, each one checked against what the server says: the
trajectory sent as binary frames is rendered frame by frame; the cartoon is
the study's DSSP in every frame, where Mol*'s own pairs no strands between
chains; atoms are named by the index MDTraj gives them in the same
topology; a study's per-residue result is a colour theme; and a picture is
the size asked for.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.request
from pathlib import Path

import mdtraj as md
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study  # noqa: E402

SETUP = """async () => {
    const holder = document.createElement('div');
    holder.id = 'engine-under-test';
    holder.style.cssText = 'position:fixed;left:0;top:0;width:800px;height:600px;z-index:9999';
    document.body.appendChild(holder);
    window.engine = await FastMDXViewerEngine.create(holder, {quality: 'low'});
    return await engine.loadFrames('/structure/frames-topology.pdb', '/structure/frames.dcd');
}"""


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    return _helical_study(tmp_path_factory.mktemp("engine") / "study")


@pytest.fixture(scope="module")
def page(study):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        urllib.request.urlopen(base + "/api/frames-info").read()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            opened = browser.new_page(viewport={"width": 1200, "height": 900})
            opened.set_default_timeout(120000)
            errors: list[str] = []
            opened.on("pageerror", lambda error: errors.append(str(error)))
            outside: list[str] = []
            opened.on("request", lambda request: None if request.url.startswith(base)
                      or request.url.startswith("data:") else outside.append(request.url))
            opened.goto(base + "/#viewer", wait_until="domcontentloaded")
            opened.add_script_tag(url=base + "/static/molstar/molstar.js")
            opened.add_script_tag(url=base + "/static/viewer-engine.js")
            opened.loaded = opened.evaluate(SETUP)
            opened.errors = errors
            opened.outside = outside
            opened.base = base
            yield opened
            browser.close()
    finally:
        session.server.shutdown()


def _server(page, path: str):
    return json.loads(urllib.request.urlopen(page.base + path).read())


def test_the_frames_are_rendered(page):
    assert page.loaded == {"frames": 6, "atoms": 2192}
    assert page.errors == []
    # Mol* is the vendored bundle: nothing is fetched from anywhere else.
    assert page.outside == []


def test_the_cartoon_is_the_study_s_dssp_in_every_frame(page):
    said = _server(page, "/api/secondary-structure?of=frames")
    assert page.evaluate("(said) => engine.setSecondaryStructure(said)", said) is True
    for frame in (0, 5):
        page.evaluate(f"() => engine.setFrame({frame})")
        shown = page.evaluate("() => engine.secondaryStructureShown()")
        assert shown["A"] + shown["B"] == said["frames"][frame]
    # And it is the study's that is rendered, not Mol*'s: an assignment given is the
    # assignment shown.
    altered = dict(said, frames=["E" * len(codes) for codes in said["frames"]])
    page.evaluate("(said) => engine.setSecondaryStructure(said)", altered)
    assert set(page.evaluate("() => engine.secondaryStructureShown()")["B"]) == {"E"}
    page.evaluate("(said) => engine.setSecondaryStructure(said)", said)


def test_strands_paired_across_chains_are_rendered_as_strands(page):
    """A fibril has each strand in a chain of its own. Mol*'s DSSP, chain by
    chain, finds no strand in it; the study's finds them, as MDTraj does."""
    import gzip

    from fastmdxplora.gui.by_residue import _dssp

    raw = gzip.decompress((Path(__file__).parent / "data" / "assemblies" / "1STP.pdb.gz")
                          .read_bytes()).decode()
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "1stp.pdb"
        source.write_text(raw, encoding="utf-8")
        chain = md.load_pdb(str(source))
        chain = chain.atom_slice(chain.topology.select("protein and chainid 0"))
        one = Path(folder) / "one.pdb"
        chain.save_pdb(str(one))
        lines = [line for line in one.read_text(encoding="utf-8").splitlines()
                 if line.startswith("ATOM")]
    codes = _dssp("\n".join(lines) + "\nEND\n")["frames"][0]
    numbers = []
    for line in lines:
        if not numbers or numbers[-1] != int(line[22:26]):
            numbers.append(int(line[22:26]))
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    chain_of, strand, previous = {}, 0, "C"
    for number, code in zip(numbers, codes):
        if code == "E" and previous != "E":
            strand += 1
        chain_of[number] = letters[strand % 26]
        previous = code
    strands = "\n".join(line[:21] + chain_of[int(line[22:26])] + line[22:]
                         for line in lines) + "\nEND\n"
    study = _dssp(strands)
    assert study["available"] and study["frames"][0].count("E") > 40
    page.evaluate("() => engine.setSecondaryStructure(null)")
    page.evaluate("(text) => engine.loadStructure({text})", strands)
    own = "".join(page.evaluate("() => engine.secondaryStructureShown()").values())
    assert own.count("E") == 0
    page.evaluate("(said) => engine.setSecondaryStructure(said)", study)
    shown = "".join(page.evaluate("() => engine.secondaryStructureShown()").values())
    assert shown.count("E") == study["frames"][0].count("E")
    page.evaluate("() => engine.loadFrames('/structure/frames-topology.pdb', '/structure/frames.dcd')")


def test_atoms_are_named_by_mdtraj_s_index(page, study):
    topology = md.load_topology(str(study / "simulation" / "frames_topology.pdb"))
    picked = [int(i) for i in topology.select("resSeq 40 to 60 and name CA")]
    assert len(picked) == 42
    page.evaluate("() => engine.setFrame(0)")
    found = page.evaluate(f"() => engine.atoms({picked})")
    assert [atom["index"] for atom in found] == picked
    assert {atom["atom"] for atom in found} == {"CA"}
    assert {(atom["chain"], atom["resi"]) for atom in found} == {
        (topology.atom(i).residue.chain.chain_id, topology.atom(i).residue.resSeq)
        for i in picked}
    frames = md.load_dcd(str(study / "simulation" / "frames.dcd"), top=topology)
    first = picked[0]
    page.evaluate("() => engine.setFrame(5)")
    moved = page.evaluate(f"() => engine.atoms([{first}])[0]")
    assert moved["x"] == pytest.approx(10 * frames.xyz[5, first, 0], abs=1e-3)


def test_a_result_is_a_colour_theme(page, study):
    values = _server(page, "/api/residue-values")["properties"]
    rmsf = next(p for p in values if p["key"] == "rmsf")
    page.evaluate("(p) => engine.setColour('result', p)", rmsf)
    topology = md.load_topology(str(study / "simulation" / "frames_topology.pdb"))
    a = int(topology.select("chainid 0 and resSeq 60 and name CA")[0])
    b = int(topology.select("chainid 1 and resSeq 60 and name CA")[0])
    red = page.evaluate(f"() => engine.resultColourOf({a})")
    blue = page.evaluate(f"() => engine.resultColourOf({b})")
    assert (red >> 16) > (red & 0xFF) and (blue & 0xFF) > (blue >> 16)
    assert page.evaluate("() => engine.themeName()") == "fastmdx-result-rmsf"
    page.evaluate("() => engine.setColour('chain')")
    assert page.evaluate("() => engine.themeName()") == "chain-id"


def test_a_representation_is_changed(page):
    page.evaluate("() => engine.setRepresentation('sticks')")
    kinds = page.evaluate("""() => engine.plugin.managers.structure.hierarchy.current.structures[0]
        .components.flatMap((c) => c.representations.map((r) => r.cell.transform.params.type.name))""")
    assert "ball-and-stick" in kinds and "cartoon" not in kinds
    page.evaluate("() => engine.setRepresentation('cartoon')")


def test_a_picture_is_the_size_asked_for(page):
    from PIL import Image

    uri = page.evaluate("() => engine.picture(2400, 1800)")
    image = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert image.size == (2400, 1800)


def test_a_click_names_the_atom(page, study):
    topology = md.load_topology(str(study / "simulation" / "frames_topology.pdb"))
    index = int(topology.select("chainid 1 and resSeq 77 and name CA")[0])
    clicked = page.evaluate(f"""() => new Promise((resolve) => {{
        engine.on('click', resolve);
        engine.plugin.behaviors.interaction.click.next({{current: {{loci: engine.lociOf([{index}])}},
            buttons: 0, button: 0, modifiers: {{}}}});
    }})""")
    assert clicked["index"] == index and clicked["resi"] == 77 and clicked["chain"] == "B"


#: Alanine dipeptide's heavy atoms, ACE and NME as OpenMM writes them.
DIPEPTIDE = """\
HETATM    1  CH3 ACE A   1       2.000   2.090   0.000  1.00  0.00           C
HETATM    2  C   ACE A   1       3.427   2.641  -0.000  1.00  0.00           C
HETATM    3  O   ACE A   1       4.391   1.877  -0.000  1.00  0.00           O
ATOM      4  N   ALA A   2       3.555   3.970  -0.000  1.00  0.00           N
ATOM      5  CA  ALA A   2       4.853   4.614  -0.000  1.00  0.00           C
ATOM      6  CB  ALA A   2       5.661   4.221  -1.232  1.00  0.00           C
ATOM      7  C   ALA A   2       4.713   6.129   0.000  1.00  0.00           C
ATOM      8  O   ALA A   2       3.601   6.653   0.000  1.00  0.00           O
HETATM    9  N   NME A   3       5.846   6.835   0.000  1.00  0.00           N
HETATM   10  C   NME A   3       5.846   8.284   0.000  1.00  0.00           C
END
"""


def test_a_capped_peptide_is_rendered_whole(page):
    """Mol* counts ACE and NME as no part of the chain: rendered with the
    protein, in one with it, so the bonds to them are rendered."""
    page.evaluate("(text) => engine.loadStructure({text})", DIPEPTIDE)
    rendered = page.evaluate("""() => ({parts: engine.rendered,
        atoms: engine.components.polymer.cell.obj.data.elementCount})""")
    assert rendered == {"parts": ["polymer", "short"], "atoms": 10}
    page.evaluate("() => engine.loadFrames('/structure/frames-topology.pdb', '/structure/frames.dcd')")


def test_by_element_is_by_element_throughout(page):
    page.evaluate("() => engine.setColour('element')")
    props = page.evaluate("() => engine.colourProps()")
    assert props["color"] == "element-symbol"
    assert props["colorParams"]["carbonColor"]["name"] == "element-symbol"
    page.evaluate("() => engine.setColour('chain')")


def test_spinning_starts_and_stops(page):
    page.evaluate("() => engine.spin(true)")
    page.wait_for_timeout(600)
    page.evaluate("() => engine.spin(false)")
    assert page.errors == []


def test_a_click_with_the_mouse_moves_nothing(page):
    """A click is the page's: Mol*'s own flew the camera to the residue and
    rendered what was around it, at every atom picked to measure."""
    page.evaluate("() => engine.setFrame(0)")
    holder = page.locator("#engine-under-test").bounding_box()
    page.evaluate("() => { window.clicks = []; engine.on('click', (atom) => clicks.push(atom)); }")
    # Where the camera looks from and at: the depth Mol* clips at is its own
    # to recompute as it renders.
    view = """() => { const c = engine.cameraSnapshot();
        return [JSON.stringify([c.position, c.target, c.up, c.radius]),
                engine.plugin.state.data.cells.size]; }"""
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => "
                  "requestAnimationFrame(done)))")
    before = page.evaluate(view)
    for fx in (0.5, 0.45, 0.55, 0.4, 0.6, 0.35, 0.65):
        for fy in (0.5, 0.4, 0.6):
            x, y = holder["x"] + holder["width"] * fx, holder["y"] + holder["height"] * fy
            page.mouse.move(x, y)
            page.wait_for_timeout(300)
            page.mouse.click(x, y)
            page.wait_for_timeout(500)
            if any(page.evaluate("() => clicks")):
                break
        else:
            continue
        break
    assert any(page.evaluate("() => clicks")), "no atom was found under the mouse"
    after = page.evaluate(view)
    assert after == before
