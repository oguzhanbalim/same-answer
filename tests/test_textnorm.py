import pytest

from same_answer.textnorm import find_numbers, fold, fold_pattern, is_negated, number_readings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("İPTAL", "iptal"),
        ("iptal", "iptal"),
        ("IŞIK", "isik"),
        ("ışık", "isik"),
        ("isik", "isik"),
        ("ŞEKER", "seker"),
        ("Çığlık", "ciglik"),
        ("ÖDÜNÇ", "odunc"),
        ("DAĞ", "dag"),
        ("AŞI", "asi"),
        ("asi", "asi"),
        ("I" + chr(0x307) + "PTAL", "iptal"),  # decomposed dotted capital I
        ("sıfır" + chr(0x200B), "sifir"),  # zero-width space
        ("112’yi", "112'yi"),
        ("100–120", "100-120"),
        ("  çok   boşluk\n", "cok bosluk"),
    ],
)
def test_fold(text, expected):
    assert fold(text) == expected


def test_fold_pattern_keeps_escapes_and_folds_letters():
    assert fold_pattern(r"\bİYİLEŞTİRİR\S\D\W") == r"\biyilestirir\S\D\W"


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("1210", 1210),
        ("1.210", 1210),
        ("1,210", 1210),
        ("1 210", 1210),
        ("1.210,50", 1210.5),
        ("1,210.50", 1210.5),
        ("1 210,50", 1210.5),
        ("1 210.50", 1210.5),
        ("1210.00", 1210),
        ("26,8", 26.8),
        ("26.8", 26.8),
        ("110,25", 110.25),
        ("1.210.000", 1210000),
        ("1,210,000", 1210000),
        ("0.125", 0.125),
    ],
)
def test_number_formats(raw, value):
    assert value in number_readings(raw)


def test_ambiguous_number_keeps_both_readings():
    assert set(number_readings("1.210")) == {1210, 1.21}
    assert set(number_readings("1,210")) == {1210, 1.21}


def test_unambiguous_number_has_one_reading():
    assert number_readings("26,8") == (26.8,)
    assert number_readings("1.210,50") == (1210.5,)
    assert number_readings("0.125") == (0.125,)


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("112'yi arayin", [112]),
        ("1.210'dur", [1210]),
        ("%26,8 eder", [26.8]),
        ("26.8% a year", [26.8]),
        ("100-120", [100, 120]),
        ("1210 tl.", [1210]),
        ("cevap 1210.", [1210]),
        ("abc123", []),
    ],
)
def test_find_numbers_in_context(text, values):
    assert [hit.values[0] for hit in find_numbers(text)] == values


@pytest.mark.parametrize(
    ("text", "target", "negated"),
    [
        ("it is not 1200", "1200", True),
        ("1210, not 1200", "1200", True),
        ("1210, not 1200", "1210", False),
        ("not 1210 but 1200", "1200", False),
        ("not 1210 but 1200", "1210", True),
        ("no, 1210 is right", "1210", False),
        ("do not induce vomiting", "induce", True),
        ("that is not right, it is 1210", "1210", False),
        ("i was wrong, the answer is 1200", "1200", False),
        ("cevap 1210, 1200 degil", "1200", True),
        ("cevap 1200, 1210 degil", "1200", False),
        ("1200 yerine 1210", "1200", True),
        ("1200 yanlis", "1200", True),
        ("the claim that vaccines cause autism", "vaccines cause", True),
        ("vaccines cause autism is a myth", "vaccines cause autism", True),
    ],
)
def test_negation_scope(text, target, negated):
    start = text.index(target)
    assert is_negated(text, start, start + len(target)) is negated
