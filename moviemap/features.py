"""Turn film records into a similarity matrix.

Instead of one long vector compared with a single metric, each kind of metadata
is its own block with its own weight, and the similarity of two films is the
weighted sum of per-block similarities:

    S(a, b) = sum_b  w_b * s_b(a, b)          (the weights add up to 1)

* Genres, keywords, people, studios, language/countries: multi-hot vectors
  weighted by IDF (a keyword shared by 3 films says more than one shared by
  300), L2-normalised, compared by cosine. Features seen in only one film can't
  link two films, so they are dropped (this is what "most common" means here,
  and it adapts as the collection grows).
* Cast is weighted by billing: sharing a lead counts more than sharing an extra.
* Numbers (year, runtime, popularity, rating) are log-scaled where skewed,
  standardised, and compared with a Gaussian kernel, so "close in value" means
  "similar".
* Missing data is not evidence: when TMDB simply has no keywords (or genres) for
  a film, that block counts as average for it instead of as "shares nothing".
  This keeps thinly documented new releases from clumping together.

Semantic blocks (genres, keywords) carry most of the weight; raw numbers little.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np

BLOCK_WEIGHTS = {
    "genres": 0.20,
    "keywords": 0.27,
    "directors": 0.11,
    "writers": 0.04,
    "cast": 0.10,
    "crew": 0.05,
    "studios": 0.06,
    "place": 0.09,
    "numbers": 0.08,
}
MISSING_AWARE = {"genres", "keywords", "cast"}

# Keywords that describe the production or the credits, not the film.
KEYWORD_STOPLIST = {
    "duringcreditsstinger", "aftercreditsstinger", "woman director", "short film", "3d", "imax",
    "live action remake", "anime", "remake", "sequel", "prequel", "based on movie",
}


@dataclass
class Block:
    name: str
    matrix: np.ndarray        # films x features, rows L2-normalised (zero rows allowed)
    present: np.ndarray       # bool per film: does the film have data for this block
    vocab: list[str]          # feature labels, for explanations


def _idf_block(name: str, items: list[Counter], present: np.ndarray, min_df: int = 2,
               idf_power: float = 1.0, n_idf: int | None = None) -> Block:
    df = Counter()
    for c in items:
        df.update(c.keys())
    vocab = sorted(k for k, v in df.items() if v >= min_df)
    index = {k: i for i, k in enumerate(vocab)}
    n = len(items)
    base = n_idf or n  # frozen between full re-layouts, so one new film doesn't shift every weight
    m = np.zeros((n, len(vocab)), dtype=np.float64)
    for row, c in enumerate(items):
        for k, weight in c.items():
            j = index.get(k)
            if j is not None:
                m[row, j] = weight * (math.log((1 + base) / (1 + df[k])) + 1.0) ** idf_power
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    m = np.divide(m, norms, out=np.zeros_like(m), where=norms > 0)
    return Block(name, m, present, vocab)


def _year(rec: dict) -> float | None:
    d = rec.get("release_date") or ""
    return float(d[:4]) if d[:4].isdigit() else None


def build_blocks(records: list[dict], n_idf: int | None = None) -> list[Block]:
    n = len(records)
    genres, keywords, directors, writers, cast, crew, studios, place = ([] for _ in range(8))
    for r in records:
        genres.append(Counter({g: 1.0 for g in r.get("genres", [])}))
        kws = [k["name"].casefold() for k in r.get("keywords", []) if k.get("name")]
        keywords.append(Counter({k: 1.0 for k in kws if k not in KEYWORD_STOPLIST}))
        people = r.get("crew", [])
        directors.append(Counter({f"p{c['id']}": 1.0 for c in people if c["role"] == "director"}))
        writers.append(Counter({f"p{c['id']}": 1.0 for c in people if c["role"] == "writer"}))
        crew.append(Counter({f"{c['role']}:p{c['id']}": 1.0 for c in people
                             if c["role"] in ("cinematography", "music", "editor", "design")}))
        cc = Counter()
        for order, c in enumerate(r.get("cast", [])[:15]):
            cc[f"p{c['id']}"] = max(cc[f"p{c['id']}"], 1.0 / math.sqrt(1.0 + order / 3.0))
        cast.append(cc)
        st = Counter({f"co{c['id']}": 1.0 for c in r.get("production_companies", [])})
        if r.get("collection"):
            st[f"coll{r['collection']['id']}"] = 1.5
        studios.append(st)
        pl = Counter()
        if r.get("original_language"):
            pl[f"lang:{r['original_language']}"] = 1.0
        countries = r.get("origin_country") or r.get("production_countries") or []
        for c in countries[:4]:
            pl[f"country:{c}"] = 0.6
        place.append(pl)

    has = lambda items: np.array([len(c) > 0 for c in items])
    raw_kw = np.array([len(r.get("keywords", [])) > 0 for r in records])
    blocks = [
        _idf_block("genres", genres, has(genres), min_df=1, idf_power=0.5, n_idf=n_idf),
        _idf_block("keywords", keywords, raw_kw, n_idf=n_idf),
        _idf_block("directors", directors, np.ones(n, bool), n_idf=n_idf),
        _idf_block("writers", writers, np.ones(n, bool), n_idf=n_idf),
        _idf_block("cast", cast, has(cast), n_idf=n_idf),
        _idf_block("crew", crew, np.ones(n, bool), n_idf=n_idf),
        _idf_block("studios", studios, np.ones(n, bool), n_idf=n_idf),
        _idf_block("place", place, np.ones(n, bool), min_df=1, idf_power=0.5, n_idf=n_idf),
    ]
    return blocks


def numeric_matrix(records: list[dict], stats: list | None = None) -> tuple[np.ndarray, list]:
    """Standardised numeric features: year, log runtime, log popularity, rating.
    `stats` ([mean, std] per column) is reused between full re-layouts for stability."""
    cols, used = [], []
    year = [_year(r) for r in records]
    runtime = [math.log(r["runtime"]) if r.get("runtime") else None for r in records]
    votes = [math.log1p(r.get("vote_count") or 0) for r in records]
    rating = [r["vote_average"] if (r.get("vote_count") or 0) >= 5 else None for r in records]
    for c, (values, weight) in enumerate(((year, 1.0), (runtime, 0.6), (votes, 0.6), (rating, 0.5))):
        if stats and c < len(stats):
            mean, std = stats[c]
        else:
            known = [v for v in values if v is not None]
            mean = float(np.mean(known)) if known else 0.0
            std = float(np.std(known)) or 1.0
        used.append([mean, std])
        cols.append([weight * (((v if v is not None else mean) - mean) / std) for v in values])
    return np.array(cols, dtype=np.float64).T, used


def similarity(records: list[dict], weights: dict[str, float] | None = None,
               calibration: dict | None = None) -> tuple[np.ndarray, list[Block], dict]:
    """Weighted block similarity, films x films, values in [0, 1].

    `calibration` holds the collection-wide statistics (rarity base, average block
    similarities, number scaling) from the last full re-layout. Reusing them on ordinary
    updates means a new film changes only the similarities it actually takes part in,
    instead of nudging every value (and so every line of the published data).
    """
    weights = weights or BLOCK_WEIGHTS
    calibration = calibration or {}
    n = len(records)
    n_idf = int(calibration.get("n") or n)
    blocks = build_blocks(records, n_idf=n_idf)
    mus = dict(calibration.get("mu") or {})
    total = np.zeros((n, n))
    for b in blocks:
        s = b.matrix @ b.matrix.T
        if b.name in MISSING_AWARE:
            both = np.outer(b.present, b.present)
            if b.name not in mus:
                off = ~np.eye(n, dtype=bool)
                mus[b.name] = float(s[both & off].mean()) if (both & off).any() else 0.0
            s = np.where(both, s, mus[b.name])
        total += weights[b.name] * s
    z, num_stats = numeric_matrix(records, calibration.get("num"))
    d2 = ((z[:, None, :] - z[None, :, :]) ** 2).sum(-1)
    total += weights["numbers"] * np.exp(-d2 / (2 * 1.5 ** 2))
    np.fill_diagonal(total, 1.0)
    total = np.clip((total + total.T) / 2, 0.0, 1.0)
    used = {"n": n_idf, "mu": {k: round(v, 6) for k, v in mus.items()},
            "num": [[round(a, 6), round(b, 6)] for a, b in num_stats]}
    return np.round(total, 9), blocks, used
