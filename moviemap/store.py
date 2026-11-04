"""Reading and writing the project's files (all UTF-8, written atomically)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .config import CORRECTIONS_PATH, FILMS_DIR, MATCHES_PATH


# --- atomic text/JSON writing ---------------------------------------------------
def write_text_atomic(path: Path, text: str) -> bool:
    """Write only if the content changed. Returns True when the file was written."""
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def dump_json(data: object, *, indent: int | None = 1) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False) + "\n"


def write_json(path: Path, data: object, *, indent: int | None = 1) -> bool:
    return write_text_atomic(path, dump_json(data, indent=indent))


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def today() -> str:
    return dt.date.today().isoformat()


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


# --- the collection listing -------------------------------------------------------
def read_collection(source: Path) -> list[str]:
    """File names from a CSV export (column 'Name') or from a folder on disk."""
    if source.is_dir():
        names = []
        for entry in sorted(source.rglob("*")):
            if entry.is_file():
                names.append(nfc(entry.name))
        return _dedupe(names)
    raw = source.read_bytes()
    text = _decode(raw)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        return []
    header = [h.strip().lstrip("﻿").casefold() for h in rows[0]]
    col = header.index("name") if "name" in header else 0
    start = 1 if header and (header[col] in ("name", "title", "file", "filename")) else 0
    if len(header) == 1:
        # A one-column listing: extra "columns" can only be a name with commas that was added
        # by hand without quotes ("[1979] Stalker (Russian; Сталкер, Stalker).mkv"). Rejoin them.
        names = [nfc(",".join(r)) for r in rows[start:] if r and ",".join(r).strip()]
    else:
        names = [nfc(r[col]) for r in rows[start:] if len(r) > col and r[col].strip()]
    return _dedupe(names)


def decode_text(raw: bytes) -> str:
    """Text saved by Notepad may be UTF-8 (with or without BOM) or UTF-16 ("Unicode")."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return text
        except UnicodeDecodeError:
            continue
    raise SystemExit(
        "The collection CSV is not UTF-8. Re-export it from Google Drive (it exports UTF-8) "
        "or save it as 'CSV UTF-8' in Excel."
    )


def _dedupe(names: list[str]) -> list[str]:
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


# --- film records -------------------------------------------------------------------
def film_path(tmdb_id: int) -> Path:
    return FILMS_DIR / f"{int(tmdb_id)}.json"


def load_film(tmdb_id: int) -> dict | None:
    return read_json(film_path(tmdb_id))


def save_film(record: dict) -> bool:
    record = dict(record)
    old = load_film(record["id"])
    if old:
        # keep the old fetch date when nothing else changed, so files don't churn
        compare_old = {k: v for k, v in old.items() if k != "fetched"}
        if compare_old == record:
            return False
    record["fetched"] = today()
    return write_json(film_path(record["id"]), record)


# --- the match ledger ---------------------------------------------------------------
def load_matches() -> dict:
    data = read_json(MATCHES_PATH, default=None) or {}
    data.setdefault("version", 1)
    data.setdefault("files", {})
    return data


def save_matches(data: dict) -> bool:
    order = lambda d: {k: d[k] for k in sorted(d, key=lambda s: s.casefold())}
    out = {"version": data.get("version", 1), "files": order(data["files"])}
    if data.get("removed"):
        out["removed"] = order(data["removed"])  # files no longer listed, kept for a year
    return write_json(MATCHES_PATH, out)


# --- corrections.txt -----------------------------------------------------------------
CORRECTIONS_HEADER = """\
# Manual match decisions. They always win over automatic matching and survive every update.
#
# One line per file:   <file name> = <decision>     (text after a " #" is a comment)
#
#   <file name>  exactly as in files.csv; the extension may be left off.
#   <decision>   a TMDB id                     [2016] Elle (French).mkv = 337674
#                a TMDB or IMDb link           ... = https://www.themoviedb.org/movie/337674-elle
#                an IMDb id                    ... = tt3716530
#                skip                          not a film, or leave it off the map
#
# The review page (review.cmd) writes these lines for you.
"""

_TMDB_URL = re.compile(r"themoviedb\.org/movie/(\d+)", re.IGNORECASE)
_IMDB_ID = re.compile(r"\b(tt\d{6,10})\b", re.IGNORECASE)


@dataclass
class Decision:
    key: str
    kind: str            # "tmdb", "imdb", "skip"
    value: str | int | None
    line: int
    comment: str = ""


def parse_decision(text: str) -> tuple[str, str | int | None] | None:
    value = text.strip()
    if not value:
        return None
    low = value.casefold()
    if low in ("skip", "ignore", "not a film", "none", "-"):
        return "skip", None
    if low in ("ok", "yes", "confirm", "accept"):
        return "ok", None
    m = _TMDB_URL.search(value)
    if m:
        return "tmdb", int(m.group(1))
    m = _IMDB_ID.search(value)
    if m:
        return "imdb", m.group(1).lower()
    m = re.fullmatch(r"(?:tmdb[:\s]*)?(\d{1,9})", low)
    if m:
        return "tmdb", int(m.group(1))
    return None


def read_corrections(path: Path = CORRECTIONS_PATH) -> tuple[dict[str, Decision], list[str]]:
    """Return ({normalised key: Decision}, problems)."""
    decisions: dict[str, Decision] = {}
    problems: list[str] = []
    if not path.exists():
        return decisions, problems
    text = decode_text(path.read_bytes())
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped == "#" or stripped.startswith("# "):
            continue
        split = _split_line(line)
        if stripped.startswith("#") and split is None:
            continue  # "#note" is a comment too; "#Alive.mkv = 614696" is a decision about a film
        if split is None:
            problems.append(f"corrections.txt line {n}: expected '<file name> = <TMDB id, link or skip>'")
            continue
        name, parsed, comment = split
        key = correction_key(name)
        decisions[key] = Decision(key, parsed[0], parsed[1], n, comment)
    return decisions, problems


def _split_line(line: str):
    """'<name> = <decision>  # comment' -> (name, (kind, value), comment). Names may contain '='."""
    idx = len(line)
    while True:
        idx = line.rfind("=", 0, idx)
        if idx <= 0:
            return None
        name, rest = line[:idx].strip(), line[idx + 1:]
        value, _, comment = rest.partition(" #")
        parsed = parse_decision(value)
        if parsed is not None and name:
            return name, parsed, comment.strip()


def correction_key(name: str) -> str:
    return nfc(name).casefold()


def strip_ext(name: str) -> str:
    m = re.match(r"^(.*[^.\s].*?)\.([A-Za-z0-9]{1,5})$", name)
    return m.group(1) if m else name


def same_file(a: str, b: str) -> bool:
    """Same file for decision purposes: case, Unicode form and extension don't matter, so a
    decision survives replacing a .mkv with an .mp4 of the same film."""
    return correction_key(strip_ext(a)) == correction_key(strip_ext(b))


def find_decision(decisions: dict[str, Decision], file_name: str) -> Decision | None:
    d = decisions.get(correction_key(file_name))
    if d is not None:
        return d
    for key, d in decisions.items():
        if same_file(key, file_name):
            return d
    return None


def set_correction(file_name: str, decision_text: str, comment: str = "",
                   path: Path = CORRECTIONS_PATH) -> None:
    """Add or replace the line for one file (used by the review page)."""
    text = decode_text(path.read_bytes()) if path.exists() else CORRECTIONS_HEADER
    lines = text.splitlines()
    new_line = f"{file_name} = {decision_text}" + (f"  # {comment}" if comment else "")
    out, replaced = [], False
    for line in lines:
        s = line.strip()
        if s and not (s == "#" or s.startswith("# ")):
            split = _split_line(line)
            if split and same_file(split[0], file_name):
                if not replaced:
                    out.append(new_line)
                    replaced = True
                continue
        out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(new_line)
    write_text_atomic(path, "\n".join(out).rstrip("\n") + "\n")


def remove_correction(file_name: str, path: Path = CORRECTIONS_PATH) -> bool:
    if not path.exists():
        return False
    lines = decode_text(path.read_bytes()).splitlines()
    out = []
    removed = False
    for line in lines:
        s = line.strip()
        split = None if (s == "#" or s.startswith("# ")) else _split_line(line)
        if split and same_file(split[0], file_name):
            removed = True
            continue
        out.append(line)
    if removed:
        write_text_atomic(path, "\n".join(out).rstrip("\n") + "\n")
    return removed
