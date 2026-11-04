"""Build data/movies.js (the only data the web page loads) from the stored records."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections import Counter, defaultdict

import numpy as np

from . import store
from .config import (DATA_DIR, DATA_JS, DB_DIR, ENV_PATH, INDEX_HTML, LAYOUT_PATH, load_settings,
                     load_tmdb_credential)
from .features import BLOCK_WEIGHTS, KEYWORD_STOPLIST, similarity
from .languages import display_name
from .layout import (assign_hues, assign_to_clusters, cluster_colour, compute_layout, find_clusters,
                     match_clusters, memberships)

BUILD_VERSION = 3
NEIGHBOURS = 20

# TMDB genre names as shown on the map. TMDB's "Family" genre is shown as "All Ages".
GENRE_LABELS = {"Family": "All Ages", "Science Fiction": "Sci-Fi", "TV Movie": "TV Film"}
GENRE_PHRASES = {
    "Action": "action", "Adventure": "adventure", "Animation": "animation", "Comedy": "comedy",
    "Crime": "crime", "Documentary": "documentary", "Drama": "drama", "Family": "all-ages tales",
    "Fantasy": "fantasy", "History": "history", "Horror": "horror", "Music": "music",
    "Mystery": "mystery", "Romance": "romance", "Science Fiction": "science fiction",
    "TV Movie": "TV films", "Thriller": "thrillers", "War": "war", "Western": "westerns",
}
LANG_ADJECTIVES = {
    "ja": "Japanese", "ko": "Korean", "fr": "French", "zh": "Chinese", "cn": "Hong Kong", "es": "Spanish-language",
    "de": "German", "it": "Italian", "da": "Danish", "no": "Norwegian", "nb": "Norwegian", "sv": "Swedish",
    "fi": "Finnish", "is": "Icelandic", "fa": "Iranian", "ro": "Romanian", "ru": "Russian", "pt": "Portuguese-language",
    "tr": "Turkish", "pl": "Polish", "hu": "Hungarian", "hi": "Indian", "th": "Thai", "ar": "Arabic-language",
}
NORDIC = {"da", "no", "nb", "nn", "sv", "fi", "is"}
FORBIDDEN_WORDS = re.compile(r"famil(y|ies)", re.IGNORECASE)
NAME_STOPWORDS = {"based on novel or book", "based on true story", "biography", "sequel", "remake",
                  "based on comic", "based on play or musical", "independent film", "anime", "lgbt",
                  "based on short story", "based on memoir or autobiography", "historical figure"}


def _year(rec: dict) -> int | None:
    d = rec.get("release_date") or ""
    return int(d[:4]) if d[:4].isdigit() else None


def _people(rec: dict, role: str, limit: int) -> list[str]:
    out = []
    for c in rec.get("crew", []):
        if c["role"] == role and c["name"] not in out:
            out.append(c["name"])
    return out[:limit]


def _fingerprint(records: dict[int, dict], files: dict[int, list[str]], added: dict[int, str], settings) -> str:
    h = hashlib.sha256()
    h.update(f"v{BUILD_VERSION}|{json.dumps(BLOCK_WEIGHTS, sort_keys=True)}".encode())
    h.update(f"|{settings.title}|{settings.tagline}|{settings.drive_folder_url}".encode())
    h.update(json.dumps(settings_cluster_names(), sort_keys=True).encode())
    for i in sorted(records):
        rec = {k: v for k, v in records[i].items() if k != "fetched"}
        h.update(json.dumps([i, rec, sorted(files[i]), added.get(i, "")], sort_keys=True,
                            ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:24]


def settings_cluster_names() -> dict[str, str]:
    """Optional [cluster_names] table in settings.toml, e.g.  c3 = "Hand-drawn wonder"."""
    from .config import SETTINGS_PATH
    import tomllib
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, "rb") as fh:
        table = tomllib.load(fh).get("cluster_names") or {}
    return {str(k): str(v) for k, v in table.items() if str(v).strip()}


# --- naming clusters -------------------------------------------------------------------------
def _cluster_name(member_recs: list[dict], all_recs: list[dict]) -> tuple[str, str]:
    n, total = len(member_recs), len(all_recs)

    def share(recs, fn):
        c = Counter()
        for r in recs:
            c.update(set(fn(r)))
        return c

    lang_c = share(member_recs, lambda r: [r.get("original_language") or ""])
    lang, lang_n = lang_c.most_common(1)[0]
    nordic_n = sum(v for k, v in lang_c.items() if k in NORDIC)
    adj = ""
    if lang != "en" and lang_n / n >= 0.55 and lang in LANG_ADJECTIVES:
        adj = LANG_ADJECTIVES[lang]
    elif nordic_n / n >= 0.55:
        adj = "Nordic"
    elif sum(v for k, v in lang_c.items() if k != "en") / n >= 0.75:
        adj = "International"

    g_all = share(all_recs, lambda r: r.get("genres", []))
    g_in = share(member_recs, lambda r: r.get("genres", []))
    scored = []
    for g, k in g_in.items():
        support = k / n
        lift = support / (g_all[g] / total)
        if support >= 0.4:
            scored.append((support * min(lift, 4.0), g))
    scored.sort(reverse=True)
    genres = [g for _, g in scored[:2]]
    if not genres and g_in:
        genres = [g_in.most_common(1)[0][0]]
    if "Family" in genres:  # TMDB's all-ages genre reads best as a qualifier
        others = [g for g in genres if g != "Family"]
        phrase = (GENRE_PHRASES.get(others[0], others[0].lower()) if others else "films") + " for all ages"
    else:
        phrase = " & ".join(GENRE_PHRASES.get(g, g.lower()) for g in genres) or "cinema"
    title = f"{adj} {phrase}" if adj else phrase
    title = title[0].upper() + title[1:]

    k_all = share(all_recs, lambda r: [k["name"].casefold() for k in r.get("keywords", [])])
    k_in = share(member_recs, lambda r: [k["name"].casefold() for k in r.get("keywords", [])])
    kws = []
    for k, c in k_in.items():
        if c < 2 or k in KEYWORD_STOPLIST or k in NAME_STOPWORDS or FORBIDDEN_WORDS.search(k):
            continue
        support = c / n
        lift = support / (k_all[k] / total)
        kws.append((support * math.log1p(lift), k))
    kws.sort(reverse=True)
    d_in = share(member_recs, lambda r: _people(r, "director", 3))
    directors = [d for d, c in d_in.most_common(3) if c >= max(2, 0.2 * n)]
    tagline = ", ".join(directors[:2] + [k for _, k in kws[:4 - min(2, len(directors))]])
    return title, tagline


# --- main build ---------------------------------------------------------------------------------
def build_map(force: bool = False, relayout: bool = False, log=print) -> str:
    settings = load_settings()
    names = store.read_collection(settings.source_path)
    ledger = store.load_matches()["files"]

    files: dict[int, list[str]] = defaultdict(list)
    added: dict[int, str] = {}
    unplaced: list[dict] = []
    for name in names:
        e = ledger.get(name)
        if e and e.get("status") in ("matched", "manual") and e.get("tmdb_id"):
            fid = int(e["tmdb_id"])
            files[fid].append(name)
            a = e.get("added") or ""
            added[fid] = min(added.get(fid, a) or a, a) if a else added.get(fid, "")
        elif e and e.get("status") in ("flagged", "error") or (not e):
            parsed = (e or {}).get("parsed") or {}
            if parsed.get("skip_reason"):
                continue
            unplaced.append({"f": name, "t": parsed.get("title") or name, "y": parsed.get("year")})

    records = {}
    for fid in sorted(files):
        rec = store.load_film(fid)
        if rec:
            records[fid] = rec
    if len(records) < 3:
        return "Not enough matched films to draw a map yet."
    _prune_unused_records(set(records), ledger)

    previous = store.read_json(LAYOUT_PATH, default={}) or {}
    fp = _fingerprint(records, files, added, settings)
    if not force and previous.get("fingerprint") == fp and DATA_JS.exists():
        return "Map is up to date. Nothing to do."

    ids = sorted(records)
    recs = [records[i] for i in ids]
    log(f"Building the map for {len(ids)} films ...")
    S, _ = similarity(recs)
    D = 1.0 - S
    np.fill_diagonal(D, 0.0)

    # Ordinary updates leave every film already on the map exactly where it is, in its region.
    # A full re-layout happens only on request or once the collection has changed by 15%.
    prev_pos = previous.get("positions") or {}
    prev_ids = {int(k) for k in prev_pos}
    old_clusters = previous.get("clusters", [])
    base = previous.get("full_count") or len(prev_ids)
    changed = len(set(ids) ^ prev_ids)
    full = relayout or not prev_ids or not old_clusters or changed >= 0.15 * max(base, 1)
    Y, lstats = compute_layout(D, S, ids, prev_pos or None,
                               None if relayout else previous.get("raw_span"), full=full, log=log)

    if full:
        labels = find_clusters(D)
        ks = sorted(k for k in set(labels.tolist()) if k >= 0)
        new_members = {k: {ids[i] for i in np.flatnonzero(labels == k)} for k in ks}
        pairing = match_clusters(new_members, old_clusters)
    else:
        member_of = {fid: n for n, c in enumerate(old_clusters) for fid in c["members"]}
        labels = np.array([member_of.get(fid, -1) for fid in ids])
        labels = assign_to_clusters(S, labels, np.array([fid not in prev_ids for fid in ids]))
        ks = sorted(k for k in set(labels.tolist()) if k >= 0)
        new_members = {k: {ids[i] for i in np.flatnonzero(labels == k)} for k in ks}
        pairing = {k: old_clusters[k] for k in ks}
    W = memberships(S, labels) if ks else np.zeros((len(ids), 0))
    next_id = max([int(c["id"][1:]) for c in old_clusters] + [0]) + 1
    centre = Y.mean(0)
    clusters = []
    for k in ks:
        idx = np.flatnonzero(labels == k)
        old = pairing[k]
        if old:
            cid, hue = old["id"], old.get("hue")
        else:
            cid, hue = f"c{next_id}", None
            next_id += 1
        core = idx[np.argsort(-W[idx, ks.index(k)])][: max(3, len(idx) // 2)]
        clusters.append({"id": cid, "k": k, "hue": hue, "members": sorted(new_members[k]),
                         "centre": np.median(Y[core], axis=0).tolist()})
    clusters.sort(key=lambda c: int(c["id"][1:]))
    assign_hues(clusters, centre)
    custom = settings_cluster_names()
    for i, c in enumerate(clusters):
        member_recs = [records[fid] for fid in c["members"]]
        auto_name, tagline = _cluster_name(member_recs, recs)
        c["auto_name"], c["tagline"] = auto_name, tagline
        c["name"] = custom.get(c["id"], auto_name)
        c["color"] = cluster_colour(c["hue"], i)
    _dedupe_names(clusters)

    # Neighbours, with a 0-100 "how close" score calibrated on this collection: a typical pair
    # scores 0, the closest 0.1% of pairs score about 85 and up, near-twins (sequels) approach 100.
    off = S[~np.eye(len(ids), dtype=bool)]
    lo, top = float(np.percentile(off, 50)), float(np.percentile(off, 99.9))
    tau = max(1e-6, (top - lo) / 1.9)
    closeness = lambda s: int(round(100 * (1 - math.exp(-max(0.0, s - lo) / tau))))
    order = np.argsort(-S, axis=1)
    k_index = {c["k"]: j for j, c in enumerate(clusters)}

    genre_names = sorted({g for r in recs for g in r.get("genres", [])})
    films_out = []
    for row, fid in enumerate(ids):
        r = records[fid]
        nb = [j for j in order[row] if j != row][:NEIGHBOURS]
        mem = []
        if len(ks):
            top = np.argsort(-W[row])[:3]
            for t in top:
                if W[row, t] >= 0.08:
                    mem.append([k_index[ks[t]], round(float(W[row, t]), 2)])
        films_out.append(_film_entry(r, files[fid], added.get(fid, ""), Y[row], mem,
                                     [ids[j] for j in nb],
                                     [min(99, closeness(float(S[row, j]))) for j in nb],
                                     genre_names))

    meta = {
        "title": settings.title,
        "tagline": settings.tagline,
        "built": dt.date.today().isoformat(),
        "since": min((a for a in added.values() if a), default=""),
        "count": len(films_out),
        "files": sum(len(v) for v in files.values()),
        "genres": [GENRE_LABELS.get(g, g) for g in genre_names],
        "languages": {code: display_name(code) for code in sorted({r.get("original_language") or "" for r in recs} - {""})},
        "driveFolder": settings.drive_folder_url,
        "images": "https://image.tmdb.org/t/p/",
    }
    clusters_out = [{"id": c["id"], "name": c["name"], "tagline": c["tagline"], "color": c["color"],
                     "x": round(c["centre"][0], 1), "y": round(c["centre"][1], 1), "n": len(c["members"])}
                    for c in clusters]
    unplaced.sort(key=lambda u: (u["t"] or "").casefold())
    _write_js(meta, clusters_out, films_out, unplaced)

    store.write_json(LAYOUT_PATH, {
        "fingerprint": fp,
        "built": meta["built"],
        "full_count": len(ids) if full else base,
        "raw_span": lstats.get("raw_span"),
        "positions": {str(fid): [round(float(Y[i, 0]), 2), round(float(Y[i, 1]), 2)] for i, fid in enumerate(ids)},
        "clusters": [{"id": c["id"], "hue": c["hue"], "auto_name": c["auto_name"], "members": c["members"]}
                     for c in clusters],
    }, indent=None)

    problems = safety_check()
    noise = int((labels < 0).sum())
    lines = [f"Map built: {len(ids)} films in {len(clusters)} regions ({noise} sit between regions)"]
    if lstats["mode"] == "incremental":
        lines.append(f"  {lstats['kept']} films kept their exact place and region; {lstats['new']} new film(s) "
                     f"placed beside their nearest neighbours")
    elif lstats["mode"] == "full re-layout":
        lines.append(f"  full re-layout: {lstats['kept']} films kept their exact place, {lstats['moved']} moved "
                     f"(median {lstats.get('median_shift', 0)}% of the map width); region colours carried over")
    else:
        lines.append("  fresh layout")
    if unplaced:
        lines.append(f"  {len(unplaced)} file(s) are listed but not on the map until reviewed")
    lines += [f"  ! {p}" for p in problems]
    return "\n".join(lines)


def _prune_unused_records(in_use: set[int], ledger: dict) -> None:
    """Drop stored records no file points to any more (removed films, replaced matches), so the
    repository holds only what is on the map. The raw TMDB responses stay in the local cache,
    so bringing a film back costs no extra request."""
    keep = set(in_use)
    for e in ledger.values():
        for key in ("tmdb_id", "suggestion"):
            if e.get(key):
                keep.add(int(e[key]))
    for path in (DB_DIR / "films").glob("*.json"):
        if path.stem.isdigit() and int(path.stem) not in keep:
            path.unlink()


def _dedupe_names(clusters: list[dict]) -> None:
    seen = Counter(c["name"] for c in clusters)
    for c in clusters:
        if seen[c["name"]] > 1 and c["tagline"]:
            first = c["tagline"].split(", ")[0]
            c["name"] = f"{c['name']}: {first}"


def _film_entry(r: dict, files: list[str], added: str, xy, mem, nb_ids, nb_scores, genre_names) -> dict:
    gi = {g: i for i, g in enumerate(genre_names)}
    e = {
        "id": r["id"],
        "t": r["title"],
        "y": _year(r),
        "xy": [round(float(xy[0]), 1), round(float(xy[1]), 1)],
    }
    if r.get("original_title") and r["original_title"] != r["title"]:
        e["o"] = r["original_title"]
    e.update({
        "r": r.get("runtime") or 0,
        "g": [gi[g] for g in r.get("genres", []) if g in gi],
        "l": r.get("original_language") or "",
        "c": (r.get("origin_country") or r.get("production_countries") or [])[:3],
        "dir": _people(r, "director", 4),
        "wri": _people(r, "writer", 3),
        "dop": _people(r, "cinematography", 1),
        "mus": _people(r, "music", 1),
        "cast": [c["name"] for c in r.get("cast", [])[:10]],
        "k": [k["name"] for k in r.get("keywords", [])[:14]],
        "ov": r.get("overview") or "",
        "tg": r.get("tagline") or "",
        "p": r.get("poster_path") or "",
        "b": r.get("backdrop_path") or "",
        "va": round(r.get("vote_average") or 0, 1),
        "vc": r.get("vote_count") or 0,
        "m": mem,
        "n": nb_ids,
        "s": nb_scores,
        "f": files,
    })
    if r.get("certification"):
        e["cert"] = r["certification"]
    if r.get("collection"):
        e["col"] = r["collection"]["name"]
    if r.get("imdb_id"):
        e["im"] = r["imdb_id"]
    if r.get("trailer"):
        e["tr"] = r["trailer"]
    if added:
        e["a"] = added
    return e


def _write_js(meta: dict, clusters: list[dict], films: list[dict], unplaced: list[dict]) -> None:
    def j(x):  # pure ASCII output: no encoding can ever garble it
        return json.dumps(x, ensure_ascii=True, separators=(",", ":"))
    lines = [
        "/* Generated by moviemap (python -m moviemap build). Do not edit by hand. */",
        "window.MOVIE_MAP_DATA = {",
        f'"meta":{j(meta)},',
        f'"clusters":{j(clusters)},',
        f'"unplaced":{j(unplaced)},',
        '"films":[',
    ]
    for i, f in enumerate(sorted(films, key=lambda f: f["id"])):
        lines.append(j(f) + ("," if i < len(films) - 1 else ""))
    lines.append("]};")
    store.write_text_atomic(DATA_JS, "\n".join(lines) + "\n")


# --- safety -------------------------------------------------------------------------------------
def safety_check() -> list[str]:
    """Everything the browser loads must be free of the TMDB key and of the forbidden word."""
    problems = []
    key = (load_tmdb_credential() or "").strip()
    public = [INDEX_HTML] + sorted(p for p in DATA_DIR.glob("**/*") if p.is_file())
    for path in public:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if key and key.casefold() in text.casefold():
            problems.append(f"{path.name} contains the TMDB key!")
        if re.search(r"api_key|themoviedb\.org/3/|api\.tmdb\.org", text):
            problems.append(f"{path.name} mentions the TMDB API itself")
        if path == INDEX_HTML:
            own = re.sub(r"font-family", "", text, flags=re.IGNORECASE)
            for n, line in enumerate(own.splitlines(), 1):
                if FORBIDDEN_WORDS.search(line):
                    problems.append(f"index.html line {n} uses the word 'family'")
    if DATA_JS.exists():
        text = DATA_JS.read_text(encoding="ascii", errors="replace")
        m = re.search(r'^"meta":(.*),$', text, re.MULTILINE)
        c = re.search(r'^"clusters":(.*),$', text, re.MULTILINE)
        for label, chunk in (("page settings", m), ("cluster names", c)):
            if chunk and FORBIDDEN_WORDS.search(json.loads(chunk.group(1)).__str__()):
                problems.append(f"the {label} in movies.js use the word 'family'")
        size = DATA_JS.stat().st_size
        if size > 8_000_000:
            problems.append(f"movies.js is {size / 1e6:.1f} MB; consider trimming fields")
    films_size = sum(p.stat().st_size for p in (DB_DIR / "films").glob("*.json"))
    if films_size > 60_000_000:
        problems.append(f"db/films is {films_size / 1e6:.0f} MB")
    if ENV_PATH.exists() and (DATA_DIR / ENV_PATH.name).exists():
        problems.append("a copy of .env sits in data/")
    return problems
