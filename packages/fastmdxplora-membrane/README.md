# fastmdxplora-membrane

The lipid bilayer analyses of [FastMDXplora](https://github.com/aai-research-lab/FastMDXplora):

| Analysis | What it measures |
|---|---|
| `area_per_lipid` | The area each lipid takes in the plane of the bilayer |
| `bilayer_thickness` | The phosphate-to-phosphate thickness, from the density profile |
| `lipid_order` | The carbon-deuterium order parameter down each acyl chain |

It is installed with FastMDXplora, so there is usually nothing to do:

    pip install fastmdxplora

FastMDXplora finds the analyses through this package's entry points and runs
them on any study whose frames hold a bilayer, exactly as before they lived
here. A study's YAML, the `fastmdx` commands and the results are unchanged.

To install it on its own, beside a FastMDXplora installed without it:

    pip install fastmdxplora-membrane

The modules are `fastmdxplora_membrane.bilayer`, `.area_per_lipid`,
`.bilayer_thickness` and `.lipid_order`. Their old paths
(`fastmdxplora.analysis.lipid_order` and the rest) still import.

See the [membrane guide](https://fastmdxplora.readthedocs.io/en/stable/membranes.html).
