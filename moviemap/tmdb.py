"""A small, polite TMDB client.

* Every response is cached in a local SQLite file the moment it arrives, so a run
  can be stopped at any point (Ctrl+C, closed window, lost network) and the next
  run picks up where it left off without asking TMDB for anything twice.
* Requests are spaced out (a few per second) and retried with backoff on
  rate limits, server errors and network hiccups.
* The key never appears in cache keys, logs or error messages.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import socket
import sqlite3
import ssl
import threading
import time
import urllib.parse
import zlib
from pathlib import Path

# api.tmdb.org is TMDB's alternate API domain. Some networks (several Indian ISPs, for
# one) reset connections to api.themoviedb.org but not to it, so it goes first.
API_HOSTS = ("api.tmdb.org", "api.themoviedb.org")
DETAIL_APPENDS = "keywords,credits,release_dates,alternative_titles,translations,external_ids,videos"
USER_AGENT = "MovieMap/1.0 (personal film collection map)"


class TMDBError(Exception):
    pass


class NetworkDown(TMDBError):
    """TMDB could not be reached even after waiting and retrying."""


class AuthError(TMDBError):
    pass


class OfflineMiss(TMDBError):
    """Raised in offline mode when a response is not cached yet."""


class ResponseCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            " key TEXT PRIMARY KEY, status INTEGER NOT NULL, body BLOB, fetched_at TEXT NOT NULL)"
        )

    def get(self, key: str) -> tuple[int, object, str] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT status, body, fetched_at FROM responses WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        status, body, fetched_at = row
        data = json.loads(zlib.decompress(body).decode("utf-8")) if body is not None else None
        return status, data, fetched_at

    def put(self, key: str, status: int, data: object) -> None:
        body = None if data is None else zlib.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"), 6)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses (key, status, body, fetched_at) VALUES (?, ?, ?, ?)",
                (key, status, body, now),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()


class TMDB:
    def __init__(self, credential: str | None, cache: ResponseCache, *, rate: float = 4.0,
                 offline: bool = False, log=print):
        self._credential = (credential or "").strip()
        self._cache = cache
        self._interval = 1.0 / max(rate, 0.1)
        self._next_slot = 0.0
        self.offline = offline or not self._credential
        self.log = log
        self.network_calls = 0
        self.cache_hits = 0
        self._host = 0
        self._conn: http.client.HTTPSConnection | None = None
        self.fresh_searches = False  # True for rematch and the review page: ask TMDB again

    # -- low level -----------------------------------------------------------
    @staticmethod
    def cache_key(path: str, params: dict) -> str:
        clean = {k: v for k, v in params.items() if v is not None and k != "api_key"}
        return path + "?" + urllib.parse.urlencode(sorted(clean.items()))

    def _throttle(self) -> None:
        now = time.monotonic()
        if now < self._next_slot:
            time.sleep(self._next_slot - now)
        self._next_slot = max(now, self._next_slot) + self._interval

    def _connection(self) -> http.client.HTTPSConnection:
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(
                API_HOSTS[self._host], timeout=25, context=ssl.create_default_context())
        return self._conn

    def _drop_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def close(self) -> None:
        self._drop_connection()

    def _fetch(self, path: str, params: dict) -> tuple[int, object]:
        query = {k: v for k, v in params.items() if v is not None}
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, "Connection": "keep-alive"}
        if self._credential.startswith("eyJ"):  # v4 read-access token
            headers["Authorization"] = f"Bearer {self._credential}"
        else:
            query["api_key"] = self._credential
        target = "/3" + path + "?" + urllib.parse.urlencode(query)
        network_failures = 0
        rate_limited = 0
        server_errors = 0
        while True:
            self._throttle()
            self.network_calls += 1
            try:
                conn = self._connection()
                conn.request("GET", target, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                status = resp.status
                retry_after = resp.getheader("Retry-After")
            except (http.client.HTTPException, socket.timeout, TimeoutError, ConnectionError, OSError) as err:
                self._drop_connection()
                network_failures += 1
                if network_failures >= 2:
                    self._host = (self._host + 1) % len(API_HOSTS)  # try the other domain
                if network_failures > 8:
                    raise NetworkDown(
                        f"Cannot reach TMDB ({_describe(err)}). Progress so far is saved; run again later."
                    ) from None
                self._backoff(network_failures, _describe(err))
                continue
            if status == 200:
                return 200, json.loads(body.decode("utf-8"))
            if status == 404:
                return 404, None
            if status == 401:
                raise AuthError("TMDB rejected the API key (HTTP 401). Check TMDB_API_KEY in .env.")
            if status == 429 and rate_limited < 12:
                rate_limited += 1
                wait = _seconds(retry_after) or min(5.0 * rate_limited, 30.0)
                self.log(f"  TMDB asked us to slow down; waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            if status >= 500 and server_errors < 6:
                server_errors += 1
                self._drop_connection()
                self._backoff(server_errors, f"TMDB server error {status}")
                continue
            raise TMDBError(f"TMDB answered HTTP {status} for {path}")

    def _backoff(self, attempt: int, why: str) -> None:
        wait = min(2 ** (attempt - 1), 30)
        if attempt > 1:
            self.log(f"  {why}; retrying in {wait}s")
        time.sleep(wait)

    def get(self, path: str, params: dict | None = None, *, refresh: bool = False,
            retry_empty_after_days: float | None = None) -> object:
        params = dict(params or {})
        key = self.cache_key(path, params)
        if not refresh:
            hit = self._cache.get(key)
            if hit is not None and not (retry_empty_after_days is not None and not self.offline
                                        and _is_empty(hit[1]) and _age_days(hit[2]) > retry_empty_after_days):
                self.cache_hits += 1
                return hit[1]
        if self.offline:
            raise OfflineMiss(key)
        status, data = self._fetch(path, params)
        self._cache.put(key, status, data)
        return data

    def fetched_at(self, path: str, params: dict | None = None) -> str | None:
        hit = self._cache.get(self.cache_key(path, dict(params or {})))
        return hit[2] if hit else None

    # -- endpoints -----------------------------------------------------------
    def search(self, query: str, year: int | None = None, *, refresh: bool | None = None) -> list[dict]:
        # An empty answer is asked again after a week: a film added before TMDB lists it
        # (brand-new releases) must not stay unmatched forever.
        data = self.get("/search/movie", {
            "query": query, "year": year, "include_adult": "false", "language": "en-US", "page": 1,
        }, refresh=self.fresh_searches if refresh is None else refresh, retry_empty_after_days=7)
        return list((data or {}).get("results") or [])

    def movie(self, movie_id: int, *, refresh: bool = False) -> dict | None:
        return self.get(f"/movie/{int(movie_id)}", {
            "language": "en-US", "append_to_response": DETAIL_APPENDS,
            "include_video_language": "en,null",
        }, refresh=refresh)

    def find_imdb(self, imdb_id: str) -> int | None:
        data = self.get(f"/find/{imdb_id}", {"external_source": "imdb_id", "language": "en-US"}) or {}
        results = data.get("movie_results") or []
        return int(results[0]["id"]) if results else None

    def genres(self) -> list[dict]:
        data = self.get("/genre/movie/list", {"language": "en-US"}) or {}
        return list(data.get("genres") or [])


def _is_empty(data: object) -> bool:
    return data is None or (isinstance(data, dict) and "results" in data and not data["results"])


def _age_days(fetched_at: str) -> float:
    try:
        then = dt.datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return 1e9
    return (dt.datetime.now(dt.timezone.utc) - then).total_seconds() / 86400


def _seconds(value: str | None) -> float | None:
    try:
        return max(1.0, min(float(value), 120.0)) if value else None
    except (TypeError, ValueError):
        return None


def _describe(err: BaseException) -> str:
    reason = getattr(err, "reason", None)
    text = str(reason or err) or err.__class__.__name__
    return text if "api_key" not in text else "network error"


# --- trimming a full TMDB record down to what we keep ------------------------
KEY_CREW_JOBS = {
    "Director": "director",
    "Screenplay": "writer", "Writer": "writer", "Story": "writer", "Novel": "writer",
    "Author": "writer", "Original Story": "writer", "Book": "writer", "Comic Book": "writer",
    "Short Story": "writer", "Play": "writer", "Theatre Play": "writer", "Characters": "writer",
    "Director of Photography": "cinematography", "Cinematography": "cinematography",
    "Original Music Composer": "music", "Music": "music", "Composer": "music",
    "Editor": "editor", "Production Design": "design",
    "Producer": "producer",
}
RECORD_SCHEMA = 1


def trim_record(d: dict) -> dict:
    """Keep the useful parts of a /movie/{id} response (with appends)."""
    credits = d.get("credits") or {}
    cast = sorted(credits.get("cast") or [], key=lambda c: (c.get("order", 999), c.get("id", 0)))
    crew: list[dict] = []
    seen = set()
    for c in credits.get("crew") or []:
        role = KEY_CREW_JOBS.get(c.get("job") or "")
        if not role or (c.get("id"), role) in seen:
            continue
        seen.add((c.get("id"), role))
        crew.append({"id": c.get("id"), "name": c.get("name"), "role": role, "job": c.get("job")})
    producers = [c for c in crew if c["role"] == "producer"][:6]
    crew = [c for c in crew if c["role"] != "producer"] + producers

    release_years: set[int] = set()
    cert = ""
    for country in (d.get("release_dates") or {}).get("results") or []:
        for rel in country.get("release_dates") or []:
            date = rel.get("release_date") or ""
            if rel.get("type") in (1, 2, 3) and date[:4].isdigit():
                release_years.add(int(date[:4]))
            if country.get("iso_3166_1") == "US" and rel.get("certification") and not cert:
                cert = rel["certification"].strip()

    titles: list[str] = []
    for t in (d.get("alternative_titles") or {}).get("titles") or []:
        if t.get("title"):
            titles.append(t["title"].strip())
    for t in (d.get("translations") or {}).get("translations") or []:
        title = ((t.get("data") or {}).get("title") or "").strip()
        if title:
            titles.append(title)
    alt = []
    for t in titles:
        if t not in alt and t not in (d.get("title"), d.get("original_title")):
            alt.append(t)

    trailer = None
    videos = [v for v in (d.get("videos") or {}).get("results") or [] if v.get("site") == "YouTube"]
    videos.sort(key=lambda v: (v.get("type") != "Trailer", not v.get("official"),
                               v.get("iso_639_1") not in ("en", None), v.get("published_at") or ""))
    if videos and videos[0].get("type") in ("Trailer", "Teaser"):
        trailer = videos[0].get("key")

    ext = d.get("external_ids") or {}
    coll = d.get("belongs_to_collection") or None
    return {
        "id": d["id"],
        "imdb_id": d.get("imdb_id") or ext.get("imdb_id") or None,
        "wikidata_id": ext.get("wikidata_id") or None,
        "title": d.get("title") or d.get("original_title") or "",
        "original_title": d.get("original_title") or "",
        "original_language": d.get("original_language") or "",
        "release_date": d.get("release_date") or "",
        "release_years": sorted(release_years),
        "status": d.get("status") or "",
        "runtime": d.get("runtime") or 0,
        "genres": [g["name"] for g in d.get("genres") or [] if g.get("name")],
        "overview": (d.get("overview") or "").strip(),
        "tagline": (d.get("tagline") or "").strip(),
        "poster_path": d.get("poster_path"),
        "backdrop_path": d.get("backdrop_path"),
        "vote_average": round(float(d.get("vote_average") or 0), 2),
        "vote_count": int(d.get("vote_count") or 0),
        "popularity": round(float(d.get("popularity") or 0), 3),
        "budget": int(d.get("budget") or 0),
        "revenue": int(d.get("revenue") or 0),
        "certification": cert,
        "origin_country": list(d.get("origin_country") or []),
        "production_countries": [c["iso_3166_1"] for c in d.get("production_countries") or []
                                 if c.get("iso_3166_1")],
        "spoken_languages": [s["iso_639_1"] for s in d.get("spoken_languages") or [] if s.get("iso_639_1")],
        "production_companies": [{"id": c.get("id"), "name": c.get("name")}
                                 for c in d.get("production_companies") or [] if c.get("name")],
        "collection": {"id": coll.get("id"), "name": coll.get("name")} if coll else None,
        "keywords": [{"id": k.get("id"), "name": k.get("name")}
                     for k in (d.get("keywords") or {}).get("keywords") or [] if k.get("name")],
        "cast": [{"id": c.get("id"), "name": c.get("name"), "character": (c.get("character") or "").strip(),
                  "profile_path": c.get("profile_path")} for c in cast[:24]],
        "crew": crew,
        "alt_titles": alt[:80],
        "trailer": trailer,
        "schema": RECORD_SCHEMA,
    }
