"""Parse collection file names.

The convention (studied from files.csv):

    [YYYY] Title (Language; Native title, Romanization).ext

* The year is in square brackets at the start. A few names put it after the
  title instead: "Carlos (2010) - Part 1.mkv".
* The trailing parenthesis is a language tag only when it starts with a language
  name. It may hold several languages ("Icelandic; Kona fer í stríð, Ukrainian;
  «…»"), use "꞉" or ":" instead of ";", contain nested parentheses, or lack its
  closing parenthesis. Other parentheses belong to the title: "(500) Days of
  Summer", "BIRDMAN or (The Unexpected Virtue of Ignorance)".
* Characters Windows forbids are replaced by look-alikes ("꞉" for ":", "？" for
  "?"), and real titles may end in periods: "Mulholland Dr..mkv".
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

from .languages import LANGUAGE_CODES, codes_for
from .textnorm import clean, has_non_latin_letters

VIDEO_EXTENSIONS = {
    "mkv", "mp4", "m4v", "avi", "mov", "wmv", "mpg", "mpeg", "m2ts", "mts", "ts", "webm",
    "ogv", "ogm", "flv", "3gp", "divx", "xvid", "vob", "iso", "rmvb", "rm", "asf", "f4v",
}
JUNK_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "icon\r"}

_LANG_ALT = "|".join(sorted((re.escape(n) for n in LANGUAGE_CODES), key=len, reverse=True))
_LANG_ANCHOR = re.compile(rf"(?:^|[;,])\s*({_LANG_ALT})\s*(?=[;:,]|$)", re.IGNORECASE)
_LANG_HEAD = re.compile(rf"^\s*({_LANG_ALT})\s*(?:[;:,]|$)", re.IGNORECASE)

_PART_RE = re.compile(
    r"^(?P<base>.+?)(?:\s*[-–—:,]\s*|\s+)(?P<label>Part|Pt\.?|Chapter|Volume|Vol\.?)\s*"
    r"(?P<num>\d{1,2}|[IVX]{1,4}|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)\s*$",
    re.IGNORECASE,
)
_PART_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
               "eight": 8, "nine": 9, "ten": 10}
_ROMAN_VALUES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
                 "ix": 9, "x": 10}

_EDITION_RE = re.compile(
    r"director'?s cut|extended|theatrical|remaster|restored|uncut|unrated|final cut|"
    r"special edition|anniversary|criterion|imax|\b4k\b|\buhd\b|\bhdr\b|\b\d{3,4}p\b|blu-?ray|"
    r"web-?dl|webrip|\bx26[45]\b|\bhevc\b|\bremux\b|\bdvdrip\b|\bbdrip\b",
    re.IGNORECASE,
)
_TRAILING_GROUP = re.compile(r"^(?P<title>.*?)\s*(?P<group>[\(\[][^()\[\]]*[\)\]])\s*$")
_COPY_SUFFIX = re.compile(r"^(?P<title>.+?)(?:\s+-\s+Copy(?:\s*\(\d+\))?|\s*\((?P<n>\d)\))$")


@dataclass
class ParsedName:
    file: str
    stem: str
    ext: str
    is_video: bool
    title: str = ""
    year: int | None = None
    langs: list[str] = field(default_factory=list)
    lang_codes: list[str] = field(default_factory=list)
    natives: list[str] = field(default_factory=list)
    romanized: list[str] = field(default_factory=list)
    part: int | None = None
    base_title: str | None = None
    edition: str | None = None
    notes: list[str] = field(default_factory=list)
    skip_reason: str | None = None

    def to_dict(self) -> dict:
        d = {"title": self.title, "year": self.year}
        for key in ("langs", "lang_codes", "natives", "romanized", "notes"):
            if getattr(self, key):
                d[key] = list(getattr(self, key))
        for key in ("part", "base_title", "edition", "skip_reason"):
            if getattr(self, key) is not None:
                d[key] = getattr(self, key)
        return d


def _split_extension(name: str) -> tuple[str, str]:
    m = re.match(r"^(.*[^.\s].*?)\.([A-Za-z0-9]{1,5})$", name)
    if not m:
        return name, ""
    return m.group(1), m.group(2).lower()


def _extract_year(s: str) -> tuple[int | None, str]:
    max_year = _dt.date.today().year + 3
    m = re.match(r"^\[\s*(\d{4})\s*\]\s*(.*)$", s)
    if m and 1870 <= int(m.group(1)) <= max_year:
        return int(m.group(1)), m.group(2)
    m = re.match(r"^(.+?)\s*[\(\[]\s*(\d{4})\s*[\)\]]\s*(.*)$", s)
    if m and 1870 <= int(m.group(2)) <= max_year:
        rest = m.group(1)
        if m.group(3):
            rest = f"{rest} {m.group(3)}"
        return int(m.group(2)), rest
    return None, s


def _is_language_tag(content: str) -> bool:
    return bool(_LANG_HEAD.match(content))


def _split_trailing_tag(text: str) -> tuple[str, str | None, bool]:
    """Return (title part, tag content, tag was closed)."""
    t = text.rstrip()
    if t.endswith(")"):
        depth = 0
        for i in range(len(t) - 1, -1, -1):
            if t[i] == ")":
                depth += 1
            elif t[i] == "(":
                depth -= 1
                if depth == 0:
                    content = t[i + 1:-1]
                    if _is_language_tag(content):
                        return t[:i].rstrip(), content, True
                    break
    i = t.rfind("(")
    if i >= 0 and ")" not in t[i:] and _is_language_tag(t[i + 1:]):
        return t[:i].rstrip(), t[i + 1:], False
    return t, None, True


def _split_native(text: str) -> tuple[str | None, str | None]:
    text = text.strip(" ,;:")
    if not text:
        return None, None
    if has_non_latin_letters(text):
        for m in re.finditer(r",\s+", text):
            left, right = text[:m.start()].strip(), text[m.end():].strip()
            if right and has_non_latin_letters(left) and not has_non_latin_letters(right):
                return _unquote(left), _unquote(right)
    return _unquote(text), None


def _unquote(s: str) -> str:
    return s.strip().strip("«»\"“”„'‘’「」『』").strip()


def parse_tag(content: str) -> list[tuple[str, str | None, str | None]]:
    """'Icelandic; Kona fer í stríð, Ukrainian; «…»' -> [(lang, native, romanised), ...]"""
    anchors = list(_LANG_ANCHOR.finditer(content))
    groups = []
    for n, m in enumerate(anchors):
        end = anchors[n + 1].start() if n + 1 < len(anchors) else len(content)
        body = content[m.end():end]
        native, roman = _split_native(body)
        groups.append((" ".join(m.group(1).split()).title(), native, roman))
    return groups


def _part_number(token: str) -> int | None:
    t = token.lower().rstrip(".")
    if t.isdigit():
        return int(t)
    return _PART_WORDS.get(t) or _ROMAN_VALUES.get(t)


def parse(file_name: str) -> ParsedName:
    name = clean(file_name)
    stem, ext = _split_extension(name)
    p = ParsedName(file=file_name, stem=stem, ext=ext, is_video=ext in VIDEO_EXTENSIONS)
    if name.casefold() in JUNK_NAMES or name.startswith(("~$", ".")):
        p.is_video = False
    if not p.is_video:
        p.skip_reason = f"not a video file (.{ext})" if ext else "not a video file"
        p.title = stem
        return p

    p.year, rest = _extract_year(stem)
    if p.year is None:
        p.notes.append("no year in the name")

    # Copy markers and edition labels can follow the language tag ("[2016] Elle (French) (1).mkv",
    # "... (Japanese; ...) [1080p].mkv"); take them off first so the tag is still found.
    while True:
        m = _COPY_SUFFIX.match(rest)
        if m:
            p.notes.append(f"ignored copy marker in '{rest}'")
            rest = m.group("title").rstrip()
            continue
        m = _TRAILING_GROUP.match(rest)
        if m and _EDITION_RE.search(m.group("group")) and not _is_language_tag(m.group("group")[1:-1]):
            p.edition = m.group("group")[1:-1].strip()
            p.notes.append(f"edition label '{p.edition}' left out of the title")
            rest = m.group("title").rstrip()
            continue
        break

    title, tag, closed = _split_trailing_tag(rest)
    if tag is not None:
        if not closed:
            p.notes.append("language tag has no closing parenthesis")
        for lang, native, roman in parse_tag(tag):
            p.langs.append(lang)
            for code in codes_for(lang):
                if code not in p.lang_codes:
                    p.lang_codes.append(code)
            if native:
                p.natives.append(native)
            if roman:
                p.romanized.append(roman)

    # Leftovers from copying/downloading ("Title - Copy", "Title (1)") and edition labels.
    m = _COPY_SUFFIX.match(title)
    if m:
        p.notes.append(f"ignored copy marker in '{title}'")
        title = m.group("title").rstrip()
    m = _TRAILING_GROUP.match(title)
    if m and _EDITION_RE.search(m.group("group")):
        p.edition = m.group("group")[1:-1].strip()
        p.notes.append(f"edition label '{p.edition}' left out of the title")
        title = m.group("title").rstrip()

    p.title = title.strip(" -–—")
    m = _PART_RE.match(p.title)
    if m:
        number = _part_number(m.group("num"))
        if number:
            p.part = number
            p.base_title = m.group("base").strip(" -–—:,")
    if not p.title:
        p.skip_reason = "no title found in the name"
    return p
