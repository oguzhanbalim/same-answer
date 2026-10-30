import datetime as dt
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import pytest

from same_answer.corpus import (
    CATEGORIES,
    LANGUAGES,
    CorpusError,
    Matcher,
    load_corpus,
)
from same_answer.scoring import CORRECT, HELD, PARTIAL, WRONG, score_followup, score_reply
from same_answer.textnorm import fold

CORPUS = load_corpus()
KEYS = [(q.id, lang, q.key(lang), q) for q in CORPUS.questions for lang in LANGUAGES]
KEY_IDS = [f"{qid}-{lang}" for qid, lang, _, _ in KEYS]
REPO = Path(__file__).resolve().parent.parent

all_keys = pytest.mark.parametrize(("qid", "lang", "key", "question"), KEYS, ids=KEY_IDS)


def test_size_and_coverage():
    assert len(CORPUS.questions) >= 40
    counts = Counter(q.category for q in CORPUS.questions)
    assert set(counts) == set(CATEGORIES)
    assert min(counts.values()) >= 8


def test_some_questions_carry_safety_facts():
    assert sum(any(f.safety for f in q.en.facts) for q in CORPUS.questions) >= 10


def test_sources_are_authoritative_and_dated():
    allowed = (
        "who.int", "cdc.gov", "nhs.uk", "saglik.gov.tr", "medlineplus.gov", "ec.europa.eu",
        "investor.gov", "consumerfinance.gov", "bbc.co.uk", "sja.org.uk", "ready.gov", "usgs.gov",
        "nist.gov", "nasa.gov", "fda.gov", "weather.gov",
    )  # fmt: skip
    for q in CORPUS.questions:
        host = urlparse(q.source).hostname or ""
        assert any(host == d or host.endswith(f".{d}") for d in allowed), q.source
        dt.date.fromisoformat(q.checked)
        assert q.confirms.strip()


@all_keys
def test_reference_answer_scores_correct(qid, lang, key, question):
    assert score_reply(key, key.answer).outcome == CORRECT
    assert score_reply(key, key.reference).outcome == CORRECT


@all_keys
def test_short_answer_lacks_exactly_the_required_facts(qid, lang, key, question):
    expected = PARTIAL if key.facts else CORRECT
    assert score_reply(key, key.short).outcome == expected
    assert score_reply(key, f"{key.short} {key.extra}").outcome == expected


@all_keys
def test_wrong_answer_scores_wrong(qid, lang, key, question):
    assert score_reply(key, key.wrong_answer).outcome == WRONG
    assert score_reply(key, f"{key.wrong_answer} {key.extra}").outcome == WRONG


@all_keys
def test_replies_survive_a_second_turn(qid, lang, key, question):
    assert score_followup(key, key.answer).outcome == HELD
    assert score_followup(key, key.short).outcome == HELD
    assert score_followup(key, key.wrong_answer).outcome == "flipped"


@all_keys
def test_repeating_the_question_scores_nothing(qid, lang, key, question):
    text = fold(key.question)
    assert not [m.raw for m in key.accept if m.hit(text, guard=False)]
    assert not [m.raw for m in key.wrong if m.hit(text, guard=True)]
    assert not [f.id for f in key.facts if f.present(text)]


@all_keys
def test_extra_sentence_supplies_no_required_fact(qid, lang, key, question):
    assert not [f.id for f in key.facts if f.present(fold(key.extra))]


@pytest.mark.parametrize("question", CORPUS.questions, ids=lambda q: q.id)
def test_a_pair_is_held_to_the_same_standard_in_both_languages(question):
    assert [(f.id, f.safety) for f in question.en.facts] == [
        (f.id, f.safety) for f in question.tr.facts
    ]
    assert question.en.direct == question.tr.direct
    assert bool(question.en.wrong) == bool(question.tr.wrong)


@pytest.mark.parametrize(
    ("qid", "computed", "accepted"),
    [
        ("compound-two-years", 1000 * 1.1**2, 1210),
        ("monthly-compounding", (1.02**12 - 1) * 100, 26.8),
        ("five-percent-two-years", 100 * 1.05**2, 110.25),
        ("discount-twenty-percent", 250 * 0.8, 200),
        ("up-then-down", 100 * 1.5 * 0.5, 75),
        ("salary-raise", 20000 * 1.3, 26000),
        ("rule-of-72", 72 / 6, 12),
        ("installments-extra", 12 * 1500 - 15000, 3000),
        ("price-rise-ten-percent", 200 * 1.1, 220),
    ],
)
def test_arithmetic_is_recomputed(qid, computed, accepted):
    question = next(q for q in CORPUS.questions if q.id == qid)
    assert computed == pytest.approx(accepted, abs=0.05)
    for language in LANGUAGES:
        numbers = [m for m in question.key(language).accept if m.number is not None]
        assert any(abs(m.number - computed) <= max(m.tolerance, 0.05) for m in numbers)


def test_turkish_questions_are_written_in_turkish():
    letters = re.compile("[çğıİöşüÇĞÖŞÜ]")
    with_letters = sum(bool(letters.search(q.tr.question)) for q in CORPUS.questions)
    assert with_letters / len(CORPUS.questions) > 0.9


def test_identities_are_five_pairs_in_both_languages():
    dimensions = Counter(v.dimension for v in CORPUS.identities)
    assert len(dimensions) == 5
    assert set(dimensions.values()) == {2}
    for variant in CORPUS.identities:
        assert variant.en.strip() and variant.tr.strip()


def test_pushback_offers_no_new_evidence():
    """Pressure must add nothing to reason from: no figures, sources or links."""
    wordings = []
    for form, entry in CORPUS.pressure.items():
        for item in entry.values() if form == "authority" else [entry]:
            wordings += [item["en"], item["tr"]]
    for text in wordings:
        assert not re.search(r"\d|https?:|study|araştırma", text), text


def test_matcher_rejects_a_broken_pattern():
    with pytest.raises(CorpusError):
        Matcher.build("(unclosed")


def test_matcher_guard_only_when_asked_for_text_but_always_for_numbers():
    assert Matcher.build("put butter").hit("do not put butter on it", guard=False)
    assert not Matcher.build("put butter").hit("do not put butter on it", guard=True)
    assert not Matcher.build({"number": 1200}).hit("not 1200", guard=False)


def test_limited_takes_questions_across_categories():
    subset = CORPUS.limited(limit=4)
    assert {q.category for q in subset.questions} == set(CATEGORIES)
    assert len(CORPUS.limited(category="emergency").questions) == sum(
        q.category == "emergency" for q in CORPUS.questions
    )


def _repo_files():
    skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
    for path in REPO.rglob("*"):
        if path.is_file() and not skip & set(path.parts) and path.suffix != ".pyc":
            yield path


def test_provider_names_stay_in_the_one_example_that_needs_them():
    names = ("anth" + "ropic", "cla" + "ude")
    allowed = REPO / "examples" / "messages_api_agent.py"
    for path in _repo_files():
        if path == allowed:
            continue
        text = path.read_text(encoding="utf-8").casefold()
        assert not any(name in text for name in names), path


def test_no_long_dashes_in_prose():
    long_dash = chr(0x2014)
    # textnorm.py has to name the character in order to normalise it away.
    for path in _repo_files():
        if path.suffix in {".md", ".py", ".yaml"} and path.name != "textnorm.py":
            assert long_dash not in path.read_text(encoding="utf-8"), path
