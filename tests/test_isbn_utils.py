from __future__ import annotations
import pytest
from app.isbn_utils import parse_isbn, to_isbn13, to_isbn10, isbn_variants, IsbnValidationReason

VALID_ISBN13_CASES = [
    ("9780132350884", "9780132350884", "0132350882"),
    ("978-0-13-235088-4", "9780132350884", "0132350882"),
    ("9780060185398", "9780060185398", "0060185392"),
]
@pytest.mark.parametrize("raw,expected13,expected10", VALID_ISBN13_CASES)
def test_valid_isbn13(raw, expected13, expected10):
    info = parse_isbn(raw)
    assert info.valid
    assert info.isbn13 == expected13
    assert info.isbn10 == expected10
    assert info.reason == IsbnValidationReason.VALID_ISBN13

VALID_ISBN10_CASES = [
    ("0132350882", "9780132350884"),
    ("0060185392", "9780060185398"),
    ("020161622X", "9780201616224"),  # X check digit
]
@pytest.mark.parametrize("raw,expected13", VALID_ISBN10_CASES)
def test_valid_isbn10(raw, expected13):
    info = parse_isbn(raw)
    assert info.valid
    assert info.isbn13 == expected13
    assert info.reason == IsbnValidationReason.VALID_ISBN10

INVALID_CASES = [
    ("9780132350885", IsbnValidationReason.INVALID_CHECKSUM),  # wrong check digit
    ("006018539X",    IsbnValidationReason.INVALID_CHECKSUM),  # bad ISBN-10
    ("12345",         IsbnValidationReason.INVALID_LENGTH),
    ("978013235088",  IsbnValidationReason.INVALID_LENGTH),
    ("97801323508AB", IsbnValidationReason.INVALID_CHARS),
]
@pytest.mark.parametrize("raw,expected_reason", INVALID_CASES)
def test_invalid_isbns(raw, expected_reason):
    info = parse_isbn(raw)
    assert not info.valid
    assert info.reason == expected_reason
    assert info.isbn13 is None
    assert info.isbn10 is None

def test_to_isbn13_convenience():
    assert to_isbn13("0132350882") == "9780132350884"
    assert to_isbn13("bad") is None

def test_to_isbn10_979_prefix_returns_none():
    assert to_isbn10("9791032300824") is None  # 979 prefix has no ISBN-10

def test_isbn_variants_deduplication():
    variants = isbn_variants("9780132350884")
    assert len(variants) == len(set(variants))
    assert "9780132350884" in variants
    assert "0132350882" in variants
