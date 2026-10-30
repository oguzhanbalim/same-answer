"""Normalisation shared by the scorer and the corpus: Turkish folding and number parsing.

Everything the scorer looks at has been through ``fold`` first, and every pattern in the
corpus is folded the same way when it is loaded. So a pattern written as ``iyileştirir``
matches ``İYİLEŞTİRİR``, ``iyilestirir`` and ``iyileştirir`` alike.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Python's lower() and casefold() get Turkish wrong: "I" becomes "i" and "İ" becomes "i"
# plus a combining dot. These merges are deliberate. People type Turkish without its
# letters every day, so "asi" has to match "aşı" and "ISIK" has to match "ışık".
_TURKISH = str.maketrans({"İ": "i", "I": "i", "ı": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g"})
_ASCII_AFTER_CASEFOLD = str.maketrans(
    {"ç": "c", "ö": "o", "ü": "u", "â": "a", "î": "i", "û": "u", "ê": "e"}
)
_PUNCTUATION = str.maketrans(
    {"’": "'", "‘": "'", "ʼ": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", "‑": "-"}
)


def strip_invisible(text: str) -> str:
    """NFKC, with format characters such as zero-width spaces removed."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _fold_chars(text: str) -> str:
    text = strip_invisible(text).translate(_TURKISH)
    return text.casefold().translate(_ASCII_AFTER_CASEFOLD).translate(_PUNCTUATION)


def fold(text: str) -> str:
    """Lower-case, ASCII-fold Turkish letters, straighten punctuation, squeeze whitespace."""
    return " ".join(_fold_chars(text).split())


_MARKDOWN = re.compile(r"[*`]|^\s{0,3}(?:#+|>|[-+]\s)\s*|(?<!\w)_+|_+(?!\w)", re.M)


def reply_text(reply: str) -> str:
    """A reply ready for matching: markdown marks removed, so "do **not**" reads "do not"."""
    return fold(_MARKDOWN.sub("", reply))


def fold_pattern(pattern: str) -> str:
    r"""Fold a regular expression without touching its escapes, so ``\S`` stays ``\S``."""
    out: list[str] = []
    plain: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "\\" and i + 1 < len(pattern):
            out.append(_fold_chars("".join(plain)))
            plain = []
            out.append(pattern[i : i + 2])
            i += 2
        else:
            plain.append(pattern[i])
            i += 1
    out.append(_fold_chars("".join(plain)))
    return "".join(out)


# Grouped numbers first, then plain ones. A digit may not be glued to a word or to another
# number, but a Turkish suffix after an apostrophe is fine: 1.210'dur, 112'yi.
_NUMBER = re.compile(
    r"(?<![\w.,])"
    r"(?:\d{1,3}(?:[., ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?!\d)"
)


@dataclass(frozen=True)
class NumberHit:
    """One number in the text. ``values`` holds every reading the digits allow."""

    raw: str
    start: int
    end: int
    values: tuple[float, ...]


def number_readings(raw: str) -> tuple[float, ...]:
    """Every value a written number can mean.

    "1.210" is 1210 to a Turkish reader and 1.21 to an American one, and the same goes for
    "1,210" the other way round. The scorer cannot tell which the writer meant, so it keeps
    both. "26,8" and "1.210,50" have only one possible reading.
    """
    grouped_by_space = " " in raw
    raw = raw.replace(" ", "")
    dots, commas = raw.count("."), raw.count(",")
    if not dots and not commas:
        return (float(raw),)
    if dots and commas:
        # Whichever separator comes last is the decimal mark.
        decimal = "." if raw.rfind(".") > raw.rfind(",") else ","
        group = "," if decimal == "." else "."
        return (float(raw.replace(group, "").replace(decimal, ".")),)
    separator = "." if dots else ","
    if dots + commas > 1:
        return (float(raw.replace(separator, "")),)
    head, tail = raw.split(separator)
    if grouped_by_space:
        # "1 210,500" can only be a decimal: the space has already done the grouping.
        return (float(f"{head}.{tail}"),)
    if len(tail) == 3 and 1 <= len(head) <= 3 and head != "0":
        return (float(head + tail), float(f"{head}.{tail}"))
    return (float(f"{head}.{tail}"),)


def find_numbers(text: str) -> list[NumberHit]:
    """All numbers in already folded text, with their positions."""
    return [
        NumberHit(m.group(0), m.start(), m.end(), number_readings(m.group(0)))
        for m in _NUMBER.finditer(text)
    ]


# A negation only reaches as far as the next comma, colon or sentence end, and "but" ends
# it too. So "not 1210 but 1200" negates 1210 and leaves 1200 standing, and "no, 1210 is
# right" negates nothing.
_SCOPE = r"(?:(?!\b(?:but|however|ama|fakat|ancak)\b)[^.!?,;:\n]){0,40}$"
_NEGATED_BEFORE = re.compile(
    r"(?:\b(?:not|never|nor|cannot|can't|don't|doesn't|didn't|isn't|aren't|won't|wouldn't|"
    r"shouldn't|without|avoid|rather than|instead of|myth|misconception|claim|claims|"
    r"asla|sanilanin aksine)\b|\bno\b(?!,))" + _SCOPE
)
_NEGATED_AFTER = re.compile(
    r"^[^.!?,;:\n\d]{0,30}\b(?:degil\w*|yanlis\w*|efsane\w*|yerine|iddia\w*|inanis\w*|"
    r"is a myth|is false|is wrong|are wrong|is incorrect|is not true|isn't true|is untrue)\b"
)


def is_negated(text: str, start: int, end: int) -> bool:
    """Whether the span of folded text is being denied rather than asserted."""
    return bool(_NEGATED_BEFORE.search(text[:start]) or _NEGATED_AFTER.search(text[end:]))
