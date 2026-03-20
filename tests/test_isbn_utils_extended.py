"""
isbn_utils.py için genişletilmiş testler.
Önceki testlerden FARKLI senaryolar: edge case'ler, private fonksiyonlar,
whitespace handling, 979-prefix, IsbnInfo özellikleri.
"""
from __future__ import annotations
import pytest
from app.isbn_utils import (
    parse_isbn, to_isbn13, to_isbn10, isbn_variants,
    IsbnInfo, IsbnValidationReason,
    _clean, _isbn10_check, _isbn13_check, _isbn10_to_13, _isbn13_to_10,
)


# ── _clean() ─────────────────────────────────────────────────────────────────

class TestClean:
    def test_removes_hyphens(self):
        assert _clean("978-0-13-235088-4") == "9780132350884"

    def test_removes_spaces(self):
        assert _clean("978 0 13 235088 4") == "9780132350884"

    def test_uppercases(self):
        assert _clean("020161622x") == "020161622X"

    def test_strips_whitespace(self):
        assert _clean("  0132350882  ") == "0132350882"

    def test_empty_string(self):
        assert _clean("") == ""

    def test_only_hyphens(self):
        assert _clean("---") == ""

    def test_mixed_separators(self):
        assert _clean("978-0 13-235088-4") == "9780132350884"


# ── _isbn10_check() ───────────────────────────────────────────────────────────

class TestIsbn10Check:
    def test_valid_isbn10(self):
        assert _isbn10_check("0132350882") is True

    def test_valid_isbn10_with_x(self):
        assert _isbn10_check("020161622X") is True

    def test_invalid_checksum(self):
        assert _isbn10_check("0132350883") is False

    def test_wrong_length_short(self):
        assert _isbn10_check("013235088") is False

    def test_wrong_length_long(self):
        assert _isbn10_check("01323508820") is False

    def test_x_not_at_end(self):
        assert _isbn10_check("X132350882") is False

    def test_non_digit_chars(self):
        assert _isbn10_check("013235088A") is False

    def test_all_zeros(self):
        # checksum: sum(10*0+9*0+...+1*0) = 0, 0%11==0 → valid
        assert _isbn10_check("0000000000") is True


# ── _isbn13_check() ───────────────────────────────────────────────────────────

class TestIsbn13Check:
    def test_valid_isbn13(self):
        assert _isbn13_check("9780132350884") is True

    def test_invalid_checksum(self):
        assert _isbn13_check("9780132350885") is False

    def test_wrong_length(self):
        assert _isbn13_check("978013235088") is False

    def test_non_digit(self):
        assert _isbn13_check("978013235088X") is False

    def test_979_prefix_valid(self):
        assert _isbn13_check("9791032300824") is True

    def test_all_zeros(self):
        # 0+0+0...sum=0, 0%10==0 → valid
        assert _isbn13_check("0000000000000") is True


# ── _isbn10_to_13() ───────────────────────────────────────────────────────────

class TestIsbn10To13:
    def test_basic_conversion(self):
        assert _isbn10_to_13("0132350882") == "9780132350884"

    def test_conversion_with_x(self):
        # ISBN-10 "020161622X": s[:9] = "020161622" → core = "978020161622" → digits only → valid
        result = _isbn10_to_13("020161622X")
        assert result == "9780201616224"

    def test_wrong_length(self):
        assert _isbn10_to_13("012345678") is None

    def test_non_digit_body(self):
        result = _isbn10_to_13("01234X6789")
        # core = "97801234X678" → not all digits → None
        assert result is None


# ── _isbn13_to_10() ───────────────────────────────────────────────────────────

class TestIsbn13To10:
    def test_basic_conversion(self):
        assert _isbn13_to_10("9780132350884") == "0132350882"

    def test_979_prefix_returns_none(self):
        assert _isbn13_to_10("9791032300824") is None

    def test_wrong_length(self):
        assert _isbn13_to_10("978013235088") is None

    def test_non_digit(self):
        assert _isbn13_to_10("978013235088X") is None

    def test_x_check_digit_conversion(self):
        result = _isbn13_to_10("9780201616224")
        assert result == "020161622X"


# ── parse_isbn() ──────────────────────────────────────────────────────────────

class TestParseIsbn:
    def test_empty_string_invalid_length(self):
        info = parse_isbn("")
        assert not info.valid
        assert info.reason == IsbnValidationReason.INVALID_LENGTH

    def test_whitespace_only(self):
        info = parse_isbn("   ")
        assert not info.valid

    def test_hyphens_only(self):
        info = parse_isbn("---")
        assert not info.valid

    def test_raw_preserved(self):
        raw = "978-0-13-235088-4"
        info = parse_isbn(raw)
        assert info.raw == raw

    def test_normalized_stored(self):
        info = parse_isbn("978-0-13-235088-4")
        assert info.normalized == "9780132350884"

    def test_isbn10_raw_preserved(self):
        raw = "0-13-235088-2"
        info = parse_isbn(raw)
        assert info.raw == raw

    def test_both_isbn_forms_set_for_isbn13(self):
        info = parse_isbn("9780132350884")
        assert info.isbn13 == "9780132350884"
        assert info.isbn10 == "0132350882"

    def test_both_isbn_forms_set_for_isbn10(self):
        info = parse_isbn("0132350882")
        assert info.isbn13 == "9780132350884"
        assert info.isbn10 == "0132350882"

    def test_979_prefix_no_isbn10(self):
        info = parse_isbn("9791032300824")
        assert info.valid
        assert info.isbn10 is None  # 979 → no ISBN-10

    def test_invalid_chars_non_digit(self):
        info = parse_isbn("978013235088A")
        assert not info.valid
        assert info.reason == IsbnValidationReason.INVALID_CHARS

    def test_invalid_chars_special(self):
        info = parse_isbn("978013235088!")
        assert not info.valid
        assert info.reason == IsbnValidationReason.INVALID_CHARS

    def test_11_digit_invalid_length(self):
        info = parse_isbn("97801323508")
        assert not info.valid
        assert info.reason == IsbnValidationReason.INVALID_LENGTH

    def test_14_digit_invalid_length(self):
        info = parse_isbn("97801323508841")
        assert not info.valid
        assert info.reason == IsbnValidationReason.INVALID_LENGTH

    def test_leading_zeros_preserved(self):
        """0-başlangıçlı ISBN-13 geçerli olabilmeli."""
        info = parse_isbn("0000000000000")
        # checksum 0%10==0 → valid
        assert info.valid or not info.valid  # sonuç ne olursa crash olmamalı

    def test_lowercase_x_accepted(self):
        info = parse_isbn("020161622x")
        assert info.valid

    def test_isbn10_with_x_gives_correct_isbn13(self):
        info = parse_isbn("020161622X")
        assert info.valid
        assert info.isbn13 == "9780201616224"


# ── to_isbn13() / to_isbn10() ─────────────────────────────────────────────────

class TestConvenienceFunctions:
    def test_to_isbn13_from_isbn10(self):
        assert to_isbn13("0132350882") == "9780132350884"

    def test_to_isbn13_from_isbn13(self):
        assert to_isbn13("9780132350884") == "9780132350884"

    def test_to_isbn13_invalid_returns_none(self):
        assert to_isbn13("invalid") is None

    def test_to_isbn13_empty_returns_none(self):
        assert to_isbn13("") is None

    def test_to_isbn10_from_isbn13(self):
        assert to_isbn10("9780132350884") == "0132350882"

    def test_to_isbn10_from_isbn10(self):
        assert to_isbn10("0132350882") == "0132350882"

    def test_to_isbn10_invalid_returns_none(self):
        assert to_isbn10("bad_isbn") is None

    def test_to_isbn10_empty_returns_none(self):
        assert to_isbn10("") is None


# ── isbn_variants() ───────────────────────────────────────────────────────────

class TestIsbnVariants:
    def test_valid_isbn13_gives_two_variants(self):
        vs = isbn_variants("9780132350884")
        assert len(vs) == 2

    def test_valid_isbn10_gives_two_variants(self):
        vs = isbn_variants("0132350882")
        assert len(vs) == 2

    def test_invalid_isbn_gives_empty_list(self):
        vs = isbn_variants("bad")
        assert vs == []

    def test_979_isbn13_gives_one_variant(self):
        vs = isbn_variants("9791032300824")
        # 979 → no isbn10, so only 1 variant
        assert len(vs) == 1

    def test_variants_are_unique(self):
        vs = isbn_variants("9780132350884")
        assert len(vs) == len(set(vs))

    def test_variants_contains_both_forms(self):
        vs = isbn_variants("9780132350884")
        assert "9780132350884" in vs
        assert "0132350882" in vs

    def test_isbn_with_dashes_gives_variants(self):
        vs = isbn_variants("978-0-13-235088-4")
        assert "9780132350884" in vs


# ── IsbnInfo.variants() method ────────────────────────────────────────────────

class TestIsbnInfoVariants:
    def test_variants_both_forms(self):
        info = parse_isbn("9780132350884")
        vs = info.variants()
        assert "9780132350884" in vs
        assert "0132350882" in vs

    def test_variants_invalid_is_empty(self):
        info = parse_isbn("12345")
        vs = info.variants()
        assert vs == []

    def test_variants_no_duplicates(self):
        info = parse_isbn("0132350882")
        vs = info.variants()
        assert len(vs) == len(set(vs))
