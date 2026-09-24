from __future__ import annotations

import re
from dataclasses import dataclass

_USERNAME_RE = r"[A-Za-z][A-Za-z0-9_]{4,31}"
_SEPARATOR_RE = r"\s*[,\-—–]?\s*"
_PHRASE_RE = r"[вВ]\s+[рР][аА][бБ][оО][тТ][уУ]"
_ASSIGNMENT_RE = re.compile(
    rf"^\s*@(?P<username>{_USERNAME_RE})"
    rf"{_SEPARATOR_RE}"
    rf"(?:(?:[вВ]\s+[рР][аА][бБ][оО][тТ][уУ])|"
    rf"(?:[вВ][оО][зЗ][ьЬ]\s+[вВ]\s+[рР][аА][бБ][оО][тТ][уУ]))"
    rf"\s*[.!]?\s*$"
)

@dataclass
class ParseResult:
    is_assignment: bool
    username: str | None


def parse_assignment(text: str | None) -> ParseResult:
    if not text:
        return ParseResult(False, None)
    match = _ASSIGNMENT_RE.match(text.strip())
    if not match:
        return ParseResult(False, None)
    return ParseResult(True, match.group("username"))
