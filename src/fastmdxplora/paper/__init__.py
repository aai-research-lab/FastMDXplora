"""The MD studies a paper reports, read from it and written as configs.

A paper is given as a file (PDF, JATS XML, Word, text) or as the DOI of an
open-access paper, with its supporting information where the methods are
there. An AI model lists the MD studies in it, one system under one
protocol each, and reads each study's settings and the results it reports;
every value it gives must come with the words of the paper it was read
from, and those words are looked for in the paper and the number read from
them here (:mod:`.quotes`, :mod:`.values`), so nothing the paper does not
say reaches a config. Each study is then written as a config, each setting
as the paper states it, as this software would set it where the paper is
silent, or as close as this software can come where it differs, each with
the paper's words as its reason (:mod:`.mapping`); a study this software
cannot run is said so, with why. What the paper reports is kept in the
config, and the report compares it with what the study determined
(:mod:`.reproduction`).

The text is read here (:mod:`.text`) or fetched from an open-access source
(:mod:`.fetch`); the AI model is asked in :mod:`.extract`.
"""

from __future__ import annotations

from fastmdxplora.refusals import CodedError

__all__ = ["PaperRefused"]


class PaperRefused(CodedError, RuntimeError):
    """A paper that cannot be read, fetched or turned into a study."""

    default_code = "environment.paper.unreadable"
