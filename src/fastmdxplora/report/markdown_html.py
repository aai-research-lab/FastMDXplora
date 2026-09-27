"""Markdown as HTML that carries no markup of its own.

The report page, the Files preview and the PDF all render Markdown, and the
text is not all FastMDXplora's: a report quotes the names a structure file
and a config give, and the preview renders any `.md` in a study. Markdown
passes raw HTML through and takes any link, so a residue, ligand or study
name, or a file somebody put in a study, could carry a script into the page
or have the PDF renderer fetch a local file. Here raw HTML is kept as the
text it was written as, and a link or image goes only to the web, to mail,
or somewhere relative to the document.
"""

from __future__ import annotations

import html
import re
from typing import Any

#: What a link or an image may point at, besides a place relative to the
#: document or an anchor in it.
SCHEMES = frozenset({"http", "https", "mailto"})

_SCHEME = re.compile(r"^([a-z][a-z0-9+.\-]*):")
# Browsers drop these from a URL before they read its scheme, so
# "java\tscript:" is `javascript:` to them and must be to this check.
_IGNORED_IN_A_URL = re.compile(r"[\x00-\x20\x7f]")


def is_safe_url(url: str) -> bool:
    """Whether a link or image address stays on the web, in mail, or
    relative to the document. Entities are read as a browser reads them."""
    plain = _IGNORED_IN_A_URL.sub("", html.unescape(str(url))).lower()
    found = _SCHEME.match(plain)
    return found is None or found.group(1) in SCHEMES


def markdown_as_html(text: str) -> str:
    """Tables, fenced code and heading anchors, as the report was always
    rendered, with raw HTML shown as written and unsafe addresses dropped.

    Raises ImportError where the `markdown` library is not installed; the
    callers each say what that means for them.
    """
    import markdown
    from markdown.extensions import Extension
    from markdown.treeprocessors import Treeprocessor

    class _DropUnsafeAddresses(Treeprocessor):
        def run(self, root: Any) -> None:
            for element in root.iter():
                for attribute in ("href", "src"):
                    value = element.get(attribute)
                    if value is not None and not is_safe_url(value):
                        del element.attrib[attribute]

    class _Inert(Extension):
        def extendMarkdown(self, md: Any) -> None:  # noqa: N802 - library API
            md.preprocessors.deregister("html_block")
            md.inlinePatterns.deregister("html")
            md.treeprocessors.register(_DropUnsafeAddresses(md), "drop_unsafe", 0)

    return markdown.markdown(
        str(text), extensions=["tables", "fenced_code", "toc", _Inert()],
        output_format="html5")
