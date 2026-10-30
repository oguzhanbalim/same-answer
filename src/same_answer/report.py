"""Plain-text reports. They say what the evidence allows and stop there."""

from __future__ import annotations

from collections.abc import Sequence

from same_answer.analysis import (
    LENGTH_BAND,
    AgentContrast,
    Analysis,
    Comparison,
    PressureRow,
    not_pressured,
    outcome_counts,
)
from same_answer.harness import BenchResult, Trial
from same_answer.scoring import (
    CORRECT,
    HEDGED,
    OUTCOMES,
    PARTIAL,
    REFUSED,
    UNSCORABLE,
    WRONG,
)
from same_answer.stats import (
    ALPHA,
    GAP,
    MAX_EXCLUDED_SHARE,
    MIN_DISCORDANT,
    NO_CLAIM,
    REVERSE_GAP,
    TOO_FEW,
    TOO_UNSCORABLE,
    wilson,
)

DEMO_NOTICE = (
    "Demo agent: a fake, rule-based agent with no model behind it. "
    "These numbers test the bench and say nothing about any real model."
)
NOT_MEDICAL = "Not medical advice. The questions test agents; they do not advise anyone."
NOT_PROOF = (
    "A gap is a thing to investigate. It is not proof of intent, and it does not say why the "
    "replies differ."
)
VERDICT_WORDS = {
    GAP: "GAP",
    REVERSE_GAP: "reverse gap",
    NO_CLAIM: "no claim",
    TOO_FEW: "too few",
    TOO_UNSCORABLE: "unscorable",
}
UNSCORABLE_WARNING_POINTS = 0.05


def _pct(value: float | None) -> str:
    return " n/a" if value is None else f"{value:4.0%}"


def _signed(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:+5.0%}"


def _ci(interval: tuple[float, float] | None) -> str:
    if interval is None:
        return "[  n/a  ]"
    low, high = interval
    return f"[{low:4.0%},{high:4.0%}]"


def _rate_ci(k: int, n: int) -> str:
    return f"{_pct(k / n if n else None)} {_ci(wilson(k, n))}"


def _p(value: float) -> str:
    return "<.001" if value < 0.001 else f"{value:5.3f}"


def _outcome_line(label: str, trials: Sequence[Trial]) -> str:
    counts = outcome_counts(trials)
    n = len(trials)
    return (
        f"{label:22} {n:6} {counts[CORRECT]:7} {counts[PARTIAL]:7} {counts[WRONG]:5} "
        f"{counts[REFUSED]:7} {counts[HEDGED]:6} {counts[UNSCORABLE]:10}  "
        f"{_rate_ci(counts[CORRECT], n)}"
    )


def _outcomes(result: BenchResult, *, detail: bool) -> list[str]:
    lines = [
        f"{'condition':22} {'asked':>6} {'correct':>7} {'partial':>7} {'wrong':>5} "
        f"{'refused':>7} {'hedged':>6} {'unscorable':>10}  correct rate [95% CI]"
    ]
    for language in result.languages:
        trials = [t for (_, lang), t in result.baseline.items() if lang == language]
        lines.append(_outcome_line(f"baseline {language}", trials))
    if result.identity:
        for language in result.languages:
            trials = [t for (_, lang, _v), t in result.identity.items() if lang == language]
            lines.append(_outcome_line(f"with identity {language}", trials))
    if detail:
        categories = sorted({t.category for t in result.baseline.values()})
        for language in result.languages:
            for category in categories:
                trials = [
                    t
                    for (_, lang), t in result.baseline.items()
                    if lang == language and t.category == category
                ]
                lines.append(_outcome_line(f"{language} {category}", trials))
    return lines


def _comparison_line(c: Comparison) -> str:
    if c.is_rate:
        base = f"{_pct(c.base_rate)} {_ci(c.base_ci)}"
        variant = f"{_pct(c.variant_rate)} {_ci(c.variant_ci)}"
        change = _signed(c.diff)
    else:
        ratio = "n/a" if c.median_ratio is None else f"{c.median_ratio:.2f}"
        base = ""
        variant = f"ratio {ratio}"
        change = "     "
    return (
        f"{c.metric:13} {c.pairs:5} {c.excluded:4}  {base:16} {variant:16} {change} "
        f"{c.worse:5} {c.better:6} {_p(c.p):>6} {_p(c.p_holm):>6}  {VERDICT_WORDS[c.verdict]}"
    )


def _comparison_header(base: str, variant: str) -> str:
    return (
        f"{'metric':13} {'pairs':>5} {'excl':>4}  {base:16} {variant:16} {'diff':>5} "
        f"{'worse':>5} {'better':>6} {'p':>6} {'Holm':>6}  verdict"
    )


def _holm_line(family: int) -> str:
    return (
        f"Holm correction: applied across {family} tests in this probe, "
        f"claims need Holm p < {ALPHA:g}."
    )


def _language_block(comparisons: list[Comparison]) -> list[str]:
    lines = [
        "probe language: the same question in Turkish and in English",
        _comparison_header("en [95% CI]", "tr [95% CI]"),
    ]
    lines += [_comparison_line(c) for c in comparisons]
    lines.append(
        f"length: Turkish against English, adjusted by each question's two reference answers. "
        f"A pair counts only if more than {LENGTH_BAND:.0%} apart."
    )
    lines.append(_holm_line(comparisons[0].family))
    return lines


def _identity_cell(c: Comparison) -> str:
    return f"{_signed(c.diff)} {_p(c.p_holm):>5}"


def _identity_block(comparisons: list[Comparison], *, detail: bool) -> list[str]:
    lines = [
        "probe identity: a short self-description before the question, against no description",
        f"{'variant':18} {'lang':4} {'pairs':>5} {'excl':>4}  {'correct [95% CI]':17}"
        f"{'diff  Holm':>11} {'answered':>11} {'safety':>11}  {'length Holm':>12}  verdict",
    ]
    grouped: dict[tuple[str, str], dict[str, Comparison]] = {}
    for c in comparisons:
        grouped.setdefault((c.label, c.language), {})[c.metric] = c
    for (variant, language), metrics in grouped.items():
        correct = metrics["correct"]
        length = metrics["length"]
        ratio = "n/a" if length.median_ratio is None else f"{length.median_ratio:.2f}"
        flagged = [
            f"{m.metric}: {VERDICT_WORDS[m.verdict]}"
            for m in metrics.values()
            if m.verdict in (GAP, REVERSE_GAP, TOO_UNSCORABLE)
        ]
        lines.append(
            f"{variant:18} {language:4} {correct.pairs:5} {correct.excluded:4}  "
            f"{_pct(correct.variant_rate)} {_ci(correct.variant_ci):12} "
            f"{_identity_cell(correct):>11} {_identity_cell(metrics['answered']):>11} "
            f"{_identity_cell(metrics['safety_facts']):>11}  "
            f"{ratio:>5} {_p(length.p_holm):>5}   {'; '.join(flagged) or 'no claim'}"
        )
    if detail:
        lines += ["", _comparison_header("baseline", "variant")]
        for (variant, language), metrics in grouped.items():
            lines.append(f"{variant} {language}")
            lines += [_comparison_line(m) for m in metrics.values()]
    lines.append(
        "diff: variant minus baseline, in percentage points. length: median ratio to baseline."
    )
    lines.append(_holm_line(comparisons[0].family))
    return lines


def _pressure_line(row: PressureRow) -> str:
    c = row.comparison
    tail = "control"
    if c is not None:
        tail = (
            f"{c.worse:5} {c.better:6} {_p(c.p):>6} {_p(c.p_holm):>6}  {VERDICT_WORDS[c.verdict]}"
        )
    return (
        f"{row.form:10} {row.language:4} {row.pushed:6} {row.held:5} {row.flipped:7} "
        f"{row.softened:8} {row.unscorable:10}  {_pct(row.flip_rate)} {_ci(row.flip_ci):12} {tail}"
    )


def _pressure_block(result: BenchResult, rows: list[PressureRow]) -> list[str]:
    lines = [
        "probe pressure: pushback with no new evidence, applied only after a correct first answer",
        f"{'form':10} {'lang':4} {'pushed':>6} {'held':>5} {'flipped':>7} {'softened':>8} "
        f"{'unscorable':>10}  {'flip rate [95% CI]':17} {'worse':>5} {'better':>6} "
        f"{'p':>6} {'Holm':>6}  verdict",
    ]
    lines += [_pressure_line(row) for row in rows]
    lines.append(
        "worse: gave way (flipped or softened) under this form but not under the neutral control."
    )
    for language, counts in not_pressured(result).items():
        parts = "  ".join(f"{name} {counts[name]}" for name in OUTCOMES if name in counts)
        lines.append(f"first answers not pressured, not counted as flips, {language}: {parts}")
    tested = [row.comparison.family for row in rows if row.comparison]
    if tested:
        lines.append(_holm_line(tested[0]))
    return lines


def _claim_line(c: Comparison) -> str:
    where = f"{c.label} {c.language}" if c.probe != "language" else c.label
    if c.metric == "held":
        what = "gives way more than under the neutral control"
        if c.verdict == REVERSE_GAP:
            what = "gives way less than under the neutral control"
        return f"  pressure, {where}: {what} ({c.worse} against {c.better}, Holm p {_p(c.p_holm)})"
    direction = "worse" if c.verdict == GAP else "better"
    return (
        f"  {c.probe}, {where}, {c.metric}: variant {direction} "
        f"({c.worse} worse, {c.better} better, Holm p {_p(c.p_holm)})"
    )


def _refused_summary(analysis: Analysis) -> list[str]:
    rows = [*analysis.language, *analysis.identity]
    rows += [p.comparison for p in analysis.pressure if p.comparison]
    few = sum(c.verdict == TOO_FEW for c in rows)
    weak = sum(c.verdict == NO_CLAIM for c in rows)
    bad = sum(c.verdict == TOO_UNSCORABLE for c in rows)
    return [
        f"no claim made for {few + weak + bad} of {len(rows)} tests: "
        f"{few} had under {MIN_DISCORDANT} discordant pairs, so no result could reach "
        f"significance; {weak} did not survive correction; {bad} had over "
        f"{MAX_EXCLUDED_SHARE:.0%} of pairs unscorable.",
        "No claim does not mean no gap. It means this evidence does not show one.",
    ]


def _unscorable_lines(result: BenchResult, *, show: bool) -> list[str]:
    trials = [*result.baseline.values(), *result.identity.values()]
    lines = []
    shares: dict[str, float] = {}
    for language in result.languages:
        subset = [t for t in trials if t.language == language]
        bad = [t for t in subset if t.score.outcome == UNSCORABLE]
        shares[language] = len(bad) / len(subset) if subset else 0.0
        lines.append(
            f"unscorable first answers, {language}: {len(bad)} of {len(subset)} "
            f"({shares[language]:.0%}), reported and left out of the paired tests"
        )
    if len(shares) == 2 and abs(shares["en"] - shares["tr"]) > UNSCORABLE_WARNING_POINTS:
        lines.append(
            "WARNING: the key misses more replies in one language. Read the language gap with "
            "care: the patterns may be failing, not the agent."
        )
    if show:
        for t in trials:
            if t.score.outcome == UNSCORABLE:
                reply = (t.reply or t.error or "").replace("\n", " ")[:100]
                where = f"{t.question_id} {t.language} {t.variant}"
                lines.append(f"  {where} {t.score.reason}: {reply}")
    return lines


def render(
    result: BenchResult,
    analysis: Analysis,
    *,
    detail: bool = False,
    show_unscorable: bool = False,
) -> str:
    lines = [
        f"agent: {result.agent}",
        f"questions: {len(result.corpus.questions)}  languages: {', '.join(result.languages)}  "
        f"agent calls: {result.calls}",
        "",
        *_outcomes(result, detail=detail),
    ]
    if analysis.language:
        lines += ["", *_language_block(analysis.language)]
    elif "language" in result.probes:
        lines += ["", "probe language: not run, it needs both languages."]
    if analysis.identity:
        lines += ["", *_identity_block(analysis.identity, detail=detail)]
    if analysis.pressure:
        lines += ["", *_pressure_block(result, analysis.pressure)]
    lines += ["", *_unscorable_lines(result, show=show_unscorable), ""]
    claims = analysis.claims
    if claims:
        lines.append(f"claims: {len(claims)}")
        lines += [_claim_line(c) for c in claims]
    else:
        lines.append("claims: none")
    lines += _refused_summary(analysis)
    lines += [NOT_PROOF, NOT_MEDICAL]
    return "\n".join(lines)


def render_contrasts(contrasts: Sequence[AgentContrast]) -> str:
    lines = [
        f"{'agent a':18} {'agent b':18} {'metric':8} {'pairs':>5} {'a ahead':>7} {'b ahead':>7} "
        f"{'p':>6} {'Holm':>6}  result"
    ]
    claims = []
    for c in contrasts:
        if c.verdict == GAP:
            result = f"{c.b} ahead"
        elif c.verdict == REVERSE_GAP:
            result = f"{c.a} ahead"
        else:
            result = f"no ranking ({VERDICT_WORDS[c.verdict]})"
        if c.verdict in (GAP, REVERSE_GAP):
            claims.append(c)
        lines.append(
            f"{c.a:18} {c.b:18} {c.metric:8} {c.pairs:5} {c.a_ahead:7} {c.b_ahead:7} "
            f"{_p(c.p):>6} {_p(c.p_holm):>6}  {result}"
        )
    lines.append(
        f"Holm correction: applied across {len(contrasts)} tests, claims need Holm p < {ALPHA:g}."
    )
    if not claims:
        lines.append("No ranking: the evidence here does not separate these agents.")
    lines.append(
        "No overall rank is given. Combining metrics would take a weight, and a weight is an "
        "opinion. A lead on one metric on these questions says nothing wider."
    )
    return "\n".join(lines)
