"""Implementation of the command-line commands."""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
import time

from . import store
from .config import ENV_PATH, HTTP_CACHE, LAYOUT_PATH, ROOT, load_settings, load_tmdb_credential
from .tmdb import TMDB, ResponseCache

# What gets committed when publishing (everything else stays local or is git-ignored).
PUBLISH_PATHS = ["index.html", "manifest.webmanifest", "sw.js", "assets", "data", "db", "corrections.txt",
                 "settings.toml", "files.csv",
                 "README.md", "moviemap", "tests", "requirements.txt", "update.cmd", "review.cmd",
                 ".github", ".gitignore", ".gitattributes"]


def _client(settings) -> TMDB:
    credential = load_tmdb_credential()
    if not credential:
        print("No TMDB key found. Put a line like  TMDB_API_KEY=your_key  in the file .env "
              "(next to index.html). Working from the local cache only.")
    return TMDB(credential, ResponseCache(HTTP_CACHE), rate=settings.requests_per_second, log=print)


def _read_names(settings) -> list[str]:
    src = settings.source_path
    if not src.exists():
        raise SystemExit(f"Can't find the collection listing: {src}\n"
                         f"Export it from Google Drive as files.csv, or set 'source' in settings.toml.")
    return store.read_collection(src)


def _print_sync(rep) -> None:
    print()
    print(f"Collection: {rep.total} files"
          + (f", {len(rep.new)} new" if rep.new else "")
          + (f", {len(rep.removed)} removed (kept aside in case they come back)" if rep.removed else "")
          + (f", {len(rep.restored)} back again" if rep.restored else ""))
    if rep.matched:
        print(f"  matched automatically: {len(rep.matched)}")
    if rep.manual:
        print(f"  decisions from corrections.txt applied: {len(rep.manual)}")
    if rep.skipped:
        print(f"  skipped (not films): {len(rep.skipped)}")
        for n in rep.skipped[:10]:
            print(f"     - {n}")
    for p in rep.problems:
        print(f"  ! {p}")
    if rep.stale_corrections:
        print(f"  ! corrections.txt has entries for files no longer in the listing: "
              f"{', '.join(rep.stale_corrections)}")
    if rep.errors:
        print(f"  ! {len(rep.errors)} files hit a TMDB error and will be retried next time")
    if rep.flagged:
        print(f"\n  {len(rep.flagged)} file(s) need your review (they stay off the map until settled):")
        ledger = store.load_matches()["files"]
        for n in rep.flagged[:25]:
            flags = "; ".join(ledger.get(n, {}).get("flags", []))
            print(f"     - {n}\n         {flags}")
        if len(rep.flagged) > 25:
            print(f"     ... and {len(rep.flagged) - 25} more")
        print("  Run review.cmd (or: python -m moviemap review) to settle them.")
    if rep.stopped:
        print(f"\nStopped early: {rep.stopped}")


def cmd_update(args) -> int:
    from .sync import sync
    settings = load_settings()
    names = _read_names(settings)
    tmdb = _client(settings)
    print(f"Reading {settings.source_path.name}: {len(names)} files")
    t0 = time.time()
    try:
        interactive = not getattr(args, "no_prompt", False) and sys.stdin is not None and sys.stdin.isatty()
        rep = sync(names, tmdb, confirm_removal=_confirm_removal if interactive else None)
    finally:
        tmdb.close()
    _print_sync(rep)
    print(f"\nTMDB requests this run: {tmdb.network_calls} (answered from cache: {tmdb.cache_hits}); "
          f"{time.time() - t0:.0f}s")
    if rep.stopped:
        return 2
    code = _build(force=False)
    interactive = not getattr(args, "no_prompt", False) and sys.stdin is not None and sys.stdin.isatty()
    if interactive and rep.flagged and _ask("Open the review page now?", default=True):
        cmd_review(argparse.Namespace(port=8765, no_browser=False))
    if interactive and code == 0 and _git_ready() is None and _pending_changes():
        if _ask("Publish the updated map to GitHub now?", default=False):
            return cmd_publish(args)
    return code


def _confirm_removal(missing: list[str]) -> bool:
    print(f"\n{len(missing)} files that were in the collection are missing from the listing, for example:")
    for name in missing[:8]:
        print(f"   - {name}")
    print("If the export was cut short, answer no and export it again.")
    return _ask("Treat them as removed? (they are kept aside and come back if listed again)", default=False)


def _ask(question: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"\n{question} {hint} ").strip().casefold()
    except (EOFError, KeyboardInterrupt):
        return False
    return default if not answer else answer in ("y", "yes")


def _build(force: bool, relayout: bool = False) -> int:
    try:
        from .build import build_map
    except ImportError as err:  # numpy & co. missing
        print(f"\nCan't build the map: {err}. Run update.cmd once to install what's needed.")
        return 1
    result = build_map(force=force, relayout=relayout)
    print(result)
    return 0


def cmd_build(args) -> int:
    return _build(force=True, relayout=args.relayout)


def cmd_status(args) -> int:
    settings = load_settings()
    names = _read_names(settings)
    files = store.load_matches()["files"]
    counts: dict[str, int] = {}
    for n in names:
        status = files.get(n, {}).get("status", "new (not matched yet)")
        counts[status] = counts.get(status, 0) + 1
    print(f"{len(names)} files in {settings.source_path.name}")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:24s} {v}")
    conf: dict[str, int] = {}
    for n in names:
        e = files.get(n, {})
        if e.get("status") == "matched":
            conf[e.get("confidence", "?")] = conf.get(e.get("confidence", "?"), 0) + 1
    if conf:
        print("  automatic matches by confidence: " + ", ".join(f"{k} {v}" for k, v in sorted(conf.items())))
    flagged = [n for n in names if files.get(n, {}).get("status") == "flagged"]
    for n in flagged:
        print(f"  ? {n}\n      {'; '.join(files[n].get('flags', []))}")
    layout = store.read_json(LAYOUT_PATH, default={}) or {}
    if layout.get("clusters"):
        from .build import settings_cluster_names
        custom = settings_cluster_names()
        print("\nRegions (rename any of them under [cluster_names] in settings.toml):")
        for c in sorted(layout["clusters"], key=lambda c: -len(c["members"])):
            name = custom.get(c["id"], c.get("auto_name", ""))
            print(f"  {c['id']:>4}  {name}  ({len(c['members'])} films)")
    return 0


def cmd_rematch(args) -> int:
    from .sync import sync
    settings = load_settings()
    names = _read_names(settings)
    files = store.load_matches()["files"]
    pats = [p.casefold() for p in args.patterns]
    chosen = {n for n in names
              if args.all
              or (args.flagged and files.get(n, {}).get("status") == "flagged")
              or any(p in n.casefold() for p in pats)}
    if not chosen:
        print("Nothing to re-match (give part of a file name, --flagged or --all).")
        return 1
    print(f"Re-matching {len(chosen)} file(s)")
    tmdb = _client(settings)
    try:
        tmdb.fresh_searches = True  # "match again" means asking TMDB again, not replaying old answers
        rep = sync(names, tmdb, rematch=chosen)
    finally:
        tmdb.close()
    _print_sync(rep)
    return 2 if rep.stopped else _build(force=False)


def cmd_refresh(args) -> int:
    from .sync import ensure_record
    settings = load_settings()
    names = _read_names(settings)
    files = store.load_matches()["files"]
    ids = sorted({int(files[n]["tmdb_id"]) for n in names
                  if files.get(n, {}).get("tmdb_id") and files[n].get("status") in ("matched", "manual")})
    if args.ids:
        ids = [i for i in args.ids]
    elif args.recent:
        cutoff = dt.date.today().year - 1
        ids = [i for i in ids if int(((store.load_film(i) or {}).get("release_date") or "0")[:4] or 0) >= cutoff]
    elif not args.all:
        print("Say which films: refresh --recent, refresh --all, or refresh <tmdb id> ...")
        return 1
    tmdb = _client(settings)
    changed = 0
    try:
        for n, i in enumerate(ids, 1):
            before = store.load_film(i)
            rec = ensure_record(tmdb, i, refresh=True)
            if rec and before and {k: v for k, v in before.items() if k != "fetched"} != rec:
                changed += 1
            print(f"  [{n}/{len(ids)}] {rec['title'] if rec else i}")
    finally:
        tmdb.close()
    print(f"Refreshed {len(ids)} film(s); {changed} changed.")
    return _build(force=False)


def cmd_review(args) -> int:
    from .review import serve
    return serve(port=args.port, open_browser=not args.no_browser)


def cmd_check(args) -> int:
    from .build import safety_check
    problems = safety_check() + _key_in_committable_files()
    if problems:
        for p in problems:
            print(f"  ! {p}")
        return 1
    print("Publishable files look clean: no TMDB key, no forbidden wording, sizes fine.")
    return 0


def _key_in_committable_files() -> list[str]:
    """The key may live only in .env. Look through everything that would be committed."""
    key = (load_tmdb_credential() or "").strip()
    if not key:
        return []
    found = []
    for rel in PUBLISH_PATHS:
        p = ROOT / rel
        for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else []):
            if f.is_file() and "__pycache__" not in f.parts and f.stat().st_size < 20_000_000:
                if key.encode() in f.read_bytes():
                    found.append(f"{f.relative_to(ROOT)} contains the TMDB key")
    return found


# --- publishing with git ----------------------------------------------------------------------
def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _git_ready() -> str | None:
    if not shutil.which("git"):
        return "Git is not installed (see README: Deploying)"
    if not (ROOT / ".git").exists():
        return "this folder is not a Git repository yet (see README: Deploying)"
    if not _git("remote").stdout.strip():
        return "the repository has no GitHub remote yet (see README: Deploying)"
    return None


def _existing_publish_paths() -> list[str]:
    return [p for p in PUBLISH_PATHS if (ROOT / p).exists()]


def _has_upstream() -> bool:
    return _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode == 0


def _unpushed() -> bool:
    """True when local commits are not on GitHub yet (a failed push, a commit made by hand,
    or a branch that was never pushed)."""
    if not _has_upstream():
        return _git("rev-parse", "--verify", "-q", "HEAD").returncode == 0
    ahead = _git("rev-list", "--count", "@{u}..HEAD")
    return ahead.returncode == 0 and ahead.stdout.strip() not in ("", "0")


def _pending_changes() -> bool:
    out = _git("status", "--porcelain", "--", *_existing_publish_paths()).stdout
    return bool(out.strip()) or _unpushed()


def cmd_publish(args) -> int:
    from .build import safety_check
    problem = _git_ready()
    if problem:
        print(f"Not publishing: {problem}.")
        return 1
    if ENV_PATH.exists() and _git("check-ignore", "-q", ".env").returncode != 0:
        print("Not publishing: .env (which holds the TMDB key) is not ignored by Git. Check .gitignore.")
        return 1
    problems = safety_check() + _key_in_committable_files()
    if problems:
        print("Not publishing; fix these first:")
        for p in problems:
            print(f"  ! {p}")
        return 1
    paths = _existing_publish_paths()
    _git("add", "--", *paths)
    if _git("diff", "--cached", "--name-only").stdout.strip():
        data = store.read_json(LAYOUT_PATH, default={}) or {}
        count = len(data.get("positions") or {})
        msg = f"Update the map ({count} films, {dt.date.today().isoformat()})"
        commit = _git("commit", "-m", msg)
        if commit.returncode != 0:
            print(commit.stdout + commit.stderr)
            return 1
        print(f"Committed: {msg}")
    elif not _unpushed():
        print("Nothing new to publish.")
        return 0
    print("Pushing to GitHub ...")
    if _has_upstream():
        push = _git("push")
    else:  # first push of this branch: remember where it goes
        push = _git("push", "-u", _git("remote").stdout.split()[0], "HEAD")
    print((push.stdout + push.stderr).strip())
    if push.returncode != 0:
        print("The push failed; the commit is saved locally. Fix the problem above and run publish again.")
        return 1
    print("Published. GitHub Pages refreshes the site within a minute or two.")
    return 0


if __name__ == "__main__":
    sys.exit(0)
