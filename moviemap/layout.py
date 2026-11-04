"""Map coordinates, clusters and colours, kept stable from one update to the next.

Two independent jobs, as in the brief:
  (a) where films sit: UMAP on the full similarity matrix (no lossy PCA step is
      needed because the similarity is computed exactly, block by block);
  (b) which films group together: HDBSCAN on the same full-detail distances,
      never on the flattened 2-D map.

Stability: an update starts UMAP from the previous positions (new films start
next to their nearest neighbours), runs a short refinement, aligns the result
back onto the old frame and leaves films that barely moved exactly where they
were. Clusters are matched to the previous ones by membership, so they keep
their colour and name.
"""

from __future__ import annotations

import math
import warnings

import numpy as np

SEED = 42


# --- layout -------------------------------------------------------------------------------
def _umap(D: np.ndarray, init, n_epochs: int, learning_rate: float = 1.0) -> np.ndarray:
    import umap  # imported lazily: it is slow to import
    n = D.shape[0]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reducer = umap.UMAP(
            n_neighbors=min(15, n - 1), min_dist=0.32, spread=1.0, metric="precomputed",
            init=init, n_epochs=n_epochs, learning_rate=learning_rate, random_state=SEED,
        )
        return reducer.fit_transform(D).astype(np.float64)


def _affine_fit(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares affine map src -> dst, returned as a 3x2 matrix."""
    A = np.hstack([src, np.ones((len(src), 1))])
    M, *_ = np.linalg.lstsq(A, dst, rcond=None)
    return M


def _apply(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return np.hstack([pts, np.ones((len(pts), 1))]) @ M


def _normalise_first(Y: np.ndarray) -> np.ndarray:
    """First layout: centre it and scale so the larger side spans 1000 units."""
    Y = Y - Y.mean(0)
    span = (Y.max(0) - Y.min(0)).max() or 1.0
    return Y * (1000.0 / span)


def _relax(Y: np.ndarray, min_gap: float, fixed: np.ndarray | None = None, rounds: int = 40) -> np.ndarray:
    """Nudge apart films that sit on top of each other (keeps posters legible when zoomed)."""
    from scipy.spatial import cKDTree
    Y = Y.copy()
    for _ in range(rounds):
        pairs = cKDTree(Y).query_pairs(min_gap, output_type="ndarray")
        if len(pairs) == 0:
            break
        delta = np.zeros_like(Y)
        for i, j in pairs:
            v = Y[i] - Y[j]
            d = math.hypot(*v)
            if d < 1e-9:
                v, d = np.array([math.cos(i + j), math.sin(i + j)]), 1.0
            push = (min_gap - d) / 2 * v / d
            delta[i] += push
            delta[j] -= push
        if fixed is not None:
            delta[fixed] *= 0.25
        Y += delta * 0.6
    return Y


def _place_new(S: np.ndarray, disp: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Put each new film among its nearest already-placed neighbours (similarity-weighted)."""
    known_idx = np.flatnonzero(known)
    rng = np.random.default_rng(SEED)
    span = float((disp[known_idx].max(0) - disp[known_idx].min(0)).max()) or 1000.0
    for r in np.flatnonzero(~known):
        nearest = known_idx[np.argsort(-S[r, known_idx])[:6]]
        w = np.maximum(S[r, nearest], 1e-6) ** 2
        disp[r] = (disp[nearest] * w[:, None]).sum(0) / w.sum() + rng.normal(0, 0.004 * span, 2)
    return disp


def compute_layout(D: np.ndarray, S: np.ndarray, ids: list[int], previous: dict | None,
                   raw_span: float | None = None, full: bool = True, log=print) -> tuple[np.ndarray, dict]:
    """Display coordinates (the larger side of the first map spans ~1000 units).

    full=False (an ordinary update): films already on the map keep their exact position;
    new films are placed beside their nearest neighbours.
    full=True with a previous layout: UMAP restarts from the previous positions and the result
    is mapped back onto the old frame, so the map keeps its shape and orientation.
    """
    n = len(ids)
    prev = {int(k): np.array(v, dtype=np.float64) for k, v in (previous or {}).items()}
    known = np.array([i in prev for i in ids])
    stats = {"mode": "fresh", "moved": 0, "kept": 0, "new": int((~known).sum()), "raw_span": raw_span}
    if n < 4:
        return np.array([[i * 50.0, 0.0] for i in range(n)]), stats
    usable = raw_span and known.sum() >= max(10, n // 2)

    if usable and not full:
        disp = np.zeros((n, 2))
        for r, fid in enumerate(ids):
            if known[r]:
                disp[r] = prev[fid]
        disp = _place_new(S, disp, known)
        span = float((disp.max(0) - disp.min(0)).max())
        Y = _relax(disp, min_gap=0.0045 * span, fixed=known)
        Y[known] = disp[known]
        stats.update(mode="incremental", kept=int(known.sum()))
    elif usable:
        disp = np.zeros((n, 2))
        for r, fid in enumerate(ids):
            if known[r]:
                disp[r] = prev[fid]
        disp = _place_new(S, disp, known)
        known_idx = np.flatnonzero(known)
        Y = _umap(D, (disp * (raw_span / 1000.0)).astype(np.float32), n_epochs=300, learning_rate=0.5)
        Y = _apply(_affine_fit(Y[known_idx], disp[known_idx]), Y)  # back onto the old frame
        span = float((disp[known_idx].max(0) - disp[known_idx].min(0)).max())
        moved = np.linalg.norm(Y[known_idx] - disp[known_idx], axis=1)
        frozen = np.zeros(n, dtype=bool)
        frozen[known_idx[moved < 0.01 * span]] = True
        Y = _relax(Y, min_gap=0.0045 * span, fixed=frozen)
        Y[frozen] = disp[frozen]
        stats.update(mode="full re-layout", kept=int(frozen.sum()), moved=int(len(known_idx) - frozen.sum()),
                     median_shift=round(float(np.median(moved)) / span * 100, 2))
    else:
        raw = _umap(D, "spectral", n_epochs=600)
        stats["raw_span"] = float((raw.max(0) - raw.min(0)).max())
        Y = _normalise_first(raw)
        if prev and known.sum() >= 10:  # a forced fresh layout keeps the old orientation
            known_idx = np.flatnonzero(known)
            P = np.array([prev[ids[r]] for r in known_idx])
            Y = _apply(_similarity_fit(Y[known_idx], P), Y)
        Y = _relax(Y, min_gap=4.5)
    return Y, stats


def _similarity_fit(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Best rotation/reflection + uniform scale + shift taking src onto dst, as a 3x2 matrix."""
    ms, md = src.mean(0), dst.mean(0)
    A, B = src - ms, dst - md
    U, sig, Vt = np.linalg.svd(A.T @ B)
    R = U @ Vt
    scale = sig.sum() / (A ** 2).sum()
    M = np.zeros((3, 2))
    M[:2] = scale * R
    M[2] = md - ms @ (scale * R)
    return M


# --- clusters -------------------------------------------------------------------------------
def find_clusters(D: np.ndarray) -> np.ndarray:
    """HDBSCAN on a 10-dimensional UMAP embedding of the full-detail distances.

    Run directly on the raw distances, HDBSCAN labelled 57-71% of this collection as noise:
    in a space this high-dimensional nearly every film is about equally far from every other,
    so density is invisible. A 10-D UMAP embedding of the same similarity graph keeps who is
    near whom (it is not the 2-D map) while making the dense regions show up; HDBSCAN then
    finds coherent groups and leaves only the genuinely in-between films unassigned.
    """
    import umap
    from sklearn.cluster import HDBSCAN
    n = D.shape[0]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        E = umap.UMAP(n_neighbors=min(15, n - 1), n_components=min(10, n - 2), min_dist=0.0,
                      metric="precomputed", random_state=SEED).fit_transform(D)
        model = HDBSCAN(min_cluster_size=max(6, int(round(n / 73))), min_samples=5,
                        cluster_selection_method="eom", copy=True)
        return model.fit_predict(E)


def assign_to_clusters(S: np.ndarray, labels: np.ndarray, todo: np.ndarray, top: int = 5) -> np.ndarray:
    """Give films without a label (new ones) the region they most resemble, if they fit it well."""
    labels = labels.copy()
    ks = sorted(k for k in set(labels.tolist()) if k >= 0)
    for i in np.flatnonzero(todo):
        best, best_aff = -1, 0.0
        for k in ks:
            members = np.flatnonzero((labels == k) & ~todo)
            if len(members) == 0:
                continue
            aff = float(np.sort(S[i, members])[-top:].mean())
            sub = S[np.ix_(members, members)].copy()
            np.fill_diagonal(sub, 0.0)
            typical = float(np.median(np.sort(sub, axis=1)[:, -top:].mean(1)))
            if aff >= 0.8 * typical and aff > best_aff:
                best, best_aff = k, aff
        labels[i] = best
    return labels


def memberships(S: np.ndarray, labels: np.ndarray, temperature: float = 0.025, top: int = 6) -> np.ndarray:
    """Soft membership of every film in every cluster: films deep inside a cluster get its
    colour, films between clusters a blend of the nearest ones."""
    ks = sorted(k for k in set(labels.tolist()) if k >= 0)
    n = S.shape[0]
    aff = np.zeros((n, len(ks)))
    for c, k in enumerate(ks):
        members = np.flatnonzero(labels == k)
        sub = S[:, members].copy()
        sub[members, np.arange(len(members))] = -1  # ignore self-similarity
        m = min(top, max(1, len(members) - 1))
        aff[:, c] = np.sort(sub, axis=1)[:, -m:].mean(1)
    z = (aff - aff.max(1, keepdims=True)) / temperature
    w = np.exp(z)
    return w / w.sum(1, keepdims=True)


def match_clusters(new_members: dict[int, set[int]], old: list[dict]) -> dict[int, dict | None]:
    """Pair each new cluster with the old cluster it overlaps most (Jaccard >= 0.3)."""
    pairs = []
    for k, mem in new_members.items():
        for o in old:
            om = set(o.get("members", []))
            j = len(mem & om) / max(1, len(mem | om))
            if j >= 0.3:
                pairs.append((j, k, o["id"]))
    pairs.sort(reverse=True)
    used_new, used_old, out = set(), set(), {}
    by_id = {o["id"]: o for o in old}
    for j, k, oid in pairs:
        if k in used_new or oid in used_old:
            continue
        used_new.add(k)
        used_old.add(oid)
        out[k] = by_id[oid]
    for k in new_members:
        out.setdefault(k, None)
    return out


# --- colour -----------------------------------------------------------------------------------
def oklch_to_hex(L: float, C: float, h_deg: float) -> str:
    h = math.radians(h_deg)
    a, b = C * math.cos(h), C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)

    def enc(x: float) -> int:
        x = max(0.0, min(1.0, x))
        x = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
        return int(round(x * 255))
    return "#" + "".join(f"{enc(c):02x}" for c in rgb)


def cluster_colour(hue: float, index: int) -> str:
    # Alternate lightness a little so neighbouring regions stay distinguishable.
    L = 0.80 if index % 2 == 0 else 0.73
    return oklch_to_hex(L, 0.135, hue)


def assign_hues(clusters: list[dict], centre: np.ndarray) -> None:
    """Hues follow the angle around the map (a colour wheel), evenly spaced on first build;
    clusters that already have a hue keep it, new ones take the middle of the widest gap."""
    fresh = [c for c in clusters if c.get("hue") is None]
    if not fresh:
        return
    if len(fresh) == len(clusters):
        order = sorted(clusters, key=lambda c: math.atan2(c["centre"][1] - centre[1], c["centre"][0] - centre[0]))
        step = 360.0 / len(order)
        for i, c in enumerate(order):
            c["hue"] = round((25 + i * step) % 360, 1)
        return
    for c in fresh:
        hues = sorted(x["hue"] for x in clusters if x.get("hue") is not None)
        gaps = [((hues[(i + 1) % len(hues)] - h) % 360 or 360, h) for i, h in enumerate(hues)]
        size, start = max(gaps)
        c["hue"] = round((start + size / 2) % 360, 1)
