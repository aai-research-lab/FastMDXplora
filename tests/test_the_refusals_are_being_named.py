"""How far the naming has got, and that it does not go backwards.

Three hundred and fifty-eight places raise. Coding them is mechanical and
it is not quick, and a migration with no instrument attached is a migration
that stalls silently and gets described as finished.

So this counts. It reads the source, finds every raise site, and reports
what fraction carries a code. The number is asserted against a floor that
only ever moves up, which is the smallest mechanism that makes the
direction a property of the repository rather than of whoever remembers.

This is the same instinct as :mod:`fastmdxplora.validation.corpus`: state
the rate rather than assert a binary, because the rate is the thing that
can be argued with.

Deliberately *not* asserted: that every raise site is coded. An assertion
that fails for a year teaches people to skip the file it lives in.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import fastmdxplora
from fastmdxplora.refusals import known

PACKAGE = pathlib.Path(fastmdxplora.__file__).parent

#: Raised at a boundary where there is no study to refuse -- a programming
#: error in this package, or a control-flow signal. Coding them would say
#: something false: that a user could have caused it.
NOT_REFUSALS = frozenset({
    "AssertionError", "NotImplementedError", "SystemExit",
    "KeyboardInterrupt", "StopIteration", "AttributeError",
    # A protocol exception, not a refusal: it is what Popen.wait raises,
    # and the adopted-process handle raises the same so the runtime's
    # stop() can catch it by name whichever kind of process it holds.
    "TimeoutExpired",
})

#: Helpers that carry an inner refusal's code out to an outer raise. A site
#: that calls one is coded by delegation.
FORWARDERS = frozenset({"_rewrapped"})

#: Helpers that build the exception rather than being raised themselves:
#: `raise _validation_error(stage, detail)`. The code lives in the builder,
#: so eleven sites share one, which is better than eleven copies -- but it
#: means this finder cannot see it at the raise, and counting them as
#: uncoded understates the work by thirteen sites. Named explicitly rather
#: than accepting any call, so an actual uncoded helper cannot hide here.
BUILDERS = frozenset({"_validation_error", "_explain_unparameterized"})

#: Sites allowed to raise without saying which refusal they are. None.
#:
#: This was a fraction while the migration ran, which was the right shape
#: for measuring progress and the wrong one for holding a finished job. A
#: floor of 0.99 let exactly what it was meant to prevent through: one new
#: raise site, added in an unrelated change, saying nothing about itself
#: and passing. The number it protected was not the point -- every refusal
#: being named was.
#:
#: So the assertion is the property rather than a proxy for it. A new raise
#: site fails this until it is given a code, which is a minute of work and
#: the whole of what this branch was for.
UNCODED_SITES_ALLOWED = 0


def _raise_sites() -> list[tuple[pathlib.Path, int, str, bool]]:
    """Every `raise SomeError(...)` in the package, and whether it is coded.

    A site counts as coded when its call passes `code=` with a literal the
    registry knows, or when it raises a class whose `default_code` is set.
    The second is read from the live classes rather than from the source,
    so a class that gains a default does not need every site revisited.
    """
    from fastmdxplora import refusals as _r

    defaults: dict[str, str] = {}
    for module in _iter_modules():
        for name in dir(module):
            obj = getattr(module, name, None)
            if (isinstance(obj, type) and issubclass(obj, _r.CodedError)
                    and getattr(obj, "default_code", "unclassified")
                    != "unclassified"):
                defaults[name] = obj.default_code

    sites: list[tuple[pathlib.Path, int, str, bool]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "id", None) or getattr(
                call.func, "attr", None)
            if name is None or name in NOT_REFUSALS:
                continue
            coded = name in defaults or name in BUILDERS
            for kw in call.keywords:
                if kw.arg == "code":
                    # A literal is checked against the registry. A computed
                    # one -- `code=verdict.code` -- cannot be, and counting
                    # it as uncoded would say a site that passes a code
                    # does not. The literals are what
                    # test_every_code_passed_at_a_raise_site_is_registered
                    # holds; this only decides whether a code was passed.
                    coded = coded or (
                        known(str(kw.value.value))
                        if isinstance(kw.value, ast.Constant) else True)
                # `raise ConfigError(str(exc), **_rewrapped(exc))` carries
                # the inner refusal's code outward. The literal is at the
                # inner raise site, not here, so a forwarding helper counts
                # as coded. Named explicitly rather than accepting any `**`,
                # which would let an ordinary dict of details look like a
                # code and quietly inflate this number.
                if kw.arg is None and isinstance(kw.value, ast.Call):
                    forwarder = getattr(kw.value.func, "id", None) or getattr(
                        kw.value.func, "attr", None)
                    coded = coded or forwarder in FORWARDERS
            sites.append((path.relative_to(PACKAGE), node.lineno, name, coded))
    return sites


def _iter_modules():
    import importlib
    import pkgutil
    yield fastmdxplora
    for info in pkgutil.walk_packages(
            fastmdxplora.__path__, prefix="fastmdxplora."):
        try:
            yield importlib.import_module(info.name)
        except Exception:  # pragma: no cover - optional backends
            continue


class TestTheMigrationHasAnInstrument(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sites = _raise_sites()

    def test_there_are_raise_sites_to_count(self):
        # If this ever reads zero, the finder has broken and every other
        # assertion here is passing vacuously.
        self.assertGreater(len(self.sites), 200)

    def test_every_refusal_says_which_refusal_it_is(self):
        uncoded = [f"{path}:{line} {name}"
                   for path, line, name, ok in self.sites if not ok]
        self.assertLessEqual(
            len(uncoded), UNCODED_SITES_ALLOWED,
            "these raise without saying which refusal they are:\n  "
            + "\n  ".join(uncoded)
            + "\n\nAdd `code=\"...\"` from fastmdxplora.refusals, or give "
              "the exception class a `default_code` if every one of its "
              "sites means the same thing. If none of the registered codes "
              "fits, add one -- the taxonomy is meant to grow, and a code "
              "invented for a real condition is worth more than one "
              "guessed at in advance.",
        )

    def test_the_config_loader_is_finished(self):
        # The first thing any caller meets, and the one place where being
        # partially done would be worse than not having started: an agent
        # that gets a code for three of four config mistakes cannot tell
        # the fourth from a crash.
        # as_posix, so this finds the file on Windows too, where str()
        # on a relative Path gives "config\\loader.py".
        loader = [s for s in self.sites
                  if s[0].as_posix() == "config/loader.py"]
        self.assertTrue(loader)
        uncoded = [(str(p), ln, name) for p, ln, name, ok in loader if not ok]
        self.assertEqual(uncoded, [], f"uncoded refusals in the loader: {uncoded}")

    def test_every_code_passed_at_a_raise_site_is_registered(self):
        # A typo in a `code=` argument degrades to `unclassified` at
        # runtime, by design, so a study never fails strangely over it.
        # This is what makes sure the degradation is never silently
        # relied on.
        import re
        pattern = re.compile(r'code\s*=\s*["\']([a-z0-9_.]+)["\']')
        bad: list[str] = []
        for path in sorted(PACKAGE.rglob("*.py")):
            if path.name == "refusals.py":
                continue
            for n, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                for match in pattern.finditer(line):
                    if not known(match.group(1)):
                        bad.append(f"{path.name}:{n} {match.group(1)}")
        self.assertEqual(bad, [], f"unregistered codes at raise sites: {bad}")

    def test_the_report_reads(self):
        # Printed by `-s`, so somebody picking the migration back up can
        # see where the remaining work is without writing a script.
        import collections
        by_dir = collections.Counter()
        coded_by_dir = collections.Counter()
        for path, _, _, ok in self.sites:
            key = str(path.parent) if str(path.parent) != "." else "(top)"
            by_dir[key] += 1
            coded_by_dir[key] += int(ok)
        print("\n  refusals named, by module")
        for key, total in by_dir.most_common():
            print(f"    {coded_by_dir[key]:3d}/{total:3d}  {key}")
        total = len(self.sites)
        coded = sum(coded_by_dir.values())
        print(f"    {coded:3d}/{total:3d}  TOTAL ({coded / total:.1%})")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
