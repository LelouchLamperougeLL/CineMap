"""Bring the match ledger and the film records in line with the collection listing.

Only files that are new (or whose decision changed) cost any TMDB requests. The
ledger is saved every few files and on any exit, and every TMDB response is
cached the moment it arrives, so stopping at any point loses nothing.
"""

from __future__ import annotations

import datetime as dt
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import store
from .matcher import MATCHER_VERSION, match
from .names import parse
from .textnorm import fold
from .tmdb import TMDB, AuthError, NetworkDown, OfflineMiss, TMDBError, trim_record

SAVE_EVERY = 15


@dataclass
class SyncReport:
    total: int = 0
    new: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)     # came back: their old decision was reused
    matched: list[str] = field(default_factory=list)      # newly matched automatically this run
    flagged: list[str] = field(default_factory=list)      # currently flagged (all, not only new)
    manual: list[str] = field(default_factory=list)       # decisions applied this run
    skipped: list[str] = field(default_factory=list)      # newly skipped this run
    errors: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    stale_corrections: list[str] = field(default_factory=list)
    stopped: str | None = None
    changed: bool = False


def ensure_record(tmdb: TMDB, tmdb_id: int, *, refresh: bool = False) -> dict | None:
    rec = None if refresh else store.load_film(tmdb_id)
    if rec is not None:
        return rec
    raw = tmdb.movie(tmdb_id, refresh=refresh)
    if not raw or not raw.get("id"):
        return None
    rec = trim_record(raw)
    store.save_film(rec)
    return rec


def _decision_entry(d: store.Decision, previous: dict | None, tmdb: TMDB) -> dict:
    keep = {k: previous[k] for k in ("candidates", "parsed", "added") if previous and k in previous}
    note = f"set by hand in corrections.txt (line {d.line})"
    if d.kind == "skip":
        return {"status": "skipped", "why": [note], **keep}
    if d.kind == "ok":
        target = (previous or {}).get("tmdb_id") or (previous or {}).get("suggestion")
        if not target:
            return {"status": "flagged", "flags": ["'ok' in corrections.txt, but there is no suggestion to accept"],
                    "why": [], **keep}
        tmdb_id = int(target)
    elif d.kind == "imdb":
        tmdb_id = tmdb.find_imdb(str(d.value))
        if not tmdb_id:
            return {"status": "flagged", "flags": [f"IMDb id {d.value} (corrections.txt line {d.line}) is not on TMDB"],
                    "why": [], **keep}
        note += f" as IMDb {d.value}"
    else:
        tmdb_id = int(d.value)
    rec = ensure_record(tmdb, tmdb_id)
    if rec is None:
        return {"status": "flagged", "flags": [f"TMDB has no film with id {tmdb_id} (corrections.txt line {d.line})"],
                "why": [], **keep}
    year = (rec.get("release_date") or "")[:4]
    return {"status": "manual", "tmdb_id": tmdb_id, "why": [f"{note}: {rec['title']} ({year or '?'})"], **keep}


def sync(names: list[str], tmdb: TMDB, *, log=print, rematch: set[str] | None = None,
         confirm_removal=None) -> SyncReport:
    """`confirm_removal(missing_names) -> bool` is asked when many files vanish at once
    (a cut-short export, a half-synced folder). Without it (unattended runs, the review
    page) such a run changes nothing: a few missing files are archived as usual, a mass
    disappearance has to be confirmed in update.cmd."""
    rep = SyncReport(total=len(names))
    ledger = store.load_matches()
    files: dict = ledger["files"]
    archive: dict = ledger.setdefault("removed", {})
    decisions, rep.problems = store.read_corrections()
    rematch = rematch or set()

    # Files missing from the listing are archived, not forgotten: if they come back, their
    # match, any hand decision and the date they were first added return with them.
    current = set(names)
    vanished = [n for n in files if n not in current]
    if len(vanished) > max(5, 0.05 * len(files)) and not (confirm_removal and confirm_removal(vanished)):
        rep.stopped = (f"{len(vanished)} files that were in the collection are missing from the listing; "
                       "nothing was changed. If the export was cut short, export it again; if they really "
                       "are gone, run update.cmd and answer yes.")
        return rep
    for name in vanished:
        entry = files.pop(name)
        entry["removed_on"] = store.today()
        archive[name] = entry
        rep.removed.append(name)
        rep.changed = True
    for name in names:
        if name not in files and name in archive:
            entry = archive.pop(name)
            entry.pop("removed_on", None)
            files[name] = entry
            rep.restored.append(name)
            rep.changed = True
    year_ago = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    for name in [n for n, e in archive.items() if (e.get("removed_on") or "") < year_ago]:
        del archive[name]
        rep.changed = True

    used_keys = set()
    pending = 0
    last_save = time.monotonic()
    try:
        for i, name in enumerate(names):
            try:
                entry = files.get(name)
                p = parse(name)
                d = store.find_decision(decisions, name)
                if d:
                    used_keys.add(d.key)
                is_new = entry is None
                if is_new:
                    rep.new.append(name)

                if d is not None:
                    # Re-apply only when the decision itself changed.
                    signature = f"{d.kind}:{d.value}"
                    if entry is None or entry.get("decision") != signature or name in rematch:
                        new_entry = _decision_entry(d, entry, tmdb)
                        new_entry["decision"] = signature
                        new_entry.setdefault("parsed", p.to_dict())
                        new_entry["added"] = (entry or {}).get("added") or store.today()
                        files[name] = new_entry
                        rep.manual.append(name)
                        rep.changed = True
                        pending += 1
                    elif entry.get("status") == "manual" and entry.get("tmdb_id"):
                        ensure_record(tmdb, int(entry["tmdb_id"]))
                elif not p.is_video or p.skip_reason:
                    if entry is None or entry.get("status") != "skipped" or entry.get("decision"):
                        files[name] = {"status": "skipped", "why": [p.skip_reason or "not a film"],
                                       "parsed": p.to_dict(), "added": (entry or {}).get("added") or store.today()}
                        rep.skipped.append(name)
                        rep.changed = True
                else:
                    stale = (
                        entry is None
                        or name in rematch
                        or entry.get("decision")                       # a hand decision was removed
                        or entry.get("status") == "error"
                        or (entry.get("status") == "flagged" and entry.get("matcher") != MATCHER_VERSION)
                    )
                    if stale:
                        label = "new" if entry is None else "re-matching"
                        log(f"  [{i + 1}/{len(names)}] {label}: {name}")
                        try:
                            result = match(p, tmdb)
                        except (NetworkDown, AuthError, OfflineMiss):
                            raise
                        except TMDBError as err:
                            files[name] = {"status": "error", "why": [str(err)], "parsed": p.to_dict(),
                                           "added": (entry or {}).get("added") or store.today()}
                            rep.errors.append(name)
                            rep.changed = True
                            continue
                        new_entry = result.ledger_entry(p)
                        new_entry["added"] = (entry or {}).get("added") or store.today()
                        files[name] = new_entry
                        if result.status == "matched":
                            store.save_film(result.records[result.tmdb_id])
                            rep.matched.append(name)
                            log(f"      matched: {result.records[result.tmdb_id]['title']} "
                                f"({(result.records[result.tmdb_id].get('release_date') or '????')[:4]})")
                        else:
                            log(f"      needs review: {'; '.join(result.flags)}")
                        rep.changed = True
                        pending += 1
                    elif entry.get("status") == "matched" and not store.film_path(entry["tmdb_id"]).exists():
                        ensure_record(tmdb, int(entry["tmdb_id"]))
            except (NetworkDown, AuthError, OfflineMiss):
                raise
            except Exception as err:  # one odd file must never stop the whole update
                files[name] = {"status": "error", "why": [f"{type(err).__name__}: {err}"],
                               "parsed": {"title": name}, "added": (files.get(name) or {}).get("added") or store.today()}
                rep.errors.append(name)
                rep.changed = True
                log(f"      could not process {name}: {type(err).__name__}: {err}")
            if pending >= SAVE_EVERY or (pending and time.monotonic() - last_save > 20):
                _check_duplicates(files)
                store.save_matches(ledger)
                pending = 0
                last_save = time.monotonic()
    except NetworkDown as err:
        rep.stopped = str(err)
    except AuthError as err:
        rep.stopped = str(err)
    except OfflineMiss:
        rep.stopped = "No TMDB key configured, and some films are not in the local cache yet."
    finally:
        _check_duplicates(files)
        if store.save_matches(ledger):
            rep.changed = True

    rep.stale_corrections = sorted(
        f"line {d.line}" for k, d in decisions.items() if k not in used_keys)
    rep.flagged = sorted(n for n, e in files.items() if e.get("status") == "flagged")
    return rep


def _check_duplicates(files: dict) -> None:
    """Two files on one TMDB film is fine for parts of one work, suspicious otherwise."""
    for e in files.values():  # recomputed from scratch each time, so it heals itself
        if e.pop("duplicate", None):
            e["status"] = "matched"
            e["tmdb_id"] = e.pop("suggestion")
            e["flags"] = [f for f in e.get("flags", []) if not f.startswith("same TMDB film as")]
            if not e["flags"]:
                del e["flags"]
    by_film: dict[int, list[str]] = defaultdict(list)
    for name, e in files.items():
        if e.get("status") in ("matched", "manual") and e.get("tmdb_id"):
            by_film[int(e["tmdb_id"])].append(name)
    for tmdb_id, names in by_film.items():
        if len(names) < 2:
            continue
        auto = [n for n in names if files[n]["status"] == "matched"]
        if not auto:
            continue  # all decided by hand
        bases = {fold(files[n]["parsed"].get("base_title") or "") for n in names}
        parts = [files[n]["parsed"].get("part") for n in names]
        if len(bases) == 1 and "" not in bases and all(parts) and len(set(parts)) == len(parts):
            continue  # Part 1 / Part 2 / ... of one work
        for n in auto:
            e = files[n]
            others = [o for o in names if o != n]
            e["status"] = "flagged"
            e["duplicate"] = True
            e["suggestion"] = e.pop("tmdb_id")
            e.setdefault("flags", []).append("same TMDB film as " + "; ".join(others))
