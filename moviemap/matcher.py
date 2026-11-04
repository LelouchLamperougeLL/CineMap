"""Match parsed file names to TMDB films.

Philosophy: a wrong match silently corrupts the map, so the matcher only accepts a
film when the title AND the year agree and nothing else fits as well. Everything
else is flagged with a plain-English explanation and the best candidates, for
the owner to settle once (in corrections.txt, usually via the review page).

Evidence, per candidate:
  T  title agreement, best over every title form we have on both sides
     (English title, original-language title, romanisation; TMDB title,
     original title, alternative titles and translations)
  Y  year agreement (exact, off by one, or a festival/country release that year)
  L  language agreement with the file's language tag, if there is one
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .languages import display_name
from .names import ParsedName
from .textnorm import SEQUEL_MARKERS, drop_article, fold, has_non_latin_letters, numerals
from .tmdb import TMDB, trim_record

MATCHER_VERSION = 2
PLAUSIBLE_T = 0.86
PLAUSIBLE_Y = 0.85


# --- title similarity ---------------------------------------------------------------
def title_sim(a: str, b: str) -> float:
    """Similarity of two folded titles, 0..1. 1.0 only for identical titles."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a2, b2 = drop_article(a), drop_article(b)
    if a2 == b2:
        return 0.97
    ta, tb = a2.split(), b2.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if long_[: len(short)] == short:
        # One title starts with the other. Without a subtitle separator in the raw title this is
        # usually a different film ("Lincoln" / "Lincoln Lawyer"); see subtitle_sim for the rest.
        extra = long_[len(short):]
        if any(t.isdigit() or t in SEQUEL_MARKERS for t in extra):
            return 0.5  # "Toy Story" vs "Toy Story 2" are different films
        return 0.75
    if len(ta) == len(tb) >= 2:
        diffs = [(x, y) for x, y in zip(ta, tb) if x != y]
        if len(diffs) == 1:
            x, y = diffs[0]
            if min(len(x), len(y)) >= 2 and (x.startswith(y) or y.startswith(x)) and not (x.isdigit() or y.isdigit()):
                return 0.88  # abbreviation: "Mulholland Dr." / "Mulholland Drive"
    ratio = SequenceMatcher(None, a2, b2).ratio()
    sa, sb = set(ta), set(tb)
    dice = 2 * len(sa & sb) / (len(sa) + len(sb))
    score = 0.92 * max(ratio, dice)
    if numerals(a2) != numerals(b2):
        score = min(score, 0.45)
    return score


_SUBTITLE_SEPARATORS = re.compile(r"\s*(?::|\s[-–—]\s|\(|\[)\s*")


def form_sim(a_raw: str, b_raw: str, a: str | None = None, b: str | None = None) -> float:
    """title_sim plus the subtitle case: 'Glass Onion' vs 'Glass Onion: A Knives Out Mystery'."""
    a = fold(a_raw) if a is None else a
    b = fold(b_raw) if b is None else b
    s = title_sim(a, b)
    if s >= 0.9:
        return s
    # a = the file's title form, b = TMDB's. TMDB adding a subtitle the file left out ("Glass
    # Onion" / "Glass Onion: A Knives Out Mystery") is common and fine. The file adding a
    # subtitle TMDB lacks ("Avatar: Fire and Ash" / "Avatar") usually means a sequel or another
    # film, so that direction is never strong enough on its own to accept a match.
    for short, long_raw, file_is_longer in ((a, b_raw, False), (b, a_raw, True)):
        for m in _SUBTITLE_SEPARATORS.finditer(long_raw):
            head = fold(long_raw[: m.start()])
            if head and head == short:
                extra = fold(long_raw[m.end():]).split()
                if any(t.isdigit() or t in SEQUEL_MARKERS for t in extra):
                    return max(s, 0.5)
                if file_is_longer:
                    return max(s, 0.8)
                strong = len(short.split()) >= 2 or len(short) >= 4
                return max(s, 0.9 if strong else 0.78)
    return s


@dataclass
class TitleForm:
    folded: str
    weight: float
    label: str
    text: str


def user_forms(p: ParsedName) -> list[TitleForm]:
    raw = [(p.title, 1.0, "title")]
    if p.base_title:
        raw.append((p.base_title, 0.92, "title without the part number"))
    raw += [(f"{p.title} ({n})", 1.0, "title") for n in p.natives if has_non_latin_letters(n)]
    raw += [(n, 1.0, "original title") for n in p.natives]
    raw += [(r, 0.95, "romanised title") for r in p.romanized]
    out, seen = [], set()
    for text, w, label in raw:
        f = fold(text)
        if f and (f, label) not in seen:
            seen.add((f, label))
            out.append(TitleForm(f, w, label, text))
    return out


# --- candidates ---------------------------------------------------------------------
@dataclass
class Candidate:
    id: int
    title: str
    original_title: str
    language: str
    year: int | None
    votes: int
    popularity: float
    found_by: set = field(default_factory=set)
    record: dict | None = None
    T: float = 0.0
    T_primary: float = 0.0           # title agreement using main titles only (no alternatives)
    Y: float = 0.0
    L: float = 0.0
    score: float = 0.0
    title_note: str = ""
    year_note: str = ""
    lang_note: str = ""
    native_conflict: str = ""

    @classmethod
    def from_search(cls, r: dict) -> "Candidate":
        date = r.get("release_date") or ""
        return cls(
            id=int(r["id"]), title=r.get("title") or "", original_title=r.get("original_title") or "",
            language=r.get("original_language") or "", year=int(date[:4]) if date[:4].isdigit() else None,
            votes=int(r.get("vote_count") or 0), popularity=float(r.get("popularity") or 0),
        )

    def attach(self, record: dict) -> None:
        self.record = record
        self.title = record.get("title") or self.title
        self.original_title = record.get("original_title") or self.original_title
        self.language = record.get("original_language") or self.language
        date = record.get("release_date") or ""
        self.year = int(date[:4]) if date[:4].isdigit() else self.year
        self.votes = int(record.get("vote_count") or self.votes)

    def label(self) -> str:
        return f"{self.title} ({self.year or '?'})"

    def directors(self) -> list[str]:
        if not self.record:
            return []
        return [c["name"] for c in self.record.get("crew", []) if c.get("role") == "director"]

    def summary(self) -> dict:
        d = {"id": self.id, "title": self.title, "year": self.year, "language": self.language,
             "votes": self.votes, "score": round(self.score, 3),
             "T": round(self.T, 3), "Y": round(self.Y, 3), "L": round(self.L, 3)}
        if self.original_title and self.original_title != self.title:
            d["original_title"] = self.original_title
        if self.record:
            d["directors"] = self.directors()[:3]
            d["poster_path"] = self.record.get("poster_path")
            d["runtime"] = self.record.get("runtime")
        return d


def score_candidate(p: ParsedName, c: Candidate, forms: list[TitleForm]) -> None:
    tmdb_forms = [(c.title, 1.0, "TMDB title"), (c.original_title, 1.0, "TMDB original title")]
    if c.record:
        tmdb_forms += [(t, 0.95, "TMDB alternative title") for t in c.record.get("alt_titles", [])]
    best, note, primary = 0.0, "", 0.0
    folded_tmdb = [(fold(t), w, lab, t) for t, w, lab in tmdb_forms if t]
    for uf in forms:
        for tf, tw, tlabel, ttext in folded_tmdb:
            raw = form_sim(uf.text, ttext, uf.folded, tf)
            s = raw * uf.weight * tw
            if uf.label in ("title", "original title") and tlabel in ("TMDB title", "TMDB original title"):
                primary = max(primary, raw)
            if s > best + 1e-9:
                best = s
                rel = "=" if s >= 0.97 else "≈"
                note = f"{uf.label} “{uf.text}” {rel} {tlabel} “{ttext}”"
    c.T, c.T_primary, c.title_note = best, primary, note

    # A non-Latin original title in the file should agree with TMDB's.
    c.native_conflict = ""
    natives = [n for n in p.natives if has_non_latin_letters(n)]
    if natives and has_non_latin_letters(c.original_title):
        targets = [fold(c.original_title)] + [fold(t) for t in (c.record or {}).get("alt_titles", [])
                                              if has_non_latin_letters(t)]
        agree = max(title_sim(fold(n), t) for n in natives for t in targets)
        if agree < 0.5:
            c.native_conflict = f"the file's original title “{natives[0]}” differs from TMDB's “{c.original_title}”"

    # Year
    # Releases count for the file's year only around the film's first appearance (festival
    # premiere, then cinemas a year later); re-releases and restorations decades on do not, or
    # an old namesake re-released this year would pass for a brand-new film.
    all_years = set((c.record or {}).get("release_years") or [])
    first = min(all_years | ({c.year} if c.year else set()), default=None)
    years = {y for y in all_years if first is not None and y <= first + 1}
    if p.year is None:
        c.Y, c.year_note = 0.7, "no year in the file name"
    elif c.year is None:
        c.Y, c.year_note = 0.5, "TMDB has no release date"
    else:
        d = abs(p.year - c.year)
        if d == 0:
            c.Y, c.year_note = 1.0, f"year {p.year} = TMDB {c.year}"
        elif p.year in years:
            c.Y, c.year_note = 0.97, f"year {p.year}: TMDB lists a {p.year} release (premiere or another country); main date {c.year}"
        elif d == 1:
            c.Y, c.year_note = 0.9, f"year {p.year} vs TMDB {c.year} (one apart: festival and release years often differ)"
        elif d == 2:
            c.Y, c.year_note = 0.55, f"year {p.year} vs TMDB {c.year} (two apart)"
        elif d <= 4:
            c.Y, c.year_note = 0.25, f"year {p.year} vs TMDB {c.year} ({d} apart)"
        else:
            c.Y, c.year_note = 0.0, f"year {p.year} vs TMDB {c.year} ({d} apart)"

    # Language
    spoken = set((c.record or {}).get("spoken_languages") or [])
    lang_name = display_name(c.language) if c.language else "unknown"
    if p.lang_codes:
        tag = "/".join(p.langs)
        if c.language in p.lang_codes:
            c.L, c.lang_note = 1.0, f"language tag {tag} ↔ TMDB original language {lang_name}"
        elif spoken & set(p.lang_codes):
            c.L, c.lang_note = 0.8, f"language tag {tag} is spoken in the film (TMDB original language {lang_name})"
        elif c.language == "xx":
            c.L, c.lang_note = 0.7, f"language tag {tag}; TMDB lists the film as having no dialogue"
        else:
            c.L, c.lang_note = 0.35, f"language tag {tag} but TMDB's original language is {lang_name}"
    else:
        if c.language == "en":
            c.L, c.lang_note = 0.9, "no language tag; TMDB original language English"
        else:
            c.L, c.lang_note = 0.65, f"no language tag; TMDB original language {lang_name}"

    c.score = 0.55 * c.T + 0.30 * c.Y + 0.15 * c.L - (0.1 if c.native_conflict else 0.0)


def plausible(c: Candidate) -> bool:
    return c.T >= PLAUSIBLE_T and c.Y >= PLAUSIBLE_Y


# --- the search plan ------------------------------------------------------------------
def _collect(p: ParsedName, tmdb: TMDB, forms: list[TitleForm]) -> dict[int, Candidate]:
    cands: dict[int, Candidate] = {}
    done: set = set()

    def run(query: str | None, year: int | None, label: str) -> None:
        if not query or (query, year) in done:
            return
        done.add((query, year))
        for r in tmdb.search(query, year):
            if not r.get("id"):
                continue
            c = cands.get(int(r["id"]))
            if c is None:
                c = cands[int(r["id"])] = Candidate.from_search(r)
                score_candidate(p, c, forms)
            c.found_by.add(label)

    def good_enough() -> bool:
        return any(plausible(c) and c.score >= 0.9 for c in cands.values())

    def unmistakable() -> bool:
        return any(plausible(c) and c.T_primary >= 0.97 and c.Y >= 0.97 for c in cands.values())

    run(p.title, p.year, "title + year")
    if p.base_title:
        run(p.base_title, p.year, "title without part + year")
    if unmistakable():
        return cands
    run(p.title, None, "title")
    for n in p.natives:
        run(n, p.year, "original title + year")
    for r in p.romanized:
        run(r, p.year, "romanised title + year")
    if p.base_title:
        run(p.base_title, None, "title without part")
    if good_enough():
        return cands
    if p.year:
        run(p.title, p.year - 1, "title + previous year")
        run(p.title, p.year + 1, "title + next year")
    for n in p.natives:
        run(n, None, "original title")
    if ":" in p.title:
        head = p.title.split(":", 1)[0].strip()
        if len(fold(head)) >= 4:
            run(head, p.year, "main title before the colon + year")
    return cands


@dataclass
class MatchResult:
    status: str                      # matched | flagged
    tmdb_id: int | None
    suggestion: int | None
    confidence: str                  # high | good | low
    score: float
    why: list[str]
    flags: list[str]
    candidates: list[dict]
    records: dict[int, dict]         # trimmed TMDB records fetched on the way

    def ledger_entry(self, p: ParsedName) -> dict:
        d = {"status": self.status}
        if self.tmdb_id is not None:
            d["tmdb_id"] = self.tmdb_id
        if self.suggestion is not None:
            d["suggestion"] = self.suggestion
        d["confidence"] = self.confidence
        d["score"] = round(self.score, 3)
        d["why"] = self.why
        if self.flags:
            d["flags"] = self.flags
        d["candidates"] = self.candidates
        d["parsed"] = p.to_dict()
        d["matcher"] = MATCHER_VERSION
        return d


def match(p: ParsedName, tmdb: TMDB) -> MatchResult:
    forms = user_forms(p)
    cands = _collect(p, tmdb, forms)
    ranked = sorted(cands.values(), key=lambda c: (c.score, c.votes), reverse=True)

    # Look closer (alternative titles, all release dates, spoken languages) at the front runners.
    close = [c for c in ranked if plausible(c) or (ranked and c.score >= ranked[0].score - 0.12)][:4]
    if ranked and ranked[0] not in close:
        close.insert(0, ranked[0])
    records: dict[int, dict] = {}
    for c in close:
        raw = tmdb.movie(c.id)
        if raw and raw.get("id"):
            rec = trim_record(raw)
            records[c.id] = rec
            c.attach(rec)
            score_candidate(p, c, forms)
    ranked = sorted(cands.values(), key=lambda c: (c.score, c.votes), reverse=True)
    result = decide(p, ranked, records)
    chosen = result.tmdb_id
    if result.status == "matched" and chosen not in records:
        # decided by popularity among more films than were looked at closely: look at this one too
        raw = tmdb.movie(chosen)
        if raw and raw.get("id"):
            rec = trim_record(raw)
            records[chosen] = rec
            cands[chosen].attach(rec)
            score_candidate(p, cands[chosen], forms)
            ranked = sorted(cands.values(), key=lambda c: (c.score, c.votes), reverse=True)
            result = decide(p, ranked, records)
        if result.status == "matched" and result.tmdb_id not in records:
            missing = cands[result.tmdb_id]
            result = MatchResult("flagged", None, missing.id, "low", missing.score,
                                 [r for r in (missing.title_note, missing.year_note, missing.lang_note) if r],
                                 [f"TMDB has no details for {missing.label()}, the likely match"],
                                 [c.summary() for c in ranked[:5]], records)
    return result


def decide(p: ParsedName, ranked: list[Candidate], records: dict[int, dict]) -> MatchResult:
    summaries = [c.summary() for c in ranked[:5]]
    fits = [c for c in ranked if plausible(c)]
    why: list[str] = []
    flags: list[str] = []

    def flagged(suggest: Candidate | None, *reasons: str) -> MatchResult:
        if suggest:
            reasons_why = [r for r in (suggest.title_note, suggest.year_note, suggest.lang_note) if r]
        else:
            reasons_why = []
        return MatchResult("flagged", None, suggest.id if suggest else None, "low",
                           suggest.score if suggest else 0.0, reasons_why, list(reasons), summaries, records)

    if not ranked:
        return flagged(None, "TMDB returned nothing for this title")
    if not fits:
        best = ranked[0]
        if best.T < PLAUSIBLE_T:
            reason = f"no TMDB film has this title (closest: {best.label()})"
        else:
            reason = f"title matches {best.label()} but the year does not ({best.year_note})"
        return flagged(best, reason)

    pool = fits
    if len(pool) > 1 and p.lang_codes:
        same_lang = [c for c in pool if c.L >= 0.8]
        if same_lang and len(same_lang) < len(pool):
            why.append(f"{len(pool)} films fit the title and year; the language tag ({'/'.join(p.langs)}) "
                       + ("singles out this one" if len(same_lang) == 1 else f"narrows them to {len(same_lang)}"))
            pool = same_lang
    elif len(pool) > 1:
        # No tag: by the naming convention the film is probably in English. Only lean on that
        # when the English candidate is also at least as well known (untagged non-English films exist).
        english = [c for c in pool if c.language == "en"]
        others = [c for c in pool if c.language != "en"]
        if english and others and max(c.votes for c in english) >= max(c.votes for c in others):
            why.append(f"{len(pool)} films fit the title and year; without a language tag the "
                       f"English-language {'one comes' if len(english) == 1 else 'ones come'} first")
            pool = english
    chosen = pool[0]
    if len(pool) > 1:
        top_t = max(c.T for c in pool)
        exact = [c for c in pool if c.T >= top_t - 0.02]
        best_known = max(pool, key=lambda c: c.votes)
        if len(exact) == 1:
            chosen = exact[0]
            if chosen.votes < 0.5 * best_known.votes:
                return flagged(best_known, f"{chosen.label()} matches the title exactly, but "
                                           f"{best_known.label()} is far better known")
            why.append(f"of {len(pool)} films that fit, its title matches most exactly")
        else:
            by_votes = sorted(exact, key=lambda c: c.votes, reverse=True)
            chosen, runner = by_votes[0], by_votes[1]
            if chosen.votes >= 20 and chosen.votes >= 10 * max(runner.votes, 1):
                why.append(f"{len(exact)} films share this title and year; chose by far the best known "
                           f"({chosen.votes:,} TMDB votes vs {runner.votes:,})")
            else:
                names = "; ".join(f"{c.label()}{', ' + c.directors()[0] if c.directors() else ''}"
                                  for c in by_votes[:3])
                return flagged(chosen, f"{len(exact)} films share this title and year: {names}")

    why = [chosen.title_note, chosen.year_note, chosen.lang_note] + why
    if chosen.native_conflict:
        flags.append(chosen.native_conflict)
    if p.year and chosen.T_primary < 0.97:
        exact = [c for c in ranked if c is not chosen and c.T_primary >= 0.97 and c.year
                 and abs(c.year - p.year) <= 3 and (not p.lang_codes or c.L >= 0.8)]
        if exact:
            flags.append(f"matched {chosen.label()} through a secondary title, but {exact[0].label()} "
                         f"carries this exact title")
    if p.lang_codes and chosen.L < 0.6 and not (chosen.T >= 0.97 and chosen.Y == 1.0):
        flags.append(chosen.lang_note)
    if flags:
        res = flagged(chosen, *flags)
        res.why = [w for w in why if w]
        return res

    high = chosen.T >= 0.97 and chosen.Y >= 0.97 and chosen.L >= 0.8 and len(pool) == 1
    if p.part and chosen.T < 0.97:
        why.append(f"part {p.part} of a work TMDB lists as one film")
    return MatchResult("matched", chosen.id, None, "high" if high else "good", chosen.score,
                       [w for w in why if w], [], summaries, records)
