"""Numbers with their units, read from a paper's own words.

An AI model gives a value and the words it read it from; the value used is
the one read here from those words, in this software's unit. A value the
words do not hold (an AI model's arithmetic, a number from elsewhere, a unit
misread) is not used: "3 x 100 ns" holds 100 ns, and a total of 300 ns is
not in it.

Each kind of quantity has its unit here (:data:`CANONICAL`): kelvin, bar,
nanoseconds of simulated time, femtoseconds of timestep, nanometres, molar,
atomic mass units, a count. A claim's numbers (:func:`numbers_in`) are
taken as written, with whatever unit the paper gives them.
"""

from __future__ import annotations

import math
import re
import unicodedata

__all__ = ["CANONICAL", "candidates", "read_value", "numbers_in", "to_canonical",
           "plain", "WORD_NUMBERS"]

#: The unit each kind of quantity is kept in.
CANONICAL = {
    "temperature": "K",
    "pressure": "bar",
    "time": "ns",
    "timestep": "fs",
    "length": "nm",
    "concentration": "M",
    "mass": "amu",
    "count": "",
}

#: Each unit as written (reduced by :func:`plain`, lowercase), and the
#: factor to the kind's unit. Temperature in Celsius is an offset, handled
#: on its own.
_UNITS: dict[str, dict[str, float]] = {
    "temperature": {"k": 1.0, "kelvin": 1.0},
    "pressure": {"bar": 1.0, "bars": 1.0, "atm": 1.01325, "atmosphere": 1.01325,
                 "atmospheres": 1.01325, "pa": 1e-5, "kpa": 1e-2, "mpa": 10.0,
                 "mbar": 1e-3},
    "time": {"fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6,
             "femtosecond": 1e-6, "femtoseconds": 1e-6, "picosecond": 1e-3,
             "picoseconds": 1e-3, "nanosecond": 1.0, "nanoseconds": 1.0,
             "microsecond": 1e3, "microseconds": 1e3, "millisecond": 1e6,
             "milliseconds": 1e6},
    "length": {"a": 0.1, "angstrom": 0.1, "angstroms": 0.1, "nm": 1.0,
               "nanometer": 1.0, "nanometers": 1.0, "nanometre": 1.0,
               "nanometres": 1.0, "pm": 1e-3},
    "concentration": {"m": 1.0, "mm": 1e-3, "um": 1e-6, "mol/l": 1.0,
                      "moll-1": 1.0, "mol l-1": 1.0, "molar": 1.0,
                      "millimolar": 1e-3, "mmol/l": 1e-3},
    "mass": {"amu": 1.0, "da": 1.0, "u": 1.0, "g/mol": 1.0, "dalton": 1.0,
             "daltons": 1.0},
}
_UNITS["timestep"] = {name: factor * 1e6 for name, factor in _UNITS["time"].items()}

#: Number words, as papers write small counts.
WORD_NUMBERS = {
    "one": 1, "single": 1, "two": 2, "duplicate": 2, "duplicates": 2, "twice": 2,
    "three": 3, "triplicate": 3, "triplicates": 3, "thrice": 3, "four": 4,
    "quadruplicate": 4, "five": 5, "quintuplicate": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
}

_NUMBER = r"(?<![\w.])(\d+(?:[.,]\d+)?)(?:\s*(?:x|\*)\s*10\s*\^\s*([-+]?\d+)|[eE]([-+]?\d+)(?![a-z]))?"


def plain(text: str) -> str:
    """``text`` with accents set aside (``Å`` is ``A``), micro signs as
    ``u``, every dash and minus as ``-``, ``×`` as ``x``, spaces single."""
    out = []
    for char in unicodedata.normalize("NFKD", str(text)):
        if unicodedata.combining(char) or (
                unicodedata.category(char) == "Sk" and char not in "^`"):
            continue
        if char in "μµ":
            char = "u"
        elif char in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d":
            char = "-"
        elif char == "×":
            char = "x"
        elif char in "   ":
            char = " "
        out.append(char)
    return re.sub(r"[ \t]+", " ", "".join(out))


def _number(digits: str, power: str | None) -> float:
    if "," in digits:
        whole, _, rest = digits.partition(",")
        # 1,000 is a thousand; 0,15 is a decimal comma.
        digits = whole + rest if len(rest) == 3 and whole != "0" else whole + "." + rest
    value = float(digits)
    if power:
        value *= 10.0 ** int(power)
    return value


def numbers_in(text: str) -> list[float]:
    """Every number written in ``text``, as written (signs kept)."""
    found = []
    for match in re.finditer(r"(-?)" + _NUMBER, plain(text)):
        value = _number(match.group(2), match.group(3) or match.group(4))
        found.append(-value if match.group(1) == "-" else value)
    return found


def to_canonical(value: float, unit: str, kind: str) -> float | None:
    """``value`` in ``unit`` as the kind's own unit, or None for a unit the
    kind does not have."""
    if kind == "count":
        return float(value)
    unit_key = re.sub(r"\s+", "", plain(unit).strip().lower()).replace("·", "")
    if not unit_key:
        return float(value)
    if kind == "temperature" and unit_key in ("c", "°c", "degc", "oc", "celsius",
                                               "degreescelsius", "ºc"):
        return round(float(value) + 273.15, 9)
    table = _UNITS.get(kind, {})
    for written, factor in table.items():
        if unit_key == re.sub(r"\s+", "", written):
            return float(value) * factor
    return None


#: What may follow each number of a list before the unit closes it: a short
#: label in brackets, as in "300.0 (POPS), 303.15 (POPC) and 323.15 K".
_LABEL = r"(?:\s*\([^()\d]{1,40}\))?"

#: Words saying no salt was added, which is a concentration of none.
_NO_SALT = re.compile(r"\b(?:no|without(?: any)?)(?: (?:added|additional|extra|further|other))?"
                      r" (?:added )?(?:salt|ions?)\b"
                      r"(?! (?:other|beyond|besides|except|apart))|\bsalt[- ]free\b")


def candidates(text: str, kind: str) -> list[float]:
    """Every quantity of ``kind`` written in ``text``, in the kind's unit.
    A number followed by a unit of the kind counts; for a count, every
    whole number and number word."""
    words = plain(text)
    lowered = words.lower()
    found: list[float] = []
    if kind == "count":
        for match in re.finditer(_NUMBER, words):
            value = _number(match.group(1), match.group(2) or match.group(3))
            if float(value).is_integer():
                found.append(value)
        for word, value in WORD_NUMBERS.items():
            if re.search(rf"\b{word}\b", lowered):
                found.append(float(value))
        return found
    table = _UNITS.get(kind, {})
    names = sorted(table, key=len, reverse=True)
    unit_pattern = "|".join(re.escape(name) for name in names)
    if kind == "temperature":
        unit_pattern = r"°\s*c|º\s*c|degc|celsius|" + unit_pattern
    pattern = re.compile(
        r"(?<![\w.])(\d+(?:[.,]\d+)?)((?:" + _LABEL + r"\s*(?:,\s*)?(?:-|to|and|or|,)\s*\d+(?:[.,]\d+)?)*)"
        r"\s*-?\s*(" + unit_pattern + r")(?![a-z])",
        re.IGNORECASE)
    if kind == "concentration" and _NO_SALT.search(lowered):
        found.append(0.0)
    for match in pattern.finditer(words):
        unit = match.group(3)
        if re.search(r"(?:fig\.?|figure|table|panel|eq\.?|ref\.?|scheme)\s*s?\d*$",
                     words[:match.start()], re.IGNORECASE):
            continue  # a label: "Fig. 2a" holds no angstroms
        listed = re.sub(r"\([^()]*\)", " ", match.group(2) or "")
        values = [match.group(1)] + re.findall(r"\d+(?:[.,]\d+)?", listed)
        for digits in values:
            value = _number(digits, None)
            canonical = to_canonical(value, unit, kind)
            if canonical is not None:
                found.append(round(canonical, 9))
    return found


def read_value(quote: str, kind: str, value: object = None,
               unit: object = None) -> float | None:
    """The value of ``kind`` the quote holds: the AI model's ``value`` (in
    ``unit``) where the quote holds it, else the quote's only value of that
    kind; None where the quote holds none, or several and the AI model's
    is not among them."""
    found = candidates(quote, kind)
    if not found:
        return None
    given = _as_float(value)
    if given is not None:
        wanted = to_canonical(given, str(unit or CANONICAL.get(kind, "")), kind)
        for candidate in found:
            if wanted is not None and _same(candidate, wanted):
                return candidate
        return None
    distinct = {round(candidate, 9) for candidate in found}
    return found[0] if len(distinct) == 1 else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        numbers = numbers_in(value)
        if len(numbers) == 1:
            return numbers[0]
        lowered = value.strip().lower()
        if lowered in WORD_NUMBERS:
            return float(WORD_NUMBERS[lowered])
    return None


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)
