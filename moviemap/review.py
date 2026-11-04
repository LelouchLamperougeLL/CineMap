"""The local review page: settle flagged matches by clicking instead of typing.

It runs a small web server on 127.0.0.1 only, for as long as the command runs. Every
decision is written to corrections.txt (the durable record), so nothing here is needed
to keep a fix: the server is just a convenient way to write those lines. It is never
deployed; the public site has no way to reach it.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import store
from .config import HTTP_CACHE, LAYOUT_PATH, load_settings, load_tmdb_credential
from .tmdb import TMDB, ResponseCache, TMDBError

PAGE = Path(__file__).with_name("review_page.html")
LOCK = threading.Lock()


class ReviewApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.token = secrets.token_urlsafe(24)
        self.tmdb = TMDB(load_tmdb_credential(), ResponseCache(HTTP_CACHE),
                         rate=self.settings.requests_per_second, log=lambda *a: None)
        self.dirty = False

    def names(self) -> list[str]:
        return store.read_collection(self.settings.source_path)

    def state(self) -> dict:
        names = self.names()
        files = store.load_matches()["files"]
        decisions, problems = store.read_corrections()
        out = []
        for n in names:
            e = files.get(n) or {"status": "new"}
            item = {"file": n, **{k: e.get(k) for k in ("status", "tmdb_id", "suggestion", "confidence", "why",
                                                       "flags", "candidates", "parsed", "decision")}}
            fid = e.get("tmdb_id")
            if fid:
                rec = store.load_film(int(fid)) or {}
                item["film"] = {"id": fid, "title": rec.get("title"), "year": (rec.get("release_date") or "")[:4],
                                "poster_path": rec.get("poster_path"), "original_title": rec.get("original_title"),
                                "directors": [c["name"] for c in rec.get("crew", []) if c.get("role") == "director"][:2]}
            d = store.find_decision(decisions, n)
            item["has_decision"] = d is not None
            out.append(item)
        order = {"flagged": 0, "error": 1, "new": 2, "manual": 3, "matched": 4, "skipped": 5}
        out.sort(key=lambda x: (order.get(x["status"], 9), x.get("confidence") != "good", x["file"].casefold()))
        return {"files": out, "problems": problems, "dirty": self.dirty, "token": self.token,
                "regions": self.regions()}

    def regions(self) -> list[dict]:
        from .build import settings_cluster_names
        layout = store.read_json(LAYOUT_PATH, default={}) or {}
        custom = settings_cluster_names()
        out = []
        for c in layout.get("clusters", []):
            recs = [r for r in (store.load_film(i) for i in c["members"]) if r]
            recs.sort(key=lambda r: -(r.get("vote_count") or 0))
            out.append({"id": c["id"], "name": custom.get(c["id"], c.get("auto_name", "")),
                        "auto_name": c.get("auto_name", ""), "n": len(c["members"]),
                        "examples": [r["title"] for r in recs[:5]]})
        out.sort(key=lambda r: -r["n"])
        return out

    def decide(self, file_name: str, decision: str) -> dict:
        from .sync import sync
        names = self.names()
        if file_name not in names:
            raise ValueError("That file is not in the collection listing any more.")
        if decision == "auto":
            store.remove_correction(file_name)
        else:
            parsed = store.parse_decision(decision)
            if parsed is None:
                raise ValueError("Use a TMDB id, a TMDB or IMDb link, an IMDb id (tt...), or 'skip'.")
            comment = ""
            if parsed[0] == "tmdb":
                raw = self.tmdb.movie(int(parsed[1]))
                if not raw or not raw.get("id"):
                    raise ValueError(f"TMDB has no film with id {parsed[1]}.")
                comment = f"{raw.get('title')} ({(raw.get('release_date') or '????')[:4]})"
            text = "skip" if parsed[0] == "skip" else (str(parsed[1]) if parsed[0] == "tmdb" else parsed[1])
            store.set_correction(file_name, text, comment)
        rep = sync(names, self.tmdb, log=lambda *a: None, rematch={file_name})
        if rep.stopped:
            raise ValueError(rep.stopped)
        self.dirty = True
        return {"ok": True}

    def lookup(self, decision: str) -> dict:
        """What a typed or pasted decision refers to, so the page can confirm it before saving."""
        parsed = store.parse_decision(decision)
        if parsed is None:
            raise ValueError("Use a TMDB id, a TMDB or IMDb link, an IMDb id (tt...), or 'skip'.")
        kind, value = parsed
        if kind in ("skip", "ok"):
            return {"kind": kind}
        tmdb_id = self.tmdb.find_imdb(str(value)) if kind == "imdb" else int(value)
        raw = self.tmdb.movie(tmdb_id) if tmdb_id else None
        if not raw or not raw.get("id"):
            raise ValueError(f"TMDB has no film for {value}.")
        return {"kind": "tmdb", "id": raw["id"], "title": raw.get("title"),
                "year": (raw.get("release_date") or "")[:4], "decision": str(raw["id"])}

    def search(self, query: str, year: str | None) -> list[dict]:
        y = int(year) if year and year.isdigit() else None
        results = self.tmdb.search(query, y, refresh=True)
        if y and not results:
            results = self.tmdb.search(query, None, refresh=True)
        out = []
        for r in results[:12]:
            out.append({"id": r.get("id"), "title": r.get("title"), "original_title": r.get("original_title"),
                        "year": (r.get("release_date") or "")[:4], "language": r.get("original_language"),
                        "votes": r.get("vote_count"), "poster_path": r.get("poster_path"),
                        "overview": (r.get("overview") or "")[:240]})
        return out

    def build(self) -> str:
        from .build import build_map
        msg = build_map(force=False, log=lambda *a: None)
        self.dirty = False
        return msg


def _handler(app: ReviewApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MovieMapReview/1.0"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            url = urlparse(self.path)
            if url.path == "/":
                html = PAGE.read_text(encoding="utf-8").replace("{{TOKEN}}", app.token)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path.startswith("/api/") and self.headers.get("X-Review-Token") != app.token:
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            if url.path == "/api/state":
                with LOCK:
                    return self._json(app.state())
            if url.path == "/api/lookup":
                try:
                    with LOCK:
                        return self._json(app.lookup((parse_qs(url.query).get("d") or [""])[0]))
                except (ValueError, TMDBError) as err:
                    return self._json({"error": str(err)}, 400)
            if url.path == "/api/search":
                qs = parse_qs(url.query)
                try:
                    with LOCK:
                        return self._json(app.search((qs.get("q") or [""])[0], (qs.get("year") or [""])[0]))
                except TMDBError as err:
                    return self._json({"error": str(err)}, 502)
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if (not self._host_ok() or self.headers.get("X-Review-Token") != app.token
                    or not (self.headers.get("Content-Type") or "").startswith("application/json")):
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            length = min(int(self.headers.get("Content-Length") or 0), 100_000)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad request"}, 400)
            try:
                with LOCK:
                    if self.path == "/api/decide":
                        return self._json(app.decide(str(body.get("file", "")), str(body.get("decision", "")).strip()))
                    if self.path == "/api/build":
                        return self._json({"message": app.build()})
            except (ValueError, TMDBError) as err:
                return self._json({"error": str(err)}, 400)
            return self._send(404, b"not found", "text/plain")

    return Handler


class _ExclusiveServer(ThreadingHTTPServer):
    """On Windows a second program may otherwise bind the same port and take over requests."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(port: int = 8765, open_browser: bool = True) -> int:
    app = ReviewApp()
    server = None
    for p in range(port, port + 20):
        try:
            server = _ExclusiveServer(("127.0.0.1", p), _handler(app))
            break
        except OSError:
            continue
    if server is None:
        print("Could not find a free port for the review page.")
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Review page: {url}")
    print("Leave this window open while you review. Press Ctrl+C here when you are done.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if app.dirty:
            print("\nRebuilding the map with your decisions ...")
            print(app.build())
        app.tmdb.close()
    return 0
