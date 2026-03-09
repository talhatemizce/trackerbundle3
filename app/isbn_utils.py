"""
TrackerBundle3 — Merkezi ISBN Utility Modülü
============================================
ISBN-10 / ISBN-13 doğrulama, dönüşüm, normalize etme.
Tüm ISBN işlemleri bu modülden yapılmalı — dağınık dönüşümler kaldırıldı.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional, List


class IsbnValidationReason(str, Enum):
    VALID_ISBN13      = "valid_isbn13"
    VALID_ISBN10      = "valid_isbn10"
    INVALID_CHECKSUM  = "invalid_checksum"
    INVALID_LENGTH    = "invalid_length"
    INVALID_CHARS     = "invalid_chars"


class IsbnInfo:
    def __init__(
        self,
        raw: str,
        normalized: Optional[str],
        isbn13: Optional[str],
        isbn10: Optional[str],
        valid: bool,
        reason: IsbnValidationReason,
    ):
        self.raw        = raw
        self.normalized = normalized   # stripsiz, büyük harf
        self.isbn13     = isbn13
        self.isbn10     = isbn10
        self.valid      = valid
        self.reason     = reason

    def variants(self) -> List[str]:
        v = set()
        if self.isbn13: v.add(self.isbn13)
        if self.isbn10: v.add(self.isbn10)
        return list(v)


def _clean(isbn: str) -> str:
    return isbn.replace("-", "").replace(" ", "").upper().strip()


def _isbn10_check(s: str) -> bool:
    """ISBN-10 checksum doğrula (X son rakam olabilir)."""
    if len(s) != 10:
        return False
    total = 0
    for i, ch in enumerate(s):
        if i == 9 and ch == "X":
            val = 10
        elif ch.isdigit():
            val = int(ch)
        else:
            return False
        total += (10 - i) * val
    return total % 11 == 0


def _isbn13_check(s: str) -> bool:
    """ISBN-13 checksum doğrula."""
    if len(s) != 13 or not s.isdigit():
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
    return total % 10 == 0


def _isbn10_to_13(s: str) -> Optional[str]:
    """ISBN-10 → ISBN-13 (978 prefix). Checksum dahil."""
    if len(s) != 10:
        return None
    core = "978" + s[:9]
    if not core.isdigit():
        return None
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
    check = (10 - (total % 10)) % 10
    return core + str(check)


def _isbn13_to_10(s: str) -> Optional[str]:
    """ISBN-13 (978 prefix) → ISBN-10. Checksum dahil."""
    if len(s) != 13 or not s.startswith("978") or not s.isdigit():
        return None
    body = s[3:12]
    total = sum(int(c) * (10 - i) for i, c in enumerate(body))
    check = (11 - (total % 11)) % 11
    check_char = "X" if check == 10 else str(check)
    return body + check_char


def parse_isbn(raw: str) -> IsbnInfo:
    """
    Ham ISBN stringini parse et, validate et, hem ISBN-10 hem ISBN-13 döndür.

    Örnekler:
        parse_isbn("0060185392")  → valid ISBN-10, isbn13="9780060185398"
        parse_isbn("9780060185398") → valid ISBN-13, isbn10="0060185392"
        parse_isbn("006018539X")  → invalid checksum
        parse_isbn("12345")       → invalid length
    """
    s = _clean(raw)

    if not s:
        return IsbnInfo(raw, s, None, None, False, IsbnValidationReason.INVALID_LENGTH)

    # Sadece rakam + olası 'X' son karakter
    allowed = set("0123456789X")
    if not all(c in allowed for c in s):
        return IsbnInfo(raw, s, None, None, False, IsbnValidationReason.INVALID_CHARS)

    if len(s) == 10:
        if not _isbn10_check(s):
            return IsbnInfo(raw, s, None, None, False, IsbnValidationReason.INVALID_CHECKSUM)
        isbn13 = _isbn10_to_13(s)
        return IsbnInfo(raw, s, isbn13, s, True, IsbnValidationReason.VALID_ISBN10)

    elif len(s) == 13:
        if not _isbn13_check(s):
            return IsbnInfo(raw, s, None, None, False, IsbnValidationReason.INVALID_CHECKSUM)
        isbn10 = _isbn13_to_10(s)
        return IsbnInfo(raw, s, s, isbn10, True, IsbnValidationReason.VALID_ISBN13)

    else:
        return IsbnInfo(raw, s, None, None, False, IsbnValidationReason.INVALID_LENGTH)


def to_isbn13(raw: str) -> Optional[str]:
    """Kısa yol: herhangi bir ISBN → ISBN-13. Geçersizse None."""
    info = parse_isbn(raw)
    return info.isbn13 if info.valid else None


def to_isbn10(raw: str) -> Optional[str]:
    """Kısa yol: herhangi bir ISBN → ISBN-10. 979-prefix'li → None."""
    info = parse_isbn(raw)
    return info.isbn10 if info.valid else None


def isbn_variants(raw: str) -> List[str]:
    """Tüm geçerli varyantları döndür (ISBN-10 + ISBN-13)."""
    info = parse_isbn(raw)
    return info.variants()
