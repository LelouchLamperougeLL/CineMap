# Lumière: a map of the collection

`index.html` shows every film in the collection as a point of light. Films that share people, themes, genres, language and era sit close together, regions of similar films are coloured, and clicking a film shows its details and its 20 nearest films. Everything below runs on this computer; the public site is only `index.html`, `manifest.webmanifest`, `sw.js`, `assets/` and `data/`.

## First run

1. Python 3.12 must be installed (it is on this PC). On another PC: install it from python.org and tick "Add python.exe to PATH".
2. The TMDB key lives in the file `.env` (already created here; it is never committed or published). On another PC create `.env` next to `index.html` containing one line: `TMDB_API_KEY=your key`.
3. Double-click **`update.cmd`**. The first time it installs the Python libraries (a few minutes), then fetches every film from TMDB (about 12 minutes for 740 films) and draws the map.
4. Double-click **`index.html`**. It opens in Edge and works offline from disk (posters need internet).

You can close the window or lose the connection at any point while it fetches: everything already fetched is kept, and running `update.cmd` again carries on from where it stopped without asking TMDB for anything twice.

## Adding films

1. Export the listing from Google Drive as before and save it over `files.csv` (rows may be in any order; added and removed rows are both picked up).
2. Double-click **`update.cmd`**.

Only the new films are fetched. Films already on the map keep their exact place and colour; new ones appear beside their closest neighbours. Running it again with an unchanged `files.csv` does nothing.

Files that disappear from the listing leave the map, but their match (and any decision you made) is kept aside for a year, so a file that comes back needs nothing. If more than a few files vanish at once (more than 5% of the collection), `update.cmd` lists some and asks before removing them, in case the export was cut short; unattended runs change nothing in that case.

Skipping the export: if the collection folder is visible on this PC (Google Drive for Desktop), set `source = "G:/My Drive/<your folder>"` in `settings.toml` and the files are listed directly.

## When a match needs your review

A wrong match would quietly spoil the map, so when the matcher is not sure it leaves the film off the map and says why. `update.cmd` then offers to open the review page (or double-click **`review.cmd`**):

* each doubtful file shows the candidates with posters, directors and years; click **Use this film**;
* or search TMDB right there, or paste a TMDB/IMDb link or id and click **Use this link** (it shows which film the link points to and asks before using it);
* or mark it **Not a film (skip)**;
* when done, click **Rebuild the map** or press Ctrl+C in the review window (closing the window also works; the next `update.cmd` rebuilds then).

Every decision is written to **`corrections.txt`**, which you can also edit by hand. One line per file, it wins over automatic matching for good:

```
[2016] Elle (French).mkv = 337674
[2016] Elle (French) = https://www.themoviedb.org/movie/337674-elle     (extension optional)
[2010] Something.mkv = tt1234567
desktop.ini = skip
```

The review page also lists the less-than-certain automatic matches and every map region's id (to rename a region, add `c3 = "A better name"` under `[cluster_names]` in `settings.toml`).

## Deploying to GitHub

One-time setup:

1. Install Git: `winget install Git.Git` (or install GitHub Desktop).
2. Create a **private** repository on github.com (no README), then in this folder:
   ```
   git init -b main
   git add .
   git commit -m "Movie map"
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
   `.gitignore` keeps `.env`, `movie-map-prompt.md` (it contains the key), `.venv/` and `.cache/` out of the repository.
3. On GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**. The workflow in `.github/workflows/pages.yml` publishes only `index.html`, `manifest.webmanifest`, `sw.js`, `assets/` and `data/`. Pages on a private repository needs a paid GitHub plan (Pro); the published site itself is public to anyone with the link, which is why it holds no key and no management features.

After that, `update.cmd` asks **"Publish the updated map to GitHub now?"** whenever something changed (or run `python -m moviemap publish`). It refuses to publish if the key or other problems are found.

## Installing it as an app (laptop, phone, tablet)

Installing works from the published address (the GitHub Pages link), not from `index.html` opened on disk: browsers only install sites served over https.

* **Edge or Chrome on a computer:** the install icon at the right end of the address bar, or the menu, then **Apps**, then **Install**. The map also shows its own install button (a downward arrow beside the zoom buttons) when the browser allows it.
* **Android (Chrome, Edge, Samsung Internet):** the browser menu, then **Install app** or **Add to Home screen**.
* **iPhone or iPad (Safari):** the Share menu (on recent iPhones it sits behind the **···** button), then **Add to Home Screen**.

The installed app opens in its own window, works without a connection (the page, data and fonts are kept; posters you have already seen usually are too), and picks up new films by itself the next time it is online. Long-pressing (or right-clicking) its icon offers "A random film" and "Browse the collection".

## What viewers can do

Besides exploring, searching and tracing paths between films, each viewer can mark films **Seen it** and **Want to watch**, then hide what they have seen or show only their list (Filters). These marks are saved only in that viewer's browser; they never touch the collection. Each film offers **Find in Google Drive**, which searches the viewer's own Drive (including files shared with them) for that file, and on the published site a **Share** button that sends a link straight to the film.

## Commands

Run from this folder as `.venv\Scripts\python -m moviemap <command>`:

| command | does |
|---|---|
| `update` | read the listing, match and fetch new films, rebuild the map (what `update.cmd` runs) |
| `review` | open the review page (what `review.cmd` runs) |
| `status` | counts, anything needing review, region ids |
| `refresh --recent` | re-download TMDB details for films from the last two years (new releases fill in over time) |
| `rematch "Elle"` | match some files again from scratch (`--flagged`, `--all`) |
| `build --relayout` | recompute the whole layout (orientation and region colours are kept where possible) |
| `check` | look for the TMDB key or forbidden wording in anything publishable |
| `publish` | commit and push the map to GitHub after the same checks (`update.cmd` offers this itself) |
| `--source other.csv update` | update from another listing (a CSV or a folder) instead of `files.csv`; files missing from it leave the map as described above |

Tests: `.venv\Scripts\python -m unittest discover -s tests -t .`

## How it works, briefly

* **Matching** reads the naming convention (`[YYYY] Title (Language; Native title, Romanization).ext`, look-alike characters such as `꞉` and `？`, parts, unclosed tags, nested parentheses), searches TMDB, and accepts a film only when title and year agree and nothing else fits as well; ties are settled by the language tag or, only when one film is far better known, by popularity. Everything else is flagged. Each decision and its reasons are stored in `db/matches.json`.
* **Similarity** is a weighted sum of per-kind similarities: genres, keywords, directors, writers, cast (weighted by billing), key crew, studios/series, language and country (all weighted by rarity, so a shared rare keyword counts more than a common one), and year/runtime/popularity/rating (scaled). Missing TMDB data counts as average, not as "nothing in common".
* **Layout**: UMAP on the full similarity. **Regions**: HDBSCAN on a 10-dimensional UMAP embedding of the same similarities (not the 2-D map). Colours follow the angle around the map, and films between regions blend their neighbours' colours.
* **Stability**: ordinary updates never move existing films, and they reuse the collection-wide statistics of the last full layout, so only films related to what was added get new neighbours (an update of three films grows the repository by about 10 KB). A full re-layout happens only on request or after the collection has changed by 15%.

## Files

| | |
|---|---|
| `index.html`, `assets/`, `data/movies.js` | the page and the only data it loads (published) |
| `manifest.webmanifest`, `sw.js`, `assets/icons/` | what makes it installable and usable offline (published; the manifest is rewritten from `settings.toml` on each build) |
| `files.csv` | the listing exported from Drive |
| `corrections.txt` | your manual decisions |
| `settings.toml` | title, tagline, listing source, region names |
| `db/films/*.json` | one trimmed TMDB record per film (committed; only new files appear on updates) |
| `db/matches.json`, `db/layout.json` | match decisions with reasons (plus, for a year, those of files no longer listed); last layout and regions |
| `.cache/` | raw TMDB responses, local only |
| `moviemap/` | the Python tools |

If TMDB cannot be reached: some networks block `api.themoviedb.org`; the tools use TMDB's alternative address `api.tmdb.org` first and fall back automatically.

Film data and images: [TMDB](https://www.themoviedb.org/). This product uses the TMDB API but is not endorsed or certified by TMDB.
