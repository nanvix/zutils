# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Sphinx configuration for nanvix-zutil documentation."""

import importlib.metadata

project = "nanvix-zutil"
copyright = "The Maintainers of Nanvix"
author = "The Maintainers of Nanvix"
release = importlib.metadata.version("nanvix-zutil")
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
]

# -- Autodoc settings -------------------------------------------------------
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_class_signature = "separated"

# -- Napoleon settings (Google-style docstrings) ----------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_special_with_doc = False
napoleon_use_ivar = True

# -- Intersphinx (cross-reference Python stdlib) ----------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3.12", None),
}

# -- HTML output -------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
}

# -- General -----------------------------------------------------------------
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
templates_path = []
html_static_path = []
