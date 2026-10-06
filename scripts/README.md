# Scripts

Helper scripts for development, benchmarking, and release engineering.

- `make_mark.py` makes FastMDXplora's mark, a three-atom molecule going
  right with each atom's trail behind it, from its atoms, bonds and trails.
  It writes the tab's icon (`src/fastmdxplora/gui/static/fastmdx-mark.svg`),
  prints its shapes for `ICONS["mark"]` in
  `src/fastmdxplora/gui/sidebar_icons.py`, checks that both match it with
  `--check`, and writes a page of the mark at each size in both schemes
  with `--preview FILE`.
