"""Sphinx configuration for FastMDXplora documentation."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

project = "FastMDXplora"
author = "Adekunle Aina, Derrick Kwan"
copyright = "2026, AAI Research Lab"

try:
    from fastmdxplora import __version__ as release
except ImportError:
    release = "2.0.0"
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

# MyST: allow ```{eval-rst} blocks and common extensions.
myst_enable_extensions = [
    "colon_fence",
    "deflist",
]

# Generate anchors for headings so pages can link to their own sections.
myst_heading_anchors = 3

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_mock_imports = [
    "openmm",
    "openmmforcefields",
    "openff",
    "pdbfixer",
    "openmmplumed",
]

napoleon_google_docstring = True
napoleon_numpy_docstring = True

# Seventeen analysis docstrings end in an "Output" section saying which
# files the analysis writes. Napoleon does not know that name, so it never
# closes the Parameters section: every line of Output was parsed as another
# parameter, split on its commas, and rendered as a list of invented
# arguments with the real text destroyed. Registering it renders it as a
# titled block, which is what it reads as in the source.
napoleon_custom_sections = ["Output"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "mdtraj": ("https://www.mdtraj.org/1.9.8.dev0/", None),
}

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

master_doc = "index"
exclude_patterns = ["_build", "design"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

html_theme_options = {
    "style_external_links": True,
    "collapse_navigation": False,
    "navigation_depth": 2,
}

# Puts a "View page source" / "Edit on GitHub" link in the header of every
# page, so the repository is one click away from anywhere in the docs.
html_context = {
    "display_github": True,
    "github_user": "aai-research-lab",
    "github_repo": "FastMDXplora",
    "github_version": "main",
    "conf_py_path": "/docs/",
}
