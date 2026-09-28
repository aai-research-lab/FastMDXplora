"""`python -m fastmdxplora`: the `fastmdx` command, from this interpreter.

How the GUI starts a study, so the run uses the Python the GUI runs in.
`python -m fastmdxplora.cli.main` does the same and warns: the CLI package
imports `main` from that module, so runpy finds it already imported and runs
it a second time as `__main__`, with every class in it defined twice.
"""

import sys

from fastmdxplora.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
