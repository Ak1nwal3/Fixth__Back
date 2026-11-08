import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
_SEPARATOR_RE = re.compile(r"[_\-/\\,;:]+")
_APOSTROPHE_RE = re.compile(r"['\u2019`]")
_EDGE_PUNCTUATION = " \t\r\n.,;:!?()[]{}\"'"


def clean_display(value: str) -> str:
    """Return a display-safe term without changing its meaning."""
    if not isinstance(value, str):
        value = str(value)
    value = unicodedata.normalize("NFKC", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def normalize_term(value: str) -> str:
    """Build the case-insensitive lookup key used for canonical terms.

    Separators are treated as spaces, apostrophes are removed, and common
    surrounding punctuation is ignored. Technical symbols such as ``+`` and
    ``#`` are preserved so ``C++``, ``C#``, and ``C`` stay distinct.
    """
    value = clean_display(value).casefold()
    value = _SEPARATOR_RE.sub(" ", value)
    value = _APOSTROPHE_RE.sub("", value)
    value = value.strip(_EDGE_PUNCTUATION)
    return _WHITESPACE_RE.sub(" ", value).strip()
