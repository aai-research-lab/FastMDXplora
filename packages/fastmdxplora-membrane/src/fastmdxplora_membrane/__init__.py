"""The analyses of a lipid bilayer, for FastMDXplora.

`area_per_lipid`, `bilayer_thickness` and `lipid_order`, and what the three
share (:mod:`fastmdxplora_membrane.bilayer`). FastMDXplora finds them by this
package's entry points (``fastmdxplora.analyses``) and registers them among
its own, so a study names and reads them as it always has; importing this
package registers nothing.
"""

from __future__ import annotations

# FastMDXplora's analyses first, whatever of this package is imported first:
# its analysis package reads this one's entry points as it is imported, and
# the modules here import from it.
import fastmdxplora.analysis  # noqa: F401

__all__ = ["area_per_lipid", "bilayer", "bilayer_thickness", "lipid_order"]
