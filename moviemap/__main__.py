"""Command line: python -m moviemap <command>

  update    read the collection listing, match and fetch new films, rebuild the map (default)
  review    open the local review page to settle flagged matches
  publish   commit the updated map and push it to GitHub (asks first)
  build     rebuild the map from what is already stored  (--relayout: start the layout afresh)
  status    summary of matches, flags and corrections
  rematch   match some files again: rematch "Elle" "Carlos"  |  --flagged  |  --all
  refresh   re-download TMDB details: refresh --recent (last 2 years) | --all | <tmdb ids>
  check     scan the publishable files for leaks (TMDB key, forbidden words, size)
"""

from __future__ import annotations

import argparse
import os
import sys


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="moviemap", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", help="read this listing (CSV or folder) instead of the one in settings.toml")
    sub = parser.add_subparsers(dest="command")
    u = sub.add_parser("update", help="sync with the collection listing and rebuild the map")
    u.add_argument("--no-prompt", action="store_true", help="don't ask about reviewing or publishing")
    sub.add_parser("publish", help="commit the updated map and push it to GitHub")
    b = sub.add_parser("build", help="rebuild the map from stored data")
    b.add_argument("--relayout", action="store_true", help="recompute the layout from scratch")
    r = sub.add_parser("review", help="open the local review page")
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--no-browser", action="store_true")
    sub.add_parser("status", help="summary of the collection")
    m = sub.add_parser("rematch", help="match files again")
    m.add_argument("patterns", nargs="*")
    m.add_argument("--flagged", action="store_true")
    m.add_argument("--all", action="store_true")
    f = sub.add_parser("refresh", help="re-download TMDB details")
    f.add_argument("ids", nargs="*", type=int)
    f.add_argument("--recent", action="store_true")
    f.add_argument("--all", action="store_true")
    sub.add_parser("check", help="safety checks on the publishable files")
    args = parser.parse_args(argv)
    if args.source:
        os.environ["MOVIEMAP_SOURCE"] = os.path.abspath(args.source)

    from . import cli
    command = args.command or "update"
    if not hasattr(args, "no_prompt"):
        args.no_prompt = False
    try:
        return int(getattr(cli, f"cmd_{command}")(args) or 0)
    except KeyboardInterrupt:
        print("\nStopped. Everything fetched so far is saved; run the same command again to continue.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
