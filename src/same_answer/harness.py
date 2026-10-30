"""Run any agent callable through the three probes.

An agent sees only what a person would type, as a list of messages. It is never told the
question id, the probe, the language or the identity variant, so it cannot behave well just
for the bench unless it recognises the wording. The demo agents do exactly that, by looking
the text up in the corpus, which is one reason their numbers mean nothing about any model.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from same_answer.corpus import LANGUAGES, Corpus, Question
from same_answer.scoring import (
    AGENT_ERROR,
    CORRECT,
    UNSCORABLE,
    FollowupScore,
    Score,
    score_followup,
    score_reply,
    unscorable,
)

PROBES = ("language", "identity", "pressure")


@dataclass(frozen=True)
class Message:
    role: str  # "user" or "assistant"
    content: str


@dataclass(frozen=True)
class AgentRequest:
    """The whole conversation so far. The agent returns the next assistant message."""

    messages: tuple[Message, ...]


Agent = Callable[[AgentRequest], str]


@dataclass(frozen=True)
class Trial:
    """One question asked one way, and how the reply scored."""

    question_id: str
    category: str
    language: str
    variant: str  # "baseline" or an identity variant id
    prompt: str
    reply: str | None
    error: str | None
    score: Score

    def to_dict(self) -> dict[str, Any]:
        row = self.__dict__.copy()
        row["score"] = self.score.to_dict()
        return row


@dataclass(frozen=True)
class PressureTrial:
    """A push against a correct first answer, and what became of the answer."""

    question_id: str
    category: str
    language: str
    form: str
    pushback: str
    reply: str | None
    error: str | None
    followup: FollowupScore

    def to_dict(self) -> dict[str, Any]:
        row = self.__dict__.copy()
        row["outcome"] = self.followup.outcome
        row["reason"] = self.followup.reason
        del row["followup"]
        return row


@dataclass(frozen=True)
class BenchResult:
    agent: str
    corpus: Corpus
    probes: tuple[str, ...]
    languages: tuple[str, ...]
    calls: int
    baseline: dict[tuple[str, str], Trial]
    identity: dict[tuple[str, str, str], Trial]
    pressure: dict[tuple[str, str, str], PressureTrial]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "probes": list(self.probes),
            "languages": list(self.languages),
            "questions": len(self.corpus.questions),
            "calls": self.calls,
            "baseline": [t.to_dict() for t in self.baseline.values()],
            "identity": [t.to_dict() for t in self.identity.values()],
            "pressure": [t.to_dict() for t in self.pressure.values()],
        }


class _Caller:
    """Calls the agent and keeps a crash from ending the run. A crash scores unscorable."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        self.calls = 0

    def __call__(self, messages: tuple[Message, ...]) -> tuple[str | None, str | None]:
        self.calls += 1
        try:
            reply = self._agent(AgentRequest(messages))
        except Exception as exc:  # Agents can fail in any way. Scoring must survive it.
            return None, repr(exc)
        if not isinstance(reply, str):
            return None, f"agent returned {type(reply).__name__}, expected str"
        return reply, None


def identity_prompt(self_description: str, question: str) -> str:
    return f"{self_description} {question}"


def _ask(caller: _Caller, question: Question, language: str, variant: str, prompt: str) -> Trial:
    reply, error = caller((Message("user", prompt),))
    score = unscorable(AGENT_ERROR) if error else score_reply(question.key(language), reply)
    return Trial(question.id, question.category, language, variant, prompt, reply, error, score)


def _push(
    caller: _Caller, corpus: Corpus, question: Question, first: Trial, form: str
) -> PressureTrial:
    """One branch of conversation per form, so one push cannot colour the next."""
    language = first.language
    key = question.key(language)
    pushback = corpus.pressure_text(form, question, language)
    assert first.reply is not None
    messages = (
        Message("user", first.prompt),
        Message("assistant", first.reply),
        Message("user", pushback),
    )
    reply, error = caller(messages)
    if error:
        followup = FollowupScore(UNSCORABLE, AGENT_ERROR, unscorable(AGENT_ERROR))
    else:
        followup = score_followup(key, reply)
    return PressureTrial(
        question.id, question.category, language, form, pushback, reply, error, followup
    )


def run_bench(
    agent: Agent,
    corpus: Corpus,
    *,
    name: str = "agent",
    probes: Iterable[str] = PROBES,
    languages: Iterable[str] = LANGUAGES,
) -> BenchResult:
    probes = tuple(p for p in PROBES if p in set(probes))
    languages = tuple(lang for lang in LANGUAGES if lang in set(languages))
    caller = _Caller(agent)

    # Every probe shares one baseline answer, so a baseline is asked once per language.
    baseline = {
        (q.id, lang): _ask(caller, q, lang, "baseline", q.key(lang).question)
        for q in corpus.questions
        for lang in languages
    }
    identity: dict[tuple[str, str, str], Trial] = {}
    if "identity" in probes:
        for q in corpus.questions:
            for lang in languages:
                for variant in corpus.identities:
                    prompt = identity_prompt(variant.text(lang), q.key(lang).question)
                    identity[(q.id, lang, variant.id)] = _ask(caller, q, lang, variant.id, prompt)

    pressure: dict[tuple[str, str, str], PressureTrial] = {}
    if "pressure" in probes:
        for q in corpus.questions:
            for lang in languages:
                first = baseline[(q.id, lang)]
                # A wrong first answer cannot flip. It is reported apart, and never pushed.
                if first.score.outcome != CORRECT:
                    continue
                for form in corpus.pressure:
                    pressure[(q.id, lang, form)] = _push(caller, corpus, q, first, form)

    return BenchResult(
        agent=name,
        corpus=corpus,
        probes=probes,
        languages=languages,
        calls=caller.calls,
        baseline=baseline,
        identity=identity,
        pressure=pressure,
    )
