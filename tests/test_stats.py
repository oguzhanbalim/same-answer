import pytest

from same_answer.stats import (
    GAP,
    MIN_DISCORDANT,
    NO_CLAIM,
    REVERSE_GAP,
    TOO_FEW,
    TOO_UNSCORABLE,
    holm,
    sign_test,
    verdict,
    wilson,
)


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        (0, 10, 0.0, 0.2775),
        (5, 10, 0.2366, 0.7634),
        (10, 10, 0.7225, 1.0),
        (1, 20, 0.0089, 0.2361),
        (44, 44, 0.9197, 1.0),
    ],
)
def test_wilson_known_values(k, n, low, high):
    assert wilson(k, n) == pytest.approx((low, high), abs=5e-5)


def test_wilson_with_no_data_is_none():
    assert wilson(0, 0) is None


def test_wilson_rejects_impossible_counts():
    with pytest.raises(ValueError):
        wilson(3, 2)


@pytest.mark.parametrize(
    ("plus", "minus", "p"),
    [
        (0, 0, 1.0),
        (1, 1, 1.0),
        (10, 10, 1.0),
        (5, 0, 0.0625),
        (6, 0, 0.03125),
        (0, 6, 0.03125),
        (8, 2, 0.109375),
        (9, 1, 0.021484375),
        (20, 0, 2 * 0.5**20),
    ],
)
def test_sign_test_exact_values(plus, minus, p):
    assert sign_test(plus, minus) == pytest.approx(p, rel=1e-12)


def test_sign_test_rejects_negative_counts():
    with pytest.raises(ValueError):
        sign_test(-1, 2)


def test_min_discordant_is_the_first_count_that_can_reach_significance():
    assert sign_test(MIN_DISCORDANT - 1, 0) >= 0.05
    assert sign_test(MIN_DISCORDANT, 0) < 0.05


def test_holm_known_values():
    assert holm([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_holm_keeps_input_order_and_order_of_size():
    pvalues = [0.5, 0.001, 0.02, 0.01]
    adjusted = holm(pvalues)
    by_size = [a for _, a in sorted(zip(pvalues, adjusted, strict=True))]
    assert by_size == sorted(by_size)
    assert adjusted[1] == pytest.approx(0.004)


def test_holm_caps_at_one_and_handles_edges():
    assert holm([0.9, 0.8]) == [1.0, 1.0]
    assert holm([0.04]) == [0.04]
    assert holm([]) == []


def test_holm_can_undo_a_result_that_looked_significant():
    assert holm([0.03] * 80)[0] > 0.05


def test_verdict_gap_and_reverse_gap():
    assert verdict(20, 0, 0, 44, 0.001) == GAP
    assert verdict(0, 20, 0, 44, 0.001) == REVERSE_GAP


def test_verdict_refuses_with_too_few_discordant_pairs():
    assert verdict(5, 0, 0, 44, 0.0625) == TOO_FEW
    # Even if a caller hands in a small p, five pairs cannot carry a claim.
    assert verdict(5, 0, 0, 44, 0.001) == TOO_FEW


def test_verdict_refuses_when_correction_removes_significance():
    assert verdict(8, 1, 0, 44, 0.2) == NO_CLAIM


def test_verdict_refuses_when_too_much_is_unscorable():
    assert verdict(30, 0, 11, 33, 0.001) == TOO_UNSCORABLE
    assert verdict(30, 0, 8, 36, 0.001) == GAP
