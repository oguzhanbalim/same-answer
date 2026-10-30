"""Load and validate the question bank, identities and pressure wordings."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from same_answer.textnorm import find_numbers, fold_pattern, is_negated

LANGUAGES = ("en", "tr")
CATEGORIES = ("health_safety", "money_arithmetic", "emergency", "everyday_science")
PRESSURE_FORMS = ("neutral", "doubt", "authority", "emotion")
DIRECT_ANSWERS = ("no", "yes", None)


class CorpusError(ValueError):
    """The question bank breaks one of its own rules."""


@dataclass(frozen=True)
class Matcher:
    """One accepted or known-wrong form: a regular expression or a number."""

    raw: str
    pattern: re.Pattern[str] | None = None
    number: float | None = None
    tolerance: float = 1e-9

    @classmethod
    def build(cls, spec: Any) -> Matcher:
        if isinstance(spec, dict):
            if "number" not in spec:
                raise CorpusError(f"matcher needs a number: {spec!r}")
            number = float(spec["number"])
            tolerance = float(spec.get("tolerance", 1e-9))
            return cls(raw=f"{number:g}", number=number, tolerance=tolerance)
        if not isinstance(spec, str) or not spec:
            raise CorpusError(f"matcher must be a pattern or a number: {spec!r}")
        try:
            return cls(raw=spec, pattern=re.compile(fold_pattern(spec)))
        except re.error as exc:
            raise CorpusError(f"bad pattern {spec!r}: {exc}") from exc

    def hit(self, folded: str, *, guard: bool) -> bool:
        """Whether this form appears in folded text.

        With ``guard``, a hit that is being denied does not count: "not 1200" is no evidence
        for 1200. Numbers are always guarded, because a bare digit has no meaning of its own.
        """
        if self.number is not None:
            return any(
                any(abs(value - self.number) <= self.tolerance for value in found.values)
                and not is_negated(folded, found.start, found.end)
                for found in find_numbers(folded)
            )
        assert self.pattern is not None
        if not guard:
            return self.pattern.search(folded) is not None
        return any(
            not is_negated(folded, m.start(), m.end()) for m in self.pattern.finditer(folded)
        )


@dataclass(frozen=True)
class Fact:
    id: str
    safety: bool
    any: tuple[Matcher, ...]

    def present(self, folded: str) -> bool:
        return any(m.hit(folded, guard=False) for m in self.any)


@dataclass(frozen=True)
class LanguageKey:
    """The answer key for one question in one language."""

    language: str
    question: str
    answer: str
    short: str
    extra: str
    wrong_answer: str
    accept: tuple[Matcher, ...]
    wrong: tuple[Matcher, ...]
    facts: tuple[Fact, ...]
    direct: str | None

    @property
    def reference(self) -> str:
        """The full reference reply, used to compare lengths across languages."""
        return f"{self.answer} {self.extra}"


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    source: str
    confirms: str
    checked: str
    en: LanguageKey
    tr: LanguageKey

    def key(self, language: str) -> LanguageKey:
        if language not in LANGUAGES:
            raise CorpusError(f"unknown language: {language}")
        return self.en if language == "en" else self.tr


@dataclass(frozen=True)
class IdentityVariant:
    id: str
    dimension: str
    en: str
    tr: str

    def text(self, language: str) -> str:
        return self.en if language == "en" else self.tr


@dataclass(frozen=True)
class Corpus:
    questions: tuple[Question, ...]
    identities: tuple[IdentityVariant, ...]
    pressure: dict[str, Any]

    def pressure_text(self, form: str, question: Question, language: str) -> str:
        entry = self.pressure[form]
        if form == "authority":
            entry = entry[question.category]
        return entry[language]

    def limited(self, *, limit: int | None = None, category: str | None = None) -> Corpus:
        """The same corpus with fewer questions, taken evenly across categories."""
        questions = [q for q in self.questions if category in (None, q.category)]
        if limit is not None:
            order = {name: 0 for name in CATEGORIES}
            ranked = []
            for q in questions:
                ranked.append((order[q.category], CATEGORIES.index(q.category), q))
                order[q.category] += 1
            questions = [q for *_, q in sorted(ranked, key=lambda r: r[:2])[:limit]]
        return Corpus(tuple(questions), self.identities, self.pressure)


def _text(entry: dict[str, Any], field: str, where: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"{where}: missing {field}")
    return value


def _language_key(entry: dict[str, Any], language: str, where: str) -> LanguageKey:
    direct = entry.get("direct")
    if direct not in DIRECT_ANSWERS:
        raise CorpusError(f"{where}: direct must be yes, no or absent")
    accept = tuple(Matcher.build(s) for s in entry.get("accept") or ())
    if not accept:
        raise CorpusError(f"{where}: needs at least one accept pattern")
    facts = tuple(
        Fact(
            id=_text(f, "id", where),
            safety=bool(f.get("safety", False)),
            any=tuple(Matcher.build(s) for s in f["any"]),
        )
        for f in entry.get("facts") or ()
    )
    return LanguageKey(
        language=language,
        question=_text(entry, "question", where),
        answer=_text(entry, "answer", where),
        short=_text(entry, "short", where),
        extra=_text(entry, "extra", where),
        wrong_answer=_text(entry, "wrong_answer", where),
        accept=accept,
        wrong=tuple(Matcher.build(s) for s in entry.get("wrong") or ()),
        facts=facts,
        direct=direct,
    )


def _question(entry: dict[str, Any]) -> Question:
    qid = _text(entry, "id", "question")
    category = _text(entry, "category", qid)
    if category not in CATEGORIES:
        raise CorpusError(f"{qid}: unknown category {category}")
    source = _text(entry, "source", qid)
    if not source.startswith("https://"):
        raise CorpusError(f"{qid}: source must be an https URL")
    checked = entry.get("checked")
    if not isinstance(checked, dt.date):
        raise CorpusError(f"{qid}: checked must be a date")
    en = _language_key(entry.get("en") or {}, "en", f"{qid}/en")
    tr = _language_key(entry.get("tr") or {}, "tr", f"{qid}/tr")
    if [(f.id, f.safety) for f in en.facts] != [(f.id, f.safety) for f in tr.facts]:
        raise CorpusError(f"{qid}: facts differ between languages, so the pair is not fair")
    if en.direct != tr.direct:
        raise CorpusError(f"{qid}: direct answer differs between languages")
    return Question(
        id=qid,
        category=category,
        source=source,
        confirms=_text(entry, "confirms", qid),
        checked=checked.isoformat(),
        en=en,
        tr=tr,
    )


def _load_yaml(name: str) -> Any:
    path = resources.files("same_answer") / "data" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_corpus() -> Corpus:
    questions = tuple(_question(entry) for entry in _load_yaml("questions.yaml"))
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise CorpusError("question ids must be unique")
    identities = tuple(
        IdentityVariant(
            id=variant["id"], dimension=dimension["id"], en=variant["en"], tr=variant["tr"]
        )
        for dimension in _load_yaml("identities.yaml")
        for variant in dimension["variants"]
    )
    pressure = _load_yaml("pressure.yaml")
    if set(pressure) != set(PRESSURE_FORMS):
        raise CorpusError(f"pressure forms must be exactly {PRESSURE_FORMS}")
    if set(pressure["authority"]) != set(CATEGORIES):
        raise CorpusError("authority pressure needs a wording for every category")
    return Corpus(questions, identities, pressure)
