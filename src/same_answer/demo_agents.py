"""Three fake agents. There is no model in this file.

They exist to show that each metric catches what it should, and they say nothing about any
real model. Each one finds the question by looking its text up in the corpus and then reads
a canned reply from the answer key.

``steady``           answers from the key every time and never gives way.
``caves``            the same, until it meets claimed authority or emotion. Then it agrees.
``thin_in_turkish``  full in English. In Turkish it is shorter, drops required facts, and is
                     wrong on about a third of the questions. It also leaves out its closing
                     advice for four identities, in both languages, while the answer itself
                     stays the same.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable
from dataclasses import dataclass

from same_answer.corpus import LANGUAGES, Corpus, LanguageKey, Question, load_corpus
from same_answer.harness import AgentRequest

HOLD_PREFIX = {"en": "I understand, but the answer stands.", "tr": "Anlıyorum ama cevap aynı."}
CAVE_PREFIX = {"en": "You are right, I apologise.", "tr": "Haklısınız, özür dilerim."}

# The identities this fake agent is written to shortchange. They are named here to test the
# length metric. The bench itself holds no such list and treats every variant alike.
SHORTCHANGED = frozenset(
    {"place_village", "school_left_early", "origin_refugee", "work_unemployed"}
)


@dataclass(frozen=True)
class _Turn:
    question: Question
    language: str
    variant: str | None
    form: str | None

    @property
    def key(self) -> LanguageKey:
        return self.question.key(self.language)


class _Reader:
    """Works out from the text alone what was asked, the way a person reading it would."""

    def __init__(self, corpus: Corpus) -> None:
        self._questions = {
            q.key(lang).question: (q, lang) for q in corpus.questions for lang in LANGUAGES
        }
        self._identities = {v.text(lang): v.id for v in corpus.identities for lang in LANGUAGES}
        self._forms: dict[str, str] = {}
        for form, entry in corpus.pressure.items():
            wordings = entry.values() if form == "authority" else [entry]
            for wording in wordings:
                for language in LANGUAGES:
                    self._forms[wording[language]] = form

    def read(self, request: AgentRequest) -> _Turn:
        first = request.messages[0].content
        for text, (question, language) in self._questions.items():
            if first.endswith(text):
                preface = first[: -len(text)].strip()
                variant = self._identities.get(preface) if preface else None
                form = None
                if len(request.messages) > 1:
                    form = self._forms.get(request.messages[-1].content)
                return _Turn(question, language, variant, form)
        raise ValueError(f"not a question from the corpus: {first!r}")


_reader: _Reader | None = None


def _read(request: AgentRequest) -> _Turn:
    global _reader
    if _reader is None:
        _reader = _Reader(load_corpus())
    return _reader.read(request)


def steady_agent(request: AgentRequest) -> str:
    """Fake agent: answers from the key and does not move."""
    turn = _read(request)
    reply = turn.key.reference
    return f"{HOLD_PREFIX[turn.language]} {reply}" if turn.form else reply


def caves_agent(request: AgentRequest) -> str:
    """Fake agent: holds against plain doubt and flips under authority or emotion."""
    turn = _read(request)
    if turn.form in ("authority", "emotion"):
        return f"{CAVE_PREFIX[turn.language]} {turn.key.wrong_answer}"
    reply = turn.key.reference
    return f"{HOLD_PREFIX[turn.language]} {reply}" if turn.form else reply


def _wrong_in_turkish(question_id: str) -> bool:
    # crc32 and not hash(), so the same questions go wrong on every run.
    return zlib.crc32(question_id.encode()) % 3 == 0


def thin_in_turkish_agent(request: AgentRequest) -> str:
    """Fake agent: worse in Turkish, and shorter for some identities in both languages."""
    turn = _read(request)
    key = turn.key
    if turn.language == "en":
        core = key.answer
    else:
        core = key.wrong_answer if _wrong_in_turkish(turn.question.id) else key.short
    reply = core if turn.variant in SHORTCHANGED else f"{core} {key.extra}"
    return f"{HOLD_PREFIX[turn.language]} {reply}" if turn.form else reply


AGENTS: dict[str, Callable[[AgentRequest], str]] = {
    "steady": steady_agent,
    "caves": caves_agent,
    "thin_in_turkish": thin_in_turkish_agent,
}
