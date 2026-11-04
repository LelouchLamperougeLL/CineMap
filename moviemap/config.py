"""Paths and settings. Everything lives inside the project folder."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_DIR = ROOT / "db"
FILMS_DIR = DB_DIR / "films"           # one small JSON file per TMDB film (committed)
MATCHES_PATH = DB_DIR / "matches.json"  # file name -> match decision + reasons (committed)
LAYOUT_PATH = DB_DIR / "layout.json"    # last map layout, kept so updates stay stable (committed)
CORRECTIONS_PATH = ROOT / "corrections.txt"
SETTINGS_PATH = ROOT / "settings.toml"
DATA_DIR = ROOT / "data"
DATA_JS = DATA_DIR / "movies.js"         # the only data the web page loads
INDEX_HTML = ROOT / "index.html"
CACHE_DIR = ROOT / ".cache"             # local only (git-ignored)
HTTP_CACHE = CACHE_DIR / "tmdb-responses.sqlite"
ENV_PATH = ROOT / ".env"

DEFAULT_SETTINGS = {
    "source": "files.csv",
    "title": "Lumière",
    "tagline": "The closer two films sit, the more they have in common.",
    "drive_folder_url": "",
    "requests_per_second": 4.0,
}


@dataclass
class Settings:
    source: str
    title: str
    tagline: str
    drive_folder_url: str
    requests_per_second: float

    @property
    def source_path(self) -> Path:
        p = Path(os.path.expandvars(os.path.expanduser(self.source)))
        return p if p.is_absolute() else ROOT / p


def load_settings() -> Settings:
    values = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as fh:
            values.update({k: v for k, v in tomllib.load(fh).items() if k in DEFAULT_SETTINGS})
    return Settings(
        source=os.environ.get("MOVIEMAP_SOURCE") or str(values["source"]),
        title=str(values["title"]),
        tagline=str(values["tagline"]),
        drive_folder_url=str(values["drive_folder_url"]),
        requests_per_second=max(0.5, min(20.0, float(values["requests_per_second"]))),
    )


def load_tmdb_credential() -> str | None:
    """TMDB key from the TMDB_API_KEY environment variable or the git-ignored .env file."""
    value = os.environ.get("TMDB_API_KEY", "").strip()
    if value:
        return value
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == "TMDB_API_KEY":
                return val.strip().strip('"').strip("'") or None
    return None
