import importlib.util
import json
import re
import shlex
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from same_answer.analysis import analyse, contrast_agents
from same_answer.cli import main
from same_answer.corpus import load_corpus
from same_answer.demo_agents import AGENTS, SHORTCHANGED
from same_answer.harness import run_bench
from same_answer.report import DEMO_NOTICE, render_contrasts
from same_answer.stats import GAP

CORPUS = load_corpus()


@pytest.fixture(scope="module")
def runs():
    results = {name: run_bench(agent, CORPUS, name=name) for name, agent in AGENTS.items()}
    return {name: (result, analyse(result)) for name, result in results.items()}


def claims(analysis):
    return {(c.probe, c.label, c.language, c.metric) for c in analysis.claims}


def test_steady_triggers_nothing(runs):
    result, analysis = runs["steady"]
    assert claims(analysis) == set()
    assert {t.score.outcome for t in result.baseline.values()} == {"correct"}
    assert {t.score.outcome for t in result.identity.values()} == {"correct"}
    assert {t.followup.outcome for t in result.pressure.values()} == {"held"}


def test_caves_triggers_exactly_authority_and_emotion(runs):
    result, analysis = runs["caves"]
    assert claims(analysis) == {
        ("pressure", form, language, "held")
        for form in ("authority", "emotion")
        for language in ("en", "tr")
    }
    n = len(CORPUS.questions)
    for row in analysis.pressure:
        if row.form in ("authority", "emotion"):
            assert (row.flipped, row.held, row.flip_rate) == (n, 0, 1.0)
        else:
            assert (row.flipped, row.held, row.flip_rate) == (0, n, 0.0)
    assert {t.score.outcome for t in result.baseline.values()} == {"correct"}


def test_thin_in_turkish_triggers_exactly_its_three_language_metrics_and_the_identities(runs):
    _, analysis = runs["thin_in_turkish"]
    expected = {("language", "tr vs en", "tr", m) for m in ("correct", "safety_facts", "length")}
    expected |= {
        ("identity", variant, language, "length")
        for variant in SHORTCHANGED
        for language in ("en", "tr")
    }
    assert claims(analysis) == expected


def test_thin_in_turkish_is_correct_and_still_thinner_for_the_shortchanged_identities(runs):
    """The point of the length metric: the answers match, and one side still gets less."""
    result, analysis = runs["thin_in_turkish"]
    for variant in SHORTCHANGED:
        for language in ("en", "tr"):
            rows = {
                c.metric: c
                for c in analysis.identity
                if c.label == variant and c.language == language
            }
            assert rows["correct"].worse == rows["correct"].better == 0
            assert rows["safety_facts"].worse == 0
            assert rows["length"].verdict == GAP
            assert rows["length"].median_ratio < 0.9
    assert all(
        t.score.outcome == result.baseline[(qid, lang)].score.outcome
        for (qid, lang, _), t in result.identity.items()
    )


def test_thin_in_turkish_does_not_flip_and_english_is_untouched(runs):
    result, analysis = runs["thin_in_turkish"]
    assert all(row.flipped == 0 for row in analysis.pressure)
    en = [t for (_, lang), t in result.baseline.items() if lang == "en"]
    assert {t.score.outcome for t in en} == {"correct"}
    tr = [t for (_, lang), t in result.baseline.items() if lang == "tr"]
    assert {t.score.outcome for t in tr} == {"correct", "partial", "wrong"}


def test_nothing_unscorable_in_the_demos(runs):
    for result, _ in runs.values():
        assert not [t for t in result.baseline.values() if t.score.outcome == "unscorable"]
        assert not [t for t in result.identity.values() if t.score.outcome == "unscorable"]
        assert not [t for t in result.pressure.values() if t.followup.outcome == "unscorable"]


def test_contrast_ranks_only_where_a_metric_separates_the_agents(runs):
    contrasts = {
        (c.a, c.b, c.metric): c for c in contrast_agents([runs["steady"][0], runs["caves"][0]])
    }
    assert contrasts["steady", "caves", "correct"].verdict == "too_few"
    assert contrasts["steady", "caves", "held"].a_ahead == 2 * len(CORPUS.questions)
    assert contrasts["steady", "caves", "held"].verdict == "reverse_gap"


def test_contrast_report_refuses_an_overall_rank(runs):
    text = render_contrasts(contrast_agents([runs["steady"][0], runs["steady"][0]]))
    assert "No ranking: the evidence here does not separate these agents." in text
    assert "No overall rank is given." in text


@pytest.mark.parametrize("agent", list(AGENTS))
def test_cli_demo_run_says_it_is_fake(agent, capsys):
    assert main(["run", "--agent", agent, "--limit", "4"]) == 0
    out = capsys.readouterr().out
    assert DEMO_NOTICE in out
    assert "say nothing about any real model" in out
    assert "Holm correction: applied across" in out
    assert "It is not proof of intent" in out
    assert "Not medical advice" in out


def test_cli_json_carries_the_notice_and_every_trial(capsys):
    assert main(["run", "--agent", "caves", "--limit", "4", "--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["demo_notice"] == DEMO_NOTICE
    assert DEMO_NOTICE in captured.err
    assert data["questions"] == 4
    assert data["baseline"][0]["reply"]
    assert data["analysis"]["pressure"]


def test_cli_fail_on_gap_exit_code(capsys):
    assert main(["run", "--agent", "caves", "--probe", "pressure", "--fail-on-gap"]) == 1
    assert main(["run", "--agent", "steady", "--probe", "pressure", "--fail-on-gap"]) == 0
    capsys.readouterr()


def test_cli_list_prints_sources(capsys):
    assert main(["list", "--category", "emergency"]) == 0
    out = capsys.readouterr().out
    assert "https://" in out
    assert "turkiye-112" in out


def test_cli_compare(capsys):
    assert main(["compare", "--agent", "steady", "--agent", "caves"]) == 0
    out = capsys.readouterr().out
    assert DEMO_NOTICE in out
    assert "steady ahead" in out


def test_cli_compare_needs_two_agents(capsys):
    assert main(["compare", "--agent", "steady"]) == 2
    capsys.readouterr()


def test_cli_rejects_unknown_agent(capsys):
    assert main(["run", "--agent", "nobody"]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_rejects_bad_limit(capsys):
    assert main(["run", "--agent", "steady", "--limit", "0"]) == 2
    capsys.readouterr()


def test_cli_loads_an_agent_from_a_file(tmp_path, capsys):
    path = tmp_path / "my_agent.py"
    path.write_text("def agent(request):\n    return 'I do not know.'\n", encoding="utf-8")
    assert main(["run", "--agent", f"{path}:agent", "--limit", "2", "--probe", "language"]) == 0
    out = capsys.readouterr().out
    assert DEMO_NOTICE not in out


def _load_example():
    path = Path(__file__).resolve().parent.parent / "examples" / "messages_api_agent.py"
    spec = importlib.util.spec_from_file_location("messages_api_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Scripted stand-in for the SDK client. No network, no key."""

    def __init__(self, text="No.", stop_reason="end_turn"):
        self.text, self.stop_reason, self.calls = text, stop_reason, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=self.text)
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason)


def test_example_passes_the_conversation_and_reads_the_model_from_the_environment(monkeypatch):
    module = _load_example()
    monkeypatch.setenv("SAME_ANSWER_MODEL", "model-under-test")
    client = FakeClient()
    # "No." is a full answer to this question in English, so the first answer gets pushed.
    question = next(q for q in CORPUS.questions if q.id == "vaccines-autism")
    corpus = replace(CORPUS, questions=(question,))
    result = run_bench(module.make_agent(client=client), corpus)
    assert result.calls == len(client.calls)
    assert {call["model"] for call in client.calls} == {"model-under-test"}
    assert client.calls[0]["messages"][0]["role"] == "user"
    assert "system" not in client.calls[0]
    three_turn = next(call for call in client.calls if len(call["messages"]) == 3)
    assert [m["role"] for m in three_turn["messages"]] == ["user", "assistant", "user"]


def test_example_without_a_model_scores_unscorable_and_does_not_crash_the_run(monkeypatch):
    module = _load_example()
    monkeypatch.delenv("SAME_ANSWER_MODEL", raising=False)
    result = run_bench(
        module.make_agent(client=FakeClient()), CORPUS.limited(limit=2), probes=("language",)
    )
    trial = next(iter(result.baseline.values()))
    assert trial.score.outcome == "unscorable"
    assert "SAME_ANSWER_MODEL" in trial.error


def test_example_treats_a_cut_off_reply_as_an_error(monkeypatch):
    module = _load_example()
    monkeypatch.setenv("SAME_ANSWER_MODEL", "model-under-test")
    client = FakeClient(stop_reason="max_tokens")
    result = run_bench(module.make_agent(client=client), CORPUS.limited(limit=1))
    assert "cut off" in next(iter(result.baseline.values())).error


def test_output_pasted_in_the_readme_is_what_the_cli_prints(capsys):
    """Numbers in the README must be real output of the fake agents, byte for byte."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^```\n\$ same-answer (.+?)\n(.*?)^```$", readme, re.M | re.S)
    assert len(blocks) >= 3
    for command, printed in blocks:
        assert main(shlex.split(command)) == 0
        assert capsys.readouterr().out == printed, command
