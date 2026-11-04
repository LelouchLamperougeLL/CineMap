"""Text normalisation shared by the file-name parser and the matcher."""

from __future__ import annotations

import re
import unicodedata

# Windows forbids \ / : * ? " < > | in file names, so people swap in look-alikes.
LOOKALIKES = {
    "꞉": ":",  # MODIFIER LETTER COLON
    "：": ":",  # FULLWIDTH COLON
    "∶": ":",  # RATIO
    "˸": ":",  # MODIFIER LETTER RAISED COLON
    "﹕": ":",  # SMALL COLON
    "？": "?",  # FULLWIDTH QUESTION MARK
    "﹖": "?",  # SMALL QUESTION MARK
    "∕": "/",  # DIVISION SLASH
    "⁄": "/",  # FRACTION SLASH
    "⧸": "/",  # BIG SOLIDUS
    "／": "/",  # FULLWIDTH SOLIDUS
    "⧹": "\\",  # BIG REVERSE SOLIDUS
    "＼": "\\",  # FULLWIDTH REVERSE SOLIDUS
    "＊": "*",  # FULLWIDTH ASTERISK
    "∗": "*",  # ASTERISK OPERATOR
    "＂": '"',  # FULLWIDTH QUOTATION MARK
    "＜": "<",  # FULLWIDTH LESS-THAN
    "＞": ">",  # FULLWIDTH GREATER-THAN
    "｜": "|",  # FULLWIDTH VERTICAL LINE
    "∣": "|",  # DIVIDES
}
_LOOKALIKE_RE = re.compile("[" + "".join(re.escape(c) for c in LOOKALIKES) + "]")

# Invisible direction marks that sneak in with right-to-left text.
_BIDI_RE = re.compile("[‎‏‪-‮⁦-⁩﻿]")
_JOINERS_RE = re.compile("[​-‍⁠]")

ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9",
         "xi": "11", "xii": "12", "xiii": "13"}
SMALL_ROMAN = {"i": "1", "v": "5", "x": "10"}
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
SEQUEL_MARKERS = {"part", "pt", "chapter", "vol", "volume", "episode", "ep", "book"}
ARTICLES = {"the", "a", "an", "le", "la", "les", "l", "el", "los", "las", "il", "lo", "gli",
            "der", "die", "das", "de", "het", "en"}

_SPECIAL_LETTERS = str.maketrans({
    "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
    "þ": "th", "Þ": "TH", "ð": "d", "Ð": "D", "œ": "oe", "Œ": "OE", "ħ": "h", "Ħ": "H",
    "ı": "i", "ß": "ss",
})
_APOSTROPHES = re.compile("['’‘ʼʻ`´]")


def fix_lookalikes(text: str) -> str:
    """Undo the look-alike characters used to dodge Windows file-name rules."""
    return _LOOKALIKE_RE.sub(lambda m: LOOKALIKES[m.group(0)], text)


def strip_invisibles(text: str) -> str:
    return _BIDI_RE.sub("", text)


def clean(text: str) -> str:
    """Tidy a human-readable string: NFC, look-alikes fixed, invisibles gone, spaces collapsed."""
    text = strip_invisibles(fix_lookalikes(unicodedata.normalize("NFC", text)))
    return " ".join(text.split())


def is_latin_letter(ch: str) -> bool:
    if ch.isascii():
        return True
    if 0x02B0 <= ord(ch) <= 0x02FF:  # spacing modifier letters (ʻ ʼ ...), used in romanisation
        return True
    return unicodedata.name(ch, "").startswith("LATIN")


def has_non_latin_letters(text: str) -> bool:
    return any(ch.isalpha() and not is_latin_letter(ch) for ch in text)


def _strip_latin_marks(text: str) -> str:
    out: list[str] = []
    base_is_latin = False
    for ch in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(ch):
            if base_is_latin:
                continue  # é -> e, but keep marks that belong to other scripts (が, ं ...)
        else:
            base_is_latin = is_latin_letter(ch)
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _split_scripts(text: str) -> str:
    """Put a space where Latin letters/digits meet another script: '無間道II' -> '無間道 II'."""
    out: list[str] = []
    prev = None
    for ch in text:
        if ch.isalnum():
            cls = "L" if is_latin_letter(ch) else "O"
            if prev is not None and cls != prev:
                out.append(" ")
            prev = cls
        else:
            prev = None
        out.append(ch)
    return "".join(out)


def fold(text: str) -> str:
    """Comparison key: lower case, no accents or punctuation, numerals unified.

    'Mission꞉ Impossible – Ghost Protocol' -> 'mission impossible ghost protocol'
    'Dune: Part One' -> 'dune part 1'; 'Face/Off' and 'Face-Off' -> 'face off'
    """
    if not text:
        return ""
    text = strip_invisibles(fix_lookalikes(text))
    text = _JOINERS_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text).translate(_SPECIAL_LETTERS)
    text = _strip_latin_marks(text).casefold()
    text = text.replace("&", " and ").replace("+", " and ")
    text = _APOSTROPHES.sub("", text)
    tokens = re.findall(r"[^\W_]+", _split_scripts(text))
    out: list[str] = []
    for i, tok in enumerate(tokens):
        prev = out[-1] if out else ""
        if tok in ROMAN:
            tok = ROMAN[tok]
        elif prev in SEQUEL_MARKERS and (tok in SMALL_ROMAN or tok in NUMBER_WORDS):
            tok = SMALL_ROMAN.get(tok) or NUMBER_WORDS[tok]
        elif tok.isdecimal():  # not isdigit(): ፩ or ² are "digits" that int() rejects
            tok = str(int(tok)) if len(tok) < 6 else tok
        out.append(tok)
    return " ".join(out)


def drop_article(folded: str) -> str:
    parts = folded.split(" ", 1)
    if len(parts) == 2 and parts[0] in ARTICLES:
        return parts[1]
    return folded


def numerals(folded: str) -> frozenset[str]:
    return frozenset(t for t in folded.split() if t.isdecimal())
