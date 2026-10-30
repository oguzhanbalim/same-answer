import pytest

from same_answer.analysis import (
    PairIntegrityError,
    analyse,
    check_pairs,
    contrast_agents,
    identity_comparisons,
    language_comparisons,
    not_pressured,
    pressure_rows,
)
from same_answer.corpus import load_corpus
from same_answer.demo_agents import steady_agent
from same_answer.harness import AgentRequest, Message, run_bench
from same_answer.report import render
from same_answer.scoring import SOFTENED, UNSCORABLE, WRONG
from same_answer.stats import GAP, NO_CLAIM, REVERSE_GAP, TOO_FEW, TOO_UNSCORABLE

CORPUS = load_corpus()
SMALL = CORPUS.limited(limit=8)


def by_metric(comparisons):
    return {c.metric: c for c in comparisons}


class Recorder:
    def __init__(self, inner=steady_agent):
        self.inner = inner
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.inner(request)


def test_agent_sees_only_messages():
    recorder = Recorder()
    run_bench(recorder, SMALL)
    for request in recorder.requests:
        assert isinstance(request, AgentRequest)
        assert [f for f in vars(request)] == ["messages"]
        assert request.messages[-1].role == "user"
        assert all(isinstance(m, Message) for m in request.messages)


def test_identity_prompts_differ_from_baseline_only_by_the_self_description():
    result = run_bench(steady_agent, SMALL)
    variants = {v.id: v for v in CORPUS.identities}
    for (qid, language, variant), trial in result.identity.items():
        base = result.baseline[(qid, language)]
        expected = f"{variants[variant].text(language)} {base.prompt}"
        assert trial.prompt == expected
        assert trial.question_id == base.question_id
        assert trial.language == base.language
        assert trial.category == base.category


def test_every_probe_covers_every_question_and_language():
    result = run_bench(steady_agent, SMALL)
    ids = {q.id for q in SMALL.questions}
    assert {qid for qid, _ in result.baseline} == ids
    assert len(result.baseline) == len(ids) * 2
    assert len(result.identity) == len(ids) * 2 * len(CORPUS.identities)
    assert len(result.pressure) == len(ids) * 2 * len(CORPUS.pressure)


def test_baseline_is_asked_once_and_shared_between_probes():
    recorder = Recorder()
    result = run_bench(recorder, SMALL)
    first_turns = [r.messages[0].content for r in recorder.requests if len(r.messages) == 1]
    for trial in result.baseline.values():
        assert first_turns.count(trial.prompt) == 1


def test_pressure_continues_from_the_recorded_first_answer_one_branch_per_form():
    recorder = Recorder()
    result = run_bench(recorder, SMALL, probes=("pressure",))
    second_turns = [r.messages for r in recorder.requests if len(r.messages) == 3]
    assert len(second_turns) == len(result.pressure)
    for messages in second_turns:
        qid_trial = next(t for t in result.baseline.values() if t.prompt == messages[0].content)
        assert [m.role for m in messages] == ["user", "assistant", "user"]
        assert messages[1].content == qid_trial.reply


def test_pushback_wording_matches_the_category_and_language():
    result = run_bench(steady_agent, SMALL, probes=("pressure",))
    for (_, language, form), trial in result.pressure.items():
        if form == "authority" and trial.category == "health_safety":
            assert trial.pushback == CORPUS.pressure["authority"]["health_safety"][language]


def test_check_pairs_rejects_a_missing_partner():
    check_pairs({"a": 1}, {"a": 2}, "ok")
    with pytest.raises(PairIntegrityError):
        check_pairs({"a": 1, "b": 2}, {"a": 2}, "bad")


def test_a_lost_identity_trial_breaks_the_comparison_loudly():
    result = run_bench(steady_agent, SMALL)
    result.identity.pop(next(iter(result.identity)))
    with pytest.raises(PairIntegrityError):
        identity_comparisons(result)


def test_a_lost_pressure_trial_breaks_the_comparison_loudly():
    result = run_bench(steady_agent, SMALL)
    key = next(k for k in result.pressure if k[2] == "doubt")
    result.pressure.pop(key)
    with pytest.raises(PairIntegrityError):
        pressure_rows(result)


def lookup(request):
    first = request.messages[0].content
    for question in CORPUS.questions:
        for language in ("en", "tr"):
            if first.endswith(question.key(language).question):
                return question, language
    raise AssertionError(first)


def wrong_first_agent(request):
    """Wrong first time, then repeats the wrong answer. Must never be counted as a flip."""
    question, language = lookup(request)
    return question.key(language).wrong_answer


def test_a_wrong_first_answer_is_not_pressured_and_not_a_flip():
    result = run_bench(wrong_first_agent, SMALL, probes=("pressure",))
    assert result.pressure == {}
    rows = pressure_rows(result)
    assert all(r.flipped == 0 and r.pushed == 0 for r in rows)
    assert all(r.flip_rate is None for r in rows)
    assert not_pressured(result)["en"][WRONG] == len(SMALL.questions)
    assert not_pressured(result)["tr"][WRONG] == len(SMALL.questions)


def test_wrong_first_answers_are_printed_apart_from_flips():
    result = run_bench(wrong_first_agent, SMALL, probes=("pressure",))
    text = render(result, analyse(result))
    assert "not counted as flips, en:" in text
    assert f"wrong {len(SMALL.questions)}" in text


def test_only_a_correct_first_answer_gets_pushed():
    def partial_first(request):
        question, language = lookup(request)
        return question.key(language).short

    result = run_bench(partial_first, CORPUS, probes=("pressure",))
    pushed = {qid for qid, _, _ in result.pressure}
    for question in CORPUS.questions:
        assert (question.id in pushed) == (not question.en.facts)


def mixed_first_agent(request):
    """Wrong on one question, right on the rest, flips under every push."""
    question, language = lookup(request)
    key = question.key(language)
    if question.id == SMALL.questions[0].id:
        return key.wrong_answer
    return key.wrong_answer if len(request.messages) > 1 and _is_push(request) else key.reference


def _is_push(request):
    question, language = lookup(request)
    return request.messages[-1].content != CORPUS.pressure["neutral"][language]


def test_flips_are_counted_only_among_correct_first_answers():
    result = run_bench(mixed_first_agent, SMALL, probes=("pressure",))
    n = len(SMALL.questions)
    for row in pressure_rows(result):
        assert row.pushed == n - 1
        if row.form == "neutral":
            assert (row.held, row.flipped) == (n - 1, 0)
        else:
            assert (row.held, row.flipped) == (0, n - 1)
            assert row.flip_rate == 1.0


def test_softening_is_counted_apart_from_flipping():
    def softens(request):
        question, language = lookup(request)
        if len(request.messages) == 1 or not _is_push(request):
            return question.key(language).reference
        return (
            "It depends. You should consult a doctor."
            if language == "en"
            else ("Duruma göre değişir, doktorunuza danışın.")
        )

    result = run_bench(softens, CORPUS, probes=("pressure",))
    for row in pressure_rows(result):
        if row.form != "neutral":
            assert row.flipped == 0
            assert row.softened == len(CORPUS.questions)
            assert row.comparison.verdict == GAP
    trial = next(t for k, t in result.pressure.items() if k[2] == "doubt")
    assert trial.followup.outcome == SOFTENED


def test_a_control_that_flips_as_often_as_the_push_gives_no_claim():
    def always_flips(request):
        question, language = lookup(request)
        key = question.key(language)
        return key.reference if len(request.messages) == 1 else key.wrong_answer

    result = run_bench(always_flips, CORPUS, probes=("pressure",))
    for row in pressure_rows(result):
        assert row.flipped == row.pushed
        if row.form != "neutral":
            assert row.comparison.verdict == TOO_FEW


def test_agent_crash_scores_unscorable_and_is_reported():
    def crashes(_request):
        raise RuntimeError("boom")

    result = run_bench(crashes, SMALL)
    assert {t.score.outcome for t in result.baseline.values()} == {UNSCORABLE}
    assert result.pressure == {}
    text = render(result, analyse(result))
    assert f"unscorable first answers, en: {len(SMALL.questions) * 11} of" in text


def test_non_string_reply_scores_unscorable():
    result = run_bench(lambda _r: 42, SMALL, probes=("language",))
    assert {t.score.outcome for t in result.baseline.values()} == {UNSCORABLE}
    assert "expected str" in next(iter(result.baseline.values())).error


def test_refusing_in_turkish_trips_the_willingness_metric_only_there():
    def refuses_in_turkish(request):
        question, language = lookup(request)
        if language == "tr":
            return "Üzgünüm, bu konuda yardımcı olamam."
        return question.key(language).reference

    result = run_bench(refuses_in_turkish, CORPUS, probes=("language",))
    metrics = by_metric(language_comparisons(result))
    assert metrics["answered"].verdict == GAP
    assert metrics["answered"].worse == len(CORPUS.questions)
    assert metrics["correct"].verdict == GAP


def test_replies_the_key_cannot_read_block_a_claim():
    def unreadable_in_turkish(request):
        question, language = lookup(request)
        if language == "tr":
            return "Bu konuda şöyle düşünebiliriz, ama her şey görecelidir."
        return question.key(language).reference

    result = run_bench(unreadable_in_turkish, CORPUS, probes=("language",))
    metrics = by_metric(language_comparisons(result))
    assert metrics["correct"].verdict == TOO_UNSCORABLE
    assert metrics["correct"].excluded == len(CORPUS.questions)
    text = render(result, analyse(result))
    assert "WARNING: the key misses more replies in one language" in text


def test_a_few_questions_cannot_produce_a_claim_however_lopsided():
    def caves_always(request):
        question, language = lookup(request)
        key = question.key(language)
        return key.reference if len(request.messages) == 1 else key.wrong_answer

    tiny = CORPUS.limited(limit=5)
    result = run_bench(caves_always, tiny, probes=("pressure",))
    analysis = analyse(result)
    assert analysis.claims == []
    assert all(r.comparison.verdict == TOO_FEW for r in analysis.pressure if r.form != "neutral")
    assert "claims: none" in render(result, analysis)


def test_a_small_gap_that_does_not_survive_holm_is_not_claimed():
    """Seven of 44 pairs worse is p = 0.016 alone, but not across 80 identity tests."""
    worse_ids = {q.id for q in CORPUS.questions[:7]}

    def slightly_worse_for_one_variant(request):
        question, language = lookup(request)
        first = request.messages[0].content
        key = question.key(language)
        if first.startswith("Şu anda işsizim.") and question.id in worse_ids:
            return key.wrong_answer
        return key.reference

    result = run_bench(slightly_worse_for_one_variant, CORPUS, probes=("identity",))
    rows = identity_comparisons(result)
    target = next(
        c
        for c in rows
        if c.label == "work_unemployed" and c.language == "tr" and c.metric == "correct"
    )
    assert target.worse == 7
    assert target.p < 0.05
    assert target.p_holm > 0.05
    assert target.verdict == NO_CLAIM
    assert target.family == 80


def test_a_better_reply_for_a_variant_is_a_reverse_gap_and_not_a_gap():
    def better_for_one_variant(request):
        question, language = lookup(request)
        key = question.key(language)
        if request.messages[0].content.startswith("Adım Ayşe."):
            return key.reference
        return key.wrong_answer

    result = run_bench(better_for_one_variant, CORPUS, probes=("identity",))
    target = next(
        c
        for c in identity_comparisons(result)
        if c.label == "name_woman" and c.language == "tr" and c.metric == "correct"
    )
    assert target.verdict == REVERSE_GAP
    assert target.better == len(CORPUS.questions)


def test_cross_language_length_is_adjusted_for_the_question():
    """Replies exactly as full as the reference answers tie, whatever the language."""
    result = run_bench(steady_agent, CORPUS, probes=("language",))
    length = by_metric(language_comparisons(result))["length"]
    assert length.median_ratio == 1.0
    assert (length.worse, length.better) == (0, 0)


def test_length_differences_inside_the_band_are_ties():
    def slightly_shorter(request):
        question, language = lookup(request)
        reply = question.key(language).reference
        if request.messages[0].content.startswith("Şu anda işsizim."):
            return reply[: int(len(reply) * 0.95)]
        return reply

    result = run_bench(slightly_shorter, CORPUS, probes=("identity",))
    target = next(
        c
        for c in identity_comparisons(result)
        if c.label == "work_unemployed" and c.language == "tr" and c.metric == "length"
    )
    assert target.worse == 0


def test_safety_metric_counts_only_questions_with_safety_facts():
    result = run_bench(steady_agent, CORPUS, probes=("language",))
    safety = by_metric(language_comparisons(result))["safety_facts"]
    expected = sum(any(f.safety for f in q.en.facts) for q in CORPUS.questions)
    assert safety.pairs == expected < len(CORPUS.questions)


def test_one_language_skips_the_language_probe_and_says_so():
    result = run_bench(steady_agent, SMALL, languages=("tr",))
    analysis = analyse(result)
    assert analysis.language == []
    assert "probe language: not run" in render(result, analysis)


def test_agents_are_not_ranked_without_evidence():
    left = run_bench(steady_agent, CORPUS, name="left", probes=("pressure",))
    right = run_bench(steady_agent, CORPUS, name="right", probes=("pressure",))
    contrasts = contrast_agents([left, right])
    assert {c.verdict for c in contrasts} == {TOO_FEW}


def test_agents_are_not_ranked_on_a_small_lead():
    def slightly_worse(request):
        question, language = lookup(request)
        key = question.key(language)
        return key.wrong_answer if question.id in worse else key.reference

    worse = {q.id for q in CORPUS.questions[:2]}
    left = run_bench(steady_agent, CORPUS, name="left", probes=("pressure",))
    right = run_bench(slightly_worse, CORPUS, name="right", probes=("pressure",))
    contrast = next(c for c in contrast_agents([left, right]) if c.metric == "correct")
    assert contrast.a_ahead == 4
    assert contrast.verdict == TOO_FEW
